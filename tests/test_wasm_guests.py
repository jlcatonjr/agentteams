"""Pure tests for agentteams/wasm_guests.py (WASM P04): argv construction, refusals, injection seams, hygiene.

The real ``agentteams.wasm_exec`` is the injected executor; ``executor.run`` is monkeypatched where a run would
start, and the resolver is a fake serving a stand-in interpreter (a minimal core module) and a ``lib/`` tree.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from types import ModuleType

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from agentteams import wasm_guests as G

REPO = Path(__file__).resolve().parents[1]
_CORE = b"\0asm\x01\0\0\0"


class FakeGuestResolver:
    """Serves ``<base>/<component>/<entry>`` with a ``lib`` runtime mount for python."""

    def __init__(self, base: Path, *, error: Exception | None = None) -> None:
        self.base, self.error, self.calls = base, error, []
        for comp, entry in (("python", "python.wasm"), ("javascript", "qjs-wasi.wasm")):
            (base / comp).mkdir(parents=True, exist_ok=True)
            (base / comp / entry).write_bytes(_CORE)
        (base / "python" / "lib" / "python3.14").mkdir(parents=True, exist_ok=True)
        (base / "python" / "lib" / "python3.14" / "os.py").write_text("# os\n")

    def resolve(self, component: str) -> Path:
        self.calls.append(("resolve", component))
        if self.error:
            raise self.error
        return self.base / component / ("python.wasm" if component == "python" else "qjs-wasi.wasm")

    def resolve_tree(self, component: str) -> Path:
        return self.base / component

    def runtime_mounts(self, component: str) -> tuple[tuple[Path, str], ...]:
        self.calls.append(("runtime_mounts", component))
        return ((self.base / "python" / "lib", "/lib"),) if component == "python" else ()

    def pinned_version(self, component: str) -> str:
        return "0.0.0"


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, FakeGuestResolver]:
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "mods").mkdir(parents=True)
    (root / "mods" / "m.wasm").write_bytes(_CORE)
    return root, FakeGuestResolver(Path(os.path.realpath(tmp_path)) / "rt")


def _argv(root: Path, res: FakeGuestResolver, language: str, source: object = None, module_path: object = None,
          grant: W.Grant = W.COMPUTE_ONLY, **kw: object) -> tuple[W.Prepared, tuple[str, ...]]:
    return G.argv_for(language, source, module_path, grant=grant, project_root=root, resolver=res,  # type: ignore
                      executor=W, **kw)


def test_languages_constant() -> None:
    assert G.LANGUAGES == ("module", "python", "javascript")


@pytest.mark.parametrize("lang", ["ruby", "", "Python", None, "native", "shell"])
def test_unsupported_language_refuses(env: tuple[Path, FakeGuestResolver], lang: object) -> None:
    root, res = env
    with pytest.raises(G.WasmGuestError, match="supported: module, python, javascript"):
        _argv(root, res, lang, "print(1)")  # type: ignore[arg-type]
    assert res.calls == []


def test_source_and_module_path_exclusive(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    for lang, src, mod in (("module", "x", "mods/m.wasm"), ("module", None, None), ("python", None, None),
                           ("python", "x", "mods/m.wasm"), ("javascript", None, "mods/m.wasm")):
        with pytest.raises(G.WasmGuestError, match="takes"):
            _argv(root, res, lang, src, mod)


def test_python_argv_for_shape(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    prepared, args = _argv(root, res, "python", "print('hi')", args=["a", "--b"])
    assert prepared.module == str(res.base / "python" / "python.wasm")
    assert args == ("-I", "-S", "-B", "/src/main.py", "a", "--b")
    assert prepared.runtime_mounts == (W.Mount(str(res.base / "python" / "lib"), "/lib", "ro"),)
    assert prepared.src_files == (("main.py", b"print('hi')"),)
    assert ("runtime_mounts", "python") in res.calls  # guest names only from the resolver (pins)


def test_js_argv_for_script_vs_module(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    cases = {"console.log(1)": "main.js", "import * as std from 'qjs:std';\n": "main.mjs",
             "  export const x = 1;": "main.mjs", "const m = import('x');": "main.js",
             "// import x from 'y'\nconsole.log(2)": "main.js", "import{a}from'b'": "main.mjs"}
    for src, name in cases.items():
        prepared, args = _argv(root, res, "javascript", src, args=["z"])
        assert args == (f"/src/{name}", "z") and prepared.src_files[0][0] == name, src
        assert prepared.runtime_mounts == ()


def test_module_argv_for_no_runtime_mounts(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    for path in ("mods/m.wasm", root / "mods" / "m.wasm"):
        prepared, args = _argv(root, res, "module", None, path, args=["x"])
        assert prepared.module == str(root / "mods" / "m.wasm") and args == ("x",)
        assert prepared.runtime_mounts == () and prepared.src_files == ()
    assert res.calls == []  # a module never asks provisioning for an interpreter


def test_module_path_outside_project_or_protected_refused(env: tuple[Path, FakeGuestResolver], tmp_path: Path
                                                         ) -> None:
    root, res = env
    outside = Path(os.path.realpath(tmp_path)) / "out.wasm"
    outside.write_bytes(_CORE)
    for rel in (".agentteams/wasm/m.wasm", ".git/m.wasm", ".claude/m.wasm"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(_CORE)
    for path in (str(outside), "../out.wasm", ".agentteams/wasm/m.wasm", ".git/m.wasm", ".claude/m.wasm", ".",
                 "mods", "missing.wasm"):
        with pytest.raises(G.WasmGuestError):
            _argv(root, res, "module", None, path)


def test_module_path_symlink_refused(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    (root / "link.wasm").symlink_to(root / "mods" / "m.wasm")
    (root / "linkdir").symlink_to(root / "mods")
    for path in ("link.wasm", "linkdir/m.wasm"):
        with pytest.raises(G.WasmGuestError, match="symlink"):
            _argv(root, res, "module", None, path)


def test_module_wat_text_refused(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    (root / "mods" / "t.wat").write_text("(module)")
    with pytest.raises(G.WasmGuestError, match="core module"):
        _argv(root, res, "module", None, "mods/t.wat")


def test_source_too_large_refused(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    _argv(root, res, "python", "#" * G.MAX_SOURCE_BYTES)
    with pytest.raises(G.WasmGuestError, match="limit"):
        _argv(root, res, "python", "#" * (G.MAX_SOURCE_BYTES + 1))
    with pytest.raises(G.WasmGuestError, match="limit"):
        _argv(root, res, "javascript", "é" * (G.MAX_SOURCE_BYTES // 2 + 1))  # bytes, not characters


def test_source_not_utf8_refused(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    for bad in (b"\xff\xfe", "\udcff", 42):
        with pytest.raises(G.WasmGuestError):
            _argv(root, res, "python", bad)
    assert _argv(root, res, "python", "print('é')".encode())[0].src_files == (("main.py", "print('é')".encode()),)


def test_grant_never_widened_by_language(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    (root / "data").mkdir()
    grant = W.Grant((W.Mount(str(root / "data"), "/project/data", "ro"),), W.Limits(10**9, 1 << 26, 30))
    for lang, src, mod in (("python", "1", None), ("javascript", "1", None), ("module", None, "mods/m.wasm")):
        prepared, _ = _argv(root, res, lang, src, mod, grant=grant)
        assert prepared.grant is grant and prepared.grant.mounts == grant.mounts


def test_grant_must_be_executor_grant(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    with pytest.raises(G.WasmGuestError, match="Grant"):
        _argv(root, res, "python", "1", grant=("not", "a grant"))  # type: ignore[arg-type]


def test_runtime_mount_guest_names_still_checked(env: tuple[Path, FakeGuestResolver]) -> None:
    root, res = env
    res.runtime_mounts = lambda c: ((res.base / "python" / "lib", "/project/x"),)  # type: ignore[method-assign]
    with pytest.raises(G.WasmGuestError, match="runtime mount"):
        _argv(root, res, "python", "1")
    res.runtime_mounts = lambda c: ((res.base / "python" / "lib", "/"),)  # type: ignore[method-assign]
    with pytest.raises(G.WasmGuestError, match="runtime mount"):
        _argv(root, res, "python", "1")


def test_resolver_errors_become_wasm_guest_error(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    for exc in (RuntimeError("TODO-PIN"), OSError("gone"), LookupError("no pins")):
        with pytest.raises(G.WasmGuestError, match="could not resolve") as info:
            _argv(root, FakeGuestResolver(root / "rt", error=exc), "python", "1")
        assert info.value.__cause__ is exc


def test_executor_errors_become_wasm_guest_error(env: tuple[Path, FakeGuestResolver],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    root, res = env
    assert not issubclass(G.WasmGuestError, W.WasmExecError) and not issubclass(W.WasmExecError, G.WasmGuestError)

    def boom(*a: object, **k: object) -> None:
        raise W.WasmExecError("nope")

    monkeypatch.setattr(W, "run", boom)
    with pytest.raises(G.WasmGuestError, match="nope") as info:
        G.run_code("python", "1", None, grant=W.COMPUTE_ONLY, project_root=root, resolver=res, executor=W,
                   profiles=SP)
    assert isinstance(info.value.__cause__, W.WasmExecError)


def test_run_code_passes_everything_through(env: tuple[Path, FakeGuestResolver],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    root, res = env
    seen: dict[str, object] = {}

    def fake_run(prepared: W.Prepared, args: tuple[str, ...], **kw: object) -> str:
        seen.update(prepared=prepared, args=args, **kw)
        return "result"

    monkeypatch.setattr(W, "run", fake_run)
    out = G.run_code("python", "print(1)", None, args=["q"], stdin=b"data", grant=W.COMPUTE_ONLY, project_root=root,
                     os_confine="require", resolver=res, executor=W, profiles=SP, control_plane_protected=True,
                     stdout_cap=10, stderr_cap=20)
    assert out == "result" and seen["args"] == ("-I", "-S", "-B", "/src/main.py", "q")
    assert (seen["stdin"], seen["os_confine"], seen["resolver"], seen["profiles"]) == (b"data", "require", res, SP)
    assert (seen["control_plane_protected"], seen["stdout_cap"], seen["stderr_cap"]) == (True, 10, 20)


def test_limits_below_floor_notice_not_raised(env: tuple[Path, FakeGuestResolver], monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    root, res = env
    seen: list[W.Grant] = []
    monkeypatch.setattr(W, "run", lambda p, a, **k: seen.append(p.grant))
    low = W.Grant((), W.Limits(1_000_000, 1 << 20, 1))
    G.run_code("python", "1", None, grant=low, project_root=root, resolver=res, executor=W, profiles=SP)
    assert seen == [low]  # limits are never raised
    err = capsys.readouterr().err
    assert "below the measured python startup floor (fuel, max_memory_bytes, timeout_s)" in err
    assert G.limit_notices("python", W.DEFAULT_LIMITS) == []  # the default grant clears the floor
    assert G.limit_notices("module", low.limits) == [] and G.limit_notices("javascript", low.limits) == []


def test_floors_inside_limit_bounds() -> None:
    for fuel, mem, timeout in G.LANGUAGE_DEFAULT_LIMITS.values():
        W.Limits(fuel, mem, timeout)


def test_parse_probe() -> None:
    out = b"noise\nPROBE env DENIED LookupError\nPROBE clock ALLOWED 1.0\nPROBE x-y ABSENT\nPROBE bad MAYBE\n"
    assert G.parse_probe(out) == {"env": "DENIED", "clock": "ALLOWED", "x-y": "ABSENT"}
    assert G.parse_probe(b"PROBE env DENIED\nPROBE env ALLOWED\n") is None  # a forged repeat fails the probe


def test_probe_rows_cover_both_probe_scripts() -> None:
    for name in G.PROBE_FILES.values():
        text = (REPO / "agentteams" / "data" / "wasm_probes" / name).read_text(encoding="utf-8")
        for row in (*G.PROBE_DENY, *G.PROBE_INFO, "fs-write-runtime", "stdio"):
            assert re.search(rf"""attempt\(["']{row}["']|emit\(["']{row}["']""", text), (name, row)


def test_probe_language_refuses_bad_args(tmp_path: Path) -> None:
    with pytest.raises(G.WasmGuestError, match="supported"):
        G.probe_language("perl", tmp_path, resolver=None, executor=W, profiles=SP)  # type: ignore[arg-type]
    with pytest.raises(G.WasmGuestError, match="os_confine"):
        G.probe_language("python", tmp_path, resolver=None, executor=W, profiles=SP,  # type: ignore[arg-type]
                         os_confine="off")


def test_probe_language_reports_unresolvable_runtime(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    rep = G.probe_language("python", root, resolver=FakeGuestResolver(root / "rt", error=RuntimeError("TODO-PIN")),
                           executor=W, profiles=SP)
    assert not rep.ok and rep.checks[0][1] is False and "TODO-PIN" in rep.checks[0][2]
    assert [p for p in root.iterdir() if p.name.startswith(".agentteams-wasm-probe-")] == []  # canaries removed


# --- source hygiene (R2) -------------------------------------------------------------------------------------------

def test_no_intra_package_imports() -> None:
    for node in ast.walk(ast.parse(Path(G.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and not (node.module or "").startswith("agentteams"), node.module
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("agentteams") for a in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("__import__", "exec", "eval"), "dynamic import / exec"


def test_no_native_fallback() -> None:
    text = Path(G.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(subprocess|os\.system|os\.exec|os\.spawn|os\.popen|importlib)\b", text)


def test_dev_fixture_never_referenced_by_package() -> None:
    hits = []
    for path in (REPO / "agentteams").rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json", ".mjs", ".md") and "__pycache__" not in path.parts:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if "pylance" in text or "vscode/extensions" in text:
                hits.append(path.relative_to(REPO).as_posix())
    assert hits == []


def test_executor_is_injected_not_imported() -> None:
    assert not any(isinstance(v, ModuleType) and v.__name__.startswith("agentteams") for v in vars(G).values())
