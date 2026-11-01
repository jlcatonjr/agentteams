"""WASM plan P17 §3: the runtime matrix, from a brief's grant to a guest's view (P01 -> P04 -> P02).

Per-plan tests own each layer (``test_wasm_exec*.py``, ``test_wasm_guests*.py``); this file crosses them:

* **No runtime needed.** The grant the brief derives (``wasm_sandbox.agents.<slug>.read_paths`` + ``scratch``) is
  what ``run_code`` takes, and its R7 snapshot checks refuse a symlink, a hardlink or a FIFO planted in a granted dir
  after the render, a read path overlapping the scratch dir, and a tree past the size cap, before any guest starts.
  R8: guest-written files land only in ``.agentteams/wasm/scratch/<agent>/``, under the control plane R1 denies and
  outside every framework's instruction dir. A project path containing ``::`` (the verified ``--dir`` bypass) is
  refused, at grant derivation and at render.
* **Live (dev wasmtime; dev CPython-WASI for Python; skip otherwise).** For each language x ``os_confine``
  (``auto`` / ``off`` / ``inherited``) the deny probe shows no file, env or network access; fuel and timeout traps
  are reported; ``os_confinement`` is exactly ``nested`` / ``inherited`` / ``none`` with ``read_only_mode == "os"``
  only when ``nested`` (R5: only nested counts as enforced). ``require`` either nests or refuses, never a false pass.
  JavaScript skips until QuickJS-ng is pinned and installed (D10).

Seatbelt cannot nest inside this session's sandbox (``sandbox_apply: Operation not permitted``), so here ``auto``
reports ``none`` and the nested assertions skip with that reason; the operator runs them unnested
(``PENDING-operator-commands.md``, P17).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

import build_team
from agentteams import _sandbox_profiles as SP
from agentteams import analyze
from agentteams import wasm_exec as W
from agentteams import wasm_guests as G
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.registry import FRAMEWORKS, agents_dir_for
from agentteams.sandbox_state import SCRATCH_REL, effective_sandbox_state, wasm_grant_for
from tests._fresh_intel import seed_offline_intel
from tests._wasm_helpers import PROBE, FakeResolver, dev_wasmtime, needs_wasmtime
from tests.test_wasm_guests_live import DevResolver, dev_python_dir, needs_js_guest

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
AGENT = "primary-producer"
_NESTABLE = SP.available()
_NO_NEST = (f"no nestable OS sandbox here (kind={SP.kind()!r}; Seatbelt cannot nest inside a sandboxed session) - "
            "operator gate: run unnested")
needs_python = pytest.mark.skipif(dev_wasmtime() is None or dev_python_dir() is None,
                                  reason="no dev wasmtime + CPython WASI build (AGENTTEAMS_WASM_DEV_*)")


def _brief(read_paths: list[str], scratch: bool = True) -> dict[str, Any]:
    return {**BRIEF, "wasm_sandbox": {"enabled": True, "native_exec_keep": [],
                                      "agents": {AGENT: {"read_paths": read_paths, "scratch": scratch}}}}


def _project(base: Path) -> Path:
    root = Path(os.path.realpath(base)) / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "f.txt").write_text("granted data\n")
    (root / "mods").mkdir()
    shutil.copy(PROBE, root / "mods" / "probe.wasm")
    return root


def _brief_grant(root: Path, read_paths: list[str], scratch: bool = True) -> W.Grant:
    """The grant P01 derives for AGENT from a brief (the one ``run_code`` / the MCP server / the runner receive)."""
    manifest = analyze.build_manifest(_brief(read_paths, scratch), framework="claude")
    manifest[PROJECT_ROOT_KEY] = root
    grant = wasm_grant_for(effective_sandbox_state(manifest), AGENT)
    assert grant is not None
    return grant


# --- R7 / R8 / '::' without a runtime --------------------------------------------------------------------------------

def _module_run(root: Path, grant: W.Grant, **kw: Any) -> Any:
    """``run_code("module", ...)`` with a resolver that never reaches a real wasmtime (refusals precede the spawn)."""
    return G.run_code("module", None, "mods/probe.wasm", args=["deny"], grant=grant, project_root=root,
                      os_confine="off", resolver=FakeResolver(sys.executable, version="none"), executor=W,
                      profiles=SP, **kw)


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: Any, **k: Any) -> Any:
        raise AssertionError("the guest started; the refusal must come first")
    monkeypatch.setattr(W, "execute_launch", refuse)


def _plant(root: Path, kind: str) -> None:
    target = root / "data"
    if kind == "symlink":
        (root / "outside.txt").write_text("not granted\n")
        os.symlink(root / "outside.txt", target / "link.txt")
    elif kind == "hardlink":
        (root / "outside.txt").write_text("not granted\n")
        os.link(root / "outside.txt", target / "hard.txt")
    elif kind == "fifo":
        os.mkfifo(target / "pipe")
    elif kind == "dirlink":
        (root / "outside").mkdir()
        os.symlink(root / "outside", target / "sub")


_R7_CAUSE = {"symlink": "link.txt is a symlink", "hardlink": "hard.txt is a hardlink",
             "fifo": "pipe is a FIFO, socket or device", "dirlink": "sub is a symlink"}


@pytest.mark.parametrize("kind", sorted(_R7_CAUSE))
def test_r7_entry_planted_in_a_brief_granted_dir_after_render_is_refused(tmp_path: Path, kind: str,
                                                                         no_spawn: None) -> None:
    root = _project(tmp_path)
    grant = _brief_grant(root, ["data"])
    _plant(root, kind)
    with pytest.raises(G.WasmGuestError, match=f"snapshot of /project/data: snapshot: {_R7_CAUSE[kind]}; refused"):
        _module_run(root, grant)
    assert not os.listdir(root / ".agentteams" / "wasm" / "exec")   # the refused run left nothing behind


def test_r7_size_cap_is_enforced_during_the_copy(tmp_path: Path, no_spawn: None,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    (root / "data" / "big.bin").write_bytes(b"x" * 4096)
    grant = _brief_grant(root, ["data"])
    monkeypatch.setattr(W, "SNAPSHOT_MAX_BYTES", 1024)
    with pytest.raises(G.WasmGuestError, match="snapshot of /project/data: read mount too large to snapshot"):
        _module_run(root, grant)


@pytest.mark.parametrize("where, cause", [
    ("scratch-parent", r"host .* overlaps the protected .*/\.agentteams; refused"),
    ("project-root", r"host .* must be inside the project, not its root"),
])
def test_r7_a_read_path_overlapping_the_scratch_dir_is_refused(tmp_path: Path, where: str, cause: str,
                                                               no_spawn: None) -> None:
    """Every read mount that overlaps an agent scratch also touches the control plane or is the project root, so
    those rules refuse it before the overlap rule itself (``wasm_exec._check_mount_host``) is reached."""
    root = _project(tmp_path)
    grant = _brief_grant(root, ["data"])
    scratch = next(m for m in grant.mounts if m.guest == "/scratch")
    host = str(Path(scratch.host).parent) if where == "scratch-parent" else str(root)
    overlapping = W.Grant((W.Mount(host, "/project/sc", "ro"), scratch), grant.limits)
    Path(scratch.host).mkdir(parents=True)
    with pytest.raises(G.WasmGuestError, match=f"/project/sc: {cause}"):
        _module_run(root, overlapping)


def test_r8_scratch_lies_under_the_r1_control_plane_and_outside_every_instruction_dir(tmp_path: Path) -> None:
    root = _project(tmp_path)
    scratch = Path(next(m.host for m in _brief_grant(root, ["data"]).mounts if m.guest == "/scratch"))
    rel = scratch.relative_to(root).as_posix()
    assert rel == f"{SCRATCH_REL}/{AGENT}" and rel.split("/")[0] == ".agentteams"   # R1 denies .agentteams
    for fw in FRAMEWORKS:
        agents = agents_dir_for(fw, root).relative_to(root).as_posix()
        assert not rel.startswith(agents.rstrip("/") + "/"), fw
    assert all(W.copy_out_refused(f"{rel}/{n}") for n in ("CLAUDE.md", "AGENTS.md", "x.agent.md", "GEMINI.md",
                                                          ".cursorrules"))


@pytest.mark.parametrize("parent", ["a::b", "a:b"])
def test_colon_in_the_project_path_refuses_the_grant(tmp_path: Path, parent: str) -> None:
    root = _project(tmp_path / parent)
    with pytest.raises(W.WasmExecError, match="without ':'"):
        _brief_grant(root, ["data"])


def _render_in(root: Path) -> tuple[Any, str]:
    brief = root.parent / "b.json"
    brief.write_text(json.dumps({**_brief(["data"]), "enforce_decision_signing": False}), encoding="utf-8")
    extra = seed_offline_intel(root / ".claude" / "agents", "claude")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            rc: Any = build_team.main(["--description", str(brief), "--framework", "claude", "--project", str(root),
                                       "--yes", "--no-scan", *extra])
        except Exception as exc:   # noqa: BLE001 - the outcome under test
            rc = exc
    return rc, err.getvalue()


def test_colon_in_the_project_path_refuses_the_render_and_writes_no_agents(tmp_path: Path) -> None:
    root = _project(tmp_path / "a::b")
    rc, _ = _render_in(root)
    assert isinstance(rc, W.WasmExecError) and "without ':'" in str(rc)   # fail closed (the xfail below: not clean)
    assert not list((root / ".claude" / "agents").glob("*.md"))


@pytest.mark.xfail(strict=True, reason=(
    "P01/P02: a grant in a project whose path contains ':' escapes generate as an uncaught WasmExecError traceback "
    "(sandbox_state.grant_from_tokens -> wasm_exec.Mount) instead of an 'Error:' line and exit 1"))
def test_colon_in_the_project_path_is_a_clean_cli_refusal(tmp_path: Path) -> None:
    root = _project(tmp_path / "a::b")
    rc, err = _render_in(root)
    assert rc == 1 and "Error:" in err and "':'" in err


# --- live matrix -----------------------------------------------------------------------------------------------------

CONFINES = ("auto", "off", "inherited")
_LANGS = [pytest.param("module", marks=needs_wasmtime, id="module"),
          pytest.param("python", marks=needs_python, id="python"),
          pytest.param("javascript", marks=needs_js_guest, id="javascript")]
#: The probe rows per class (module probe lines; Python / JS probe rows, ``wasm_guests.PROBE_DENY``).
_CLASSES = {"file": (b"PREOPEN0", ("fs-abs-file", "fs-abs-list", "fs-rel", "fs-parent", "fs-project")),
            "env": (b"ENV0", ("env", "env-canary")),
            "network": (b"SOCK0", ("net-connect", "net-dns"))}


def _resolver(language: str) -> Any:
    if language == "javascript":
        from agentteams import wasm_runtime
        return wasm_runtime.Resolver(Path(os.environ["AGENTTEAMS_WASM_LIVE_PROJECT"]))
    return DevResolver()


def _run(root: Path, language: str, confine: str, *, mode: str = "deny", grant: W.Grant = W.COMPUTE_ONLY) -> Any:
    """One guest run. ``inherited`` goes to the executor directly: only the P08 launcher may pass it (R5)."""
    src = None if language == "module" else G._probe_source(language).decode()
    mod = "mods/probe.wasm" if language == "module" else None
    if confine == "inherited":
        prepared, args = G.argv_for(language, src, mod, args=[mode], grant=grant, project_root=root,
                                    resolver=_resolver(language), executor=W)
        return W.run(prepared, args, project_root=root, os_confine="inherited", resolver=_resolver(language),
                     profiles=SP)
    return G.run_code(language, src, mod, args=[mode], grant=grant, project_root=root, os_confine=confine,
                      resolver=_resolver(language), executor=W, profiles=SP)


@pytest.fixture(scope="module")
def probes(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Deny-probe results per (language, confine), computed once, with canaries in the parent environment."""
    root = _project(tmp_path_factory.mktemp("runtime-matrix"))
    cache: dict[tuple[str, str], Any] = {}

    def get(language: str, confine: str) -> Any:
        if (language, confine) not in cache:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("AGENTTEAMS_WASM_CANARY", "parent-env-canary")
                mp.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-canary-not-real")
                cache[(language, confine)] = _run(root, language, confine)
        return cache[(language, confine)]
    get.root = root  # type: ignore[attr-defined]
    return get


