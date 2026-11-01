"""Pure tests for agentteams/wasm_exec.py (no wasmtime needed): grant spec, argv shape, mounts, refusals, nesting."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import confinement as C
from agentteams import wasm_exec as W
from agentteams import wasm_pins
from agentteams.frameworks._sandbox_emit import SIGNING_KEY_DIR
from tests._wasm_helpers import PROBE, FakeResolver, project

REPO = Path(__file__).resolve().parents[1]
WAT = REPO / "tests" / "fixtures" / "wasm" / "wasm_deny_probe.wat"
S = W.GRANT_SPEC["S"]


def _fake_wasmtime(tmp_path: Path) -> str:
    path = tmp_path / "bin" / "wasmtime"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return os.path.realpath(path)


def _profiles(kind: str | None) -> types.ModuleType:
    """A stand-in profiles module: the real builders, a fixed ``available()``."""
    mod = types.ModuleType("fake_profiles")
    for name in ("seatbelt_profile", "bwrap_argv", "snapshot_tree", "remove_tree", "refuse_credentials",
                 "ConfinementError"):
        setattr(mod, name, getattr(SP, name))
    mod.available = lambda: kind  # type: ignore[attr-defined]
    return mod


# --- the spec ------------------------------------------------------------------------------------------------------

def test_compute_only_flags_equal_spec() -> None:
    argv = W.build_argv("/w/wasmtime", "/m/probe.wasm", [], W.COMPUTE_ONLY)
    assert argv[2:8] == ["-C", "cache=n", "-W", "fuel=10000000000,timeout=120s,max-memory-size=67108864,"
                         "trap-on-grow-failure=y,max-instances=1,max-memories=1,max-tables=1", "-S", S]
    assert argv == ["/w/wasmtime", "run", *argv[2:8], "--argv0", "probe", "--", "/m/probe.wasm"]


def test_grant_spec_sha_and_print_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert W.main(["--print-spec"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("\n") and hashlib.sha256(out[:-1].encode()).hexdigest() == W.GRANT_SPEC_SHA256
    assert json.loads(out) == json.loads(json.dumps(W.GRANT_SPEC))
    assert W.main([]) == 2


def test_limit_bounds_derive_from_spec() -> None:
    b = W.GRANT_SPEC["limit_bounds"]
    assert W.LIMIT_BOUNDS == {"fuel": tuple(b["fuel"]), "max_memory_bytes": tuple(b["max_memory_bytes"]),
                              "timeout_s": (1, b["timeout_ms"][1] // 1000)}
    assert W.DEFAULT_LIMITS == W.Limits(10_000_000_000, 64 << 20, 120)


@pytest.mark.parametrize("field,lo,hi", [("fuel", 1, 10**15), ("max_memory_bytes", 65536, 2**32),
                                         ("timeout_s", 1, 3600)])
def test_limits_bounds(field: str, lo: int, hi: int) -> None:
    base = {"fuel": 1, "max_memory_bytes": 65536, "timeout_s": 1}
    for ok in (lo, hi):
        W.Limits(**{**base, field: ok})
    for bad in (lo - 1, hi + 1, True, "5"):
        with pytest.raises(W.WasmExecError):
            W.Limits(**{**base, field: bad})


# --- argv shape ----------------------------------------------------------------------------------------------------

def _argv(*, flags: list[str] | None = None, dirs: list[str] = (), tail: list[str] | None = None) -> list[str]:
    flags = flags if flags is not None else W.grant_flags(W.DEFAULT_LIMITS)
    d = [x for pair in (("--dir", v) for v in dirs) for x in pair]
    return ["/w/wasmtime", "run", *flags, *d, *(tail or ["--argv0", "m", "--", "/abs/m.wasm"])]


@pytest.mark.parametrize("extra", [["--dir", "/"], ["--env", "A=B"], ["--preload", "x=/y"], ["--allow-precompiled"],
                                   ["--config", "/c"], ["--invoke", "f"], ["-S", "inherit-network=y"],
                                   ["-W", "fuel=1"], ["--profile", "perfmap"]])
def test_shape_refuses_extras(extra: list[str]) -> None:
    for argv in (_argv(flags=[*W.grant_flags(W.DEFAULT_LIMITS), *extra]),
                 _argv(tail=["--argv0", "m", *extra, "--", "/abs/m.wasm"])):
        with pytest.raises(W.WasmExecError):
            W.assert_grant_shape(argv, W.COMPUTE_ONLY)
        with pytest.raises(W.WasmExecError):
            W.assert_grant_shape(argv, None)


@pytest.mark.parametrize("key", ["inherit-env", "inherit-network", "tcp", "udp", "allow-ip-name-lookup", "http"])
def test_shape_refuses_flipped_s_key(key: str) -> None:
    flags = W.grant_flags(W.DEFAULT_LIMITS)
    flags[5] = flags[5].replace(f"{key}=n", f"{key}=y")
    with pytest.raises(W.WasmExecError, match="element 7"):
        W.assert_grant_shape(_argv(flags=flags), None)


def test_shape_refuses_reordered_and_out_of_bounds() -> None:
    f = W.grant_flags(W.DEFAULT_LIMITS)
    for flags in ([f[4], f[5], f[2], f[3], f[0], f[1]], [*f[:3], f[3].replace("fuel=10000000000", "fuel=0")],
                  [*f[:3], f[3].replace("max-memory-size=67108864", "max-memory-size=4294967297"), *f[4:]],
                  [*f[:3], f[3].replace("timeout=120s", "timeout=3601s"), *f[4:]],
                  [*f[:3], f[3].replace("max-instances=1", "max-instances=2"), *f[4:]],
                  [*f[:3], f[3].replace("fuel=10000000000", "fuel=١٢"), *f[4:]]):
        with pytest.raises(W.WasmExecError):
            W.assert_grant_shape(_argv(flags=flags), None)


def test_parity_mode_accepts_ms_and_component_and_refuses_any_dir() -> None:
    f = W.grant_flags(W.DEFAULT_LIMITS)
    W.assert_grant_shape(_argv(flags=[*f[:3], f[3].replace("timeout=120s", "timeout=500ms"), *f[4:]]), None)
    W.assert_grant_shape(_argv(flags=W.grant_flags(W.DEFAULT_LIMITS, profile="component")), None,
                         profile="component")
    W.assert_grant_shape(_argv(flags=[*f[:3], f[3].replace("67108864", "4294967296"), *f[4:]]), None)
    with pytest.raises(W.WasmExecError, match="parity"):
        W.assert_grant_shape(_argv(dirs=["/x::/project/x"]), None)
    with pytest.raises(W.WasmExecError):  # ms spelling is parity-only
        W.assert_grant_shape(_argv(flags=[*f[:3], f[3].replace("timeout=120s", "timeout=120000ms"), *f[4:]]),
                             W.COMPUTE_ONLY)
    with pytest.raises(W.WasmExecError, match="component"):
        W.assert_grant_shape(_argv(), W.COMPUTE_ONLY, profile="component")


def test_grant_mode_requires_exact_dirs() -> None:
    m = W.Mount("/p/data", "/project/data", "ro")
    g = W.Grant((m,), W.DEFAULT_LIMITS)
    W.assert_grant_shape(_argv(dirs=["/p/data::/project/data"]), g)
    for dirs in ([], ["/p/data::/project/data", "/p/data::/project/data"], ["/p/other::/project/data"],
                 ["/p/data::/project/data::readonly"]):
        with pytest.raises(W.WasmExecError):
            W.assert_grant_shape(_argv(dirs=dirs), g)
    with pytest.raises(W.WasmExecError):
        W.assert_grant_shape(_argv(dirs=["/p/data::/project/data"]), W.COMPUTE_ONLY)


def test_trailing_args_are_guest_args() -> None:
    argv = W.build_argv("/w/wasmtime", "/m/x.wasm", ["deny", "--dir", "/"], W.COMPUTE_ONLY)
    assert argv[-4:] == ["/m/x.wasm", "deny", "--dir", "/"] and argv[-5] == "--"
    for bad in (["a\0b"], ["x"] * 257, ["x" * (64 << 10 + 1)]):
        with pytest.raises(W.WasmExecError):
            W.build_argv("/w/wasmtime", "/m/x.wasm", bad, W.COMPUTE_ONLY)


# --- mounts and guest names ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["/a::b", "/a/b::/project/x::readonly", "rel/dir", "-x", "/a\nb", '/a"b', "/a\0b",
                                  "/a\x7fb", "/a\\b"])
def test_bad_mount_hosts_refused(host: str) -> None:
    with pytest.raises(W.WasmExecError):
        W.Mount(host, "/project/x", "ro")


@pytest.mark.parametrize("host", ["/a:b", "/p/data:/project/x", "/a/b:", "/:x", "/a/b:ro"])
def test_any_colon_in_a_mount_host_is_refused(host: str) -> None:
    """S2: not only '::' — a single ':' in the host path is refused (wasmtime splits --dir on ':')."""
    with pytest.raises(W.WasmExecError, match="without ':'"):
        W.Mount(host, "/project/x", "ro")


@pytest.mark.parametrize("guest,mode,ok", [
    ("/project/a/b", "ro", True), ("/scratch", "rw", True), ("/lib", "ro", True), ("/lib/python3.14", "ro", True),
    ("/src", "ro", True), ("/etc", "ro", False), ("/project", "ro", False), ("/project/../x", "ro", False),
    ("/project/./x", "ro", False), ("/project/x", "rw", False), ("/scratch", "ro", False), ("/scratch/x", "rw", False),
    ("/project/a b", "ro", False), ("project/x", "ro", False), ("/project/x", "wo", False)])
def test_guest_name_grammar(guest: str, mode: str, ok: bool) -> None:
    if ok:
        W.Mount("/h", guest, mode)  # type: ignore[arg-type]
    else:
        with pytest.raises(W.WasmExecError):
            W.Mount("/h", guest, mode)  # type: ignore[arg-type]


def test_grant_refuses_runtime_names_and_duplicates() -> None:
    for mounts in ((W.Mount("/h", "/lib", "ro"),), (W.Mount("/h", "/src", "ro"),),
                   (W.Mount("/h", "/scratch", "rw"), W.Mount("/i", "/scratch", "rw")),
                   (W.Mount("/h", "/project/a", "ro"), W.Mount("/i", "/project/a", "ro"))):
        with pytest.raises(W.WasmExecError):
            W.Grant(mounts, W.DEFAULT_LIMITS)
    with pytest.raises(W.WasmExecError):
        W.Mount(W.RUN_SCRATCH, "/project/x", "ro")
    with pytest.raises(W.WasmExecError):
        W.prepare(PROBE, grant=W.COMPUTE_ONLY, runtime_mounts=(W.Mount("/h", "/project/x", "ro"),))


def test_nest_constants_match_confinement() -> None:
    assert W.NEST_PROTECTED_REL == C.PROTECTED_REL
    assert W.NEST_KEY_DIR == SIGNING_KEY_DIR == C.KEY_DIR
    assert W.NEST_DEV_WRITES == C._DEV_WRITES


# --- traps, copy-out -----------------------------------------------------------------------------------------------

# Texts measured on wasmtime 36.0.16 (plan §4 V7, re-measured 2026-10-31, P02 CSV step 2).
_FUEL = b"Error: failed to run main module `spin.wasm`\n\nCaused by:\n    2: wasm trap: all fuel consumed by WebAssembly\n"
_INTR = b"Error: failed to run main module `spin.wasm`\n\nCaused by:\n    2: wasm trap: interrupt\n"
_GROW = b"Caused by:\n    2: forcing trap when growing memory to 131137536 bytes\n"
_UNR = b"Caused by:\n    2: wasm trap: wasm `unreachable` instruction executed\n"


def test_classify_trap_table() -> None:
    assert W.classify_trap(134, _FUEL) == "fuel"
    assert W.classify_trap(134, _INTR) == "timeout"
    assert W.classify_trap(1, _GROW) == "memory"
    assert W.classify_trap(134, _UNR) == "trap:wasm `unreachable` instruction executed"
    assert W.classify_trap(1, b"Error: expected `(`\n") == "wasmtime-error"
    assert W.classify_trap(7, b"") is None and W.classify_trap(0, _FUEL) is None
    assert W.classify_trap(0, b"", host_label="output-cap") == "output-cap"
    assert W.classify_trap(134, _FUEL, host_label="host-timeout") == "host-timeout"
    assert W.classify_trap(134, _FUEL + b"x" * 5000) is None  # only the last 4 KiB is scanned


@pytest.mark.parametrize("name,refused", [("CLAUDE.md", True), ("claude.MD", True), ("sub/AGENTS.md", True),
                                          ("GEMINI.md", True), (".cursorrules", True), ("x.agent.md", True),
                                          ("X.Agent.MD", True), ("a\\b\\agents.md", True), ("notes.md", False),
                                          ("agent.md", False), ("CLAUDE.md.txt", False)])
def test_copy_out_refused_names(name: str, refused: bool) -> None:
    assert W.copy_out_refused(name) is refused


# --- module checks -------------------------------------------------------------------------------------------------

def test_module_header_check(tmp_path: Path) -> None:
    assert W.prepare(PROBE, grant=W.COMPUTE_ONLY).module_sha256 == W.DENY_PROBE_SHA256
    cases = {"component.wasm": b"\0asm\x0d\0\x01\0rest", "text.wat": b"(module)", "empty.wasm": b"",
             "v2.wasm": b"\0asm\x02\0\0\0"}
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
        with pytest.raises(W.WasmExecError, match="component" if "component" in name else "core module"):
            W.prepare(tmp_path / name, grant=W.COMPUTE_ONLY)
    (tmp_path / "link.wasm").symlink_to(PROBE)
    with pytest.raises(W.WasmExecError, match="symlink"):
        W.prepare(tmp_path / "link.wasm", grant=W.COMPUTE_ONLY)
    big = tmp_path / "big.wasm"
    with open(big, "wb") as fh:
        fh.write(b"\0asm\x01\0\0\0")
        fh.truncate(W.MAX_MODULE_BYTES + 1)
    with pytest.raises(W.WasmExecError, match="at most"):
        W.prepare(big, grant=W.COMPUTE_ONLY)


def test_deny_probe_sha_constant_matches_file() -> None:
    assert hashlib.sha256(PROBE.read_bytes()).hexdigest() == W.DENY_PROBE_SHA256


@pytest.mark.skipif(shutil.which("jco") is None and not os.path.exists(os.path.expanduser("~/.local/bin/jco")),
                    reason="jco not installed")
def test_deny_probe_binary_matches_source(tmp_path: Path) -> None:
    jco = shutil.which("jco") or os.path.expanduser("~/.local/bin/jco")
    subprocess.run([jco, "parse", str(WAT), "-o", str(tmp_path / "p.wasm")], check=True, capture_output=True)
    assert (tmp_path / "p.wasm").read_bytes() == PROBE.read_bytes()


def test_control_dir_tolerates_a_concurrent_creator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # P04 found it: two first runs raced lexists() -> mkdir() on .agentteams/wasm/exec and one refused.
    (tmp_path / ".agentteams" / "wasm").mkdir(parents=True)
    monkeypatch.setattr(W.os.path, "lexists", lambda p: False)  # the other run created each dir in between
    assert W._control_dir(str(tmp_path), ".agentteams", "wasm", "exec") == str(tmp_path / ".agentteams/wasm/exec")
    (tmp_path / "f").write_text("")
    with pytest.raises(W.WasmExecError, match="not a real directory"):
        W._control_dir(str(tmp_path), "f")


def test_prepare_src_files_and_grace() -> None:
    p = W.prepare(PROBE, grant=W.COMPUTE_ONLY, src_files={"b.py": b"2", "a.py": b"1"}, compile_grace_s=0)
    assert p.src_files == (("a.py", b"1"), ("b.py", b"2")) and p.argv0 == "wasm_deny_probe"
    for kw in ({"src_files": {"../x": b""}}, {"src_files": {"a": "text"}}, {"src_files": {"a": b"x" * (8 << 20 | 1)}},
               {"compile_grace_s": 121}, {"compile_grace_s": -1}):
        with pytest.raises(W.WasmExecError):
            W.prepare(PROBE, grant=W.COMPUTE_ONLY, **kw)


# --- source hygiene (R2) -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [W, wasm_pins, SP])
def test_no_intra_package_imports(mod: types.ModuleType) -> None:
    for node in ast.walk(ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and not (node.module or "").startswith("agentteams"), node.module
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("agentteams") for a in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("__import__",), "dynamic import"


#: Files allowed to pass os_confine="inherited" (R5): the executor itself and the P08 launcher (merged as
#: ``cli/wasm_agent_commands.py``; ``tests/test_wasm_framework_cli.py`` holds the stricter AST check).
_INHERITED_OK = re.compile(r"^agentteams/(wasm_exec\.py|wasm_agent/launch[a-z_]*\.py|cli/wasm_agent_commands\.py)$")


def test_only_p08_launcher_passes_inherited() -> None:
    offenders = []
    for path in (REPO / "agentteams").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if re.search(r"""os_confine\s*=\s*["']inherited["']""", path.read_text(encoding="utf-8", errors="replace")) \
                and not _INHERITED_OK.match(rel):
            offenders.append(rel)
    assert offenders == []


# --- materialize: env, run dir, copies, mounts, nesting -------------------------------------------------------------

def test_spawn_env_empty_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "canary")
    seen = {}

    def boom(argv, **kw):
        seen.update(kw)
        raise OSError("stop")
    monkeypatch.setattr(W.subprocess, "Popen", boom)
    root = project(tmp_path)
    with pytest.raises(W.WasmExecError, match="could not start"):
        W.run(W.prepare(PROBE, grant=W.COMPUTE_ONLY), [], project_root=root, os_confine="off",
              resolver=FakeResolver(_fake_wasmtime(tmp_path), "1.0.0"), profiles=SP)
    assert seen["env"] == {} and seen["start_new_session"] is True and seen["shell"] is False


