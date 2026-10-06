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
import os
import importlib.util
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Modules whose integrity the constitution depends on. Each is here because a silent edit to
#: it would disable or weaken a control rather than merely change behaviour.
ENFORCEMENT_MODULES: tuple[str, ...] = (
    "agentteams/cli/security_gate.py",   # C-2, C-5: the destructive-action gate
    "agentteams/cli/decision_log.py",    # C-2 HALT-finality + C-5 authorization authenticity
                                         # + the enforce_decision_signing switch read; the gate
                                         # imports its enforcement from here, so it carries the
                                         # same constitutional weight and must be tracked too.
    "agentteams/scan.py",                # C-4: the deterministic content scanner
    "agentteams/fences.py",              # C-1: fence restore, template-authoritative set
    "agentteams/unfenced.py",            # C-1: constraint ratchet
    "agentteams/front_matter_merge.py",  # C-3: capability comparison
    "agentteams/front_matter_reconcile.py",
    "agentteams/cli/adopt_merge_gate.py",  # C-3 + Rule 15: the one gated path that extends an
                                           # orchestrator's `agents:` grant under --merge
    "scripts/goose-readfs-mcp.py",  # C-3 on Goose: the read-only file server; read-only by
                                    # construction, so any change to it must be a reviewed re-record
    # The standing red-team audit's phase-6 checks. These are controls, not reporters: a
    # silent edit to any of them turns a check that fires into one that cannot, which is the
    # F-1 defect applied to the machinery built to catch F-1. Registry is included because it
    # holds the evidence normalisation the intent check compares against — widen the
    # normaliser far enough and every probe's evidence digests to the same value.
    "agentteams/redteam/checks_static.py",
    "agentteams/redteam/checks_report.py",
    "agentteams/redteam/registry.py",
    # Privilege EMITTERS (D-2, 2026-08-26; @security CONDITIONAL PASS). The code that mints/emits
    # the workspace + agent-position controls. A silent edit here weakens the emitted boundary
    # without tripping E4 — the same class of gap the phase-6 checks above close for the redteam
    # machinery. (frameworks/claude.py deferred to a separate @code-hygiene split decision.)
    "agentteams/cli/grants.py",          # P2: capability-grant issue/verify + hash-chained ledger
    "agentteams/rank_conformance.py",    # AP-2: the tools:-vs-taxonomy-rank ceiling validator
    # The sandbox EMITTER (D-2b, 2026-08-26; @security CONDITIONAL PASS). Extracted from the
    # high-churn frameworks/claude.py into this small stable module so it can be tamper-tracked
    # without pinning the whole adapter. Emits the confined/exclusive sandbox block —
    # allowWrite / denyRead / denyWrite (D-3 control-plane protection). A silent edit here (e.g.
    # dropping the denyWrite or widening allowWrite) weakens every emitted boundary.
    "agentteams/frameworks/_sandbox_emit.py",
    # The goose SEATBELT emitter (2026-09-30; @security CONDITIONAL PASS). The goose analog of
    # _sandbox_emit.py: it builds .goose/sandbox.sb, including the `(deny file-write* …)`
    # control-plane block (switch, gate hook, verify-key store, the profile itself). It was the
    # one sibling emitter left unpinned, so a silent edit dropping a control-plane entry weakened
    # every emitted goose/macOS boundary without tripping --verify-integrity or E4.
    "agentteams/frameworks/_goose_sandbox_emit.py",
    # Opt-in prompt-root protection (follow-up #8 phase 2, 2026-10-01): the Edit rules and
    # present-only denyWrite set _sandbox_emit.py emits, and the RELAXATION-notice rule set.
    "agentteams/frameworks/_prompt_root_protect.py",
    # The write-root chokepoint (follow-up #2, 2026-09-30; @security CLEAR_WITH_CONDITIONS). Every
    # emitter refuses hard-banned roots through it, and the CLI acceptance gate below uses it; a
    # silent edit here re-opens `~`, `/` or shell injection into the operator-run goose runner.
    "agentteams/frameworks/_write_roots.py",
    "agentteams/cli/write_root_policy.py",   # --accept-write-root gate + widening notice
    # The settings example that WIRES the gate hook into PreToolUse and receives the injected
    # sandbox block (2026-09-30). Neutering its hook wiring disables the gate once merged: the same
    # D-1 class as the pinned hook template below. Safe to pin: the sandbox block is injected into
    # the OUTPUT, never written back to this template.
    "agentteams/templates/universal/hooks/settings.hooks.example.json",
    # The framework-neutral LINUX sandbox EMITTER (2026-W36; framework-neutral Linux OS-confinement).
    # Emits the provider-agnostic bwrap launcher (sandbox/confine-run.sh) that is the Linux boundary
    # for a confined/exclusive team of ANY framework. Same D-2b class as _sandbox_emit.py: a silent
    # edit here (dropping --unshare-net / the credential read-excludes, widening the writable set, or
    # changing the neutral emit path) weakens every emitted Linux boundary without tripping E4.
    "agentteams/frameworks/_linux_sandbox_emit.py",
    # The Linux launcher ASSET itself — the bwrap flags that ARE the boundary (--unshare-net,
    # --ro-bind / /, the credential --tmpfs masks) live in this shipped script, not in the .py
    # (which reads it verbatim). Pinning only the emitter would leave the boundary content
    # untracked: a silent edit dropping --unshare-net here weakens every emitted Linux boundary and
    # trips nothing. Precedent: the constitutional-gate template asset is pinned the same way.
    "agentteams/templates/universal/sandbox/confine-run.sh",
    # The macOS deny-test CONTROL (2026-W36; @security + @code-hygiene CONDITIONAL PASS). Emitted
    # beside the launcher on darwin, mac-escape-tests.sh is the gate that decides whether the macOS
    # boundary may be called "confined" (C3: no confined claim until it passes on-host, unnested).
    # It is a control, not a reporter: a silent edit that turns its deny gates into always-PASS (or
    # neuters the nesting/positive-control guards) would falsely bless an UNCONFINED boundary and
    # trip nothing — the same phase-6 class the red-team checks above are pinned to close. So the
    # boundary's verifier is tamper-tracked exactly as the boundary content (confine-run.sh) is. The
    # two inert Tier-B .example.* files are NOT pinned: they are reference-only, never executed by
    # the launcher, so editing them weakens no active boundary.
    "agentteams/templates/universal/sandbox/mac-escape-tests.sh",
    # The PreToolUse constitutional-gate hook (D-1, 2026-08-26; @security CONDITIONAL PASS). The
    # hook verifies scan.py against this manifest before trusting it, but nothing pinned the hook
    # ITSELF — a silent flip of _FAIL_CLOSED_ON_ERROR or a gutted _decide would neuter the gate
    # undetected. Pinning it lets E4/verify() flag a neutered gate and makes each edit git-visible.
    # (Generated *teams* carry their own emitted hook + manifest; this pins agentteams' own hooks.)
    "agentteams/templates/universal/hooks/constitutional-gate.py",  # the authoritative template
    ".claude/hooks/constitutional-gate.py",                          # this repo's installed Claude hook
    ".github/hooks/constitutional-gate.py",                          # this repo's installed copilot hook
    # Exception-governance hardening (2026-W38). These carry constitutional weight now:
    # a silent edit to any would reopen exactly the gap it closes, without tripping E4.
    "agentteams/cli/signed_ledger.py",   # C-2/WS-B: HMAC + Ed25519 verify primitives (was unpinned;
                                         # verify_by_scheme is now the asymmetric authorization core)
    # The operator SIGNING path (pin-signing-cli-modules, 2026-09-30; @security PR-E review: "pin
    # them, or move the signing code into a pinned module"). The only code that reads the operator
    # Ed25519 private key and builds/displays/refuses/signs/appends --sign-decision and --sign-grant
    # payloads (plus the --issue-grant spec helpers). It was carved out of the high-churn
    # commands.py / grant_commands.py, whose runners are now one-line delegators.
    "agentteams/cli/operator_signing.py",
    # Runs during the signed append (writes the ledger) inside operator_signing's closure.
    "agentteams/atomicio.py",
    # #10 (2026-09-30): the install-location check the minters run before the key is read; in the
    # signing closure, so drift in it refuses signing.
    "agentteams/cli/signer_location.py",
    "agentteams/cli/management_directives.py",  # C-4: the directive denylist / EXACT-scope boundary (M1)
    "agentteams/cli/effect_classifier.py",      # WS-D keystone: the derived relaxing/exception class
    "agentteams/cli/governance_targets.py",     # the single shared trust-root vocabulary both paths use
    "agentteams/cli/exception_registry.py",     # WS-C: the aggregate/proliferation guard + declined-terminal
    # The learned-block doc sync (self-updating-agents PR-A, 2026-10-01). It runs OUTSIDE every
    # agent sandbox (an operator systemd unit) and writes agent files, so its gates — scan,
    # fence-token refusal, byte-identity outside the block, O_NOFOLLOW/realpath checks, the Claude
    # staging rule — are a boundary: a silent edit weakening one turns agent-written text into an
    # unreviewed write into another framework's agent file.
    "agentteams/agent_doc_sync.py",
    "agentteams/learned_blocks.py",
    # The branch lifecycle (2026-10-04). branch_cleanup deletes local and remote refs under a
    # plan-bound @security clearance or an operator branch-delete grant; branch_inventory decides
    # what is deletable (ancestry, Merged-by-PR, holds, api-unknown). A silent edit to either turns
    # a guard (lease, drift skip, refs/pull check, HALT check) into unreviewed ref deletion.
    "agentteams/branch_inventory.py",
    "agentteams/branch_cleanup.py",
    # Shrink-guard overrides (2026-10-04). fences.py (pinned) delegates the release decision to
    # shrink_allow.key/body_digest; an unpinned edit there could drop the reviewed-body digest
    # check and release sections nobody reviewed (security A4 re-verification, finding 1).
    "agentteams/shrink_allow.py",
    "agentteams/integrity.py",           # self, so removing an entry is itself detectable
)

