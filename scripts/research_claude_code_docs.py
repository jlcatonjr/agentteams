#!/usr/bin/env python3
"""Daily-pipeline upstream research stage for Claude Code sub-agents.

Thin CLI wrapper around `agentteams.framework_research.refresh_snapshot`.
Writes `tmp/daily-pipeline/framework-research/latest.json` plus a
date-stamped markdown report. Consumer repos do not run this script;
they consume the snapshot through `build_team.py --update --merge`
which calls `agentteams.framework_research.build_framework_placeholders`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentteams import framework_research  # noqa: E402


def _write_report(snapshot: dict) -> Path:
    date_dir = ROOT / "tmp" / "daily-pipeline" / "framework-research" / snapshot.get("generated_on", "unknown-date")
    date_dir.mkdir(parents=True, exist_ok=True)
    report = date_dir / "claude-code.md"
    tokens = snapshot.get("upstream_tokens", {})
    diff = snapshot.get("keys_diff", {})
    adapter = snapshot.get("local_adapter", {})
    lines = [
        "# Claude Code Sub-Agent Upstream Research",
        "",
        f"- generated_at: {snapshot.get('generated_at', '')}",
        f"- source_url: {snapshot.get('source_url', '')}",
        f"- fetch_status: {snapshot.get('fetch_status', '')}",
        "",
        "## Local adapter constants",
        f"- required_front_matter_keys: {adapter.get('required_front_matter_keys', [])}",
        f"- default_allowed_tools: {adapter.get('default_allowed_tools', [])}",
        "",
        "## Upstream tokens observed",
        f"- front_matter_keys_present: {tokens.get('front_matter_keys_present', [])}",
        f"- locations_present: {tokens.get('locations_present', [])}",
        "",
        "## Diff (required local vs observed upstream)",
        f"- matched: {diff.get('matched', [])}",
        f"- documented locally but not seen upstream: {diff.get('missing_upstream', [])}",
        f"- seen upstream but not in local required set: {diff.get('new_upstream', [])}",
        "",
    ]
    if snapshot.get("fetch_error"):
        lines += ["## Fetch error", "", f"`{snapshot['fetch_error']}`", ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Daily Claude Code docs research stage")
    parser.add_argument("--offline", action="store_true", help="Skip network fetch; reuse cached snapshot if present.")
    args = parser.parse_args(argv)
    snapshot = framework_research.refresh_snapshot(ROOT, offline=args.offline)
    _write_report(snapshot)
    diff = snapshot.get("keys_diff", {})
    print(
        f"[{snapshot.get('fetch_status', '?')}] matched={len(diff.get('matched', []))} "
        f"new={len(diff.get('new_upstream', []))} missing={len(diff.get('missing_upstream', []))}"
    )
    # Non-zero exit when snapshot is stale or fetch failed without prior cache.
    if snapshot.get("fetch_status") not in {"ok", "skipped"}:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
