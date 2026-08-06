# Documentation Refresh Procedure

**Status:** active · **Owner:** `@orchestrator` (routes), `@technical-validator` (detects),
`@agent-updater` (applies) · **Established:** 2026-08-06

**Origin:** the 2026-08-06 documentation staleness audit. The report is retained locally
at `references/plans/documentation-staleness-audit-2026-08-06.report.md`, which is
**gitignored by design** (`references/filing-conventions.md`) — so a reader of this
repository will not find it. Its findings are summarised below and in the CHANGELOG.
**Trigger:** `.github/workflows/docs-freshness-watch.yml` (+ watchdog, + daily driver)
**Detector:** `scripts/docs_freshness_watch.py`
**Tests:** `tests/test_docs_freshness_watch.py`

---

## 1. Why this exists

The 2026-08-06 staleness audit found a clean split: **every documentation surface with a
standing check was current, and every surface without one had drifted.** `agentteams.1` was
90/90 flags because CI diffs it. `docs_src/cli-reference.md` documented the same surface,
claimed to be exhaustive, and was 19 flags short — because nothing checked it.

The audit's conclusion was that fixing the 19 flags fixes today and nothing else. This
procedure is the part that fixes tomorrow.

It does two things a check alone cannot:

- It **names the work per document**, so "the docs are stale" becomes a specific diff
  against a specific authority.
- It **runs on a trigger**, so the work is not gated on someone remembering.

---

## 2. The trigger

```
FIRE  iff  docs_age > 24h  AND  src_age <= 24h
```

`docs_age` and `src_age` are the ages of the most recent commits touching the documentation
set and the source set. Both sets are derived from `git ls-files` by what a path *is* —
never from a declared list, because a declared list is itself an artifact that goes stale.

**In words: source moved today, documentation did not.**

### Trigger channels

| Channel | File | Cadence | Catches |
|---|---|---|---|
| Push | `docs-freshness-watch.yml` | every push to `main` | `src_age` → 0 while `docs_age` already past the window |
| Cron | `docs-freshness-watch.yml` | every 6h | `docs_age` crossing the window with no new push |
| Manual | `docs-freshness-watch.yml` | `workflow_dispatch` | recovery, ad-hoc |
| Daily driver | `scripts/run_daily_bridge_maintenance.sh` | daily, different workflow | the watcher's own workflow being disabled or deleted |
| Watchdog | `docs-freshness-watchdog.yml` | daily, different cron | the watcher not running at all |

Push and cron are **both load-bearing**. A push-only trigger cannot observe the window
rolling over with no new commit; a cron-only trigger delays the common case by up to 6h.

### Running it by hand

```bash
python3 scripts/docs_freshness_watch.py            # verdict, human-readable
python3 scripts/docs_freshness_watch.py --json     # verdict, machine-readable
python3 scripts/docs_freshness_watch.py --check    # exit 2 if firing (opt-in)
python3 scripts/docs_freshness_watch.py --window 48
```

It always exits 0 except under `--check`. It is **advisory** — the same instrument class as
`scripts/check_session_obligations.py`, for the same reason: it reports absent evidence, not
violation, and it cannot see that a doc was reviewed and correctly judged to need no change.

---

## 3. The procedure

Two stages. **Stage 1 is mechanical and must be run first** — it decides how much of
Stage 2 is real work.

### Stage 1 — Mechanical (scripted, no judgment)

Run in this order. Each command is the authority for the surface beneath it.

