"""WASM plan P17 §5: ARCH-A end to end — the rendered agent file is what launches ``agentteams_wasm`` (P01 -> P11 -> P10).

``test_wasm_mcp_server.py`` drives the server with argv the test builds (``wasm_mcp.launch_args``). Here a real
``--framework claude`` render with WASM on (ungoverned, so the widening runs under a notice) writes the server, its
bundle and each eligible subagent's ``mcpServers`` entry; the tests spawn the **exact command and args from the
rendered front matter** over stdio:

* a granted agent (``cleanup``: ``docs`` ro + scratch in the brief) and a compute-only one (``format-converter``);
* the render installed byte-exactly the packaged bundle, and the rendered pins hash is the installed pins file's;
* the rendered ``--grant-sha256`` is the server's digest: flags widened (read, scratch, limits) or the digest
  dropped refuse to start (the digest covers the grant, not the ``--agent`` name: R1 protects the files);
* without a runtime: ``tools/list`` is ``run_wasm`` only, a path outside the grant (or any path for the compute-only
  agent) is refused, and a call inside the grant reaches the clear ``--wasm-install`` error;
* an ineligible agent (``adversarial``, read-only) carries no server entry and no ``run_wasm`` tool;
* live (dev wasmtime + CPython WASI installed under a test pins file, whose hash replaces only the rendered
  ``--pins-sha256`` value): the granted agent reads ``/project/docs`` and writes its scratch, the compute-only agent
  sees no ``/project``; neither sees the parent environment.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

import build_team
from agentteams import wasm_mcp as M
from agentteams.sandbox_state import SCRATCH_REL
from tests import _wasm_mcp_helpers as H
from tests._fresh_intel import seed_offline_intel
from tests.test_wasm_guests_live import dev_python_dir
from tests._wasm_helpers import dev_wasmtime

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
GRANTED, COMPUTE, INELIGIBLE = "cleanup", "format-converter", "adversarial"
needs_live = pytest.mark.skipif(dev_wasmtime() is None or dev_python_dir() is None,
                                reason="no dev wasmtime + CPython WASI build (AGENTTEAMS_WASM_DEV_*)")
_PINS_FLAG = "--pins-sha256"


def _front(root: Path, slug: str) -> str:
    return (root / ".claude" / "agents" / f"{slug}.md").read_text(encoding="utf-8").split("\n---", 1)[0]


def _launch_of(root: Path, slug: str) -> tuple[str, list[str]]:
    """``(command, args)`` of the agent's rendered ``agentteams_wasm`` entry."""
    front = _front(root, slug)
    block = front[front.index("agentteams_wasm:"):]
    command = json.loads(re.search(r"^\s+command: (.+)$", block, re.M)[1])
    args = json.loads(re.search(r"^\s+args: (\[.+\])$", block, re.M)[1])
    return command, args


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = Path(str(tmp_path_factory.mktemp("archa"))).resolve()
    root = base / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("granted doc\n")
    (root / "secret").mkdir()
    (root / "secret" / "s.py").write_text("print('not granted')\n")
    (root / "docs" / "s.py").write_text("print('granted script')\n")
    brief = {**BRIEF, "enforce_decision_signing": False,
             "wasm_sandbox": {"enabled": True, "languages": ["module", "python"], "native_exec_keep": [],
                              "agents": {GRANTED: {"read_paths": ["docs"], "scratch": True}}}}
    path = base / "brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    extra = seed_offline_intel(root / ".claude" / "agents", "claude")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = build_team.main(["--description", str(path), "--framework", "claude", "--project", str(root), "--yes",
                              "--no-scan", "--os-sandbox", "on", *extra])
    assert rc == 0
    return root


