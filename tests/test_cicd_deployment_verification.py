"""Regression tests: every generated team carries the post-merge CI/CD deployment
verification responsibility (git-operations invariant + the github-workflows-merge
reference procedure + the orchestrator's fenced Workflow-11 closeout step).

Asserts against the committed example snapshots (the canonical generative output, kept
in sync with the templates by tests/test_integration.py::test_snapshot_comparison)."""
from __future__ import annotations

from pathlib import Path

import pytest

_EXPECTED = Path("examples/software-project/expected")
pytestmark = pytest.mark.skipif(
    not (_EXPECTED / "git-operations.agent.md").exists(),
    reason="software-project example snapshot not present",
)


def _read(rel: str) -> str:
    return (_EXPECTED / rel).read_text(encoding="utf-8")


def test_git_operations_agent_has_cicd_deployment_invariant():
    body = _read("git-operations.agent.md")
    # The binding agent-level invariant (the "github agent" owner).
    assert "CI/CD deployment verification" in body
    assert "triggered" in body                     # post-merge triggered runs, not pre-merge gates
    assert "gh run" in body or "github-workflows-merge.reference" in body
    assert "not done" in body or "before reporting the operation complete" in body
    # Tooling preference: git + REST API are primary; gh is only a fallback.
    assert "GitHub REST API" in body
    assert "Prefer the `git` CLI" in body
    # Output contract surfaces a CI/CD status line.
    assert "CI/CD status" in body


def test_reference_has_post_merge_deployment_verification_section():
    ref = _read("references/github-workflows-merge.reference.md")
    assert "Post-Merge / Post-Push CI/CD Deployment Verification" in ref
    # Must be delineated from pre-merge required status checks (no blur).
    assert "Distinct from pre-merge required status checks" in ref
    # Prefer git + the REST API; gh is an explicit optional fallback (never a dependency).
    assert "prefer `git` and the REST API over `gh`" in ref
    assert "api.github.com/repos/" in ref                          # REST is the primary path
    assert "git rev-parse HEAD" in ref                             # git-first run discovery
    assert "`gh` fallback:" in ref                                 # gh demoted to fallback
    assert "gh run view <run-id> --log-failed" in ref             # the failure-debug fallback
    assert "gh run list" in ref


def test_orchestrator_closeout_gate_is_inside_a_fence():
    """The orchestrator tie-in lives in Workflow 11 (Part B), inside the
    `available_workflows` fence, so it propagates to existing teams on --update --merge."""
    orch = _read("orchestrator.agent.md")
    assert "CI/CD deployment verification (closeout gate)" in orch
    # It is conditional (must not be an unconditional network dependency).
    assert "Only when this session pushed or merged" in orch
    # And it sits inside the available_workflows fenced region.
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    gate = orch.index("CI/CD deployment verification (closeout gate)")
    assert begin < gate < end, "closeout gate must be inside the available_workflows fence"