#: The subset of ENFORCEMENT_MODULES that are per-install, machine-local *copies* of a tracked
#: template — the two installed constitutional-gate hooks. Their template
#: (`agentteams/templates/universal/hooks/constitutional-gate.py`) is a normal, tracked,
#: present-required pinned module; these two are gitignored (`.gitignore` `.claude/`,
#: `.github/hooks/`) and therefore legitimately ABSENT in any checkout that has not installed
#: them (CI, a fresh clone). `verify` treats their absence as benign but still flags a PRESENT
#: copy whose bytes differ from the manifest — that is the tooth row 52 pinned them for (a
#: silent edit to the gate that actually executes). Rule-12 trade-off, named deliberately: this
#: exempts an undetected *local deletion* of an installed copy (the same epistemics as a missing
#: manifest), while the *edit* tooth on a present copy and the template-removal tooth (via E3 and
#: the template's own pin) both remain. Extends the 2026-08-26 D-1 clearance (row 52), does not
#: reverse it.
INSTALLED_COPIES: frozenset[str] = frozenset({
    ".claude/hooks/constitutional-gate.py",
    ".github/hooks/constitutional-gate.py",
})

#: Where the manifest lives, relative to the repository root.
MANIFEST_REL_PATH = "references/enforcement-integrity.json"

