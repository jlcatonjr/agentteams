"""
vscode_tasks.py — Discovery, rendering, and sentinel-merge for .vscode/tasks.json.

Discovers runnable commands from project tooling files (package.json, Makefile,
pyproject.toml, tox.ini, Taskfile.yml, scripts/*.sh) and emits a tasks.json
file tagged with "detail": "AGENTTEAMS" so it can be safely merged on re-runs
while preserving user-authored tasks.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Safety allowlist for task names
# ---------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:\- ]+$")


def _safe_name(name: str) -> bool:
    """Return True when a task name is safe to include in JSON output.

    Rejects names with shell-expansion characters, path separators, or other
    metacharacters that could produce harmful or malformed task entries.
    """
    return bool(name and _SAFE_NAME_RE.fullmatch(name))


# ---------------------------------------------------------------------------
# ProjectCommand dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProjectCommand:
    label: str
    command: str
    group: str = "none"      # "test" | "build" | "none"
    is_default: bool = False
    presentation: "dict[str, Any] | None" = None  # None → default (panel: shared)
    # False for an arbitrary project script (scripts/*.sh): never the default build/test task.
    default_eligible: bool = True
    # False for a launcher whose label is not a project command name (Goose recipes).
    hazard_screened: bool = True


# ---------------------------------------------------------------------------
# Group classifier
# ---------------------------------------------------------------------------

_TEST_KEYWORDS = ("test", "spec", "check", "lint", "verify", "qa")
_BUILD_KEYWORDS = ("build", "compile", "bundle", "dist", "package", "install")
#: Side-effecting names that may be a build/test-group task but never the one-keystroke default.
_NO_DEFAULT_KEYWORDS = ("install", "deploy", "publish", "release", "upload")


def _classify_group(name: str) -> str:
    n = name.lower()
    if any(k in n for k in _TEST_KEYWORDS):
        return "test"
    if any(k in n for k in _BUILD_KEYWORDS):
        return "build"
    return "none"


def _assign_group_defaults(commands: list[ProjectCommand]) -> None:
    """Mark the first test-group and first build-group command as group defaults.

    Only commands from declared project tooling (package.json, Makefile, pyproject, tox,
    Taskfile) are eligible, and none whose name installs, deploys or publishes. An arbitrary
    ``scripts/*.sh`` is never made the default: the default build task runs on a single
    keystroke, and the fleet once bound a cron installer to it.
    """
    saw_test = False
    saw_build = False
    for cmd in commands:
        if not cmd.default_eligible or any(k in cmd.label.lower() for k in _NO_DEFAULT_KEYWORDS):
            continue
        if cmd.group == "test" and not saw_test:
            cmd.is_default = True
            saw_test = True
        elif cmd.group == "build" and not saw_build:
            cmd.is_default = True
            saw_build = True


# ---------------------------------------------------------------------------
# Per-source discoverers
# ---------------------------------------------------------------------------

def _from_package_json(project_path: Path) -> list[ProjectCommand]:
    p = project_path / "package.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    commands: list[ProjectCommand] = []
    for name, body in (data.get("scripts") or {}).items():
        if not isinstance(name, str) or not _safe_name(name):
            continue
        if isinstance(body, str) and _HAZARD_CONTENT_RE.search(body):
            _skip_notice(f"npm script '{name}'")
            continue
        commands.append(ProjectCommand(
            label=f"npm: {name}",
            command=f"npm run {name}",
            group=_classify_group(name),
        ))
    return commands


_PHONY_RE = re.compile(r"^\.PHONY\s*:\s*(.+)$", re.MULTILINE)
_RULE_HEADER_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_-]*):\s*(?:#.*)?$", re.MULTILINE
)


def _from_makefile(project_path: Path) -> list[ProjectCommand]:
    p = project_path / "Makefile"
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    seen: set[str] = set()
    commands: list[ProjectCommand] = []

    # Pass 1 — explicit .PHONY declarations (existing behaviour).
    for m in _PHONY_RE.finditer(text):
        for name in m.group(1).split():
            name = name.strip()
            if not name or not _safe_name(name) or name in seen:
                continue
            seen.add(name)
            commands.append(ProjectCommand(
                label=f"make: {name}",
                command=f"make {name}",
                group=_classify_group(name),
            ))

    # Pass 2 — rule headers not already covered by .PHONY.
    for m in _RULE_HEADER_RE.finditer(text):
        name = m.group(1)
        # _safe_name is defensive-only here: every name captured by
        # _RULE_HEADER_RE is of the form [A-Za-z][A-Za-z0-9_-]*, which is a
        # strict subset of _safe_name's allowed set.  The check always passes
        # but is kept to guard against future regex loosening.
        if name in seen or not _safe_name(name):
            continue
        seen.add(name)
        commands.append(ProjectCommand(
            label=f"make: {name}",
            command=f"make {name}",
            group=_classify_group(name),
        ))

    return commands


_TASK_SECTION_RE = re.compile(
    r"^\[tool\.(?:taskipy|poethepoet|poe)\.tasks\]", re.MULTILINE
)
_NEXT_SECTION_RE = re.compile(r"^\[", re.MULTILINE)


def _from_pyproject_toml(project_path: Path) -> list[ProjectCommand]:
    p = project_path / "pyproject.toml"
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    commands: list[ProjectCommand] = []
    for m in _TASK_SECTION_RE.finditer(text):
        section_start = m.end()
        next_section = _NEXT_SECTION_RE.search(text, section_start)
        block = text[section_start: next_section.start() if next_section else None]
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].strip()
            if not name or not _safe_name(name):
                continue
            commands.append(ProjectCommand(
                label=f"task: {name}",
                command=f"task {name}",
                group=_classify_group(name),
            ))
    return commands


_TESTENV_RE = re.compile(r"^\[testenv:([^\]]+)\]", re.MULTILINE)


def _from_tox_ini(project_path: Path) -> list[ProjectCommand]:
    p = project_path / "tox.ini"
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [
        ProjectCommand(
            label=f"tox: {m.group(1)}",
            command=f"tox -e {m.group(1)}",
            group="test",
        )
        for m in _TESTENV_RE.finditer(text)
        if _safe_name(m.group(1))
    ]


_TASKFILE_TASKS_RE = re.compile(r"^tasks:\s*$", re.MULTILINE)
_TASKFILE_RESERVED = frozenset({
    "tasks", "vars", "env", "includes", "version",
    "silent", "run", "output", "method", "cmds",
    "deps", "desc", "dir", "ignore_error", "internal",
    "label", "preconditions", "prompt", "requires",
    "sources", "generates", "status", "set", "shopt",
})


def _from_taskfile(project_path: Path) -> list[ProjectCommand]:
    for name in ("Taskfile.yml", "Taskfile.yaml"):
        p = project_path / name
        if p.is_file():
            break
    else:
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    tasks_match = _TASKFILE_TASKS_RE.search(text)
    if not tasks_match:
        return []

    after_tasks = text[tasks_match.end():]
    # Detect indent level from first indented line under `tasks:`.
    first_indent = re.search(r"^( +)\w", after_tasks, re.MULTILINE)
    if not first_indent:
        return []
    indent = first_indent.group(1)
    # Collect only lines at exactly the task-key indent level.
    end_block = re.search(r"^\S", after_tasks, re.MULTILINE)
    block = after_tasks[:end_block.start()] if end_block else after_tasks
    key_re = re.compile(rf"^{re.escape(indent)}(\w[\w\-]+):\s*$", re.MULTILINE)

    return [
        ProjectCommand(
            label=f"task: {m.group(1)}",
            command=f"task {m.group(1)}",
            group=_classify_group(m.group(1)),
        )
        for m in key_re.finditer(block)
        if m.group(1) not in _TASKFILE_RESERVED and _safe_name(m.group(1))
    ]


#: Name tokens marking a command that schedules jobs or purges/deletes data. Such commands are not
#: offered as tasks at all: a one-click task is the wrong surface for them (fleet round 3, 2026-10-11).
_HAZARD_TOKENS = frozenset({
    "cron", "crontab", "launchd", "launchctl", "systemd", "systemctl",
    "purge", "wipe", "destroy", "nuke", "delete", "erase", "truncate",
})
_HAZARD_TOKEN_PREFIXES = ("cron", "purge", "launchd", "launchctl", "systemd", "systemctl")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
#: Script-body signals of the same: installing a scheduled job, or deleting stored data.
_HAZARD_CONTENT_RE = re.compile(
    r"\bcrontab\b|\blaunchctl\b|LaunchAgents|LaunchDaemons|/etc/cron|\bsystemctl\b[^\n]*\b(enable|start)\b"
    r"|\.timer\b|\bDROP\s+(TABLE|DATABASE|SCHEMA)\b|\bDELETE\s+FROM\b|\bTRUNCATE\s+TABLE\b|\bshred\b"
    r"|\brm\s+(-\w+\s+)*-\w*(r\w*f|f\w*r)|\brm\s+-\w*[rR]\w*\s+-\w*f|\bfind\b[^\n]*\s-delete\b|\bgit\s+clean\s+-\w*f",
    re.IGNORECASE,
)
_HAZARD_SCAN_BYTES = 65536


def _skip_notice(what: str) -> None:
    print(f"  ℹ  .vscode/tasks.json: {what} not offered as a task (it schedules jobs or "
          "purges/deletes data); run it by hand.", file=sys.stderr)


def hazardous_command_name(name: str) -> bool:
    """Return True when a command name says it schedules jobs or purges/deletes data.

    Args:
        name: A script stem, npm script, make target or task name.

    Returns:
        True for names such as ``install_tracers_purge_cron`` or ``purge-store``.
    """
    for token in _TOKEN_SPLIT_RE.split(name.lower()):
        if token in _HAZARD_TOKENS or token.startswith(_HAZARD_TOKEN_PREFIXES):
            return True
    return False


def _hazardous_script(script: Path) -> bool:
    """True when a script's name or (first 64 KiB of) content schedules jobs or purges data."""
    if hazardous_command_name(script.stem):
        return True
    try:
        with script.open("rb") as fh:
            head = fh.read(_HAZARD_SCAN_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return True  # unreadable: cannot clear it, so do not offer it
    return bool(_HAZARD_CONTENT_RE.search(head))


def _from_scripts_dir(project_path: Path) -> list[ProjectCommand]:
    scripts_dir = project_path / "scripts"
    if not scripts_dir.is_dir():
        return []
    commands: list[ProjectCommand] = []
    for script in sorted(scripts_dir.glob("*.sh")):
        stem = script.stem
        if not _safe_name(stem):
            continue
        if _hazardous_script(script):
            _skip_notice(f"scripts/{script.name}")
            continue
        commands.append(ProjectCommand(
            label=f"script: {stem}",
            command=f"bash scripts/{script.name}",
            group=_classify_group(stem),
            default_eligible=False,
        ))
    return commands


_GOOSE_TITLE_RE = re.compile(r"^title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_GOOSE_SKIP_STEMS = frozenset({"SETUP-REQUIRED", "AGENTS"})
_GOOSE_PRESENTATION = {"reveal": "always", "panel": "dedicated", "focus": True}


def _from_goose_recipes(project_path: Path) -> list[ProjectCommand]:
    """Discover Goose recipe YAML files and generate launch tasks.

    Each recipe becomes a ``Goose: <Title>`` task. The command sources
    ``~/.config/goose/goose-backend.sh`` before invoking ``goose-or``, which
    scopes OPENROUTER_API_KEY to a single goose run without leaving the key in
    the parent shell. Recipes use ``panel: dedicated`` so Goose's interactive
    session gets its own terminal.
    """
    recipes_dir = project_path / ".goose" / "recipes"
    if not recipes_dir.is_dir():
        return []
    commands: list[ProjectCommand] = []
    for recipe in sorted(recipes_dir.glob("*.yaml")):
        stem = recipe.stem
        if stem in _GOOSE_SKIP_STEMS or not _safe_name(stem):
            continue
        label = _goose_label(recipe, stem)
        recipe_rel = f".goose/recipes/{recipe.name}"
        command = (
            f"zsh -c '. ~/.config/goose/goose-backend.sh"
            f" && goose-or run --recipe {recipe_rel}'"
        )
        commands.append(ProjectCommand(
            label=f"Goose: {label}",
            command=command,
            group="none",
            presentation=_GOOSE_PRESENTATION,
            hazard_screened=False,  # an agent launcher, labelled by its recipe title
        ))
    return commands


_SLUG_SPLIT_RE = re.compile(r"[-_ ]+")


def _goose_label(recipe: Path, stem: str) -> str:
    """Extract a human-readable label from a recipe file's ``title:`` field.

    Strips the ``— ProjectName`` suffix that agentteams appends, then normalizes
    by title-casing each dash/underscore/space-separated word so that slugs like
    ``team-builder`` become ``Team Builder``.  Falls back to title-casing the
    file stem when the field is absent or unreadable.
    """
    def _title_case(s: str) -> str:
        return " ".join(w.capitalize() for w in _SLUG_SPLIT_RE.split(s) if w)

    fallback = _title_case(stem)
    try:
        header = recipe.read_text(encoding="utf-8")[:500]
    except (OSError, UnicodeDecodeError):
        return fallback
    m = _GOOSE_TITLE_RE.search(header)
    if not m:
        return fallback
    title = m.group(1).strip()
    if " — " in title:   # em-dash separator: "Orchestrator — Project"
        title = title.split(" — ")[0].strip()
    elif " - " in title:
        title = title.split(" - ")[0].strip()
    return _title_case(title) if title else fallback


# ---------------------------------------------------------------------------
# agentteams meta-tasks (always included)
# ---------------------------------------------------------------------------

AGENTTEAMS_META_TASKS: list[dict[str, Any]] = [
    {
        "label": "agentteams: update agents",
        "type": "shell",
        "command": "agentteams --update --merge",
        "group": "none",
        "presentation": {"reveal": "always", "panel": "shared"},
        "problemMatcher": [],
        "detail": "AGENTTEAMS",
    },
    {
        "label": "agentteams: dry-run check",
        "type": "shell",
        "command": "agentteams --update --dry-run",
        "group": "none",
        "presentation": {"reveal": "always", "panel": "shared"},
        "problemMatcher": [],
        "detail": "AGENTTEAMS",
    },
    {
        "label": "agentteams: fleet update (all)",
        "type": "shell",
        "command": "agentteams --fleet . --fleet-frameworks all --update --merge",
        "group": "none",
        "presentation": {"reveal": "always", "panel": "shared"},
        "problemMatcher": [],
        "detail": "AGENTTEAMS",
    },
]


# ---------------------------------------------------------------------------
# Top-level discovery
# ---------------------------------------------------------------------------

_DISCOVERERS = (
    _from_package_json,
    _from_makefile,
    _from_pyproject_toml,
    _from_tox_ini,
    _from_taskfile,
    _from_scripts_dir,
    _from_goose_recipes,
)


def discover_project_commands(project_path: Path) -> list[ProjectCommand]:
    """Discover runnable commands from project tooling files.

    Commands whose name marks them as scheduling jobs or purging/deleting data
    (:func:`hazardous_command_name`) are dropped, and ``scripts/*.sh`` are additionally screened
    by content; neither kind is ever emitted as a task.

    Args:
        project_path: The project root.

    Returns:
        The discovered commands, with group defaults assigned.
    """
    commands: list[ProjectCommand] = []
    for discoverer in _DISCOVERERS:
        commands.extend(discoverer(project_path))
    kept: list[ProjectCommand] = []
    for cmd in commands:
        if not cmd.hazard_screened or not hazardous_command_name(cmd.label.split(": ", 1)[-1]):
            kept.append(cmd)
        else:
            _skip_notice(f"'{cmd.label}'")
    _assign_group_defaults(kept)
    return kept


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_tasks_json(commands: list[ProjectCommand]) -> str:
    """Render a fresh tasks.json string from discovered commands + meta-tasks."""
    tasks: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for cmd in commands:
        if cmd.label in seen_labels:
            continue
        seen_labels.add(cmd.label)
        if cmd.group in ("test", "build"):
            group: Any = {"kind": cmd.group, "isDefault": cmd.is_default}
        else:
            group = "none"
        pres = cmd.presentation if cmd.presentation is not None else {"reveal": "always", "panel": "shared"}
        tasks.append({
            "label": cmd.label,
            "type": "shell",
            "command": cmd.command,
            "group": group,
            "presentation": pres,
            "problemMatcher": [],
            "detail": "AGENTTEAMS",
        })

    for meta in AGENTTEAMS_META_TASKS:
        if meta["label"] not in seen_labels:
            tasks.append(meta)

    return json.dumps({"version": "2.0.0", "tasks": tasks}, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Sentinel merge
# ---------------------------------------------------------------------------

def _try_parse_json(text: str) -> "dict[str, Any] | None":
    """Return the parsed JSON object, or None if parsing fails."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _strip_jsonc_comments(text: str) -> str:
    """Strip // line comments from JSONC text for parsing purposes.

    This is a simple heuristic — it strips every ``//`` to end-of-line, including
    those that might appear inside string values.  For sentinel detection this is
    acceptable: the only consequence of a false strip is that a task label or
    command is slightly mangled during parse, which will not affect whether we
    find ``"detail": "AGENTTEAMS"`` on a task object.
    """
    return re.sub(r"//[^\n]*", "", text)


def _merge_standard_json(existing: dict[str, Any], new_content: str) -> str:
    """Sentinel merge for well-formed JSON files."""
    user_tasks = [
        t for t in (existing.get("tasks") or [])
        if isinstance(t, dict) and t.get("detail") != "AGENTTEAMS"
    ]
    new_tasks: list[dict[str, Any]] = json.loads(new_content).get("tasks", [])
    new_labels = {t["label"] for t in new_tasks}
    user_tasks = [t for t in user_tasks if t.get("label") not in new_labels]
    merged = {"version": "2.0.0", "tasks": user_tasks + new_tasks}
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


def _find_tasks_close_bracket(raw: str) -> int:
    """Find the index of the ] that closes the top-level 'tasks' array (-1 when not found)."""
    return _find_tasks_array(raw)[1]


def _find_tasks_array(raw: str) -> tuple[int, int]:
    """Find the ``[`` and ``]`` indices of the top-level 'tasks' array (``(-1, -1)`` if absent).

    Performs a forward scan of raw JSONC text, properly tracking string
    boundaries (including \\ escape sequences) and // / /* */ comment regions,
    to find the ] that matches the opening [ of the "tasks" value at depth 1
    of the outer object.  Returns -1 if the tasks array cannot be located.

    This replaces raw.rfind(']'), which fails when:
      - an "inputs" or other array follows the tasks array (last ] is wrong)
      - a JSONC comment after the tasks ] contains ] (last ] is inside comment)
    """
    n = len(raw)

    def _advance_string(pos: int) -> int:
        """Return index just past the closing \" of the string starting at pos."""
        pos += 1  # skip opening "
        while pos < n:
            c = raw[pos]
            if c == "\\":
                pos += 2  # skip escaped character
            elif c == '"':
                return pos + 1
            else:
                pos += 1
        return pos

    def _advance_line_comment(pos: int) -> int:
        """Return index of the character after the end of a // comment."""
        while pos < n and raw[pos] != "\n":
            pos += 1
        return pos

    def _advance_block_comment(pos: int) -> int:
        """Return index just past the closing */ of a block comment."""
        pos += 2  # skip /*
        while pos < n - 1:
            if raw[pos] == "*" and raw[pos + 1] == "/":
                return pos + 2
            pos += 1
        return n  # unterminated block comment — consumed to EOF

    brace_depth = 0
    i = 0

    while i < n:
        c = raw[i]

        # Skip // and /* */ comments — they are not JSON structure.
        if c == "/" and i + 1 < n:
            if raw[i + 1] == "/":
                i = _advance_line_comment(i + 2)
                continue
            if raw[i + 1] == "*":
                i = _advance_block_comment(i)
                continue

        if c == '"':
            if brace_depth == 1:
                # At top level of the outer object — this might be the "tasks" key.
                key_end = _advance_string(i)
                if raw[i:key_end] == '"tasks"':
                    # Consume whitespace and ':'.
                    j = key_end
                    while j < n and raw[j] in " \t\r\n":
                        j += 1
                    if j < n and raw[j] == ":":
                        j += 1
                        while j < n and raw[j] in " \t\r\n":
                            j += 1
                        if j < n and raw[j] == "[":
                            # Track bracket depth to find the matching ].
                            open_pos = j
                            arr_depth = 0
                            while j < n:
                                ch = raw[j]
                                if ch == '"':
                                    j = _advance_string(j)
                                    continue
                                if ch == "/" and j + 1 < n:
                                    if raw[j + 1] == "/":
                                        j = _advance_line_comment(j + 2)
                                        continue
                                    if raw[j + 1] == "*":
                                        j = _advance_block_comment(j)
                                        continue
                                if ch == "[":
                                    arr_depth += 1
                                elif ch == "]":
                                    arr_depth -= 1
                                    if arr_depth == 0:
                                        return open_pos, j
                                j += 1
                            return -1, -1  # unmatched opening [
                i = key_end
                continue
            else:
                i = _advance_string(i)
                continue

        if c == "{":
            brace_depth += 1
        elif c == "}":
            brace_depth -= 1

        i += 1

    return -1, -1


