"""Fleet flags a new or changed fence-twin acknowledgement for operator review (agentteams/_fleet_twin_acks.py).

A ``references/fence-twin-acks.csv`` row turns a kept twin's REVIEW into a ``twin kept (acknowledged)``
notice. Nothing ties the row to the operator, so the first applied fleet run that renders with it must
still report REVIEW; only a notice an earlier run already showed the operator stays quiet.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from agentteams import _fleet_twin_acks as acks
from agentteams import fleet
from tests.test_fleet import _Args, _init_repo

_BALANCED = "<!-- AGENTTEAMS:BEGIN rules v=1 -->\nbody\n<!-- AGENTTEAMS:END rules -->\n"
_REASON = "CH-08..10 are project rules the template never carried; kept until moved to notes."


def _ack(reason: str = _REASON, digest: str = "3f1c0e9a2b7d4c51") -> str:
    return (f"    Notice: a.agent.md: twin kept (acknowledged): '## Rules' beside fence 'rules' — matches no past "
            f"template rendering of this section; kept as acknowledged in references/fence-twin-acks.csv "
            f"(digest {digest}): {reason}\n")


def _setup(tmp_path: Path) -> Path:
    agents = tmp_path / "ws" / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "_build-description.json").write_text("{}", encoding="utf-8")
    (agents / "a.agent.md").write_text(_BALANCED, encoding="utf-8")
    _init_repo(tmp_path / "ws")
    return agents


def _run(tmp_path: Path, monkeypatch, output: str, run_id: str, *, body: str = _BALANCED, rc: int = 0) -> dict:
    """One applied fleet run with a fixed run id; returns its single target row."""
    agents = tmp_path / "ws" / ".github" / "agents"

    def fake_update(argv):
        (agents / "a.agent.md").write_text(body.replace("body", f"body {run_id}"), encoding="utf-8")
        return rc, output

    class _Fixed(datetime):   # a real datetime class whose now() is this run's id
        @classmethod
        def now(cls, tz=None):
            return cls.strptime(run_id, "%Y%m%d-%H%M%S").replace(tzinfo=tz)

    monkeypatch.setattr(fleet, "_run_main", fake_update)
    monkeypatch.setattr(fleet, "datetime", _Fixed)
    fleet.run_fleet(_Args(fleet=str(tmp_path), yes=True), parser=None)
    data = json.loads((tmp_path / ".agentteams-fleet" / run_id / "report.json").read_text())
    (target,) = [t for w in data["workspaces"] for t in w["targets"]]
    return target


def test_twin_signals_splits_the_notices():
    sig = acks.twin_signals("x\n" + _ack())
    assert len(sig["twins_acknowledged"]) == 1 and not sig["twins_kept"] and not sig["twins_retired"]


def test_a_new_acknowledgement_is_review_until_a_run_has_shown_it(tmp_path, monkeypatch):
    _setup(tmp_path)
    first = _run(tmp_path, monkeypatch, _ack(), "20261011-000001")
    assert first["status"] == "REVIEW" and acks.REVIEW_REASON in first["detail"]
    assert first["twins_ack_unreviewed"] == first["twins_acknowledged"] and len(first["twins_acknowledged"]) == 1
    assert (tmp_path / ".agentteams-fleet" / "20261011-000001" / acks.SEEN_FILE).is_file()
    second = _run(tmp_path, monkeypatch, _ack(), "20261011-000002")
    assert second["status"] == "OK" and not second["twins_ack_unreviewed"]
    assert len(second["twins_acknowledged"]) == 1, "still listed in the report"


def test_a_changed_reason_or_digest_is_flagged_again(tmp_path, monkeypatch):
    _setup(tmp_path)
    _run(tmp_path, monkeypatch, _ack(), "20261011-000001")
    assert _run(tmp_path, monkeypatch, _ack(), "20261011-000002")["status"] == "OK"
    changed = _run(tmp_path, monkeypatch, _ack(reason=_REASON + " Reworded."), "20261011-000003")
    assert changed["status"] == "REVIEW" and acks.REVIEW_REASON in changed["detail"]
    redigest = _run(tmp_path, monkeypatch, _ack(digest="0" * 16), "20261011-000004")
    assert redigest["status"] == "REVIEW"


def test_a_removed_then_restored_acknowledgement_is_flagged_again(tmp_path, monkeypatch):
    _setup(tmp_path)
    _run(tmp_path, monkeypatch, _ack(), "20261011-000001")
    assert _run(tmp_path, monkeypatch, "Merged\n", "20261011-000002")["status"] == "OK"
    assert _run(tmp_path, monkeypatch, _ack(), "20261011-000003")["status"] == "REVIEW"


def test_a_malformed_record_flags_everything(tmp_path, monkeypatch):
    _setup(tmp_path)
    _run(tmp_path, monkeypatch, _ack(), "20261011-000001")
    (tmp_path / ".agentteams-fleet" / "20261011-000001" / acks.SEEN_FILE).write_text("{not json", encoding="utf-8")
    assert _run(tmp_path, monkeypatch, _ack(), "20261011-000002")["status"] == "REVIEW"


def test_a_flag_hidden_behind_a_fence_imbalance_is_not_recorded(tmp_path, monkeypatch):
    _setup(tmp_path)
    broken = _BALANCED + "<!-- AGENTTEAMS:END stray -->\n"
    first = _run(tmp_path, monkeypatch, _ack(), "20261011-000001", body=broken)
    assert first["status"] == "REVIEW" and acks.REVIEW_REASON not in first["detail"]
    assert _run(tmp_path, monkeypatch, _ack(), "20261011-000002")["status"] == "REVIEW", "never shown, so flagged"


def test_a_failed_target_is_not_recorded(tmp_path, monkeypatch):
    _setup(tmp_path)
    failed = _run(tmp_path, monkeypatch, "Error: boom\n" + _ack(), "20261011-000001", rc=1)
    assert failed["status"] == "FAIL"
    assert _run(tmp_path, monkeypatch, _ack(), "20261011-000002")["status"] == "REVIEW"


def test_dry_run_neither_reads_nor_writes_the_record(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(fleet, "_run_main", lambda argv: (0, _ack()))
    fleet.run_fleet(_Args(fleet=str(tmp_path), yes=False), parser=None)
    assert not list((tmp_path / ".agentteams-fleet").rglob(acks.SEEN_FILE))


def test_load_seen_ignores_later_and_non_run_directories(tmp_path):
    root = tmp_path / "r"
    for name, keys in (("20261011-000001", ["a"]), ("20261011-000009", ["later"]), ("manual-backup-x", ["bak"])):
        (root / name).mkdir(parents=True)
        (root / name / acks.SEEN_FILE).write_text(
            json.dumps({"version": 1, "workspaces": {"ws": {"github": keys}}}), encoding="utf-8")
    assert acks.load_seen(root / "20261011-000005") == {"ws": {"github": ["a"]}}
    assert acks.load_seen(tmp_path / "missing" / "20261011-000005") == {}


def test_load_seen_rejects_a_wrong_shape(tmp_path, capsys):
    run = tmp_path / "20261011-000001"
    run.mkdir()
    (run / acks.SEEN_FILE).write_text(json.dumps({"version": 1, "workspaces": {"ws": {"github": [1]}}}),
                                      encoding="utf-8")
    assert acks.load_seen(tmp_path / "20261011-000002") == {}
    assert "flagged for review" in capsys.readouterr().err


def test_record_seen_warns_when_it_cannot_write(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    acks.record_seen(blocker / "20261011-000001", [], {})
    assert "not written" in capsys.readouterr().err


def _record(root: Path, run: str, keys: list[str]) -> Path:
    (root / run).mkdir(parents=True)
    path = root / run / acks.SEEN_FILE
    path.write_text(json.dumps({"version": 1, "workspaces": {"ws": {"github": keys}}}), encoding="utf-8")
    return path


def test_a_record_inside_a_git_work_tree_is_not_used(tmp_path, capsys):
    root = tmp_path / "parent-repo" / ".agentteams-fleet"
    _record(root, "20261011-000001", ["a"])
    subprocess.run(["git", "init", "-q", str(tmp_path / "parent-repo")], check=True)
    assert acks.load_seen(root / "20261011-000002") == {}
    assert "git work tree" in capsys.readouterr().err


def test_a_symlinked_record_is_not_used(tmp_path, capsys):
    real = _record(tmp_path / "elsewhere", "20261011-000001", ["a"])
    (tmp_path / "r" / "20261011-000001").mkdir(parents=True)
    (tmp_path / "r" / "20261011-000001" / acks.SEEN_FILE).symlink_to(real)
    assert acks.load_seen(tmp_path / "r" / "20261011-000002") == {}
    assert "symlink" in capsys.readouterr().err


def test_a_fence_imbalance_detail_naming_the_reason_does_not_record(tmp_path):
    """A changed file's name is writer-controlled; the imbalance detail must not count as showing the flag."""
    tr = fleet.TargetResult(workspace="ws", target="github", status="REVIEW",
                            detail=f"FENCE IMBALANCE (malformed render — do not push): {acks.REVIEW_REASON}.md (1/0)",
                            fence_imbalances=[f"{acks.REVIEW_REASON}.md (1/0)"],
                            twins_acknowledged=["x"], twins_ack_unreviewed=["x"])
    seen: dict = {}
    acks.record_seen(tmp_path / "20261011-000001", [tr], seen)
    assert seen == {}


def test_a_record_that_cannot_be_checked_flags_everything(tmp_path, monkeypatch, capsys):
    _record(tmp_path / "r", "20261011-000001", ["a"])

    def no_git(*args, **kwargs):
        raise OSError("git cannot be started")

    monkeypatch.setattr(acks, "run_git", no_git)
    assert acks.load_seen(tmp_path / "r" / "20261011-000002") == {}
    assert "cannot check it" in capsys.readouterr().err