class Server:
    """One spawned server, exactly as the host would launch it (cwd = project root, a minimal environment)."""

    def __init__(self, root: Path, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        self.proc = subprocess.Popen([command, *args], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env or {"PATH": "/usr/bin:/bin"})
        self.client = H.Client(self.proc)
        self.client.request("initialize", {"protocolVersion": "2024-11-05"})

    def __enter__(self) -> H.Client:
        return self.client

    def __exit__(self, *exc: Any) -> None:
        self.client.close()


def _start_refusal(root: Path, command: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([command, *args], cwd=root, input=b"", capture_output=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin"})


# --- what the render wrote -------------------------------------------------------------------------------------------

def test_render_installed_the_packaged_bundle_and_the_rendered_pins_hash_matches(rendered: Path) -> None:
    for rel, data in M.bundle_files():
        assert (rendered / rel).read_bytes() == data, rel
    installed = (rendered / M.PINS_INSTALLED).read_bytes()
    for slug in (GRANTED, COMPUTE):
        command, args = _launch_of(rendered, slug)
        assert command == M.interpreter() and args[:4] == ["-I", "-S", "-B", M.PROTECTED_PATH]
        assert args[args.index("--agent") + 1] == slug
        assert args[args.index(_PINS_FLAG) + 1] == hashlib.sha256(installed).hexdigest()
    assert ("--read" in _launch_of(rendered, GRANTED)[1]) and ("--read" not in _launch_of(rendered, COMPUTE)[1])


def test_an_ineligible_agent_has_no_server_and_no_run_wasm(rendered: Path) -> None:
    front = _front(rendered, INELIGIBLE)
    assert "agentteams_wasm" not in front and "run_wasm" not in front


# --- spawned from the rendered args, no runtime ----------------------------------------------------------------------

def test_granted_agent_tools_list_path_checks_and_missing_runtime(rendered: Path) -> None:
    command, args = _launch_of(rendered, GRANTED)
    with Server(rendered, command, args) as client:
        assert [t["name"] for t in client.request("tools/list")["result"]["tools"]] == ["run_wasm"]
        err, text = client.call({"language": "python", "path": "secret/s.py"})
        assert err and "not under a read path" in text
        err, text = client.call({"language": "python", "path": "docs/s.py"})    # inside the grant: gets as far
        assert err and "agentteams --wasm-install" in text                     # as the runtime check
        err, text = client.call({"language": "python", "source": "print(1)", "grant": {"read": ["secret"]}})
        assert err and "never from a call" in text


def test_compute_only_agent_refuses_every_path(rendered: Path) -> None:
    command, args = _launch_of(rendered, COMPUTE)
    with Server(rendered, command, args) as client:
        for path in ("docs/s.py", "secret/s.py"):
            err, text = client.call({"language": "python", "path": path})
            assert err and "no read paths" in text, path


def test_widening_the_rendered_flags_without_the_digest_refuses_to_start(rendered: Path) -> None:
    """The rendered ``--grant-sha256`` (P11) is the server's own digest (P10): a widened copy of the flags fails."""
    def edited(slug: str, edit: Any) -> tuple[str, list[str]]:
        command, args = _launch_of(rendered, slug)
        return command, edit(list(args))

    def insert(flags: list[str]) -> Any:
        return lambda a: [*a[:a.index("--fuel")], *flags, *a[a.index("--fuel"):]]

    def bump(flag: str) -> Any:
        return lambda a: [*a[:a.index(flag) + 1], str(int(a[a.index(flag) + 1]) * 2), *a[a.index(flag) + 2:]]

    tampered = [edited(COMPUTE, insert(["--read", "secret"])), edited(COMPUTE, insert(["--scratch"])),
                edited(GRANTED, insert(["--read", "secret"])), edited(COMPUTE, bump("--fuel")),
                edited(COMPUTE, bump("--timeout-s"))]
    for command, args in tampered:
        proc = _start_refusal(rendered, command, args)
        assert proc.returncode == 2 and proc.stdout == b"" and b"grant" in proc.stderr, (args, proc.stderr[-400:])
    command, args = edited(COMPUTE, lambda a: a[:a.index("--grant-sha256")])   # the digest flag dropped
    proc = _start_refusal(rendered, command, args)
    assert proc.returncode == 2 and proc.stdout == b"", proc.stderr[-400:]
    for slug in (GRANTED, COMPUTE):   # the rendered flags themselves start
        assert _start_refusal(rendered, *_launch_of(rendered, slug)).returncode == 0


# --- live: the dev runtime under a test pins file --------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_pins(rendered: Path) -> str:
    """Install the dev runtime + a matching test pins file over the rendered bundle; return the pins sha256."""
    return H.install_bundle(rendered, H.install_runtime(rendered, dev_python_dir()))


def _live_args(root: Path, slug: str, pins_sha: str) -> tuple[str, list[str]]:
    command, args = _launch_of(root, slug)
    args = list(args)
    args[args.index(_PINS_FLAG) + 1] = pins_sha   # the one value the dev runtime changes (Rule 5: test-only pins)
    return command, args


_PROBE = ("import os\nprint(sorted(os.environ))\n"
          "for p in ('/project/docs/a.md', '/project/secret/s.py'):\n"
          "    try:\n        print(p, open(p).read().strip())\n"
          "    except OSError as e:\n        print(p, 'DENIED', type(e).__name__)\n"
          "try:\n    open('/scratch/o.txt', 'w').write('x')\n    print('scratch', 'WROTE')\n"
          "except OSError as e:\n    print('scratch', 'DENIED', type(e).__name__)\n")


@needs_live
def test_live_granted_agent_reads_its_grant_and_writes_its_scratch(rendered: Path, live_pins: str) -> None:
    command, args = _live_args(rendered, GRANTED, live_pins)
    env = {"PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "sk-ant-api03-canary-not-real"}
    with Server(rendered, command, args, env=env) as client:
        err, out = client.call({"language": "python", "source": _PROBE})
    assert not err, out
    lines = out["stdout"].splitlines()
    assert lines[0] == "[]" and "canary" not in json.dumps(out)
    assert lines[1] == "/project/docs/a.md granted doc"
    assert lines[2].startswith("/project/secret/s.py DENIED") and lines[3] == "scratch WROTE"
    assert (rendered / SCRATCH_REL / GRANTED / "o.txt").read_text() == "x"
    assert out["agent"] == GRANTED and out["os_confinement"] in ("nested", "none")


@needs_live
def test_live_compute_only_agent_sees_no_project_and_no_scratch(rendered: Path, live_pins: str) -> None:
    command, args = _live_args(rendered, COMPUTE, live_pins)
    with Server(rendered, command, args) as client:
        err, out = client.call({"language": "python", "source": _PROBE})
    assert not err, out
    lines = out["stdout"].splitlines()
    assert lines[1].startswith("/project/docs/a.md DENIED") and lines[2].startswith("/project/secret/s.py DENIED")
    assert lines[3].startswith("scratch DENIED") and not (rendered / SCRATCH_REL / COMPUTE).exists()


# --- a grant on an agent the framework never wires -------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "P01/P11/D12: the shipped examples/software-project/wasm-sandbox.brief.json widens primary-producer's grant "
    "(SANDBOX RELAXATION printed; a signature when governed), but on claude primary-producer holds no execute tool, "
    "so it is not WASM-eligible and gets no agentteams_wasm; nothing says the grant is inert (D2: never silent)"))
def test_a_grant_on_an_ineligible_agent_is_noticed(tmp_path: Path) -> None:
    root = Path(str(tmp_path)).resolve() / "proj"
    for d in ("docs", "src"):
        (root / d).mkdir(parents=True)
    brief = json.loads((REPO / "examples" / "software-project" / "wasm-sandbox.brief.json").read_text("utf-8"))
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({**brief, "enforce_decision_signing": False}), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    extra = seed_offline_intel(root / ".claude" / "agents", "claude")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        assert build_team.main(["--description", str(path), "--framework", "claude", "--project", str(root),
                                "--yes", "--no-scan", *extra]) == 0
    assert "agentteams_wasm" not in _front(root, "primary-producer")      # the precondition of the finding
    notice = re.compile(r"not (wasm[- ])?eligible|not wired|no run_wasm|inert|unhonou?red|never used", re.I)
    assert any("primary-producer" in ln and notice.search(ln) for ln in (out.getvalue() + err.getvalue()).splitlines())