def _skip_jsonc_token(raw: str, i: int) -> int | None:
    """Index just past the string / comment starting at ``raw[i]``, or None when none starts there."""
    if raw[i] == '"':
        i += 1
        while i < len(raw) and raw[i] != '"':
            i += 2 if raw[i] == "\\" else 1
        return i + 1
    if raw.startswith("//", i):
        end = raw.find("\n", i)
        return len(raw) if end == -1 else end
    if raw.startswith("/*", i):
        end = raw.find("*/", i + 2)
        return len(raw) if end == -1 else end + 2
    return None


def _task_object_spans(raw: str) -> list[tuple[int, int, int | None, int | None]]:
    """``(start, end, comma_before, comma_after)`` of each object element of the tasks array.

    ``end`` is the index of the closing ``}``; the commas are the depth-0 separators around it.
    """
    open_pos, close_pos = _find_tasks_array(raw)
    spans: list[tuple[int, int, int | None, int | None]] = []
    if open_pos == -1:
        return spans
    depth, start, last_comma, i = 0, -1, None, open_pos + 1
    while i < close_pos:
        skipped = _skip_jsonc_token(raw, i)
        if skipped is not None:
            i = skipped
            continue
        c = raw[i]
        if c in "{[":
            if depth == 0 and c == "{":
                start = i
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0 and c == "}" and start != -1:
                spans.append((start, i, last_comma, None))
                start = -1
        elif c == "," and depth == 0:
            if spans and spans[-1][3] is None:
                spans[-1] = spans[-1][:3] + (i,)
            last_comma = i
        i += 1
    return spans


