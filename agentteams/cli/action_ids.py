"""action_ids.py — how a decision row's ``action_reviewed`` id is matched to a gate action.

Two rules, deliberately different, because a false match means opposite things on each side:

* :func:`action_matches` decides which rows can **clear** an action. It stays a strict id match
  (``<action>`` or ``<action>`` plus a ``-_.:`` suffix): widening it would let a row named for one
  thing authorize another.
* :func:`halt_names_action` decides which HALT rows **block** an action. A HALT also blocks every
  action whose id appears as a token run in the HALT's own id, so a HALT recorded as
  ``m6-overwrite-render-1a2fad6`` blocks ``overwrite`` (C-2: a HALT is not evaded by spelling). A
  false positive only blocks, so this side errs wide (1.0 must-fix, mathAgents M6 finding). A HALT on a family
  id also blocks every id under it after a colon (``server-apply`` blocks ``server-apply:remove-path:...``;
  #636). ``-``, ``_`` and ``.`` do not make a family here: ``branch`` does not block ``branch-delete``.

A HALT whose id has no letter or digit names nothing, so neither rule can attribute it. It blocks
**every** action (:func:`halt_has_no_id`), and is retracted by the row handle
:func:`halt_row_handle` gives it: a HALT the gate reads but cannot attribute must block, never vanish.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.cli.decision_chain import row_chain_digest
from agentteams.cli.sandbox_effects import SANDBOX_RELAXATION_KINDS

#: Token boundaries for the HALT-side match: any non-alphanumeric run, after camelCase and
#: letter/digit boundaries are split (``m6OverwriteRender``, ``overwrite2``, ``(prune)``, ``overwrite,``).
#: Wide on purpose: on the HALT side a false match only blocks.
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

#: The action ids agentteams' own gates are called with (``_assert_destructive_action_allowed``,
#: ``check_clearance``, ``_assert_no_unretracted_halt``); ``branch-cleanup`` is the family of the
#: per-plan ``branch-cleanup:<sha256>`` id. The pre-upgrade report checks old HALTs against these; a
#: test checks both directions against the call sites. The seven sandbox-relaxation families
#: (``cli/sandbox_effects.py``) are called as ``<kind>:<qualifier>`` instance ids and as the bare family.
#: ``server-apply`` is the family of the per-path ``server-apply:remove-path:<host>:<path>`` id.
GATE_ACTIONS: tuple[str, ...] = ("overwrite", "prune", "restore-backup", "adopt-orphans-merge", "branch-delete",
                                 "branch-cleanup", "vulnscan-remediation", "vulnscan-publish", "wasm-pin-remediation",
                                 "vulnscan-known", "server-apply", *SANDBOX_RELAXATION_KINDS, "s10-cooldown-exempt",
                                 "s10-cooldown-deprecated", "carveout", "s10-toolchain-group", "integration-merge", "dev-merge")


def action_matches(action_reviewed: str, action: str) -> bool:
    """Return True for strict action-id style matches.

    Accepted patterns:
    - <action>
    - <action>-<suffix>
    - <action>_<suffix>
    - <action>.<suffix>
    - <action>:<suffix>

    **The suffix decorates; it never narrows.** ``prune-001`` is an instance id, and matching
    it to the ``prune`` action is the intended behaviour. But the same field gets written as a
    *scope descriptor* — ``overwrite-single-readme-only`` — and this matcher cannot tell the
    two readings apart, so a clearance that reads as narrow grants the unrestricted action
    (audit W11 / probe A5). Expressing a real restriction requires the ``scope`` column, which
    ``decision_log._scope_permits`` enforces; this function deliberately stays a pure id match,
    because inferring intent from a suffix is exactly the ambiguity that caused the finding.

    Args:
        action_reviewed: The row's ``action_reviewed`` (or ``decision``) id.
        action: The gate action being checked.

    Returns:
        Whether the row is for *action*.
    """
    action_norm = action.strip().lower()
    reviewed_norm = action_reviewed.strip().lower()
    if not action_norm:
        return False
    if reviewed_norm == action_norm:
        return True
    return reviewed_norm.startswith(
        (f"{action_norm}-", f"{action_norm}_", f"{action_norm}.", f"{action_norm}:")
    )


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(_CAMEL.sub(" ", text.strip()).lower()) if t]


def _colon_parent(halt_reviewed: str, action: str) -> bool:
    """Whether *action* is the HALT's id plus ``:`` and more: a family HALT blocks its instances (#636).

    Trailing colons on the HALT's id are ignored (``server-apply:`` is the ``server-apply`` family).
    """
    halt_norm = halt_reviewed.strip().lower().rstrip(":")
    return bool(halt_norm) and action.strip().lower().startswith(halt_norm + ":")


def halt_names_action(halt_reviewed: str, action: str) -> bool:
    """Return True when a HALT row's id names *action*.

    A strict match (:func:`action_matches`), *action* as a token run in the HALT's id, or *action* under the HALT's
    id after a colon (``server-apply`` names ``server-apply:remove-path:<host>:<path>``; #636; no other
    delimiter makes a family on this side).

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.
        action: The gate action being checked.

    Returns:
        Whether the HALT blocks *action*.
    """
    if action_matches(halt_reviewed, action) or _colon_parent(halt_reviewed, action):
        return True
    wanted, have = _tokens(action), _tokens(halt_reviewed)
    return bool(wanted) and any(have[i:i + len(wanted)] == wanted for i in range(len(have) - len(wanted) + 1))


def halt_has_no_id(halt_reviewed: str) -> bool:
    """Return True when a HALT row's id has no letter or digit (empty, blank or punctuation only).

    Such a HALT names no action, so it blocks every one. Through 1.3.0 it matched none, and a later
    PASS cleared the action (C-2 fail-open).

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.

    Returns:
        Whether the id is unusable as a name.
    """
    return not _tokens(halt_reviewed)


def halt_blocks(halt_reviewed: str, action: str) -> bool:
    """Return True when a HALT row blocks *action*: its id names it, or it has no usable id at all.

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.
        action: The gate action being checked.

    Returns:
        Whether the HALT blocks *action*.
    """
    return halt_has_no_id(halt_reviewed) or halt_names_action(halt_reviewed, action)


def halt_retraction_id(halt_reviewed: str, row_number: int, row: dict[str, str]) -> str:
    """Return the normalized id a ``HALT-RETRACTED`` row must carry to retract one HALT by exact id.

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.
        row_number: The HALT's 1-based row number in the log (the header is not counted).
        row: The HALT row.

    Returns:
        The HALT's own id, lower-cased; for a HALT with no usable id, its row handle.
    """
    return halt_row_handle(row_number, row) if halt_has_no_id(halt_reviewed) else halt_reviewed.strip().lower()


def idless_halt_note(halt_reviewed: str, row_number: int, row: dict[str, str]) -> str:
    """Return the sentence a refusal adds for a HALT with no usable id (empty for any other HALT).

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.
        row_number: The HALT's 1-based row number in the log.
        row: The HALT row.

    Returns:
        A sentence naming the row handle that retracts it, with a leading space; or ``""``.
    """
    if not halt_has_no_id(halt_reviewed):
        return ""
    return (" This HALT has no usable action id, so it names no action and blocks every one; it is retracted only "
            f"by a row whose action_reviewed is exactly '{halt_row_handle(row_number, row)}'.")


def halt_display_id(halt_reviewed: str, row_number: int, row: dict[str, str]) -> str:
    """Return the id a refusal names for a HALT: the id a retraction of it must carry.

    Tools that read the refusal (``vulnscan halts``) build the retraction from this id, so for a HALT with
    no usable id it must be the row handle, never the gate action.

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.
        row_number: The HALT's 1-based row number in the log.
        row: The HALT row.

    Returns:
        The HALT's own id as written; for a HALT with no usable id, its row handle.
    """
    return halt_row_handle(row_number, row) if halt_has_no_id(halt_reviewed) else halt_reviewed.strip()


def halt_row_handle(row_number: int, row: dict[str, str]) -> str:
    """Return the id that retracts the id-less HALT on a given row: ``halt-row:<N>:<digest>``.

    The handle binds the row's position **and** its signed fields (16 hex digits of its chain digest; free-text
    ``conditions``/``evidence`` only through ``text_digest``). A row
    number alone is not sound: rows in a signed log's unchained legacy prefix can be removed or added
    without breaking the chain, which would move a retraction onto a different HALT. With the digest, a
    shifted or edited row no longer matches its retraction and blocks again.

    Args:
        row_number: The HALT's 1-based row number in the log (the header is not counted).
        row: The HALT row.

    Returns:
        The ``action_reviewed`` a ``HALT-RETRACTED`` row must carry, exactly, to retract that row.
    """
    return f"halt-row:{row_number}:{row_chain_digest(row)[:16]}"


def newly_blocking_halts(rows: list[dict[str, str]]) -> list[tuple[int, str, str]]:
    """List unretracted HALTs that block a gate action only through the token match.

    These rows did not block under the pre-1.0 prefix-only rule and do now, so a team should
    review them before upgrading (each is cleared by a ``HALT-RETRACTED`` row with the same id).

    Args:
        rows: The decisions-log rows, in file order.

    Returns:
        ``(1-based row number, HALT id, gate action)`` for each pair, in row order.
    """
    found: list[tuple[int, str, str]] = []
    open_ids = _unretracted_halt_ids(rows)
    for index in range(len(rows)):
        reviewed = open_ids[index]
        if not reviewed:
            continue
        for action in GATE_ACTIONS:
            if halt_names_action(reviewed, action) and not action_matches(reviewed, action):
                found.append((index + 1, reviewed, action))
    return found


def family_halts(rows: list[dict[str, str]]) -> list[tuple[int, str, str]]:
    """List unretracted HALTs with a usable id: since 1.3.2 each also blocks every id under it after a colon (#636).

    Consumer-defined ids are listed too: a HALT on ``deploy`` now blocks a consumer gate's ``deploy:prod``.

    Args:
        rows: The decisions-log rows, in file order.

    Returns:
        ``(1-based row number, HALT id, agentteams gate family or "")`` in row order; the family is the
        :data:`GATE_ACTIONS` id the HALT's id equals or extends after a colon.
    """
    found: list[tuple[int, str, str]] = []
    for index, reviewed in enumerate(_unretracted_halt_ids(rows)):
        if reviewed and not halt_has_no_id(reviewed):
            norm = reviewed.lower().rstrip(":")
            family = next((g for g in GATE_ACTIONS if norm == g or norm.startswith(g + ":")), "")
            found.append((index + 1, reviewed, family))
    return found


def describe_newly_blocking_halts(team_dir: Path) -> list[str]:
    """Report lines for :func:`newly_blocking_halts` on a team's decisions log (empty when none).

    Args:
        team_dir: The team root (the log is ``references/security-decisions.log.csv``).

    Returns:
        Human-readable lines, each naming the row, the gate action it blocks and the retraction id. HALT rows
        with no usable id come first, each with the row handle that retracts it; then the token-rule rows; then
        every unretracted HALT that now also blocks the ids under it after a colon (:func:`family_halts`).
    """
    # Local import: decision_log imports this module, so a top-level import would be a cycle.
    from agentteams.cli.decision_log import _read_decision_rows

    try:
        read = _read_decision_rows(team_dir)
    except RuntimeError:
        return []  # an unreadable log is reported by the governance-log check
    rows = read[0] if read else []
    hits = newly_blocking_halts(rows)
    named = {n for n, _, _ in hits}
    lines: list[str] = []
    idless = idless_open_halts(rows)
    if idless:
        lines.append(f"HALT rows with no usable id ({len(idless)}): each blocks EVERY gated action:")
        lines += [f"  row {n}: if it no longer applies, retract it with a HALT-RETRACTED row whose "
                  f"action_reviewed is exactly '{handle}'" for n, handle in idless]
    if hits:
        lines.append(f"HALT rows that newly block an agentteams gate action under the 1.0 token rule ({len(hits)}):")
        lines += [f"  row {n}: '{rid}' blocks '{act}' (and every id under it, '{rid}:...'); if it no longer "
                  f"applies, retract it with a HALT-RETRACTED row whose action_reviewed is exactly '{rid}'"
                  for n, rid, act in hits]
    families = [f for f in family_halts(rows) if f[0] not in named]   # each HALT is listed once
    if families:
        lines.append(f"Other unretracted HALT rows ({len(families)}): each blocks every id under it after a colon "
                     "(since 1.3.2) and any id your own gates check (check_clearance) that appears as a token in it:")
        lines += [f"  row {n}: '{rid}' now also blocks every id under it ('{rid}:...'), "
                  + (f"in agentteams gate family '{fam}'" if fam else "including ids your own gates check")
                  for n, rid, fam in families]
    return lines


def idless_open_halts(rows: list[dict[str, str]]) -> list[tuple[int, str]]:
    """List HALT rows with no usable id that no later row retracts by handle.

    Args:
        rows: The decisions-log rows, in file order.

    Returns:
        ``(1-based row number, row handle)`` in row order. Whether a retraction is authentic is the gate's
        check, not this report's.
    """
    found: list[tuple[int, str]] = []
    for index, row in enumerate(rows):
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        if (row.get("verdict") or row.get("status") or "").strip().upper() != "HALT" or not halt_has_no_id(reviewed):
            continue
        handle = halt_row_handle(index + 1, row)
        if not _retracted_later(rows, index, handle):
            found.append((index + 1, handle))
    return found


def _retracted_later(rows: list[dict[str, str]], index: int, retraction_id: str) -> bool:
    """Whether a row after *index* is a ``HALT-RETRACTED`` whose id is exactly *retraction_id* (case-insensitive)."""
    return any((later.get("verdict") or later.get("status") or "").strip().upper() == "HALT-RETRACTED"
               and (later.get("action_reviewed") or later.get("decision") or "").strip().lower()
               == retraction_id.lower() for later in rows[index + 1:])


def _unretracted_halt_ids(rows: list[dict[str, str]]) -> list[str]:
    """Per row: the HALT id when the row is an unretracted HALT (by exact id), else ``""``."""
    out: list[str] = []
    for index, row in enumerate(rows):
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        is_halt = (row.get("verdict") or row.get("status") or "").strip().upper() == "HALT"
        out.append(reviewed if is_halt and not _retracted_later(rows, index, reviewed) else "")
    return out


__all__ = ["GATE_ACTIONS", "action_matches", "describe_newly_blocking_halts", "halt_blocks", "halt_display_id",
           "family_halts", "halt_has_no_id",
           "halt_names_action", "halt_retraction_id", "halt_row_handle", "idless_halt_note", "idless_open_halts",
           "newly_blocking_halts"]
