"""Nested-sandbox tests for agentteams/wasm_exec.py: wasmtime inside Seatbelt / bwrap.

These need a host where ``_sandbox_profiles.available()`` succeeds. Inside a sandboxed session (this repository's
Claude Code sessions) Seatbelt cannot nest (``sandbox_apply: Operation not permitted``), so every test here SKIPS with
that reason rather than passing on an un-nested run. Operator merge gate (P02 CSV step 11): run this file on an
unsandboxed macOS host and on Linux with bwrap. CI runs it in both jobs (``macos-seatbelt``, ``wasm-live``); the
network-deny and credential-deny cases have a Seatbelt and a bwrap branch under the same nodeid, so neither job
may skip them (``scripts/wasm_live_ci.py`` SANDBOX_ALLOW).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from tests._wasm_helpers import PROBE, FakeResolver, dev_wasmtime, needs_wasmtime, project

_KIND = SP.available()
pytestmark = [needs_wasmtime, pytest.mark.skipif(
    _KIND is None, reason=f"no usable OS sandbox here (kind={SP.kind()!r}; Seatbelt cannot nest inside a sandboxed "
                          "session) - operator gate: run on an unsandboxed host")]


def _run(root: Path, args: list[str], grant: W.Grant = W.COMPUTE_ONLY, **kw: object) -> W.WasmResult:
    res = W.run(W.prepare(PROBE, grant=grant, src_files=kw.pop("src", None)), args, project_root=root,
                os_confine="require", resolver=FakeResolver(dev_wasmtime()), profiles=SP, **kw)  # type: ignore
    assert res.os_confinement == "nested" and res.os_reason == f"nested:{_KIND}", res  # never a silent un-nested pass
    return res


def test_nested_runs_probe(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["deny"])
    assert (res.exit_code, res.stdout) == (0, b"PREOPEN0\nENV0\nSOCK0\n"), res.stderr[-2000:]


def test_nested_probe_report_ok(tmp_path: Path) -> None:
    rep = W.probe(project(tmp_path), resolver=FakeResolver(dev_wasmtime()), profiles=SP, os_confine="require")
    assert rep.ok and rep.os_kind == _KIND, rep.checks


def test_nested_ro_mount_write_denied_by_os(tmp_path: Path) -> None:
    root = project(tmp_path)
    res = _run(root, ["write"], W.Grant((W.Mount(str(root / "data"), "/project/data", "ro"),), W.DEFAULT_LIMITS))
    assert res.read_only_mode == "os" and res.stdout == b"WRITE0\n"
    assert sorted(os.listdir(root / "data")) == ["f.txt"]


def test_nested_src_write_denied_by_os(tmp_path: Path) -> None:
    res = _run(project(tmp_path), ["write"], src={"a.txt": b"a"})  # /src is perm-writable when nested: the OS denies
    assert res.read_only_mode == "os" and res.stdout == b"WRITE0\n"


def test_nested_scratch_writable_bin_not(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch = root / ".agentteams" / "wasm" / "scratch" / "nav"
    res = _run(root, ["write"], W.Grant((W.Mount(str(scratch), "/scratch", "rw"),), W.DEFAULT_LIMITS))
    assert res.stdout == b"WRITE1\n" and (scratch / "probe.out").exists()
    if _KIND != "seatbelt":
        return
    run_dir = root / ".agentteams" / "wasm" / "exec" / ("ab" * 8)
    (run_dir / "bin").mkdir(parents=True)
    prof = _profile(root, late=[str(scratch)], exec_paths=["/usr/bin/touch"])
    touch = ["/usr/bin/sandbox-exec", "-p", prof, "/usr/bin/touch"]
    assert subprocess.run([*touch, str(scratch / "ok")], capture_output=True).returncode == 0
    assert subprocess.run([*touch, str(run_dir / "bin" / "x")], capture_output=True).returncode != 0
    assert subprocess.run([*touch, str(root / "data" / "x")], capture_output=True).returncode != 0


def _profile(root: Path, *, late: list[str] = (), exec_paths: list[str], deny_network: bool = True) -> str:
    return SP.seatbelt_profile(root=root, exec_paths=exec_paths, write_roots=[], tmp_dir=root / ".agentteams",
                               protected_rel=W.NEST_PROTECTED_REL, key_dir=W.NEST_KEY_DIR, dev_writes=W.NEST_DEV_WRITES,
                               late_write_allows=late, deny_reads=W.NEST_DENY_READS, deny_network=deny_network)


def _bwrap(root: Path, *, deny_network: bool = True, deny_reads: tuple[str, ...] = W.NEST_DENY_READS) -> list[str]:
    """The bwrap prefix the nested run builds (``wasm_exec`` passes these same NEST_* values), ending in ``--``."""
    tmp = root / ".agentteams" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return [*SP.bwrap_argv(root=root, write_roots=[], tmp_dir=tmp, cwd=root, protected_rel=W.NEST_PROTECTED_REL,
                           key_dir=W.NEST_KEY_DIR, deny_reads=deny_reads, deny_network=deny_network), "--"]


_CONNECT = ("import socket, sys\n"
            "s = socket.socket(); s.settimeout(3)\n"
            "try:\n    s.connect(('127.0.0.1', int(sys.argv[1])))\n"
            "except OSError as exc:\n    sys.exit(f'refused: {exc}')\n")


def test_nested_network_denied(tmp_path: Path) -> None:
    if _KIND == "seatbelt" and not os.path.exists("/usr/bin/nc"):
        pytest.skip("needs /usr/bin/nc under Seatbelt")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = str(srv.getsockname()[1])
    try:
        for deny, want_ok in ((False, True), (True, False)):  # the control proves the listener is reachable
            root = project(tmp_path / str(deny))
            if _KIND == "seatbelt":
                prof = _profile(root, exec_paths=["/usr/bin/nc"], deny_network=deny)
                cmd = ["/usr/bin/sandbox-exec", "-p", prof, "/usr/bin/nc", "-z", "-w", "2", "127.0.0.1", port]
            else:  # bwrap: --unshare-net gives the run its own namespace, so the host's loopback is out of reach
                cmd = [*_bwrap(root, deny_network=deny), sys.executable, "-I", "-c", _CONNECT, port]
            res = subprocess.run(cmd, capture_output=True, timeout=20)
            assert (res.returncode == 0) is want_ok, res.stderr[-2000:]
    finally:
        srv.close()


def _fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A HOME holding a credential directory (``~/.ssh``) and a credential file (``~/.netrc``), both NEST_DENY_READS."""
    home = Path(os.path.realpath(tmp_path)) / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_test").write_text("SECRET-KEY\n")
    (home / ".netrc").write_text("machine x login y password SECRET-NETRC\n")
    monkeypatch.setenv("HOME", str(home))  # bwrap_argv expands ~ when called
    return home / ".ssh" / "id_test", home / ".netrc"


