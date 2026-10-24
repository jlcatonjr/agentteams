"""The docker probes never hang or raise (code-hygiene D6): a hung or missing client means "unreachable"."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_run
from tests._vulnscan_helpers import operator_env


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired(["docker"], 60), FileNotFoundError("docker")])
def test_a_hung_or_missing_docker_client_reads_as_unreachable(monkeypatch: pytest.MonkeyPatch,
                                                              failure: Exception) -> None:
    timeouts: list[object] = []

    def fake(argv: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
        timeouts.append(timeout)
        raise failure

    monkeypatch.setattr(agent_run, "_docker", fake)
    assert agent_run._image_id("img:tag") == ""
    assert "agent image img:tag is not built" in agent_run.docker_unreachable("img:tag")
    assert agent_run.docker_unreachable().startswith("docker is not reachable")
    assert timeouts and all(t == 60 for t in timeouts)        # never the 10-minute default


def test_selftest_ok_reports_unreachable_instead_of_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operator_env(tmp_path, monkeypatch)
    rec = {"passed": True, "image": "sha256:x", "flags_sha256": agent_run.flags_sha256(), "checks": {}}
    path = agent_run.paths.state_dir() / "agent-selftest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rec))

    def hung(argv: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(agent_run, "_docker", hung)
    ok, why = agent_run.selftest_ok({})
    assert not ok and "docker is not reachable" in why and why.endswith("then re-run agent-selftest")