```bash
# 1.1  Man page — regenerate and diff (this is what CI does)
python3 -m agentteams.man > /tmp/man-check.1
diff /tmp/man-check.1 agentteams.1 || python3 -m agentteams.man > agentteams.1

# 1.2  CLI flag parity — parser vs every doc surface
python3 - <<'PY'
import re, pathlib, importlib
p = importlib.import_module('agentteams.cli.parser')._build_parser()
flags = {s for a in p._actions for s in a.option_strings if s.startswith('--')} - {'--help'}
doc = lambda f: set(re.findall(r'(--[a-z0-9][a-z0-9\-]*)', pathlib.Path(f).read_text()))
for f in ('docs_src/cli-reference.md', 'README.md', 'agentteams.1'):
    missing = sorted(flags - doc(f))
    print(f"{f}: {len(flags)-len(missing)}/{len(flags)}  missing={missing}")
PY

# 1.3  API-reference parity: no page may document a deleted module
python3 scripts/check_api_doc_parity.py

# 1.4  Nav completeness: every page reachable, every nav entry real
python3 - <<'PY'
import re, pathlib
nav = set(re.findall(r':\s*([A-Za-z0-9_\-/]+\.md)\s*$', pathlib.Path('mkdocs.yml').read_text(), re.M))
files = {str(p.relative_to('docs_src')) for p in pathlib.Path('docs_src').rglob('*.md')}
print("not in nav:", sorted(files - nav))
print("nav points at missing:", sorted(nav - files))
PY

# 1.5  Internal links
python3 - <<'PY'
import re, pathlib
bad = []
for p in pathlib.Path('docs_src').rglob('*.md'):
    for m in re.finditer(r'\[[^\]]*\]\(([^)#\s]+\.md)(?:#[^)]*)?\)', p.read_text(errors='ignore')):
        if not m.group(1).startswith(('http','mailto')) and not (p.parent/m.group(1)).resolve().exists():
            bad.append((str(p), m.group(1)))
print("broken:", len(bad), bad)
PY

# 1.6  The tool's own staleness detector, plus the doc ratchets
python3 build_team.py --stale-check --self
python3 -m pytest tests/test_module_doc_ratchet.py tests/test_api_doc_parity.py \
                  tests/test_published_artifacts_have_checks.py -q

# 1.7  Generated instruction files — brief is the source, the file is fenced
#      Fix .github/agents/_build-description.json FIRST, then:
python3 build_team.py --self --update --merge
```

**1.7 warning.** `.claude/CLAUDE.md`, `AGENTS.md` and `.github/copilot-instructions.md` carry
`AGENTTEAMS:BEGIN` fences. Hand-editing a fenced region is overwritten on the next update.
Fix the brief, then update. Use `--update --merge`, **never** `--overwrite` — `--overwrite`
has been observed reusing a stale manifest and not applying brief edits.

**1.7 does not currently work for placeholder-only edits — verify it by hand.** A brief edit
that changes only *placeholder values* (a path, a name) does not propagate to fenced regions:
on 2026-08-06 three corrected path fields produced "No structural or template-content changes
detected" and the fence kept the old values. This is not a stale manifest —
`analyze.build_manifest` re-read from disk and resolved the new values correctly — so the loss
is downstream, in the drift/merge decision, which appears to compare template content and
structure but not resolved placeholder values.

**The run reports success, so the failure is silent.** Until this is fixed, after 1.7 always
diff the fenced block against what the brief now claims:

```bash
python3 -c "
from pathlib import Path
from agentteams import ingest, analyze
d = ingest.load(Path('.github/agents/_build-description.json'))
m = analyze.build_manifest(d, framework='copilot-vscode')
for k in ('primary_output_dir','build_output_dir','figures_dir','reference_db_path'):
    print(f'{k:22} = {m.get(k)!r}')
"
grep -A 8 'BEGIN directory_structure' .claude/CLAUDE.md
```

If they disagree, write the manifest's values into the fenced block. That is
idempotent-compatible — the next *working* regeneration produces the same content — but it is
exactly the hand-editing the fence system exists to make unnecessary. Tracked in
`references/agentteams-remediation-log.csv` (2026-08-06).

### Stage 2 — Authored (judgment required, routed)

Stage 1 produces a defect list. Work it in this order — the ordering is by blast radius,
not by effort.