@pytest.mark.parametrize("language", _LANGS)
@pytest.mark.parametrize("confine", CONFINES)
@pytest.mark.parametrize("cls", sorted(_CLASSES))
def test_deny_probe_class_fails_inside_the_guest(probes: Any, language: str, confine: str, cls: str) -> None:
    res = probes(language, confine)
    assert res.exit_code == 0 and res.trap is None, res.stderr[-400:]
    assert b"parent-env-canary" not in res.stdout + res.stderr and b"sk-ant-" not in res.stdout + res.stderr
    line, rows = _CLASSES[cls]
    if language == "module":
        assert line in res.stdout.splitlines(), res.stdout
    else:
        parsed = G.parse_probe(res.stdout)
        assert parsed is not None
        assert {r: parsed[r] for r in rows if parsed[r] not in ("DENIED", "ABSENT")} == {}


@pytest.mark.parametrize("language", _LANGS)
@pytest.mark.parametrize("confine", CONFINES)
def test_os_confinement_is_three_state_and_only_nested_is_enforced(probes: Any, language: str,
                                                                   confine: str) -> None:
    res = probes(language, confine)
    expected = {"off": "none", "inherited": "inherited", "auto": "nested" if _NESTABLE else "none"}[confine]
    assert res.os_confinement == expected, res.os_reason
    if confine == "auto" and not _NESTABLE:
        assert res.os_reason == f"unavailable:{sys.platform}"   # said, never a silent fresh-sandbox claim
    # read-only mounts (the interpreter's /lib, /src) are OS-enforced only when nested; else a snapshot
    assert (res.read_only_mode == "os") is (res.os_confinement == "nested")
    if language != "module":
        assert res.read_only_mode in ("os", "snapshot")


