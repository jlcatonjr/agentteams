"""Scan mirrors (``scan_mirror``): the daily scan reads a fresh default-branch worktree, reports the checkout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import daily, scan_mirror
from tests.test_vulnscan_core import Feed
from tests.test_vulnscan_daily import _cfg, feed, xdg  # noqa: F401  (pytest fixtures)

DAY = "2026-10-31"


def _repo(tmp_path: Path, pin: str, mirror_pin: str | None) -> tuple[Path, Path]:
    root = tmp_path / "code" / "app"
    (root / ".git").mkdir(parents=True)
    (root / "requirements.txt").write_text(f"PyJWT=={pin}\n", encoding="utf-8")
    mirror = tmp_path / "code" / "app.worktrees" / "vulnscan-mirror"
    if mirror_pin is not None:
        mirror.mkdir(parents=True)
        (mirror / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        (mirror / "requirements.txt").write_text(f"PyJWT=={mirror_pin}\n", encoding="utf-8")
    return root, mirror


def _stamp(root: Path, state: str) -> None:
    p = scan_mirror.stamp_path(DAY)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({str(root): state}), encoding="utf-8")


def test_mirror_is_used_only_when_named_real_and_recorded_fresh_today(xdg: Path, tmp_path: Path) -> None:
    root, mirror = _repo(tmp_path, "2.3.0", "2.15.0")
    assert scan_mirror.mirror_for({"scan_mirror": ""}, root, DAY) is None            # off by default
    on = {"scan_mirror": "vulnscan-mirror"}
    assert scan_mirror.mirror_for(on, root, DAY) is None                             # no record today
    _stamp(root, "failed fetch")
    assert scan_mirror.mirror_for(on, root, DAY) is None                             # refresh failed
    _stamp(root, "ok " + "a" * 40)
    assert scan_mirror.mirror_for(on, root, DAY) == mirror
    assert scan_mirror.mirror_for(on, root, "2026-10-30") is None                    # yesterday's record is not today's
    for bad in ("..", "a/b", "."):
        assert scan_mirror.mirror_for({"scan_mirror": bad}, root, DAY) is None


def test_a_symlinked_mirror_is_never_scanned(xdg: Path, tmp_path: Path) -> None:
    root, _ = _repo(tmp_path, "2.3.0", None)
    (tmp_path / "elsewhere").mkdir()
    (root.parent / "app.worktrees").mkdir()
    (root.parent / "app.worktrees" / "vulnscan-mirror").symlink_to(tmp_path / "elsewhere")
    _stamp(root, "ok " + "a" * 40)
    assert scan_mirror.mirror_for({"scan_mirror": "vulnscan-mirror"}, root, DAY) is None


def test_exclusions_hold_in_the_mirror_too(tmp_path: Path) -> None:
    root, mirror = tmp_path / "app", tmp_path / "app.worktrees" / "m"
    root.mkdir(); mirror.mkdir(parents=True)
    out = scan_mirror.also_under([root / "vendor", tmp_path / "other"], root, mirror)
    assert out == [root / "vendor", mirror.resolve() / "vendor", tmp_path / "other"]


def test_config_rejects_a_path_as_the_mirror_name(xdg: Path) -> None:
    daily.paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    daily.paths.config_file().write_text(json.dumps({"scan_mirror": "../x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="scan_mirror"):
        daily.load_config()
    assert daily.DEFAULT_CONFIG["scan_mirror"] == ""


def test_the_daily_scan_reads_the_fresh_mirror_and_reports_the_checkout(xdg: Path, feed: Feed,  # noqa: F811
                                                                         tmp_path: Path) -> None:
    root, _mirror = _repo(tmp_path, "2.15.0", "2.3.0")   # the checkout is fixed, the default branch is not
    _stamp(root, "ok " + "a" * 40)
    rep, _, run_dir = daily.run_daily(_cfg(repo_roots=[str(root)], scan_mirror="vulnscan-mirror"), today=DAY)
    found = [f for f in rep.findings if f.name == "PyJWT"]
    assert [(f.target, f.version) for f in found] == [(str(root), "2.3.0")]       # named after the checkout
    plans = list(run_dir.glob("fixplan-*.md"))
    assert len(plans) == 1 and str(root) in plans[0].read_text(encoding="utf-8")   # plan targets the checkout
    assert "vulnscan-mirror" not in plans[0].name
    assert "mirror `vulnscan-mirror`" in (run_dir / "summary.md").read_text(encoding="utf-8")


def test_without_a_fresh_mirror_the_checkout_is_scanned_as_before(xdg: Path, feed: Feed,  # noqa: F811
                                                                   tmp_path: Path) -> None:
    root, _mirror = _repo(tmp_path, "2.3.0", "2.15.0")
    _stamp(root, "failed fetch")
    rep, _, run_dir = daily.run_daily(_cfg(repo_roots=[str(root)], scan_mirror="vulnscan-mirror"), today=DAY)
    assert [(f.target, f.version) for f in rep.findings if f.name == "PyJWT"] == [(str(root), "2.3.0")]
    assert "| checkout |" in (run_dir / "summary.md").read_text(encoding="utf-8")
