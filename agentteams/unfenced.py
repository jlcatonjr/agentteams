"""Everything outside the fences: where they begin and end, and what changed out there.

Carved out of ``fences.py`` on 2026-08-03 under CH-07, and — like the four carves before it —
**forced by an ordinary edit hitting the wall** rather than chosen: adding one shared predicate
took `fences.py` from 943 to 982 lines, inside the 25-line warning margin, and
``test_no_new_module_crowds_the_ceiling`` fired. The seam was already there.

`fences.py` is about *merging fenced regions*. This module is about the complement — the prose a
merge preserves verbatim, which is exactly the text no `--update --merge` will ever restore. Two
questions live here and nowhere else:

* **Has untouched template prose drifted?** (:func:`_detect_unfenced_drift`) — reported only for
  files nobody has edited since generation.
* **Has a rule vanished?** (:func:`_detect_deleted_constraints`) — reported regardless of edit
  state, because a tampered file is modified by definition.

It owns the fence-boundary patterns because locating the boundary *is* how you find what lies
outside it. The dependency runs one way: `fences.py` imports from here and re-exports, so every
existing ``from agentteams.fences import unfenced_lines`` still resolves.
"""

from __future__ import annotations

import re

from agentteams.front_matter_merge import _YAML_FM_RE

# ---------------------------------------------------------------------------
# Fence boundaries — the patterns that separate managed regions from prose.
# ---------------------------------------------------------------------------

_FENCE_BEGIN_RE = re.compile(
    r"<!-- AGENTTEAMS:BEGIN (?P<sid>[a-z][a-z0-9_]*) v=\d+ -->",
)
_FENCE_END_RE = re.compile(
    r"<!-- AGENTTEAMS:END (?P<sid>[a-z][a-z0-9_]*) -->",
)


#: Language that carries an obligation, a prohibition, or a capability limit — i.e. a *rule* as
#: opposed to prose about one. Deliberately narrow: it should fire on "HALT is final" and "you are
#: read-only", not on ordinary descriptive text.
#:
#: Homed in production rather than in a test so that :func:`_detect_deleted_constraints` and
#: ``tests/test_fence_coverage_policy.py`` cannot disagree about what a rule is. A notice claiming
#: "a rule was deleted" and a ratchet counting "rules outside a fence" have to mean the same thing
#: by the same definition, or neither number means anything.
CONSTRAINT_BEARING_RE = re.compile(
    r"⛔|\bread-only\b|PRIORITY LEVEL|\bHALT\b|MUST NOT|\bnever\b", re.IGNORECASE
)

#: A numbered, bolded rule — the shape every Constitutional Rule uses.
#:
#: Tracked by SHAPE because the keyword set missed 15 of the orchestrator's 17 rules,
#: including "1. **`@security` before destructive operations**" — the rule the entire
#: enforced stack rests on. It says "require", not "never", so it matched nothing.
#: Those rules are unfenced by design (projects extend the list), so deleting one was
#: neither restored NOR reported.
#:
#: Broadening the vocabulary instead would trade a false negative for false positives on
#: ordinary prose, and a notice that fires on rewording is one people learn to skip. A
#: numbered bolded rule is structurally identifiable and its disappearance is unambiguous.
NUMBERED_RULE_RE = re.compile(r"^\d+\.\s+\*\*")

#: Shortest constraint-bearing line worth tracking. Below this a "line" is a fragment — a table
#: cell or a heading — and its absence says nothing about whether a rule survived.
_CONSTRAINT_MIN_CHARS = 30


def is_trackable_constraint_line(line: str) -> bool:
    """Could *line* state a rule at all, before any vocabulary is considered?

    The shared half of the definition :data:`CONSTRAINT_BEARING_RE`'s docstring demands. Two
    consumers — this module's :func:`_detect_deleted_constraints` and the unfenced-constraint
    ratchet — both claimed to measure "constraint-bearing lines" while applying different
    filters: the ratchet excluded markdown table rows inline, the detector did not. The result
    was a "deleted rule" notice on 5 of 54 files for text like::

        | invariant_core | FENCED | ⛔ contract: responsibilities + codes |

    which matches ⛔, clears the length floor, and is a *manifest of where rules live* rather
    than a rule. Its absence from a deployed file carries no information.

    Two exclusions, and only these:

    * **Table rows.** Leading `|` after stripping. A row is structure, not obligation.
    * **Fragments.** Under :data:`_CONSTRAINT_MIN_CHARS` collapsed characters, a "line" is a
      cell or a heading.

    Whitespace is collapsed rather than merely stripped, matching the haystack the detector
    searches. Verified equivalent to the ratchet's previous ``strip()`` form across the whole
    template library on 2026-08-03 — 43 files, 159 lines, identical under both.

    What this deliberately does **not** decide is which *matcher* a consumer then applies. The
    detector uses :data:`CONSTRAINT_BEARING_RE` or :data:`NUMBERED_RULE_RE`; the ratchet uses
    only the former. Unifying that is a 130-line re-baselining across 24 templates, tracked
    separately.

    Args:
        line: A single raw line of template text.

    Returns:
        True if the line is eligible to be judged a rule.
    """
    collapsed = " ".join(line.split())
    return len(collapsed) >= _CONSTRAINT_MIN_CHARS and not collapsed.startswith("|")