def _refresh_jsonc_sentinels(raw: str, new_tasks: list[dict[str, Any]]) -> str:
    """Drop or rewrite AGENTTEAMS-tagged tasks in a JSONC file that the new render changed.

    A tagged task no longer rendered (e.g. a script now screened out as hazardous) is removed with
    one adjacent comma; one whose content changed (e.g. it lost ``isDefault``) is replaced in place.
    User tasks, comments and formatting elsewhere are untouched. A tagged task that does not parse
    after comment-stripping is left as it is.
    """
    by_label = {t["label"]: t for t in new_tasks}
    for _ in range(len(new_tasks) + 1000):  # one edit per pass; bounded
        for start, end, before, after in _task_object_spans(raw):
            obj = _try_parse_json(_strip_jsonc_comments(raw[start:end + 1]))
            if not isinstance(obj, dict) or obj.get("detail") != "AGENTTEAMS":
                continue
            new = by_label.get(obj.get("label"))
            if new is None:
                lo, hi = (before, end + 1) if before is not None else (
                    start, (after + 1) if after is not None else end + 1)
                raw = raw[:lo] + raw[hi:]
                break
            if obj != new:
                body = "\n".join("    " + l for l in json.dumps(new, indent=2, ensure_ascii=False).splitlines())
                raw = raw[:start] + body.lstrip() + raw[end + 1:]
                break
        else:
            return raw
    return raw


