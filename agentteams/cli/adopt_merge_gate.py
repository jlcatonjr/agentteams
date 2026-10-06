"""Merge-mode adoption: gated, append-only union of adopted agents into the orchestrator's ``agents:``.

``--update --merge --adopt-orphans`` lets a team fold bespoke agents into its orchestrator without a
full ``--overwrite`` (which would re-render every agent file). Routing rows already merge through
the fenced ``routing_table_rows`` region (:mod:`agentteams.adopted_agents`). The front-matter
``agents:`` list is different: it is a capability key (``front_matter_merge.CAPABILITY_FRONT_MATTER_KEYS``)
that merge never applies on its own, because adding an entry widens what the orchestrator may
dispatch (C-3). This module is the one explicit, gated exception, with the conditions of the
2026-10-05 ``@security`` design review:

1. **Governed workspaces only.** Outside ``enforce_decision_signing`` an author name alone clears a
   row, which is too weak for a capability grant, so the action is refused there.
2. **A signed decision, never a waiver.** A waiver carries no ``effect_grants``. The accepted row's
   ``scope`` must be exactly :data:`ACTION` (so a broader row cannot clear it) and its
   ``effect_grants`` must be exactly ``agents:<slug>`` for the slugs this run adds — a superset or a
   subset refuses and names the difference. Declaring a grant makes Rule 15 demand the operator's
   Ed25519 signature, and ``scope``/``effect_*`` are now inside the signed payload.
3. **The Rule 15(d) aggregate cap** is checked before the grant.
4. **Append-only.** Only the orchestrator's ``agents:`` list changes; existing entries are never
   removed, reordered or rewritten, and nothing else in the file is touched. A second run adds
   nothing. The written set is re-checked against the gated set immediately before the write.

Rows rendered by an earlier run are file content, not a clearance, so the plain ``--update``
carry-forward path renders routing rows only and never reaches this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.atomicio import _atomic_write_text as atomic_write_text
from agentteams.cli import security_gate
from agentteams.cli.decision_log import _enforce_decision_signing
from agentteams.cli.exception_registry import ExceptionRegistryError, assert_within_aggregate_cap

#: The gate action id. Distinct from ``overwrite`` so neither clearance satisfies the other.
ACTION = "adopt-orphans-merge"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n", re.S)
_AGENTS_BLOCK_RE = re.compile(r"^agents:[ \t]*\n((?:[ \t]+-[ \t]*\S[^\n]*\n)+)", re.M)
_AGENTS_ITEM_RE = re.compile(r"^([ \t]+)-[ \t]*['\"]?([^'\"\s]+)['\"]?[ \t]*$", re.M)


class AdoptMergeError(RuntimeError):
    """Merge-mode adoption is refused (fail-closed)."""


@dataclass
class AdoptMergePlan:
    """What a merge-mode adoption will do to one orchestrator file."""

    orchestrator: Path
    to_add: list[str] = field(default_factory=list)
    clearance: dict[str, str] = field(default_factory=dict)

    def as_report(self) -> dict[str, object]:
        """Return the plan as the ``adopt_orphans_merge`` entry of a dry-run report."""
        return {"orchestrator": str(self.orchestrator), "agents_to_add": list(self.to_add),
                "action": ACTION, "clearance": dict(self.clearance)}


def existing_agents(text: str) -> list[str] | None:
    """Return the slugs in a file's front-matter ``agents:`` block list.

    Args:
        text: The orchestrator file content.

    Returns:
        The listed slugs in order, or ``None`` when the front matter has no ``agents`` key at all
        (a framework without a roster, such as Claude: routing rows alone carry adoption there).

    Raises:
        AdoptMergeError: An ``agents`` key exists in a shape this module does not edit (a flow
            list, CRLF line endings, an item with a comment or anything else unparsed). Refusing
            beats guessing: a mis-parse would make the exact-set check and the write disagree.
    """
    if "\r" in text[:4096]:
        raise AdoptMergeError("orchestrator uses CRLF line endings; normalize to LF before adopting")
    fm = _FRONT_MATTER_RE.match(text)
    if fm is None or not re.search(r"^agents[ \t]*:", fm.group(1), re.M):
        return None
    block = _AGENTS_BLOCK_RE.search(fm.group(1))
    if block is None:
        raise AdoptMergeError("orchestrator `agents:` is not a block list (`  - slug` lines); refusing to edit it")
    lines = block.group(1).splitlines()
    items = [m.group(2) for m in _AGENTS_ITEM_RE.finditer(block.group(1))]
    if len(items) != len(lines):
        raise AdoptMergeError("orchestrator `agents:` has a line this module cannot parse; refusing to edit it")
    return items


def union_agents(text: str, slugs: list[str]) -> tuple[str, list[str]]:
    """Append ``slugs`` missing from the front-matter ``agents:`` list; touch nothing else.

    Args:
        text: The orchestrator file content.
        slugs: Slugs to ensure are listed.

    Returns:
        ``(new_text, added)``; ``new_text == text`` when nothing is added.

    Raises:
        AdoptMergeError: The file has no editable ``agents:`` block list, or a slug is malformed.
    """
    if existing_agents(text) is None:
        raise AdoptMergeError("orchestrator front matter has no `agents:` block list to extend")
    _check_slugs(slugs)
    fm = _FRONT_MATTER_RE.match(text)
    block = _AGENTS_BLOCK_RE.search(fm.group(1))
    items = list(_AGENTS_ITEM_RE.finditer(block.group(1)))
    present = {m.group(2) for m in items}
    added = [s for s in sorted(set(slugs)) if s not in present]
    if not added:
        return text, []
    indent = items[-1].group(1)
    insert_at = fm.start(1) + block.end(1)
    addition = "".join(f"{indent}- {s}\n" for s in added)
    return text[:insert_at] + addition + text[insert_at:], added


def _check_slugs(slugs: list[str]) -> None:
    bad = [s for s in slugs if not _SLUG_RE.match(s)]
    if bad:
        raise AdoptMergeError(f"refusing malformed agent slug(s): {', '.join(sorted(bad))}")


def plan(output_dir: Path, orphan_slugs: list[str], agent_ext: str) -> AdoptMergePlan | None:
    """Work out which adopted slugs the orchestrator's ``agents:`` list lacks.

    Args:
        output_dir: The framework agent directory.
        orphan_slugs: Adoptable slugs discovered on disk (``adopted_agents.discover_orphans``).
        agent_ext: The framework's agent file extension.

    Returns:
        A plan, or ``None`` when this framework's orchestrator has no ``agents:`` list (routing
        rows alone carry adoption there — no capability is granted, so no gate applies).

    Raises:
        AdoptMergeError: A slug is malformed, or an adopted file is a symlink.
    """
    _check_slugs(orphan_slugs)
    links = [s for s in orphan_slugs if (output_dir / f"{s}{agent_ext}").is_symlink()]
    if links:
        raise AdoptMergeError(f"refusing to adopt symlinked agent file(s): {', '.join(sorted(links))}")
    orchestrator = output_dir / f"orchestrator{agent_ext}"
    if orchestrator.is_symlink():
        raise AdoptMergeError(f"refusing to edit a symlinked orchestrator ({orchestrator.name})")
    try:
        current = existing_agents(orchestrator.read_text(encoding="utf-8"))
    except OSError:
        return None
    if current is None:
        return None
    return AdoptMergePlan(orchestrator, sorted(set(orphan_slugs) - set(current)))


def authorize(output_dir: Path, adopt_plan: AdoptMergePlan, *, consume: bool) -> dict[str, str]:
    """Clear ``adopt_plan`` through the security gate, or refuse (fail-closed).

    Args:
        output_dir: The team root holding ``references/security-decisions.log.csv``.
        adopt_plan: The plan whose ``to_add`` slugs must be exactly what the clearance grants.
        consume: Spend the clearance (the real run) or only inspect it (``--dry-run``).

    Returns:
        The matched clearance row's identifying fields (date, action, key id, signature prefix),
        for the run output and build log.

    Raises:
        AdoptMergeError: Any condition fails; the message names what to fix.
    """
    try:
        return _authorize(output_dir, adopt_plan, consume=consume)
    except AdoptMergeError:
        raise
    except (ExceptionRegistryError, RuntimeError, OSError, ValueError) as exc:
        # An unreadable switch, a malformed log or an invalid waiver is still a refusal (fail-closed),
        # reported cleanly rather than as a traceback.
        raise AdoptMergeError(str(exc)) from exc


def _authorize(output_dir: Path, adopt_plan: AdoptMergePlan, *, consume: bool) -> dict[str, str]:
    if not _enforce_decision_signing(output_dir):
        raise AdoptMergeError(
            f"'{ACTION}' widens the orchestrator's `agents:` grant and is allowed only in a "
            "signing-governed workspace (enforce_decision_signing); use --overwrite --adopt-orphans "
            "with an `overwrite` clearance instead"
        )
    if security_gate._latest_security_waiver(output_dir, action=ACTION) is not None:
        raise AdoptMergeError(f"a waiver cannot authorize '{ACTION}'; record a signed decision row")
    row = security_gate._latest_security_decision(output_dir, action=ACTION)
    if row is None:
        raise AdoptMergeError(f"no unconsumed '{ACTION}' decision in references/security-decisions.log.csv")
    if (row.get("scope") or "").strip().lower() != ACTION:
        raise AdoptMergeError(f"the '{ACTION}' decision must carry scope={ACTION} exactly")
    granted = {g.strip() for g in re.split(r"[;,]", row.get("effect_grants") or "") if g.strip()}
    wanted = {f"agents:{s}" for s in adopt_plan.to_add}
    if granted != wanted:
        extra, missing = sorted(granted - wanted), sorted(wanted - granted)
        raise AdoptMergeError(
            f"the '{ACTION}' clearance must grant exactly this run's agents "
            f"(extra in clearance: {extra or 'none'}; missing from clearance: {missing or 'none'})"
        )
    assert_within_aggregate_cap(output_dir, adding=1)
    security_gate._assert_destructive_action_allowed(output_dir, action=ACTION, consume=consume)
    return {
        "date": (row.get("timestamp") or row.get("date") or "").strip(),
        "action_reviewed": (row.get("action_reviewed") or "").strip(),
        "key_id": (row.get("key_id") or "").strip(),
        "signature_prefix": (row.get("signature") or "").strip()[:16],
    }


def apply(adopt_plan: AdoptMergePlan) -> list[str]:
    """Append the gated slugs to the orchestrator's ``agents:`` list (atomic, append-only).

    Args:
        adopt_plan: A plan that :func:`authorize` cleared with ``consume=True``.

    Returns:
        The slugs actually added (``[]`` when already present).

    Raises:
        AdoptMergeError: The file now needs a different set than the one cleared.
    """
    text = adopt_plan.orchestrator.read_text(encoding="utf-8")
    current = set(existing_agents(text) or [])
    if set(adopt_plan.to_add) - current != set(adopt_plan.to_add):
        raise AdoptMergeError("the orchestrator's `agents:` list changed after clearance; re-run")
    new_text, added = union_agents(text, adopt_plan.to_add)
    if sorted(added) != sorted(adopt_plan.to_add):
        raise AdoptMergeError("refusing to write a set other than the one cleared")
    if added:
        atomic_write_text(adopt_plan.orchestrator, new_text)
    return added



#: Append-only record of every applied merge-mode adoption and the clearance that authorized it.
#: Separate from ``build-log.json``, which ``_write_run_log`` rewrites from scratch on every run.
RECORD_REL = "references/adopt-orphans-merge.log.csv"
_RECORD_COLUMNS = ("applied_at", "orchestrator", "added", "clearance_date", "key_id", "signature_prefix")


def run(
    output_dir: Path, orphan_slugs: list[str], agent_ext: str, *, dry_run: bool, backup: bool = True
) -> AdoptMergePlan | None:
    """Plan, clear and apply a merge-mode adoption, entirely before the render/emit phase.

    Self-contained on purpose: the ``--update`` pipeline can return early ("no material drift")
    before emit, so a clearance spent here must be applied here. A dry run never spends the
    clearance; it reports the exact slugs to sign and whether a matching clearance exists.

    Args:
        output_dir: The framework agent directory (also the team root holding ``references/``).
        orphan_slugs: Adoptable slugs discovered on disk.
        agent_ext: The framework's agent file extension.
        dry_run: Inspect only.
        backup: Back up the orchestrator first (``--no-backup`` turns this off).

    Returns:
        The plan (for the dry-run report), or ``None`` when there is no ``agents:`` list or
        nothing to add.

    Raises:
        AdoptMergeError: The run is not cleared, or the orchestrator changed after clearance.
    """
    adopt_plan = plan(output_dir, orphan_slugs, agent_ext)
    if adopt_plan is None or not adopt_plan.to_add:
        return None
    if dry_run:
        try:
            adopt_plan.clearance = dict(authorize(output_dir, adopt_plan, consume=False), status="cleared")
        except AdoptMergeError as exc:
            adopt_plan.clearance = {"status": "not cleared", "reason": str(exc)}
        print(f"  [DRY RUN] {ACTION}: would add to {adopt_plan.orchestrator.name} agents: "
              f"{', '.join(adopt_plan.to_add)} ({adopt_plan.clearance['status']})")
        return adopt_plan
    # Everything that can fail runs before the clearance is spent: a non-consuming check, then
    # the backup. Only then is the clearance consumed and the append written.
    authorize(output_dir, adopt_plan, consume=False)
    if backup:
        from agentteams.backup import backup_output_dir

        try:
            backup_output_dir(output_dir, files_to_backup=[adopt_plan.orchestrator.name], reason=ACTION)
        except OSError as exc:
            raise AdoptMergeError(f"backup of {adopt_plan.orchestrator.name} failed: {exc}") from exc
    adopt_plan.clearance = authorize(output_dir, adopt_plan, consume=True)
    added = apply(adopt_plan)
    record(output_dir, adopt_plan, added)
    print(f"  ✓  {ACTION}: added to {adopt_plan.orchestrator.name} agents: {', '.join(added)} "
          f"(cleared by the decision of {adopt_plan.clearance['date']}, key "
          f"{adopt_plan.clearance['key_id'] or '-'})")
    return adopt_plan


def record(output_dir: Path, adopt_plan: AdoptMergePlan, added: list[str]) -> None:
    """Append the applied adoption and its clearance to :data:`RECORD_REL`.

    Args:
        output_dir: The team root.
        adopt_plan: The applied plan.
        added: The slugs :func:`apply` wrote.

    Returns:
        None.

    Raises:
        AdoptMergeError: The record cannot be written (the grant is applied; the record must not
            silently go missing).
    """
    import csv
    from datetime import UTC, datetime

    from agentteams.atomicio import atomic_rewrite_csv_rows

    path = output_dir / RECORD_REL
    rows: list[dict[str, str]] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    rows.append({
        "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "orchestrator": adopt_plan.orchestrator.name,
        "added": ";".join(added),
        "clearance_date": adopt_plan.clearance.get("date", ""),
        "key_id": adopt_plan.clearance.get("key_id", ""),
        "signature_prefix": adopt_plan.clearance.get("signature_prefix", ""),
    })
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_rewrite_csv_rows(path, rows, list(_RECORD_COLUMNS))
    except (OSError, ValueError) as exc:
        raise AdoptMergeError(f"{ACTION} applied but its record could not be written: {exc}") from exc
