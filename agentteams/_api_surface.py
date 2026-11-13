"""The public API surface of one Python module, as ``agentteams.api_impact`` compares it (carved out of that module).

Keys are public module-level functions and classes, ``Class.method`` for public methods (and ``__init__``) of public
classes, ``__all__``, ``cli:<option>`` for every ``add_argument`` option string and ``cli-cmd:<name>`` for every
``add_parser`` subcommand. Values are signatures (``__all__``: the sorted list).
"""
from __future__ import annotations

import ast


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def surface(source: str) -> dict[str, str] | None:
    """Return the public API surface of one Python module.

    Keys are public module-level functions and classes, ``Class.method`` for public methods (and ``__init__``)
    of public classes, ``__all__``, ``cli:<option>`` for every ``add_argument`` option string and
    ``cli-cmd:<name>`` for every ``add_parser`` subcommand. Values are signatures (``__all__``: the sorted list).

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
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            strings = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if node.func.attr == "add_argument":
                for opt in strings:
                    if opt.startswith("-"):
                        out[f"cli:{opt}"] = ""
            elif node.func.attr == "add_parser" and strings:
                out[f"cli-cmd:{strings[0]}"] = ""
    return out
