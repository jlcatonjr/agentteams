"""Opaque repository aliases: how the remediation agent names a repository without seeing its path.

The gateway's DLP redacts home paths in everything the model reads, so an absolute repository path in a
fix plan or a finding reaches the agent mangled, and a ``stage-fix`` it asks for with a guessed path is
refused. The MCP server (:mod:`agentteams.vulnscan.mcp`) therefore shows each repository as an alias,
``<name>-<8 hex>`` (also the fix plan's file-name key, :func:`agentteams.vulnscan.daily.plan_slug`, and the
executor's state key), and maps an alias the agent sends back to the host path before it writes the
request. Only an alias of a repository named in this run's fix plans or findings is accepted; two paths
that share an alias are both left unmapped. The executor re-validates the path as before, so this is a
convenience, not a trust boundary. The DLP is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: The line ``fix.FixResult.markdown`` writes for the repository a daily fix plan is for (its third line).
PLAN_TARGET = re.compile(r"^Repository \(the `stage-fix` target\): `([^`\n]+)`$", re.M)


def alias(path: str, *, empty: str = "root") -> str:
    """Return the alias of the repository at ``path``: its sanitised name plus a digest of the full path.

    Args:
        path: Repository path, exactly as written (an absolute, resolved path string).
        empty: The name used when the path has none (``/``). The executor's state files pass ``""``,
            which keeps their existing names byte-identical.

    Returns:
        ``<name>-<8 hex>``.
    """
    name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(path).name or empty)
    return f"{name}-{hashlib.sha256(path.encode()).hexdigest()[:8]}"


def known(run_dir: Path, *, extra: Iterable[str] = ()) -> dict[str, str]:
    """Return ``{alias: path}`` for every repository this run's fix plans or findings name, plus ``extra``.

    Two different paths with the same alias are both dropped (never last-wins): the agent cannot name
    either one by alias, and the tools keep showing them as paths.

    Args:
        run_dir: The mounted run directory.
        extra: More repository paths the host listed, such as the staged fixes awaiting a PR.

    Returns:
        The mapping; empty when nothing is readable. Only absolute paths are mapped.
    """
    paths: list[str] = []
    for plan in sorted([*run_dir.glob("fixplan-*.md"), *run_dir.glob("fix-*.md")]):
        lines = plan.read_text(encoding="utf-8", errors="replace").splitlines()
        m = PLAN_TARGET.fullmatch(lines[2]) if len(lines) > 2 else None
        if m:
            paths.append(m.group(1))
    rows = _finding_rows(run_dir / "summary.json")
    paths += [f["target"] for f in rows if isinstance(f, dict) and isinstance(f.get("target"), str)]
    paths += [p for p in extra if isinstance(p, str)]
    out: dict[str, str] = {}
    clashed: set[str] = set()
    for p in dict.fromkeys(p for p in paths if p.startswith("/") and "\n" not in p):
        name = alias(p)
        if name in out and out[name] != p:
            clashed.add(name)
        out.setdefault(name, p)
    return {k: v for k, v in out.items() if k not in clashed}


def _finding_rows(path: Path) -> list:
    """Return the findings list of ``summary.json``; [] when it is missing or malformed (plans still map)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("findings", []) if isinstance(data, dict) else []
    return rows if isinstance(rows, list) else []


def resolve(target: str, aliases: dict[str, str]) -> str:
    """Return the host path for an alias, or ``target`` unchanged when it is an absolute path.

    Args:
        target: What the agent sent: an alias, or (backward compatible) an absolute path.
        aliases: :func:`known` for this run.

    Returns:
        The path to put in the request.

    Raises:
        KeyError: ``target`` is neither an absolute path nor a known alias.
    """
    if target.startswith("/"):
        return target
    return aliases[target]


def hide_paths(text: str, aliases: dict[str, str]) -> str:
    """Replace every known repository path in ``text`` with its alias (longest path first).

    Args:
        text: Tool output.
        aliases: :func:`known` for this run.

    Returns:
        The text with repository paths shown as aliases.
    """
    for name, path in sorted(aliases.items(), key=lambda kv: -len(kv[1])):
        text = re.sub(re.escape(path) + r"(?![A-Za-z0-9._-])", lambda _m, n=name: n, text)
    return text


def hide_in(value: Any, aliases: dict[str, str]) -> Any:
    """Return ``value`` (parsed JSON) with :func:`hide_paths` applied to every string in it, keys included.

    Args:
        value: A JSON value, such as an executor result.
        aliases: :func:`known` for this run.

    Returns:
        A copy with repository paths shown as aliases.
    """
    if isinstance(value, str):
        return hide_paths(value, aliases)
    if isinstance(value, list):
        return [hide_in(v, aliases) for v in value]
    if isinstance(value, dict):
        return {hide_paths(str(k), aliases): hide_in(v, aliases) for k, v in value.items()}
    return value
