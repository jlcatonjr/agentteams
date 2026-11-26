"""1.3.2: every module ``--merge-pr`` loads is integrity-pinned, so a weakened gate cannot merge unprompted.

The tree is the gate modules, every ``agentteams`` module they import (function-local imports included, one hop),
and the module-level imports of those, transitively. ``dev_merge_release`` refuses a change to any pinned module, so
pinning the tree makes every change to it an operator merge (D3).
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentteams.integrity import ENFORCEMENT_MODULES

_ROOT = Path(__file__).resolve().parents[1]
_GATE = ("agentteams/cli/dev_merge.py", "agentteams/cli/dev_merge_release.py", "agentteams/cli/dev_merge_commands.py")
#: Named exemptions. capability_hints holds LAST_RELEASE_TAG, which changes at every release; pinning it would force a
#: manifest re-pin into each version commit. dev_merge_release refuses any change to that tag instead.
_EXEMPT = frozenset({"agentteams/capability_hints.py"})


def _imports(rel: str, *, module_level: bool) -> list[str]:
    tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
    nodes = tree.body if module_level else list(ast.walk(tree))
    out: list[str] = []
    for n in nodes:
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("agentteams"):
            out += [n.module] + [f"{n.module}.{a.name}" for a in n.names]
        elif isinstance(n, ast.Import):
            out += [a.name for a in n.names if a.name.startswith("agentteams")]
    return out


def _resolve(module: str) -> str | None:
    for cand in (module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py"):
        if (_ROOT / cand).is_file():
            return cand
    return None


def _tree() -> set[str]:
    todo = list(_GATE) + [r for g in _GATE for r in map(_resolve, _imports(g, module_level=False)) if r]
    seen: set[str] = set()
    while todo:
        rel = todo.pop()
        if rel not in seen:
            seen.add(rel)
            todo += [r for r in map(_resolve, _imports(rel, module_level=True)) if r]
    return seen


def test_every_module_the_merge_gate_loads_is_pinned():
    tree = _tree()
    assert set(_GATE) <= tree
    unpinned = sorted(p for p in tree if p not in ENFORCEMENT_MODULES and p not in _EXEMPT
                      and not p.endswith("__init__.py"))
    assert not unpinned, unpinned


def test_the_exemptions_are_still_in_the_tree():
    assert _EXEMPT <= _tree()   # an exemption that no longer applies must be removed