def _merge_jsonc(existing: dict[str, Any], raw: str, new_content: str) -> str:
    """Append new AGENTTEAMS tasks to a JSONC file, preserving original structure.

    Rather than re-serialising (which would destroy comments, ``inputs`` blocks,
    and hand-crafted formatting), this function locates the structural closing
    ``]`` of the tasks array via ``_find_tasks_close_bracket`` — a forward scan
    that tracks string boundaries and JSONC comments — and splices new task JSON
    immediately before it.

    On the second run the file is parsed again (with comments stripped), the
    sentinel labels are found, and ``tasks_to_add`` is empty — so the raw string
    is returned unchanged (idempotent).
    """
    new_tasks_all: list[dict[str, Any]] = json.loads(new_content).get("tasks", [])
    refreshed = _refresh_jsonc_sentinels(raw, new_tasks_all)
    if refreshed != raw:
        raw = refreshed
        existing = _try_parse_json(_strip_jsonc_comments(raw)) or existing
    existing_tasks = existing.get("tasks") or []
    existing_sentinel_labels = {
        t.get("label") for t in existing_tasks
        if isinstance(t, dict) and t.get("detail") == "AGENTTEAMS"
    }
    new_tasks: list[dict[str, Any]] = json.loads(new_content).get("tasks", [])
    tasks_to_add = [t for t in new_tasks if t["label"] not in existing_sentinel_labels]

    if not tasks_to_add:
        return raw  # Already up to date.

    # Format each new task with 4-space indent to match the VS Code JSONC convention
    # used in hand-crafted tasks.json files.
    chunks: list[str] = []
    for task in tasks_to_add:
        task_str = json.dumps(task, indent=2, ensure_ascii=False)
        indented = "\n".join("    " + line for line in task_str.splitlines())
        chunks.append(indented)

    insertion = ",\n\n" + ",\n\n".join(chunks)

    # Splice before the structural ] that closes the tasks array.
    # _find_tasks_close_bracket scans forward with full string and comment
    # awareness, so ] characters inside task values, nested arrays, or
    # JSONC comments after the tasks block cannot mislead it.
    close_pos = _find_tasks_close_bracket(raw)
    if close_pos == -1:
        return raw  # Structure not recognised — leave untouched.

    before = raw[:close_pos].rstrip()
    after = raw[close_pos:].lstrip()       # starts with `]`
    return before + "\n" + insertion + "\n\n  " + after


