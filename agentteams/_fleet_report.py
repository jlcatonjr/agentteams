"""Fleet run report: ``report.json``, ``summary.md`` and the closing console summary.

Split out of ``agentteams.fleet`` to keep that module under the CH-07 line ceiling.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from agentteams._fleet_shrink_grants import shrink_grant_summary


def write_report(results, report_root: Path, parent: Path, apply: bool, frameworks: str) -> int:
    """Write ``report.json`` and ``summary.md`` for a fleet run and print the closing summary.

    Args:
        results: The run's ``WorkspaceResult`` list.
        report_root: The run's report directory (created if missing).
        parent: The fleet parent directory.
        apply: Whether this was an apply run (else dry-run).
        frameworks: The ``--fleet-frameworks`` value.

    Returns:
        1 when any target FAILed (so CI can gate), else 0; REVIEW exits 0 with a notice.
    """
    rows = []
    counts: dict[str, int] = {}
    for wr in results:
        for tr in wr.targets:
            rows.append(asdict(tr))
            counts[tr.status] = counts.get(tr.status, 0) + 1

    report_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "parent": str(parent),
        "frameworks": frameworks,
        "applied": apply,
        "summary": counts,
        "workspaces": [asdict(w) for w in results],
    }
    (report_root / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [f"# Fleet update report ({'apply' if apply else 'dry-run'})", "",
             f"- Parent: `{parent}`", f"- Frameworks: {frameworks}",
             f"- Status counts: {counts}", "",
             "| workspace | target | status | +/- | shrink | grant warnings | UE-del | detail |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {Path(r['workspace']).name} | {r['target']} | {r['status']} | "
            f"+{r['added_lines']}/-{r['removed_lines']} | {len(r['shrink_notices'])} | "
            f"{len(r['shrink_grant_warnings'])} | {len(r['user_editable_deletions'])} | {r['detail']} |"
        )
    (report_root / "summary.md").write_text("\n".join(lines + shrink_grant_summary(rows)) + "\n", encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"Fleet {'apply' if apply else 'dry-run'} complete: {counts}")
    print(f"Report: {report_root}")
    review = [r for r in rows if r["status"] in ("REVIEW", "FAIL")]
    if review:
        print(f"  ⚠  {len(review)} target(s) need review:")
        for r in review[:10]:
            print(f"     [{r['status']}] {Path(r['workspace']).name}/{r['target']} — {r['detail']}")
    # Non-zero exit only on a real FAIL (so CI can gate); REVIEW is exit 0 with notice.
    return 1 if counts.get("FAIL") else 0
