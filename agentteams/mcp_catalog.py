"""mcp_catalog.py — the catalogue of foundational MCP servers every team can draw on.

One JSON file per server under ``agentteams/templates/mcp/``. Each is a valid ``mcp-server.schema.json`` entry
plus catalogue-only keys (:data:`CATALOG_KEYS`) that are removed before the entry reaches the manifest:

* ``catalog_default`` — ``True`` for the first-party read-only servers (``agentteams-recall``,
  ``agentteams-gitread``), which are on wherever MCP is enabled for the framework; ``False`` for opt-in templates
  (``github-read``, ``github-write``, ``fetch``);
* ``role_scope`` — the agent slugs the server suits, intersected with the team's roster;
* ``pin`` — the vetted upstream release (C4), for third-party servers.

:func:`expand` runs after ``manifest["host_features"]`` is set. It adds the selected servers to
``manifest["mcp_servers"]`` after any operator-declared ones; activation is unchanged machinery (Goose and Codex
wire only first-party read-only servers; Claude gets the inert ``.claude/mcp-servers.agentteams.json``). With no
MCP token and no opt-in, the manifest is untouched.

Operator decisions (2026-10-07, ``references/plans/mcp-catalog.design.md`` §8):

* under ``write_policy: "orchestrator-only"`` the default servers are not emitted, and the GitHub servers are
  withheld until P5 — so nothing from the catalogue is emitted under the switch;
* Codex ignores ``scope``, so its default-on servers are withheld unless explicitly opted in (``mcp_catalog``);
* a brief's own ``mcp_servers`` entry with the same ``server_id`` wins.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

#: Where the catalogue lives.
CATALOG_DIR = Path(__file__).resolve().parent / "templates" / "mcp"
#: Keys that exist only in the catalogue and are stripped before an entry reaches the manifest.
CATALOG_KEYS = ("catalog_default", "role_scope", "pin")
#: Catalogue ids that bring the PR agents (``pr-manager``, ``pr-notifier``) into the roster.
GITHUB_IDS = frozenset({"github-read", "github-write"})
#: Withheld under the switch until P5 (signed per-agent grants) lands.
_WITHHELD_UNDER_SWITCH = GITHUB_IDS


def load_catalog() -> dict[str, dict[str, Any]]:
    """Load every catalogue entry.

    Returns:
        ``server_id`` -> the raw entry, catalogue keys included.

    Raises:
        ValueError: A catalogue file is unreadable, or its ``server_id`` does not match its file name.
    """
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(CATALOG_DIR.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"MCP catalogue entry {path.name} is unreadable: {exc}") from exc
        if entry.get("server_id") != path.stem:
            raise ValueError(f"MCP catalogue entry {path.name} declares server_id {entry.get('server_id')!r}")
        out[path.stem] = entry
    return out


def selection(description: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Read and validate the brief's ``mcp_catalog`` opt-ins and ``mcp_catalog_exclude`` exclusions.

    Args:
        description: The project brief.

    Returns:
        ``(opt_ins, excludes)``, each in brief order without duplicates.

    Raises:
        ValueError: Either field is not a list of strings, or names an id not in the catalogue.
    """
    known = load_catalog()
    lists = []
    for field in ("mcp_catalog", "mcp_catalog_exclude"):
        raw = description.get(field) or []
        if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
            raise ValueError(f"{field} must be a list of MCP catalogue ids")
        unknown = [i for i in raw if i not in known]
        if unknown:
            raise ValueError(f"{field}: unknown MCP catalogue id(s) {', '.join(unknown)}; "
                             f"known: {', '.join(sorted(known))}")
        lists.append(list(dict.fromkeys(raw)))
    return lists[0], lists[1]


