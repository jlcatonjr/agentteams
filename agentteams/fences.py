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
from typing import Any

from agentteams.atomicio import _atomic_write_text

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

    @property
    def has_errors(self) -> bool:
        return bool(self.parse_errors)

    @property
    def content_changed(self) -> bool:
        return bool(self.sections_replaced or self.sections_added)


# ---------------------------------------------------------------------------
# Section-fencing internals
# ---------------------------------------------------------------------------

_FENCE_BEGIN_RE = re.compile(
    r"<!-- AGENTTEAMS:BEGIN (?P<sid>[a-z][a-z0-9_]*) v=\d+ -->",
)
_FENCE_END_RE = re.compile(
    r"<!-- AGENTTEAMS:END (?P<sid>[a-z][a-z0-9_]*) -->",
)
# W2: detect AGENTTEAMS-BRIDGE fences (written by --bridge-refresh) so the
# --merge path can emit a targeted notice instead of the generic "legacy file" warning.
_BRIDGE_FENCE_BEGIN_RE = re.compile(r"<!--\s*AGENTTEAMS-BRIDGE:BEGIN\s+")
_YAML_FM_RE = re.compile(r"^(---\n.+?\n---\n)", re.DOTALL)

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

    Live-feed fences (`_LIVE_DATA_FENCES`) are exempt: their bodies are
    refreshed each run from upstream feeds and rotation is expected. The
    sidecar mechanism in `lost_fence_bodies` still preserves the prior body
    on disk if real recovery is ever needed.
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


#: Front-matter keys excluded from drift reporting because they are project-interpolated by
#: construction: every generated file's `name`/`description` embeds {PROJECT_NAME}, so they
#: differ between template and render in EVERY file. Reporting them would make the notice fire
#: universally, and a notice that fires on everything gets muted — which is the same silence
#: this detection exists to end.
_DRIFT_EXEMPT_FRONT_MATTER_KEYS: frozenset[str] = frozenset({"name", "description"})

#: Matches a top-level `key: value` line inside a YAML front-matter block. Deliberately not a
#: YAML parse: front matter here is flat scalars plus inline lists, PyYAML is a test-only
#: dependency, and this module is on the stdlib-only base install path.
_FM_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$", re.MULTILINE)


def _front_matter_keys(content: str) -> dict[str, str]:
    """Return the top-level front-matter keys of ``content`` mapped to their raw values.

    Args:
        content: A full file body, with or without a YAML front-matter block.

    Returns:
        ``{key: raw_value}`` for the first front-matter block, or ``{}`` when there is none.
    """
    match = _YAML_FM_RE.match(content)
    if not match:
        return {}
    return {k: v.strip() for k, v in _FM_KEY_RE.findall(match.group(1))}


def _detect_front_matter_drift(new_rendered: str, existing_on_disk: str) -> list[str]:
    """Report front-matter keys whose template value differs from the on-disk one.

    Front matter lies outside every AGENTTEAMS fence, so merge preserves the on-disk version
    verbatim. That is correct — it is how a project keeps its own edits — but it also means a
    template change to a key like ``tools:`` reaches new teams only, and silently. Verified
    2026-07-30 against two downstream repos: the fenced body updated, the tool grant did not,
    and nothing in the run output said so.

    Scope is deliberately narrow. Only *keys* are compared, never values-as-diff, and
    :data:`_DRIFT_EXEMPT_FRONT_MATTER_KEYS` is skipped. Unfenced *prose* drift is not reported
    at all: prose below the front matter is genuinely user-owned, and without a provenance
    mechanism the format does not have, an edit cannot be told apart from intended authorship.
    Front matter is the case where the template is the de-facto owner and the failure was
    demonstrated.

    Args:
        new_rendered: Freshly rendered file content.
        existing_on_disk: Current on-disk content.

    Returns:
        Human-readable notices, one per drifted key. Empty when nothing drifted.
    """
    fresh = _front_matter_keys(new_rendered)
    current = _front_matter_keys(existing_on_disk)
    if not fresh or not current:
        return []
    notices: list[str] = []
    for key, value in fresh.items():
        if key in _DRIFT_EXEMPT_FRONT_MATTER_KEYS:
            continue
        # The notice carries the exact replacement line. Auto-applying it was designed and then
        # withdrawn: `tools:` is a privilege declaration, so writing it unattended is a
        # privilege-escalation path, and the same command runs against consumer repos — one of
        # which has no version control at all, making a bad write unrecoverable. A human applying
        # a one-line edit they can see is the right cost here.
        if key not in current:
            notices.append(
                f"front matter: template adds {key!r} (absent on disk) — add `{key}: {value}`"
            )
        elif current[key] != value:
            notices.append(
                f"front matter: {key!r} differs — template {value!r}, on disk {current[key]!r}"
                f" — to adopt it, set `{key}: {value}`"
            )
    return notices


