"""sync_hub_refresh.py — bring the canonical hub's instruction fences up to the pin's deployed ones.

``--sync`` reconciles agents three-way, but the hub's ``instructions_binding.content`` is seeded by
``--sync-init`` and was never refreshed, so it aged while the deployed instruction files were regenerated
from newer templates and a corrected brief. Projecting that stale text lowered fence versions and
brought back stale brief values (remediation-log 2026-11-03, HIGH). Projection now never lowers a
fence version (``interop_helpers.keep_newer_fences``), and ``run_sync`` refreshes the hub through
:func:`refresh_hub_binding` before every projection, so same-version stale values go too.
``--sync-refresh-instructions`` runs the same refresh on its own.

The pin's deployed instruction file is the source (the pin wins, as everywhere in pinned sync).
The operator-run ``--sync-refresh-instructions`` also takes a fence another framework in the sync set
deploys at a higher ``v=`` (a pre-guard sync lowered the pin's own file too); ``--sync`` and
``--sync-init`` refuse such a pin first (:func:`pin_instruction_problem`). Only fenced regions move;
the hub's text outside fences is kept (a hub with no fences at all is replaced by the pin's file, since
nothing in it could merge), and the same max(v=) rule applies, so the hub never takes an older fence
than it has. A hub fence still below the newest template's version afterwards is reported: the copies it was refreshed
from are stale too, and the pin needs a native ``--update`` before the refresh can help.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.backup import backup_output_dir
from agentteams.canonical import TEAM_FILE_NAME, load_canonical, materialize_canonical
from agentteams.frameworks.registry import agents_dir_for
from agentteams.interop import export_to_cai
from agentteams.interop_helpers import _VERSIONED_BEGIN_RE, keep_newer_fences, template_fence_versions
from agentteams.sync_pin import DEFAULT_CANONICAL_REL, read_pin

__all__ = [
    "HubRefreshResult",
    "pin_instruction_problem",
    "refresh_hub_binding",
    "refresh_hub_for_projection",
    "refresh_hub_instructions",
    "refuse_unsafe_pin_instructions",
]

#: Reads the fence id and both versions back out of a ``keep_newer_fences`` "kept" notice.
_KEPT_RE = re.compile(r"'(?P<sid>[a-z][a-z0-9_]*)' at v=(?P<peer>\d+) \(incoming v=(?P<pin>\d+)")


@dataclass
class HubRefreshResult:
    """Outcome of a hub instruction-fence refresh."""

    pin: str
    refreshed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    changed: bool = False
    dry_run: bool = False
    #: The hub's instruction content had no fences and was replaced wholesale by the pin's (row 662 (2)):
    #: the save paths back up ``team.cai.json`` first, since a fence merge would have kept the old text.
    replaced_fenceless: bool = False


def refresh_hub_instructions(root: Path, *, dry_run: bool = False) -> HubRefreshResult:
    """Fence-merge the pin's deployed instruction file into the canonical hub.

    Args:
        root: The pinned project root.
        dry_run: Report what would change without writing.

    Returns:
        A :class:`HubRefreshResult` naming the fences replaced or added.

    Raises:
        ValueError: If the project is not pinned, the pin has no instruction file, or either
            side's fences do not parse (nothing is written).
        FileNotFoundError: If the pin's canonical hub directory has no ``team.cai.json``.
    """
    root = Path(root)
    pin_doc = read_pin(root)
    if pin_doc is None:
        raise ValueError("project is not pinned — run --sync-init first")
    canonical_dir = root / pin_doc.get("canonical_dir", DEFAULT_CANONICAL_REL)
    hub = load_canonical(canonical_dir)
    result = refresh_hub_binding(root, pin_doc, hub, dry_run=dry_run)
    if result.changed and not dry_run:
        _backup_fenceless_hub(canonical_dir, result)
        materialize_canonical(hub, canonical_dir)
    return result


def _backup_fenceless_hub(canonical_dir: Path, result: HubRefreshResult) -> None:
    """Back up the hub's ``team.cai.json`` before a fence-less instruction text is replaced (row 662 (2)).

    The backup goes with the hub's other backups, ``agents/.agentteams-backups/`` (as
    ``__external__/team.cai.json…``), so ``--restore-backup`` on the hub's agents dir maps it back.

    Args:
        canonical_dir: The hub's directory.
        result: The refresh outcome; nothing is done unless ``replaced_fenceless`` is set. A notice names
            the backup, or says it could not be taken (the replacement still proceeds: git history, if the
            hub is committed, is then the only copy).
    """
    if not result.replaced_fenceless:
        return
    agents_dir = Path(canonical_dir) / "agents"
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_output_dir(agents_dir, files_to_backup=[f"../{TEAM_FILE_NAME}"],
                                   reason="pre-sync-hub-fenceless-replace")
    except (OSError, ValueError) as exc:
        result.notices.append(f"could not back up the hub's fence-less instruction text before replacing it: {exc}")
        return
    if backup.backup_path is not None:
        result.notices.append(f"the hub's old fence-less instruction text is backed up in {backup.backup_path}")


def pin_instruction_problem(root: Path, pin_doc: dict[str, Any], hub: dict[str, Any]) -> str:
    """Why the pin's deployed instruction file is unsafe to sync from (``""`` when it is safe).

    The pin wins, so a bad pin file would spread to every framework. Unsafe means: the file is missing or
    has no parseable fences while the hub has fences to project, or another framework deploys a fence
    newer than both the pin and the hub. Stale values at the same version cannot be detected here.

    Args:
        root: The pinned project root.
        pin_doc: The parsed pin (``pinned_framework`` and ``frameworks``).
        hub: The canonical team about to be projected.

    Returns:
        The problem and its remedy, or ``""``.
    """
    from agentteams.fences import _extract_fenced_regions

    root = Path(root)
    pin = pin_doc["pinned_framework"]
    current = str(hub.get("instructions_binding", {}).get("content", ""))
    hub_regions = _extract_fenced_regions(current) if current else {}
    hub_has_fences = isinstance(hub_regions, dict) and bool(hub_regions)
    # --update --merge can't repair these: it skips a file with no parseable fences and doesn't recreate a missing one.
    restore = f"restore it from git or the newest {pin} .agentteams-backups copy, or repair its fence markers by hand"
    deployed = _deployed_instructions(root, pin)
    if not deployed:
        return f"the pin ({pin}) has no deployed instruction file; {restore}" if hub_has_fences else ""
    regions = _extract_fenced_regions(deployed)
    if isinstance(regions, str):
        return f"the pin's ({pin}) instruction file has unparseable fences ({regions}); {restore}"
    if not regions and hub_has_fences:
        return f"the pin's ({pin}) instruction file has no AGENTTEAMS fences; {restore}"
    effective = keep_newer_fences(deployed, current)[0] if current else deployed
    newer: list[str] = []
    for fw in pin_doc.get("frameworks", []):
        if fw != pin and (peer := _deployed_instructions(root, fw)):
            newer += [f"{fw} '{m['sid']}' v={m['peer']} (pin v={m['pin']})"
                      for m in map(_KEPT_RE.search, keep_newer_fences(effective, peer)[1]) if m]
    if not newer:
        return ""
    remedy = f"update the pin natively (--framework {pin} --update --merge), then sync again"
    if read_pin(root) is not None:  # --sync-refresh-instructions needs an existing pin; --sync-init has none yet
        remedy += ", or review those fences and take them into the hub with --sync-refresh-instructions"
    return f"other frameworks deploy newer fences than the pin: {', '.join(newer)}; {remedy}"


def refresh_hub_binding(root: Path, pin_doc: dict[str, Any], hub: dict[str, Any], *,
                        dry_run: bool = False, take_peer_fences: bool = True) -> HubRefreshResult:
    """Refresh *hub*'s ``instructions_binding`` in memory from the pin's deployed instruction file.

    ``run_sync`` calls this before every projection, so a hub that aged since ``--sync-init`` never
    carries stale fence bodies (same version or older) into the deployed files.

    Args:
        root: The pinned project root.
        pin_doc: The parsed pin (``sync_pin.read_pin``).
        hub: The loaded canonical team; its ``instructions_binding.content`` is updated in place
            unless *dry_run*.
        dry_run: Report what would change without touching *hub*.
        take_peer_fences: Take a fence another framework in the sync set deploys at a higher ``v=``
            than the pin (the operator-run ``--sync-refresh-instructions``). ``run_sync`` passes False:
            an automatic pass keeps the pin authoritative, so an edited peer file cannot spread its
            body to every framework unasked.

    Returns:
        A :class:`HubRefreshResult` naming the fences replaced or added.

    Raises:
        ValueError: If the pin has no deployed instruction file, or either side's fences do not parse
            (*hub* is left unchanged).
    """
    from agentteams.fences import _extract_fenced_regions, _merge_fenced_content

    root = Path(root)
    pin = pin_doc["pinned_framework"]
    deployed = _deployed_instructions(root, pin)
    if not deployed:
        raise ValueError(f"pin {pin!r} has no deployed instruction file to refresh the hub from")
    result = HubRefreshResult(pin=pin, dry_run=dry_run)
    seed_note = "the hub had no instruction content; seeded from the pin"
    for fw in pin_doc.get("frameworks", []) if take_peer_fences else ():
        if fw != pin and (peer := _deployed_instructions(root, fw)):
            deployed, kept = keep_newer_fences(deployed, peer)  # a peer's higher fence beats the pin's
            # Only its version number says this body is newer, so name what the operator should now review.
            result.notices += [f"took fence '{m['sid']}' v={m['peer']} from {fw}, not the pin (v={m['pin']}): "
                               "review its body" for m in map(_KEPT_RE.search, kept) if m]
    binding = hub.setdefault("instructions_binding", {})
    current = str(binding.get("content", ""))
    pin_regions = _extract_fenced_regions(deployed)
    if current and _extract_fenced_regions(current) == {} and isinstance(pin_regions, dict) and pin_regions:
        # A fence-less hub can't be fence-merged, so it never refreshed and projected nothing (remediation-log
        # 2026-11-04). Its text came from a pin at --sync-init; the pin decides, so it is replaced.
        current, seed_note = "", ("the hub's instruction content had no AGENTTEAMS fences; replaced with the "
                                  "pin's (a real run backs up team.cai.json first)")
        result.replaced_fenceless = True
    if current:
        incoming, hub_kept = keep_newer_fences(deployed, current)
        result.notices.extend(hub_kept)
        # No shrink-preserve: the operator asked for the pin's bodies, and a stale hub body is
        # often the longer one (it still lists src/ and templates/).
        merge = _merge_fenced_content(incoming, current)
        if merge.parse_errors:
            raise ValueError("cannot refresh the hub's instructions: " + "; ".join(merge.parse_errors))
        new, result.refreshed, result.added = merge.merged_content, merge.sections_replaced, merge.sections_added
    else:
        new = deployed
        result.notices.append(seed_note)
    result.changed = new != str(binding.get("content", ""))
    newest = template_fence_versions()
    for m in _VERSIONED_BEGIN_RE.finditer(new):
        if int(m["v"]) < newest.get(m["sid"], 0):
            result.notices.append(
                f"fence '{m['sid']}' is v={m['v']} in the hub and the "
                f"{'deployed copies' if take_peer_fences else 'pin'}, below the newest template's "
                f"v={newest[m['sid']]}; regenerate the pin natively (--update) and refresh again")
    if result.changed and not dry_run:
        binding["content"] = new
        binding.setdefault("source_name", "")
    return result


def refuse_unsafe_pin_instructions(root: Path, pin_doc: dict[str, Any], hub: dict[str, Any]) -> None:
    """Refuse a sync whose pin instruction file would spread bad content, before anything is written.

    Args:
        root: The pinned project root.
        pin_doc: The parsed pin (``pinned_framework`` and ``frameworks``).
        hub: The canonical team about to be projected.

    Raises:
        ValueError: :func:`pin_instruction_problem` found a problem.
    """
    problem = pin_instruction_problem(root, pin_doc, hub)
    if problem:
        raise ValueError(f"refusing to sync, nothing was written: {problem}")


def refresh_hub_for_projection(root: Path, pin_doc: dict[str, Any], hub: dict[str, Any], canonical_dir: Path, *,
                               dry_run: bool, notices: list[str]) -> None:
    """Bring the hub's instruction fences up to the pin's deployed file before projecting (pin wins).

    The hub's instruction content is seeded at ``--sync-init`` and otherwise never reconciled, so without
    this a stale hub projected same-version stale fence bodies over current deployed files. *hub* is
    refreshed in memory even under *dry_run*, so the dry projection previews what a real run writes; only
    the save is skipped. Peers' fences are never taken: :func:`refuse_unsafe_pin_instructions` has already
    refused a pin older than a peer.

    Args:
        root: The pinned project root.
        pin_doc: The parsed pin.
        hub: The canonical team about to be projected (updated in place).
        canonical_dir: The hub's directory.
        dry_run: Skip the save.
        notices: Receives what was refreshed, or why it could not be.
    """
    try:
        result = refresh_hub_binding(root, pin_doc, hub, take_peer_fences=False)
    except ValueError as exc:
        notices.append(f"hub instructions not refreshed from the pin: {exc}")
        return
    notices.extend(result.notices)
    notices.extend(_peer_fences_replaced(root, pin_doc, hub))
    if not result.changed:
        return
    moved = ", ".join(result.refreshed + result.added) or "content outside fences only"
    notices.append(f"hub instruction fences {'would be ' if dry_run else ''}refreshed from the pin "
                   f"({result.pin}): {moved}")
    if not dry_run:
        already = len(result.notices)
        _backup_fenceless_hub(canonical_dir, result)
        notices.extend(result.notices[already:])
    materialize_canonical(hub, canonical_dir, dry_run=dry_run)


def _peer_fences_replaced(root: Path, pin_doc: dict[str, Any], hub: dict[str, Any]) -> list[str]:
    """One notice per peer whose fence bodies differ from the hub's, so the projection doesn't replace them unsaid.

    ``--sync`` classifies agent fields only; a peer's edit inside an instruction fence gets no log row.
    """
    from agentteams.fences import _extract_fenced_regions

    hub_regions = _extract_fenced_regions(str(hub.get("instructions_binding", {}).get("content", "")))
    if isinstance(hub_regions, str):
        return []
    out: list[str] = []
    for fw in pin_doc.get("frameworks", []):
        peer = _deployed_instructions(root, fw) if fw != pin_doc["pinned_framework"] else ""
        regions = _extract_fenced_regions(peer) if peer else {}
        if isinstance(regions, dict) and (differ := sorted(s for s, b in regions.items() if s in hub_regions
                                                           and b != hub_regions[s])):
            out.append(f"{fw}: instruction fences {', '.join(differ)} differ from the pin's and get the pin's text "
                       f"(the projection backup keeps {fw}'s copy)")
    return out


def _deployed_instructions(root: Path, framework: str) -> str:
    """The instruction content a framework deploys ("" when it has none or cannot be read)."""
    try:
        cai = export_to_cai(agents_dir_for(framework, Path(root)), framework)
    except (OSError, ValueError):
        return ""
    return str(cai.get("instructions_binding", {}).get("content", ""))
