"""The public API surface of one Python module, as ``agentteams.api_impact`` compares it (carved out of that module).

Keys and values (a value is what a change is detected on):

- public module-level functions and classes, and ``Class.method`` for public methods (and ``__init__``) of public
  classes: their signature;
- ``__all__``: the sorted list;
- ``cli:<option>`` for every ``add_argument`` option string: its ``choices`` (sorted), or ``""``;
- ``cli-pos:<name>`` for every positional ``add_argument`` (#451 a): its ``choices`` (sorted), or ``""``, so adding or
  removing a subcommand-like choice counts as a change;
- ``cli-argv:<option>`` for a literal ``"--x" in sys.argv`` test (#451 a). This is a heuristic: it finds options read
  straight from ``sys.argv`` that no parser declares, and may report a string that is not really an option;
- ``cli-cmd:<name>`` for every ``add_parser`` subcommand.
"""
from __future__ import annotations

import ast

#: Key prefix → how :func:`cli_label` labels the entry in a report.
_CLI_PREFIXES = {"cli:": "", "cli-pos:": " (positional)", "cli-argv:": " (sys.argv, heuristic)"}


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def _choices(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "choices":
            try:
                return "choices=" + repr(sorted(map(str, ast.literal_eval(kw.value))))
            except (ValueError, TypeError, SyntaxError):
                return "choices=" + ast.unparse(kw.value)
    return ""


def _is_sys_argv(node: ast.expr) -> bool:
    """``sys.argv`` or a subscript/slice of it."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return (isinstance(node, ast.Attribute) and node.attr == "argv"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _cli_entries(tree: ast.Module, out: dict[str, str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            strings = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if node.func.attr == "add_argument" and strings:
                if strings[0].startswith("-"):
                    for opt in strings:
                        out[f"cli:{opt}"] = _choices(node)
                else:
                    out[f"cli-pos:{strings[0]}"] = _choices(node)
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