@pytest.mark.parametrize("language", _LANGS)
def test_require_nests_or_refuses_never_a_false_pass(probes: Any, language: str) -> None:
    if _NESTABLE:
        assert _run(probes.root, language, "require").os_confinement == "nested"
        return
    with pytest.raises((W.WasmExecError, G.WasmGuestError), match="require"):
        _run(probes.root, language, "require")


def _trap_limits(trap: str, language: str) -> W.Limits:
    """Fuel low enough to trap fast (an interpreter needs ~1e9 to start), or a 1 s timeout with fuel to spare."""
    if trap == "fuel":
        return W.Limits(fuel=1_000_000 if language == "module" else 2_000_000_000, max_memory_bytes=64 << 20,
                        timeout_s=60)
    return W.Limits(fuel=10**15, max_memory_bytes=64 << 20, timeout_s=1)


@pytest.mark.parametrize("language", _LANGS)
@pytest.mark.parametrize("confine", ["auto", "off"])
@pytest.mark.parametrize("trap", ["fuel", "timeout"])
def test_fuel_and_timeout_traps_are_reported(probes: Any, language: str, confine: str, trap: str) -> None:
    res = _run(probes.root, language, confine, mode="spin", grant=W.Grant((), _trap_limits(trap, language)))
    assert res.exit_code != 0 and res.trap == trap, (res.trap, res.stderr[-300:])
    if trap == "timeout":
        assert res.wall_s < 15


