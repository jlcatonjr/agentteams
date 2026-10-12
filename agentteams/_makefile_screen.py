"""
_makefile_screen.py — Screen a Makefile's targets for generated ``.vscode/tasks.json``.

:mod:`agentteams.vscode_tasks` offers make targets as one-click tasks. A target name says little
about what ``make <target>`` runs, so this module reads what it does run: the target's recipe, the
variables and ``define`` blocks that recipe expands, and every target it runs in turn (as a
prerequisite, through a pattern rule, or through a recursive ``$(MAKE)``). Text make runs while it
*reads* the file (``$(shell …)``, ``$(eval …)``, ``!=``) runs for every target, so a hazard there
marks them all.

The hazard predicates are passed in by the caller, so the npm-script, shell-script and make
screens share one definition and this module imports nothing from ``vscode_tasks``.

Known residuals (also listed in the CHANGELOG): an ``include``d makefile and a sub-directory's own
makefile (``$(MAKE) -C sub``) are not read, a computed target name (``$(MAKE) $(T)``) is not
followed, and a recipe that reaches a hazard only through a program it calls (``python purge.py``)
is out of reach of any text screen.
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
_RECIPEPREFIX_RE = re.compile(r"^\s*\.RECIPEPREFIX\s*:{0,3}=\s*(\S?)")
#: ``targets: prerequisites [; inline recipe]``, comments already stripped. Greedy, colon-anchored
#: groups keep matching linear on long lines.
_HEADER_RE = re.compile(r"^([^\s:=][^:=]*)(::?)(?![:=])([^=;]*)(?:;(.*))?$")
_SIMPLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_VAR_REF_RE = re.compile(r"\$[({](?:call\s+|value\s+)?([A-Za-z0-9_.-]+)|\$([A-Za-z0-9_])")
#: A variable name computed from another reference (``$(CMD_$(T))``, ``$(call $(F))``).
_COMPUTED_REF_RE = re.compile(r"\$[({](?:(?:call|value)\s+)?[A-Za-z0-9_.-]*\$")
#: The reference a computed name stands for: it may name any variable.
ANY_VARIABLE = "\0any"
#: Functions that run while make reads the file.
_PARSE_FN_RE = re.compile(r"\$[({](?:shell|eval)\b")
#: A recursive make at the start of a command (``$(MAKE) purge``, ``@make -C sub clean``). The
#: whitespace runs never overlap, so matching stays linear.
_SUBMAKE_RE = re.compile(
    r"(?:^|[;&|(])[ \t]*(?:[@+-]+[ \t]*)?(?:\$[({]MAKE[)}]|make)(?![\w.-])([^;&|\n)]*)", re.MULTILINE)
_SUBMAKE_VALUE_OPTS = frozenset({"-C", "-f", "-I", "-o", "-W", "--directory", "--file", "--makefile"})

#: make's ``$(RM)`` is ``rm -f``, so ``$(RM) -r`` is a recursive force delete.
MAKE_RM_RE = re.compile(r"\$[({]RM[)}]\s+(?:-\w+\s+)*-\w*[rR]")
#: Recipe signals of an install-like target: package installs, privilege, the install(1) tool,
#: a piped installer.
INSTALL_CONTENT_RE = re.compile(
    r"\b(?:pip3?|pipx|npm|yarn|pnpm|brew|apt|apt-get|dnf|yum|gem|cargo|go|poetry|conda)\s+"
    r"(?:install|add|i)\b|\buv\s+(?:pip\s+install|add|tool\s+install)\b|\bsudo\b|\bdoas\b"
    r"|\$[({](?:INSTALL\w*|SUDO)[)}]|(?:^|[;&|(])[ \t]*(?:[@+-]+[ \t]*)?install[ \t]"
    r"|\bsetup\.py\s+(?:install|develop)\b|\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class MakefileRules:
    """A Makefile reduced to what screening needs."""

    #: Target -> its recipe text (unexpanded).
    recipes: dict[str, str] = field(default_factory=dict)
    #: Target -> the targets it runs (prerequisites, pattern rules, recursive makes).
    edges: dict[str, set[str]] = field(default_factory=dict)
    #: Variable or ``define`` name -> every value ever assigned to it, joined (conditional branches,
    #: ``+=`` and target-specific values included), so no branch can hide a hazardous one.
    variables: dict[str, str] = field(default_factory=dict)
    #: ``name:`` headers with one plain name and no prerequisites, in file order.
    simple_targets: list[str] = field(default_factory=list)
    #: Text make may run while reading the file: every line outside a recipe or ``define`` body,
    #: and ``!=`` commands (wrapped as ``$(shell …)``). The screen keeps only text that reaches
    #: ``$(shell …)``/``$(eval …)``, directly or through variables.
    parse_time: list[str] = field(default_factory=list)


def _var_refs(text: str) -> set[str]:
    """Return the names of the variables ``text`` references (``$(X)``, ``${X}``, ``$X``).

    A computed name (``$(CMD_$(T))``) could be any variable, so it adds :data:`ANY_VARIABLE`.
    """
    refs = {a or b for a, b in _VAR_REF_RE.findall(text)}
    if _COMPUTED_REF_RE.search(text):
        refs.add(ANY_VARIABLE)
    return refs


def parse_makefile(text: str) -> MakefileRules:
    """Parse a Makefile into recipes, run-edges, variables, plain target names and parse-time text.

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
    current: list[str] = []
    define: str | None = None
    for line in text.splitlines():
        if define is not None:
            if _ENDEF_RE.match(line):
                define = None
            else:
                rules.variables[define] = rules.variables.get(define, "") + "\n" + line
            continue
        # A prefixed line outside any rule is ordinary makefile text, and make reads it as such.
        if line.startswith(prefix) and current:
            for target in current:
                raw[target].append(line)
            continue
        # `#` inside a function call is literal, so parse-time text and values keep the whole line.
        code = line.split("#", 1)[0]
        if not code.strip():
            continue
        assign = _ASSIGN_RE.match(line)
        rules.parse_time.append(line)
        if _CONDITIONAL_RE.match(code):
            continue
        if m := _RECIPEPREFIX_RE.match(code):
            prefix, current = m.group(1) or "\t", []
            continue
        if m := _DEFINE_RE.match(code):
            define, current = m.group(1), []
            continue
        if assign:
            _record_assignment(rules, *assign.groups())
            current = []
            continue
        m = _HEADER_RE.match(code)
        if not m:
            _record_target_variable(rules, line)
            current = []
            continue
        current = [t for t in m.group(1).split() if not _SPECIAL_TARGET_RE.match(t)]
        for target in current:
            raw.setdefault(target, [])
            rules.edges.setdefault(target, set()).update(m.group(3).split())
            if m.group(4):
                raw[target].append(m.group(4))
        if m.group(2) == ":" and not m.group(3).strip() and len(current) == 1 \
                and _SIMPLE_NAME_RE.match(current[0]) and current[0] not in rules.simple_targets:
            rules.simple_targets.append(current[0])
    rules.recipes = {t: "\n".join(lines) for t, lines in raw.items()}
    for target, recipe in rules.recipes.items():
        for sm in _SUBMAKE_RE.finditer(recipe):
            args = sm.group(1).split()
            for i, arg in enumerate(args):
                if arg[:1] not in "-$" and "=" not in arg and (i == 0 or args[i - 1] not in _SUBMAKE_VALUE_OPTS):
                    rules.edges[target].add(arg)
    _link_pattern_rules(rules)
    return rules