#: Region-level drift, reported only for files the operator has not touched. See
#: :func:`_detect_unfenced_drift`.
_UNFENCED_DRIFT_MIN_CHARS = 40


def _unfenced_regions(content: str) -> str:
    """Return everything outside AGENTTEAMS fences and outside the front-matter block.

    Args:
        content: A full file body.

    Returns:
        The concatenated unfenced prose, whitespace-collapsed for comparison. Front matter is
        excluded because :func:`_detect_front_matter_drift` already reports it at key level, and
        reporting the same divergence twice in two vocabularies is noise.
    """
    body = content
    fm = _YAML_FM_RE.match(body)
    if fm:
        body = body[fm.end():]
    # Drop every fenced block; what remains is the prose the merge preserves verbatim.
    body = re.sub(
        r"<!--\s*AGENTTEAMS:BEGIN\s+[A-Za-z0-9_]+.*?-->.*?<!--\s*AGENTTEAMS:END\s+[A-Za-z0-9_]+\s*-->",
        " ", body, flags=re.DOTALL,
    )
    return re.sub(r"\s+", " ", body).strip()


def _detect_unfenced_drift(
    new_rendered: str,
    existing_on_disk: str,
    *,
    file_is_unmodified: bool,
) -> list[str]:
    """Report that template prose outside the fences has moved on — for untouched files only.

    The previous round deferred this on the stated grounds that "an edit cannot be told apart
    from intended authorship" without provenance the format lacks. That was wrong:
    ``references/build-log.json`` records a per-file hash of what was last emitted, and
    :func:`agentteams.drift.detect_user_customizations` already compares it. When the on-disk
    hash still matches, nobody has edited the file since generation, so any divergence in its
    unfenced prose is template drift by elimination — not authorship.

    For a file that *has* been modified, this stays silent. That silence is correct: the prose is
    the operator's, and a tool that nags about content it deliberately preserves teaches people
    to stop reading its output.

    Args:
        new_rendered: Freshly rendered file content.
        existing_on_disk: Current on-disk content.
        file_is_unmodified: Whether the build-log hash still matches the file on disk.

    Returns:
        A single-item notice list when untouched prose diverged, else empty.
    """
    if not file_is_unmodified:
        return []
    fresh = _unfenced_regions(new_rendered)
    current = _unfenced_regions(existing_on_disk)
    if fresh == current:
        return []
    if len(fresh) < _UNFENCED_DRIFT_MIN_CHARS and len(current) < _UNFENCED_DRIFT_MIN_CHARS:
        # Both sides are essentially empty; a whitespace-level difference is not drift.
        return []
    return [
        "unfenced prose: template text outside the fences differs from this file, which is "
        "unmodified since generation — the template update did not reach it"
    ]


#: Front-matter keys that declare CAPABILITY rather than metadata. Excluded from automatic
#: application even when three-way provenance proves the project never edited the key: proving
#: nobody touched `tools:` is not the same as having authority to grant a downstream agent shell
#: access. These are always reported as proposals for a human to apply.
_CAPABILITY_FRONT_MATTER_KEYS: frozenset[str] = frozenset({"tools", "model", "agents"})


