"""1.3.2: ``agentteams --merge-pr`` loads only integrity-pinned modules (operator ruling 2026-11-26).

A fresh interpreter imports the console entry, takes the ``--merge-pr`` route's modules and every module the gate
imports inside its functions, and then lists what actually loaded. Every ``agentteams`` module there must be pinned,
and ``build_team`` (the full CLI) must not load. ``tests/test_dev_merge_import_tree_pinned.py`` checks the same tree
statically from the source.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentteams.integrity import ENFORCEMENT_MODULES
from tests.test_dev_merge_import_tree_pinned import _EXEMPT

_ROOT = Path(__file__).resolve().parents[1]
_PROBE = r"""
import ast, importlib, json, sys
from pathlib import Path
import agentteams.cli.entry as entry
assert entry._is_merge_pr(["--merge-pr", "7"])
import agentteams.cli.merge_pr_main
gate = ["agentteams/cli/merge_pr_main.py", "agentteams/cli/dev_merge.py", "agentteams/cli/dev_merge_release.py",
        "agentteams/cli/dev_merge_commands.py"]
for rel in gate:   # the gate's function-local imports, as a merge runs them
    for n in ast.walk(ast.parse(Path(rel).read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("agentteams"):
            importlib.import_module(n.module)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("agentteams"):
                    importlib.import_module(a.name)
out = []
for name, mod in sorted(sys.modules.items()):
    f = getattr(mod, "__file__", None) or ""
    if name == "build_team" or name == "agentteams" or name.startswith("agentteams."):
        out.append([name, f])
print(json.dumps(out))
"""


def _loaded() -> list[tuple[str, str]]:
    proc = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(_ROOT)!r})\n" + _PROBE],
                          cwd=_ROOT, capture_output=True, text=True, timeout=120, check=True)
    return [(n, f) for n, f in json.loads(proc.stdout)]


def test_the_merge_pr_route_loads_only_pinned_modules_and_never_the_full_cli():
    loaded = _loaded()
    assert "build_team" not in {n for n, _ in loaded}
    rels = {Path(f).resolve().relative_to(_ROOT).as_posix() for _, f in loaded if f}
    assert {"agentteams/cli/entry.py", "agentteams/cli/merge_pr_main.py", "agentteams/cli/dev_merge.py"} <= rels
    unpinned = sorted(r for r in rels if r not in ENFORCEMENT_MODULES and r not in _EXEMPT)
    assert not unpinned, unpinned


def test_the_full_cli_refuses_merge_pr_and_names_the_entry_point(capsys):
    from agentteams.cli import app

    assert app.main(["--merge-pr", "7"]) == 2
    assert "dedicated entry point" in capsys.readouterr().err


def test_the_console_entry_routes_only_merge_pr_to_the_gate():
    from agentteams.cli import entry

    assert entry._is_merge_pr(["--merge-pr", "7"]) and entry._is_merge_pr(["--merge-pr=7", "--team-id", "t"])
    assert not entry._is_merge_pr(["--update", "--merge"]) and not entry._is_merge_pr(["--merge-pr-x"])
