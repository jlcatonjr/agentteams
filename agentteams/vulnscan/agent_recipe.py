"""The selected agent definition (``agent.recipe``): which file a run loads, and what the self-test binds of it.

@security rc.4 review, Medium 1: a run loads ``agent.recipe`` (default: the driver's), so the self-test runs
with the same file (:attr:`.agent_driver_base.Driver.recipe`, set by :func:`.agent_drivers.driver_for`) and the
flags fingerprint binds it (:func:`binding`). What is bound depends on what the CLI does with the file:

| Framework | How the CLI reads the file | Bound in ``flags_sha256`` |
|---|---|---|
| copilot | ``--agent``: YAML front matter is **configuration** (``tools``, ``model``, ``mcp-servers`` ...), the body is the prompt | path **and** sha256 of the whole file; a front matter with any key but ``name`` / ``description`` is refused (:func:`front_matter_problem`), at run time and in the self-test |
| claude | ``--append-system-prompt-file``: appended to the system prompt verbatim, never parsed | path only |
| codex | ``model_instructions_file``: the instructions text, never parsed | path only |
| goose | ``--recipe``: a YAML recipe (its own prompt and settings) | path only (see the residual below) |

claude and codex read the file as prompt text only: its content configures no tool, server or model, so a
prompt edit must not invalidate the self-test (a regenerated team would otherwise churn it daily); the path is
bound so the self-test runs with the selected file. goose's recipe *can* declare ``extensions`` and other
settings; goose's self-test runs no CLI and judges nothing in the recipe, so binding its content would ask for a
self-test that cannot check it (documented residual; the generated recipe declares no extension).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agentteams.vulnscan.agent_driver_base import Driver

#: The only front-matter keys a Copilot agent file may carry (everything else is configuration).
COPILOT_KEYS = frozenset({"name", "description"})
#: Frameworks whose agent file content is bound in the fingerprint (its front matter is configuration).
CONTENT_BOUND = frozenset({"copilot"})
_KEY_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t]+(.*))?")
_SINGLE = re.compile(r"'(?:[^']|'')*'")
#: A plain YAML scalar may not start with an indicator (flow, block, anchor, alias, tag, directive ...).
_INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`")


def selected(a: dict[str, Any], d: Driver) -> str:
    """Return the agent definition a run loads, relative to the team: ``agent.recipe``, else the driver's."""
    return str(a.get("recipe") or d.default_recipe).lstrip("/")


def _scalar_problem(key: str, value: str) -> str:
    """Return why a front-matter value is not a one-line string scalar, or ``""``."""
    if value.startswith('"'):
        try:
            ok = isinstance(json.loads(value), str)
        except ValueError:
            ok = False
        return "" if ok else f"front matter {key!r}: unparseable double-quoted value"
    if value.startswith("'"):
        return "" if _SINGLE.fullmatch(value) else f"front matter {key!r}: unparseable single-quoted value"
    if not value or value.startswith(_INDICATORS) or ": " in value or " #" in value or value.endswith(":"):
        return f"front matter {key!r} must be a one-line string (plain or quoted)"
    return ""


def front_matter_problem(text: str) -> str:
    """Return why a Copilot agent file's front matter is refused, or ``""`` (none, or ``name``/``description`` only).

    A strict subset of YAML, so no YAML library is needed and nothing ambiguous passes: ``key: value`` lines at
    column 0 with one-line string values, blank lines and ``#`` comment lines. Anything else (an indented or
    continuation line, a block or flow value, an anchor, a duplicate key, an unclosed block) is refused as
    unparseable, and any key other than :data:`COPILOT_KEYS` as configuration.
    """
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip()), len(lines))
    if start == len(lines) or not lines[start].lstrip().startswith("---"):
        return ""                                          # no front matter: the whole file is the prompt
    if lines[start].rstrip() != "---":
        return "unparseable front matter (the opening line must be exactly ---)"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].rstrip() in ("---", "...")), None)
    if end is None:
        return "unparseable front matter (no closing ---)"
    seen: set[str] = set()
    for n, line in enumerate(lines[start + 1:end], start=start + 2):
        if not line.strip() or line.startswith("#"):
            continue
        m = _KEY_LINE.fullmatch(line.rstrip())
        if not m:
            return f"unparseable front matter (line {n}: only 'key: value' lines at column 0 are accepted)"
        key = m.group(1)
        if key not in COPILOT_KEYS:
            return (f"front matter key {key!r} is configuration (Copilot reads tools, model, mcp-servers ... from it); "
                    f"only {', '.join(sorted(COPILOT_KEYS))} are accepted")
        if key in seen:
            return f"front matter key {key!r} appears twice"
        seen.add(key)
        problem = _scalar_problem(key, (m.group(2) or "").strip())
        if problem:
            return problem
    return ""


def _team_file(a: dict[str, Any], d: Driver) -> Path | None:
    team = str(a.get("team_dir") or "").strip()
    return Path(team).expanduser() / selected(a, d) if team else None


def problem(a: dict[str, Any], d: Driver) -> str:
    """Return why the selected agent file may not run, or ``""`` (copilot only: its front matter is checked).

    Read on the host from ``agent.team_dir`` (the read-only team mount a run passes the CLI): absent,
    unreadable or not UTF-8 is refused too (fail closed).
    """
    if d.name not in CONTENT_BOUND:
        return ""
    path = _team_file(a, d)
    try:
        text = path.read_text(encoding="utf-8") if path else ""
    except (OSError, UnicodeDecodeError) as exc:
        return f"the copilot agent file {selected(a, d)} cannot be read ({type(exc).__name__})"
    if path is None:
        return "agent.team_dir is not set (the copilot agent file is read from it)"
    why = front_matter_problem(text)
    return f"the copilot agent file {selected(a, d)} is refused: {why}" if why else ""


def binding(a: dict[str, Any], d: Driver) -> str | dict[str, str]:
    """Return what ``flags_sha256`` binds of the selected agent file (see the module table).

    The relative path for every framework (unchanged from the earlier ``default_recipe`` value when the default
    is selected, so a goose, claude or codex fingerprint does not move); copilot adds the file's sha256
    (``"absent"`` when it cannot be read).
    """
    rel = selected(a, d)
    if d.name not in CONTENT_BOUND:
        return rel
    path = _team_file(a, d)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path else "absent"
    except OSError:
        digest = "absent"
    return {"path": rel, "sha256": digest}
