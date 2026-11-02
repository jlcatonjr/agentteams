"""A ``--dry-run`` writes nothing: the whole project tree is byte-identical afterwards (baseAgent P14 follow-up).

baseAgent saw eight tracked files rewritten near an ``--update --merge --dry-run`` (2026-11-02). The run was
cleared (another process wrote them later), but the property is worth pinning for every framework.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests._fresh_intel import run_build, seed_offline_intel

BRIEF = Path(__file__).resolve().parent.parent / "examples" / "software-project" / "brief.json"
_AGENTS_DIR = {"copilot-vscode": ".github/agents", "claude": ".claude/agents", "goose": ".goose/recipes"}


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


@pytest.mark.parametrize("framework", sorted(_AGENTS_DIR))
def test_update_merge_dry_run_leaves_the_tree_byte_identical(tmp_path, framework):
    brief = tmp_path / "brief.json"
    brief.write_text(BRIEF.read_text(encoding="utf-8"))
    out = tmp_path / _AGENTS_DIR[framework]

    def run(*extra: str, seed: bool = True):
        return run_build(brief, tmp_path, framework, out, *extra, seed=seed)

    first = run()
    assert first.returncode == 0, first.stdout[-1000:] + first.stderr[-1000:]
    # Give the merge something to do: a hand edit inside one fence and one outside.
    agent = next(p for p in sorted(out.iterdir()) if p.is_file() and "orchestrator" in p.name)
    text = agent.read_text(encoding="utf-8")
    agent.write_text(text.replace("-->\n", "-->\nhand edit inside a fence\n", 1) + "\nhand edit outside\n",
                     encoding="utf-8")
    seed_offline_intel(out, framework)
    before = _tree(tmp_path)
    proc = run("--update", "--merge", "--dry-run", seed=False)
    assert proc.returncode == 0, proc.stdout[-1000:] + proc.stderr[-1000:]
    after = _tree(tmp_path)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"--dry-run wrote: {changed}"
