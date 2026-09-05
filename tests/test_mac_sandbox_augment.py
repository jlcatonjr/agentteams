"""Tests for the macOS sandbox augmentation of the framework-neutral confinement launcher.

The macOS augmentation (2026-W36, cross-orchestrator handoff from baseAgent) adds a real
``build_macos`` branch to ``sandbox/confine-run.sh`` (sandbox-exec + a generated Seatbelt profile,
RLIMIT_CPU/NPROC caps, a loopback-only proxy DNS contract, a non-exhaustive setuid-exec denylist)
plus an on-host deny test and two INERT Tier-B examples, emitted for EVERY framework on darwin.

These tests pin the contract that agentteams' own governance requires (@security + @adversarial,
2026-W36):

* darwin emission wires the launcher + ``mac-escape-tests.sh`` + the two inert examples into every
  framework (via ``base.extra_output_files``);
* the emitted launcher is byte-identical to the shipped template asset AND matches a LITERAL sha256
  pin — so a re-encode / NETNS rename / non-ASCII drift is caught (a pure ``output == asset``
  assertion would be a tautology; the literal pin is what actually catches drift);
* the launcher is ASCII-only (protects the sha pin);
* the @security-mandated hardening is present: a backslash (SBPL escape) is rejected before the
  profile is written, and a non-loopback ``--proxy`` FAILS CLOSED rather than widening egress;
* the honest residuals survive verbatim (memory UNCAPPED, denylist NOT exhaustive, no no-new-privs
  claim, ENFORCEMENT-UNVERIFIED until the on-host deny test passes);
* ``MAC_RESOURCE_CAPS`` describes Tier A/B/C honestly and emits NO syscall-filtering entry.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks._linux_sandbox_emit import (
    _LAUNCHER_ASSET_REL,
    macos_sandbox_output_files,
)
from agentteams.frameworks.agents_md import AgentsMdAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.codex import CodexAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.goose import GooseAdapter
from agentteams.host_features import MAC_RESOURCE_CAPS, is_sandbox_capable

# The canonical macOS-augmented launcher sha256. Handed back to baseAgent for its re-pin
# (tests/test_confine_run_parity.py). A LITERAL pin (not a recompute) so any drift — a NETNS
# rename, a UTF-8 re-encode, an accidental edit — fails loudly here.
EXPECTED_LAUNCHER_SHA256 = "743b90ca44a757886fa5af57287c2e47bf64a562ff78700411b38f00f829b059"

_TEMPLATES_UNIVERSAL = Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "universal"
_LAUNCHER_ASSET = _TEMPLATES_UNIVERSAL / _LAUNCHER_ASSET_REL

_CONFINED = {"privilege_profile": "confined"}

_ALL_ADAPTERS = [
    ClaudeAdapter,
    GooseAdapter,
    CodexAdapter,
    CopilotVSCodeAdapter,
    CopilotCLIAdapter,
    AgentsMdAdapter,
]

_EXPECTED_MACOS_BASENAMES = {
    "confine-run.sh",
    "mac-escape-tests.sh",
    "dedicated-uid-provisioning.example.sh",
    "pf-per-tenant-anchor.example.conf",
}


def _basenames(files: list[tuple[str, str]]) -> set[str]:
    return {path.rsplit("/", 1)[-1] for path, _ in files}


# ---------------------------------------------------------------------------
# Emission wiring — every framework, on darwin
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("Adapter", _ALL_ADAPTERS, ids=lambda a: a.__name__)
def test_every_framework_emits_macos_bundle_on_darwin(monkeypatch, Adapter):
    monkeypatch.setattr(sys, "platform", "darwin")
    files = Adapter().extra_output_files(dict(_CONFINED))
    got = _basenames(files)
    assert _EXPECTED_MACOS_BASENAMES <= got, f"{Adapter.__name__} missing: {_EXPECTED_MACOS_BASENAMES - got}"


def test_macos_bundle_lands_in_sandbox_dir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    files = macos_sandbox_output_files(dict(_CONFINED), platform="darwin")
    # All four artifacts share the launcher's sandbox/ directory.
    dirs = {path.rsplit("/", 1)[0] for path, _ in files}
    assert len(dirs) == 1, f"macOS bundle scattered across dirs: {dirs}"
    assert _basenames(files) == _EXPECTED_MACOS_BASENAMES


def test_macos_emitter_noop_off_darwin(monkeypatch):
    # Off darwin the macOS emitter is silent (Linux launcher is the linux emitter's job).
    assert macos_sandbox_output_files(dict(_CONFINED), platform="linux") == []
    assert macos_sandbox_output_files(dict(_CONFINED), platform="win32") == []


def test_macos_emitter_requires_confinement_request():
    # No confined/exclusive profile and no *:sandbox token → nothing emitted, even on darwin.
    assert macos_sandbox_output_files({}, platform="darwin") == []
    assert macos_sandbox_output_files({"privilege_profile": "cooperative"}, platform="darwin") == []


def test_darwin_is_sandbox_capable_framework_neutral():
    # The launcher emits for any framework on darwin, so capability is framework-neutral there.
    for fw in ("claude", "goose", "codex", "copilot-cli", "anything"):
        assert is_sandbox_capable(fw, "darwin") is True


# ---------------------------------------------------------------------------
# Byte-parity + sha pin + ASCII (drift guards)
# ---------------------------------------------------------------------------
def test_emitted_launcher_is_verbatim_template_bytes():
    files = macos_sandbox_output_files(dict(_CONFINED), platform="darwin")
    launcher_text = next(text for path, text in files if path.endswith("confine-run.sh"))
    assert launcher_text == _LAUNCHER_ASSET.read_text(encoding="utf-8")


def test_launcher_sha256_matches_literal_pin():
    # LITERAL pin (not a recompute of the same bytes) — catches NETNS/re-encode/ASCII drift.
    digest = hashlib.sha256(_LAUNCHER_ASSET.read_bytes()).hexdigest()
    assert digest == EXPECTED_LAUNCHER_SHA256


def test_launcher_is_ascii_only():
    # Non-ASCII would silently change the sha pin under a UTF-8 re-encode.
    _LAUNCHER_ASSET.read_bytes().decode("ascii")


# ---------------------------------------------------------------------------
# @security-mandated hardening present in the emitted launcher (OS-independent structural checks)
# ---------------------------------------------------------------------------
def test_launcher_rejects_backslash_before_profile_write():
    # @security 2026-W36 residual: a backslash escapes the (subpath "...") quote even with no
    # literal double-quote. reject_sbpl_meta must reject it.
    src = _LAUNCHER_ASSET.read_text(encoding="utf-8")
    assert "contains a backslash" in src
    assert "reject_sbpl_meta" in src
    # applied to $SCRATCH before the profile is written to "$sb".
    assert 'reject_sbpl_meta "$SCRATCH"' in src


def test_launcher_proxy_fails_closed_for_non_loopback_structurally():
    src = _LAUNCHER_ASSET.read_text(encoding="utf-8")
    # non-loopback proxy dies; only the loopback localhost form is ever emitted.
    assert "cannot pin egress to a remote proxy IP" in src
    assert 'remote ip \\"localhost:$PROXY_PORT\\"' in src
    # never widen to the real address:port form that fails at SBPL parse.
    assert 'remote ip \\"$PROXY_ADDR:$PROXY_PORT\\"' not in src


def test_launcher_carries_honest_residuals_verbatim():
    src = _LAUNCHER_ASSET.read_text(encoding="utf-8")
    assert "MEMORY UNCAPPED" in src  # Tier C
    assert "NOT exhaustive" in src or "NOT-EXHAUSTIVE" in src  # setuid denylist
    assert "ENFORCEMENT-UNVERIFIED" in src  # no green claim off an on-host deny test
    assert "no-new-privs" in src  # denylist is not NNP
    # No syscall filtering is emitted (operator decision 2026-W36). The tokens DO appear in the
    # header's honest "never emits ..." documentation, so check only ACTIVE (non-#-comment) lines.
    active = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "syscall" not in active
    assert "no-sandbox" not in active


# ---------------------------------------------------------------------------
# MAC_RESOURCE_CAPS honesty
# ---------------------------------------------------------------------------
def test_mac_resource_caps_tiers_and_no_syscall_field():
    assert set(MAC_RESOURCE_CAPS) == {"cpu_max", "nproc_max", "mem_max"}
    assert MAC_RESOURCE_CAPS["cpu_max"]["tier"] == "A"
    assert MAC_RESOURCE_CAPS["nproc_max"]["tier"] == "B"
    assert MAC_RESOURCE_CAPS["mem_max"]["tier"] == "C"
    # Tier C is honestly uncapped, not claimed enforced.
    assert MAC_RESOURCE_CAPS["mem_max"]["status"] == "uncapped"
    # No syscall-filtering cap exists (operator decision 2026-W36).
    assert not any("syscall" in k for k in MAC_RESOURCE_CAPS)


# ---------------------------------------------------------------------------
# Behavioral fail-closed — darwin-only (build_macos runs only on Darwin)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "darwin", reason="build_macos proxy validation runs only on macOS")
def test_non_loopback_proxy_exits_2_on_macos(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = subprocess.run(
        ["bash", str(_LAUNCHER_ASSET), "--scratch", str(scratch),
         "--egress", "proxy", "--proxy", "8.8.8.8:443", "--check", "--", "/bin/echo", "hi"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2, f"expected fail-closed exit 2, got {proc.returncode}: {proc.stderr}"
    assert "FAIL CLOSED" in proc.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="build_macos runs only on macOS")
def test_loopback_proxy_emits_localhost_form_on_macos(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = subprocess.run(
        ["bash", str(_LAUNCHER_ASSET), "--scratch", str(scratch),
         "--egress", "proxy", "--proxy", "127.0.0.1:8443", "--check", "--", "/bin/echo", "hi"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert 'localhost:8443' in proc.stdout
