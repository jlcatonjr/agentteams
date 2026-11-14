"""The public API surface of one Python module, as ``agentteams.api_impact`` compares it (carved out of that module).

Keys and values (a value is what a change is detected on):

- public module-level functions and classes, and ``Class.method`` for public methods (and ``__init__``) of public
  classes: their signature;
- ``__all__``: the sorted list;
- ``cli:<option>`` for every ``add_argument`` option string: its literal ``choices`` (sorted), or ``""``;
- ``cli-pos:<name>`` for every positional ``add_argument``, also ``add_argument(dest="name")`` (#451 a): its literal
  ``choices`` (sorted), or ``""``, so adding or removing a subcommand-like choice counts as a change. ``choices``
  given as a name or call is compared by its source text, not by what it evaluates to. A name declared twice in one
  module (two subparsers) keeps both values joined with ``|``;
- ``cli-argv:<option>`` for a literal ``"--x" in sys.argv`` test (#451 a), unless a parser in the same module declares
  it. This is a heuristic: it misses ``from sys import argv`` and ``sys.argv[1] == "--x"``, and may report a string
  that is not really an option;
- ``cli-cmd:<name>`` for every ``add_parser`` subcommand.
"""
from __future__ import annotations

import ast
import re

#: Key prefix → how :func:`cli_label` labels the entry in a report.
_CLI_PREFIXES = {"cli:": "", "cli-pos:": " (positional)", "cli-argv:": " (sys.argv, heuristic)"}


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def _choices(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "choices":
            try:  # a literal is compared by its members; anything else (a name, a call) by its source text only
                return "choices=" + repr(sorted(map(str, ast.literal_eval(kw.value))))
            except (ValueError, TypeError):
                return "choices=" + ast.unparse(kw.value)
    return ""


def _is_sys_argv(node: ast.expr) -> bool:
    """``sys.argv`` or a subscript/slice of it."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return (isinstance(node, ast.Attribute) and node.attr == "argv"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _put(out: dict[str, str], key: str, value: str) -> None:
    """Record ``key``; two declarations of one name (in two subparsers, say) keep both values, so neither hides."""
    if key in out and out[key] != value:
        value = " | ".join(sorted({*out[key].split(" | "), value}))
    out[key] = value


def _cli_entries(tree: ast.Module, out: dict[str, str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            strings = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if node.func.attr == "add_argument" and not strings:
                dest = next((kw.value.value for kw in node.keywords if kw.arg == "dest"
                             and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)), None)
                if dest:  # add_argument(dest="x"): a positional named by keyword
                    _put(out, f"cli-pos:{dest}", _choices(node))
            elif node.func.attr == "add_argument":
                if strings[0].startswith("-"):
                    for opt in strings:
                        _put(out, f"cli:{opt}", _choices(node))
                else:
                    _put(out, f"cli-pos:{strings[0]}", _choices(node))
            elif node.func.attr == "add_parser" and strings:
                out[f"cli-cmd:{strings[0]}"] = ""
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) \
                and isinstance(node.left.value, str) and node.left.value.startswith("-") \
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) \
                and any(_is_sys_argv(c) for c in node.comparators):
            out.setdefault(f"cli-argv:{node.left.value}", "")


def surface(source: str) -> dict[str, str] | None:
    """Return the public API surface of one Python module (the keys are described in the module docstring).

    Args:
        source: The module's source text.

    Returns:
        The surface map, or ``None`` when the source does not parse.

    Raises:
        Nothing.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            out[node.name] = _signature(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out[node.name] = f"({', '.join(ast.unparse(b) for b in node.bases)})"
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        not item.name.startswith("_") or item.name == "__init__"):
                    out[f"{node.name}.{item.name}"] = _signature(item)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets) and node.value is not None:
                try:
                    out["__all__"] = repr(sorted(ast.literal_eval(node.value)))
                except (ValueError, TypeError):
                    out["__all__"] = ast.unparse(node.value)
    _cli_entries(tree, out)
    # A declared option is not also a heuristic one.
    for key in [k for k in out if k.startswith("cli-argv:")]:
        if f"cli:{key.split(':', 1)[1]}" in out:
            del out[key]
    return out


def cli_label(key: str) -> str | None:
    """The report label for a command-line surface key, or ``None`` for any other key.

    Args:
        key: A :func:`surface` key.

    Returns:
        ``--x`` for ``cli:--x``, ``name (positional)`` for ``cli-pos:name``, ``--x (sys.argv, heuristic)`` for
        ``cli-argv:--x``; ``None`` otherwise (``cli-cmd:`` keys stay symbols, as before).

    Raises:
        Nothing.
    """
    for prefix, suffix in _CLI_PREFIXES.items():
        if key.startswith(prefix):
            return key[len(prefix):] + suffix
    return None


def cli_term(label: str) -> str:
    """The bare option or argument name in a :func:`cli_label` label (what a checklist searches for).

    Args:
        label: A label from :func:`cli_label`.

    Returns:
        The name without its ``(positional)`` / ``(sys.argv, heuristic)`` suffix.

    Raises:
        Nothing.
    """
    return label.split(" (", 1)[0]


_CONSTANT = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def _top_level(body: list[ast.stmt]) -> list[ast.stmt]:
    """Module-level statements, including those inside top-level ``if`` / ``try`` blocks (where constants often are)."""
    out: list[ast.stmt] = []
    for node in body:
        out.append(node)
        if isinstance(node, ast.If):
            out += _top_level(node.body) + _top_level(node.orelse)
        elif isinstance(node, ast.Try):
            out += _top_level(node.body) + [s for h in node.handlers for s in _top_level(h.body)]
            out += _top_level(node.orelse) + _top_level(node.finalbody)
    return out


def constants(source: str) -> dict[str, str] | None:
    """Public module-level constants: ``UPPER_CASE`` names assigned at the top level, mapped to their value's source
    text (an annotation-only declaration maps to ``: <annotation>``), for the opt-in ``constants`` category (#451 c).
    Assignments inside top-level ``if`` / ``try`` blocks and tuple targets count; when a name is assigned more than
    once, the last assignment in source order is used. Augmented assignments and ``type`` aliases do not count.

    Args:
        source: The module's source text.

    Returns:
        Name → value text, or ``None`` when the source does not parse.

    Raises:
        TypeError: When ``source`` is not a string.
    """
    if not isinstance(source, str):
        raise TypeError(f"source must be str, got {type(source).__name__}")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out: dict[str, str] = {}
    for node in _top_level(tree.body):
        if isinstance(node, ast.Assign):
            names = [n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            value = ast.unparse(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = ast.unparse(node.value) if node.value is not None else f": {ast.unparse(node.annotation)}"
        else:
            continue
        for name in names:
            if _CONSTANT.match(name):
                out[name] = value
    return out