def wants_pr_agents(description: dict[str, Any]) -> bool:
    """True when the brief brings in the PR agents: ``pr_management: true`` or a GitHub catalogue opt-in.

    Args:
        description: The project brief.

    Returns:
        Whether ``pr-manager`` and ``pr-notifier`` join the roster.
    """
    opted = description.get("mcp_catalog") or []
    return description.get("pr_management") is True or (
        isinstance(opted, list) and any(i in GITHUB_IDS for i in opted))


def _mcp_on(features: list[str], framework_id: str) -> bool:
    if framework_id == "goose":
        from agentteams.frameworks.goose import _GOOSE_MCP_TOKEN

        return _GOOSE_MCP_TOKEN in features
    if framework_id == "codex":
        from agentteams.codex_mcp_emit import codex_mcp_enabled

        return codex_mcp_enabled(features)
    from agentteams.mcp_emit import mcp_enabled

    return mcp_enabled(features)


def expand(manifest: dict[str, Any], framework_id: str) -> list[str]:
    """Add the selected catalogue servers to ``manifest["mcp_servers"]`` (mutated in place).

    Args:
        manifest: The team manifest, with ``host_features`` already resolved and the brief's ``mcp_catalog`` /
            ``mcp_catalog_exclude`` carried through by analyze.
        framework_id: The target framework id.

    Returns:
        Human-readable notices: every server withheld or skipped, and why. Empty when nothing applies.

    Raises:
        ValueError: The catalogue is unreadable or an id is unknown.
    """
    opt_ins = list(manifest.get("mcp_catalog") or [])
    excludes = set(manifest.get("mcp_catalog_exclude") or [])
    features = list(manifest.get("host_features") or [])
    mcp_on = _mcp_on(features, framework_id)
    if not opt_ins and not mcp_on:
        return []
    catalog = load_catalog()
    unknown = [i for i in [*opt_ins, *excludes] if i not in catalog]
    if unknown:
        raise ValueError(f"unknown MCP catalogue id(s) {', '.join(unknown)}")
    notices: list[str] = []
    chosen: list[str] = []
    if mcp_on:
        defaults = [i for i, e in catalog.items() if e.get("catalog_default") is True]
        if framework_id == "codex":
            held = [i for i in defaults if i not in opt_ins]
            if held:
                notices.append(f"Codex applies MCP servers to the whole project (it ignores scope), so the default "
                               f"server(s) {', '.join(held)} are withheld; add them to mcp_catalog to opt in.")
            defaults = [i for i in defaults if i in opt_ins]
        chosen.extend(defaults)
    chosen.extend(i for i in opt_ins if i not in chosen)
    chosen = [i for i in chosen if i not in excludes]

    switch = manifest.get("write_policy") == "orchestrator-only"
    roster = set(manifest.get("agent_slug_list") or [])
    declared = {s.get("server_id") for s in manifest.get("mcp_servers") or [] if isinstance(s, dict)}
    added: list[dict[str, Any]] = []
    for sid in chosen:
        entry = catalog[sid]
        if sid in declared:
            notices.append(f"{sid}: the brief declares its own server with this id; the catalogue copy is skipped.")
            continue
        if switch:
            why = ("withheld until P5 (signed per-agent grants)" if sid in _WITHHELD_UNDER_SWITCH
                   else "not emitted (the orchestrator uses the CLI and git directly)")
            notices.append(f"{sid}: write_policy orchestrator-only — {why}.")
            continue
        scope = [s for s in entry.get("role_scope") or [] if s in roster]
        if not scope:
            notices.append(f"{sid}: no agent in this team's roster matches its role scope; not emitted.")
            continue
        server = {k: copy.deepcopy(v) for k, v in entry.items() if k not in CATALOG_KEYS}
        server["scope"] = scope
        added.append(server)
    if added:
        manifest["mcp_servers"] = list(manifest.get("mcp_servers") or []) + added
    return notices


__all__ = ["CATALOG_DIR", "CATALOG_KEYS", "GITHUB_IDS", "expand", "load_catalog", "selection", "wants_pr_agents"]
