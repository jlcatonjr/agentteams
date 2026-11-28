# `gate_catch_ledger`

A read-only reporter that derives, from the governance logs a team already keeps, how often each
process gate fired and how often it caught something. It exists so that the cost of a gate can be
weighed against what it catches. It adds no logging duty and writes nothing unless given `--output`.

It emits counts only, keyed by fixed labels. No cell of any log is copied into the output.

**Beta.** The functions, the report's keys and the classification rules may change in a minor release.

> *Source: `agentteams/gate_catch_ledger.py`*

## Layout

- **Module:** `agentteams.gate_catch_ledger` (importable)
- **CLI:** `python -m agentteams.gate_catch_ledger [--root DIR] [--framework ID | --team-dir DIR] [--security-log FILE] [--json] [--output FILE]`

## What it reads

| Source | Path | What it yields |
|---|---|---|
| Security decisions log | `<team dir>/references/security-decisions.log.csv`, or the file given with `--security-log` | `@security` verdicts. The one gate with a usable denominator: HALT and CONDITIONAL are catches, PASS-like verdicts are clean firings. |
| Conflict log | `<team dir>/references/conflict-log.csv` | `@conflict-auditor` findings (claimed catches). Clean audits are logged only sometimes, so there is no catch rate. |
| Plan step files | `<root>/tmp/by-week/<week>/*.steps.csv` | Steps naming a gate agent: a lower bound on firings. |

A source that is absent, unreadable, or holds nothing countable gives `None` for its section and is
reported as having no denominator. It is never reported as zero.

The team directory is the framework's agents directory, for example `.claude/agents`. Pass
`--framework` or `--team-dir` to choose it. With neither, it is detected; a repository holding more
than one team is refused, not guessed. A named directory that does not exist is a usage error.

## Limits

- A logged catch is a claim. Some audit findings are wrong.
- A clean record may be caused by the gate itself. Do not retire a gate on absence of catches alone.
- A re-recorded security verdict counts as another firing.
- Verdicts are classed by their leading word. Rows with no recognisable verdict are counted as unclassified.

## Exit status

`0` whatever the ledger shows (it reports; it does not gate). `1` when `--output` cannot be written.
`2` on a usage error, including an ambiguous or missing team directory. Error messages name
directories relative to `--root`, never an absolute path.

## Public Surface

### `rule_of_three(clean_firings)`

> *Source: `agentteams/gate_catch_ledger.py`*

Return the 95% upper bound on a catch rate after `clean_firings` firings with no catch:
`min(1.0, 3 / n)`, or `None` when `n` is not positive.

### `classify_security_status(status, decision='')`

> *Source: `agentteams/gate_catch_ledger.py`*

Map a security-log row to `halt`, `conditional`, `pass` or `other`. `decision` is read when `status`
holds bookkeeping or a bare "cleared", so a cleared row cannot hide a HALT or CONDITIONAL decision.
A row whose status is a retracted HALT is `other`, whatever its decision cell says.

### `security_ledger(rows)`

> *Source: `agentteams/gate_catch_ledger.py`*

Summarise `@security` verdicts: counts by class and month, the firing total, and the HALT and
CONDITIONAL rates over that total.

### `conflict_ledger(rows)`

> *Source: `agentteams/gate_catch_ledger.py`*

Summarise conflict-log rows: findings by severity label, status label and month; rows recording a
clean audit or note; findings whose resolution reads as a false catch. `firings` is always `None`.

### `gates_named(agent_cell)`

> *Source: `agentteams/gate_catch_ledger.py`*

Return the gate agents a plan step's `agent` cell names, tolerating spaces, underscores and capitals.

### `plan_ledger(root)`

> *Source: `agentteams/gate_catch_ledger.py`*

Count plan steps that name a gate agent, per ISO week, under `root`.

### `resolve_team_dir(root, framework=None, team_dir=None)`

> *Source: `agentteams/gate_catch_ledger.py`*

Find the team directory whose logs the ledger reads. With neither argument it detects the team and
returns `None` when no team directory holds a governance log. Raises `ValueError` for an unregistered
framework, for a named directory that does not exist, or when neither argument is given and more than
one team directory holds a governance log.

### `build_ledger(root, team_dir=None, security_log=None)`

> *Source: `agentteams/gate_catch_ledger.py`*

Build the whole ledger: `coverage`, `security`, `conflict_auditor` and `plans`. A source that is absent,
unreadable or holds nothing countable gives `None` for its section, with its state in `coverage`.

### `render_markdown(ledger)`

> *Source: `agentteams/gate_catch_ledger.py`*

Render a ledger as Markdown containing counts and fixed labels only.

### `main(argv=None)`

> *Source: `agentteams/gate_catch_ledger.py`*

Command-line entry point. Returns the exit status described above.

## Background reading

The method the `devops-bloat` agent applies to this ledger draws on the following. The first five were
fetched and checked against the claim noted; the last three are leads, not evidence.

1. Google, *The Site Reliability Workbook*, "Example Error Budget Policy" (S. Thurgood, 2018): gating tightens only when the error budget is spent. An example policy, not a normative one. <https://sre.google/workbook/error-budget-policy/>
2. Google, *Site Reliability Engineering*, ch. 5 "Eliminating Toil" (V. Rau, 2017): the definition of toil. <https://sre.google/sre-book/eliminating-toil/>
3. R. Wilsenach, "Ship / Show / Ask" (2021): the author of a change chooses how much review it gets. <https://www.martinfowler.com/articles/ship-show-ask.html>
4. D. Jaroslawicz, B. Whiting, P. Shah, K. Maamari, "How Many Instructions Can LLMs Follow at Once?" (2025): adherence falls as instruction count rises. Abstract only. <https://arxiv.org/abs/2507.11538>
5. M. Cemri et al., "Why Do Multi-Agent LLM Systems Fail?" (2025): 14 failure modes, including task verification. Abstract only. <https://arxiv.org/abs/2503.13657>
6. Regulatory offsetting ("one-in, x-out"): known only from search summaries of an OECD working paper. Not fetched.
7. A. Rae, D. Provan, D. Weber, S. Dekker, "Safety clutter" (2018): duplication, generalisation and over-specification as generators of clutter. The publisher's page refused retrieval; described from secondary summaries.
8. Team Topologies, "When DORA metrics meet governance in banking": a summary of DORA findings on formal change approval. Fetched, but it is a summary of the research, not the research.
