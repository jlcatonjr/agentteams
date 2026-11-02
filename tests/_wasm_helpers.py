"""Shared helpers for the WASM executor tests (P02): a dev wasmtime locator and fake resolvers.

wasmtime is located through ``AGENTTEAMS_WASM_DEV_WASMTIME`` (then ``~/.local/bin/wasmtime``); live tests skip when
neither exists. The dev binary is NOT the pinned 36.0.20 (D9), so the fake resolver reports whatever version the
binary prints: these tests prove the executor's behaviour, not the pin.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from agentteams import wasm_exec as W

PROBE = Path(W.__file__).with_name("data") / "wasm_deny_probe.wasm"


def dev_wasmtime() -> str | None:
    """The dev wasmtime binary, or None."""
    for cand in (os.environ.get("AGENTTEAMS_WASM_DEV_WASMTIME"), os.path.expanduser("~/.local/bin/wasmtime")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.realpath(cand)
    return None


def _version(binary: str) -> str:
    out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10, env={}).stdout
    found = re.match(r"^wasmtime (\d+\.\d+\.\d+) ", out)
    return found[1] if found else "unknown"


needs_wasmtime = pytest.mark.skipif(dev_wasmtime() is None,
                                    reason="no dev wasmtime (set AGENTTEAMS_WASM_DEV_WASMTIME)")


class FakeResolver:
    """Points ``wasmtime`` at a fixed path; ``pinned_version`` reports *version* (default: what the binary says)."""

    def __init__(self, path: str, version: str | None = None) -> None:
        self.path = path
        self.version = version if version is not None else _version(path)
        self.calls = 0

    def resolve(self, component: str) -> Path:
        assert component == "wasmtime"
        self.calls += 1
        return Path(self.path)

    def pinned_version(self, component: str) -> str:
        return self.version


def project(tmp_path: Path) -> Path:
    """A real-path project root with one ordinary data directory."""
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "f.txt").write_text("original\n")
    return root
