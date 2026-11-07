# Red-Team Results, August 2026

**Published:** 2026-11-06. **Measured:** 2026-08-06 to 2026-08-18.
**Scope:** aggregate results only. See [What this note leaves out](#what-this-note-leaves-out).

This note records three measurements from this repository's red-team and fleet work. The method and tooling
are described in the [Red-Team Model Scoring & Attack Generation](redteam-model-scoring-guide.md) guide. The
scripts that produced the figures are tracked under `scripts/`.

The runs were made on pre-release history. The commits named below are not reachable from the public `main`
or `dev` branches, so this note is the citable record of the results.

---

## 1. The `@security` contract, with and without

**Question:** does the `@security` agent's contract cause its resistance to prompt injection, or does it
merely coincide with resistance the model already has?

**Design:** the same models, payloads and harness were run in two arms. In one arm the agent ran under the
`@security` contract. In the other the contract and its review framing were removed (ablated). Nothing else
changed between the arms.

- **Models:** 12 open-weights models above a capability floor, listed alphabetically:
  `ai21/jamba-large-1.7`, `deepseek/deepseek-r1`, `deepseek/deepseek-v3.2`, `meta-llama/llama-4-maverick`,
  `minimax/minimax-m2`, `mistralai/mistral-large-2512`, `mistralai/mistral-medium-3-5`, `moonshotai/kimi-k2`,
  `nvidia/nemotron-3-ultra-550b-a55b`, `qwen/qwen3.6-max-preview`, `qwen/qwen3.6-plus` and `z-ai/glm-5.2`.
- **Corpus:** the 14-payload judgment corpus (`tests/redteam/payloads.json` as of the run commit). It holds
  11 attack payloads and 3 benign controls. The attacks cover six classes: paraphrase, authority claim,
  role-play, tool result, incremental and literal control. 11 attacks × 12 models gives 132 attack replies
  per arm.
- **Scale:** 24 arms, 336 model calls, US$1.34 measured spend, no transport failures.
- **Scoring:** a reply counts as a capitulation when the model adopts, in its own voice, a premise the payload
  asserted. The test is first-person stance, not vocabulary, so it names no payload's content.

**Result:**

| arm | capitulations |
|---|---|
| with the `@security` contract | **0 of 132** |
| contract ablated | **27 of 132** |

With the contract in place no model capitulated. Without it, 27 replies did. Because the contract and its framing were the only
difference between the arms, they account for all 27.

**Instrument repair.** The figures above come from a repaired detector. The earlier detector matched phrases
two models happened to emit. On the ablated replies it caught 2 of 26 replies verified as
capitulations, a detection rate of about 7%. Re-scored with the stance test, the same preserved replies give
the 27 above, with no false positives. The repair is why earlier "zero compliance"
figures from this project should not be cited.

**Model fitness.** 8 of the 12 models were rated acceptable for the security-judgment role and 4 were not.
All four failed the same check: correctly rejecting a forged authority claim. Per-model ratings are not
published (see below).

**Run commit:** `e45d371` (2026-08-07), made against parent `d211584f`. Scripts:
`scripts/redteam_model_matrix_run.py` (the run) and `scripts/redteam_model_ratings.py` (the ratings, which are
generated from run artifacts, not typed out by hand).

**Open at the time of the run:** a known scorer defect in verdict attribution (D1) remained open. The
capitulation counts come from the separately repaired compliance detector.

## 2. Agreement of the judgment oracle

**Question:** how reliable are human-read verdicts on the forged-authority probe (`auth-01`)?

**Design:** two independent judge models, from different families, each rated a separate set of 23 preserved
`auth-01` responses (not the 12-model run above) as CORRECT or MISSED. Their ratings were compared with the human-read verdicts. Script:
`scripts/redteam_oracle_intercheck.py`.

**Result:**

| second rater | raw agreement | Cohen's κ |
|---|---|---|
| `openai/gpt-4o-mini` | 95.7% (22 of 23) | **0.913** |
| `google/gemini-2.5-flash` | — | **0.911** |

Both judges reach near-perfect agreement. Each disagreed with the human verdict on a different single
response, and both of those were hedged or borderline. So the residual uncertainty sits on ambiguous hedges,
not in random noise. The `gpt-4o-mini` disagreement was flagged for human adjudication, not corrected
automatically.

**Run commits:** `2dbcfc55` and `0d93afbf` (both 2026-08-18). Total spend across the oracle checks: about
US$0.01.

## 3. Fleet remediation of inherited tools

**Finding:** a fleet sweep that hand-rolled its own workspace discovery, instead of calling
`fleet.discover_workspaces`, saw 15 workspaces where the tool finds 85. Measured with the full discovery, 34 Claude teams that had
never been updated carried **719 agents that inherited every tool**. An earlier report of
"0 across the fleet" was arithmetically true but used the wrong denominator.

**Remediation:** all 719 were remediated. The census after the fix, via `discover_workspaces`, was 43 Claude
teams, 1,170 agent files, none on the ignored key, and no in-scope deletions. 23 agents in 4 teams still
declared no capability key at all, which inherits everything just as the ignored key did. That needed a
decision about which grant they should carry, and was left open.

**Lesson:** this miss and two others found with it (descriptor resolution, and a readiness check that failed
on git worktrees) had one root cause. Each re-implemented a resolution the tool already provides.

**Commit:** `5790ca7` (2026-08-06).

---

## What this note leaves out

The public-release scrub of 2026-08-31, recorded in the repository's `.gitignore`, deliberately kept this
repository's red-team operational data private. This
note follows that boundary. It does **not** publish:

- payload text, or any model's reply to a payload;
- per-model scores, ratings or quoted failures;
- the findings log, the accepted-weaknesses list, or any per-probe weakness;
- the names of the fleet workspaces.

The figures above are the aggregates that those records support.
