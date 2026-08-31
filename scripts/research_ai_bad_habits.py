#!/usr/bin/env python3
"""AI bad-habits catalog sync stage.

Thin CLI wrapper around `agentteams.ai_bad_habits`. Regenerates the single
tracked artifact `references/ai-bad-habits-watch.md` from the curated catalog,
and (with `--propose`/`--apply`) drives the supervised-PR sync guard at
`.github/workflows/ai-bad-habits-watch.yml`. The catalog is curated and
version-controlled — there is no upstream watch (security taxonomies are
`@security`'s domain).

Usage:
    python scripts/research_ai_bad_habits.py            # regenerate tracked catalog
    python scripts/research_ai_bad_habits.py --propose  # write proposal.json (drift?)
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


def _cmd_refresh() -> int:
    path = ai_bad_habits.write_watch(ROOT)
    snap = ai_bad_habits.refresh_snapshot(ROOT)
    print(f"refreshed: {path.relative_to(ROOT)} (hash {ai_bad_habits.content_hash(snap)})")
    return 0


def _cmd_propose() -> int:
    proposal = ai_bad_habits.propose_watch_patch(ROOT)
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
    parser = argparse.ArgumentParser(description="AI bad-habits catalog sync stage")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--propose", action="store_true", help="Write a patch proposal to tmp/.")
    group.add_argument("--apply", action="store_true", help="Apply the written proposal in-place.")
    args = parser.parse_args(argv)

    if args.propose:
        return _cmd_propose()
    if args.apply:
        return _cmd_apply()
    return _cmd_refresh()


if __name__ == "__main__":
    raise SystemExit(main())
