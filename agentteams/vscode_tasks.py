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


# ---------------------------------------------------------------------------
# Group classifier
# ---------------------------------------------------------------------------

_TEST_KEYWORDS = ("test", "spec", "check", "lint", "verify", "qa")
_BUILD_KEYWORDS = ("build", "compile", "bundle", "dist", "package", "install")


def _classify_group(name: str) -> str:
    n = name.lower()
    if any(k in n for k in _TEST_KEYWORDS):
        return "test"
    if any(k in n for k in _BUILD_KEYWORDS):
        return "build"
    return "none"


def _assign_group_defaults(commands: list[ProjectCommand]) -> None:
    """Mark the first test-group and first build-group command as group defaults."""
    saw_test = False
    saw_build = False
    for cmd in commands:
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
    for name in (data.get("scripts") or {}):
        if not isinstance(name, str) or not _safe_name(name):
            continue
        commands.append(ProjectCommand(
            label=f"npm: {name}",
            command=f"npm run {name}",
            group=_classify_group(name),
        ))
    return commands


_PHONY_RE = re.compile(r"^\.PHONY\s*:\s*(.+)$", re.MULTILINE)


def _from_makefile(project_path: Path) -> list[ProjectCommand]:
    p = project_path / "Makefile"
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    commands: list[ProjectCommand] = []
    for m in _PHONY_RE.finditer(text):
        for name in m.group(1).split():
            name = name.strip()
            if not name or not _safe_name(name):
                continue
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


def _from_scripts_dir(project_path: Path) -> list[ProjectCommand]:
    scripts_dir = project_path / "scripts"
    if not scripts_dir.is_dir():
        return []
    commands: list[ProjectCommand] = []
    for script in sorted(scripts_dir.glob("*.sh")):
        stem = script.stem
        if not _safe_name(stem):
            continue
        commands.append(ProjectCommand(
            label=f"script: {stem}",
            command=f"bash scripts/{script.name}",
            group=_classify_group(stem),
        ))
    return commands


_GOOSE_TITLE_RE = re.compile(r"^title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_GOOSE_SKIP_STEMS = frozenset({"SETUP-REQUIRED", "AGENTS"})
_GOOSE_PRESENTATION = {"reveal": "always", "panel": "dedicated", "focus": True}


def _from_goose_recipes(project_path: Path) -> list[ProjectCommand]:
    """Discover Goose recipe YAML files and generate launch tasks.

    Each recipe becomes a `Goose: <Title>` task using ``goose run --recipe``.
    Recipes use ``panel: dedicated`` so Goose's interactive session gets its
    own terminal rather than sharing the shared output panel.
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
        commands.append(ProjectCommand(
            label=f"Goose: {label}",
            command=f"goose run --recipe .goose/recipes/{recipe.name}",
            group="none",
            presentation=_GOOSE_PRESENTATION,
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
    """Discover runnable commands from project tooling files."""
    commands: list[ProjectCommand] = []
    for discoverer in _DISCOVERERS:
        commands.extend(discoverer(project_path))
    _assign_group_defaults(commands)
    return commands


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


def _merge_jsonc(existing: dict[str, Any], raw: str, new_content: str) -> str:
    """Append new AGENTTEAMS tasks to a JSONC file, preserving original structure.

    Rather than re-serialising (which would destroy comments, ``inputs`` blocks,
    and hand-crafted formatting), this function splices the new task JSON into the
    raw file string immediately before the closing ``]`` of the tasks array.

    On the second run the file is parsed again (with comments stripped), the
    sentinel labels are found, and ``tasks_to_add`` is empty — so the raw string
    is returned unchanged (idempotent).
    """
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

    # Splice before the last `]` in the file.  For any well-structured tasks.json
    # the last `]` is the one that closes the tasks array (it comes after the final
    # task object and before the outer `}`).
    close_pos = raw.rfind("]")
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
