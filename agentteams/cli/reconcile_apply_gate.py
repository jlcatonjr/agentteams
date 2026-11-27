"""The clearance ``--reconcile-apply`` needs before it widens a capability grant (C-3).

``--reconcile-front-matter --reconcile-apply`` takes the template's value for each diverging
front-matter key. A narrowing (a smaller ``tools`` list, a longer ``disallowedTools`` list) or a
non-capability key applies as before. A **widening** (a capability added or made broader, a
capability key added where the file has none, any ``model`` change, or a value either side cannot
parse) is applied only when a decision row for :data:`ACTION` clears it. Modelled on
:mod:`agentteams.cli.adopt_merge_gate`:

* **Governed workspace** (``enforce_decision_signing``): never a waiver; the newest ``ACTION`` row
  must carry ``scope`` exactly :data:`ACTION` and ``effect_grants`` exactly this run's tokens
  (:func:`grant_token`, which carries a digest of the value to be written), so a superset or a subset
  refuses and names the difference, and a value changed after signing refuses too. A declared
  grant makes Rule 15 demand the operator's Ed25519 signature. The aggregate cap is checked, then
  the destructive-action gate.
* **Ungoverned workspace:** an ordinary ``ACTION`` PASS row from an approved author clears it,
  through the same destructive-action gate. A waiver still does not (it names no keys), and a row
  that does carry ``effect_grants`` must name exactly this run's tokens.

Both paths end in ``security_gate._assert_destructive_action_allowed``, so an unretracted HALT
blocks, a clearance older than a HALT retraction is void, and the row is consumed once. A HALT may be
recorded under either id in :data:`HALT_IDS`; the HALT scan and the retraction-void check cover both.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.cli import security_gate
from agentteams.cli.decision_log import _enforce_decision_signing
from agentteams.cli.exception_registry import ExceptionRegistryError, assert_within_aggregate_cap

#: The gate action id. Distinct from ``overwrite`` and ``adopt-orphans-merge``: neither clears it.
ACTION = "reconcile-apply-widen"

#: Every id a HALT on this gate may be recorded under; the HALT scan and the retraction-void check both use it.
HALT_IDS: tuple[str, ...] = (ACTION, "reconcile-apply")


class ReconcileApplyError(RuntimeError):
    """A capability widening through ``--reconcile-apply`` is not cleared (fail-closed)."""


def grant_token(rel_path: str, key: str, digest: str) -> str:
    """Return the ``effect_grants`` token for writing one widened value of ``key`` in the agent file ``rel_path``.

    Args:
        rel_path: The agent file's path relative to the agents directory.
        key: The front-matter key being widened.
        digest: :func:`agentteams.front_matter_widening.value_digest` of the value that will be written, so a
            clearance covers that value and no other one rendered for the key later.

    Returns:
        ``frontmatter:<rel_path>#<key>@<digest>``.
    """
    return f"frontmatter:{rel_path}#{key}@{digest}"


def _split(cell: str) -> set[str]:
    return {g.strip() for g in re.split(r"[;,]", cell or "") if g.strip()}


def authorize(team_dir: Path, tokens: set[str], *, consume: bool) -> None:
    """Clear this run's widening ``tokens``, or refuse.

    Args:
        team_dir: The agents directory holding ``references/security-decisions.log.csv``.
        tokens: The :func:`grant_token` of every widening this run would write.
        consume: Spend the clearance (the real run) or only inspect it (``--dry-run``).

    Returns:
        None.

    Raises:
        ReconcileApplyError: Any condition fails; the message names what to fix.
    """
    try:
        _authorize(team_dir, tokens, consume=consume)
    except ReconcileApplyError:
        raise
    except (ExceptionRegistryError, RuntimeError, OSError, ValueError) as exc:
        # An unreadable switch, a malformed log, an invalid waiver or a HALT is still a refusal.
        raise ReconcileApplyError(str(exc)) from exc


def _authorize(team_dir: Path, tokens: set[str], *, consume: bool) -> None:
    governed = _enforce_decision_signing(team_dir)
    for halt_id in HALT_IDS:  # C-2 first, so a HALT reads as one
        security_gate._assert_no_unretracted_halt(team_dir, action=halt_id)
    if security_gate._latest_security_waiver(team_dir, action=ACTION) is not None:
        raise ReconcileApplyError(f"a waiver cannot authorize '{ACTION}'; record a decision row")

    def row_check(row: dict[str, str]) -> None:
        granted = _split(row.get("effect_grants") or "")
        if governed and (row.get("scope") or "").strip().lower() != ACTION:
            raise ReconcileApplyError(f"the '{ACTION}' decision must carry scope={ACTION} exactly")
        if (governed or granted) and granted != tokens:
            extra, missing = sorted(granted - tokens), sorted(tokens - granted)
            raise ReconcileApplyError(
                f"the '{ACTION}' decision must grant exactly this run's widenings "
                f"(extra in decision: {extra or 'none'}; missing from decision: {missing or 'none'})"
            )

    if governed:
        assert_within_aggregate_cap(team_dir, adding=1)
    # One read: the gate selects the newest row, runs row_check on it, and spends only that row.
    security_gate._assert_destructive_action_allowed(team_dir, action=ACTION, consume=consume, row_check=row_check,
                                                     also=HALT_IDS)


def record_hint(tokens: set[str], *, governed: bool) -> str:
    """Return the decision row the operator records to clear ``tokens``.

    Args:
        tokens: The refused widenings' grant tokens.
        governed: Whether the workspace enforces decision signing.

    Returns:
        One line naming the row's required fields.
    """
    grants = ";".join(sorted(tokens))
    if governed:
        return (f"record a PASS row signed with the operator's Ed25519 key (--sign-decision): "
                f"action_reviewed={ACTION}, scope={ACTION}, effect_grants={grants}, "
                "derives_from=<a recorded decision id>")
    return f"record a PASS row from an approved author: action_reviewed={ACTION} (optional effect_grants={grants})"


def is_governed(team_dir: Path) -> bool:
    """Return whether ``team_dir`` enforces decision signing, reading an unreadable switch as governed.

    Args:
        team_dir: The agents directory.

    Returns:
        True when governed or when the switch cannot be read (only used to word the refusal hint).
    """
    try:
        return _enforce_decision_signing(team_dir)
    except (RuntimeError, OSError, ValueError):
        return True
