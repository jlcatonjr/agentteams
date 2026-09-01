# SOURCES — provenance for every canonical fact

> Every canonical fact in `SKELETON.md` rests on repo evidence. **No fact ships without a source; no
> line number is quoted from memory.** `@technical-validator` verifies each row resolves on disk and
> that each ✅/⚙ marker is accurate; `@adversarial` verifies each honest ceiling is not overstated.
>
> Status legend: ✅ *implemented & enforced in code/tests* · ⚙ *design / procedural / agent-instruction only*.

## By section

| § | Topic | Primary sources | Status |
|---|---|---|---|
| S1 | What agentteams security is | `SECURITY.md` (threat-model, design-time-vs-runtime); `.claude/CLAUDE.md` (Constitutional Core); `agentteams/templates/universal/security.template.md`; `agentteams/host_features.py:134-145` (cooperative default); `hooks/constitutional-gate.py:22-36` (fail-open default) | ✅/⚙ |
| S2 | Two surfaces & where enforcement lives | `SECURITY.md`; `agentteams/cli/security_gate.py:1-10`; `agentteams/templates/universal/hooks/constitutional-gate.py:1-49`; `agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-44` | ✅/⚙ |
| S3 | Constitutional Core (C-1..C-5) | `.claude/CLAUDE.md`; `agentteams/templates/universal/orchestrator.template.md:121-142`; `agentteams/templates/universal/instruction-authority.reference.template.md:27-43` | ✅ |
| S4 | Instruction-authority ordering | `agentteams/templates/universal/instruction-authority.reference.template.md:9-91`; `agentteams/audit_agent_contract.py:95-152` | ⚙ rule / ✅ presence |
| S5 | The `@security` sentinel | `agentteams/templates/universal/security.template.md:5,28-38,49-72,76-247,250-279`; `agentteams/scan.py` | ✅ contract / ⚙ rules |
| S6 | HALT finality & capability limits | `agentteams/cli/security_gate.py:120-169,430-477`; `agentteams/cli/signed_ledger.py:9-92`; `agentteams/front_matter_merge.py:368-408`; `agentteams/audit_agent_contract.py:202-243` | ✅ |
| S7 | The authorization triad | `agentteams/cli/grants.py:1-36`; `agentteams/cli/security_gate.py:39-69`; `agentteams/cli/signed_ledger.py:9-14` | ✅ |
| S8 | Decisions log & CONDITIONAL PASS | `agentteams/cli/decision_log.py:22-63,187-234`; `agentteams/cli/security_gate.py:96-259,619-659`; `agentteams/templates/universal/orchestrator.template.md:339-350` | ✅ |
| S9 | Signed waivers | `agentteams/cli/security_gate.py:39-69,430-477,511-616`; `agentteams/cli/signed_ledger.py:40-92` | ✅ |
| S10 | Capability grants | `agentteams/cli/grants.py:56-84,126-235,306-473,476-639` | ✅ |
| S11 | Destructive-operation gate | `agentteams/cli/generate.py:605-615,1060-1072`; `agentteams/cli/standalone_modes.py:62-65`; `agentteams/cli/security_gate.py:24-37,96-214` | ✅ |
| S12 | Intelligence-freshness gate | `agentteams/cli/generate.py:466-471`; `agentteams/cli/security_gate.py:262-427`; `agentteams/security_refs.py:835-916` | ✅ |
| S13 | Shrink-policy | `agentteams/cli/parser.py:725-739`; `agentteams/emit.py:302-334,685-717`; `agentteams/fences.py:158-268,271-300,351-403` | ✅ |
| S14 | Bridge-refresh safety | `references/bridge-refresh-safety.md:5-95,219-245`; `agentteams/templates/universal/orchestrator.template.md:177` | ✅ policy / ⚙ Pre-Flight |
| S15 | The content scanner | `agentteams/scan.py:38-102,157-174,275-303,536-568,679-857` | ✅ |
| S16 | Redaction & feed sanitization | `agentteams/fences.py:158-161,304-403`; `agentteams/security_feed_render.py:21-60` | ✅ |
| S17 | Infrastructure-layers model | `agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-70,119-130` | ✅ ref |
| S18 | Sandbox emission & profiles | `agentteams/host_features.py:134-261`; `agentteams/frameworks/_sandbox_emit.py:25-208`; `agentteams/frameworks/_goose_sandbox_emit.py:1-222`; `references/agentteams-remediation-log.csv` (D-3); `agentteams/cli/artifacts.py:321-411` | ✅ Linux (bwrap deny-tested) / ⚙ macOS Seatbelt UNVERIFIED, Windows |
| S19 | The constitutional-gate hook | `agentteams/templates/universal/hooks/constitutional-gate.py:1-209` | ✅ (fail-closed under confined/exclusive; fail-open default) |
| S20 | Threat-intelligence watch | `agentteams/security_refs.py:42-70,710-917`; `agentteams/security_feed_render.py:21-144`; `agentteams/templates/universal/security-vulnerability-watch.reference.template.md` | ✅ |
| S21 | Red-team methodology | `agentteams/templates/universal/redteam-methodology.reference.template.md:22-239`; `agentteams/redteam/registry.py:36-55`; `agentteams/redteam/runner.py:39-79`; `agentteams/redteam/selfaudit.py:33-101`; `agentteams/redteam/cycle.py:1-215` | ✅ |
| S22 | Integrity manifests | `agentteams/integrity.py:1-188`; `agentteams/cli/commands.py:170-266` | ✅ |
| S23 | Provenance stamps | `agentteams/provenance.py:1-99` | ⚙ |
| S24 | Backups & baselines | `agentteams/backup.py:1-497`; `agentteams/baseline.py:1-131`; `agentteams/cli/backup_switch.py:1-85`; `agentteams/cli/app.py:133-163` | ✅ |
| S25 | Defense-in-depth synthesis | synthesis of S1–S24; `SECURITY.md`; `security-infrastructure-layers.reference.template.md` | ✅/⚙ |
| S26 | Glossary | the defining section for each term (S3–S24) | ✅ |
| S27 | Sources | this file; all files cited in S1–S26 | ✅ |

