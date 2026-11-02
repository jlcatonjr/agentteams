"""Live tests for agentteams/wasm_guests.py (WASM P04): real wasmtime runs of Python, module and JavaScript guests.

Python runs against a DEV-ONLY CPython WASI build (``AGENTTEAMS_WASM_DEV_PYTHON``, else Pylance's bundled
``~/.vscode/extensions/ms-python.vscode-pylance-*/dist/wasi/python-3.14.6/``) served by a test-only fake resolver.
It is not a pin and is never referenced by ``agentteams/`` (Rule 5). The pinned builds run when installed under
``AGENTTEAMS_WASM_LIVE_PROJECT`` (``--wasm-install``); QuickJS-ng has no local build, so the JavaScript tests skip.

Runs use ``os_confine="auto"``. Seatbelt cannot nest inside a sandboxed session, so these runs are un-nested and
read-only mounts are snapshot-enforced; the nested variants skip with that reason.
"""

from __future__ import annotations

import glob
import hashlib
import os
import threading
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from agentteams import wasm_guests as G
from tests._wasm_helpers import PROBE, dev_wasmtime, needs_wasmtime, project

_DEV_GLOB = "~/.vscode/extensions/ms-python.vscode-pylance-*/dist/wasi/python-3.14.6"


def dev_python_dir() -> str | None:
    """The dev CPython WASI dir (``python.wasm`` + ``lib/``), or None."""
    cands = [os.environ.get("AGENTTEAMS_WASM_DEV_PYTHON") or "", *sorted(glob.glob(os.path.expanduser(_DEV_GLOB)))]
    for cand in cands:
        if cand and os.path.isfile(os.path.join(cand, "python.wasm")) and os.path.isdir(os.path.join(cand, "lib")):
            return os.path.realpath(cand)
    return None


def _pinned_status(component: str) -> bool:
    root = os.environ.get("AGENTTEAMS_WASM_LIVE_PROJECT")
    if not root:
        return False
    from agentteams import wasm_runtime
    return wasm_runtime.status(Path(root)).get(component, "").startswith("ok") and \
        wasm_runtime.status(Path(root)).get("wasmtime", "").startswith("ok")


needs_dev_python = pytest.mark.skipif(dev_python_dir() is None, reason="no dev CPython WASI build "
                                      "(set AGENTTEAMS_WASM_DEV_PYTHON)")
needs_pinned_python = pytest.mark.skipif(not _pinned_status("python"), reason="pinned CPython WASI not installed "
                                         "(set AGENTTEAMS_WASM_LIVE_PROJECT after --wasm-install)")
needs_js_guest = pytest.mark.skipif(not _pinned_status("javascript"), reason="QuickJS-ng is not pinned/installed "
                                    "here (release downloads blocked; P04 step 7)")
needs_nesting = pytest.mark.skipif(SP.available() is None, reason="no nestable OS sandbox here (Seatbelt cannot "
                                   "nest inside a sandboxed session)")

pytestmark = needs_wasmtime


class DevResolver:
    """Test-only: dev wasmtime + the dev CPython build; ``runtime_mounts`` mirrors the pins' ``lib`` -> ``/lib``."""

    def __init__(self) -> None:
        self.wasmtime, self.py = dev_wasmtime(), dev_python_dir()

    def resolve(self, component: str) -> Path:
        assert component in ("wasmtime", "python"), component
        return Path(self.wasmtime if component == "wasmtime" else os.path.join(self.py, "python.wasm"))

    def resolve_tree(self, component: str) -> Path:
        return Path(self.py)

    def runtime_mounts(self, component: str) -> tuple[tuple[Path, str], ...]:
        return ((Path(self.py) / "lib", "/lib"),) if component == "python" else ()

    def pinned_version(self, component: str) -> str:
        return "dev"


def _resolvers() -> list[object]:
    out: list[object] = []
    if dev_python_dir():
        out.append(pytest.param("dev", id="dev-python"))
    out.append(pytest.param("pinned", id="pinned-python", marks=needs_pinned_python))
    return out


def _resolver(kind: str) -> object:
    if kind == "dev":
        return DevResolver()
    from agentteams import wasm_runtime
    return wasm_runtime.Resolver(Path(os.environ["AGENTTEAMS_WASM_LIVE_PROJECT"]))


