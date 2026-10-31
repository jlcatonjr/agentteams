"""Live tests for agentteams/wasm_exec.py: real wasmtime runs of the deny probe (skip without a dev wasmtime).

These run with ``os_confine="auto"``. Inside a sandboxed session Seatbelt cannot nest, so the runs are un-nested
(``os_confinement="none"``) and read-only mounts are snapshot-enforced; tests/test_wasm_exec_nested.py covers the
nested path and skips here. Nothing in this file claims OS confinement.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from tests._wasm_helpers import PROBE, FakeResolver, dev_wasmtime, needs_wasmtime, project

pytestmark = needs_wasmtime


def _run(root: Path, args: list[str], grant: W.Grant = W.COMPUTE_ONLY, **kw: object) -> W.WasmResult:
    src = kw.pop("src", None)
    return W.run(W.prepare(PROBE, grant=grant, src_files=src), args, project_root=root, os_confine="auto",
                 resolver=FakeResolver(dev_wasmtime()), profiles=SP, **kw)  # type: ignore[arg-type]


def test_probe_deny_compute_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_WASM_CANARY", "canary-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "canary-key")
    res = _run(project(tmp_path), ["deny"])
    assert (res.exit_code, res.stdout, res.trap) == (0, b"PREOPEN0\nENV0\nSOCK0\n", None)
    assert res.module_sha256 == W.DENY_PROBE_SHA256 and res.read_only_mode == "none"
    assert res.os_confinement in ("none", "nested")


def test_probe_control_with_scratch(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["deny"], W.Grant((W.Mount(W.RUN_SCRATCH, "/scratch", "rw"),), W.DEFAULT_LIMITS))
    assert res.stdout == b"PREOPEN1\nENV0\nSOCK0\n"  # the probe sees a widening, so PREOPEN0 above means something


def test_fuel_trap(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["spin"], W.Grant((), W.Limits(1_000_000, 1 << 20, 30)))
    assert res.trap == "fuel" and res.exit_code != 0


def test_memory_trap(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["grow"], W.Grant((), W.Limits(10**9, 1 << 20, 30)))
    assert res.trap == "memory"


def test_timeout_trap(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["spin"], W.Grant((), W.Limits(10**15, 1 << 20, 1)))
    assert res.trap == "timeout" and res.wall_s < 15


def test_output_cap_kills(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["flood"], stdout_cap=10_000)
    assert res.trap == "output-cap" and res.truncated and len(res.stdout) == 10_000


def test_stdin_is_passed_and_exit_code_kept(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert _run(root, ["echo"], stdin=b"line one\nline two\n").stdout == b"line one\nline two\n"
    res = _run(root, ["x"])
    assert res.exit_code == 7 and res.trap is None


def test_snapshot_read_only_leaves_host_unchanged(tmp_path: Path) -> None:
    root = project(tmp_path)
    res = _run(root, ["write"], W.Grant((W.Mount(str(root / "data"), "/project/data", "ro"),), W.DEFAULT_LIMITS))
    if res.os_confinement == "none":
        assert res.read_only_mode == "snapshot"
    assert sorted(os.listdir(root / "data")) == ["f.txt"] and (root / "data" / "f.txt").read_text() == "original\n"
    assert os.listdir(root / ".agentteams" / "wasm" / "exec") == []  # the run dir (and snapshot) is gone


def test_agent_scratch_is_writable(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch = root / ".agentteams" / "wasm" / "scratch" / "navigator"
    res = _run(root, ["write"], W.Grant((W.Mount(str(scratch), "/scratch", "rw"),), W.DEFAULT_LIMITS))
    assert res.stdout == b"WRITE1\n" and (scratch / "probe.out").read_text() == "WROTE\n"


def test_src_is_read_only(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["write"], src={"a.txt": b"a"})
    assert res.stdout == b"WRITE0\n"


def test_trailing_args_not_parsed_by_wasmtime(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["deny", "--dir", "/"])
    assert res.stdout == b"PREOPEN0\nENV0\nSOCK0\n"


def test_available_returns_version_string(tmp_path: Path) -> None:
    root = project(tmp_path)
    resolver = FakeResolver(dev_wasmtime())
    rep = W.probe(root, resolver=resolver, profiles=SP)
    assert rep.ok, rep.checks
    assert [c[0] for c in rep.checks] == ["resolve", "version", "probe-sha", "deny", "control", "fuel", "memory",
                                         "read-only"]
    got = W.available(root, resolver=resolver, profiles=SP)
    assert got == f"wasmtime {resolver.version} (+{rep.os_kind or 'alone'})"


def test_available_none_on_version_or_probe_mismatch(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert W.available(root, resolver=FakeResolver(dev_wasmtime(), "0.0.1"), profiles=SP) is None
    other = tmp_path / "other.wasm"
    other.write_bytes(PROBE.read_bytes() + b"\0")  # a trailing byte: still a module header, different sha
    assert W.available(root, resolver=FakeResolver(dev_wasmtime()), profiles=SP, probe_module=other) is None


def test_available_none_when_require_and_no_os_sandbox(tmp_path: Path) -> None:
    if SP.available() is not None:
        pytest.skip("an OS sandbox is usable here; covered by tests/test_wasm_exec_nested.py")
    rep = W.probe(project(tmp_path), resolver=FakeResolver(dev_wasmtime()), profiles=SP, os_confine="require")
    assert not rep.ok and any("require" in c[2] for c in rep.checks)
