"""update_report.py — durable record of what an update chose to do.

The module's less-used operations already leave reports: ``--bridge-merge``
writes ``bridge-merge.report.md``, ``--recipe-check`` writes
``recipe-check.report.md``, and ``--fleet`` writes a report directory. The most
common operation, ``--update --merge``, wrote nothing. Its decisions — which
fenced bodies were preserved against a shrinking re-render, which legacy files
were skipped, which markers were retrofitted — were announced to stdout and lost
with the scrollback.

Those are exactly the decisions that need to survive the run. A constitution
enables accountability for what it cannot prevent; tooling that silently
declines to overwrite an agent's work owes the same account of itself.

Report convention (shared with recipe-check and bridge-merge):

* named ``<operation>.report.md``
* written beside the artifacts the operation acted on, never a new location
* Markdown, ``## <subject>`` per unit, ``- PASS`` / ``- FAIL:`` bullets
* **silent when there is nothing to attribute** — a clean run writes no file

The report is an additional artifact and never a gate: it does not change exit
codes and does not alter behaviour on a run with nothing to record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPORT_NAME = "update.report.md"

__all__ = [
    "REPORT_NAME",
    "has_attributable_events",
    "build_report",
    "write_report",
    "report_run",
]


def _get(result: Any, name: str) -> list:
    """Read a list attribute from an EmitResult-like object, defaulting to []."""
    value = getattr(result, name, None)
    return list(value) if value else []


def has_attributable_events(result: Any, *, routing_loss_accepted: dict | None = None) -> bool:
    """True when the run made a decision worth recording.

    A run that simply wrote or left files alone has nothing to attribute. A run
    that preserved, skipped, retrofitted, set content aside, or dropped routing
    it was told to drop (``--accept-routing-loss``) does.

    Args:
        result: An EmitResult-like object from the emit phase.
        routing_loss_accepted: ``{"slugs", "orchestrator_sha256"}`` when an
            ``--overwrite`` dropped routing under ``--accept-routing-loss`` (#392 item 12).

    Returns:
        Whether a report should be written for this run.
    """
    if routing_loss_accepted:
        return True
    for name in (
        "skipped_legacy",
        "sections_preserved",
        "shrink_notices",
        "lost_fence_bodies",
        "retrofitted",
    ):
        if _get(result, name):
            return True
    return False


def build_report(result: Any, *, backup_path: str | None = None,
                 routing_loss_accepted: dict | None = None) -> str:
    """Render the Markdown report for a completed update.

    Args:
        result: An EmitResult-like object from the emit phase.
        backup_path: Directory holding this run's backup, when one was taken.
        routing_loss_accepted: As :func:`has_attributable_events`.

    Returns:
        The report body. Callers should only write it when
        :func:`has_attributable_events` is true.
    """
    written = _get(result, "written")
    merged = _get(result, "merged")
    skipped_legacy = _get(result, "skipped_legacy")
    preserved = _get(result, "sections_preserved")
    notices = _get(result, "shrink_notices")
    lost = _get(result, "lost_fence_bodies")
    retrofitted = _get(result, "retrofitted")

    lines: list[str] = [
        "# update — report",
        "",
        "Decisions this run made that are not visible in the resulting files.",
        "A clean run writes no report; this one had something to record.",
        "",
        f"- files written: {len(written)}",
        f"- files merged: {len(merged)}",
        "",
    ]

    if routing_loss_accepted:
        lines.append("## Routing loss accepted")
        lines.append("")
        lines.append(
            "`--overwrite` replaced an orchestrator that routed to these agents, "
            "which this build does not keep, under `--accept-routing-loss`."
        )
        lines.append("")
        lines.append(f"- orchestrator sha256 before the run: `{routing_loss_accepted.get('orchestrator_sha256')}`")
        for slug in routing_loss_accepted.get("slugs", []):
            lines.append(f"- dropped: `@{slug}`")
        lines.append("")

    if preserved:
        lines.append("## Fenced bodies preserved")
        lines.append("")
        lines.append(
            "The re-render was shorter than the deployed body. The existing "
            "text was kept: content the tooling cannot account for is content "
            "it does not remove."
        )
        lines.append("")
        for sid in preserved:
            lines.append(f"- PASS: preserved `{sid}`")
        lines.append("")

    if notices:
        lines.append("## Shrink notices")
        lines.append("")
        for n in notices:
            lines.append(f"- {n}")
        lines.append("")

    if lost:
        lines.append("## Prior bodies set aside")
        lines.append("")
        for sid in lost:
            lines.append(f"- PASS: `{sid}` written to a `.lost.{sid}.md.agentteams-bak` sidecar")
        lines.append("")

    if skipped_legacy:
        lines.append("## Legacy files skipped")
        lines.append("")
        lines.append(
            "These files carry no fence markers, so the template update was "
            "not applied to them."
        )
        lines.append("")
        for path in skipped_legacy:
            lines.append(f"- FAIL: update not applied to `{path}`")
        lines.append("")

    if retrofitted:
        lines.append("## Fence markers retrofitted")
        lines.append("")
        for path in retrofitted:
            lines.append(f"- PASS: markers added to `{path}`")
        lines.append("")

    if backup_path:
        lines.append("## Recovery")
        lines.append("")
        lines.append(f"- backup: `{backup_path}`")
        lines.append("")

    return "\n".join(lines)


def write_report(
    result: Any, output_dir: Path, *, backup_path: str | None = None,
    routing_loss_accepted: dict | None = None,
) -> Path | None:
    """Write the update report beside the agent files, when warranted.

    Args:
        result: An EmitResult-like object from the emit phase.
        output_dir: The agents directory this run wrote to.
        backup_path: Directory holding this run's backup, when one was taken.
        routing_loss_accepted: As :func:`has_attributable_events`.

    Returns:
        The report path, or None when the run had nothing to attribute.

    Raises:
        OSError: If the report cannot be written to *output_dir*.
    """
    if not has_attributable_events(result, routing_loss_accepted=routing_loss_accepted):
        return None
    path = Path(output_dir) / REPORT_NAME
    path.write_text(build_report(result, backup_path=backup_path, routing_loss_accepted=routing_loss_accepted),
                    encoding="utf-8")
    return path


def report_run(result: Any, output_dir: Path, backup_path: object = None, *,
               routing_loss_accepted: dict | None = None) -> None:
    """Write the update report for a completed run and announce it.

    Wraps :func:`write_report` with the announcement and the guarantee callers
    depend on: a report is never allowed to fail an otherwise successful update,
    and a run with nothing to attribute stays silent.

    Args:
        result: An EmitResult-like object from the emit phase.
        output_dir: The agents directory this run wrote to.
        backup_path: Backup location for this run, if one was taken.
        routing_loss_accepted: As :func:`has_attributable_events`.
    """
    try:
        path = write_report(
            result, output_dir, backup_path=str(backup_path) if backup_path else None,
            routing_loss_accepted=routing_loss_accepted,
        )
    except OSError as exc:
        print(f"  !  Could not write update report: {exc}")
        return
    if path is not None:
        print(f"  ✓  Update report: {path.name}")