def _detect_deleted_constraints(new_rendered: str, existing_on_disk: str) -> list[str]:
    """Report template rules that have vanished from the deployed file.

    The complement of :func:`_detect_unfenced_drift`, and the reason that one is not enough. Drift
    is reported only for files nobody has edited since generation — correct for its purpose, since
    nagging about prose a tool deliberately preserves is how a notice gets muted, but it means the
    mechanism that would surface a deleted constitutional rule is switched off in exactly the case
    where one was deleted. A tampered file is modified by definition.

    This fires regardless of modification state, and stays quiet by being specific: it reports only
    when a **constraint-bearing** line (:data:`CONSTRAINT_BEARING_RE`) present in the template's
    *unfenced* prose is absent from the deployed file.

    Measured behaviour on the three edit shapes that actually occur:

    - Ordinary prose reworded — **silent**. The line carries no constraint language, so it is
      never tracked. This is the property that keeps the notice worth reading.
    - A project *appends* its own rule — **silent**. Extension is the normal, sanctioned case and
      the check only looks for template text going missing, never for text arriving.
    - A template rule *reworded* in place — **fires**, and this is a known imprecision rather than
      a defect: the rule as the template writes it is genuinely no longer in the file, and nothing
      short of semantic comparison can tell "rephrased the same obligation" from "weakened it".
      Firing is the safe direction, and the notice quotes the missing text so the reader resolves
      it in seconds. Rewording a shipped constitutional rule is rare; deleting one is the case
      this exists for.

    Only unfenced template text is checked. Fenced content is restored by the merge anyway, so its
    absence is self-healing and not worth a notice.

    Args:
        new_rendered: Freshly rendered file content.
        existing_on_disk: Current on-disk content.

    Returns:
        One notice per missing rule, capped so a wholesale rewrite reports a count rather than a
        wall of text. Empty when nothing is missing.
    """
    template_prose = _unfenced_regions(new_rendered)
    if not template_prose.strip():
        return []
    haystack = " ".join(existing_on_disk.split())

    missing: list[str] = []
    for raw_line in unfenced_lines(new_rendered):
        line = " ".join(raw_line.split())
        if not is_trackable_constraint_line(raw_line):
            continue
        if not (CONSTRAINT_BEARING_RE.search(line) or NUMBERED_RULE_RE.match(line)):
            continue
        if line not in haystack:
            missing.append(line)

    if not missing:
        return []
    shown = missing[:3]
    notices = [
        f"deleted rule: template text {m[:110]!r} is not present in this file — an unfenced rule "
        f"is preserved-forever, so a merge will not restore it"
        for m in shown
    ]
    if len(missing) > len(shown):
        notices.append(
            f"deleted rule: {len(missing) - len(shown)} further constraint-bearing template "
            f"line(s) are also absent"
        )
    return notices


def unfenced_lines(content: str):
    """Yield each line of *content* that lies outside every fence and outside the front matter.

    Single-sourced deliberately. Two test modules had their own copy of this walk, and both
    carried the same latent bug: the opening-delimiter branch was guarded only by ``lineno <= 3``,
    so a *three-line* front matter (``---`` / one key / ``---``) re-entered front-matter mode on
    its closing delimiter instead of leaving it, and the walk then yielded nothing at all. Real
    agent templates have front matter dozens of lines long, so the closing ``---`` sits well past
    line 3 and the bug never showed — it surfaced the moment production code reused the walk on a
    minimal fixture. The guard now requires ``not in_front_matter``, and the tests import this
    rather than reimplementing it.

    Args:
        content: A full file body.

    Yields:
        Each line outside both the YAML front matter and every AGENTTEAMS fence. Marker lines and
        front-matter delimiters are not yielded.
    """
    in_fence = False
    in_front_matter = False
    for lineno, line in enumerate(content.splitlines(), 1):
        if line.strip() == "---" and lineno <= 3 and not in_front_matter:
            in_front_matter = True
            continue
        if in_front_matter and line.strip() == "---":
            in_front_matter = False
            continue
        if _FENCE_BEGIN_RE.search(line):
            in_fence = True
            continue
        if _FENCE_END_RE.search(line):
            in_fence = False
            continue
        if not in_fence and not in_front_matter:
            yield line


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
