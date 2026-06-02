#!/usr/bin/env python3
"""Daily-pipeline AI bad-habits watch stage.

Thin CLI wrapper around `agentteams.ai_bad_habits`. Refreshes the single tracked
artifact `references/ai-bad-habits-watch.md`, and (with `--propose`/`--apply`)
drives the supervised-PR workflow at `.github/workflows/ai-bad-habits-watch.yml`.

Usage:
    python scripts/research_ai_bad_habits.py            # refresh tracked watch (offline)
    python scripts/research_ai_bad_habits.py --online   # probe upstream editions
    python scripts/research_ai_bad_habits.py --propose --online   # write proposal.json
    python scripts/research_ai_bad_habits.py --apply     # apply proposal in-place

Mirrors scripts/research_claude_code_docs.py and the scope-guard of
scripts/run_daily_security_maintenance.sh.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentteams import ai_bad_habits  # noqa: E402

_PROPOSAL_PATH = ROOT / "tmp" / "daily-pipeline" / "ai-bad-habits" / "proposal.json"


def _require_agentteams_root() -> None:
    """Hard scope guard — this stage only runs in the agentteams repo root."""
    if not (ROOT / "build_team.py").is_file() or not (ROOT / "agentteams").is_dir() or ROOT.name != "agentteams":
        print(f"[CRITICAL] Refusing to run outside agentteams repository root: {ROOT}", file=sys.stderr)
        raise SystemExit(2)


def _cmd_refresh(offline: bool) -> int:
    path = ai_bad_habits.write_watch(ROOT, offline=offline)
    snap = ai_bad_habits.refresh_snapshot(ROOT, offline=offline)
    drifting = [s["id"] for s in snap["sources"] if s.get("freshness") == "drift-suspected"]
    print(f"refreshed: {path.relative_to(ROOT)} (hash {ai_bad_habits.content_hash(snap)})")
    if drifting:
        print(f"drift suspected: {', '.join(drifting)}")
    return 0


def _cmd_propose(offline: bool) -> int:
    proposal = ai_bad_habits.propose_watch_patch(ROOT, offline=offline)
    _PROPOSAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROPOSAL_PATH.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    n = len(proposal.get("changes", []))
    print(f"proposal: {n} change(s), hash {proposal.get('dedup_hash')} → {_PROPOSAL_PATH.relative_to(ROOT)}")
    return 0


def _cmd_apply() -> int:
    if not _PROPOSAL_PATH.exists():
        print("no proposal.json — run --propose first", file=sys.stderr)
        return 2
    proposal = json.loads(_PROPOSAL_PATH.read_text(encoding="utf-8"))
    result = ai_bad_habits.apply_watch_patch(proposal, ROOT)
    print(f"applied: {result.get('applied', [])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _require_agentteams_root()
    parser = argparse.ArgumentParser(description="AI bad-habits watch daily stage")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--propose", action="store_true", help="Write a patch proposal to tmp/.")
    group.add_argument("--apply", action="store_true", help="Apply the written proposal in-place.")
    parser.add_argument("--online", action="store_true", help="Probe upstream sources (default offline).")
    args = parser.parse_args(argv)

    offline = not args.online
    if args.propose:
        return _cmd_propose(offline=offline)
    if args.apply:
        return _cmd_apply()
    return _cmd_refresh(offline=offline)


if __name__ == "__main__":
    raise SystemExit(main())
