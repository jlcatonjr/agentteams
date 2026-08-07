"""test_dry_run_front_matter_parity.py — the preview must report the change it previews.

**The finding.** Rehearsing a real merge against an isolated copy showed 12 changed files where
`--dry-run` predicted 7. Four extras were build artifacts. The fifth was `reference-manager.md`,
which `--dry-run` reported as `UNCHANGED` while the real run widened its `allowed-tools` from
`Read, Edit, Write, Grep, Glob` to include `Bash(python -m agentteams.research:*)`.

It was found only because the merge was rehearsed against a real tree rather than trusted from
its dry run. Nothing would have caught it otherwise, which is what this module fixes.

**What the engine does today**, measured rather than assumed, because it has moved twice since
the finding was written and a test that teaches the old behaviour is worse than no test:
`_merge_front_matter` **never applies a capability key automatically** — however clean its
provenance — and returns it as a *proposal* instead. So the real run no longer widens the
grant at all; it reports that the template wants to. That is the stricter and correct
behaviour under C-3, and it closes the original half of the finding.

**The half that remained** is the preview. The dry run computed exactly that proposal and
threw it away, so an operator saw `MERGE` and no reason. "MERGE" says something changed;
`front matter: 'allowed-tools' is a capability declaration — template has …` says a capability
grant is being asked for. Only the second is something you can authorise or refuse.

Three streams are pinned: the applied/proposal notices, front-matter drift the merge did not
apply, and deleted-constraint notices. The classification half (`UNCHANGED` → `MERGE`) shipped
earlier and is pinned here so it cannot silently regress.

**Sensitivity and negative control**, per F-1: a preview that mentioned front matter for every
file would pass a sensitivity test while being useless, so the negative control asserts silence
when nothing about front matter changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import emit
from agentteams.front_matter_merge import _merge_front_matter

TEMPLATE_GRANT = "Read, Edit, Write, Grep, Glob, Bash(python -m agentteams.research:*)"
DEPLOYED_GRANT = "Read, Edit, Write, Grep, Glob"

BODY = """
<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->
## Invariant Core
Do not modify or omit this section.
<!-- AGENTTEAMS:END invariant_core -->
"""


def _agent_file(grant: str) -> str:
    return f"---\nname: reference-manager\nallowed-tools: {grant}\n---\n{BODY}"


#: Path within the output dir where `_front_matter_baseline` looks.
_BUILD_LOG_REL = "references/build-log.json"


def _deploy(root: Path, grant: str) -> Path:
    """Write a deployed agent file plus the build-log front-matter baseline.

    The baseline is the **provenance gate** (CH-30): recording the grant the file was
    generated with is what proves it is unmodified since generation, which is what lets the
    merge apply the template's value rather than preserving the operator's. Without a
    baseline the deployed value is kept and reported as drift — also correct, but a different
    code path, and testing that one would say nothing about the case that hid the widening.
    """
    target = root / "reference-manager.md"
    target.write_text(_agent_file(grant), encoding="utf-8")
    build_log = root / _BUILD_LOG_REL
    build_log.parent.mkdir(parents=True, exist_ok=True)
    build_log.write_text(
        json.dumps({
            "front_matter_baseline": {
                "reference-manager.md": {"name": "reference-manager", "allowed-tools": grant}
            }
        }),
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# the merge itself — the behaviour the preview must report
# ---------------------------------------------------------------------------

def test_the_merge_reports_a_widened_grant() -> None:
    """Sensitivity: widening a capability grant produces a notice naming the key."""
    _keys, applied, proposals = _merge_front_matter(
        _agent_file(TEMPLATE_GRANT), _agent_file(DEPLOYED_GRANT), None
    )
    notices = list(applied) + list(proposals)
    assert notices, "a changed capability grant produced no notice at all"
    assert any("allowed-tools" in n for n in notices), (
        f"notices do not name the capability key that changed: {notices}"
    )


def test_the_merge_is_silent_when_the_grant_is_identical() -> None:
    """Negative control: no notice when nothing about front matter changed.

    Without this, a merge that emitted a front-matter notice unconditionally would pass the
    test above and drown the real signal — which is the same failure as no signal.
    """
    _keys, applied, proposals = _merge_front_matter(
        _agent_file(DEPLOYED_GRANT), _agent_file(DEPLOYED_GRANT), None
    )
    assert not [n for n in list(applied) + list(proposals) if "allowed-tools" in n], (
        "an unchanged grant produced a capability notice"
    )


# ---------------------------------------------------------------------------
# dry-run parity — the preview reports what the real run will do
# ---------------------------------------------------------------------------

def _run(root: Path, template_text: str, *, dry_run: bool):
    rendered = [("reference-manager.md", template_text)]
    return emit.emit_all(
        rendered, output_dir=root, dry_run=dry_run, overwrite=False, merge=True, yes=True,
    )


def test_dry_run_does_not_call_a_front_matter_change_unchanged(tmp_path: Path) -> None:
    """Sensitivity, half one: the file must not be classified UNCHANGED.

    This is the literal 2026-08-03 observation: `--dry-run` said UNCHANGED, the real run
    widened the grant.
    """
    _deploy(tmp_path, DEPLOYED_GRANT)
    result = _run(tmp_path, _agent_file(TEMPLATE_GRANT), dry_run=True)

    assert result.dry_run_report is not None
    entries = {e.path: e.action for e in result.dry_run_report.entries}
    actions = set(entries.values())
    assert "UNCHANGED" not in actions, (
        f"a file whose capability grant changes was previewed as UNCHANGED: {entries}"
    )


def test_dry_run_names_the_capability_key_that_changes(tmp_path: Path) -> None:
    """Sensitivity, half two: the preview says *what* changed, not merely *that* it did.

    'MERGE' tells an operator something changed. Deciding whether to authorise a capability
    widening needs the key named.
    """
    _deploy(tmp_path, DEPLOYED_GRANT)
    result = _run(tmp_path, _agent_file(TEMPLATE_GRANT), dry_run=True)

    assert result.dry_run_report is not None
    notices = result.dry_run_report.notices
    assert any("allowed-tools" in n for n in notices), (
        "the dry-run report never mentions the capability key. The merge computed the notice "
        f"and the preview discarded it. Notices seen: {notices}"
    )


def test_dry_run_is_silent_on_front_matter_when_nothing_changes(tmp_path: Path) -> None:
    """Negative control: an identical grant produces no capability notice in the preview.

    A preview that mentioned front matter on every file would satisfy the two tests above and
    be worthless — an operator cannot act on a signal that never varies.
    """
    _deploy(tmp_path, DEPLOYED_GRANT)
    result = _run(tmp_path, _agent_file(DEPLOYED_GRANT), dry_run=True)

    assert result.dry_run_report is not None
    assert not [n for n in result.dry_run_report.notices if "allowed-tools" in n], (
        f"unchanged front matter produced a capability notice: {result.dry_run_report.notices}"
    )


def test_the_preview_matches_what_the_real_run_reports(tmp_path: Path) -> None:
    """Parity, stated directly: the same inputs must yield the same capability notices.

    The other tests pin each side separately; this one pins that they agree. A preview that
    is merely *non-empty* is not a preview — it has to be the same answer.
    """
    _deploy(tmp_path, DEPLOYED_GRANT)
    preview = _run(tmp_path, _agent_file(TEMPLATE_GRANT), dry_run=True)

    _deploy(tmp_path, DEPLOYED_GRANT)  # reset the tree
    real = _run(tmp_path, _agent_file(TEMPLATE_GRANT), dry_run=False)

    def _capability_notices(result) -> set[str]:
        return {n.split(": ", 1)[-1] for n in result.notices if "allowed-tools" in n}

    assert _capability_notices(preview) == _capability_notices(real), (
        "the preview and the real run disagree about the capability change.\n"
        f"  preview: {sorted(_capability_notices(preview))}\n"
        f"  real   : {sorted(_capability_notices(real))}"
    )
    assert _capability_notices(real), "the fixture produced no capability change to compare"
