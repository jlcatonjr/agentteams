# Adversarial Audit — Goose Bridge Remediation (2026-06-22)

Scope: `references/plans/goose-bridge-remediation-2026-06-22.plan.md` (F1–F4, R1–R5),
against `agentteams/bridge.py`, `agentteams/frameworks/goose.py`, and the mis-generated
`references/bridges/copilot-vscode-to-goose/` artifacts.

## Presupposition Review

1. Presupposition (F1): The goose bridge inventory is empty, so the bridge is non-functional.
- Verdict: Accepted.
- Basis: `…copilot-vscode-to-goose/agent-inventory.md` has only the table header; `bridge-manifest.json` records `"inventory_count": 0`. The goose entry surface (`AGENTS.md`, `.goosehints`, `bridge-orchestrator.yaml`) all point a session at `agent-inventory.md` to adopt the orchestrator identity (`goose.py:512-518`, `_goosehints_content` `goose.py:128-133`). Zero rows → nothing to route to. Reproduced: `_extract_inventory(Path('.'), 'copilot-vscode')` returns 0.

2. Presupposition (F2): Root cause is `--bridge-from .` (repo root) instead of `--bridge-from .github/agents`.
- Verdict: Accepted.
- Basis: `_extract_inventory` filters copilot-vscode sources to `*.agent.md` (`bridge.py:449`); the repo root has none → 0 agents; `.github/agents` yields 35. The manifest `source_dir` is the repo root and its `source_hashes` are 21 root files incl. `.DS_Store`/`.gitignore`/`.goosehints`. Conclusive.

3. Presupposition (F3 / R1): An empty inventory should produce a WARNING NOTICE, not a hard error.
- Verdict: Accepted with mitigation.
- Basis: "Don't hard-fail a nascent team" is legitimate and STABILITY-aligned, and a notice beats today's silent `inventory_count:0` write. But the notice does not *block* a broken bridge from shipping or passing `--bridge-check`: `_run_bridge_check` only compares source hashes (`bridge.py:522-533`) and would PASS a self-consistent 0-agent bridge. Mitigation required: also report 0-inventory as a check FAIL, condition the generate notice strictly on `len==0`, and place it on the write path (after the `check_only` branch at `bridge.py:113`) so it does not double-fire.

4. Presupposition (F4 / R2): Skipping dotfiles keeps junk out of the manifest without dropping a file that should be hashed.
- Verdict: Rejected.
- Basis: `.github/agents/_build-description.json` is `_`-prefixed (not dot-prefixed), so `name.startswith(".")` does not skip it. Empirically the dotfile filter drops **nothing** from `.github/agents`. `_build-description.json` is gitignored, build-tool-regenerated (13.8 KB), and is already hashed into the *good* claude manifest (`agents/_build-description.json`). So the F4 symptom — `--bridge-check` tripping on changes unrelated to the agent team — persists for the most volatile file even after R2. Replace with a markdown-only allowlist (or explicit junk denylist), not a dotfile skip.

5. Presupposition (R4): `--bridge-merge` regenerates the bridge-OWNED inventory + manifest unconditionally (so the empty inventory is fixed), and merge is correct because AGENTS.md is shared.
- Verdict: Accepted.
- Basis: `bridge_files` (manifest, `agent-inventory.md`, quickstart, entrypoint, domain-boundary, goose `bridge-orchestrator.yaml`) are written unconditionally under merge — the loop at `bridge.py:238-248` skips only when `path.exists() and not overwrite and not merge_only`, false under `merge_only`. The empty inventory is bridge-owned, so a re-run with `--bridge-from .github/agents --bridge-merge` overwrites it with the 35-agent render. `AGENTS.md` is fenced, so the `target_files` path re-renders only the fence (`_merge_target_file`, `bridge.py:287-317`) — correct per `bridge-refresh-safety.md`. Caveat: confirm post-merge that the fenced *body* content (not just file existence) changed.

6. Presupposition (implicit): The 35-vs-34 count and the untracked status are irrelevant.
- Verdict: Rejected.
- Basis: Untracked status is the enabling precondition for the low-risk regenerate-in-place posture (nothing committed at risk; trivial `rm` rollback) and must be stated as such. 35-vs-34 is a real, expected asymmetry: source has 35 `.agent.md`, the scoped-out claude inventory has 34 data rows — the regenerated goose bridge will legitimately show 35 while claude still shows 34; R5 must name this so it is not re-filed as a regression.

## Risk Notes

- Residual F4 after the drafted R2: `_build-description.json` survives the dotfile skip and keeps tripping `--bridge-check`. The markdown-only rule is required to actually close it.
- Broken-bridge-ships gap: a notice does not gate generation and `--bridge-check` passes a 0-inventory manifest; add the 0-inventory check FAIL.
- Test-regression risk: existing `result.notices == []` assertions (`test_bridge.py:214,257`) use a non-empty source; the new notice is safe only if it fires strictly on `len(inventory)==0` and only on the generate path.
- Merge refreshes fence *bodies*, not just presence; R5 must verify the bodies.

## Adversarial Verdict

PASS with conditions.

F1, F2, R4 are verified correct and the regenerate-in-place strategy is sound and low-risk given the all-untracked working tree. Conditions: (1) re-scope R2 from dotfile-skip to a markdown-only allowlist and assert `_build-description.json` is excluded; (2) strengthen R1 with a 0-inventory check FAIL and a strict `==0` generate-path notice; (3) add to R5 the merged-fence-body verification and the goose=35/claude=34 asymmetry note.

Recommended action: fold conditions into the plan (done — §5 trace), then proceed to the conflict audit and implementation.

---

*Conditions resolved in the revised plan (§2 R1/R2/R5, §5 trace). This record reflects the independent audit as performed.*
