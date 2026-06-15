"""Tests for backup retention/pruning + off-machine mirror (remediation Plan 3):
emit.prune_backups / _parse_backup_timestamp, emit._mirror_backup (via
backup_output_dir + AGENTTEAMS_BACKUP_MIRROR), and the --prune-backups /
--backup-mirror CLI (dispatch, CP-1 mutual-exclusivity, --dry-run)."""
from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import build_team
from agentteams import emit


def _make_backups(output_dir: Path, names: list[str]) -> None:
    """Create timestamped backup dirs (each with one file + a manifest) so
    list_backups enumerates them newest-first by name."""
    root = output_dir / ".agentteams-backups"
    for name in names:
        bdir = root / name
        bdir.mkdir(parents=True)
        (bdir / "a.agent.md").write_text("payload\n", encoding="utf-8")
        (bdir / "_manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")


def _names(output_dir: Path) -> set[str]:
    return {ts for ts, _, _ in emit.list_backups(output_dir)}


# ---------------------------------------------------------------------------
# _parse_backup_timestamp
# ---------------------------------------------------------------------------

def test_parse_backup_timestamp():
    assert emit._parse_backup_timestamp("20260615-150458") == datetime(2026, 6, 15, 15, 4, 58)
    # optional same-second collision suffix is ignored by the [:15] slice
    assert emit._parse_backup_timestamp("20260615-150458-3") == datetime(2026, 6, 15, 15, 4, 58)
    assert emit._parse_backup_timestamp("not-a-timestamp") is None
    assert emit._parse_backup_timestamp("") is None


# ---------------------------------------------------------------------------
# prune_backups core retention
# ---------------------------------------------------------------------------

def test_prune_keeps_newest_n(tmp_path):
    names = [f"20260615-1500{i:02d}" for i in range(13)]  # 00..12, newest=12
    _make_backups(tmp_path, names)
    res = emit.prune_backups(tmp_path, keep_last=5)
    assert not res.dry_run
    assert len(res.kept) == 5 and len(res.deleted) == 8
    # the 5 newest survive
    assert _names(tmp_path) == {f"20260615-1500{i:02d}" for i in range(8, 13)}


def test_prune_never_deletes_newest_even_keep_zero(tmp_path):
    names = [f"20260615-1500{i:02d}" for i in range(4)]
    _make_backups(tmp_path, names)
    res = emit.prune_backups(tmp_path, keep_last=0)
    # the single newest is always retained, even with keep_last=0
    assert res.kept == ["20260615-150003"]
    assert _names(tmp_path) == {"20260615-150003"}


def test_prune_dry_run_deletes_nothing(tmp_path):
    names = [f"20260615-1500{i:02d}" for i in range(6)]
    _make_backups(tmp_path, names)
    res = emit.prune_backups(tmp_path, keep_last=2, dry_run=True)
    assert res.dry_run and len(res.deleted) == 4
    # nothing actually removed from disk
    assert _names(tmp_path) == set(names)


def test_prune_empty_is_noop(tmp_path):
    res = emit.prune_backups(tmp_path, keep_last=3)
    assert res.deleted == [] and res.kept == []


def test_prune_keep_within_days_retains_young_beyond_keep_last(tmp_path):
    now = datetime.now()
    young = (now - timedelta(days=2)).strftime("%Y%m%d-%H%M%S")
    old = (now - timedelta(days=400)).strftime("%Y%m%d-%H%M%S")
    older = (now - timedelta(days=800)).strftime("%Y%m%d-%H%M%S")
    # newest-first after sort: young > old > older (by date). keep_last=1 keeps young.
    _make_backups(tmp_path, [young, old, older])
    res = emit.prune_backups(tmp_path, keep_last=1, keep_within_days=7)
    # young kept by both rules; old/older are beyond keep_last AND older than 7d → deleted
    assert young in res.kept
    assert set(res.deleted) == {old, older}
    assert _names(tmp_path) == {young}


def test_prune_keep_within_days_keeps_recent_unparseable_name(tmp_path):
    """An unparseable-named backup is not blindly deleted under age-based
    retention: with no parseable timestamp, prune falls back to mtime, and a
    freshly-created dir is recent → kept (fail-safe toward retention)."""
    now = datetime.now()
    newest = now.strftime("%Y%m%d-%H%M%S")
    # 'zz_' sorts AFTER digits, so reverse-sort puts it at idx 0 (newest slot);
    # use '0bad_name' which sorts BEFORE digits → oldest slot, beyond keep_last=1.
    _make_backups(tmp_path, [newest, "0bad_name"])
    assert emit._parse_backup_timestamp("0bad_name") is None  # precondition
    res = emit.prune_backups(tmp_path, keep_last=1, keep_within_days=7)
    # newest kept by keep_last; unparseable kept via recent-mtime fallback
    assert _names(tmp_path) == {newest, "0bad_name"}
    assert not res.deleted


