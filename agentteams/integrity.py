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
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agentteams.git_exec import run_git

#: Modules whose integrity the constitution depends on. Each is here because a silent edit to
#: it would disable or weaken a control rather than merely change behaviour.
ENFORCEMENT_MODULES: tuple[str, ...] = (
    "agentteams/cli/security_gate.py",   # C-2, C-5: the destructive-action gate
    "agentteams/cli/decision_log.py",    # C-2 HALT-finality + C-5 authorization authenticity
                                         # + the enforce_decision_signing switch read; the gate
                                         # imports its enforcement from here, so it carries the
                                         # same constitutional weight and must be tracked too.
    "agentteams/cli/decision_chain.py",  # the decisions log's hash chain (deletion detection)
    "agentteams/cli/decision_text.py",   # binds a signed row's conditions/evidence (text_digest)
    "agentteams/cli/decision_lineage.py",  # C-2: derives_from lineage, so a rename can't evade a HALT
    "agentteams/cli/action_ids.py",      # C-2/C-5: which rows clear an action, which HALTs block it
    "agentteams/scan.py",                # C-4: the deterministic content scanner
    "agentteams/scan_operational.py",    # C-4: which pipeline-written JSON the scanner exempts
    "agentteams/fences.py",              # C-1: fence restore, template-authoritative set
    "agentteams/unfenced.py",            # C-1: constraint ratchet
    "agentteams/front_matter_merge.py",  # C-3: capability comparison
    "agentteams/front_matter_reconcile.py",
    "agentteams/cli/adopt_merge_gate.py",  # C-3 + Rule 15: the one gated path that extends an
                                           # orchestrator's `agents:` grant under --merge
    "agentteams/data/goose-readfs-mcp.py",  # C-3 on Goose: the read-only file server; read-only by
                                            # construction, so any change to it must be a reviewed re-record
    "agentteams/data/codex-role-gate.py",  # C-3 on Codex: the PreToolUse role gate (write_policy orchestrator-only);
                                           # a silent widening lets every spawned agent write
    "agentteams/cli/proposal_commands.py",  # the CLI that hands artifacts to proposals.py
    "agentteams/write_policy.py",  # orchestrator-only-writes pilot: narrows generated agents' tools (C-3)
    "agentteams/audit_agent_contract.py",  # R6: AR_WRITE_POLICY decides which agentteams_runner grants are canonical
    "agentteams/confinement.py",  # orchestrator-only-writes pilot: the runner's OS sandbox (C-3/C-5 boundary)
    "agentteams/proposal_runner.py",  # orchestrator-only-writes pilot: sole holder of the ledger key
    "agentteams/sealed_result.py",  # seals the orchestrator's runner results in the session-readable status tree
    "agentteams/proposal_policy.py",  # orchestrator-only-writes pilot: the policy half of proposals.py
    "agentteams/proposals.py",  # orchestrator-only-writes pilot: the one path that applies agents'
                                # proposals and runs their commands; a silent widening defeats the policy
    "agentteams/proposal_run.py",  # R2 carve: the command-request half of proposals.py (runs agents' commands)
    "agentteams/proposal_staging.py",  # R3: staged/direct agent writes and the orchestrator's approvals
    "agentteams/runner_mcp.py",  # R4: the agentteams_runner server's install path and pinned hash
    "agentteams/data/agentteams-runner-mcp.py",  # R4: the server granted agents launch to write and execute
    "agentteams/mcp_direct_grants.py",  # R5: the only way an agent's writes skip the orchestrator's approval
    "agentteams/frameworks/goose_tool_scoping.py",  # C-3 on Goose: declared tools -> recipe grants;
                                                     # a silent widening here re-arms every recipe
    "agentteams/frameworks/_grok_tool_scoping.py",  # C-3 on Grok: Claude tool names -> Grok agent tools:
                                                     # (fail-closed); a silent widening re-arms every Grok agent
    "agentteams/frameworks/_grok_sandbox_emit.py",  # Grok Phase 2b: sandbox profile, confined-run launcher wrapper
                                                    # (GROK_HOME protection, self-probe) and the hook-gate wiring
    "agentteams/templates/universal/hooks/grok-gate-shim.py",  # Grok payload -> Claude shape for the gate; a silent
                                                               # edit blinds the (advisory) gate to Grok's tools
    "agentteams/grok_config_emit.py",  # Grok Phase 2: the [permission] control-plane denies + MCP wiring in
                                       # .grok/config.toml; a silent edit drops a deny or wires an unvetted server
    "agentteams/frameworks/_agents_md_ownership.py",  # who may modify the shared AGENTS.md; a silent edit
                                                       # lets a render overwrite hand-written instructions
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
    # The Codex runner emitter (Phase 1a/1b, 2026-10-08): builds .codex/confined-run.example.sh (the launcher
    # flags, CODEX_HOME placement, the in-launcher self-probe) and pins the role gate's hash. A silent edit
    # dropping a --protect, the self-probe or the gate's pin check weakens every emitted Codex boundary.
    "agentteams/frameworks/_codex_sandbox_emit.py",
    "agentteams/frameworks/_codex_role_gate_emit.py",  # the gate's pin and its hook wiring (.codex/hooks.json)
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
    # 2026-10-22 (vulnscan-remediation-2, @security R3-7): --sign-mcp-direct-grant builds the record this
    # signing path displays and signs; a silent edit to how it binds the brief or the lifetime would change
    # what the operator's confirmed digest covers.
    "agentteams/cli/mcp_grant_commands.py",
    # Runs during the signed append (writes the ledger) inside operator_signing's closure.
    "agentteams/atomicio.py",
    # CH-08 (2026-10-07): the shared git core and its read-only hardening; imported by the pinned proposals.py,
    # operator_signing.py, signer_location.py and this module, so it sits in their closure.
    "agentteams/git_exec.py",
    # #10 (2026-09-30): the install-location check the minters run before the key is read; in the
    # signing closure, so drift in it refuses signing.
    "agentteams/cli/signer_location.py",
    # vulnscan standing remediation grants (2026-10-22, plan vulnscan-autonomous-remediation): grant.py
    # signs through the operator flow and verifies the Ed25519 grant that pre-clears unattended actions
    # (C-5 / Rule 15); executor.py is the only code that acts under a grant (HALT check, begun row,
    # deterministic change, credentialed push); ledger.py is the MAC chain the use caps and the
    # after-action review trust. A silent edit to any of them would widen what an agent can get done.
    "agentteams/vulnscan/grant.py",
    "agentteams/vulnscan/executor.py",
    "agentteams/vulnscan/ledger.py",
    # paths.py decides where grants, the ledger key and config live; agent_run.py sets the container flags that are
    # S-VM's "no credentials inside the sandbox" precondition; gateway.py is the agents' only egress and spend cap;
    # mcp.py is the agents' only request channel (@security P3 review, MAJOR 4).
    "agentteams/vulnscan/paths.py",
    "agentteams/vulnscan/agent_run.py",
    "agentteams/vulnscan/selftest_probe.py",   # the self-test probe a passing self-test vouches for
    "agentteams/vulnscan/docker_mode.py",      # R4: the container user per Docker mode (never real root)
    "agentteams/vulnscan/run_records.py",      # archives earlier runs' status, transcript and report
    "agentteams/vulnscan/registry.py",         # machine registry: what is scanned and proposed for grants
    "agentteams/vulnscan/coverage.py",         # effective scan roots and coverage states
    "agentteams/vulnscan/project_cli.py",      # enroll / roots / status commands
    "agentteams/vulnscan/project_policy.py",   # per-project level, exclusions, private packages
    "agentteams/vulnscan/private_sources.py",  # GOPRIVATE, nuget.config and .npmrc sources: names kept local
    "agentteams/vulnscan/executor_guard.py",   # grants-first; protected repos by origin; scan-only refusal
    "agentteams/vulnscan/repo_alias.py",       # the agent's repository aliases (mapped back by mcp.py)
    "agentteams/vulnscan/executor_serve.py",   # spool request schema checks, batch preflight (split from executor.py)
    "agentteams/vulnscan/retry_guard.py",      # holds a futile stage-fix (refusal decided inside authorize)
    "agentteams/vulnscan/effectiveness.py",    # what the published summary says about remediation (counts only)
    "agentteams/vulnscan/executor_supersede.py",   # close-superseded: the executor's only PR-closing code
    "agentteams/vulnscan/scan_mirror.py",      # which tree the daily scan reads (mirror or checkout) and its naming
    "agentteams/vulnscan/executor_env.py",     # ExecError, the executor's git identity and hardened git/gh envs
    "agentteams/vulnscan/executor_pr.py",
    "agentteams/vulnscan/open_prs.py",         # D1: packages another open PR updates are left to it      # stage-*'s commit and open-pr's PR text, label and gh pr create
    "agentteams/vulnscan/npm_major.py",        # stage-major: the package.json raise and validate_diff's check of it
    "agentteams/vulnscan/npm_groups.py",       # stage-fix: which packages npm update moves together (selection)
    "agentteams/vulnscan/npm_ops.py",          # stage-fix: npm_ops.npm_moves runs those moves (fail-closed checks)
    "agentteams/vulnscan/image_fix.py",        # stage-image: the FROM tag planner and validate_diff's check of it
    "agentteams/vulnscan/ops.py",              # REPO_OPS: which ops get executor_guard and a repo-root grant
    "agentteams/vulnscan/merge_digest.py",     # the operator-run merge script: validated, shlex-quoted fields only
    "agentteams/vulnscan/handoff.py",          # H3: open-pr waits for the repository orchestrator's approval
    "agentteams/vulnscan/remediation_status.py",  # what blocks the executor (summary + notifications)
    "agentteams/vulnscan/remediation_report.py",  # infra (retried) vs policy (final): the anchored classification
    "agentteams/vulnscan/server_fixplan.py",   # per-host server fix plans (read-only)
    "agentteams/vulnscan/agent_drivers.py",    # per-framework agent runtime (image pin, env, command, lockdown)
    "agentteams/vulnscan/team_dir.py",         # the remediation team directory check
    "agentteams/vulnscan/gateway_dialects.py", # per-dialect allowlists, usage parsing, reservation
    "agentteams/vulnscan/agent_upstreams.py",  # per-driver gateway upstreams, key files, model rules
    "agentteams/vulnscan/selftest_canary.py",  # headless CLI tool-inventory and planted-file canary
    "agentteams/vulnscan/gateway.py",
    "agentteams/vulnscan/mcp.py",
    # R6 operator loop (2026-10-22, metaplan vulnscan-r6 T1): escalations.py and publish.py sign through the operator
    # flow and verify Ed25519 resolutions / the publish grant (publish.py also holds the only push of agent-derived
    # text off the machine); operator_cli.py drives both signing flows; escalations.py, notes.py and report_store.py
    # decide what blocks remediation and what agent / operator text reaches the agent or the review, fenced;
    # notify.py keeps agent text out of notifications; review.py fences the published review; daily.py and
    # debrief.py wire publishing and the debrief's read-only spool and caps.
    "agentteams/vulnscan/escalations.py",
    "agentteams/vulnscan/publish.py",
    "agentteams/vulnscan/operator_cli.py",
    "agentteams/vulnscan/notes.py",
    "agentteams/vulnscan/report_store.py",
    "agentteams/vulnscan/notify.py",
    "agentteams/vulnscan/review.py",
    "agentteams/vulnscan/daily.py",
    "agentteams/vulnscan/checkout_freshness.py",   # split from daily.py: labels findings fixed upstream
    "agentteams/vulnscan/infra_retry.py",          # split from daily.py: when a partial run is re-run
    "agentteams/vulnscan/worktree_fixes.py",       # split from daily.py: apply-worktree staging
    "agentteams/vulnscan/debrief.py",
    # G1 T2 condition: __main__.py wires grant signing and the operator commands; manifests.py's REQ_PIN decides
    # what validate_diff lets stage-fix change.
    "agentteams/vulnscan/__main__.py",
    "agentteams/vulnscan/manifests.py",
    "agentteams/vulnscan/lockfiles.py",       # the Go/pnpm/Bun/RubyGems/Gradle/NuGet lockfile readers
    # 2026-10-22 (vulnscan-test-campaign M1): the rest of the package. Every module runs in the unsandboxed daily
    # job: _http.py holds the feed allowlist, osv.py/engine.py/versions.py/runtimes.py decide what counts as
    # vulnerable (a silent edit could fail open), fix.py computes the change stage-fix commits, schedule.py writes
    # the job the OS runs, report.py renders what the operator reads. Pinned whole, so a new module is a test failure.
    "agentteams/vulnscan/__init__.py",
    "agentteams/vulnscan/_http.py",
    "agentteams/vulnscan/osv.py",
    "agentteams/vulnscan/engine.py",
    "agentteams/vulnscan/versions.py",
    "agentteams/vulnscan/specifiers.py",   # 2026-10-30 (N6a): judges unpinned requirement ranges
    "agentteams/vulnscan/runtimes.py",
    "agentteams/vulnscan/images.py",          # 2026-10-30 (N2): which base images count as end-of-life
    "agentteams/vulnscan/fix.py",
    "agentteams/vulnscan/schedule.py",
    "agentteams/vulnscan/report.py",
    # 2026-10-22 (vulnscan-remediation-2, @security V1-2 / @code-hygiene V3-8): the packaged server kit runs
    # as root on the operator's servers (`sudo -n`), from the executor and, since P12, from the daily job;
    # a silent edit to run-host.sh could turn the dry run into an apply. The agent image's Dockerfile
    # decides what the credential-free container holds.
    "agentteams/data/server_kit/remote.sh",
    "agentteams/data/server_kit/run-host.sh",
    "agentteams/data/server_kit/L1-audit.sh",
    "agentteams/data/server_kit/L2-os-patch.sh",
    "agentteams/data/server_kit/L3-node-runtime.sh",
    "agentteams/data/server_kit/L4-python-app.sh",
    "agentteams/data/server_kit/L5-containers.sh",
    # M1: remote.sh embeds the IOC lists into the root-run payload; an emptied list silently stops detection.
    "agentteams/data/server_kit/ioc-2026-10-litellm-1.107.0.txt",
    "agentteams/data/vulnscan_agent/Dockerfile",
    # W1 (baseagent-security-hardening): the tools container that confines the executor's git, npm and ssh;
    # its egress proxy is the only exit, and its credential helper the only holder of the push token.
    "agentteams/vulnscan/subproc_confine.py",
    "agentteams/vulnscan/subproc_confine_ssh.py",
    "agentteams/vulnscan/tools_image.py",
    "agentteams/vulnscan/docker_readiness.py",   # T2-A: the probe behind the tools container's fail-closed check
    "agentteams/data/vulnscan_tools/Dockerfile",
    "agentteams/data/vulnscan_tools/egress_proxy.py",
    "agentteams/data/vulnscan_tools/selftest.py",
    "agentteams/data/vulnscan_tools/vulnscan-with-token",
    "agentteams/data/vulnscan_tools/git-credential-vulnscan",
    # W2: the gateway's DLP filter (blocks secrets, redacts private data) and its peer-credential listener.
    "agentteams/vulnscan/gateway_dlp.py",
    "agentteams/vulnscan/gateway_peer.py",
    "agentteams/vulnscan/gateway_context.py",  # host names, secrets files and private names given to the DLP filter
    "agentteams/vulnscan/cvss.py",             # CVSS v3.1 / v4.0 base scores: decides what counts as HIGH
    # Phase 4 runtimes: the driver contract, the Copilot and Codex lockdowns, the headless CLI probe, and the
    # committed lockfile the Copilot image is built from (a changed lockfile changes what the container runs).
    "agentteams/vulnscan/agent_driver_base.py",
    "agentteams/vulnscan/agent_drivers_copilot.py",
    "agentteams/vulnscan/agent_drivers_codex.py",
    "agentteams/vulnscan/selftest_cli.py",
    "agentteams/vulnscan/agent_recipe.py",
    "agentteams/vulnscan/agent_settings.py",
    "agentteams/data/vulnscan_agent/copilot/package.json",
    "agentteams/data/vulnscan_agent/copilot/package-lock.json",
    "agentteams/cli/management_directives.py",  # C-4: the directive denylist / EXACT-scope boundary (M1)
    "agentteams/cli/effect_classifier.py",      # WS-D keystone: the derived relaxing/exception class
    "agentteams/cli/governance_targets.py",     # the single shared trust-root vocabulary both paths use
    "agentteams/cli/frozen_surfaces.py",        # Rule 15: frozen-surface owned paths + unfreeze vocabulary
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
    # Pre-fencing twins (2026-10-10). fences.py (pinned) delegates to fence_twins the one decision that
    # deletes UNFENCED text — an unfenced copy of a section a fence now owns — and the lineage corpus
    # is that decision's authority (Rule 12): text added to it becomes deletable from every deployed
    # repository. An unpinned edit to either would widen what a merge may delete.
    "agentteams/fence_twins.py",
    "agentteams/data/template_lineage.json",
    # Launcher lineage (2026-10-28, P6). launcher_lineage tells a team's sandbox/confine-run.sh copy current /
    # older / modified / missing by matching its bytes against this lineage. Informational, but an unpinned edit
    # adding a sha256 would label a tampered launcher a released one (`modified` must never read as benign).
    "agentteams/launcher_lineage.py",
    "agentteams/data/launcher_lineage.json",
    # Kept-twin acknowledgements (2026-10-11). fence_twin_acks validates the operator ledger (digest
    # format, the 40-character reason) whose rows turn a kept twin's fleet REVIEW into a notice; an
    # unpinned edit there could let an unreviewed or reasonless row silence REVIEW. It cannot delete.
    "agentteams/fence_twin_acks.py",
    # Whole-body fence retirement (2026-10-11). fences.py (pinned) delegates to whole_body_migration the
    # rewrite of a legacy `content` block and the decision which of its sections are dropped as template
    # text and which are kept or flagged. An unpinned edit there could widen what a migration deletes.
    "agentteams/whole_body_migration.py",
    # Front-matter baseline (2026-10-11). front_matter_merge.py (pinned) applies a template value only when
    # the on-disk value equals this module's baseline, and its handoff guard decides which handoffs a write
    # may drop. An unpinned edit could make a project's value look agentteams-authored again.
    "agentteams/front_matter_baseline.py",
    # Fence baseline (2026-11-01, vfail F1-D). fences.py (pinned) releases a shrink-preserved fence when its
    # on-disk block hashes to this module's recorded render; an unpinned edit could make a person's edit look
    # unedited and let a template update overwrite it.
    "agentteams/fence_baseline.py",
    # Acknowledgement review (2026-10-11). _fleet_twin_acks decides which acknowledged twins fleet still
    # reports as REVIEW (one not shown to the operator by an earlier run); an unpinned edit could drop that flag.
    "agentteams/_fleet_twin_acks.py",
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
        tracked = run_git(root, "ls-files", "--error-unmatch", "--", MANIFEST_REL_PATH, hardened=True, timeout=10)
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


#: The authoritative template of the two :data:`INSTALLED_COPIES`. When it changes, a copy
#: digest carried over by :func:`write_manifest` probably describes the superseded bytes.
_GATE_TEMPLATE = "agentteams/templates/universal/hooks/constitutional-gate.py"
if _GATE_TEMPLATE not in ENFORCEMENT_MODULES:  # a drifted path would silently drop the stale warning
    raise RuntimeError(f"integrity: {_GATE_TEMPLATE} is not a pinned enforcement module")

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _notice(message: str) -> None:
    print(f"[INTEGRITY] {message}", file=sys.stderr)


def _previous_modules(manifest_path: Path) -> dict[str, str] | None:
    """Return the ``modules`` of an existing manifest: ``{}`` when there is none, ``None`` when unreadable."""
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    modules = payload.get("modules") if isinstance(payload, dict) else None
    if not isinstance(modules, dict) or not all(isinstance(v, str) for v in modules.values()):
        return None
    return modules


def _carry_over_installed_copies(digests: dict[str, str], previous: dict[str, str] | None,
                                 repo_root: Path) -> None:
    """Keep the recorded digest of an installed copy that is absent from *repo_root*.

    The copies are gitignored, so they are absent in every worktree and fresh clone; recording
    ``""`` there blanked the main checkout's pins (GROK-RETRO-R2). Absence is judged by
    ``exists()``, never by an empty digest: :func:`_digest` also returns ``""`` for a present
    but unreadable file, and that file must not inherit the old pin.

    Rule-12, named: keeping an old digest can keep a stale or planted one alive across a
    re-record, outside that re-record's diff. Each carry-over is announced, only a well-formed
    SHA-256 is carried, and a kept digest that differs from the current gate template's (the
    copies are installed byte-for-byte from it) is warned about, rather than narrowing the
    carry-over.
    """
    absent = sorted(rel for rel in INSTALLED_COPIES if not (repo_root / rel).exists())
    if not absent:
        return
    if previous is None:
        _notice("the existing manifest is unreadable, so no installed-copy digest is preserved; "
                f"recording '' for {', '.join(absent)}.")
        return
    for rel in absent:
        kept = previous.get(rel, "")
        if not _SHA256_RE.fullmatch(kept):
            _notice(f"{rel} is absent here and has no well-formed prior digest; recording ''.")
            continue
        digests[rel] = kept
        _notice(f"{rel} is absent here (gitignored install); kept its recorded digest {kept[:12]}.")
        if kept != digests.get(_GATE_TEMPLATE):
            _notice(f"WARNING: the kept digest of {rel} differs from the current {_GATE_TEMPLATE}, so it is "
                    "probably stale. Re-record from a checkout where the hook is installed.")


def write_manifest(repo_root: Path) -> Path:
    """Write (or refresh) the integrity manifest and return its path.

    Regenerating is deliberately a separate, explicit act: it is what an operator does after an
    intentional change to a control, and what an attacker must additionally do after an
    unintentional one. Keeping it out of the normal build is the point — an auto-refreshed
    manifest verifies nothing.

    An :data:`INSTALLED_COPIES` path that does not exist keeps the digest the existing manifest
    records for it (see :func:`_carry_over_installed_copies`); every other module is re-hashed,
    and a missing one is still recorded as ``""``.

    Args:
        repo_root: Repository root.

    Returns:
        The path of the written manifest.
    """
    manifest_path = repo_root / MANIFEST_REL_PATH
    digests = compute_digests(repo_root)
    _carry_over_installed_copies(digests, _previous_modules(manifest_path), repo_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _MANIFEST_VERSION,
        "note": (
            "SHA-256 of the modules that enforce C-1..C-5. Regenerate deliberately, with "
            "`agentteams --write-integrity-manifest`, only after an intended change to a "
            "control. See agentteams/integrity.py for what this does and does not guarantee."
        ),
        "modules": digests,
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


def stale_summary(repo_root: Path) -> str:
    """Return a short developer-facing summary of a stale manifest, or ``""`` when it verifies.

    Every edit to a pinned module stales the manifest, and ``--check`` (plus the tests that assert
    its exit code) then fails until the manifest is re-recorded — a predictable failure that reads
    like a regression (INTEGRITY-MANIFEST-STALE-PER-PIN-EDIT). This names each stale pin and the
    exact re-record command. It does not re-record anything: re-recording stays a deliberate act,
    done only after the change was intended and its diff reviewed.

    Args:
        repo_root: Repository root.

    Returns:
        ``""`` when :func:`verify` reports no findings; otherwise a multi-line text with one line
        per finding (path and reason) followed by the command that re-records the manifest.

    Raises:
        RuntimeError: When the manifest exists but cannot be read (propagated from :func:`verify`).
    """
    findings = verify(repo_root)
    if not findings:
        return ""
    lines = [f"[INTEGRITY] {MANIFEST_REL_PATH} is stale: {len(findings)} pinned module(s) do not match."]
    lines += [f"  - {finding.rel_path}: {finding.reason}" for finding in findings]
    lines.append("  If the change was intended, review the diff, then re-record with:")
    lines.append(f"    agentteams --write-integrity-manifest --project {repo_root.resolve()}")
    return "\n".join(lines)


__all__ = [
    "ENFORCEMENT_MODULES",
    "INSTALLED_COPIES",
    "MANIFEST_REL_PATH",
    "IntegrityFinding",
    "compute_digests",
    "write_manifest",
    "verify",
    "stale_summary",
]
