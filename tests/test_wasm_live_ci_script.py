"""test_wasm_live_ci_script.py — the live-WASM CI skip guard must itself be trustworthy.

`scripts/wasm_live_ci.py skips` is what stops the `wasm-live` and `macos-seatbelt` jobs going green with every live
test skipped. These tests pin its failure modes: an unexplained skip fails, an allowlisted one passes, nodeids are
rebuilt from junit classnames (module and class level, with a fallback), an empty report fails, the per-sandbox
allowlists compose, every listed live file exists, and the R13 timing check fails a step that did not nest under the
expected sandbox (that check is the job's evidence of nesting).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "wasm_live_ci.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("wasm_live_ci", SCRIPT)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _junit(tmp_path: Path, cases: str) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    p = tmp_path / "junit.xml"
    p.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>')
    return p


_CASES = ('<testcase classname="tests.test_a" name="test_runs"/>'
          '<testcase classname="tests.test_a" name="test_seatbelt_only"><skipped message="needs Seatbelt"/></testcase>'
          '<testcase classname="tests.test_a.TestC" name="test_m[x-1]"><skipped message="no wasmtime"/></testcase>')


def test_unexplained_skip_fails_and_names_the_nodeid(mod, tmp_path, capsys):
    junit = _junit(tmp_path, _CASES)
    assert mod.check_skips(junit, ["tests/test_a.py::test_seatbelt_only"], tmp_path) == 1
    out = capsys.readouterr().out
    assert "unexplained skip: tests/test_a.py::TestC::test_m[x-1]" in out
    assert "skip (allowlisted): tests/test_a.py::test_seatbelt_only" in out


def test_every_skip_allowlisted_passes(mod, tmp_path, capsys):
    junit = _junit(tmp_path, _CASES)
    allow = ["tests/test_a.py::test_seatbelt_only", "tests/test_a.py::TestC::test_m[x-1]", "tests/test_a.py::gone"]
    assert mod.check_skips(junit, allow, tmp_path) == 0
    assert "allowlisted but not skipped (fine): tests/test_a.py::gone" in capsys.readouterr().out


def test_empty_report_fails(mod, tmp_path):
    assert mod.check_skips(_junit(tmp_path, ""), [], tmp_path) == 1


def test_nodeid_falls_back_to_classname_when_no_file_matches(mod, tmp_path):
    assert mod._nodeid("pkg.missing", "test_x", tmp_path) == "pkg.missing::test_x"


def test_sandbox_allowlist_composes_common_and_per_sandbox_entries(mod, tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_wasm_exec_nested.py").write_text("")
    junit = tmp_path / "junit.xml"
    junit.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest">'
                     '<testcase classname="tests.test_wasm_exec_nested" name="test_nested_network_denied">'
                     '<skipped message="needs Seatbelt"/></testcase></testsuite></testsuites>')
    args = ["skips", str(junit), "--root", str(tmp_path)]
    for sandbox in ("bwrap", "seatbelt"):  # the deny cases run under both now (row bwrap-nested-deny)
        assert mod.main([*args, "--sandbox", sandbox]) == 1
        assert mod.main([*args, "--sandbox", sandbox,
                         "--allow", "tests/test_wasm_exec_nested.py::test_nested_network_denied"]) == 0


def test_every_live_file_exists(mod):
    assert all((REPO_ROOT / f).is_file() for f in mod.LIVE_FILES)
    extras = [n for names in mod.SANDBOX_ALLOW.values() for n in names]
    assert all(n.split("::")[0] in mod.LIVE_FILES for n in (*mod.COMMON_ALLOW, *extras))


def test_env_lines_name_the_resolved_runtimes(mod, tmp_path, monkeypatch):
    from agentteams import wasm_runtime

    paths = {"wasmtime": tmp_path / "wt" / "wasmtime", "python": tmp_path / "py" / "python.wasm"}
    monkeypatch.setattr(wasm_runtime, "resolve", lambda root, component: paths[component])
    lines = mod.env_lines(tmp_path)
    assert f"AGENTTEAMS_WASM_DEV_WASMTIME={paths['wasmtime']}" in lines
    assert f"AGENTTEAMS_WASM_DEV_PYTHON={tmp_path / 'py'}" in lines
    assert "AGENTTEAMS_WASM_LIVE=1" in lines


class _Res:
    def __init__(self, os_confinement: str, os_reason: str) -> None:
        self.exit_code, self.stdout, self.stderr = 0, b'{"ok": 1}\n', b""
        self.os_confinement, self.os_reason = os_confinement, os_reason


@pytest.mark.parametrize("conf,reason,rc", [("nested", "nested:bwrap", 0), ("nested", "nested:seatbelt", 1),
                                            ("none", "unavailable:linux", 1)])
def test_timing_fails_a_step_not_nested_under_the_expected_sandbox(mod, tmp_path, monkeypatch, conf, reason, rc):
    from agentteams import wasm_guests, wasm_runtime

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(wasm_runtime, "Resolver", lambda root: object())
    monkeypatch.setattr(wasm_guests, "run_code", lambda *a, **k: _Res(conf, reason))
    assert mod.step_timing(tmp_path, "nested:bwrap", steps=2) == rc
