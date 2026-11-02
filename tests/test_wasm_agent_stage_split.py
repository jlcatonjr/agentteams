"""R4/R5/Rule-12 tests for the WASM agent host's Stage 0 / Stage 1 split (WASM P05).

* AST: the modules Stage 1 runs write no file and create no directory themselves.
* Live: a real Stage 1 subprocess with a Python audit hook records every write-capable filesystem call under
  ``.agentteams/``; there must be none (a negative control proves the hook sees one).
* R5: no wasm_agent module originates the ``"inherited"`` confinement literal; it is only compared or listed.
* Rule 12: the test-only broker / preparer stand-ins are unreachable from production code.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from agentteams.wasm_agent import launch as L
from tests._wasm_agent_helpers import project, text_reply
from tests.test_wasm_agent_launch import NativePreparer, _fake_profiles, _stage0

PKG = Path(L.__file__).parent
REPO = PKG.parent.parent
#: The modules whose code runs in Stage 1 (broker.py and transcript.py hold both sides and are checked by class).
STAGE1_MODULES = ("host.py", "mediation.py", "tools.py", "stage1.py", "protocol.py", "budget.py", "delegation.py")
_WRITE_CALLS = {"mkdir", "makedirs", "write_text", "write_bytes", "touch", "rename", "replace", "remove", "unlink",
                "rmdir", "rmtree", "chmod", "symlink", "link", "truncate", "write_new_atomic", "copy", "copyfile",
                "copytree", "mkdtemp", "mkstemp", "NamedTemporaryFile", "ensure_dir"}


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _writes(tree: ast.AST) -> list[str]:
    bad = []
    for call in _calls(tree):
        name = _name(call)
        if name in _WRITE_CALLS:
            bad.append(f"{name}() line {call.lineno}")
        elif name == "open" and isinstance(call.func, ast.Attribute) and getattr(call.func.value, "id", "") == "os":
            bad.append(f"os.open() line {call.lineno}")
        elif name == "open":
            modes = [a for a in call.args[1:2]] + [k.value for k in call.keywords if k.arg == "mode"]
            if any(not isinstance(m, ast.Constant) or set(str(m.value)) & set("wax+") for m in modes):
                bad.append(f"open(write) line {call.lineno}")
    return bad


@pytest.mark.parametrize("module", STAGE1_MODULES)
def test_stage1_modules_never_write(module: str) -> None:
    tree = ast.parse((PKG / module).read_text())
    assert _writes(tree) == [], f"{module} writes: {_writes(tree)}"


@pytest.mark.parametrize("module, cls", [("broker.py", "PipeBroker"), ("transcript.py", "PipeJournal")])
def test_stage1_classes_never_write(module: str, cls: str) -> None:
    tree = ast.parse((PKG / module).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    assert _writes(node) == []


def test_inherited_literal_is_never_originated() -> None:
    """R5: ``"inherited"`` may appear only inside a comparison or the GUEST_OS_CONFINE vocabulary."""
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text())
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) or (isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "GUEST_OS_CONFINE" for t in node.targets)):
                allowed |= {id(c) for c in ast.walk(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "inherited":
                assert id(node) in allowed, f"{path.name}:{node.lineno} originates 'inherited'"


def test_test_only_stand_ins_are_unreachable_from_production() -> None:
    """Rule 12 (S-9): the test brokers/preparers exist only under tests/; nothing in agentteams/ names them."""
    for path in (REPO / "agentteams").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in ("FnBroker", "NativePreparer", "_wasm_agent_helpers", "GuestRunner", "NativeGuestRunner",
                     "GuestPreparer", "_wasm_delegation_helpers"):
            assert name not in text, f"{path} mentions the test-only {name}"
    tree = ast.parse((PKG / "launch.py").read_text())
    built = {_name(c) for c in _calls(tree)} & {"LocalBroker", "PipeBroker", "StepPreparer"}
    assert built == {"LocalBroker", "StepPreparer"}            # Stage 1 builds the PipeBroker itself
    tree = ast.parse((PKG / "stage1.py").read_text())
    assert {_name(c) for c in _calls(tree)} & {"LocalBroker", "PipeBroker", "StepPreparer"} == {"PipeBroker"}
    tree = ast.parse((PKG / "delegation.py").read_text())     # P09: children reach Stage 0 only over the pipe
    assert {_name(c) for c in _calls(tree)} & {"LocalBroker", "PipeBroker", "StepPreparer", "FileJournal"} == {
        "PipeBroker"}


_HOOK = r"""
import os, sys
ROOT, LOG = {root!r}, os.open({log!r}, os.O_WRONLY | os.O_APPEND)
_W = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_EV = ("os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.chmod", "shutil.rmtree", "os.symlink", "os.link",
       "os.truncate", "os.utime", "shutil.copyfile")
def _under(p):
    try:
        return isinstance(p, (str, bytes, os.PathLike)) and os.fsdecode(p).startswith(ROOT)
    except Exception:
        return False
def _hook(ev, args):
    if ev == "open" and _under(args[0]) and isinstance(args[2], int) and args[2] & _W:
        os.write(LOG, ("WRITE open " + os.fsdecode(args[0]) + "\n").encode())
    elif ev in _EV and args and _under(args[0]):
        os.write(LOG, ("WRITE " + ev + " " + os.fsdecode(args[0]) + "\n").encode())
sys.addaudithook(_hook)
os.write(LOG, b"HOOKED\n")
{control}
"""


@pytest.mark.parametrize("control", [False, True], ids=["stage1", "negative-control"])
def test_live_stage1_writes_nothing_under_the_control_plane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            control: bool) -> None:
    root = project(tmp_path)
    log = tmp_path / "audit.log"
    log.write_text("")
    agt = os.path.join(os.path.realpath(root), ".agentteams")
    probe = f"open({os.path.join(agt, 'planted')!r}, 'w').close()" if control else ""
    monkeypatch.setattr(L, "_BOOT", _HOOK.format(root=agt, log=str(log), control=probe) + L._BOOT)
    monkeypatch.setattr(L, "StepPreparer", NativePreparer)
    monkeypatch.setattr(L, "host_profile_argv", lambda kind, **kw: [])
    res = _stage0(root, profiles=_fake_profiles("seatbelt"), stub_script=[text_reply("fine")])
    lines = log.read_text().splitlines()
    assert lines[0] == "HOOKED" and res.status == "ok", (lines, res)
    writes = [x for x in lines if x.startswith("WRITE")]
    assert bool(writes) == control, writes