def test_spawn_env_systemroot_only_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(W.os, "name", "nt")
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "canary")
    assert W._spawn_env() == {"SYSTEMROOT": "C:/Windows"}


def _launch(tmp_path: Path, grant: W.Grant = W.COMPUTE_ONLY, *, kind: str | None = None, os_confine: str = "auto",
            **kw: object) -> W.Launch:
    root = kw.pop("root", None) or project(tmp_path)
    resolver = kw.pop("resolver", None) or FakeResolver(_fake_wasmtime(tmp_path), "1.0.0")
    return W.materialize(W.prepare(PROBE, grant=grant, src_files=kw.pop("src", None)), ["deny"], project_root=root,
                         os_confine=os_confine, resolver=resolver, profiles=_profiles(kind), **kw)  # type: ignore


def test_verified_copy_dir_in_control_plane(tmp_path: Path) -> None:
    launch = _launch(tmp_path)
    try:
        root = os.path.realpath(tmp_path / "proj")
        assert launch.run_dir and launch.run_dir.startswith(os.path.join(root, ".agentteams", "wasm", "exec") + "/")
        assert re.fullmatch(r"[0-9a-f]{16}", os.path.basename(launch.run_dir))
        assert launch.argv[0] == os.path.join(launch.run_dir, "bin", "wasmtime")
        assert launch.argv[-2] == os.path.join(launch.run_dir, "bin", "module.wasm")
        assert oct(os.stat(launch.argv[0]).st_mode & 0o777) == "0o500"
        assert launch.env == {} and launch.cwd == launch.run_dir
    finally:
        launch.cleanup()
    assert not os.path.exists(launch.run_dir)


