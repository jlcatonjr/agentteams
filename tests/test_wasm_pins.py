"""Tests for agentteams/wasm_pins.py — the pure pins check behind every wasmtime/interpreter resolve (R10).

The pins here are synthetic fixtures computed from files the test writes; no real release hash appears (Rule 5).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from agentteams import wasm_exec as W
from agentteams import wasm_pins as P


def _install(tmp_path: Path) -> tuple[Path, dict]:
    inst = Path(os.path.realpath(tmp_path)) / ".agentteams" / "wasm"
    wt = inst / "wasmtime" / "9.9.9"
    wt.mkdir(parents=True)
    (wt / "wasmtime").write_bytes(b"fake wasmtime\n")
    py = inst / "python" / "1.2.3"
    (py / "lib" / "pkg").mkdir(parents=True)
    (py / "python.wasm").write_bytes(b"\0asm\x01\0\0\0")
    (py / "lib" / "a.py").write_text("a")
    (py / "lib" / "pkg" / "b.py").write_text("b")
    digest, count = P.tree_digest(py / "lib")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    pins = {"schema": "agentteams-wasm-runtime-pins/v1", "components": {
        "wasmtime": {"version": "9.9.9", "runtime_mounts": [], "platforms": {P.host_platform(): {
            "entry": "wasmtime", "files": {"wasmtime": {"sha256": sha(wt / "wasmtime"), "exec": True}}}}},
        "python": {"version": "1.2.3", "runtime_mounts": [{"tree": "lib", "guest": "/lib"}], "platforms": {"any": {
            "entry": "python.wasm", "files": {"python.wasm": {"sha256": sha(py / "python.wasm")}},
            "tree": {"root": "lib", "sha256": digest, "count": count}}}},
        "javascript": {"version": "0.1.0", "platforms": {"any": {"entry": "qjs.wasm",
                                                                  "files": {"qjs.wasm": {"sha256": "TODO-PIN"}}}}}}}
    return inst, pins


def test_resolve_and_version(tmp_path: Path) -> None:
    inst, pins = _install(tmp_path)
    r = P.pinned_resolver(inst, pins)
    assert r.resolve("wasmtime") == inst / "wasmtime" / "9.9.9" / "wasmtime"
    assert r.pinned_version("wasmtime") == "9.9.9"
    assert r.resolve("python").name == "python.wasm"  # the "any" platform fallback
    assert r.expected_sha256("wasmtime") == pins["components"]["wasmtime"]["platforms"][P.host_platform()][
        "files"]["wasmtime"]["sha256"]


def test_tree_digest_format(tmp_path: Path) -> None:
    (tmp_path / "t" / "d").mkdir(parents=True)
    (tmp_path / "t" / "x").write_bytes(b"1")
    (tmp_path / "t" / "d" / "y").write_bytes(b"2")
    h = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    lines = sorted([f"d/y\0{h(b'2')}\n", f"x\0{h(b'1')}\n"])
    assert P.tree_digest(tmp_path / "t") == (hashlib.sha256("".join(lines).encode()).hexdigest(), 2)


def test_mismatch_and_missing_refused(tmp_path: Path) -> None:
    inst, pins = _install(tmp_path)
    r = P.pinned_resolver(inst, pins)
    (inst / "wasmtime" / "9.9.9" / "wasmtime").write_bytes(b"tampered\n")
    with pytest.raises(P.WasmPinError, match="does not match"):
        r.resolve("wasmtime")
    with pytest.raises(P.WasmPinError, match="no pins"):
        r.resolve("rust")
    with pytest.raises(P.WasmPinError, match="platform"):
        P.pinned_resolver(inst, pins, platform="plan9-mips").resolve("wasmtime")
    (inst / "wasmtime" / "9.9.9" / "wasmtime").unlink()
    with pytest.raises(P.WasmPinError, match="does not exist"):
        r.resolve("wasmtime")


def test_todo_pin_fails_closed(tmp_path: Path) -> None:
    inst, pins = _install(tmp_path)
    (inst / "javascript" / "0.1.0").mkdir(parents=True)
    (inst / "javascript" / "0.1.0" / "qjs.wasm").write_bytes(b"x")
    with pytest.raises(P.WasmPinError, match="pin incomplete.*Rule 5"):
        P.pinned_resolver(inst, pins).resolve("javascript")


@pytest.mark.parametrize("rel", ["wasmtime", "wasmtime/9.9.9", "wasmtime/9.9.9/wasmtime"])
def test_symlink_anywhere_refused(tmp_path: Path, rel: str) -> None:
    inst, pins = _install(tmp_path)
    target = inst / rel
    os.rename(target, tmp_path / "moved")
    target.symlink_to(tmp_path / "moved")
    with pytest.raises(P.WasmPinError, match="symlink"):
        P.pinned_resolver(inst, pins).resolve("wasmtime")


def test_resolve_tree_and_runtime_mounts(tmp_path: Path) -> None:
    inst, pins = _install(tmp_path)
    r = P.pinned_resolver(inst, pins)
    lib = inst / "python" / "1.2.3" / "lib"
    assert r.runtime_mounts("python") == ((str(lib), "/lib"),)
    assert r.runtime_mounts("wasmtime") == ()
    W.Mount(*r.runtime_mounts("python")[0], "ro")  # the pairs are valid executor mounts
    (lib / "added.py").write_text("x")
    with pytest.raises(P.WasmPinError, match="tree"):
        r.resolve_tree("python")
    (lib / "added.py").unlink()
    (lib / "link.py").symlink_to(lib / "a.py")
    with pytest.raises(P.WasmPinError, match="not a regular file"):
        r.resolve_tree("python")


def test_errors_are_runtime_errors_the_executor_wraps() -> None:
    assert issubclass(P.WasmPinError, RuntimeError)
