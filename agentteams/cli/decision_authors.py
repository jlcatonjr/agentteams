"""decision_authors.py — who may issue an authorizing security decision, in one place.

The roster (``references/security-approvers.txt``, else the security sentinel), how a row names its author across
both log schemas, the one roster check the gate, the waiver check and the signer share, and the verdicts that
clear an action.

Carved out of ``decision_log`` (which sits near the CH-07 ceiling), the way ``decision_lineage``, ``decision_chain``,
``action_ids`` and ``decision_text`` were. It is integrity-pinned; ``decision_log`` re-exports these names, so
existing callers and tests are unchanged.
"""

from __future__ import annotations

from pathlib import Path

#: The verdicts that clear an action (a PASS, or a CONDITIONAL PASS whose conditions are verified).
CLEARING_VERDICTS: frozenset[str] = frozenset({"PASS", "CONDITIONAL PASS"})

#: Author values that may issue an AUTHORIZING decision, absent a project roster. The security
#: sentinel is the only agent the constitution empowers to clear an action (Constitutional Rule
#: 1); the orchestrator routes to it, it does not stand in for it.
_DEFAULT_DECISION_AUTHORS: frozenset[str] = frozenset({"security", "@security"})

#: Optional project roster, one author per line, `#` comments allowed.
_DECISION_AUTHORS_FILE = "security-approvers.txt"


def _approved_decision_authors(output_dir: Path) -> frozenset[str]:
    """Return the author values permitted to issue an authorizing decision.

    Args:
        output_dir: The generated team's output directory.

    Returns:
        The roster from ``references/security-approvers.txt`` when that file exists,
        otherwise :data:`_DEFAULT_DECISION_AUTHORS`. A present-but-empty roster returns the
        default rather than an empty set — an empty file is far more likely to be an accident
        than a deliberate decision to make every action unclearable.
    """
    roster_path = output_dir / "references" / _DECISION_AUTHORS_FILE
    if not roster_path.exists():
        return _DEFAULT_DECISION_AUTHORS
    try:
        raw = roster_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"unable to read security approver roster: {exc}") from exc
    names = {
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    return frozenset(names) or _DEFAULT_DECISION_AUTHORS


def _decision_author(row: dict[str, str]) -> str:
    """Return a decision row's author across both supported log schemas.

    The legacy six-column schema names it ``requesting_agent``; the current nine-column
    schema names it ``owner``. Both are checked so author binding works on either.
    """
    for field in ("requesting_agent", "owner", "author"):
        value = (row.get(field) or "").strip()
        if value:
            return value.lower().lstrip("@").strip() or value.lower()
    return ""


def author_is_approved(author: str, output_dir: Path) -> tuple[bool, frozenset[str]]:
    """Check one author against the team's roster: the single roster check (gate, waiver, signer).

    Names are compared lower-cased, with a leading ``@`` ignored on both sides.

    Args:
        author: The author or approver as written.
        output_dir: The team dir whose roster applies.

    Returns:
        ``(approved, roster as written)``; the roster is for the refusal message.

    Raises:
        RuntimeError: The roster file is present but unreadable.
    """
    roster = _approved_decision_authors(output_dir)
    return author.strip().lower().lstrip("@") in {a.lower().lstrip("@") for a in roster}, roster


__all__ = ["CLEARING_VERDICTS", "author_is_approved"]