def sentinel_merge(existing_path: Path, new_content: str) -> str:
    """Merge new AGENTTEAMS tasks into an existing tasks.json, preserving user tasks.

    Handles both standard JSON and JSONC (VS Code's JSON-with-comments format).
    For JSONC files the tasks are appended to the raw file, keeping comments, the
    ``inputs`` block, and hand-crafted formatting intact.

    Raises ValueError with a descriptive message only if the file cannot be parsed
    even after comment-stripping — never silently overwrites hand-crafted content.
    """
    if not existing_path.is_file():
        return new_content
    try:
        raw = existing_path.read_text(encoding="utf-8")
    except OSError:
        return new_content

    # --- Standard JSON path ---
    existing = _try_parse_json(raw)
    if existing is not None:
        return _merge_standard_json(existing, new_content)

    # --- JSONC path (JSON with // or /* comments) ---
    if "//" in raw or "/*" in raw:
        stripped = _strip_jsonc_comments(raw)
        jsonc_parsed = _try_parse_json(stripped)
        if jsonc_parsed is not None:
            return _merge_jsonc(jsonc_parsed, raw, new_content)
        try:
            json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{existing_path} uses JSONC but could not be parsed even after "
                f"stripping comments: {exc}\n"
                f"  Fix the file manually or pass --no-vscode-tasks to suppress."
            ) from exc

    # --- Genuinely corrupt JSON (no comment markers) ---
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{existing_path} contains invalid JSON and cannot be safely merged: {exc}\n"
            f"  Fix the file manually or delete it and re-run agentteams to regenerate."
        ) from exc

    return new_content  # unreachable, satisfies type checkers