def test_unwritable_control_plane_refuses(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".agentteams").mkdir(mode=0o500)
    try:
        with pytest.raises(W.WasmExecError, match="session sandbox"):
            _launch(tmp_path, root=root)
        assert not any(p.name.startswith("agentteams") for p in Path(os.environ.get("TMPDIR", "/tmp")).glob("*wasm*"))
    finally:
        (root / ".agentteams").chmod(0o700)


def test_symlinked_control_plane_refused(tmp_path: Path) -> None:
    root = project(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (root / ".agentteams").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(W.WasmExecError, match="symlink"):
        _launch(tmp_path, root=root)


def test_in_place_only_when_protected_and_installed(tmp_path: Path) -> None:
    root = project(tmp_path)
    inst = root / ".agentteams" / "wasm" / "wasmtime" / "1.0.0"
    inst.mkdir(parents=True)
    shutil.copy(_fake_wasmtime(tmp_path), inst / "wasmtime")
    res = FakeResolver(str(inst / "wasmtime"), "1.0.0")
    launch = _launch(tmp_path, root=root, resolver=res, control_plane_protected=True)
    try:
        assert launch.argv[0] == str(inst / "wasmtime")
        assert launch.argv[-2] != str(PROBE.resolve()) and launch.run_dir  # the module still is not in place
    finally:
        launch.cleanup()
    launch = _launch(tmp_path, root=root, resolver=res, control_plane_protected=False)
    try:
        assert launch.argv[0] == os.path.join(launch.run_dir, "bin", "wasmtime")
    finally:
        launch.cleanup()
    scratch = root / ".agentteams" / "wasm" / "scratch" / "a"  # scratch is guest-writable: never "in place"
    scratch.mkdir(parents=True)
    shutil.copy(_fake_wasmtime(tmp_path), scratch / "wasmtime")
    launch = _launch(tmp_path, root=root, resolver=FakeResolver(str(scratch / "wasmtime"), "1"),
                     control_plane_protected=True)
    try:
        assert launch.argv[0] != str(scratch / "wasmtime")
    finally:
        launch.cleanup()


def test_expected_sha_mismatch_refused(tmp_path: Path) -> None:
    res = FakeResolver(_fake_wasmtime(tmp_path), "1.0.0")
    res.expected_sha256 = lambda c: "0" * 64  # type: ignore[attr-defined]
    with pytest.raises(W.WasmExecError, match="changed"):
        _launch(tmp_path, resolver=res)
    assert os.listdir(tmp_path / "proj" / ".agentteams" / "wasm" / "exec") == []


def test_resolver_errors_become_refusals(tmp_path: Path) -> None:
    class Bad:
        def resolve(self, component: str) -> Path:
            raise wasm_pins.WasmPinError("pin incomplete")

        def pinned_version(self, component: str) -> str:
            return "x"
    with pytest.raises(W.WasmExecError, match="pin incomplete"):
        _launch(tmp_path, resolver=Bad())


def _ro(root: Path, rel: str) -> W.Mount:
    return W.Mount(str(root / rel), "/project/" + rel.replace("/", "-").strip("."), "ro")


def test_mount_checks(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / ".git").mkdir()
    (root / "file.txt").write_text("x")
    (tmp_path / "outside").mkdir()
    (root / "link").symlink_to(root / "data")
    for host in (root / "link", root / ".git", root / "file.txt", tmp_path / "outside", root, root / "missing"):
        with pytest.raises(W.WasmExecError):
            _launch(tmp_path, W.Grant((W.Mount(str(host), "/project/x", "ro"),), W.DEFAULT_LIMITS), root=root)
    for host in (root / "scratch", root / ".agentteams" / "wasm" / "scratch" / "Bad_Name",
                 root / ".agentteams" / "wasm" / "scratch" / "a" / "b", root / ".agentteams" / "other"):
        with pytest.raises(W.WasmExecError, match="scratch"):
            _launch(tmp_path, W.Grant((W.Mount(str(host), "/scratch", "rw"),), W.DEFAULT_LIMITS), root=root)


def test_scratch_overlap_with_read_mount_refused(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch = root / ".agentteams" / "wasm" / "scratch" / "nav"
    grant = W.Grant((W.Mount(str(scratch), "/scratch", "rw"), W.Mount(str(root / "data"), "/project/data", "ro")),
                    W.DEFAULT_LIMITS)
    launch = _launch(tmp_path, grant, root=root)
    launch.cleanup()
    assert scratch.is_dir() and oct(scratch.stat().st_mode & 0o777) == "0o700"
    with pytest.raises(W.WasmExecError):  # a read mount over the control plane (and so the scratch) is refused
        _launch(tmp_path, W.Grant((W.Mount(str(scratch), "/scratch", "rw"),
                                   W.Mount(str(root / ".agentteams"), "/project/cp", "ro")), W.DEFAULT_LIMITS),
                root=root)


def test_runtime_mount_must_lie_in_module_dir(tmp_path: Path) -> None:
    root = project(tmp_path)
    mod = tmp_path / "py" / "python.wasm"
    (tmp_path / "py" / "lib").mkdir(parents=True)
    shutil.copy(PROBE, mod)
    res = FakeResolver(_fake_wasmtime(tmp_path), "1.0.0")
    ok = W.prepare(mod, grant=W.COMPUTE_ONLY, runtime_mounts=(W.Mount(str(tmp_path / "py" / "lib"), "/lib", "ro"),))
    launch = W.materialize(ok, [], project_root=root, os_confine="off", resolver=res, profiles=SP)
    try:
        assert launch.read_only_mode == "snapshot" and "/lib" in " ".join(launch.argv)
        assert not any(str(tmp_path / "py" / "lib") + "::" in a for a in launch.argv)  # the snapshot is preopened
    finally:
        launch.cleanup()
    bad = W.prepare(mod, grant=W.COMPUTE_ONLY, runtime_mounts=(W.Mount(str(root / "data"), "/lib", "ro"),))
    with pytest.raises(W.WasmExecError, match="inside"):
        W.materialize(bad, [], project_root=root, os_confine="off", resolver=res, profiles=SP)


def test_os_confine_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    for value, kind, want in (("auto", None, ("none", f"unavailable:{sys.platform}")), ("off", "seatbelt", ("none", "off")),
                              ("inherited", "seatbelt", ("inherited", "inherited"))):
        launch = _launch(tmp_path, root=root, kind=kind, os_confine=value)
        launch.cleanup()
        assert (launch.os_confinement, launch.os_reason) == want
        assert "sandbox-exec" not in launch.argv[0]
    err = capsys.readouterr().err
    assert f"WASM SANDBOX: wasmtime runs without OS confinement (unavailable:{sys.platform})" in err
    assert "WASM SANDBOX: wasmtime runs without OS confinement (off)" in err and "(inherited)" not in err
    with pytest.raises(W.WasmExecError, match="require"):
        _launch(tmp_path, root=root, kind=None, os_confine="require")
    with pytest.raises(W.WasmExecError, match="os_confine"):
        _launch(tmp_path, root=root, kind=None, os_confine="maybe")


def test_nested_seatbelt_argv(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch = root / ".agentteams" / "wasm" / "scratch" / "nav"
    grant = W.Grant((W.Mount(str(scratch), "/scratch", "rw"), W.Mount(str(root / "data"), "/project/data", "ro")),
                    W.DEFAULT_LIMITS)
    launch = _launch(tmp_path, grant, root=root, kind="seatbelt", os_confine="require")
    try:
        assert launch.argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
        prof, inner = launch.argv[2], launch.argv[3:]
        assert (launch.os_confinement, launch.os_reason, launch.read_only_mode) == ("nested", "nested:seatbelt", "os")
        assert prof.rstrip().endswith("(deny network*)")
        assert f'(literal "{inner[0]}")' in prof and inner[0].endswith("/bin/wasmtime")
        assert f'(subpath "{os.path.realpath(os.path.expanduser("~/.ssh"))}")' in prof
        assert prof.index(f'(subpath "{scratch}")') > prof.index(f'(subpath "{root / ".agentteams"}")')
        assert f"{root / 'data'}::/project/data" in inner  # nested: the real path, OS-enforced read-only
        W.assert_grant_shape(inner, W.Grant((W.Mount(str(scratch), "/scratch", "rw"),
                                             W.Mount(str(root / "data"), "/project/data", "ro")), W.DEFAULT_LIMITS))
    finally:
        launch.cleanup()


def test_nested_bwrap_argv(tmp_path: Path) -> None:
    root = project(tmp_path)
    grant = W.Grant((W.Mount(W.RUN_SCRATCH, "/scratch", "rw"),), W.DEFAULT_LIMITS)
    launch = _launch(tmp_path, grant, root=root, kind="bwrap", os_confine="auto")
    try:
        sep = launch.argv.index("--")
        prefix = launch.argv[:sep]
        assert "--unshare-net" in prefix and launch.os_confinement == "nested"
        scratch = os.path.join(launch.run_dir, "scratch")
        assert prefix[prefix.index(scratch) - 1] == "--bind"
        assert launch.argv[sep + 1].endswith("/bin/wasmtime")
    finally:
        launch.cleanup()


def test_cleanup_does_not_follow_symlinks(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    victim.chmod(0o500)
    launch = _launch(tmp_path, W.Grant((W.Mount(W.RUN_SCRATCH, "/scratch", "rw"),), W.DEFAULT_LIMITS))
    os.symlink(victim, os.path.join(launch.run_dir, "scratch", "evil"))
    launch.cleanup()
    assert not os.path.exists(launch.run_dir) and (victim / "keep.txt").read_text() == "keep"
    assert oct(victim.stat().st_mode & 0o777) == "0o500"
    victim.chmod(0o700)


def test_src_files_written_private_and_read_only(tmp_path: Path) -> None:
    launch = _launch(tmp_path, src={"main.py": b"print(1)\n"}, os_confine="off")
    try:
        src = os.path.join(launch.run_dir, "src")
        assert f"{src}::/src" in launch.argv and launch.read_only_mode == "snapshot"
        assert oct(os.stat(src).st_mode & 0o777) == "0o500"
        assert Path(src, "main.py").read_bytes() == b"print(1)\n"
    finally:
        launch.cleanup()


def test_host_timeout_kills_the_group(tmp_path: Path) -> None:
    launch = W.Launch(argv=["/bin/sh", "-c", "sleep 30 & sleep 30"], env={}, cwd=str(tmp_path), run_dir=None,
                      cleanup=lambda: None, os_confinement="none", os_reason="off", read_only_mode="none",
                      timeout_s=1, module_sha256="x")
    code, out, err, host = W._execute(launch, b"", 1 << 10, 1 << 10)
    assert host == "host-timeout" and code == -9


def test_stdin_and_caps_validated(tmp_path: Path) -> None:
    p = W.prepare(PROBE, grant=W.COMPUTE_ONLY)
    res = FakeResolver(_fake_wasmtime(tmp_path), "1.0.0")
    for kw in ({"stdin": b"x" * (W.MAX_STDIN_BYTES + 1)}, {"stdin": "text"}, {"stdout_cap": 0}):
        with pytest.raises(W.WasmExecError):
            W.run(p, [], project_root=project(tmp_path) if not (tmp_path / "proj").exists() else tmp_path / "proj",
                  os_confine="off", resolver=res, profiles=SP, **kw)  # type: ignore[arg-type]


def test_execute_launch_runs_a_prepared_launch_with_runs_checks(tmp_path: Path) -> None:
    launch = W.Launch(argv=[sys.executable, "-I", "-c", "import sys; sys.stdout.write(sys.stdin.read() * 3)"], env={},
                      cwd=str(tmp_path), run_dir=None, cleanup=lambda: None, os_confinement="none", os_reason="off",
                      read_only_mode="none", timeout_s=30, module_sha256="m" * 64)
    res = W.execute_launch(launch, b"ab", stdout_cap=4, stderr_cap=64)
    assert res.truncated and res.trap == "output-cap" and res.module_sha256 == "m" * 64
    assert W.execute_launch(launch, b"ab", stdout_cap=64, stderr_cap=64).stdout == b"ababab"
    for stdin, out_cap, err_cap in ((b"x" * (W.MAX_STDIN_BYTES + 1), 1, 1), ("text", 1, 1), (b"", 0, 1),
                                    (b"", 1, 0), (b"", True, 1)):
        with pytest.raises(W.WasmExecError):
            W.execute_launch(launch, stdin, stdout_cap=out_cap, stderr_cap=err_cap)  # type: ignore[arg-type]


def test_remove_tree_restores_modes_and_never_follows_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    run = tmp_path / "run"
    (run / "ro").mkdir(parents=True)
    (run / "ro" / "f").write_text("x")
    (run / "link").symlink_to(outside)
    os.chmod(run / "ro", 0o500)
    SP.remove_tree(str(run / "link"))                 # a symlink itself is left alone
    SP.remove_tree(str(run))
    assert not run.exists() and (outside / "keep").read_text() == "k"


def test_nested_deny_reads_cover_the_common_credential_stores() -> None:
    for store in ("~/.ssh", "~/.aws", "~/.gnupg", "~/.docker", "~/.kube", "~/.npmrc", "~/.pypirc", "~/.netrc",
                  "~/Library/Keychains"):
        assert store in W.NEST_DENY_READS


# --- snapshot copies (R7; the walker lives in _sandbox_profiles) -----------------------------------------------------

def _snap(src: Path, dst: Path, budget: list[int] | None = None) -> None:
    SP.snapshot_tree(str(src), str(dst), budget or [W.SNAPSHOT_MAX_BYTES, W.SNAPSHOT_MAX_ENTRIES])


def test_snapshot_copies_read_only(tmp_path: Path) -> None:
    (tmp_path / "s" / "d").mkdir(parents=True)
    (tmp_path / "s" / "d" / "a.txt").write_text("A")
    _snap(tmp_path / "s", tmp_path / "out")
    assert (tmp_path / "out" / "d" / "a.txt").read_text() == "A"
    assert oct((tmp_path / "out" / "d" / "a.txt").stat().st_mode & 0o777) == "0o400"
    assert oct((tmp_path / "out" / "d").stat().st_mode & 0o777) == "0o500"
    for d in (tmp_path / "out", tmp_path / "out" / "d"):
        d.chmod(0o700)


@pytest.mark.parametrize("kind", ["symlink", "dirlink", "hardlink", "fifo", "socket", "top-symlink"])
def test_snapshot_refuses_special_entries(tmp_path: Path, kind: str) -> None:
    src = tmp_path / "s"
    src.mkdir()
    (tmp_path / "secret").write_text("s")
    if kind == "symlink":
        (src / "x").symlink_to(tmp_path / "secret")
    elif kind == "dirlink":
        (src / "x").symlink_to(tmp_path)
    elif kind == "hardlink":
        os.link(tmp_path / "secret", src / "x")
    elif kind == "fifo":
        os.mkfifo(src / "x")
    elif kind == "socket":
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(src / "x"))
        except OSError:
            pytest.skip("cannot bind a unix socket here")
        sock.close()
    else:
        src.rmdir()
        src.symlink_to(tmp_path / "secret")
    with pytest.raises((SP.ConfinementError, OSError)):
        _snap(src, tmp_path / "out")


def test_snapshot_caps_enforced_midcopy(tmp_path: Path) -> None:
    src = tmp_path / "s"
    src.mkdir()
    for i in range(5):
        (src / f"f{i}").write_bytes(b"x" * 1000)
    with pytest.raises(SP.ConfinementError, match="too large"):
        _snap(src, tmp_path / "o1", [2500, 100])
    with pytest.raises(SP.ConfinementError, match="too large"):
        _snap(src, tmp_path / "o2", [10**9, 3])
