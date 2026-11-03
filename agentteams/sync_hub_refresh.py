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
deploys at a higher ``v=`` (a pre-guard sync lowered the pin's own file too); the automatic refresh in
``run_sync`` only reports such a fence. Only fenced regions move; the hub's text outside fences is
kept, and the same max(v=) rule applies, so the hub never takes an older fence than it has. A hub
fence still below the newest template's version afterwards is reported: the copies it was refreshed
from are stale too, and the pin needs a native ``--update`` before the refresh can help.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.frameworks.registry import agents_dir_for
from agentteams.interop import export_to_cai
from agentteams.interop_helpers import _VERSIONED_BEGIN_RE, keep_newer_fences, template_fence_versions
from agentteams.sync_pin import DEFAULT_CANONICAL_REL, read_pin

__all__ = ["HubRefreshResult", "refresh_hub_binding", "refresh_hub_instructions"]

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
        materialize_canonical(hub, canonical_dir)
    return result


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
            an automatic pass keeps the pin authoritative and only reports such a fence, so an edited
            peer file cannot spread its body to every framework unasked.

    Returns:
        A :class:`HubRefreshResult` naming the fences replaced or added.

    Raises:
        ValueError: If the pin has no deployed instruction file, or either side's fences do not parse
            (*hub* is left unchanged).
    """
    from agentteams.fences import _merge_fenced_content

    root = Path(root)
    pin = pin_doc["pinned_framework"]
    deployed = _deployed_instructions(root, pin)
    if not deployed:
        raise ValueError(f"pin {pin!r} has no deployed instruction file to refresh the hub from")
    result = HubRefreshResult(pin=pin, dry_run=dry_run)
    for fw in pin_doc.get("frameworks", []):
        if fw == pin or not (peer := _deployed_instructions(root, fw)):
            continue
        with_peer, kept = keep_newer_fences(deployed, peer)  # a peer's higher fence beats the pin's
        if take_peer_fences:
            deployed = with_peer
        elif newer := [f"'{m['sid']}' v={m['peer']} (pin v={m['pin']})" for m in map(_KEPT_RE.search, kept) if m]:
            result.notices.append(
                f"{fw} deploys newer fences than the pin: {', '.join(newer)}; not taken automatically (the pin "
                f"wins). Regenerate the pin natively (--update), or run --sync-refresh-instructions to take them")
    binding = hub.setdefault("instructions_binding", {})
    current = str(binding.get("content", ""))
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
        result.notices.append("the hub had no instruction content; seeded from the pin")
    result.changed = new != current
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


def _deployed_instructions(root: Path, framework: str) -> str:
    """The instruction content a framework deploys ("" when it has none or cannot be read)."""
    try:
        cai = export_to_cai(agents_dir_for(framework, Path(root)), framework)
    except (OSError, ValueError):
        return ""
    return str(cai.get("instructions_binding", {}).get("content", ""))