## Reference templates (concept → file map)

| Concept | Template file (under `agentteams/templates/universal/`) |
|---|---|
| The `@security` sentinel contract | `security.template.md` |
| Instruction-authority ordering / injection defense | `instruction-authority.reference.template.md` |
| Deployed-system defense-in-depth (L0–L7) | `security-infrastructure-layers.reference.template.md` |
| Live CVE/threat feed + OWASP LLM Top 10 | `security-vulnerability-watch.reference.template.md` (+ `.json.template`) |
| OS platform hardening baselines | `security-{macos,linux,windows}-hardening.reference.template.md` |
| Self-auditing red-team cadence | `redteam-methodology.reference.template.md` |
| The runtime PreToolUse gate | `hooks/constitutional-gate.py` |

## Measured / dated evidence

- The **Linux** empirical verification of OS confinement (the `sandbox/confine-run.sh` bwrap launcher's
  live-kernel deny test; see the [Sandboxing Guide](../agentteams-sandboxing-guide/SOURCES.md)), the
  **UNVERIFIED** macOS Seatbelt path, and the open **D-3** bubblewrap absent-path fragility + unverified
  `denyRead` (P3a) on Claude Code's *native* Linux arm, are recorded in
  `agentteams/templates/universal/sandbox/confine-run.sh` (status header) and
  `references/agentteams-remediation-log.csv`.
- The red-team "719 exposed agents" denominator defect (motivating the type-level `Count`) and the
  probe families (E3/E4 enforcement-surface gaps, A10/W10 digest bind) are recorded in the red-team
  artifacts and the remediation log.

## Maintainer note

Edit the skeleton first, then re-project (see `_meta/projection-guide.md`). This file changes whenever a
`Source` line in `SKELETON.md` changes.
