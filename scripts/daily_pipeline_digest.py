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


def main(argv: list[str]) -> int:
    sections: list[str] = []
    any_active = False
    for renderer in (
        _framework_research_section,
        lambda: _per_day_section("shrink", "Fenced-region shrink events", "shrink-events"),
        lambda: _per_day_section("dual", "Dual-descriptor events", "dual-descriptor-events"),
        lambda: _per_day_section("orphan", "Orphan agent events", "orphan-events"),
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
