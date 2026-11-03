"""sync_hub_refresh.py — bring the canonical hub's instruction fences up to the pin's deployed ones.

``--sync`` reconciles agents only: the hub's ``instructions_binding.content`` is seeded by
``--sync-init`` and never refreshed, so it ages while the deployed instruction files are regenerated
from newer templates and a corrected brief. Projecting that stale text lowered fence versions and
brought back stale brief values (remediation-log 2026-11-03, HIGH). Projection now never lowers a
fence version (``interop_helpers.keep_newer_fences``); this module is the supported way to refresh
the hub itself (``--sync-refresh-instructions``) so same-version stale values go too.

The pin's deployed instruction file is the source (the pin wins, as everywhere in pinned sync),
except that a fence another framework in the sync set deploys at a higher ``v=`` is taken from that
framework: a pre-guard sync lowered the pin's own file too. Only fenced regions move; the hub's text
outside fences is kept, and the same max(v=) rule applies, so the hub never takes an older fence than
it has. A hub fence still below the newest template's version afterwards is reported: every deployed
copy is stale, and the pin needs a native ``--update`` before the refresh can help.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai
from agentteams.interop_helpers import _VERSIONED_BEGIN_RE, keep_newer_fences, template_fence_versions
from agentteams.multi_sync import framework_agents_dir
from agentteams.sync_pin import DEFAULT_CANONICAL_REL, read_pin

__all__ = ["HubRefreshResult", "refresh_hub_instructions"]


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
    from agentteams.fences import _merge_fenced_content

    root = Path(root)
    pin_doc = read_pin(root)
    if pin_doc is None:
        raise ValueError("project is not pinned — run --sync-init first")
    pin = pin_doc["pinned_framework"]
    canonical_dir = root / pin_doc.get("canonical_dir", DEFAULT_CANONICAL_REL)
    hub = load_canonical(canonical_dir)
    deployed = _deployed_instructions(root, pin)
    if not deployed:
        raise ValueError(f"pin {pin!r} has no deployed instruction file to refresh the hub from")
    for fw in pin_doc.get("frameworks", []):
        if fw != pin and (peer := _deployed_instructions(root, fw)):
            deployed, _ = keep_newer_fences(deployed, peer)  # a peer's higher fence beats the pin's
    binding = hub.setdefault("instructions_binding", {})
    current = str(binding.get("content", ""))
    result = HubRefreshResult(pin=pin, dry_run=dry_run)
    if current:
        incoming, result.notices = keep_newer_fences(deployed, current)
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
                f"fence '{m['sid']}' is v={m['v']} in the hub and every deployed copy, below the newest template's "
                f"v={newest[m['sid']]}; regenerate the pin natively (--update) and refresh again")
    if result.changed and not dry_run:
        binding["content"] = new
        binding.setdefault("source_name", "")
        materialize_canonical(hub, canonical_dir)
    return result


def _deployed_instructions(root: Path, framework: str) -> str:
    """The instruction content a framework deploys ("" when it has none or cannot be read)."""
    try:
        cai = export_to_cai(framework_agents_dir(root, framework), framework)
    except (OSError, ValueError):
        return ""
    return str(cai.get("instructions_binding", {}).get("content", ""))