| # | Surface | Authority it must match | How to tell it drifted | Route to |
|---|---|---|---|---|
| 2.1 | `docs_src/cli-reference.md` | `agentteams.cli.parser._build_parser()` | 1.2 reports missing flags | `@cli-and-examples-expert` → `@primary-producer` |
| 2.2 | `README.md` — framework list | `agentteams.frameworks.registry.FRAMEWORKS` | a registry key absent from the README | `@framework-adapters-expert` |
| 2.3 | `README.md` — CLI block | the parser | 1.2 reports missing flags | `@cli-and-examples-expert` |
| 2.4 | `docs_src/api-reference/*.md` | `agentteams/**/*.py` | 1.3 + `test_api_doc_signatures.py` | `@technical-validator` → `@agent-updater` |
| 2.5 | `mkdocs.yml` nav | `docs_src/**/*.md` | 1.4 reports pages not in nav | `@navigator` |
| 2.6 | `.claude/CLAUDE.md`, `AGENTS.md` | `.github/agents/_build-description.json` | a table row naming a path that does not exist | `@agent-updater` (via 1.7) |
| 2.7 | `docs_src/getting-started.md`, `how-it-works.md`, `examples.md` | observed behaviour | a documented command's real output differs | `@technical-validator` |
| 2.8 | `SECURITY.md`, `STABILITY.md` | `agentteams/scan.py`, the flag surface | a policy claim no longer true of the code | `@security` |
| 2.9 | `CHANGELOG.md` | the commits | `changelog-link.yml` already gates this | — (enforced) |

**On 2.9 and the trigger.** `CHANGELOG.md` is listed here because the procedure covers it,
but it is deliberately **excluded from the freshness set** that computes `docs_age`. It is
already enforced by a failing gate, and counting it as documentation suppressed 62% of the
trigger's signal — see finding A-7 below.

**2.7 is the audit's own blind spot and is deliberately last.** The 2026-08-06 audit was
mechanical on flags, paths, counts, links, and nav; it did *not* execute a documented command
and compare its output. Semantic drift of that kind is the most expensive to find and the
least likely to be caught by any check in Stage 1. Treat a Stage 2.7 item as unverified
until someone has actually run the command.

### Stage 3 — Close out

1. If any doc changed, add a `CHANGELOG.md` entry. (`changelog-link.yml` requires one for
   any PR touching `agentteams/**/*.py`; a docs-only PR does not need one, but a docs fix
   that reveals a code defect does.)
2. Run `@conflict-auditor` — Constitutional Rule 8 requires it after any multi-file change.
3. If a defect found here reveals a gap in the *tool* rather than in this repo's docs, log
   it to `references/agentteams-remediation-log.csv` (Constitutional Rule 11).
4. Close the `docs-freshness` issue, or leave it — the next evaluation closes it
   automatically once the condition goes false.

### The legitimate no-op

**A firing trigger is not proof that a document is wrong.** Source can move without
implicating any doc — a test-only commit, an internal refactor, a dependency bump. Stage 1
returning clean and Stage 2 finding nothing is a correct and expected outcome.

Record it by closing the issue with a one-line reason. Do not edit a doc to silence the
watcher; a cosmetic commit to reset `docs_age` is exactly the metric-gaming the audit
warned about, and it destroys the signal for the next real drift.

---

## 4. Reliability contract

The explicit requirement was: **a failed trigger must not prevent future activation of the
trigger.** The design answer is that the verdict is a pure function of git history and the
wall clock — it reads no persisted state and writes none that participates in the verdict.

Everything below follows from that one property.

| Failure | Why the trigger still fires next time |
|---|---|
| Scheduled run dropped, throttled, or cancelled | Nothing was recorded; the next run recomputes and sees the same true condition |
| The detector crashes | Verdict is `indeterminate`, routed to a **distinct** "watcher broken" issue. Indeterminate is never treated as a pass |
| `git` unavailable in the runner | Same as above — `GitUnavailable` is folded into the verdict, never mapped onto "no drift" |
| The remediation PR is never merged | Docs are still stale, so the condition is still true, so it fires again |
| The alert issue is closed without a fix | Dedupe is by label+title and suppresses duplicate *issues* only; the next evaluation reopens |
| The alert issue is left open forever | Same — an open issue never suppresses evaluation, only issue creation |
| The watcher workflow is disabled or deleted | The watchdog (separate file, separate cron) opens an issue; the daily driver still runs the detector |
| GitHub disables crons after 60d inactivity | 60d inactivity ⇒ `src_age > 24h` ⇒ the condition is false anyway. The first commit that resumes activity fires the push trigger |
| A doc set collapses to zero (derivation regression) | Returns `indeterminate` with an explicit "the derivation regressed" reason, not a silent pass |

