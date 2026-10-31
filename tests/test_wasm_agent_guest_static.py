"""Static checks on the WASM agent guest program and its pin (WASM P07).

The guest is reviewed as source and pinned by sha256, so these checks keep it reviewable and in step with the host:
an import allowlist (no sockets, processes, threads, ctypes), no dynamic code, a size ceiling, no agentteams module
importing it, the pin, and parity with the host's protocol constants, tool schemas and R8 name list.
"""

from __future__ import annotations

import ast
import hashlib
import shutil
from pathlib import Path

import pytest

from agentteams import integrity
from agentteams import wasm_exec as W
from agentteams.wasm_agent import guest_source as GS
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent import protocol as P
from agentteams.wasm_agent import tools as T
from agentteams.wasm_agent.budget import Budget
from tests._wasm_agent_guest_helpers import GUEST_PATH, load_guest

REPO = Path(__file__).resolve().parents[1]
SOURCE = GUEST_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "breakpoint", "input", "globals", "vars"}
MAX_LINES = 650   # plan target 600; 638 after the W5R trim (the rest is required docstrings)


def test_imports_are_on_the_allowlist() -> None:
    guest = load_guest()
    seen = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            seen.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            seen.add((node.module or "").split(".")[0])
    assert seen <= set(guest.ALLOWED_IMPORTS), seen - set(guest.ALLOWED_IMPORTS)
    assert not seen & {"socket", "subprocess", "threading", "ctypes", "multiprocessing", "importlib", "urllib",
                       "http", "ssl", "asyncio", "select", "signal", "agentteams"}


def test_no_dynamic_code_or_builtin_open() -> None:
    names = {n.id for n in ast.walk(TREE) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(TREE) if isinstance(n, ast.Attribute)}
    assert not names & FORBIDDEN_CALLS, names & FORBIDDEN_CALLS
    assert not attrs & {"system", "popen", "execv", "execve", "spawnv", "fork", "kill", "environ", "putenv",
                        "symlink", "link", "rename", "replace", "remove", "unlink", "rmdir", "chmod", "chown"}


def test_size_and_python_311_syntax() -> None:
    assert len(SOURCE.splitlines()) <= MAX_LINES
    ast.parse(SOURCE, feature_version=(3, 11))
    assert all(len(line) <= 120 for line in SOURCE.splitlines())


def test_no_agentteams_module_imports_or_execs_the_guest() -> None:
    for path in (REPO / "agentteams").rglob("*.py"):
        if path == GUEST_PATH:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "import wasm_agent_guest" not in text and "wasm_agent_guest import" not in text, path
    assert GS.GUEST_REL == "data/wasm_agent_guest.py"


def test_pin_matches_and_is_integrity_listed() -> None:
    data, digest = GS.load_guest_source()
    assert digest == hashlib.sha256(GUEST_PATH.read_bytes()).hexdigest() == GS.GUEST_SHA256 and data == \
        GUEST_PATH.read_bytes()
    pinned = set(integrity.ENFORCEMENT_MODULES)
    assert {"agentteams/data/wasm_agent_guest.py", "agentteams/wasm_agent/guest_source.py"} <= pinned


def test_a_tampered_guest_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = tmp_path / "guest.py"
    shutil.copy(GUEST_PATH, copy)
    copy.write_text(copy.read_text() + "\n# tampered\n")
    monkeypatch.setattr(GS, "guest_path", lambda: copy)
    with pytest.raises(GS.GuestSourceError, match="does not match its pin"):
        GS.load_guest_source()
    copy.unlink()
    with pytest.raises(GS.GuestSourceError, match="cannot read"):
        GS.load_guest_source()


def test_stage0_refuses_a_tampered_pinned_guest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = tmp_path / "guest.py"
    copy.write_bytes(GUEST_PATH.read_bytes() + b"#")
    monkeypatch.setattr(GS, "guest_path", lambda: copy)
    res = L.stage0(object(), "t", project_root=tmp_path, budget=Budget())        # type: ignore[arg-type]
    assert res.status == "refused" and "does not match its pin" in res.reason
    half = L.stage0(object(), "t", project_root=tmp_path, budget=Budget(), guest_source=b"x")  # type: ignore[arg-type]
    assert half.status == "refused" and "go together" in half.reason


def test_protocol_constants_match_the_host() -> None:
    g = load_guest()
    assert (g.PROTOCOL_VERSION, g.MAX_EVENT_BYTES, g.MAX_OP_BYTES, g.MAX_CARRY_BYTES, g.MAX_DEPTH) == \
        (P.PROTOCOL_VERSION, P.MAX_EVENT_BYTES, P.MAX_OP_BYTES, P.MAX_CARRY_BYTES, P.MAX_DEPTH)
    assert g._EVENT_KEYS == {"v", "type", "accept"} | {"run_id", "step", "agent", "mounts", "limits", "carry", "event"}
    assert set(g.LIMIT_CAPS) == {"max_tool_output_bytes", "max_read_bytes"}
    assert T.MAX_TOOL_OUTPUT_BYTES <= g.LIMIT_CAPS["max_tool_output_bytes"]
    assert T.MAX_READ_BYTES <= g.LIMIT_CAPS["max_read_bytes"]


def test_tool_schemas_match_the_host() -> None:
    g = load_guest()
    assert g.TOOL_SPEC_SHA256 == T.TOOL_SPEC_SHA256
    assert (g.IN_GUEST_TOOLS, g.HOST_TOOLS) == (T.IN_GUEST_TOOLS, T.HOST_TOOLS)
    specs = {s.name: s.input_schema for s in T.tool_specs(T.known_tools())}
    for name, (need, optional) in g.TOOL_INPUTS.items():
        schema = specs[name]
        assert tuple(schema["required"]) == need and set(schema["properties"]) == set(need) | set(optional), name
        assert all(p["type"] == "string" for p in schema["properties"].values()), name
    assert set(g.TOOL_INPUTS) == set(T.IN_GUEST_TOOLS)


@pytest.mark.parametrize("name", ["CLAUDE.md", "claude.MD", "AGENTS.md", "GEMINI.md", ".cursorrules", "a.agent.md",
                                  "A.AGENT.MD", "notes.md", "agent.md", "x/CLAUDE.md", "CLAUDE.md.bak", "readme"])
def test_r8_names_match_the_executor(name: str) -> None:
    g = load_guest()
    assert g.instruction_file_name(name) is W.copy_out_refused(name)


def test_r8_list_equals_the_executor_constant() -> None:
    g = load_guest()
    assert tuple(g.INSTRUCTION_FILE_NAMES) == tuple(W.REFUSED_COPY_OUT_NAMES)
    assert g.INSTRUCTION_FILE_SUFFIX == ".agent.md"
