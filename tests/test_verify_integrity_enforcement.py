"""test_verify_integrity_enforcement.py — --verify-integrity must heed the enforcement manifest.

Closes the 2026-08-13 remediation-log gap: the enforcement-integrity manifest
(``references/enforcement-integrity.json``) had no CLI verification path — the only
check lived inside the red-team battery, so the man page's "review the diff" guidance
had no command to run. ``_run_verify_integrity`` now re-checks the manifest whenever
one exists at the resolved root and fails on any mismatch.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil

import pytest

from agentteams import integrity
from agentteams.cli.commands import _run_verify_integrity

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The deployed constitutional-gate hook, pinned by the enforcement-integrity manifest. Absent
#: from a public-release / CI checkout, which strips the deployed .claude/ instance; without it
#: the scratch-root setup and live-tree verify below cannot reproduce a manifest-consistent tree.
_GATE_HOOK = REPO / ".claude" / "hooks" / "constitutional-gate.py"


def _args(root: pathlib.Path) -> argparse.Namespace:
    ns = argparse.Namespace(output=str(root), project=None, description=None)
    setattr(ns, "self", False)  # "self" collides with Namespace.__init__'s own parameter
    return ns


def _scratch_root_with_manifest(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal root carrying real enforcement modules + a freshly-written manifest."""
    root = tmp_path / "root"
    for rel in integrity.compute_digests(REPO):
        src = REPO / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    integrity.write_manifest(root)
    return root


@pytest.mark.skipif(not _GATE_HOOK.exists(), reason="deployed .claude/ hooks absent from this checkout (public release / CI)")
def test_matching_manifest_passes(tmp_path) -> None:
    root = _scratch_root_with_manifest(tmp_path)
    assert _run_verify_integrity(_args(root)) == 0


