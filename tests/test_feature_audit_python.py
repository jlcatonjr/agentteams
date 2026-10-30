"""Interpreter and import-root pins for running the suite outside the main checkout.

research-hardening #14: ``feature_audit.run_proof`` built ``["python", "-m", "pytest", ...]``, so with
another project's venv first on PATH every proof ran under that interpreter — against the wrong
``agentteams``, or none. It must use ``sys.executable``.

research-hardening #13: the CI venv carries an editable install of the main checkout, so pytest run in
a worktree silently imported the main checkout's ``agentteams``. ``tests/conftest.py`` now puts this
tree first on ``sys.path`` and ``PYTHONPATH`` and checks the import root at session start; the
subprocess half is pinned here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import feature_audit as fa

_ROOT = Path(__file__).resolve().parents[1]


def _row(proof_test: str = "tests/test_x.py::test_y") -> fa.FeatureRow:
    return fa.FeatureRow(
        feature_id="F-TEST", category="cat", name="name", surface="cli", kind=fa.KIND_FEATURE,
        tier=fa.TIER_UNIT, external_dep="", proof_test=proof_test, negative_control="",
        status="", notes="",
    )


def test_run_proof_uses_the_running_interpreter(monkeypatch) -> None:
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="1 passed in 0.01s\n", stderr="")

    monkeypatch.setattr(fa.subprocess, "run", fake_run)
    result = fa.run_proof(_row(), _ROOT)
    assert seen["cmd"][0] == sys.executable
    assert seen["cmd"][1:4] == ["-m", "pytest", "tests/test_x.py::test_y"]
    assert result.outcome == fa.PASS


def test_run_proof_reports_missing_pytest_as_harness_broken(monkeypatch) -> None:
    def no_spawn(*args, **kwargs):
        raise AssertionError("must not spawn a proof when pytest is absent")

    real_find_spec = fa.importlib.util.find_spec
    monkeypatch.setattr(
        fa.importlib.util, "find_spec",
        lambda name, *a: None if name == "pytest" else real_find_spec(name, *a),
    )
    monkeypatch.setattr(fa.subprocess, "run", no_spawn)
    result = fa.run_proof(_row(), _ROOT)
    assert result.outcome == fa.HARNESS_BROKEN_OUTCOME
    assert sys.executable in result.detail
    assert "pytest is not installed" in result.detail
    assert "research" not in result.detail


@pytest.mark.skipif(
    os.environ.get("AGENTTEAMS_TEST_INSTALLED") == "1",
    reason="installed-wheel run: conftest deliberately leaves PYTHONPATH alone",
)
def test_subprocesses_inherit_this_tree_on_pythonpath() -> None:
    first = os.environ.get("PYTHONPATH", "").split(os.pathsep)[0]
    assert first and Path(first).resolve() == _ROOT
    proc = subprocess.run(
        [sys.executable, "-c", "import agentteams; print(agentteams.__file__)"],
        cwd=_ROOT.parent, capture_output=True, text=True, timeout=60, check=True,
    )
    assert Path(proc.stdout.strip()).resolve().is_relative_to(_ROOT)
