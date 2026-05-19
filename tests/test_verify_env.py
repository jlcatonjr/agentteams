"""Tests for scripts/verify-env.py preflight script."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "verify-env.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_env", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ve():
    return _load_module()


def test_check_python_ok_at_minimum(ve):
    result = ve._check_python(ve.MIN_PYTHON)
    assert result["ok"] is True
    assert result["name"] == "python"
    assert result["hint"] == ""


def test_check_python_ok_above_minimum(ve):
    bumped = (ve.MIN_PYTHON[0], ve.MIN_PYTHON[1] + 5)
    assert ve._check_python(bumped)["ok"] is True


def test_check_python_fail_below_minimum(ve):
    too_old = (ve.MIN_PYTHON[0], ve.MIN_PYTHON[1] - 1)
    result = ve._check_python(too_old)
    assert result["ok"] is False
    assert "Install Python" in result["hint"]
    assert str(ve.MIN_PYTHON[0]) in result["found"]


def test_check_git_ok_at_minimum(ve):
    result = ve._check_git(ve.MIN_GIT)
    assert result["ok"] is True
    assert result["hint"] == ""


def test_check_git_fail_below_minimum(ve):
    too_old = (ve.MIN_GIT[0], ve.MIN_GIT[1] - 1)
    result = ve._check_git(too_old)
    assert result["ok"] is False
    assert "Upgrade git" in result["hint"]


def test_check_git_missing(ve):
    result = ve._check_git(None)
    assert result["ok"] is False
    assert "git not found" in result["hint"]
    assert result["found"] == "not detected"


def test_detect_git_version_parses_standard_output(ve, monkeypatch):
    class _Proc:
        stdout = "git version 2.42.1\n"

    def fake_run(*args, **kwargs):  # noqa: ARG001
        return _Proc()

    monkeypatch.setattr(ve.shutil, "which", lambda _: "/usr/bin/git")
    assert ve._detect_git_version(runner=fake_run) == (2, 42)


def test_detect_git_version_returns_none_when_absent(ve, monkeypatch):
    monkeypatch.setattr(ve.shutil, "which", lambda _: None)
    assert ve._detect_git_version() is None


def test_main_returns_zero_when_all_pass(ve, monkeypatch):
    monkeypatch.setattr(ve, "run_checks", lambda: [
        {"name": "python", "ok": True, "required": "3.11", "found": "3.12", "hint": ""},
        {"name": "git", "ok": True, "required": "2.23", "found": "2.42", "hint": ""},
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ve.main([])
    assert rc == 0
    assert "OK" in buf.getvalue()


def test_main_quiet_suppresses_success_output(ve, monkeypatch):
    monkeypatch.setattr(ve, "run_checks", lambda: [
        {"name": "python", "ok": True, "required": "3.11", "found": "3.12", "hint": ""},
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ve.main(["--quiet"])
    assert rc == 0
    assert buf.getvalue() == ""


def test_main_returns_one_on_failure_and_prints_hint(ve, monkeypatch):
    monkeypatch.setattr(ve, "run_checks", lambda: [
        {"name": "python", "ok": False, "required": "3.11", "found": "3.10",
         "hint": "Install Python >= 3.11"},
    ])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = ve.main([])
    assert rc == 1
    assert "FAIL" in err.getvalue()
    assert "Install Python" in err.getvalue()


def test_main_json_mode_emits_structured_report(ve, monkeypatch):
    monkeypatch.setattr(ve, "run_checks", lambda: [
        {"name": "python", "ok": True, "required": "3.11", "found": "3.12", "hint": ""},
        {"name": "git", "ok": False, "required": "2.23", "found": "2.10",
         "hint": "Upgrade git"},
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ve.main(["--json"])
    payload = json.loads(buf.getvalue())
    assert rc == 1
    assert payload["ok"] is False
    assert len(payload["checks"]) == 2
    assert payload["checks"][1]["name"] == "git"


def test_current_environment_passes(ve):
    """The repository's own environment must satisfy the preflight."""
    checks = ve.run_checks()
    assert all(c["ok"] for c in checks), checks
