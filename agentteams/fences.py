"""
fences.py — section-fencing internals (regexes, MergeResult, fence extraction/merge,
shrink detection, and lost-fence sidecars) for emit. Carved from emit.py (CH-07).
emit.py re-exports these so importers (drift, fence_inject, tests) resolve them from
agentteams.emit unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.atomicio import _atomic_write_text

# The unfenced view of a file lives in unfenced.py (CH-07 carve): fences.py merges managed
# regions; that module owns the complement — the prose a merge preserves verbatim, and the two
# detectors that report when it drifts or loses a rule. Re-exported so every existing
# `from agentteams.fences import unfenced_lines` still works.
from agentteams.unfenced import (  # noqa: F401
    CONSTRAINT_BEARING_RE,
    NUMBERED_RULE_RE,
    _CONSTRAINT_MIN_CHARS,
    _FENCE_BEGIN_RE,
    _FENCE_END_RE,
    _UNFENCED_DRIFT_MIN_CHARS,
    _detect_deleted_constraints,
    _detect_unfenced_drift,
    detect_deleted_fenced_constraints,
    _unfenced_regions,
    is_trackable_constraint_line,
    unfenced_lines,
)


# Front-matter handling lives in front_matter_merge.py (CH-07 carve): front matter is the one
# part of a generated file that cannot be fenced, so it needs a different mechanism entirely.
# Re-exported so every existing `from agentteams.fences import _merge_front_matter` still works.
from agentteams.front_matter_merge import (  # noqa: F401
    _CAPABILITY_FRONT_MATTER_KEYS,
    _DRIFT_EXEMPT_FRONT_MATTER_KEYS,
    _FM_KEY_RE,
    _YAML_FM_RE,
    _detect_front_matter_drift,
    _front_matter_keys,
    _merge_front_matter,
    _parse_capability_list,
    _render_front_matter,
    _restore_blanked_front_matter_blocks,
)

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s)", re.MULTILINE)
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|md|json|yaml|yml|toml|csv|tsv|sql|sh)\b")
_BACKTICK_IDENT_RE = re.compile(r"`([^`\n]+)`")


@dataclass
class MergeResult:
    """Result of a single fenced-content merge operation.

    Attributes:
        sections_replaced:  section_ids whose content was updated from the new render.
        sections_added:     section_ids present in new render but absent in existing file.
        sections_orphaned:  section_ids present in existing file but absent in new render.
        sections_preserved: section_ids whose new render would have shrunk the
                            existing body, kept unchanged under shrink_policy
                            "preserve" (respectful update — no content lost).
        parse_errors:       Human-readable messages for parse failures.
        unchanged:          section_ids that were identical in both files (no write needed).
        merged_content:     The final merged file content (empty string on parse failure).
        shrink_notices:     Per-section human-readable Notices (Plan 3) when a
                            regenerated fence body is materially shorter / less
                            specific than the existing on-disk body.
    """
    sections_replaced: list[str] = field(default_factory=list)
    sections_added: list[str] = field(default_factory=list)
    sections_orphaned: list[str] = field(default_factory=list)
    sections_preserved: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    merged_content: str = ""
    shrink_notices: list[str] = field(default_factory=list)
    # W22 data-loss recovery: full pre-merge body of every fence that fired
    # a shrink notice, keyed by section_id. Persisted as a .lost.<sid>.md
    # sidecar inside the backup dir by emit_all when backup_path is provided.
    lost_fence_bodies: dict[str, str] = field(default_factory=dict)
    # Front-matter keys whose template value moved on while the on-disk file kept its own.
    # Merge preserves everything outside a fence BY DESIGN — that is what protects user edits —
    # so this never changes what is written. It exists because the preservation was also silent:
    # measured 2026-07-30, adding `retrieval` to two templates' `tools:` lists reached no
    # already-generated team, and `--update --merge` reported nothing at all. See
    # `_detect_front_matter_drift`.
    front_matter_drift: list[str] = field(default_factory=list)
    # Sections a newly-added fence duplicates: the deployed file already carries the same heading
    # UNFENCED, from before the template fenced it. Reported, never auto-resolved — see
    # `_detect_duplicate_sections` for why the whole-body case can be resolved automatically and
    # this one cannot.
    duplicate_section_notices: list[str] = field(default_factory=list)
    # Template rules absent from the deployed file. Fires regardless of modification state —
    # see `_detect_deleted_constraints` for why `_detect_unfenced_drift` cannot cover this.
    deleted_constraint_notices: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.parse_errors)

    @property
    def content_changed(self) -> bool:
        return bool(self.sections_replaced or self.sections_added)


# ---------------------------------------------------------------------------
# Section-fencing internals
# ---------------------------------------------------------------------------

# W2: detect AGENTTEAMS-BRIDGE fences (written by --bridge-refresh) so the
# --merge path can emit a targeted notice instead of the generic "legacy file" warning.
_BRIDGE_FENCE_BEGIN_RE = re.compile(r"<!--\s*AGENTTEAMS-BRIDGE:BEGIN\s+")

_MACHINE_MANAGED_MERGE_OVERWRITE_PATHS: frozenset[str] = frozenset([
    "references/security-vulnerability-watch.json",
    # Sentinel-merge handled in vscode_tasks.py before this path reaches emit;
    # emit must overwrite (not fence-merge) so stale JSON is fully replaced.
    "../../.vscode/tasks.json",
    # Generated SVG diagrams are raw XML (no AGENTTEAMS content fence); emit must
    # overwrite them wholesale — auto-fencing would inject a comment before <?xml>
    # (invalid XML) and merge would skip them as unmanaged (stale forever).
    "references/pipeline-graph.svg",
    "references/pipeline-handoffs.svg",
    "references/architecture-graph.svg",
    "references/architecture-modules.svg",
    # The graph .md documents are 100% machine-generated ("Auto-generated. Do not
    # edit manually") with no user-editable region. Under --merge, fence-merging
    # their single `content` block preserved the stale body, so the .md drifted
    # behind its companion .svg (which IS overwritten) — the roster table would show
    # a different agent count than the diagram. Full-replace keeps the two in lockstep.
    "references/pipeline-graph.md",
    "references/architecture-graph.md",
    # Gap 3 (2026-07-24): the Goose resilient-runner script is a verbatim shipped
    # Python tool with no user-editable region (agentteams/frameworks/goose.py's
    # _resilient_runner_content reads it from disk each run, so "the source of
    # truth" already lives outside the generated project). Auto-fencing an
    # unfenced .py file inserts an HTML-comment fence marker as its new first
    # line, displacing the `#!/usr/bin/env python3` shebang and producing invalid
    # Python (a SyntaxError on any subsequent run) — the exact same failure mode
    # the SVG entries above document for XML, just for Python instead. Full-replace
    # on merge keeps the shipped copy in lockstep with this repo's own script,
    # exactly like the .md/.svg pairs above.
    "../../scripts/goose-run-resilient.py",
])

# Fences whose body is refreshed each run from an upstream live feed
# (CISA KEV, NVD CVSS, OSV.dev, etc.). Content "loss" in these fences reflects
# normal feed rotation, not user-content deletion — suppress the shrink-warn
# heuristic so structural --update runs don't emit alarming false positives.
# The canonical history for these feeds is the cache JSON, not the embedded
# snapshot. Real user content sits in adjacent operator-managed fences.
_LIVE_DATA_FENCES: frozenset[str] = frozenset([
    "threat_intelligence",
    "threat_data",
])


# Fences whose body is derived wholesale from the project brief on every run —
# the authority hierarchy and its mirrored source-repository list, both built
# from `authority_sources` in the description/brief (see
# `analyze.build_authority_hierarchy`). A path renamed in the brief (e.g. a
# package rename like src/ -> agentteams/) makes the old path vanish from the
# rendered body; _detect_fence_shrink rule (c) reads that as "lost a concrete
# reference", and under the default shrink_policy=preserve the OLD (previous)
# behavior kept the stale body indefinitely — a brief-level rename could never
# reach these two fences through the sanctioned --update --merge path.
# Confirmed live 2026-08-15 (agent-doc-optimal-structure plan): a full
# copilot-vscode --update --merge left `src/` in both fences of
# .github/copilot-instructions.md although .claude/CLAUDE.md — never stale to
# begin with, so no shrink was ever detected there — already rendered the
# correct `agentteams/` paths from the same brief.
#
# NOT wired the same way as _LIVE_DATA_FENCES (see _detect_fence_shrink):
# these fences ARE consulted via _is_template_authoritative below, on
# purpose. A first version of this fix short-circuited _detect_fence_shrink
# itself for these two sids, the same way _LIVE_DATA_FENCES does — that
# silently disabled the shrink NOTICE and the .lost.<sid>.md sidecar recovery
# too, not just the preserve decision, which also weakens
# --shrink-policy=halt (scripts/run_daily_security_maintenance.sh runs with
# halt specifically so a real content loss stops the run) for a fence that
# can genuinely regress if the brief itself is ever wrong or hand-edited.
# _LIVE_DATA_FENCES's silence is correct there because feed rotation happens
# every run and a notice every time is alert fatigue; a brief-derived rename
# is rare and deliberate, so the notice is exactly the useful signal.
# Caught by the adversarial audit of this fix (2026-08-15) — see
# references/agentteams-remediation-log.csv row 178.
_BRIEF_DERIVED_FENCES: frozenset[str] = frozenset([
    "authority_hierarchy",
    "source_repositories",
])


#: Fences whose body the TEMPLATE owns outright — never preserved from disk on shrink.
#:
#: `--shrink-policy=preserve` resolves in favour of on-disk content whenever the template
#: body looks materially smaller. For most fences that is right: it protects operator
#: enrichment. For a security contract it inverts the trust. The shrink heuristic cannot
#: distinguish enrichment from tampering — they are the same operation — so appending a
#: backticked token to a fenced security region is enough to suppress its update
#: indefinitely, with only a notice scrolling past each run.
#:
#: Deliberately SEPARATE from :data:`_LIVE_DATA_FENCES`. That set means "upstream feed
#: rotation is expected", which is a different claim; conflating them would obscure both.
#: A fence belongs here only when the project has no legitimate reason to extend its body.
_TEMPLATE_AUTHORITATIVE_FENCES: frozenset[str] = frozenset([
    "invariant_core",
    "security_authority",
    "security_rules_invariant",
    "security_verdict_contract",
])


#: Files whose EVERY fence is template-authoritative, identified by basename.
#:
#: The set above is keyed on section id, which works while the section id is distinctive. It
#: does not reach a file emitted inside a single whole-file `content` fence — and
#: `instruction-authority.reference.md` is exactly that. A 2026-08-06 red-team probe (D2)
#: appended three backticked tokens to its body and `--shrink-policy=preserve` then pinned the
#: attacker's version indefinitely: the file that DEFINES the precedence ordering C-1 depends
#: on was the one fence the shrink-preserve fix did not cover.
#:
#: Membership applies the criterion already stated for `_TEMPLATE_AUTHORITATIVE_FENCES`: a file
#: belongs here only when the project has **no legitimate reason to extend its body**. Exactly
#: one of the 24 reference files in a generated team meets that today. The other 17 that share
#: the `content` fence — the code-hygiene rules, the retrospective procedure, the retrieval
#: contracts — are files a project may reasonably add to, so blanket-listing the `content`
#: section id would trade one defect for a worse one. Add entries deliberately, one at a time,
#: and say why here.
_CONSTITUTIONAL_FILES: frozenset[str] = frozenset([
    # The instruction-authority ordering. Its own header states the whole file is module-owned
    # and restored on every `--update --merge`; before this entry that claim was false under
    # the preserve policy.
    "instruction-authority.reference.md",
])


def _is_template_authoritative(sid: str, rel_path: str) -> bool:
    """True when the template owns this fence body outright and must never yield to disk.

    Also true for :data:`_BRIEF_DERIVED_FENCES`: those fences meet the exact
    criterion this function already names ("no legitimate reason to extend
    its body") for a different reason (brief-derived, not security-critical)
    — kept as a separate, distinctly-commented set for that documentation
    value, but wired through the same never-preserve decision. Unlike
    :data:`_LIVE_DATA_FENCES`, membership here does NOT suppress
    `_detect_fence_shrink` itself — the notice and `.lost.<sid>.md` sidecar
    still fire on a real shrink; only the preserve-on-shrink outcome is
    refused, exactly like every other template-authoritative fence.

    Args:
        sid: The fence's section id.
        rel_path: Repo-relative path of the file being merged. May be empty when a caller
            does not know it, in which case only the section-id rule applies — the
            conservative direction, since it can only *fail to protect*, never wrongly
            overwrite a file the project owns.

    Returns:
        Whether shrink-preserve must be refused for this fence.
    """
    if sid in _TEMPLATE_AUTHORITATIVE_FENCES or sid in _BRIEF_DERIVED_FENCES:
        return True
    return bool(rel_path) and Path(rel_path).name in _CONSTITUTIONAL_FILES


def _rename_suspect_sid(orphan_sid: str, new_sids: set[str]) -> str | None:
    """Return the authoritative section id an orphan looks like a rename of, if any.

    A fence whose body the template owns can be escaped simply by renaming it: the deployed
    file's `security_rules_invariant` becomes `security_rules_invariant_local`, the merge treats
    it as an orphan and preserves it verbatim, and the template's real body is *also* inserted.
    The result is one agent file carrying two contradictory rule sets, with the contradiction
    left for a reading model to resolve — which is the thing C-1 exists to avoid relying on.
    Measured 2026-08-06 as probe D4.

    Matching is prefix/suffix containment rather than an edit distance: the realistic evasions
    are decorations (`_local`, `_v2`, `project_`), and a fuzzy matcher would start flagging
    unrelated sections whose names merely share a stem.

    Args:
        orphan_sid: A section id present on disk but absent from the new render.
        new_sids: Section ids the new render defines.

    Returns:
        The authoritative section id it appears to shadow, or ``None``.
    """
    normalized = orphan_sid.strip().lower()
    for authoritative in sorted(_TEMPLATE_AUTHORITATIVE_FENCES):
        if authoritative not in new_sids:
            continue
        if normalized == authoritative:
            continue
        if normalized.startswith(authoritative) or normalized.endswith(authoritative):
            return authoritative
    return None


#: Timestamp line the security payload stamps into every rendered file carrying live data.
_GENERATED_AT_RE = re.compile(r"Generated at: `[^`]+`")


def redact_live_data(text: str) -> str:
    """Blank the parts of a rendered file that legitimately differ on every run.

    Snapshot comparison excluded ``security.agent.md`` *whole-file* because one of its fences
    carries a live vulnerability feed and a generation timestamp. The consequence was that the
    ~90% of that file which is stable — the trigger table, Rules S-1..S-10, the escalation
    criteria, and the fenced authority claims — shipped with no golden coverage at all, on the
    highest-privilege agent in the team.

    This narrows the exclusion from the file to the volatile regions inside it: the body of every
    fence in :data:`_LIVE_DATA_FENCES`, plus the ``Generated at:`` stamp. Everything else stays
    comparable.

    Verified in both directions before adoption: replacing the whole ``threat_intelligence`` body
    and the timestamp produces identical redacted output, while changing "HALT is final" outside
    those fences does not — which is the drift a golden exists to catch.

    Args:
        text: A rendered file body.

    Returns:
        The same text with live-feed fence bodies and the generation stamp replaced by markers.
        Whitespace is left alone; callers that normalise it should keep doing so.
    """
    for sid in _LIVE_DATA_FENCES:
        text = re.sub(
            rf"(<!--\s*AGENTTEAMS:BEGIN\s+{re.escape(sid)}\s+v=\d+\s*-->).*?"
            rf"(<!--\s*AGENTTEAMS:END\s+{re.escape(sid)}\s*-->)",
            r"\1<<LIVE-DATA-REDACTED>>\2",
            text,
            flags=re.DOTALL,
        )
    return _GENERATED_AT_RE.sub("Generated at: `<<REDACTED>>`", text)


def _fence_body(block: str) -> str:
    """Strip the BEGIN/END marker lines from a fenced block — returns body only."""
    lines = block.splitlines(keepends=True)
    if not lines:
        return ""
    body = lines[1:-1] if len(lines) >= 2 else lines
    return "".join(body)


def _detect_fence_shrink(sid: str, existing_block: str, new_block: str) -> str | None:
    """Plan 3: return a Notice string when the new fence body is materially
    shorter or less specific than the existing body (rules a/b/c), else None.

    Rules (any one triggers):
      (a) new body length < 50% of existing body length;
      (b) new body has >= 3 fewer markdown list items than existing;
      (c) existing body contained concrete file paths or backtick-quoted
          identifiers that the new body does not.

    Live-feed fences (`_LIVE_DATA_FENCES`) are exempt from detection
    entirely: their bodies are refreshed every run and a notice on every run
    would be alert fatigue, not signal. Brief-derived fences
    (`_BRIEF_DERIVED_FENCES`) are deliberately NOT exempted here — a
    brief-driven shrink is rare, and the notice is exactly the useful signal
    an operator (or --shrink-policy=halt) needs; they are instead routed
    through `_is_template_authoritative` at the call site, so detection,
    the notice, and the `.lost.<sid>.md` sidecar all still fire — only the
    preserve-on-shrink outcome is refused.
    """
    if sid in _LIVE_DATA_FENCES:
        return None
    existing = _fence_body(existing_block)
    new = _fence_body(new_block)
    if not existing.strip():
        return None  # nothing to shrink from
    ex_len, new_len = len(existing), len(new)
    if ex_len == 0:
        return None

    reasons: list[str] = []
    # (a) length shrink > 50%
    if ex_len > 0 and new_len < ex_len / 2:
        reasons.append(
            f"body shrank {ex_len}->{new_len} bytes (>{50}% reduction)"
        )
    # (b) list-item delta >= 3
    ex_items = len(_LIST_ITEM_RE.findall(existing))
    new_items = len(_LIST_ITEM_RE.findall(new))
    if ex_items - new_items >= 3:
        reasons.append(f"lost {ex_items - new_items} list item(s) ({ex_items}->{new_items})")
    # (c) lost concrete paths / backtick identifiers
    ex_paths = set(_PATH_RE.findall(existing)) | set(_BACKTICK_IDENT_RE.findall(existing))
    new_paths = set(_PATH_RE.findall(new)) | set(_BACKTICK_IDENT_RE.findall(new))
    lost = ex_paths - new_paths
    if lost:
        sample = sorted(lost)[:3]
        more = f" (+{len(lost) - 3} more)" if len(lost) > 3 else ""
        reasons.append(f"lost concrete refs: {', '.join(sample)}{more}")

    if not reasons:
        return None
    return f"fence '{sid}': " + "; ".join(reasons)


def _extract_fenced_regions(content: str) -> dict[str, str] | str:
    """Extract all fenced regions from *content*.

    Returns a dict mapping ``section_id`` to the full fenced block (including
    the BEGIN and END markers) on success, or an error message string on failure.

    Failure conditions: unclosed BEGIN, duplicate section_id, mismatched END.
    """
    regions: dict[str, str] = {}
    lines = content.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        begin_match = _FENCE_BEGIN_RE.search(lines[i])
        if begin_match:
            sid = begin_match.group("sid")
            if sid in regions:
                return f"Duplicate section_id '{sid}'"
            block_lines = [lines[i]]
            i += 1
            closed = False
            while i < len(lines):
                end_match = _FENCE_END_RE.search(lines[i])
                if end_match:
                    end_sid = end_match.group("sid")
                    if end_sid != sid:
                        return f"Mismatched END: expected '{sid}', got '{end_sid}'"
                    block_lines.append(lines[i])
                    closed = True
                    i += 1
                    break
                # Check for nested BEGIN (not allowed)
                if _FENCE_BEGIN_RE.search(lines[i]):
                    nested_sid = _FENCE_BEGIN_RE.search(lines[i]).group("sid")
                    return f"Nested fence not allowed: '{nested_sid}' inside '{sid}'"
                block_lines.append(lines[i])
                i += 1
            if not closed:
                return f"Unclosed fence: '{sid}' has no END marker"
            regions[sid] = "".join(block_lines)
        else:
            i += 1
    return regions


def _is_machine_managed_merge_overwrite_path(rel_path: str, fresh_content: str) -> bool:
    """Return True when merge mode may safely full-replace a machine-managed file.

    Content-aware (2026-07-24, Gap 4): explicit-allowlist membership is always safe. Beyond
    that, a non-Markdown path is safe to full-replace UNLESS its freshly-rendered content
    already contains a real, engine-recognized AGENTTEAMS fence marker -- such a file (today:
    ``.goosehints``, hand-authored by ``_goosehints_content``; some ``.goose/recipes/*.yaml``,
    which inherit a fence from their source template's body) is deliberately designed for
    fence-merge, and full-replacing it would silently discard legitimate out-of-fence content.
    A blanket "any non-.md path" rule (the first version of this fix) does not hold: it
    corrupted `.goosehints` in exactly this way. ``.md`` paths are never eligible here --
    ``_normalize_generated_content`` already governs their fencing, and the explicit set is
    for ``.md`` files that need full-replace for reasons unrelated to file-type safety.
    """
    if rel_path in _MACHINE_MANAGED_MERGE_OVERWRITE_PATHS:
        return True
    if rel_path.endswith(".md"):
        return False
    return _FENCE_BEGIN_RE.search(fresh_content) is None




#: The section id `_normalize_generated_content` uses when it wraps an entire unfenced body.
_WHOLE_BODY_FENCE = "content"


def _split_at_last_fence_end(content: str) -> tuple[str, str]:
    """Split *content* immediately after the final fence END marker's line.

    Used by the whole-body migration branch to honour Merge Semantics rule 5 ("all content
    outside any fence marker in the on-disk file is preserved unconditionally") on a code path
    that otherwise replaces the file wholesale. For a whole-body-wrapped file the tail is
    everything the wrap did not cover — in practice the ``## Project-Specific Notes`` region.

    Args:
        content: A full file body.

    Returns:
        ``(prefix, tail)`` where *prefix* ends with the final END marker's newline. When the
        content carries no fence at all, or the marker is unterminated, returns
        ``(content, "")`` so callers fall back to leaving the content alone.
    """
    last = None
    for match in _FENCE_END_RE.finditer(content):
        last = match
    if last is None:
        return content, ""
    newline = content.find("\n", last.end())
    if newline == -1:
        return content, ""
    return content[: newline + 1], content[newline + 1:]


def _is_whole_body_migration(existing_regions: dict, new_regions: dict) -> bool:
    """Whether the on-disk file predates its template being split into named sections.

    A template with no fences gets its whole body wrapped in a single ``content`` fence at emit
    time. The moment that template gains a named section, the render stops being wrapped — so a
    team generated *before* the split has ``{content}`` on disk while the render has
    ``{invariant_core, ...}``.

    Merging those naively appends the named sections *alongside* the stale ``content`` block,
    leaving the file with two copies of the same section — measured 2026-07-31, two contradictory
    copies of an agent's "⛔ Do not modify or omit" contract. That is worse than not updating at
    all, and it is the real reason ~19 templates could not be fenced. (The nesting error
    originally blamed for it turned out to be a boundary bug in the fencing pass, since fixed.)

    Replacing wholesale is safe *because* of what a fence means: everything inside ``content`` is
    template-owned and already overwritten on every merge, so nothing a project authored lives
    there. Content outside it is untouched, exactly as before — see
    :func:`_split_at_last_fence_end`, which is what makes that last clause true. It was not true
    when this branch first shipped: taking ``new_rendered`` verbatim also took the *render's*
    out-of-fence tail, silently discarding the operator's ``## Project-Specific Notes`` — the one
    region emit advertises as "preserved verbatim across ``agentteams --update --merge``".

    Args:
        existing_regions: Fenced regions found on disk.
        new_regions: Fenced regions in the fresh render.

    Returns:
        True when the on-disk file is wrapped and the render is not.
    """
    return (
        set(existing_regions) == {_WHOLE_BODY_FENCE}
        and _WHOLE_BODY_FENCE not in new_regions
        and bool(new_regions)
    )


def _insert_section_at_render_position(
    merged: str,
    sid: str,
    block: str,
    render_order: list[str],
) -> tuple[str, str | None]:
    """Splice a new fenced section into ``merged`` where the fresh render puts it.

    Previously every new section was appended to the absolute end of the file, whatever its
    position in the render. That is silently wrong on an already-generated team: a template
    author adding a gate step meant to run *before* an existing instruction got correct placement
    on a fresh build and an inverted execution order on ``--update --merge``. It also made
    retrofitting fences into deployed files impossible, which is what stranded ~723 lines of
    template-owned prose across this project's own teams.

    Placement, in order of preference:

    1. immediately after the END marker of the nearest **preceding** section (per the render's
       order) that is present in ``merged``;
    2. immediately before the BEGIN marker of the nearest **following** section that is present;
    3. appended at the end — now a genuine last resort rather than the default.

    Existing content is never reordered. When the file's own section order contradicts the
    render's, the nearest consistent anchor is used and a notice is returned, because quietly
    imposing the template's order on a file someone deliberately arranged would be the same class
    of overreach this merge mode exists to avoid.

    Args:
        merged: The merged file content so far.
        sid: The section id being inserted.
        block: The full fenced block, BEGIN and END markers included.
        render_order: Section ids in the order the fresh render emits them.

    Returns:
        ``(new_merged, notice)``. ``notice`` is ``None`` for a clean anchored insertion.
    """
    block = block.rstrip("\n") + "\n"
    try:
        k = render_order.index(sid)
    except ValueError:
        return merged.rstrip("\n") + "\n\n" + block, None

    # 1. nearest preceding section present in the file -> insert after its END marker.
    for prev in reversed(render_order[:k]):
        m = re.search(rf"<!--\s*AGENTTEAMS:END\s+{re.escape(prev)}\s*-->[ \t]*\n?", merged)
        if m:
            return merged[: m.end()].rstrip("\n") + "\n\n" + block + "\n" + merged[m.end():].lstrip("\n"), None

    # 2. nearest following section present -> insert before its BEGIN marker.
    for nxt in render_order[k + 1:]:
        m = re.search(rf"<!--\s*AGENTTEAMS:BEGIN\s+{re.escape(nxt)}\b", merged)
        if m:
            return merged[: m.start()].rstrip("\n") + "\n\n" + block + "\n" + merged[m.start():], None

    # 3. no anchor exists in this file at all.
    return (
        merged.rstrip("\n") + "\n\n" + block,
        f"fence '{sid}': no anchoring section found on disk; appended at end of file",
    )


_MD_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)


def _detect_duplicate_sections(merged: str, added_sids: list[str]) -> list[str]:
    """Report headings that appear twice after new fences were added.

    The partial-adoption sibling of :func:`_is_whole_body_migration`. When a template fences a
    section that a team was generated *before*, the merge adds the fenced block while the
    deployed file's unfenced copy of the same section is preserved unconditionally — leaving two
    copies, one stale. Measured 2026-08-01 across this project's own team: 14 duplicated sections
    in 5 files, including four duplicated "⛔ Do not modify or omit" contracts.

    **Reported, never auto-resolved, and the asymmetry with the whole-body case is deliberate.**
    There, everything inside a ``content`` fence is template-owned *by definition*, so replacing
    it can lose nothing. Here the duplicated section has never been inside a fence, so a project
    may legitimately have edited it believing it was theirs. Choosing for them is how out-of-fence
    content gets destroyed — the defect fixed in this same module on the same day. Naming the
    collision lets an operator resolve each one by looking at it.

    Args:
        merged: The merged file content.
        added_sids: section_ids the merge inserted that were absent from the on-disk file.

    Returns:
        Human-readable notices, one per duplicated heading. Empty when nothing collides.
    """
    if not added_sids:
        return []
    headings = [m.group(0).strip() for m in _MD_HEADING_RE.finditer(merged)]
    seen: dict[str, int] = {}
    for heading in headings:
        seen[heading] = seen.get(heading, 0) + 1
    return [
        f"duplicate section {heading!r}: appears {count}x after adding "
        f"{', '.join(sorted(added_sids))} — the deployed file already carried this section "
        f"unfenced. Delete the unfenced copy; the fenced one is now template-owned."
        for heading, count in seen.items()
        if count > 1
    ]


def _strip_corrupt_fence_tail(content: str) -> str:
    """Remove an out-of-fence block that is terminated by an orphan ``END`` marker.

    Guards against a duplicate-append corruption observed across the fleet in
    ``parallelization.reference.md``: a section manually spliced *outside* the ``content``
    fence, followed by a second, orphaned ``AGENTTEAMS:END content`` marker (BEGIN=1, END=2).
    ``_extract_fenced_regions`` tolerates the orphan END, so ``_merge_fenced_content`` preserves
    that out-of-fence block as if it were user content — perpetuating the imbalance that the
    autosync fence-pairing gate then rejects.

    This strips exactly the corruption signature — a run of depth-0 (out-of-fence) lines
    immediately closed by an ``END`` marker with no matching open ``BEGIN`` — and nothing else.
    Legitimate out-of-fence content (any depth-0 region *not* terminated by an orphan END) is
    preserved byte-for-byte, and a well-formed file is returned unchanged.

    Args:
        content: The on-disk file content to sanitize before merging.

    Returns:
        ``content`` with any orphan-END-terminated out-of-fence block removed, or the
        original string unchanged when no such corruption is present.
    """
    lines = content.split("\n")
    kept: list[str] = []
    pending: list[str] = []   # depth-0 lines held until proven legitimate (flushed) or dropped
    stack: list[str] = []     # sids of currently-open fences
    changed = False
    for line in lines:
        mb = _FENCE_BEGIN_RE.search(line)
        me = _FENCE_END_RE.search(line)
        if mb:
            kept.extend(pending); pending = []
            kept.append(line)
            stack.append(mb.group("sid"))
        elif me and stack and stack[-1] == me.group("sid"):
            kept.extend(pending); pending = []
            kept.append(line)
            stack.pop()
        elif me and not stack:
            # Orphan END at depth 0: this and the out-of-fence block before it are the
            # duplicate-append corruption. Drop the held block and the orphan marker.
            pending = []
            changed = True
        elif stack:
            kept.append(line)          # inside a fence — always preserved verbatim
        else:
            pending.append(line)       # depth-0 content — tentatively held
    kept.extend(pending)               # trailing legitimate out-of-fence content survives
    if not changed:
        return content
    return "\n".join(kept)


def _merge_fenced_content(
    new_rendered: str,
    existing_on_disk: str,
    preserve_on_shrink: bool = False,
    *,
    file_is_unmodified: bool = False,
    rel_path: str = "",
) -> MergeResult:
    """Merge fenced sections from *new_rendered* into *existing_on_disk*.

    Template-owned (fenced) regions in the existing file are replaced with
    the corresponding regions from the new render.  All content outside any
    fence marker is preserved unchanged.

    Args:
        new_rendered:     Fully rendered file content from the render phase.
        existing_on_disk: Current content of the on-disk file.
        preserve_on_shrink: When True (shrink_policy="preserve"), a fence whose
            new render would materially shrink the existing body is left
            unchanged instead of being replaced — the richer enriched body is
            kept and recorded in ``sections_preserved``. Non-shrinking fences
            still receive their template updates. This is the respectful,
            non-destructive update path: no content is lost and no whole-file
            write is blocked.

    Returns:
        MergeResult describing what changed.  ``merged_content`` is empty on
        parse failure.
    """
    result = MergeResult()

    # Repair a duplicate-append corruption (orphan trailing END + out-of-fence duplicate) before
    # any parsing/merge, so the merge operates on well-formed input and cannot perpetuate the
    # imbalance that the autosync fence-pairing gate rejects. A well-formed file is untouched.
    existing_on_disk = _strip_corrupt_fence_tail(existing_on_disk)

    _existing_probe = _extract_fenced_regions(existing_on_disk)
    _new_probe = _extract_fenced_regions(new_rendered)
    if (isinstance(_existing_probe, dict) and isinstance(_new_probe, dict)
            and _is_whole_body_migration(_existing_probe, _new_probe)):
        # Structural migration, not an ordinary merge — see _is_whole_body_migration.
        # Merge Semantics rule 5 still applies: whatever sits outside the whole-body fence on
        # disk is the project's, not the template's, so it survives the replacement.
        prefix_new, _tail_new = _split_at_last_fence_end(new_rendered)
        _prefix_disk, tail_disk = _split_at_last_fence_end(existing_on_disk)
        preserved_tail = bool(tail_disk.strip())
        result.merged_content = (prefix_new + tail_disk) if preserved_tail else new_rendered
        result.sections_added = list(_new_probe)
        result.sections_orphaned = [_WHOLE_BODY_FENCE]
        result.shrink_notices.append(
            f"fence '{_WHOLE_BODY_FENCE}': this file predates its template being split into "
            f"named sections; the whole-body fence was replaced by "
            f"{', '.join(sorted(_new_probe))}. Everything inside it was template-owned"
            + (
                "; content outside it was carried over unchanged."
                if preserved_tail
                else "."
            )
        )
        return result

    # Reported, never applied: front matter lies outside every fence, so the merge below leaves
    # the on-disk version untouched. Recording the divergence is the whole remediation — the
    # defect was that a template `tools:` change reached already-generated teams silently.
    result.front_matter_drift = _detect_front_matter_drift(new_rendered, existing_on_disk)
    result.front_matter_drift += _detect_unfenced_drift(
        new_rendered, existing_on_disk, file_is_unmodified=file_is_unmodified
    )
    result.deleted_constraint_notices = _detect_deleted_constraints(
        new_rendered, existing_on_disk
    )

    # Parse existing file
    existing_regions = _extract_fenced_regions(existing_on_disk)
    if isinstance(existing_regions, str):
        # String return means error
        if "has no" in existing_regions and "END marker" in existing_regions:
            result.parse_errors.append(
                f"Existing file parse error: {existing_regions}"
            )
        elif not existing_regions:
            # _extract_fenced_regions returns empty dict for no fences
            pass
        else:
            result.parse_errors.append(
                f"Existing file parse error: {existing_regions}"
            )
        return result

    if not existing_regions:
        # W2: distinguish a truly unfenced legacy file from one written by
        # --bridge-refresh (AGENTTEAMS-BRIDGE namespace).  The latter needs a
        # targeted notice rather than the generic "legacy file" warning.
        if _BRIDGE_FENCE_BEGIN_RE.search(existing_on_disk):
            result.parse_errors.append(
                "No AGENTTEAMS fence markers detected — file contains AGENTTEAMS-BRIDGE "
                "fences (written by --bridge-refresh). Run --bridge-refresh to regenerate, "
                "or add AGENTTEAMS fence markers to enable --merge updates."
            )
        else:
            result.parse_errors.append(
                "No fence markers detected — legacy file. "
                "Use --overwrite to replace unconditionally, or add "
                "AGENTTEAMS fence markers manually."
            )
        # W13: the refusal above is safe but says nothing about WHAT was lost. Deleting a fence's
        # markers along with its rule is how a constitutional rule leaves a file without the
        # merge restoring it (probe D3) — the ordinary deleted-constraint detector checks only
        # unfenced text, precisely because fenced text is normally self-healing. Here it is not.
        result.deleted_constraint_notices += detect_deleted_fenced_constraints(
            new_rendered, existing_on_disk
        )
        return result

    # Parse new render
    new_regions = _extract_fenced_regions(new_rendered)
    if isinstance(new_regions, str):
        result.parse_errors.append(
            f"New render parse error: {new_regions}"
        )
        return result

    # Rebuild the existing file by replacing each fenced block in-place
    lines = existing_on_disk.splitlines(keepends=True)
    output_lines: list[str] = []
    i = 0
    replaced_sids: set[str] = set()

    while i < len(lines):
        begin_match = _FENCE_BEGIN_RE.search(lines[i])
        if begin_match:
            sid = begin_match.group("sid")
            # Skip the entire old fenced block
            i += 1
            while i < len(lines):
                if _FENCE_END_RE.search(lines[i]):
                    i += 1
                    break
                i += 1
            # Inject replacement or preserve orphan
            if sid in new_regions:
                if new_regions[sid] == existing_regions.get(sid, ""):
                    output_lines.append(new_regions[sid])
                    result.unchanged.append(sid)
                else:
                    # Plan 3: detect material shrink and queue a Notice.
                    notice = _detect_fence_shrink(
                        sid, existing_regions.get(sid, ""), new_regions[sid]
                    )
                    if (
                        notice
                        and preserve_on_shrink
                        and not _is_template_authoritative(sid, rel_path)
                    ):
                        # Respectful update: the new render would drop enriched
                        # content. Keep the existing body verbatim; surface a
                        # notice so the suppression is visible. No data lost, so
                        # no .lost.<sid>.md sidecar is needed.
                        output_lines.append(existing_regions[sid])
                        result.sections_preserved.append(sid)
                        result.shrink_notices.append(notice)
                    else:
                        output_lines.append(new_regions[sid])
                        result.sections_replaced.append(sid)
                        if notice:
                            result.shrink_notices.append(notice)
                            # W22 data-loss recovery: capture the pre-merge body
                            # so emit_all can write a .lost.<sid>.md sidecar.
                            result.lost_fence_bodies[sid] = _fence_body(
                                existing_regions.get(sid, "")
                            )
                replaced_sids.add(sid)
            else:
                # Orphaned: in existing but not in new render — leave in place.
                shadowed = _rename_suspect_sid(sid, set(new_regions))
                if shadowed is not None:
                    # A renamed template-authoritative fence. Preserving it would write a file
                    # carrying two contradictory copies of a security contract, so refuse the
                    # merge instead and name what happened. Refusing is safe: nothing is
                    # written, and the operator sees the rename rather than a duplicate-section
                    # notice framed as housekeeping (probe D4).
                    result.parse_errors.append(
                        f"security fence rename detected: on-disk section {sid!r} shadows "
                        f"template-authoritative section {shadowed!r}, whose body the template "
                        f"owns. Merging would leave BOTH bodies in the file. Rename {sid!r} "
                        f"back to {shadowed!r} (keeping any project additions outside the "
                        f"fence), or delete it so the template's copy is authoritative."
                    )
                    return result
                output_lines.append(existing_regions[sid])
                result.sections_orphaned.append(sid)
        else:
            output_lines.append(lines[i])
            i += 1

    merged = "".join(output_lines)

    # Insert sections that are new (in new render but not in existing file) AT THE POSITION THE
    # RENDER PUTS THEM — see _insert_section_at_render_position for why appending was wrong.
    render_order = list(new_regions.keys())
    for sid, block in new_regions.items():
        if sid not in replaced_sids and sid not in result.sections_orphaned:
            merged, notice = _insert_section_at_render_position(merged, sid, block, render_order)
            if not merged.endswith("\n"):
                merged += "\n"
            result.sections_added.append(sid)
            if notice:
                result.shrink_notices.append(notice)

    result.merged_content = merged
    result.duplicate_section_notices = _detect_duplicate_sections(merged, result.sections_added)
    return result



# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# W22 data-loss recovery -----------------------------------------------------

_SHRINK_NOTICE_SID_RE = re.compile(r"^fence '([^']+)':")


def _shrink_notice_sid(notice: str) -> str | None:
    """Extract the fence section_id from a shrink Notice string."""
    m = _SHRINK_NOTICE_SID_RE.match(notice)
    return m.group(1) if m else None


def _write_lost_fence_sidecars(
    backup_path: Path,
    rel_path: str,
    lost_bodies: dict[str, str],
) -> dict[str, str]:
    """Persist each lost fence body as ``<backup>/<rel_path>.lost.<sid>.md``.

    Returns a mapping of section_id → sidecar path (string, relative to repo
    root when possible, else absolute) so the caller can annotate Notices.
    Failures are non-fatal: the function returns whatever sidecars were
    written and skips the rest.
    """
    written: dict[str, str] = {}
    if not lost_bodies:
        return written
    try:
        backup_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written
    for sid, body in lost_bodies.items():
        if not body.strip():
            continue
        # Flatten the rel_path into the filename so sibling files never collide:
        # references/foo.md + sid=content → references/foo.md.lost.content.md
        sidecar = backup_path / f"{rel_path}.lost.{sid}.md"
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(sidecar, body)
        except OSError:
            continue
        try:
            written[sid] = str(sidecar.relative_to(Path.cwd()))
        except ValueError:
            written[sid] = str(sidecar)
    return written