def _merge_front_matter(
    rendered: str,
    on_disk: str,
    baseline: dict[str, str] | None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Three-way merge of a file's YAML front matter.

    Front matter cannot be fenced — YAML must be the literal first bytes of the file, before any
    HTML comment — so it needs a different mechanism rather than a better fence. Comparing the
    template's value, the on-disk value and the value emitted *last time* distinguishes "the
    project chose this" from "nobody has touched it since generation", which a two-way comparison
    cannot.

    That distinction is the whole point. An earlier `--sync-front-matter` design was withdrawn
    because, without a baseline, applying the template's value would overwrite deliberate project
    choices. With one, the safe subset is provable.

    | template vs baseline | on-disk vs baseline | outcome                          |
    |----------------------|---------------------|----------------------------------|
    | unchanged            | anything            | keep the on-disk value           |
    | changed              | unchanged           | apply the template's value       |
    | changed              | changed             | conflict — keep on-disk, report  |
    | new key              | absent              | add it                           |

    Capability keys (:data:`_CAPABILITY_FRONT_MATTER_KEYS`) are never applied automatically; they
    are reported as proposals however clean their provenance.

    Args:
        rendered: Freshly rendered file content.
        on_disk: Current on-disk content.
        baseline: Front matter as emitted at last generation, or ``None`` when unknown (an older
            build log). Without it nothing is applied — an unknown baseline is treated as
            "the project may have edited everything", which is the conservative reading.

    Returns:
        ``(merged_keys, applied_notices, proposal_notices)``.
    """
    fresh = _front_matter_keys(rendered)
    current = _front_matter_keys(on_disk)
    if not fresh or not current:
        return current, [], []

    merged = dict(current)
    applied: list[str] = []
    proposals: list[str] = []

    for key, template_value in fresh.items():
        if key in _DRIFT_EXEMPT_FRONT_MATTER_KEYS:
            continue
        disk_value = current.get(key)
        if disk_value == template_value:
            continue

        base_value = baseline.get(key) if baseline else None
        untouched = baseline is not None and disk_value == base_value
        template_moved = baseline is None or base_value != template_value

        if not template_moved:
            continue                                    # template unchanged; project's value wins

        if key in _CAPABILITY_FRONT_MATTER_KEYS:
            proposals.append(
                f"front matter: {key!r} is a capability declaration — template has "
                f"{template_value}, on disk {disk_value!r}. Not applied automatically; "
                f"set `{key}: {template_value}` to adopt it."
            )
        elif untouched:
            merged[key] = template_value
            applied.append(
                f"front matter: {key!r} updated to {template_value} "
                f"(unmodified since generation)"
            )
        elif disk_value is None and baseline is not None:
            # Only with a baseline: absent-on-disk could equally mean "deliberately removed",
            # and an unknown baseline must not be read as permission.
            merged[key] = template_value
            applied.append(f"front matter: added {key!r} = {template_value}")
        else:
            proposals.append(
                f"front matter: {key!r} changed in BOTH template and this file — template "
                f"{template_value}, on disk {disk_value!r}. Kept yours; reconcile by hand."
            )
    return merged, applied, proposals


def _render_front_matter(content: str, keys: dict[str, str]) -> str:
    """Return ``content`` with its front-matter block rewritten from ``keys``.

    Key order follows the existing block so a merge never reshuffles a file the project reads.
    A key added by the merge is appended after the ones already present.

    Args:
        content: The file whose front matter is being rewritten.
        keys: The merged key/value mapping.

    Returns:
        The file with its front matter replaced; unchanged when it has none.
    """
    match = _YAML_FM_RE.match(content)
    if not match:
        return content
    existing_order = list(_front_matter_keys(content))
    ordered = existing_order + [k for k in keys if k not in existing_order]
    body = "\n".join(f"{k}: {keys[k]}" for k in ordered if k in keys)
    return f"---\n{body}\n---\n" + content[match.end():]


#: The section id `_normalize_generated_content` uses when it wraps an entire unfenced body.
_WHOLE_BODY_FENCE = "content"


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
    there. Content outside it is untouched, exactly as before.

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


def _merge_fenced_content(
    new_rendered: str,
    existing_on_disk: str,
    preserve_on_shrink: bool = False,
    *,
    file_is_unmodified: bool = False,
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

    _existing_probe = _extract_fenced_regions(existing_on_disk)
    _new_probe = _extract_fenced_regions(new_rendered)
    if (isinstance(_existing_probe, dict) and isinstance(_new_probe, dict)
            and _is_whole_body_migration(_existing_probe, _new_probe)):
        # Structural migration, not an ordinary merge — see _is_whole_body_migration.
        result.merged_content = new_rendered
        result.sections_added = list(_new_probe)
        result.sections_orphaned = [_WHOLE_BODY_FENCE]
        result.shrink_notices.append(
            f"fence '{_WHOLE_BODY_FENCE}': this file predates its template being split into "
            f"named sections; the whole-body fence was replaced by "
            f"{', '.join(sorted(_new_probe))}. Everything inside it was template-owned."
        )
        return result

    # Reported, never applied: front matter lies outside every fence, so the merge below leaves
    # the on-disk version untouched. Recording the divergence is the whole remediation — the
    # defect was that a template `tools:` change reached already-generated teams silently.
    result.front_matter_drift = _detect_front_matter_drift(new_rendered, existing_on_disk)
    result.front_matter_drift += _detect_unfenced_drift(
        new_rendered, existing_on_disk, file_is_unmodified=file_is_unmodified
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
                    if notice and preserve_on_shrink:
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
                # Orphaned: in existing but not in new render — leave in place
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
