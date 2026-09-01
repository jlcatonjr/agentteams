"""CC-7: privilege regression probe.

A single guard that fails when a privilege_profile emits an EMPTY or UNENFORCED
boundary — the silent-downgrade failure mode that P1-2, CC-6, P2-2, and P3-x each close
individually. It pins those fixes against regression: if a future change makes
``confined``/``exclusive`` resolve to a sandbox block that does not actually confine (no
``allowWrite``, the unsandboxed-command escape hatch left open, ``enabled`` flipped off,
or ``exclusive`` emitting no ``denyRead``), or lets an unknown profile or an unenforceable
host slip through, this probe catches it.

These are invariants a human reviewer would otherwise have to re-derive by hand on every
touch of the sandbox emitter; encoding them here makes the boundary's non-emptiness a
build-time contract.
"""

from __future__ import annotations

import json

import pytest

from agentteams import analyze
from agentteams.cli.artifacts import (
    PrivilegeConfinementError,
    resolve_host_features_and_advise,
)
from agentteams.frameworks.claude import ClaudeAdapter


def _sandbox_block(profile: str) -> dict:
    """Emit the Claude sandbox block for a profile on the sandbox-capable host."""
    manifest = {"host_features": ["claude:sandbox"], "privilege_profile": profile}
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    return json.loads(files["../settings.hooks.example.json"])["sandbox"]


@pytest.mark.parametrize("profile", ["confined", "exclusive"])
def test_confining_profile_emits_a_real_boundary(profile: str):
    # A confining profile must emit an ENFORCING sandbox block, never an empty/no-op one.
    sandbox = _sandbox_block(profile)
    assert sandbox.get("enabled") is True, f"{profile}: sandbox not enabled"
    assert sandbox.get("allowUnsandboxedCommands") is False, (
        f"{profile}: unsandboxed-command escape hatch left open — boundary is porous"
    )
    fs = sandbox.get("filesystem") or {}
    assert fs.get("allowWrite"), f"{profile}: empty allowWrite — nothing is confined"


def test_exclusive_emits_nonempty_read_exclusion():
    # exclusive must actually deny reads; an empty denyRead is a silent downgrade to confined.
    fs = _sandbox_block("exclusive")["filesystem"]
    assert fs.get("denyRead"), "exclusive: empty denyRead — read-exclusion silently no-ops"
    # allowRead must re-open the write roots (P2xP3), else granted paths become unreadable.
    assert fs.get("allowRead") == fs.get("allowWrite"), (
        "exclusive: allowRead must equal allowWrite so granted/write paths stay readable"
    )


def test_cooperative_emits_no_boundary():
    # The other direction: cooperative must NOT emit a sandbox block (no accidental confinement).
    manifest = {"host_features": [], "privilege_profile": "cooperative"}
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    settings = files.get("../settings.hooks.example.json")
    if settings is not None:
        assert "sandbox" not in json.loads(settings), (
            "cooperative unexpectedly emitted a sandbox block"
        )


def test_unknown_profile_fails_closed():
    # CC-6: a typo'd profile must hard-error at build, never downgrade to unconfined.
    with pytest.raises(ValueError, match="unknown privilege_profile"):
        analyze.build_manifest(
            {"project_goal": "x", "project_name": "T", "privilege_profile": "confind"},
            framework="claude",
        )


def test_unenforceable_host_fails_closed_by_default(monkeypatch):
    # P1-2: confined on a host with no OS sandbox must fail closed unless explicitly allowed.
    # Linux-neutral flip (2026-08-31): Linux is now enforceable framework-neutrally (the emitted
    # bwrap launcher wraps any process), so NO framework is a portable unenforceable example when
    # the suite runs on Linux. Windows pins the fail-closed invariant regardless of host: codex
    # has no boundary there, so a confined codex team on win32 must raise. (The per-platform
    # matrix is covered in test_workspace_privilege_scoping.)
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    manifest = {"privilege_profile": "confined"}
    with pytest.raises(PrivilegeConfinementError):
        resolve_host_features_and_advise(manifest, [], "codex", allow_unenforced=False)


def test_confined_on_capable_host_never_downgrades_even_fail_closed():
    # The capable host must ENFORCE (not error) under the fail-closed posture.
    manifest = {"privilege_profile": "confined"}
    resolve_host_features_and_advise(manifest, [], "claude", allow_unenforced=False)
    assert manifest["host_features"] == ["claude:sandbox"]
    assert not manifest.get("advisories")
