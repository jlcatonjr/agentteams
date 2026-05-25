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


def _cmd_propose() -> int:
    import json as _json

    proposal = framework_research.propose_module_patch(ROOT)
    out_path = ROOT / "tmp" / "daily-pipeline" / "framework-research" / "proposal.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    n = len(proposal.get("changes", []))
    print(f"proposal: {n} change(s) → {out_path}")
    return 0


def _cmd_apply() -> int:
    import json as _json
    import subprocess

    proposal_path = ROOT / "tmp" / "daily-pipeline" / "framework-research" / "proposal.json"
    if not proposal_path.exists():
        print("no proposal.json — run --propose first", flush=True)
        return 2
    proposal = _json.loads(proposal_path.read_text(encoding="utf-8"))
    if not proposal.get("changes"):
        print("proposal has no changes; nothing to apply")
        return 0

    target = ROOT / framework_research.EXPERT_REF_REL
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    result = framework_research.apply_module_patch(proposal, ROOT)
    print(f"applied: {result['applied']}")

    test_cmd = ["python", "-m", "pytest", "-q", "tests/test_frameworks.py"]
    print(f"running: {' '.join(test_cmd)}")
    proc = subprocess.run(test_cmd, cwd=ROOT)
    if proc.returncode != 0:
        target.write_text(original, encoding="utf-8")
        print(f"tests failed (exit {proc.returncode}); reverted {framework_research.EXPERT_REF_REL}")
        return proc.returncode
    print("tests passed; change retained")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Daily Claude Code docs research stage")
    parser.add_argument("--offline", action="store_true", help="Skip network fetch; reuse cached snapshot if present.")
    parser.add_argument("--propose", action="store_true", help="Write a module-core patch proposal (advisory).")
    parser.add_argument("--apply", action="store_true", help="Apply the previously generated proposal; reverts on test failure.")
    args = parser.parse_args(argv)

    if args.apply:
        return _cmd_apply()
    if args.propose:
        return _cmd_propose()

    snapshot = framework_research.refresh_snapshot(ROOT, offline=args.offline)
    _write_report(snapshot)
    diff = snapshot.get("keys_diff", {})
    print(
        f"[{snapshot.get('fetch_status', '?')}] matched={len(diff.get('matched', []))} "
        f"new={len(diff.get('new_upstream', []))} missing={len(diff.get('missing_upstream', []))}"
    )
    if snapshot.get("fetch_status") not in {"ok", "skipped"}:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
