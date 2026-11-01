"""A fence unedited since generation takes the template update; it is never shrink-preserved.

**The defect (ops-team, 2026-11-01).** ``.goose/recipes/vuln-remediation.yaml``'s ``daily_remediation`` fence was
byte-identical to the template render at 9c6b7697 (2026-10-23). Every later ``--update --merge`` kept it, because
:func:`agentteams.fences._detect_fence_shrink` reported "lost concrete refs: failed, refused" (the old body
backticked them, the v=6 body discusses them in prose) and the default ``--shrink-policy=preserve`` keeps a body on
shrink. The deployed agent ran a nine-day-old workflow.

The fixture ``fixtures/fence_baseline/daily_remediation.9c6b7697.md`` is that fence block as the 9c6b7697 template
rendered it; the new side is the current template's block.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import emit
from agentteams import fence_baseline as fb
from agentteams.fences import _detect_fence_shrink, _extract_fenced_regions

_ROOT = Path(__file__).resolve().parent.parent
_SID = "daily_remediation"
_OLD = (Path(__file__).parent / "fixtures" / "fence_baseline" / "daily_remediation.9c6b7697.md").read_text(
    encoding="utf-8")
_NEW = _extract_fenced_regions(
    (_ROOT / "agentteams" / "templates" / "domain" / "vuln-remediation.template.md").read_text(encoding="utf-8")
)[_SID]
_REL = "vuln-remediation.agent.md"


def _doc(block: str) -> str:
    return ("---\nname: Vuln Remediation\n---\n# Vuln Remediation\n\n" + block
            + "\n## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — notes.\n")


def _deploy(root: Path, on_disk_block: str, *, baseline: dict | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / _REL
    target.write_text(_doc(on_disk_block), encoding="utf-8")
    log: dict = {"file_hashes": {}}
    if baseline is not None:
        log[fb.BUILD_LOG_KEY] = baseline
    (root / "references").mkdir(exist_ok=True)
    (root / "references" / "build-log.json").write_text(json.dumps(log), encoding="utf-8")
    return target


def _authored_old() -> dict:
    return {_REL: {_SID: fb.digest(_OLD)}}


def _merge(root: Path, *, backup: Path | None, dry_run: bool = False, twin_drop: bool | None = None):
    return emit.emit_all([(_REL, _doc(_NEW))], output_dir=root, dry_run=dry_run, overwrite=False, merge=True,
                         yes=True, backup_path=backup, twin_drop=twin_drop)


@pytest.fixture(autouse=True)
def _precondition():
    if _detect_fence_shrink(_SID, _OLD, _NEW) is None:
        pytest.skip("the current template no longer reads as a shrink of the 9c6b7697 block")


def test_the_real_case_reproduces_the_shrink_notice():
    assert "lost concrete refs: failed, refused" in _detect_fence_shrink(_SID, _OLD, _NEW)


def test_unedited_fence_is_replaced_and_its_old_body_saved(tmp_path):
    root, backup = tmp_path / "agents", tmp_path / "backup"
    target = _deploy(root, _OLD, baseline=_authored_old())
    result = _merge(root, backup=backup)
    text = target.read_text(encoding="utf-8")
    assert _NEW in text and _OLD not in text
    line = next(n for n in result.notices if f"fence '{_SID}'" in n)
    assert "replaced: unedited since generation" in line
    assert "retained existing enriched body" not in line
    sidecars = list(backup.rglob("*.lost.*"))
    assert len(sidecars) == 1 and "Daily Remediation workflow" in sidecars[0].read_text(encoding="utf-8")


def test_edited_fence_is_preserved_as_before(tmp_path):
    root, backup = tmp_path / "agents", tmp_path / "backup"
    edited = _OLD.replace("## Daily Remediation workflow\n", "## Daily Remediation workflow\n\nOur own step.\n")
    assert edited != _OLD
    target = _deploy(root, edited, baseline=_authored_old())
    result = _merge(root, backup=backup)
    assert edited in target.read_text(encoding="utf-8")
    line = next(n for n in result.notices if f"fence '{_SID}'" in n)
    assert "retained existing enriched body" in line and "unedited" not in line


@pytest.mark.parametrize("baseline", [None, {}, {_REL: {}}, {_REL: {_SID: "0" * 64}}, {"other.md": {_SID: "x"}}])
def test_no_or_mismatched_baseline_preserves_as_before(tmp_path, baseline):
    root, backup = tmp_path / "agents", tmp_path / "backup"
    target = _deploy(root, _OLD, baseline=baseline)
    _merge(root, backup=backup)
    assert _OLD in target.read_text(encoding="utf-8")


def test_no_backup_dir_means_no_release(tmp_path):
    """Rule 12: the release must be recoverable, so without a .lost sidecar it does not happen."""
    root = tmp_path / "agents"
    target = _deploy(root, _OLD, baseline=_authored_old())
    _merge(root, backup=None)
    assert _OLD in target.read_text(encoding="utf-8")


def test_interop_marker_authorises_nothing(tmp_path):
    root, backup = tmp_path / "agents", tmp_path / "backup"
    target = _deploy(root, _OLD, baseline=None)
    log = {"origin": "interop", fb.BUILD_LOG_KEY: _authored_old()}
    (root / "references" / "build-log.json").write_text(json.dumps(log), encoding="utf-8")
    assert fb.load_for(root) == {}
    _merge(root, backup=backup)
    assert _OLD in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("backup_on", [True, False])
def test_dry_run_plan_matches_the_real_run(tmp_path, backup_on):
    root = tmp_path / "agents"
    target = _deploy(root, _OLD, baseline=_authored_old())
    result = _merge(root, backup=None, dry_run=True, twin_drop=backup_on)
    assert target.read_text(encoding="utf-8") == _doc(_OLD)
    (entry,) = result.dry_run_report.entries
    actions = {fa["fence_id"]: fa["action"] for fa in entry.fence_actions}
    if backup_on:
        assert entry.action == "MERGE" and actions[_SID] == "replaced"
    else:  # a preserve-only merge writes nothing, so the plan reads UNCHANGED, as before this fix
        assert entry.action == "UNCHANGED" and _SID not in actions
    note = next(n for n in result.dry_run_report.notices if f"fence '{_SID}'" in n)
    assert ("unedited since generation" in note) is backup_on


# --- the baseline itself: authored, never as written -----------------------------------------------------

def test_emit_records_the_template_render_digests(tmp_path):
    root = tmp_path / "agents"
    result = emit.emit_all([(_REL, _doc(_NEW))], output_dir=root, dry_run=False, overwrite=False, merge=False,
                           yes=True)
    assert result.template_fence_digests[_REL] == {_SID: fb.digest(_NEW)}
    assert fb.compute(result.written, root, result.template_fence_digests, None) == {_REL: {_SID: fb.digest(_NEW)}}


def test_a_preserved_hand_edit_is_never_recorded_as_authored(tmp_path):
    """The front-matter-baseline lesson: recording as-written would let the next run overwrite the edit."""
    root = tmp_path / "agents"
    edited = _OLD.replace("## Daily Remediation workflow\n", "## Daily Remediation workflow\n\nOur own step.\n")
    target = _deploy(root, edited, baseline=None)
    template = {_REL: {_SID: fb.digest(_NEW)}}
    assert fb.compute([str(target)], root, template, None) == {}
    # A previous authored record is carried forward, and still does not match the edit.
    carried = fb.compute([str(target)], root, template, _authored_old())
    assert carried == _authored_old()
    assert not fb.is_unedited(carried[_REL], _SID, edited)


def test_full_round_trip_records_then_releases(tmp_path):
    """Generate at the old template, record, then update to the new one: the unedited fence moves on."""
    root, backup = tmp_path / "agents", tmp_path / "backup"
    first = emit.emit_all([(_REL, _doc(_OLD))], output_dir=root, dry_run=False, overwrite=False, merge=False,
                          yes=True)
    (root / "references").mkdir(exist_ok=True)
    recorded = fb.compute(first.written, root, first.template_fence_digests, None)
    (root / "references" / "build-log.json").write_text(json.dumps({fb.BUILD_LOG_KEY: recorded}), encoding="utf-8")
    _merge(root, backup=backup)
    assert _NEW in (root / _REL).read_text(encoding="utf-8")


def test_a_real_render_records_the_fence_baseline(tmp_path):
    """build_team writes ``fence_baseline``: relative keys, one digest per on-disk fence, matching it."""
    import subprocess
    import sys

    from tests._fresh_intel import seed_offline_intel

    out = tmp_path / ".claude" / "agents"
    proc = subprocess.run([sys.executable, str(_ROOT / "build_team.py"), "--description",
                           str(_ROOT / "examples" / "data-pipeline" / "brief.json"), "--project", str(tmp_path),
                           "--framework", "claude", "--output", str(out), "--no-scan", "--yes",
                           *seed_offline_intel(out, "claude")],
                          cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    recorded = json.loads((out / "references" / "build-log.json").read_text())[fb.BUILD_LOG_KEY]
    assert recorded and not any(Path(rel).is_absolute() for rel in recorded)
    for rel, sids in recorded.items():
        assert sids == fb.block_digests((out / rel).read_text(encoding="utf-8")), rel


@pytest.mark.parametrize("policy", ["halt", "warn"])
def test_halt_and_warn_are_unchanged(tmp_path, policy):
    root, backup = tmp_path / "agents", tmp_path / "backup"
    target = _deploy(root, _OLD, baseline=_authored_old())
    result = emit.emit_all([(_REL, _doc(_NEW))], output_dir=root, dry_run=False, overwrite=False, merge=True,
                           yes=True, backup_path=backup, shrink_policy=policy)
    assert not any("unedited since generation" in n for n in result.notices)
    assert (_OLD in target.read_text(encoding="utf-8")) is (policy == "halt")
