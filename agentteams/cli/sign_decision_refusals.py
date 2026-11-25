"""sign_decision_refusals.py — why ``--sign-decision`` refuses a spec before the key is read.

Each check returns the refusal sentence, or None. ``operator_signing.sign_decision`` prints it and stops: the team
dir is not one the gate reads, the row's author is off the roster the gate checks, or the row is a clearance the gate
would treat as void (an id under an open HALT, #616).

Carved out of ``operator_signing`` (near the module-size ceiling). It runs in the signing process before the key is
read, so it is integrity-pinned and part of the signing closure.
"""

from __future__ import annotations

from pathlib import Path


def sign_decision_team_refusal(output_dir: Path) -> str | None:
    """F-2: return why ``output_dir`` is not a team root ``--sign-decision`` may append to, or None.

    The gate reads the decisions log and the verify-key store relative to the TEAM dir, so a row
    minted anywhere else (``--project .``, a bare CWD) was a silent no-op the gate never read. A
    team is detected by ``references/agent-privilege.json`` OR ``references/build-log.json``.

    Args:
        output_dir: The resolved ``--output`` / ``--project`` / CWD directory.

    Returns:
        The refusal sentence, or None when ``output_dir`` is a team dir.
    """
    from agentteams.cli.clearance_binding import is_team_dir

    if is_team_dir(output_dir):
        return None
    return (f"{output_dir} is not an agentteams team dir (no references/agent-privilege.json or "
            "references/build-log.json). The gate reads the decisions log and the verify-key store "
            "relative to the team dir; pass --output <project>/.claude/agents (or the team's agents "
            "dir). Refusing to sign (fail-closed).")


def stored_view(row: dict[str, str], fieldnames: list[str] | None) -> dict[str, str]:
    """The cells of *row* the decisions log will keep: its own columns, or the columns a new log is created with.

    Args:
        row: The row being signed.
        fieldnames: The existing log's header, or None when the signer creates the log.

    Returns:
        *row* restricted to those columns.
    """
    from agentteams.cli import decision_log as dl

    kept = {c.strip() for c in (fieldnames or ["timestamp", "requesting_agent", *dl.SIGNED_DECISION_COLUMNS])}
    return {k: v for k, v in row.items() if k in kept}


def author_refusal(output_dir: Path, row: dict[str, str], fieldnames: list[str] | None) -> str | None:
    """Why the row's author is not one the gate accepts, or None.

    The gate refuses an authorizing row (PASS, CONDITIONAL PASS, HALT-RETRACTED) whose author is off the roster,
    whatever its signature; a HALT is honoured unauthenticated, so it is not checked. The author is read from the
    cells the log keeps (:func:`stored_view`).

    Args:
        output_dir: The team dir.
        row: The row being signed.
        fieldnames: The existing log's header, or None for a new log.

    Returns:
        The refusal sentence, or None.
    """
    from agentteams.cli import decision_log as dl

    verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
    if verdict not in {*dl.CLEARING_VERDICTS, dl._HALT_RETRACTION_VERDICT}:
        return None
    author = dl._decision_author(stored_view(row, fieldnames))
    try:
        approved_ok, approved = dl.author_is_approved(author, output_dir)
    except RuntimeError as exc:
        return f"refusing to sign: {exc}. The key was not read."
    if approved_ok:
        return None
    return (f"refusing to sign: the row's author is '{author or 'none'}', which is not an approved security author "
            f"in {output_dir / 'references' / dl._DECISION_AUTHORS_FILE} ({sorted(approved)}). The gate refuses such "
            "a row whatever its signature. Set \"author\" (or the log's requesting_agent/owner column) to a rostered "
            "author, or add one to the roster. The key was not read.")


def open_halt_refusal(output_dir: Path, row: dict[str, str]) -> str | None:
    """Why a clearance (PASS, CONDITIONAL PASS) cannot be signed under an open HALT, or None (#616).

    Such a row would be void at the gate once the HALT is retracted. HALT and HALT-RETRACTED rows are not checked.

    Args:
        output_dir: The team dir.
        row: The row being signed.

    Returns:
        The refusal sentence, or None.
    """
    from agentteams.cli import decision_log as dl

    if (row.get("verdict") or "").strip().upper() not in dl.CLEARING_VERDICTS:
        return None
    halt_scan_action = (row.get("action_reviewed") or "").strip()
    try:
        dl._assert_no_unretracted_halt(output_dir, action=halt_scan_action)
    except RuntimeError as exc:
        return (f"refusing to sign: '{halt_scan_action}' is under an open HALT ({exc}). Retract the HALT first, then "
                "sign the clearance. The key was not read.")
    return None


__all__ = ["author_refusal", "open_halt_refusal", "sign_decision_team_refusal", "stored_view"]