@needs_python
def test_r8_guest_written_instruction_file_stays_in_the_agent_scratch(tmp_path: Path) -> None:
    root = _project(tmp_path)
    grant = _brief_grant(root, ["data"])
    src = ("print(open('/project/data/f.txt').read().strip())\n"
           "for n in ('CLAUDE.md', 'x.agent.md', 'notes.txt'):\n    open('/scratch/' + n, 'w').write('obey')\n")
    res = G.run_code("python", src, None, grant=grant, project_root=root, os_confine="off", resolver=DevResolver(),
                     executor=W, profiles=SP)
    assert res.exit_code == 0 and res.stdout == b"granted data\n", res.stderr[-300:]
    scratch = root / SCRATCH_REL / AGENT
    assert sorted(p.name for p in scratch.iterdir()) == ["CLAUDE.md", "notes.txt", "x.agent.md"]
    assert sorted(p.name for p in scratch.iterdir() if W.copy_out_refused(p.name)) == ["CLAUDE.md", "x.agent.md"]
    assert not (root / "CLAUDE.md").exists() and not (root / "data" / "CLAUDE.md").exists()
    assert (root / "data" / "f.txt").read_text() == "granted data\n"


@needs_python
@pytest.mark.skipif(_NESTABLE is None, reason=_NO_NEST)
def test_nested_python_read_only_grant_is_os_enforced(tmp_path: Path) -> None:
    root = _project(tmp_path)
    res = G.run_code("python", "open('/project/data/f.txt', 'a').write('x')", None, grant=_brief_grant(root, ["data"]),
                     project_root=root, os_confine="require", resolver=DevResolver(), executor=W, profiles=SP)
    assert res.os_confinement == "nested" and res.read_only_mode == "os" and res.exit_code != 0
    assert (root / "data" / "f.txt").read_text() == "granted data\n"
