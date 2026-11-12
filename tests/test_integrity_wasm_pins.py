"""test_integrity_wasm_pins.py — the WASM / OS-sandbox program's pin set is complete, and provably so (P16).

The sandbox-switch / WASM program (base ``43f435f4``) added the executor, its bundle, the runtime pins, the guest
host, the MCP server and the switches that turn each boundary on and off. ``agentteams/integrity.py`` pins each of
them, so a silent edit trips ``--verify-integrity``. Pinning is only worth anything if the set stays whole:

* every file the program made security-relevant is listed below **by name**, so removing one from
  ``ENFORCEMENT_MODULES`` (an unpin) fails here, not silently;
* a glob over the program's naming conventions catches a new WASM / sandbox module nobody reported;
* the files the METAPLAN superseded (§6 R2 / R9) must not exist, so a stale plan cannot ship an unpinned copy;
* the committed manifest verifies, and the neutral launcher and the macOS deny-test gate are byte-identical to the
  program's base (``tests/test_mac_sandbox_augment.py`` keeps its own literal pin).

The program's other edits under ``agentteams/`` (CLI parsing and dispatch, the render pipeline, adapters that only
call the pinned modules, schemas, templates) are recorded as not pinned in the P16 plan's Implementation notes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentteams import integrity, wasm_mcp

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every module and data file the program added or made security-relevant, with the owning plan. A dict rather than
#: a set so an entry cannot be added without saying whose it is.
SECURITY_RELEVANT: dict[str, str] = {
    # The switches, their effects and the relaxation gate (P00, P01, P15).
    "agentteams/sandbox_state.py": "P15/P00/P01: every boundary decision reads it",
    "agentteams/sandbox_tokens.py": "P15: the grant-token spelling sandbox_effects re-exports",
    "agentteams/cli/sandbox_effects.py": "P15: the six sandbox-relaxation effect kinds",
    "agentteams/cli/sandbox_relaxation_gate.py": "P15: the Ed25519 chokepoint for sandbox relaxation",
    "agentteams/cli/os_sandbox_switch.py": "P00: when the relaxation gate engages",
    "agentteams/cli/wasm_sandbox_switch.py": "P01: --wasm-sandbox and the WASM half's validation",
    "agentteams/wasm_config.py": "P01: the wasm_sandbox validator (read-path bans, scratch, eligibility)",
    "agentteams/host_features.py": "P00 (P16 pin): default profile, *:sandbox expansion, the off-strip",
    # Control-plane protection and drift (P00, METAPLAN R1).
    "agentteams/frameworks/_control_plane_protect.py": "P00/R1: the .agentteams control-plane write protection",
    "agentteams/frameworks/_sandbox_drift.py": "P00: --check-wiring drift checks",
    # The executor and its bundle (P02, P03, P04).
    "agentteams/wasm_exec.py": "P02: the one place wasmtime starts; GRANT_SPEC",
    "agentteams/_sandbox_profiles.py": "P02: Seatbelt/bwrap builders (bundle file)",
    "agentteams/data/wasm_deny_probe.wasm": "P02 (P16 pin): the core-module deny probe",
    "agentteams/wasm_pins.py": "P03/R10: the one hash check every resolve goes through",
    "agentteams/wasm_runtime.py": "P03: download, extraction, verify-on-use",
    "agentteams/_wasm_archive.py": "P03: the safe extractor",
    "agentteams/_wasm_pin_update.py": "P03: the pin-update gate",
    "agentteams/cli/wasm_commands.py": "P03: the only caller of install / pin_update",
    "agentteams/data/wasm_runtime_pins.json": "P03: trust anchor of every WASM resolve",
    "agentteams/wasm_guests.py": "P04: language dispatch (bundle file)",
    "agentteams/data/wasm_probes/probe.py": "P04: Python deny probe",
    "agentteams/data/wasm_probes/probe.mjs": "P04: JavaScript deny probe",
    # The agent host, model providers and the guest (P05, P06, P07).
    "agentteams/wasm_agent/__init__.py": "P05",
    "agentteams/wasm_agent/broker.py": "P05: the grant-holding preparer",
    "agentteams/wasm_agent/budget.py": "P05: budget caps",
    "agentteams/wasm_agent/host.py": "P05: toolset / pending-id checks",
    "agentteams/wasm_agent/launch.py": "P05: key pipe, env scrub, host profile",
    "agentteams/wasm_agent/mediation.py": "P05: DLP mediation and the reserved-key vault",
    "agentteams/wasm_agent/protocol.py": "P05: closed frames and size caps",
    "agentteams/wasm_agent/stage1.py": "P05: the Stage-1 bootstrap",
    "agentteams/wasm_agent/tools.py": "P05: the guest toolset",
    "agentteams/wasm_agent/transcript.py": "P05/R4: the Stage-0 transcript chain",
    "agentteams/model_providers/__init__.py": "P06: key custody and egress",
    "agentteams/model_providers/_anthropic.py": "P06",
    "agentteams/model_providers/_common.py": "P06",
    "agentteams/model_providers/_config.py": "P06",
    "agentteams/model_providers/_gemini.py": "P06",
    "agentteams/model_providers/_openai.py": "P06",
    "agentteams/model_providers/_stub.py": "P06",
    "agentteams/model_providers/_transport.py": "P06: the vetted base URL the key travels to",
    "agentteams/model_providers/_validate.py": "P06: request / reply validation",
    "agentteams/data/wasm_agent_guest.py": "P07: the in-guest agent loop",
    "agentteams/wasm_agent/guest_source.py": "P07: the guest's sha pin and verified loader",
    # --framework wasm and delegation (P08, P09).
    "agentteams/frameworks/wasm.py": "P08: the adapter",
    "agentteams/frameworks/_wasm_tool_scoping.py": "P08: fail-closed tool translation",
    "agentteams/frameworks/_wasm_team_docs.py": "P08: the team docs carrying the grants",
    "agentteams/wasm_agent/team.py": "P08: lock writer and strict loader",
    "agentteams/cli/wasm_agent_commands.py": "P08: the launcher",
    "agentteams/wasm_agent/delegation.py": "P09/D11: sub-agent grant within the delegator's",
    "agentteams/wasm_agent/child_runs.py": "P09: the child-run service",
    # The MCP server and its wiring (P10, P11).
    "agentteams/wasm_mcp.py": "P10: server SHA256, BUNDLE_SHA256, launch flags",
    "agentteams/data/agentteams-wasm-mcp.py": "P10: the agentteams_wasm server",
    "agentteams/frameworks/_wasm_wiring.py": "P11: who gets the server; wasm_config_paths",
    "agentteams/codex_mcp_emit.py": "P11 (P16 pin): the first_party_tables carve-out",
    "agentteams/frameworks/goose_recipe_grants.py": "P11/P13 (P16 pin): recipe extensions, delegation carve-out",
    # The runner, enforcement and policy text (P12, P13, P14).
    "agentteams/runner_wasm.py": "P12: the runner's wasm jails",
    "agentteams/wasm_enforcement.py": "P13: native-exec narrowing and the enforcement verdict",
    "agentteams/frameworks/_codex_wasm_gate.py": "P13: when the Codex role gate runs in WASM mode",
    "agentteams/wasm_policy.py": "P14: the injected agent text and trigger rows",
    # The model-substrate projection (P20).
    "agentteams/sandbox_projection.py": "P20: writes .agentteams/sandbox-state.json",
    "agentteams/schemas/sandbox-state.schema.json": "P20: the projection's contract",
    # Already pinned before the program and edited by it (re-pinned, Inventory B).
    "agentteams/confinement.py": "P02/P12",
    "agentteams/proposal_run.py": "P12",
    "agentteams/proposal_runner.py": "P12",
    "agentteams/runner_mcp.py": "P10/P11",
    "agentteams/grok_config_emit.py": "P11",
    "agentteams/frameworks/_sandbox_emit.py": "P00",
    "agentteams/frameworks/_goose_sandbox_emit.py": "P00",
    "agentteams/frameworks/_codex_sandbox_emit.py": "P00",
    "agentteams/frameworks/_linux_sandbox_emit.py": "P00",
    "agentteams/frameworks/_grok_sandbox_emit.py": "P00",
    "agentteams/frameworks/_write_roots.py": "P00",
    "agentteams/cli/write_root_policy.py": "P00",
    "agentteams/frameworks/_codex_role_gate_emit.py": "P13",
    "agentteams/data/codex-role-gate.py": "P13",
    "agentteams/cli/effect_classifier.py": "P15",
    "agentteams/cli/exception_registry.py": "P15",
    "agentteams/cli/action_ids.py": "P15",
    "agentteams/cli/frozen_surfaces.py": "P15",
    "agentteams/cli/management_directives.py": "P15",
    "agentteams/cli/proposal_commands.py": "P12",
    "agentteams/atomicio.py": "P20 (replace_no_follow)",
    "agentteams/write_policy.py": "P00",
    "agentteams/vulnscan/gateway_dialects.py": "P06",
    "agentteams/integrity.py": "P16: the pin list itself",
}

#: Files the glob below matches that are deliberately not pinned, each with why (a Rule-12 exclusion: what it permits
#: is an unreported edit to that file, so each reason says why that edit weakens no boundary).
NOT_PINNED: dict[str, str] = {
    "agentteams/sandbox_handoff.py": "predates the program (not in 43f435f4..); renders operator handoff scripts the "
                                     "operator reviews and runs outside the sandbox; it grants and enforces nothing",
}

#: Files the METAPLAN superseded (§6 R2: no separate core module or nest file; R9: the grant spec is
#: ``wasm_exec.GRANT_SPEC``). Absence is asserted so a stale plan cannot ship an unpinned copy.
SUPERSEDED: tuple[str, ...] = (
    "agentteams/data/agentteams_wasm_core.py",
    "agentteams/data/wasm_grant_spec.json",
)
#: Superseded by name anywhere in the package.
SUPERSEDED_NAMES: tuple[str, ...] = ("wasm-nest.json", "agentteams_wasm_core.py", "wasm_grant_spec.json")

#: sha256 of the neutral launcher and the macOS deny-test gate at the program's base (43f435f4). The program
#: must leave both byte-identical, so their downstream pins (baseAgent's parity test, launcher_lineage) hold.
#: Re-pinned once outside the program: confine-run.sh on 2026-11-12 (cd992652, carve-out follow-ups: the exception
#: registry and the carve-out high-water mark protect-if-present; the downstream re-pin is coordinated outside this
#: repository, by the operator's infrastructure-management workspace).
_LAUNCHERS_AT_BASE: dict[str, str] = {
    "agentteams/templates/universal/sandbox/confine-run.sh":
        "e22910653d00a516773f0d6e72b256474ab58e2c32a88c56e2770b2c7f4727d7",
    "agentteams/templates/universal/sandbox/mac-escape-tests.sh":
        "369ca51f0388d18a12e2bd1503f4efec0a36f61e03d4820256b1307ca59295ed",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _program_like_files() -> set[str]:
    """Every package file named the way the program names its WASM / sandbox modules and data."""
    pkg = REPO_ROOT / "agentteams"
    hits: set[Path] = set()
    for pattern in ("wasm*.py", "_wasm*.py", "sandbox_*.py", "_sandbox_*.py"):
        hits.update(pkg.rglob(pattern))
    hits.update(p for p in (pkg / "wasm_agent").rglob("*") if p.is_file())
    hits.update(p for p in (pkg / "model_providers").rglob("*") if p.is_file())
    hits.update(p for p in (pkg / "data" / "wasm_probes").rglob("*") if p.is_file())
    hits.update((pkg / "data").glob("wasm*"))
    hits.update((pkg / "data").glob("*wasm*.py"))
    hits.update((pkg / "schemas").glob("sandbox*"))
    return {p.relative_to(REPO_ROOT).as_posix() for p in hits
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def test_every_security_relevant_file_is_pinned() -> None:
    """An unpin of any program file (or a rename that drops it) fails here."""
    pinned = set(integrity.ENFORCEMENT_MODULES)
    missing = {rel: owner for rel, owner in SECURITY_RELEVANT.items() if rel not in pinned}
    assert not missing, f"security-relevant files not in ENFORCEMENT_MODULES: {missing}"


def test_every_security_relevant_file_exists() -> None:
    """A listed path that moved verifies nothing; rename it here in the same change."""
    absent = sorted(rel for rel in SECURITY_RELEVANT if not (REPO_ROOT / rel).is_file())
    assert not absent, f"listed but not on disk: {absent}"


def test_no_unreported_wasm_or_sandbox_module() -> None:
    """A new WASM / sandbox file a plan forgot to report is caught by name, not by luck."""
    unreported = sorted(_program_like_files() - set(SECURITY_RELEVANT) - set(NOT_PINNED))
    assert not unreported, (
        f"WASM / sandbox files not classified in {Path(__file__).name}: {unreported}. Pin each in "
        "agentteams/integrity.py with a rationale and add it to SECURITY_RELEVANT, or explain the exclusion.")


def test_not_pinned_exclusions_are_live_and_unpinned() -> None:
    """A stale exclusion (file gone, or since pinned) is removed rather than left to excuse a future file."""
    for rel in NOT_PINNED:
        assert (REPO_ROOT / rel).is_file(), rel
        assert rel not in integrity.ENFORCEMENT_MODULES, f"{rel} is now pinned; move it to SECURITY_RELEVANT"


@pytest.mark.parametrize("rel", SUPERSEDED)
def test_superseded_file_does_not_exist(rel: str) -> None:
    assert not (REPO_ROOT / rel).exists(), f"{rel} was superseded by METAPLAN §6 and must not ship"


def test_superseded_names_appear_nowhere_in_the_package() -> None:
    found = sorted(p.relative_to(REPO_ROOT).as_posix() for name in SUPERSEDED_NAMES
                   for p in (REPO_ROOT / "agentteams").rglob(name))
    assert not found, f"superseded files present: {found}"


def test_every_bundle_source_is_pinned_and_matches_its_constant() -> None:
    """The bundle wasm_mcp installs is the pinned package bytes, and the constants describe them."""
    pinned = set(integrity.ENFORCEMENT_MODULES)
    for installed, source in wasm_mcp.BUNDLE:
        rel = f"agentteams/{source}"
        assert rel in pinned, rel
        want = (wasm_mcp.pins_sha256() if installed == wasm_mcp.PINS_INSTALLED
                else wasm_mcp.BUNDLE_SHA256[installed])
        assert _sha(REPO_ROOT / rel) == want, rel
    assert _sha(REPO_ROOT / "agentteams/data/agentteams-wasm-mcp.py") == wasm_mcp.SHA256


def test_the_committed_manifest_verifies() -> None:
    findings = integrity.verify(REPO_ROOT)
    assert not findings, "enforcement manifest does not verify (regenerate with " \
        "--write-integrity-manifest and explain each digest change): " + "; ".join(f.describe() for f in findings)


@pytest.mark.parametrize("rel", sorted(_LAUNCHERS_AT_BASE))
def test_launcher_bytes_unchanged_since_the_program_base(rel: str) -> None:
    assert _sha(REPO_ROOT / rel) == _LAUNCHERS_AT_BASE[rel], f"{rel} changed; the program must leave it byte-identical"
    assert rel in integrity.ENFORCEMENT_MODULES