def _record_assignment(rules: MakefileRules, name: str, op: str, value: str) -> None:
    """Record one variable assignment (kept alongside earlier ones), and a ``!=`` command."""
    rules.variables[name] = rules.variables.get(name, "") + "\n" + value
    if op == "!=":
        rules.parse_time.append(f"$(shell {value})")


def _record_target_variable(rules: MakefileRules, line: str) -> None:
    """Record a target-specific value (``clean: CMD = rm -rf``) as an ordinary variable."""
    head, sep, rest = line.partition(":")
    if sep and rest[:1] not in (":", "="):
        if assign := _ASSIGN_RE.match(rest):
            _record_assignment(rules, *assign.groups())


def _link_pattern_rules(rules: MakefileRules) -> None:
    """Make a prerequisite with no rule of its own run every pattern rule (``%.x:``) it matches."""
    patterns = {t: re.compile("^" + re.escape(t).replace("%", ".+", 1) + "$") for t in rules.recipes if "%" in t}
    for runs in list(rules.edges.values()):
        for prereq in list(runs):
            if prereq not in rules.recipes:
                rules.edges.setdefault(prereq, set()).update(t for t, rx in patterns.items() if rx.match(prereq))


def closure(seed: set[str], edges: dict[str, set[str]]) -> set[str]:
    """Return ``seed`` plus every node that reaches, directly or transitively, a node in it.

    Args:
        seed: Nodes already marked.
        edges: Node -> the nodes it runs or references.

    Returns:
        The marked set.
    """
    parents: dict[str, list[str]] = {}
    for node, runs in edges.items():
        for child in runs:
            parents.setdefault(child, []).append(node)
    marked = set(seed)
    todo = list(seed)
    while todo:
        for parent in parents.get(todo.pop(), ()):
            if parent not in marked:
                marked.add(parent)
                todo.append(parent)
    return marked


