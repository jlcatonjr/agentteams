"""runner_mcp.interpreter() skips a candidate another account could swap (remediation row 682, WP-F)."""
from __future__ import annotations

import os
import stat

import pytest

from agentteams import runner_mcp, wasm_mcp

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership checks")


def _exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    path.parent.chmod(0o755)
    return str(path)


def test_a_trusted_candidate_is_returned(monkeypatch, tmp_path):
    good = _exe(tmp_path / "a" / "python3")
    monkeypatch.setattr(runner_mcp, "SYSTEM_PYTHONS", (good,))
    assert runner_mcp.interpreter() == good


@pytest.mark.parametrize("where", ["file", "directory"])
@pytest.mark.parametrize("bit", [stat.S_IWGRP, stat.S_IWOTH])
def test_a_group_or_world_writable_candidate_is_skipped(monkeypatch, tmp_path, where, bit):
    bad = tmp_path / "bad" / "python3"
    _exe(bad)
    target = bad if where == "file" else bad.parent
    target.chmod(stat.S_IMODE(target.stat().st_mode) | bit)
    good = _exe(tmp_path / "good" / "python3")
    monkeypatch.setattr(runner_mcp, "SYSTEM_PYTHONS", (str(bad), good))
    assert runner_mcp.interpreter() == good
    monkeypatch.setattr(runner_mcp, "SYSTEM_PYTHONS", (str(bad),))
    with pytest.raises(ValueError, match="skipped: .*group- or world-writable"):
        runner_mcp.interpreter()


def test_the_check_follows_symlinks_to_the_real_file(monkeypatch, tmp_path):
    real = tmp_path / "cellar" / "python3.99"
    _exe(real)
    real.parent.chmod(0o777)
    link_dir = tmp_path / "bin"
    link_dir.mkdir()
    (link_dir / "python3").symlink_to(real)
    monkeypatch.setattr(runner_mcp, "SYSTEM_PYTHONS", (str(link_dir / "python3"),))
    with pytest.raises(ValueError, match="cellar"):
        runner_mcp.interpreter()
    real.parent.chmod(0o755)
    assert runner_mcp.interpreter() == str(link_dir / "python3")   # the literal path is what is written


def test_a_foreign_owner_is_skipped(monkeypatch, tmp_path):
    good = _exe(tmp_path / "a" / "python3")
    monkeypatch.setattr(runner_mcp, "SYSTEM_PYTHONS", (good,))
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(good).st_uid + 4242)
    if os.stat(good).st_uid == 0:
        pytest.skip("running as root: root-owned files are always trusted")
    with pytest.raises(ValueError, match="owned by uid"):
        runner_mcp.interpreter()


def test_no_candidate_at_all_keeps_the_old_message_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mcp, "SYSTEM_PYTHONS", (str(tmp_path / "missing"),))
    with pytest.raises(ValueError, match="no trusted system python3 at") as err:
        runner_mcp.interpreter()
    assert "skipped" not in str(err.value)


def test_session_launch_requires_a_hex_pins_value():
    pins = "a" * 64
    args = wasm_mcp.launch_args(wasm_mcp.SESSION_AGENT, __import__("agentteams.wasm_exec").wasm_exec.COMPUTE_ONLY,
                                ["python"], pins_sha=pins)
    command = runner_mcp.SYSTEM_PYTHONS[0]
    assert wasm_mcp.is_session_launch(command, args)
    i = args.index("--pins-sha256") + 1
    for bad in ("A" * 64, "a" * 63, "g" * 64, "../" + "a" * 61):
        tampered = list(args)
        tampered[i] = bad
        assert not wasm_mcp.is_session_launch(command, tampered), bad
