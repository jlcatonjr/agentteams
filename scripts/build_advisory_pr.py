#!/usr/bin/env python3
"""Generate today's advisory file and print PR metadata for the workflow.

Reads the in-repo advisory detectors via `agentteams.advisory`. When
findings exist:
  1. Writes `references/advisories/<today>.md` (tracked).
  2. Prints `findings=true` plus `hash=<12hex>` and `path=<rel>` to stdout
     in GitHub Actions output format (one per line).
When findings are empty: prints `findings=false` and exits 0 without
writing.

Plan: references/plans/rc6-advisory-pr-pattern-2026-05-27.plan.md
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentteams import advisory  # noqa: E402


def main(argv: list[str]) -> int:
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    body = advisory.aggregate(today=today)

    out_lines: list[str] = []

    if not body:
        out_lines.append("findings=false")
        print("\n".join(out_lines))
        # Also write to GITHUB_OUTPUT if available (for the workflow).
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            with open(gh_output, "a", encoding="utf-8") as fh:
                fh.write("\n".join(out_lines) + "\n")
        return 0

    advisories_dir = ROOT / "references" / "advisories"
    advisories_dir.mkdir(parents=True, exist_ok=True)
    rel = f"references/advisories/{today}.md"
    out_path = ROOT / rel
    out_path.write_text(body, encoding="utf-8")
    h = advisory.hash_body(body)

    out_lines.extend([
        "findings=true",
        f"hash={h}",
        f"path={rel}",
        f"today={today}",
    ])
    print("\n".join(out_lines))
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write("\n".join(out_lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
