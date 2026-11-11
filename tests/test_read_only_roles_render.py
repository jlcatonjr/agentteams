"""Read-only roles carry no write or execute capability on any rendered surface (IM item 02, G-04-8).

`tests/test_agent_tool_scopes.py` checks four Copilot templates. A regression in an adapter's mapping passed
it. This renders the read-only set on every surface that declares capabilities and checks the emitted
declaration (through each adapter's ``render_agent_file``). The merge side, a deployed file that keeps a
wider grant (G-04-1), is pinned in ``tests/test_capability_front_matter_narrowing.py``.

Codex `sandbox_mode` is a declaration Codex ignores for a spawned agent (items 15/40 own enforcement), so it
is checked as a declaration only, never counted as enforcement.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "agentteams" / "templates"

#: The read-only roles: the four Copilot-era ones plus the two read-only auditors.
READ_ONLY = ("security", "adversarial", "code-hygiene", "conflict-auditor", "quality-auditor", "technical-validator")

#: Capability names that write or execute, per surface vocabulary.
_WRITE_OR_EXEC = {"edit", "write", "execute", "bash", "multiedit", "notebookedit", "shell", "terminal"}


@functools.lru_cache(maxsize=None)
def _render(framework: str) -> dict[str, str]:
    from agentteams import analyze, ingest, render
    from agentteams.frameworks.registry import FRAMEWORKS

    adapter = FRAMEWORKS[framework]()
    desc = ingest.load(REPO / "examples/software-project/brief.json", scan_project=False)
    manifest = analyze.build_manifest(desc, framework=framework)
    out: dict[str, str] = {}
    for rel, content in render.render_all(manifest, templates_dir=TEMPLATES):
        slug = Path(rel).stem.replace(".agent", "")
        if slug in READ_ONLY and "/references/" not in rel and not rel.startswith("references/"):
            out[slug] = adapter.render_agent_file(content, slug, manifest)
    return out


def _tools_line(text: str) -> set[str]:
    m = re.search(r"^tools:\s*(.+)$", text, re.MULTILINE)
    assert m, "no tools: declaration"
    return {t.strip().strip("'\"").lower() for t in m.group(1).strip("[]").split(",") if t.strip()}


@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "claude", "grok"])
def test_tools_declaration_is_read_only(framework):
    files = _render(framework)
    assert {"security", "adversarial", "code-hygiene", "conflict-auditor"} <= set(files), sorted(files)
    for slug, text in files.items():
        granted = _tools_line(text)
        assert granted, f"{framework}/{slug}: empty tools: inherits every tool"
        assert not granted & _WRITE_OR_EXEC, f"{framework}/{slug} grants {sorted(granted & _WRITE_OR_EXEC)}"


def test_the_read_only_set_reaches_the_two_auditors_where_the_team_has_them():
    files = _render("claude")
    present = set(files)
    assert "quality-auditor" in present, "the example team should carry quality-auditor"
    assert "technical-validator" in present, "the example team should carry technical-validator"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="IM item 14: Goose read-only recipes still get the builtin `developer` "
                                       "extension (shell + file editor). Remove this mark when item 14 lands.")
def test_goose_read_only_recipes_carry_no_write_or_shell_extension():
    files = _render("goose")
    assert {"security", "adversarial"} <= set(files)
    for slug, text in files.items():
        block = text[text.find("extensions:"):] if "extensions:" in text else ""
        assert not re.search(r"name:\s*developer\b", block), f"goose/{slug} carries the developer extension"


def test_codex_read_only_agents_declare_read_only_sandbox_mode():
    """A declaration, not enforcement (Codex ignores a spawned agent's sandbox_mode)."""
    files = _render("codex")
    assert {"security", "adversarial"} <= set(files)
    for slug, text in files.items():
        assert re.search(r'^sandbox_mode = "read-only"$', text, re.MULTILINE), f"codex/{slug}"
        assert "declaration, NOT enforced" in text
