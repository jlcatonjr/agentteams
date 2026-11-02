"""Shared helpers for the WASM agent guest tests (P07).

The guest (``agentteams/data/wasm_agent_guest.py``) is a script, never imported by agentteams. Native tests load a
private copy of it as a module and remap its one filesystem seam, ``_fs``, so guest paths (``/project/<rel>``,
``/scratch``) land in a tmp tree; an unmapped guest path lands in a directory that does not exist, as an absent
WASI preopen would. Native runs test logic, never confinement.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from agentteams.wasm_agent import guest_source

GUEST_PATH = guest_source.guest_path()
RUN_ID = "ab" * 16
LIMITS = {"max_tool_output_bytes": 65536, "max_read_bytes": 1 << 20}
ALL_TOOLS = ["delegate", "list_dir", "read_file", "run_code", "search_files", "write_file"]


def load_guest(name: str = "wasm_agent_guest_under_test") -> ModuleType:
    """A fresh module object of the guest program (``__name__`` is not ``__main__``, so it does not run)."""
    spec = importlib.util.spec_from_file_location(name, GUEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remap(guest: ModuleType, mapping: Mapping[str, Path], missing: Path) -> None:
    """Point the guest's ``_fs`` at *mapping* (guest prefix -> host dir); anything else goes under *missing*."""
    def fs(path: str) -> str:
        for prefix, host in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
            if path == prefix or path.startswith(prefix + "/"):
                return str(host) + path[len(prefix):]
        return str(missing) + path
    guest._fs = fs


def frame(event: Mapping[str, Any], *, carry: Any = None, tools: list[str] | None = None,
          mounts: list[dict[str, str]] | None = None, limits: Mapping[str, int] | None = None,
          delegates: list[str] | None = None, **override: Any) -> dict[str, Any]:
    """A v1 event frame as the host's ``protocol.encode_event`` builds it (``agent.delegates`` since P09)."""
    out = {"v": 1, "type": "event", "accept": [1], "run_id": RUN_ID, "step": 0,
           "agent": {"slug": "primary-producer", "tools": ALL_TOOLS if tools is None else tools,
                     "delegates": ["a", "reviewer"] if delegates is None else delegates},
           "mounts": [{"guest": "/project/docs", "mode": "ro"}, {"guest": "/scratch", "mode": "rw"}]
           if mounts is None else mounts,
           "limits": dict(LIMITS if limits is None else limits), "carry": carry, "event": dict(event)}
    out.update(override)
    return out


def step(guest: ModuleType, fr: Mapping[str, Any] | bytes) -> dict[str, Any]:
    """Run one in-process step; return the decoded op (asserting the one-line ASCII output discipline)."""
    raw = fr if isinstance(fr, bytes) else json.dumps(fr).encode()
    line = guest.handle(raw)
    assert line.endswith("\n") and line.count("\n") == 1 and line.isascii()
    return json.loads(line)


def run_script(stdin: bytes) -> subprocess.CompletedProcess:
    """Run the guest as a script under the test interpreter (``-I``), as CPython-WASI runs ``/src/main.py``."""
    return subprocess.run([sys.executable, "-I", str(GUEST_PATH)], input=stdin, capture_output=True, timeout=60,
                          check=False)


def reply(*uses: tuple[str, str, Any], text: str = "", stop: str | None = None) -> dict[str, Any]:
    """A ``model_reply`` event body."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in uses]
    return {"kind": "model_reply", "stop": stop or ("tool_use" if uses else "end_turn"), "content": content}