def screen_makefile(
    text: str,
    hazard_text: Callable[[str], bool],
    hazard_name: Callable[[str], bool],
    install_name: Callable[[str], bool],
) -> tuple[MakefileRules, set[str], set[str]]:
    """Parse a Makefile and mark its hazardous and install-like targets.

    A text's expansion is judged without materialising it: each variable is flagged once, by its
    own value or any variable it references, so screening stays linear in the file's size.

    Args:
        text: The Makefile's contents.
        hazard_text: True for text that schedules jobs or purges/deletes data.
        hazard_name: True for a target name that says it does.
        install_name: True for a target name that says it installs, deploys or publishes.

    Returns:
        ``(rules, hazardous, install_like)``. A target is hazardous when it, or any target it runs,
        has a hazardous name or recipe, or when hazardous text runs while make reads the file
        (then every target is). It is install-like when it, or any target it runs, installs by
        name or recipe.
    """
    rules = parse_makefile(text)
    var_edges = {n: _var_refs(v) for n, v in rules.variables.items()}
    # One node stands for "any variable": it reaches every variable, and a computed reference
    # reaches it, so tainting stays linear in the number of references.
    var_edges[ANY_VARIABLE] = set(rules.variables)

    def judge(pred: Callable[[str], bool]) -> Callable[[str], bool]:
        tainted = closure({n for n, v in rules.variables.items() if pred(v)}, var_edges)
        return lambda t: pred(t) or not tainted.isdisjoint(_var_refs(t))

    is_hazard = judge(lambda t: bool(hazard_text(t) or MAKE_RM_RE.search(t)))
    runs_now = judge(lambda t: bool(_PARSE_FN_RE.search(t)))
    names = set(rules.recipes) | {t for runs in rules.edges.values() for t in runs}
    if any(runs_now(t) and is_hazard(t) for t in rules.parse_time):
        return rules, names, set()
    hazardous = closure({t for t, r in rules.recipes.items() if is_hazard(r)}
                        | {t for t in names if hazard_name(t)}, rules.edges)
    installs = judge(lambda t: bool(INSTALL_CONTENT_RE.search(t)))
    install_like = closure({t for t, r in rules.recipes.items() if installs(r)}
                           | {t for t in names if install_name(t)}, rules.edges)
    return rules, hazardous, install_like
