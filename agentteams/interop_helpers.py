"""interop_helpers.py — helper functions extracted from interop.py (CH-07).

A.3/A.4 helpers for escape-hatch capture, reference walking, raw-front-matter
serialization, and sidecar handoff merging. Extracted to keep interop.py under
the 1000-line CH-07 module ceiling.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

from agentteams.yaml_frontmatter import (
    parse_yaml_front_matter as _parse_yaml_front_matter,
)


# A.3: Escape-hatch capture for front-matter keys not already modeled.
# Keys that already have dedicated CAI fields and must NOT appear in
# raw_front_matter (they'd be redundant and could drift).
_MODELED_FM_KEYS = frozenset({
    "name", "description", "handoffs",
})


def capture_escape_hatches(
    content: str, framework: str,
) -> tuple[dict[str, Any], str | None, str | None]:
    """Capture the three CAI schema escape hatches from front matter.

    Returns ``(raw_front_matter, capabilities_raw, model_hint)``:
    - ``raw_front_matter``: any front-matter key not in ``_MODELED_FM_KEYS``,
      preserving the original parsed value (dict from YAML).
    - ``capabilities_raw``: the original capability-string for the framework
      (e.g. the raw ``tools:`` line value), or ``None``.
    - ``model_hint``: the ``model:`` front-matter value if present, or ``None``.

    Called only from the standard Markdown path (``parse_agent_source``
    returned ``None``); goose recipes have no front matter.

    Note: ``tools`` and ``allowed-tools`` are NOT in ``_MODELED_FM_KEYS``
    because while they ARE modeled into ``capabilities.tool_scopes``, their
    raw value is needed for ``capabilities.raw`` — so they are intercepted
    here for the raw capture but excluded from ``raw_front_matter``.
    """
    yaml_text, _ = _parse_yaml_front_matter(content)
    if yaml_text is None:
        return {}, None, None

    # Use canonical.py's YAML loader (PyYAML when available, fallback otherwise)
    from agentteams.canonical import _load_yaml_block

    fm = _load_yaml_block(yaml_text)
    if not isinstance(fm, dict):
        return {}, None, None

    raw_front_matter: dict[str, Any] = {}
    cap_raw: str | None = None
    model_hint: str | None = None
    _cap_keys = {"tools", "allowed-tools"}

    for key, value in fm.items():
        if key in _MODELED_FM_KEYS:
            continue
        if key in _cap_keys:
            # Already modeled via capabilities.tool_scopes, but capture the
            # raw string for capabilities.raw.
            if cap_raw is None:
                cap_raw = str(value) if not isinstance(value, str) else value
            continue
        if key == "model":
            # model_hint: keep the first scalar if it's a string, or
            # join a list. Preserve as-is otherwise.
            if isinstance(value, str):
                model_hint = value
            elif isinstance(value, list) and value:
                model_hint = str(value[0]) if len(value) == 1 else json.dumps(value)
            else:
                model_hint = str(value) if value is not None else None
        raw_front_matter[key] = value

    return raw_front_matter, cap_raw, model_hint


def capture_references(source_dir: Path) -> list[dict[str, Any]]:
    """Walk a native team's ``references/`` directory into the CAI ``references``
    list (A.3, report section 4.1).

    Each file becomes ``{"rel_path": str, "content": str}`` — the same shape
    ``canonical.materialize_canonical`` writes and ``load_canonical`` reads back.
    Returns an empty list when no ``references/`` directory exists.
    """
    refs_dir = source_dir / "references"
    if not refs_dir.is_dir():
        return []
    refs: list[dict[str, Any]] = []
    for p in sorted(refs_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_path = str(p.relative_to(source_dir))
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Binary or unreadable file — log and skip rather than crash the export.
            warnings.warn(
                f"references: skipping unreadable file {rel_path}", stacklevel=2
            )
            continue
        refs.append({"rel_path": rel_path, "content": content})
    return refs


def serialize_raw_fm_key(key: str, value: Any) -> str:
    """Serialize a raw_front_matter key-value pair into a YAML header line.

    A.2: Restores captured escape-hatch front-matter keys (user-invocable,
    model, agents:, etc.) back into the import-side YAML header so framework
    adapters like _ensure_yaml_front_matter don't overwrite them with defaults.

    Handles scalars (``user-invocable: true``), flow lists
    (``model: ["Claude Opus 4.8 (copilot)"]``), and block lists
    (``agents:\\n  - slug1\\n  - slug2``).
    """
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, str):
        # Quote if it contains special chars, else bare
        if value.startswith("[") or ":" in value or "#" in value:
            return f'{key}: "{value}"'
        return f"{key}: {value}"
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        # Use flow notation for short lists (model), block for longer ones (agents:)
        if len(value) <= 2 and all(isinstance(v, str) for v in value):
            items = ", ".join(f'"{v}"' for v in value)
            return f"{key}: [{items}]"
        # Block list
        lines = [f"{key}:"]
        for item in value:
            lines.append(f"  - {item}" if isinstance(item, str) else f"  - {json.dumps(item)}")
        return "\n".join(lines)
    # Fallback: JSON-encode complex values
    return f"{key}: {json.dumps(value)}"


def merge_sidecar_handoffs(agents: list[dict[str, Any]], source_dir: Path) -> None:
    """Read ``references/runtime-handoffs.json`` and merge handoffs into agents.

    A.4 (report section 4.3): the sidecar is written by ``import_from_cai`` for
    manifest-delivery frameworks (claude, copilot-cli, agents-md, codex) but
    was never read back by ``export_to_cai``, so handoffs vanished on every
    native→canonical round trip for those 4 frameworks.

    The sidecar sits at ``source_dir.parent / "references" / "runtime-handoffs.json"``
    and has the shape::

        {"agents": [{"agent": "slug", "handoffs": [{"label", "agent", "prompt", "send"}]}]}

    Only merges into agents that currently have **no** inline handoffs — inline
    handoffs (from native-delivery frameworks) always take priority.
    """
    sidecar = source_dir.parent / "references" / "runtime-handoffs.json"
    if not sidecar.is_file():
        return
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    sidecar_agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(sidecar_agents, list):
        return
    # Build a lookup: slug -> handoffs from the sidecar
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for entry in sidecar_agents:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("agent", "")).strip()
        if not slug:
            continue
        raw_handoffs = entry.get("handoffs") or []
        if not isinstance(raw_handoffs, list):
            continue
        normalized = [
            {
                "to": str(h.get("agent", "")).strip(),
                "label": h.get("label") or None,
                "prompt": str(h.get("prompt", "") or ""),
                "send": bool(h.get("send", False)),
            }
            for h in raw_handoffs
            if isinstance(h, dict) and str(h.get("agent", "")).strip()
        ]
        if normalized:
            by_slug[slug] = normalized
    if not by_slug:
        return
    for agent in agents:
        slug = agent.get("slug", "")
        # Only merge if the agent has no inline handoffs (manifest-delivery
        # frameworks write handoffs ONLY to the sidecar, so inline is empty).
        if not agent.get("handoffs") and slug in by_slug:
            agent["handoffs"] = by_slug[slug]