def test_nested_credential_store_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if _KIND == "bwrap":
        key, netrc = _fake_home(tmp_path, monkeypatch)
        root = project(tmp_path)
        cat = [sys.executable, "-I", "-c", "import sys; sys.stdout.write(open(sys.argv[1]).read())"]
        for path, secret in ((key, b"SECRET-KEY"), (netrc, b"SECRET-NETRC")):
            control = subprocess.run([*_bwrap(root, deny_reads=()), *cat, str(path)], capture_output=True, timeout=20)
            assert control.returncode == 0 and secret in control.stdout, control.stderr[-2000:]  # readable unhidden
            hidden = subprocess.run([*_bwrap(root), *cat, str(path)], capture_output=True, timeout=20)
            assert secret not in hidden.stdout + hidden.stderr  # tmpfs over the directory, /dev/null over the file
        assert subprocess.run([*_bwrap(root), *cat, str(key)], capture_output=True, timeout=20).returncode != 0  # dir gone
        return
    if not os.path.isdir(os.path.expanduser("~/.ssh")):
        pytest.skip("needs an existing ~/.ssh under Seatbelt")
    prof = SP.seatbelt_profile(root=tmp_path, exec_paths=["/bin/ls"], write_roots=[], tmp_dir=tmp_path,
                               protected_rel=W.NEST_PROTECTED_REL, key_dir=W.NEST_KEY_DIR,
                               dev_writes=W.NEST_DEV_WRITES, deny_reads=W.NEST_DENY_READS, deny_network=True)
    ls = ["/usr/bin/sandbox-exec", "-p", prof, "/bin/ls"]
    assert subprocess.run([*ls, str(tmp_path)], capture_output=True).returncode == 0  # control: ls itself runs
    assert subprocess.run([*ls, os.path.expanduser("~/.ssh")], capture_output=True).returncode != 0
