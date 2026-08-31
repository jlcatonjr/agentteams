#!/usr/bin/env python3
"""Daily-pipeline quality digest aggregator.

Reads the four quality-signal artefact directories under
`tmp/daily-pipeline/` and `tmp/bridge-maintenance/` and emits a
single delta-only digest at
`tmp/daily-pipeline/digest/<today>.md` when at least one signal has
produced content today.

Plan: references/plans/F3-daily-pipeline-quality-summary-2026-05-25.md
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "tmp"
DAILY = TMP / "daily-pipeline"
TODAY = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _framework_research_section() -> tuple[str, bool]:
    snap_path = DAILY / "framework-research" / "latest.json"
    if not snap_path.exists():
        return ("", False)
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ("", False)
    generated = snap.get("generated_at", "?")
    frameworks = snap.get("frameworks") or {}
    rows = [f"- generated_at: `{generated}`", f"- snapshot: [`{snap_path.relative_to(ROOT)}`]({snap_path.relative_to(ROOT)})"]
    for fid, entry in frameworks.items():
        rows.append(
            f"  - {fid}: fetch=`{entry.get('fetch_status', '?')}`, "
            f"tokens={', '.join(entry.get('upstream_tokens', {}).get('front_matter_keys_present', [])) or '—'}"
        )
    # Active if generated today
    active = generated.startswith(TODAY)
    return ("\n".join(["## Framework research", "", *rows, ""]), active)


def _per_day_section(name: str, label: str, subdir: str) -> tuple[str, bool]:
    path = DAILY / subdir / f"{TODAY}.md"
    text = _read_text(path)
    if not text.strip():
        return ("", False)
    head = text.splitlines()[:4]
    return (
        "\n".join([
            f"## {label}",
            "",
            f"- source: [`{path.relative_to(ROOT)}`]({path.relative_to(ROOT)})",
            f"- preview:",
            "",
            "  " + "\n  ".join(head),
            "",
        ]),
        True,
    )


def _bridge_summary_section() -> tuple[str, bool]:
    path = TMP / "bridge-maintenance" / "summary.md"
    text = _read_text(path)
    if not text.strip():
        return ("", False)
    # Show the started_at line and the table head.
    head = "\n".join(text.splitlines()[:12])
    return (
        "\n".join([
            "## Bridge maintenance summary",
            "",
            f"- source: [`{path.relative_to(ROOT)}`]({path.relative_to(ROOT)})",
            "",
            "```",
            head,
            "```",
            "",
        ]),
        True,
    )


def _operational_json_audit_section() -> tuple[str, bool]:
    """T7/VI.1: detect generated `.github/agents/references/*.json` files
    that look like operational metadata (high density of paths or
    high-entropy tokens) but are NOT in scan._OPERATIONAL_JSON_NAMES.
    Surfaces escapes from the suppression allow-list early so a future
    new generated file can't silently re-block the daily security scan.
    """
    refs_dir = ROOT / ".github" / "agents" / "references"
    if not refs_dir.is_dir():
        return ("", False)
    try:
        from agentteams.scan import _OPERATIONAL_JSON_NAMES
    except ImportError:
        return ("", False)

    import re as _re

    path_re = _re.compile(r"/Users/|/home/|[A-Z]:\\\\")
    hash_re = _re.compile(r'"[a-f0-9]{32,}"')
    suspects: list[tuple[str, int, int]] = []
    for jp in sorted(refs_dir.glob("*.json")):
        if jp.name in _OPERATIONAL_JSON_NAMES:
            continue
        try:
            lines = jp.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        n_path = sum(1 for ln in lines if path_re.search(ln))
        n_hash = sum(1 for ln in lines if hash_re.search(ln))
        ratio = (n_path + n_hash) / max(1, len(lines))
        if ratio > 0.05:
            suspects.append((jp.name, n_path + n_hash, len(lines)))

    if not suspects:
        return ("", False)

    lines = [
        "## Operational-JSON allow-list audit",
        "",
        "Files matching the operational-metadata shape but NOT in",
        "`agentteams.scan._OPERATIONAL_JSON_NAMES`. Review and add to",
        "the allow-list if these are legitimate pipeline-controlled artefacts,",
        "or scrub them if they leaked path/hash content unintentionally.",
        "",
        "| File | Flagged lines | Total lines |",
        "|---|---|---|",
    ]
    for name, flagged, total in suspects:
        lines.append(f"| `{name}` | {flagged} | {total} |")
    lines.append("")
    return ("\n".join(lines), True)


def main(argv: list[str]) -> int:
    sections: list[str] = []
    any_active = False
    for renderer in (
        _framework_research_section,
        lambda: _per_day_section("shrink", "Fenced-region shrink events", "shrink-events"),
        lambda: _per_day_section("dual", "Dual-descriptor events", "dual-descriptor-events"),
        lambda: _per_day_section("orphan", "Orphan agent events", "orphan-events"),
        _operational_json_audit_section,
        _bridge_summary_section,
    ):
        body, active = renderer()
        if body:
            sections.append(body)
        any_active = any_active or active

    if not any_active:
        print("digest: no active signals today; skipping")
        return 0

    digest_dir = DAILY / "digest"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"{TODAY}.md"
    body = [
        f"# Daily-Pipeline Quality Digest — {TODAY}",
        "",
        "Aggregated view of today's daily-pipeline quality signals. "
        "Delta-only: this file is written only when at least one signal "
        "produced content today.",
        "",
        *sections,
    ]
    digest_path.write_text("\n".join(body), encoding="utf-8")
    print(f"digest: wrote {digest_path.relative_to(ROOT)} with {len(sections)} section(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
