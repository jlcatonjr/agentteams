"""
_makefile_screen.py — Screen a Makefile's targets for generated ``.vscode/tasks.json``.

:mod:`agentteams.vscode_tasks` offers make targets as one-click tasks. A target name says little
about what ``make <target>`` runs, so this module reads what it does run: the target's recipe, the
variables and ``define`` blocks that recipe expands, and every target it runs in turn (as a
prerequisite, through a pattern rule, or through a recursive ``$(MAKE)``). Text make runs while it
*reads* the file (``$(shell …)``, ``!=``) runs on every target, so a hazard there marks them all.

The hazard predicates are passed in by the caller, so the npm-script, shell-script and make
screens share one definition and this module imports nothing from ``vscode_tasks``.

Known residuals (also listed in the CHANGELOG): an ``include``d makefile is not read, a computed
target name (``$(MAKE) $(T)``) is not followed, and a recipe that reaches a hazard only through a
program it calls (``python purge.py``) is out of reach of any text screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

#: Read at most this much; a larger Makefile cannot be fully screened, so none of it is offered.
MAKEFILE_MAX_BYTES = 262144

_SPECIAL_TARGET_RE = re.compile(r"^\.[A-Z_]+$")  # .PHONY, .SUFFIXES, .DEFAULT, …
_CONDITIONAL_RE = re.compile(r"^\s*(?:ifeq|ifneq|ifdef|ifndef|else|endif)\b")
_DEFINE_RE = re.compile(r"^\s*(?:(?:export|override|private)\s+)*define\s+([^\s:=+?!]+)")
_ENDEF_RE = re.compile(r"^\s*endef\b")
_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:export|override|private)\s+)*([A-Za-z0-9_.-]+)\s*(:::=|::=|:=|!=|\?=|\+=|=)(.*)$")
_RECIPEPREFIX_RE = re.compile(r"^\s*\.RECIPEPREFIX\s*:{0,3}=\s*(\S)")
#: ``targets: prerequisites [; inline recipe]``, comments already stripped. Greedy, colon-anchored
#: groups keep matching linear on long lines.
_HEADER_RE = re.compile(r"^([^\s:=][^:=]*)(::?)(?![:=])([^=;]*)(?:;(.*))?$")
_SIMPLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_VAR_REF_RE = re.compile(r"\$[({](?:call\s+|value\s+)?([A-Za-z0-9_.-]+)")
#: A recursive make at the start of a command (``$(MAKE) purge``, ``@make -C sub clean``).
_SUBMAKE_RE = re.compile(
    r"(?:^|[;&|(])[ \t]*[@+-]*[ \t]*(?:\$[({]MAKE[)}]|make)(?![\w.-])([^;&|\n)]*)", re.MULTILINE)
_SUBMAKE_VALUE_OPTS = frozenset({"-C", "-f", "-I", "-o", "-W", "--directory", "--file", "--makefile"})

#: make's ``$(RM)`` is ``rm -f``, so ``$(RM) -r`` is a recursive force delete.
MAKE_RM_RE = re.compile(r"\$[({]RM[)}]\s+(?:-\w+\s+)*-\w*[rR]")
#: Recipe signals of an install-like target: package installs, privilege, the install(1) tool,
#: a piped installer.
INSTALL_CONTENT_RE = re.compile(
    r"\b(?:pip3?|pipx|npm|yarn|pnpm|brew|apt|apt-get|dnf|yum|gem|cargo|go|poetry|conda)\s+"
    r"(?:install|add|i)\b|\buv\s+(?:pip\s+install|add|tool\s+install)\b|\bsudo\b|\bdoas\b"
    r"|\$[({](?:INSTALL\w*|SUDO)[)}]|(?:^|[;&|(])[ \t]*[@+-]*[ \t]*install[ \t]"
    r"|\bsetup\.py\s+(?:install|develop)\b|\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class MakefileRules:
    """A Makefile reduced to what screening needs."""

    #: Target -> recipe text plus the values of every variable it expands.
    recipes: dict[str, str] = field(default_factory=dict)
    #: Target -> the targets it runs (prerequisites, pattern rules, recursive makes).
    edges: dict[str, set[str]] = field(default_factory=dict)
    #: ``name:`` headers with one plain name and no prerequisites, in file order.
    simple_targets: list[str] = field(default_factory=list)
    #: Text make runs while reading the file (``$(shell …)`` lines and ``!=`` assignments).
    parse_time: str = ""


def _expand(text: str, variables: dict[str, str]) -> str:
    """Append the values of every variable ``text`` references, transitively."""
    out, seen, todo = [text], set(), [text]
    while todo:
        for name in _VAR_REF_RE.findall(todo.pop()):
            if name in variables and name not in seen:
                seen.add(name)
                out.append(variables[name])
                todo.append(variables[name])
    return "\n".join(out)


def parse_makefile(text: str) -> MakefileRules:
    """Parse a Makefile into recipes, run-edges, plain target names and parse-time text.

    Args:
        text: The Makefile's contents.

    Returns:
        The parsed :class:`MakefileRules`. Conditional and comment lines do not end a recipe;
        special targets (``.PHONY`` …) are not rules.
    """
    text = re.sub(r"\\\r?\n", " ", text)  # join backslash continuations
    prefix = "\t"
    raw: dict[str, list[str]] = {}
    rules = MakefileRules()
    variables: dict[str, str] = {}
    parse_time: list[str] = []
    current: list[str] = []
    define: str | None = None
    for line in text.splitlines():
        if define is not None:
            if _ENDEF_RE.match(line):
                define = None
            else:
                variables[define] = variables.get(define, "") + "\n" + line
            continue
        if line.startswith(prefix):
            for target in current:
                raw[target].append(line)
            continue
        line = line.split("#", 1)[0]
        if not line.strip() or _CONDITIONAL_RE.match(line):
            continue
        if "$(shell" in line or "${shell" in line:
            parse_time.append(line)
        if m := _RECIPEPREFIX_RE.match(line):
            prefix, current = m.group(1), []
            continue
        if m := _DEFINE_RE.match(line):
            define, current = m.group(1), []
            continue
        if m := _ASSIGN_RE.match(line):
            name, op, value = m.groups()
            variables[name] = (variables.get(name, "") + " " + value) if op == "+=" else value
            if op == "!=":
                parse_time.append(value)
            current = []
            continue
        m = _HEADER_RE.match(line)
        current = [t for t in m.group(1).split() if not _SPECIAL_TARGET_RE.match(t)] if m else []
        for target in current:
            raw.setdefault(target, [])
            rules.edges.setdefault(target, set()).update(m.group(3).split())
            if m.group(4):
                raw[target].append(m.group(4))
        if m and m.group(2) == ":" and not m.group(3).strip() and len(current) == 1 \
                and _SIMPLE_NAME_RE.match(current[0]) and current[0] not in rules.simple_targets:
            rules.simple_targets.append(current[0])
    rules.recipes = {t: _expand("\n".join(lines), variables) for t, lines in raw.items()}
    rules.parse_time = _expand("\n".join(parse_time), variables)
    for target, recipe in rules.recipes.items():
        for sm in _SUBMAKE_RE.finditer(recipe):
            args = sm.group(1).split()
            for i, arg in enumerate(args):
                if arg[:1] not in "-$" and "=" not in arg and (i == 0 or args[i - 1] not in _SUBMAKE_VALUE_OPTS):
                    rules.edges[target].add(arg)
    _link_pattern_rules(rules)
    return rules


def _link_pattern_rules(rules: MakefileRules) -> None:
    """Make a prerequisite with no rule of its own run every pattern rule (``%.x:``) it matches."""
    patterns = {t: re.compile("^" + re.escape(t).replace("%", ".+") + "$") for t in rules.recipes if "%" in t}
    for runs in list(rules.edges.values()):
        for prereq in list(runs):
            if prereq not in rules.recipes:
                rules.edges.setdefault(prereq, set()).update(t for t, rx in patterns.items() if rx.match(prereq))


def closure(seed: set[str], edges: dict[str, set[str]]) -> set[str]:
    """Return ``seed`` plus every target that runs, directly or transitively, a target in it.

    Args:
        seed: Targets already marked.
        edges: Target -> the targets it runs.

    Returns:
        The marked set.
    """
    marked = set(seed)
    changed = True
    while changed:
        changed = False
        for target, runs in edges.items():
            if target not in marked and runs & marked:
                marked.add(target)
                changed = True
    return marked


def screen_makefile(
    text: str,
    hazard_text: Callable[[str], bool],
    hazard_name: Callable[[str], bool],
    install_name: Callable[[str], bool],
) -> tuple[MakefileRules, set[str], set[str]]:
    """Parse a Makefile and mark its hazardous and install-like targets.

    Args:
        text: The Makefile's contents.
        hazard_text: True for text that schedules jobs or purges/deletes data.
        hazard_name: True for a target name that says it does.
        install_name: True for a target name that says it installs, deploys or publishes.

    Returns:
        ``(rules, hazardous, install_like)``. A target is hazardous when it, or any target it runs,
        has a hazardous name or recipe, or when the file's parse-time text is hazardous (then
        every target is). It is install-like when it, or any target it runs, installs by name or
        recipe.
    """
    rules = parse_makefile(text)
    names = set(rules.recipes) | {t for runs in rules.edges.values() for t in runs}
    if hazard_text(rules.parse_time) or MAKE_RM_RE.search(rules.parse_time):
        return rules, names, set()
    hazardous = closure(
        {t for t, r in rules.recipes.items() if hazard_text(r) or MAKE_RM_RE.search(r)}
        | {t for t in names if hazard_name(t)}, rules.edges)
    install_like = closure(
        {t for t, r in rules.recipes.items() if INSTALL_CONTENT_RE.search(r)}
        | {t for t in names if install_name(t)}, rules.edges)
    return rules, hazardous, install_like