_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class IntegrityFinding:
    """One enforcement module whose current digest does not match the manifest."""

    rel_path: str
    expected: str
    actual: str
    reason: str  # "modified" | "missing" | "unmanifested" | "manifest-missing"

    def describe(self) -> str:
        if self.reason == "manifest-missing":
            return (f"{self.rel_path}: the integrity manifest is missing although this repository "
                    "ships one or the scanner runs from inside it (nothing can be verified)")
        return f"{self.rel_path}: {self.reason} (expected {self.expected[:12]}, got {self.actual[:12]})"


def _manifest_expected(repo_root: Path) -> bool:
    """True when a MISSING manifest at ``repo_root`` must be a finding, not "never set up".

    Two cases (PR-D, row 31): the imported scanner (``agentteams/scan.py``) resolves inside
    ``repo_root`` below its top level (an in-project ``.venv``), so an in-project agent can edit
    both the scanner and the manifest; or ``repo_root`` is a git work tree that TRACKS the
    manifest (this repository), so its absence is a deletion. A consumer project whose scanner
    sits outside it (site-packages) and that tracks no manifest keeps the historical ``[]``.
    Rule-12: a scanner at ``<repo_root>/agentteams/scan.py`` that git does not track the
    manifest for (an untracked source copy) is NOT flagged; that is the pip-installed layout
    ``generate_helpers._verify_enforcement_integrity`` checks (site-packages as ``repo_root``).
    """
    root = repo_root.resolve()
    spec = importlib.util.find_spec("agentteams.scan")
    if spec is not None and spec.origin:
        scanner = Path(spec.origin).resolve()
        if scanner.is_relative_to(root) and scanner != root / "agentteams" / "scan.py":
            return True
    try:
        # This runs from the gate hook, OUTSIDE the sandbox, in a repository the agent can write.
        # Command-line -c overrides the repo's own config, so a planted `core.fsmonitor` (which git
        # executes during ls-files) or hooks path cannot run code here (@security, PR-D C1).
        tracked = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-c", "core.untrackedCache=false", "-C", str(root),
             "ls-files", "--error-unmatch", "--", MANIFEST_REL_PATH],
            capture_output=True, text=True, timeout=10, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return tracked.returncode == 0


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
        empty** in an ordinary consumer project — an unmanifested repository is not a tampered
        one, and treating "never set up" as "compromised" would make the check fire on every
        fresh clone until it got muted. It is ONE ``manifest-missing`` finding when the
        repository ships the manifest or the scanner runs from inside it
        (:func:`_manifest_expected`, PR-D): there the absence is a deletion, and ``[]`` would
        let the scanner-tamper check pass silently.
    """
    manifest_path = repo_root / MANIFEST_REL_PATH
    if not manifest_path.exists():
        if _manifest_expected(repo_root):
            return [IntegrityFinding(rel_path=MANIFEST_REL_PATH, expected="", actual="",
                                     reason="manifest-missing")]
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
        if not got and rel in INSTALLED_COPIES:
            # A per-install copy legitimately absent here (see INSTALLED_COPIES). Absence is
            # benign; a PRESENT copy that differs is still caught by the `modified` branch below.
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
    "INSTALLED_COPIES",
    "MANIFEST_REL_PATH",
    "IntegrityFinding",
    "compute_digests",
    "write_manifest",
    "verify",
]