def _py(root: Path, source: str, *, kind: str = "dev", grant: W.Grant = W.COMPUTE_ONLY, **kw: object) -> W.WasmResult:
    return G.run_code("python", source, None, grant=grant, project_root=root, resolver=_resolver(kind),
                      executor=W, profiles=SP, **kw)  # type: ignore[arg-type]


def _lib_sha(lib: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in lib.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(lib)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


# --- python ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", _resolvers())
def test_python_hello(tmp_path: Path, kind: str) -> None:
    res = _py(project(tmp_path), "import sys\nprint('hello', sys.prefix, sys.flags.isolated, sys.flags.no_site)",
              kind=kind)
    assert (res.exit_code, res.stdout, res.trap) == (0, b"hello / 1 1\n", None)
    assert res.os_confinement in ("none", "nested") and res.read_only_mode in ("snapshot", "os")


@needs_dev_python
def test_python_reads_stdin_data(tmp_path: Path) -> None:
    res = _py(project(tmp_path), "import sys\ndata = sys.stdin.buffer.read()\nprint(len(data), data[::-1])",
              stdin=b"print('not code')")
    assert res.stdout == b"17 b\")'edoc ton'(tnirp\"\n"  # stdin is data; the source came from /src


@needs_dev_python
def test_python_args(tmp_path: Path) -> None:
    res = G.run_code("python", "import sys\nprint(sys.argv)", None, args=["a b", "--x", "-c"], grant=W.COMPUTE_ONLY,
                     project_root=project(tmp_path), resolver=DevResolver(), executor=W, profiles=SP)
    assert res.stdout == b"['/src/main.py', 'a b', '--x', '-c']\n"


@pytest.mark.parametrize("kind", _resolvers())
def test_python_deny_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    monkeypatch.setenv("AGENTTEAMS_WASM_CANARY", "parent-env-canary")
    rep = G.probe_language("python", project(tmp_path), resolver=_resolver(kind), executor=W,  # type: ignore
                           profiles=SP)
    assert rep.ok, rep.checks
    assert [c[0] for c in rep.checks] == ["deny", "control", "fuel", "memory", "runtime-unchanged"]
    assert "parent-env-canary" not in repr(rep.checks)


@needs_dev_python
def test_python_probe_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_WASM_CANARY", "parent-env-canary")
    res = _py(project(tmp_path), G._probe_source("python").decode(), args=["deny"])  # type: ignore[call-arg]
    rows = G.parse_probe(res.stdout)
    assert rows is not None and res.exit_code == 0 and b"parent-env-canary" not in res.stdout + res.stderr
    assert {n: rows[n] for n in G.PROBE_DENY if rows[n] not in ("DENIED", "ABSENT")} == {}
    assert rows["fs-write-runtime"] in ("DENIED", "ALLOWED") and rows["stdio"] == "ALLOWED"
    assert rows["clock"] == rows["random"] == "ALLOWED"  # informational (Rule-12 carve-out, documented)
    assert b"PROBE-STDERR" in res.stderr


@needs_dev_python
def test_python_liveness_control(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "data" / "c.txt").write_text("visible-token\n")
    grant = W.Grant((W.Mount(str(root / "data"), "/project/data", "ro"), W.Mount(W.RUN_SCRATCH, "/scratch", "rw")),
                    W.DEFAULT_LIMITS)
    res = _py(root, "print(open('/project/data/c.txt').read().strip())\nopen('/scratch/x','w').write('1')\n"
                    "print(open('/scratch/x').read())", grant=grant)
    assert res.stdout == b"visible-token\n1\n" and res.exit_code == 0
    assert os.listdir(root / ".agentteams" / "wasm" / "exec") == []  # the one-off scratch died with the run


@needs_dev_python
def test_python_stdlib_and_src_write_never_changes_host(tmp_path: Path) -> None:
    root, lib = project(tmp_path), Path(dev_python_dir()) / "lib"  # type: ignore[arg-type]
    before = _lib_sha(lib)
    src = ("import glob\nfor p in glob.glob('/lib/python3.*/os.py') + ['/src/main.py', '/src/new.py']:\n"
           "    try:\n        open(p, 'a').write('# x')\n        print('WROTE', p)\n"
           "    except OSError as e:\n        print('DENIED', p, type(e).__name__)\n")
    res = _py(root, src, os_confine="off")
    assert res.exit_code == 0 and b"WROTE" not in res.stdout and res.stdout.count(b"DENIED") == 3
    assert res.read_only_mode == "snapshot" and _lib_sha(lib) == before


@needs_dev_python
@needs_nesting
def test_python_stdlib_write_never_changes_host_nested(tmp_path: Path) -> None:
    lib = Path(dev_python_dir()) / "lib"  # type: ignore[arg-type]
    before = _lib_sha(lib)
    res = _py(project(tmp_path), "import glob\nopen(glob.glob('/lib/python3.*/os.py')[0], 'a').write('#')",
              os_confine="require")
    assert res.os_confinement == "nested" and res.read_only_mode == "os" and res.exit_code != 0
    assert _lib_sha(lib) == before


@needs_dev_python
def test_python_fuel_and_memory_traps(tmp_path: Path) -> None:
    root = project(tmp_path)
    res = _py(root, "while True:\n    pass\n", grant=W.Grant((), W.Limits(2_000_000_000, 64 << 20, 60)))
    assert res.trap == "fuel" and res.exit_code != 0
    res = _py(root, "x = bytearray(200 << 20)\nprint('ALLOCATED')", grant=W.Grant((), W.Limits(10**10, 64 << 20, 60)))
    assert res.exit_code != 0 and b"ALLOCATED" not in res.stdout
    assert res.trap == "memory" or b"MemoryError" in res.stderr


@needs_dev_python
def test_python_concurrent_runs_isolated_snapshots(tmp_path: Path) -> None:
    root = project(tmp_path)
    results: dict[int, W.WasmResult] = {}

    def one(i: int) -> None:
        results[i] = _py(root, f"import os\nprint({i}, sorted(os.listdir('/lib')))")

    threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert sorted(results) == [0, 1, 2]
    for i, res in results.items():
        assert res.exit_code == 0 and res.stdout.startswith(f"{i} ".encode()), res.stderr[-300:]
    assert os.listdir(root / ".agentteams" / "wasm" / "exec") == []


# --- module ------------------------------------------------------------------------------------------------------

def test_module_runs_binary_probe(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "mods").mkdir()
    (root / "mods" / "probe.wasm").write_bytes(PROBE.read_bytes())
    res = G.run_code("module", None, "mods/probe.wasm", args=["deny"], grant=W.COMPUTE_ONLY, project_root=root,
                     resolver=DevResolver(), executor=W, profiles=SP)
    assert (res.exit_code, res.stdout) == (0, b"PREOPEN0\nENV0\nSOCK0\n")


def test_module_deny_probe(tmp_path: Path) -> None:
    rep = G.probe_language("module", project(tmp_path), resolver=DevResolver(), executor=W, profiles=SP)
    assert rep.ok, rep.checks


def test_module_wat_refused(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "m.wat").write_text("(module)")
    with pytest.raises(G.WasmGuestError, match="core module"):
        G.run_code("module", None, "m.wat", grant=W.COMPUTE_ONLY, project_root=root, resolver=DevResolver(),
                   executor=W, profiles=SP)


# --- javascript (skips until QuickJS-ng is pinned and installed: P04 step 7) ------------------------------------

def _js(root: Path, source: str, **kw: object) -> W.WasmResult:
    from agentteams import wasm_runtime
    return G.run_code("javascript", source, None, project_root=root, executor=W, profiles=SP,
                      resolver=wasm_runtime.Resolver(Path(os.environ["AGENTTEAMS_WASM_LIVE_PROJECT"])),
                      **{"grant": W.COMPUTE_ONLY, **kw})  # type: ignore[arg-type]


@needs_js_guest
def test_js_hello(tmp_path: Path) -> None:
    assert _js(project(tmp_path), "console.log('hello')").stdout == b"hello\n"


@needs_js_guest
def test_js_deny_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams import wasm_runtime
    monkeypatch.setenv("AGENTTEAMS_WASM_CANARY", "parent-env-canary")
    rep = G.probe_language("javascript", project(tmp_path), executor=W, profiles=SP,
                           resolver=wasm_runtime.Resolver(Path(os.environ["AGENTTEAMS_WASM_LIVE_PROJECT"])))
    assert rep.ok, rep.checks  # includes the liveness control


@needs_js_guest
def test_js_fuel_trap(tmp_path: Path) -> None:
    res = _js(project(tmp_path), "for(;;){}", grant=W.Grant((), W.Limits(1_000_000_000, 64 << 20, 60)))
    assert res.trap == "fuel"