### Three prohibitions

Asserted by `tests/test_docs_freshness_watch.py`. Each closes a way this design could be
undone by a well-meaning later edit:

1. **No `concurrency:` with `cancel-in-progress` on the watcher.** A cancelled run leaves no
   verdict, and on a busy push day that would be most runs.
2. **No state file that a later run reads back.** Any such file is a way for one bad run to
   poison every later run — the exact failure this design exists to avoid.
3. **The reporting steps stay `if: always()`.** A detector failure must still produce a
   report; a failure that reports nothing is indistinguishable from a pass.

### The second failure class: a trigger that never fires

Everything above answers *"can one failed run latch the trigger off?"* There is a second way
a watcher dies, and it produces no failed runs at all: the condition is technically live but
practically unsatisfiable, so every run is green and nothing is ever reported. That is
strictly worse than a broken watcher, because a broken one eventually gets noticed.

This design hit exactly that (finding A-7 — `CHANGELOG.md` acting as a freshness alibi), and
it was found by **backtesting the condition against real history**, not by reading the code.
The guard is procedural rather than mechanical:

- Any change to the documentation or source set must be backtested over ≥90 days at the real
  cadence before it lands, and the firing rate recorded.
- A firing rate of zero over a period with known documentation drift is a **defect in the
  watcher**, not evidence of healthy documentation. The 2026-08-06 audit is the ground truth
  for the current baseline: it found real drift in a period the pre-fix condition would have
  reported clean.

### Where the regress stops

Nothing watches the watchdog. Two independent workflows failing silently at the same time is
the residual risk, and it is **accepted, not solved**. The mitigations are that the two run
from different files on different crons, that the daily driver provides a third execution
path, and that the watchdog's output is a GitHub issue — a channel a human already reads.

Stating this is part of the contract. A reliability section that claimed full coverage would
be the least reliable thing in the document.

---

## 5. What this procedure does *not* cover

- **It does not verify behaviour.** Stage 1 is mechanical; Stage 2.7 is the only behavioural
  item and it is manual. No stage executes a documented command and diffs its real output.
- **It does not judge prose quality.** Route that to `@quality-auditor`.
- **It does not detect a doc that was always wrong.** The trigger is differential — it
  compares doc mtime to source mtime. A document that has been wrong since the day it was
  written has a fresh `docs_age` and never fires. Only an audit finds those; the
  2026-08-06 report is the current baseline and should be re-run periodically.
- **It does not replace the R-1/R-2 parity tests** recommended by the audit. Those make the
  defect fail the build; this makes someone look. Both are wanted, and this procedure gets
  strictly cheaper once they exist, because Stage 1 shrinks to running the suite.

---

## 6. Reliability audit — findings and revisions

This procedure and its implementation were reviewed adversarially before filing, against two
questions: *"find a way one failed run latches the trigger off"* and *"find a way this never
fires at all."* Nine findings, all applied. The second question found the worst one.

**A-7 — `CHANGELOG.md` in the documentation set made the trigger nearly unsatisfiable.**
This is the most serious finding and it was found by backtesting, not by reading.
`changelog-link.yml` fails any PR touching `agentteams/**/*.py` that does not also change
`CHANGELOG.md` — so on this repository a changelog edit accompanies nearly every code
change. With `CHANGELOG.md` counted as documentation, `docs_age` collapsed onto `src_age`
and the condition `docs_age > 24h AND src_age <= 24h` became almost unreachable. The first
live run showed the signature plainly: both ages 60.18h, both from the same commit.

Backtested over 90 days at the real 6-hour cadence (361 evaluations):

| Documentation set | Firings | Rate |
|---|---|---|
| with `CHANGELOG.md` | 13 | 3.6% |
| without `CHANGELOG.md` | 34 | 9.4% |

Including it suppressed **62% of the signal**. **Fixed:** `CHANGELOG.md` is inert — neither
documentation nor source. It is not documentation of current behaviour (it is a record of
change), and it is not a code change owing a doc update. Nothing is lost, because it is the
one doc surface already covered by a failing gate.

