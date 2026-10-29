"""The server kit is staged out of the installed package and verified before each run (plan vba-p7)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import executor
from agentteams.vulnscan import subproc_confine_ssh as ssh
from agentteams.vulnscan.subproc_confine import Unavailable


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ssh, "_STAGED", {})


def _kit(tmp_path: Path) -> Path:
    src = tmp_path / "pkg" / "server_kit"
    src.mkdir(parents=True)
    for name in ssh.KIT_REQUIRED:
        (src / name).write_text(f"# {name}\n", encoding="utf-8")
    (src / "ioc-x.txt").write_text("bad==1\n", encoding="utf-8")
    return src


def test_the_packaged_kit_has_every_required_file() -> None:
    names = set(os.listdir(executor.kit_dir()))
    assert set(ssh.KIT_REQUIRED) <= names


def test_staging_copies_every_file_into_a_private_dir_under_the_state_dir(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    copy, digests = ssh.stage_kit(src)
    assert copy.is_relative_to(tmp_path / "state")
    assert sorted(os.listdir(copy)) == sorted(os.listdir(src)) == sorted(digests)
    assert (copy.parent.stat().st_mode & 0o777) == 0o700
    assert ssh.stage_kit(src) == (copy, digests)   # once per process


def test_a_reinstall_after_staging_does_not_remove_the_mount_source(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    copy, digests = ssh.stage_kit(src)
    for f in src.iterdir():
        f.unlink()
    src.rmdir()
    ssh._check_staged(copy, digests)   # still complete and unchanged
    assert ssh.stage_kit(src)[0] == copy


def test_a_missing_kit_is_refused_with_a_reason(tmp_path: Path) -> None:
    with pytest.raises(Unavailable, match="packaged server kit is missing"):
        ssh.stage_kit(tmp_path / "gone")


def test_an_incomplete_kit_is_refused(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    (src / "run-host.sh").unlink()
    with pytest.raises(Unavailable, match="lacks run-host.sh"):
        ssh.stage_kit(src)


def test_a_symlink_in_the_kit_is_refused_not_followed(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("key", encoding="utf-8")
    (src / "L5-containers.sh").unlink()
    (src / "L5-containers.sh").symlink_to(secret)
    with pytest.raises(Unavailable, match="L5-containers.sh unreadable"):
        ssh.stage_kit(src)


def test_a_subdirectory_in_the_kit_is_refused(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    (src / "extra").mkdir()
    with pytest.raises(Unavailable, match="not a regular file"):
        ssh.stage_kit(src)


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_a_staged_copy_changed_after_staging_is_refused(tmp_path: Path, change: str) -> None:
    copy, digests = ssh.stage_kit(_kit(tmp_path))
    if change == "edit":
        (copy / "run-host.sh").write_text("sudo apply\n", encoding="utf-8")
    elif change == "add":
        (copy / "planted.sh").write_text("x\n", encoding="utf-8")
    else:
        (copy / "ioc-x.txt").unlink()
    with pytest.raises(Unavailable, match="changed after staging"):
        ssh._check_staged(copy, digests)


def test_run_kit_mounts_and_runs_the_staged_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _kit(tmp_path)
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "hosts.conf").write_text("web1|ops@web1.example\n", encoding="utf-8")
    seen: dict[str, Any] = {}

    class _Scope:
        ssh_identity = ""

    def fake_run(op: str, argv: list[str], **kw: Any) -> str:
        seen.update(op=op, argv=argv, **kw)
        return "ran"

    monkeypatch.setattr(ssh, "active", lambda: _Scope())
    monkeypatch.setattr(ssh, "build_ssh_files", lambda *a, **k: ([], "web1.example:22"))
    monkeypatch.setattr(ssh, "run_confined", fake_run)
    assert ssh.run_kit(src / "remote.sh", "web1", ["L1-audit.sh"], conf_dir=conf, logs=tmp_path / "logs") == "ran"
    kit = Path(seen["argv"][1]).parent
    assert seen["argv"][0] == "bash" and Path(seen["argv"][1]).name == "remote.sh"
    assert kit.is_relative_to(tmp_path / "state") and not kit.is_relative_to(src)
    assert kit in seen["readonly"] and src not in seen["readonly"]


def test_a_changed_package_is_staged_again_and_the_old_copy_removed(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    first, d1 = ssh.stage_kit(src)
    (src / "ioc-x.txt").write_text("bad==2\n", encoding="utf-8")   # an upgrade under a long-running executor
    second, d2 = ssh.stage_kit(src)
    assert second != first and d2 != d1 and not first.exists()
    assert (second / "ioc-x.txt").read_text(encoding="utf-8") == "bad==2\n"


def test_a_fifo_in_the_kit_is_refused_without_hanging(tmp_path: Path) -> None:
    src = _kit(tmp_path)
    os.mkfifo(src / "planted")
    with pytest.raises(Unavailable, match="planted refused"):
        ssh.stage_kit(src)


def test_copies_left_by_a_killed_process_are_removed(tmp_path: Path) -> None:
    base = tmp_path / "state" / "tools"
    orphan = base / "kit-999999-abc" / "kit"
    orphan.mkdir(parents=True)
    mine_alive = base / f"kit-{os.getppid()}-def"   # a live process's copy is kept
    mine_alive.mkdir()
    ssh.stage_kit(_kit(tmp_path))
    assert not orphan.parent.exists() and mine_alive.exists()