# ---------------------------------------------------------------------------
# off-machine mirror (emit._mirror_backup via backup_output_dir)
# ---------------------------------------------------------------------------

def test_mirror_copies_backup_and_manifest(tmp_path, monkeypatch):
    src = tmp_path / "out"; src.mkdir()
    (src / "a.agent.md").write_text("alpha\n", encoding="utf-8")
    mirror = tmp_path / "mirror"
    monkeypatch.setenv("AGENTTEAMS_BACKUP_MIRROR", str(mirror))
    res = emit.backup_output_dir(src, reason="test")
    slug_dirs = list(mirror.iterdir())
    assert len(slug_dirs) == 1  # one output-dir slug
    mirrored = slug_dirs[0] / res.backup_path.name
    assert (mirrored / "a.agent.md").read_text() == "alpha\n"
    assert (mirrored / "_manifest.json").exists()


def test_mirror_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    src = tmp_path / "out"; src.mkdir()
    (src / "b.agent.md").write_text("beta\n", encoding="utf-8")
    # point the mirror at a path that cannot be created (a regular file)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file\n", encoding="utf-8")
    monkeypatch.setenv("AGENTTEAMS_BACKUP_MIRROR", str(blocker / "sub"))
    res = emit.backup_output_dir(src, reason="test")  # must NOT raise
    assert res.backup_path.exists()  # primary backup intact despite mirror failure
    assert "mirror failed (non-fatal)" in capsys.readouterr().err.lower()


def test_no_mirror_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTTEAMS_BACKUP_MIRROR", raising=False)
    src = tmp_path / "out"; src.mkdir()
    (src / "c.agent.md").write_text("gamma\n", encoding="utf-8")
    res = emit.backup_output_dir(src, reason="test")
    assert res.backup_path.exists()  # no error, no mirror


# ---------------------------------------------------------------------------
# CLI: --prune-backups dispatch + exit codes
# ---------------------------------------------------------------------------

def test_cli_prune_backups_default_keep(tmp_path):
    names = [f"20260615-1500{i:02d}" for i in range(13)]
    _make_backups(tmp_path, names)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = build_team.main(["--prune-backups", "--output", str(tmp_path)])
    assert rc == 0
    # default keep is DEFAULT_BACKUP_KEEP_LAST (10)
    assert len(emit.list_backups(tmp_path)) == emit.DEFAULT_BACKUP_KEEP_LAST


def test_cli_prune_backups_explicit_keep(tmp_path):
    names = [f"20260615-1500{i:02d}" for i in range(8)]
    _make_backups(tmp_path, names)
    with redirect_stdout(io.StringIO()):
        rc = build_team.main(["--prune-backups", "3", "--output", str(tmp_path)])
    assert rc == 0 and len(emit.list_backups(tmp_path)) == 3


def test_cli_prune_backups_dry_run(tmp_path):
    names = [f"20260615-1500{i:02d}" for i in range(6)]
    _make_backups(tmp_path, names)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = build_team.main(["--prune-backups", "2", "--dry-run", "--output", str(tmp_path)])
    assert rc == 0 and len(emit.list_backups(tmp_path)) == 6  # nothing deleted
    assert "Would delete" in out.getvalue()


def test_cli_prune_backups_no_backups(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = build_team.main(["--prune-backups", "--output", str(tmp_path)])
    assert rc == 0 and "No backups found" in out.getvalue()


# ---------------------------------------------------------------------------
# CLI: CP-1 mutual-exclusivity + modifier validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("combo", [
    ["--verify-integrity", "--verify-backup"],
    ["--prune-backups", "--verify-integrity"],
    ["--prune-backups", "--verify-backup"],
])
def test_cli_standalone_ops_mutually_exclusive(tmp_path, combo):
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stderr(err):
        build_team.main(combo + ["--output", str(tmp_path)])
    assert exc.value.code == 2 and "mutually exclusive" in err.getvalue()


def test_cli_keep_within_days_requires_prune(tmp_path):
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stderr(err):
        build_team.main(["--keep-within-days", "7", "--output", str(tmp_path)])
    assert exc.value.code == 2 and "only applies with --prune-backups" in err.getvalue()


# ---------------------------------------------------------------------------
# CLI: --backup-mirror sets the env for the run
# ---------------------------------------------------------------------------

def test_cli_backup_mirror_sets_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTTEAMS_BACKUP_MIRROR", raising=False)
    with redirect_stdout(io.StringIO()):
        # combine with a standalone op that returns cleanly; env is set before dispatch
        rc = build_team.main(["--prune-backups", "--backup-mirror", "/some/mirror",
                              "--output", str(tmp_path)])
    assert rc == 0
    assert os.environ.get("AGENTTEAMS_BACKUP_MIRROR") == "/some/mirror"