The general lesson is worth keeping: *a freshness signal is only as good as the set it is
computed over, and a set that includes a file something else already forces you to touch is
not measuring what you think.* Any future addition to the documentation set should be
backtested the same way before it lands.

**A-8 — Point-in-time artifacts counted as documentation freshness.**
`references/*.conflict-audit.md` and `*.adversarial.md` are written once and never
maintained forward. Committing a session's audit note would have reset `docs_age` for 24h
while saying nothing about whether the maintained docs track the code. **Fixed:** excluded
by suffix, the same argument that already excluded `references/plans/`.

**A-9 — Shipped artifacts cited a gitignored path.**
The detector, the workflow, and this procedure all carried a `Plan: tmp/by-week/...`
reference. `tmp/` is gitignored in full, so every reader of the shipped files would have
followed a dead link — the class of defect `scripts/check-durable-tmp-refs.sh` exists to
catch, in files outside that lint's scope (it covers `build_team.py`, `agentteams/*.py`,
`CHANGELOG.md`, `schemas/*.json`). **Fixed:** they cite the durable origin report instead.
The working plan remains in `tmp/by-week/` as Rule 9 evidence, which is where it belongs.

The six statelessness findings follow.

**A-1 — `git log` over an enumerated path list could exceed `ARG_MAX`.**
The first implementation passed all 768 source paths as a single argv, with a comment
claiming it batched through stdin. It did not. On a larger tree the call would fail, and
`GitUnavailable` would render the watcher permanently indeterminate — a latched-off trigger
by exactly the route the design forbids. **Fixed:** calls chunk at 1000 paths and reduce by
max, so the answer does not depend on the batch count.

**A-2 — A shallow clone would silently invert the verdict.**
`actions/checkout` defaults to `fetch-depth: 1`. Every path's last-touching commit would
resolve to the graft commit, making `docs_age` and `src_age` both ≈0 and the condition
permanently false — a silent, total failure with a green run. **Fixed:** `fetch-depth: 0`,
with the reason stated at the call site so it is not "optimised" away.

**A-3 — An empty derived set read as "nothing changed".**
If the derivation regressed (an exclusion prefix typo, a `git ls-files` change), both sets
could come back empty and the condition would evaluate cleanly to false forever. **Fixed:**
zero docs or zero sources returns `indeterminate` with an explicit "the derivation
regressed, not that nothing changed" reason.

**A-4 — A detector crash was being reported as a passing run.**
The first workflow draft had `continue-on-error: true` on the detector and no failure path,
so a crash produced a green run and no issue. The watchdog would see a healthy run and stay
quiet. **Fixed:** the indeterminate branch opens a distinct `watcher broken` issue, and a
final step fails the run *after* all `if: always()` reporting has completed — so the
watchdog sees the failure and no report was lost to it.

**A-5 — Author date vs committer date.**
`git log --format=%at` would let a rebased or cherry-picked commit carry an arbitrarily old
timestamp, understating `src_age` and suppressing legitimate fires. **Fixed:** `%ct`
throughout, with the reason in the docstring.

**A-6 — A cosmetic side-effect could change the exit code.**
`--out` writing to an unwritable path would have raised, turning a successful evaluation
into a crash. **Fixed:** the write is wrapped; a failure warns on stderr and changes neither
the verdict nor the exit code.

### Consistency findings (`@conflict-auditor` pass)

**C-1 — A field named `stale_docs` held source paths.** Renamed to `recent_source_paths`.
The old name would have made the alert body read as a list of stale documents, which is a
different and wrong claim.

**C-2 — The procedure claimed `--check` was used by CI.** It is not, and it must not be —
that would convert an advisory instrument into a gate, contradicting the `advisory` framing
in the same document. Corrected to "opt-in; nothing in CI gates on this."

**C-3 — Stage 1.7 originally said to edit `.claude/CLAUDE.md` directly.** The block is
fenced and would be overwritten. Corrected to fix the brief first, with the
`--update` / never-`--overwrite` note attached.
