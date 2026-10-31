"""Shared helpers for the agentteams_wasm server tests (WASM P10): a test install and a JSON-RPC stdio client.

The packaged pins are ``TODO-PIN`` here (D10), so the live tests install the DEV wasmtime / CPython builds
(``tests/_wasm_helpers.py`` and ``tests/test_wasm_guests_live.py`` locators) under a test pins file whose sha256 they
pass as ``--pins-sha256``. Test-only: nothing in ``agentteams/`` references these builds (Rule 5).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentteams import wasm_exec as W
from agentteams import wasm_mcp as M
from agentteams import wasm_pins
from tests._wasm_helpers import _version, dev_wasmtime

SERVER = Path(M.__file__).parent / "data" / "agentteams-wasm-mcp.py"
PKG_PROBE = Path(M.__file__).parent / "data" / "wasm_deny_probe.wasm"


def project(tmp_path: Path) -> Path:
    """A real-path project root with a data dir."""
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "f.txt").write_text("project data\n")
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_runtime(root: Path, python_dir: str | None) -> dict[str, Any]:
    """Copy the dev wasmtime (and CPython WASI, when given) into ``.agentteams/wasm`` and return matching pins."""
    idir = root / ".agentteams" / "wasm"
    wt = dev_wasmtime()
    assert wt is not None
    ver = _version(wt)
    dest = idir / "wasmtime" / ver
    dest.mkdir(parents=True)
    shutil.copy2(wt, dest / "wasmtime")
    comps: dict[str, Any] = {"wasmtime": {"version": ver, "platforms": {wasm_pins.host_platform(): {
        "entry": "wasmtime", "files": {"wasmtime": {"sha256": _sha(dest / "wasmtime")}}}}}}
    if python_dir:
        pdest = idir / "python" / "3.14.6"
        pdest.mkdir(parents=True)
        shutil.copy2(os.path.join(python_dir, "python.wasm"), pdest / "python.wasm")
        shutil.copytree(os.path.join(python_dir, "lib"), pdest / "lib", symlinks=True)
        tree, count = wasm_pins.tree_digest(pdest / "lib")
        comps["python"] = {"version": "3.14.6", "runtime_mounts": [{"guest": "/lib", "tree": "lib"}],
                           "platforms": {"any": {"entry": "python.wasm",
                                                 "files": {"python.wasm": {"sha256": _sha(pdest / "python.wasm")}},
                                                 "tree": {"root": "lib", "sha256": tree, "count": count}}}}
    return {"schema": "agentteams-wasm-runtime-pins/v1", "components": comps}


def install_bundle(root: Path, pins: dict[str, Any] | None = None,
                   replace: dict[str, bytes] | None = None) -> str:
    """Install the server + bundle (packaged pins unless *pins*); *replace* swaps files (tamper tests).

    Returns:
        The sha256 of the installed pins file (for ``--pins-sha256``).
    """
    files = dict(M.bundle_files())
    if pins is not None:
        files[M.PINS_INSTALLED] = json.dumps(pins, sort_keys=True).encode()
    files.update(replace or {})
    M.write_bundle(root, list(files.items()))
    return hashlib.sha256(files[M.PINS_INSTALLED]).hexdigest()


def launch(root: Path, args: list[str]) -> subprocess.Popen:
    """Start the installed server as the agent host would: the system python, cwd = project root."""
    return subprocess.Popen([M.interpreter(), *args], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin"})


class Client:
    """A newline-delimited JSON-RPC client over one server process."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc, self.next_id = proc, 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read().decode() if self.proc.stderr else "server closed stdout"
        reply = json.loads(line)
        assert reply["id"] == self.next_id
        return reply

    def call(self, arguments: dict) -> tuple[bool, Any]:
        """``(is_error, decoded text)`` of one ``run_wasm`` call."""
        res = self.request("tools/call", {"name": "run_wasm", "arguments": arguments})["result"]
        text = res["content"][0]["text"]
        return bool(res.get("isError")), (text if res.get("isError") else json.loads(text))

    def close(self) -> str:
        assert self.proc.stdin is not None and self.proc.stderr is not None
        self.proc.stdin.close()
        self.proc.wait(timeout=30)
        return self.proc.stderr.read().decode()


def args_for(agent: str, grant: W.Grant, pins_sha: str, languages: tuple[str, ...] = ("module", "python")
             ) -> list[str]:
    """``wasm_mcp.launch_args`` with a test pins hash."""
    return M.launch_args(agent, grant, languages, pins_sha)