@pytest.mark.skipif(not _GATE_HOOK.exists(), reason="deployed .claude/ hooks absent from this checkout (public release / CI)")
def test_enforcement_module_mismatch_fails(tmp_path, capsys) -> None:
    """Tampering with a covered module after the manifest was recorded must exit 1."""
    root = _scratch_root_with_manifest(tmp_path)
    victim = root / next(iter(integrity.compute_digests(root)))
    victim.write_text(victim.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    assert _run_verify_integrity(_args(root)) == 1
    err = capsys.readouterr().err
    assert "ENFORCEMENT" in err and "MISMATCH" in capsys.readouterr().out + err


def test_absent_manifest_keeps_legacy_behavior(tmp_path) -> None:
    """No manifest, no build-log baseline: report cannot-verify, exit 0 (unchanged)."""
    root = tmp_path / "bare"
    root.mkdir()
    assert _run_verify_integrity(_args(root)) == 0


@pytest.mark.skipif(not _GATE_HOOK.exists(), reason="deployed .claude/ hooks absent from this checkout (public release / CI)")
def test_source_tree_enforcement_integrity_is_currently_consistent() -> None:
    """R5/D5: the shipped agentteams source must be manifest-consistent.

    This is the invariant the `--check` integrity gate enforces (fail-closed at CI): every
    ENFORCEMENT_MODULES entry is present in references/enforcement-integrity.json and its
    digest matches. A red result here means an enforcement module was edited without
    regenerating the manifest (`--write-integrity-manifest`) — the exact D5 trap.
    """
    from agentteams.cli.generate import _verify_enforcement_integrity

    findings = _verify_enforcement_integrity()
    assert findings == [], (
        "enforcement-integrity drift: "
        + "; ".join(f.describe() for f in findings)
        + " — regenerate with `agentteams --write-integrity-manifest`."
    )


# ---------------------------------------------------------------------------
# D-2 (2026-08-26): the privilege EMITTERS must be integrity-tracked, not just
# the gate. A silent edit to grant issuance/verification or the rank-conformance
# ceiling weakens the emitted boundary without tripping E4 unless covered here.
# ---------------------------------------------------------------------------

_PRIVILEGE_EMITTERS = (
    "agentteams/cli/grants.py",          # P2 capability-grant issue/verify + hash-chain
    "agentteams/rank_conformance.py",    # AP-2 tools:-vs-rank ceiling validator
)


def test_privilege_emitters_are_in_enforcement_modules() -> None:
    """Membership guard: removing an emitter from ENFORCEMENT_MODULES fails here."""
    missing = [m for m in _PRIVILEGE_EMITTERS if m not in integrity.ENFORCEMENT_MODULES]
    assert not missing, f"privilege emitter(s) dropped from ENFORCEMENT_MODULES: {missing}"


@pytest.mark.skipif(not _GATE_HOOK.exists(), reason="deployed .claude/ hooks absent from this checkout (public release / CI)")
def test_tampering_with_a_privilege_emitter_trips_verify(tmp_path) -> None:
    """Editing grants.py after the manifest is recorded must be detected (exit 1)."""
    root = _scratch_root_with_manifest(tmp_path)
    victim = root / "agentteams/cli/grants.py"
    assert victim.exists(), "grants.py not copied into the scratch root — is it tracked?"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    assert _run_verify_integrity(_args(root)) == 1


# ---------------------------------------------------------------------------
# D-1 (2026-08-26): the constitutional-gate HOOK must be integrity-tracked. The
# hook verifies scan.py against the manifest but nothing pinned the hook itself
# (it says so: "an attacker who can edit scan.py can edit the manifest and this
# file"). Pinning lets E4/verify() flag a neutered gate.
# ---------------------------------------------------------------------------

_GATE_HOOKS = (
    "agentteams/templates/universal/hooks/constitutional-gate.py",
    ".claude/hooks/constitutional-gate.py",
    ".github/hooks/constitutional-gate.py",
)


def test_constitutional_gate_hooks_are_in_enforcement_modules() -> None:
    """Membership guard: dropping a gate hook from ENFORCEMENT_MODULES fails here."""
    missing = [h for h in _GATE_HOOKS if h not in integrity.ENFORCEMENT_MODULES]
    assert not missing, f"constitutional-gate hook(s) not integrity-tracked: {missing}"


@pytest.mark.skipif(not _GATE_HOOK.exists(), reason="deployed .claude/ hooks absent from this checkout (public release / CI)")
def test_tampering_with_the_gate_hook_trips_verify(tmp_path) -> None:
    """A silent flip of _FAIL_CLOSED_ON_ERROR / gutted _decide must be detected (exit 1)."""
    root = _scratch_root_with_manifest(tmp_path)
    victim = root / "agentteams/templates/universal/hooks/constitutional-gate.py"
    assert victim.exists(), "gate hook not copied into the scratch root — is it tracked?"
    victim.write_text(
        victim.read_text(encoding="utf-8").replace(
            "_FAIL_CLOSED_ON_ERROR = False", "_FAIL_CLOSED_ON_ERROR = True  # tampered"
        ),
        encoding="utf-8",
    )
    assert _run_verify_integrity(_args(root)) == 1


# ---------------------------------------------------------------------------
# D-2b (2026-08-26): the extracted sandbox EMITTER (_sandbox_emit.py) must be
# integrity-tracked — it emits allowWrite/denyRead/denyWrite; a silent edit
# (dropping denyWrite, widening allowWrite) weakens every emitted boundary.
# ---------------------------------------------------------------------------

def test_sandbox_emitter_is_in_enforcement_modules() -> None:
    assert "agentteams/frameworks/_sandbox_emit.py" in integrity.ENFORCEMENT_MODULES


@pytest.mark.skipif(not _GATE_HOOK.exists(), reason="deployed .claude/ hooks absent from this checkout (public release / CI)")
def test_tampering_with_the_sandbox_emitter_trips_verify(tmp_path) -> None:
    root = _scratch_root_with_manifest(tmp_path)
    victim = root / "agentteams/frameworks/_sandbox_emit.py"
    assert victim.exists(), "_sandbox_emit.py not copied into the scratch root — is it tracked?"
    victim.write_text(
        victim.read_text(encoding="utf-8").replace(
            '"references/agent-privilege.json",', ""  # simulate dropping the switch protection
        ),
        encoding="utf-8",
    )
    assert _run_verify_integrity(_args(root)) == 1
