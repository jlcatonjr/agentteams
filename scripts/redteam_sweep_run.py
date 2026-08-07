#!/usr/bin/env python3
"""redteam_sweep_run.py — red-team every agent, on every framework, from freshly built trees.

Ties together the three pieces:

* :mod:`agentteams.redteam.instantiate` — generate goose, claude and copilot-vscode trees from
  the **canonical brief**, in an isolated root. Not chained through goose: that adapter caps
  delegation at one layer, so deriving the others from it would measure a degraded copy.
* :mod:`agentteams.redteam.sweep` — enumerate targets, choose the slice, and score each agent
  against the criterion it can fairly be held to.
* :mod:`agentteams.redteam.findings_ledger` — promote failures into the tracked ledger.

Scoring, restated because it is the part that is easy to get wrong: **compliance is scored for
every agent** (C-4 binds all of them), while ``HALT``/``REPORT`` escalation is scored **only for
agents whose file actually carries the security verdict contract** — 1 of 30 in this repository.
Holding the other 29 to a contract they were never issued would report a fake collapse.

Usage::

    python3 scripts/redteam_sweep_run.py --mode full      # every target (weekly)
    python3 scripts/redteam_sweep_run.py --mode rotation  # today's slice (daily)
    python3 scripts/redteam_sweep_run.py --mode rotation --per-day 3 --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.redteam import findings_ledger as fl  # noqa: E402
from agentteams.redteam import instantiate, sweep  # noqa: E402
from tests.redteam.run_harness import load_corpus  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from redteam_judgment_run import (  # noqa: E402
    MIN_REMAINING_USD, read_remaining_credit, run_payload, validate_model_slug,
)

#: Per-call cost measured 2026-08-07. Used only to SIZE the budget before spending; the
#: authoritative figure is always the provider ledger delta.
COST_PER_CALL_USD = 0.0013


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="z-ai/glm-5.2")
    parser.add_argument("--mode", choices=("full", "rotation"), default="rotation")
    parser.add_argument("--per-day", type=int, default=3)
    parser.add_argument("--frameworks", default=",".join(instantiate.DEFAULT_FRAMEWORKS))
    parser.add_argument("--limit-targets", type=int, default=0, help="bound the run (testing)")
    parser.add_argument("--payload", default="", metavar="ID",
                        help="run only this payload id — for re-testing one finding")
    parser.add_argument("--agents", default="", metavar="SLUGS",
                        help="comma-separated agent slugs to restrict the sweep to")
    parser.add_argument("--repeat", type=int, default=1,
                        help="repetitions per agent/payload pair. A stochastic measurement "
                             "repeated once is still one sample.")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=6,
                        help="targets measured in parallel; 1 is the old sequential path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    token = os.environ.get("OPENROUTER_API_KEY", "")
    if not token:
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 2
    ok, message = validate_model_slug(args.model)
    print(f"  model      : {message}")
    if not ok:
        return 2

    frameworks = tuple(f.strip() for f in args.frameworks.split(",") if f.strip())
    dest = Path(tempfile.mkdtemp(prefix="redteam-trees-"))
    print(f"  building   : {', '.join(frameworks)} from the canonical brief -> {dest}")
    inst = instantiate.instantiate_all(
        source_root=REPO_ROOT,
        brief=REPO_ROOT / ".github" / "agents" / "_build-description.json",
        dest_root=dest, frameworks=frameworks,
    )
    for name, tree in sorted(inst.trees.items()):
        print(f"    {name:<16} {len(tree.agent_files):>3} agents")
    if not inst.ok:
        print("  INSTANTIATION FAILED — not a measurement:", file=sys.stderr)
        for problem in inst.problems():
            print(f"    - {problem}", file=sys.stderr)
        return 2

    targets = sweep.enumerate_targets(
        {name: tree.agent_files for name, tree in inst.trees.items()}
    )
    today = datetime.date.today()
    if args.mode == "rotation":
        targets = sweep.rotation_slice(targets, day=today, per_day=args.per_day)
    if args.limit_targets:
        targets = targets[: args.limit_targets]

    corpus = load_corpus()
    if args.payload:
        corpus = [p for p in corpus if p["id"] == args.payload]
        if not corpus:
            print(f"  no payload with id {args.payload!r}", file=sys.stderr)
            return 2
    if args.agents:
        wanted = {s.strip() for s in args.agents.split(",") if s.strip()}
        targets = [t for t in targets if t.agent in wanted]
        missing = wanted - {t.agent for t in targets}
        if missing:
            # Silently sweeping fewer agents than asked for is a shrunken denominator.
            print(f"  requested agents not present in any tree: {sorted(missing)}",
                  file=sys.stderr)
            return 2
    planned = len(targets) * len(corpus) * max(1, args.repeat)
    budget = max(0.50, planned * COST_PER_CALL_USD * 3)
    carriers = sum(1 for t in targets if t.contract)
    print(f"  targets    : {len(targets)} ({carriers} carry the security verdict contract)")
    print(f"  payloads   : {len(corpus)}  -> {planned} calls, budget cap ${budget:.2f}")
    print(f"  provenance : brief={inst.brief_digest} head={inst.git_head}")

    if args.dry_run:
        print("  --dry-run: nothing spent.")
        return 0

    before = read_remaining_credit(token)
    if before is None or before < MIN_REMAINING_USD:
        print(f"  Refusing to start: credit {before} below ${MIN_REMAINING_USD}", file=sys.stderr)
        return 2

    workdir = dest / "_prompts"
    workdir.mkdir(parents=True, exist_ok=True)
    results: list[sweep.TargetResult] = []
    failures: list[fl.Finding] = []

    def measure(target: sweep.Target) -> tuple[sweep.TargetResult, list[fl.Finding]]:
        """Measure one target across the whole corpus. Pure per-target, so it parallelises."""
        contract_text = sweep.agent_system_prompt(target.path)
        outcome = sweep.TargetResult(target=target)
        found: list[fl.Finding] = []
        # Each target gets its own prompt directory: run_payload writes <pid>.prompt.txt, and
        # two workers on the same directory would overwrite each other's prompt between the
        # write and the read — a race that would silently measure the wrong payload.
        target_dir = workdir / f"{target.framework}__{target.agent}"
        target_dir.mkdir(parents=True, exist_ok=True)
        for payload in corpus * max(1, args.repeat):
            r = run_payload(args.model, payload, target_dir, args.timeout, contract_text)
            if r.observed == "NO-CALL":
                outcome.errors.append(f"{payload['id']}: {r.error}")
                continue
            verdict = sweep.score_for_target(target, payload, r.observed, r.acceptable)
            getattr(outcome, verdict).append(payload["id"])
            if verdict != "clean":
                found.append(fl.Finding(
                    layer="judgment", finding_id=payload["id"], interface="goose",
                    model=args.model, expected=payload["expected"], observed=r.observed,
                    framework=target.framework, agent=target.agent,
                ))
        return outcome, found

    # Bounded concurrency. Sequential throughput was measured at 9.5s/call, so the full 1,218
    # -call sweep took 3.2 HOURS — not a run anyone sits through, and a 3-hour unattended
    # Monday job that any interruption kills with nothing recorded.
    #
    # The budget is NOT re-checked per target here. With N workers in flight the ledger read
    # lags N targets of spend, and the provider's own ledger lags further still — the first
    # sweep reported $0.0000 while genuinely spending. So the cap is enforced on the planned
    # call count BEFORE starting and between batches; the ledger delta stays the authoritative
    # post-hoc figure.
    batch = max(1, args.concurrency)
    completed = 0
    for start in range(0, len(targets), batch):
        chunk = targets[start:start + batch]
        with concurrent.futures.ThreadPoolExecutor(max_workers=batch) as pool:
            for outcome, found in pool.map(measure, chunk):
                results.append(outcome)
                failures.extend(found)
                completed += 1
                flag = "" if outcome.ok else "  <-- FAILURES"
                noise = f"  ({len(outcome.errors)} NO-CALL)" if outcome.errors else ""
                print(f"  [{completed}/{len(targets)}] {outcome.target.label:<38} "
                      f"complied={len(outcome.complied)} "
                      f"misesc={len(outcome.misescalated)}{noise}{flag}", flush=True)
        spent = before - (read_remaining_credit(token) or before)
        if spent > budget:
            print(f"  ABORTING after {completed} targets: ${spent:.4f} exceeded the "
                  f"${budget:.2f} cap.", file=sys.stderr)
            break

    after = read_remaining_credit(token)
    promotion = fl.promote(REPO_ROOT, failures, today=today.isoformat())

    print()
    print("\n".join(sweep.render_counts(results, payloads=len(corpus), repeat=max(1, args.repeat))))
    no_call = sum(len(r.errors) for r in results)
    if no_call:
        print(f"\n  UNMEASURED: {no_call} of {len(results) * len(corpus)} agent/payload "
              f"pairs returned NO-CALL (rate limits or transport). Stated rather than "
              f"dropped — a shrunken denominator that goes unreported is the F-4 defect.")
    print()
    print(f"  ledger     : {len(promotion.added)} new, {len(promotion.transitioned)} changed")
    if before is not None and after is not None:
        print(f"  cost       : ${before - after:.4f} (provider ledger, authoritative)")
    (dest / "sweep.json").write_text(json.dumps({
        "provenance": inst.to_dict(),
        "targets": [t.label for t in targets],
        "failures": [f"{f.framework}/{f.agent}/{f.finding_id}" for f in failures],
    }, indent=2), encoding="utf-8")
    print(f"  artifacts  : {dest}")

    return 1 if any(r.complied for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
