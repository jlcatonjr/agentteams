#!/usr/bin/env python3
"""Post daily PR reminders. Invoked by .github/workflows/pr-reminders.yml.

Wraps `agentteams.pr_management` so the workflow stays thin (mirrors
`scripts/build_advisory_pr.py`). Supports `--dry-run` for offline validation.

Plan: tmp/by-week/2026-W22/pr-management-agent-system-2026-05-27.plan.md
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentteams import pr_management  # noqa: E402


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    rc = pr_management.main(["remind"] + (["--dry-run"] if dry_run else []))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        registry = pr_management.load_registry()
        unconfirmed = [r.login for r in registry.recipients
                       if not r.opt_out and not r.notifications_confirmed]
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## PR reminders\n\n")
            fh.write(f"- dry-run: `{dry_run}`\n")
            fh.write(f"- recipients: {len(registry.recipients)} "
                     f"(active: {len(registry.active())})\n")
            if unconfirmed:
                fh.write(f"- ⚠ unconfirmed notification settings: "
                         f"{', '.join(unconfirmed)}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
