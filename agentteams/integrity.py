"""integrity.py — hash manifest over the modules that enforce the constitution.

**What this is for.** Every control this project relies on — the destructive-action gate, the
content scanner, the fence-restore engine, the capability merge — is a Python file sitting in
the same working tree the agents edit. A red-team audit on 2026-08-06 (probe E4) recorded that
as the umbrella finding: *the constitution's referee plays inside the same sandbox as the
players*. An agent that can write `security_gate.py` does not need to defeat the gate.

**What this is NOT.** A hash manifest stored beside the files it protects is a **speed bump,
not a boundary**. An attacker who can edit `scan.py` can edit `integrity.json`, and one who can
edit this module can make `verify()` return an empty list. This is stated here rather than
discovered later, because a control whose limits are undocumented gets trusted past them.

What it does buy, honestly enumerated:

* it detects *accidental* corruption and *unnoticed* drift, which is most real-world damage;
* it makes tampering a **two-step, recorded** act rather than a one-line edit — the attacker
  must also regenerate the manifest, and that regeneration shows up in `git diff`;
* it gives the harness-level hook (`.claude/hooks/constitutional-gate.py`) something concrete
  to check before it trusts the scanner it is about to call.

The durable fix is elsewhere and is named in the remediation plan: move enforcement into the
harness (where agents cannot write it) and keep signing keys outside the agent's environment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

#: Modules whose integrity the constitution depends on. Each is here because a silent edit to
#: it would disable or weaken a control rather than merely change behaviour.
ENFORCEMENT_MODULES: tuple[str, ...] = (
    "agentteams/cli/security_gate.py",   # C-2, C-5: the destructive-action gate
    "agentteams/scan.py",                # C-4: the deterministic content scanner
    "agentteams/fences.py",              # C-1: fence restore, template-authoritative set
    "agentteams/unfenced.py",            # C-1: constraint ratchet
    "agentteams/front_matter_merge.py",  # C-3: capability comparison
    "agentteams/front_matter_reconcile.py",
    # The standing red-team audit's phase-6 checks. These are controls, not reporters: a
    # silent edit to any of them turns a check that fires into one that cannot, which is the
    # F-1 defect applied to the machinery built to catch F-1. Registry is included because it
    # holds the evidence normalisation the intent check compares against — widen the
    # normaliser far enough and every probe's evidence digests to the same value.
    "agentteams/redteam/checks_static.py",
    "agentteams/redteam/checks_report.py",
    "agentteams/redteam/registry.py",
    "agentteams/integrity.py",           # self, so removing an entry is itself detectable
)

#: Where the manifest lives, relative to the repository root.
MANIFEST_REL_PATH = "references/enforcement-integrity.json"

_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class IntegrityFinding:
    """One enforcement module whose current digest does not match the manifest."""

    rel_path: str
    expected: str
    actual: str
    reason: str  # "modified" | "missing" | "unmanifested"

    def describe(self) -> str:
        return f"{self.rel_path}: {self.reason} (expected {self.expected[:12]}, got {self.actual[:12]})"


def _digest(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes, or "" when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def compute_digests(repo_root: Path) -> dict[str, str]:
    """Return ``{rel_path: sha256}`` for every enforcement module present under *repo_root*.

    Args:
        repo_root: Repository root.

    Returns:
        Mapping of relative path to hex digest. A module that does not exist is mapped to the
        empty string rather than omitted, so its disappearance is a mismatch and not a silent
        gap in coverage.
    """
    return {rel: _digest(repo_root / rel) for rel in ENFORCEMENT_MODULES}


def write_manifest(repo_root: Path) -> Path:
    """Write (or refresh) the integrity manifest and return its path.

    Regenerating is deliberately a separate, explicit act: it is what an operator does after an
    intentional change to a control, and what an attacker must additionally do after an
    unintentional one. Keeping it out of the normal build is the point — an auto-refreshed
    manifest verifies nothing.
    """
    manifest_path = repo_root / MANIFEST_REL_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _MANIFEST_VERSION,
        "note": (
            "SHA-256 of the modules that enforce C-1..C-5. Regenerate deliberately, with "
            "`agentteams --write-integrity-manifest`, only after an intended change to a "
            "control. See agentteams/integrity.py for what this does and does not guarantee."
        ),
        "modules": compute_digests(repo_root),
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def verify(repo_root: Path) -> list[IntegrityFinding]:
    """Compare enforcement modules against the manifest.

    Args:
        repo_root: Repository root.

    Returns:
        A finding per mismatch; empty when everything matches. **A missing manifest returns
        empty** — an unmanifested repository is not a tampered one, and treating "never set up"
        as "compromised" would make the check fire on every fresh clone until it got muted.
        Callers that require a manifest must check for the file themselves.
    """
    manifest_path = repo_root / MANIFEST_REL_PATH
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to read enforcement integrity manifest: {exc}") from exc

    recorded: dict[str, str] = payload.get("modules", {})
    actual = compute_digests(repo_root)

    findings: list[IntegrityFinding] = []
    for rel, expected in sorted(recorded.items()):
        got = actual.get(rel, "")
        if got == expected:
            continue
        reason = "missing" if not got else "modified"
        findings.append(IntegrityFinding(rel_path=rel, expected=expected, actual=got, reason=reason))
    # A module added to ENFORCEMENT_MODULES but absent from the manifest is unverified. Report
    # it rather than passing silently: the failure mode of the manifest is under-coverage.
    for rel, got in sorted(actual.items()):
        if rel not in recorded:
            findings.append(
                IntegrityFinding(rel_path=rel, expected="", actual=got, reason="unmanifested")
            )
    return findings


__all__ = [
    "ENFORCEMENT_MODULES",
    "MANIFEST_REL_PATH",
    "IntegrityFinding",
    "compute_digests",
    "write_manifest",
    "verify",
]
