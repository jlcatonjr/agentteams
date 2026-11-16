"""An agent with no usable tool scope gets a read-only Claude grant, with a warning naming it (IM item 42, G-04-9).

``_map_allowed_tools`` falls back when there is no front matter (including front matter that never closes), no inline
``tools: [...]`` list, or a list none of whose tokens maps to a Claude tool. The fallback used to be
``Bash, Read, Write, Edit``: write access and an unrestricted shell for exactly the agent nobody scoped (C-3).
"""

from __future__ import annotations

import warnings

import pytest

from agentteams.frameworks import claude as claude_mod
from agentteams.frameworks.claude import ClaudeAdapter

READ_ONLY = "Read, Grep, Glob"

CASES = {
    "no front matter": "# Loose\n\nAn agent file with no front matter.\n",
    "unclosed front matter": "---\nname: Loose\ndescription: \"x\"\n\n# Loose\n",
    "no tools list": "---\nname: Loose\ndescription: \"x\"\n---\n\n# Loose\n",
    "block-form tools": "---\nname: Loose\ndescription: \"x\"\ntools:\n  - read\n---\n\n# Loose\n",
    "unmapped tokens": "---\nname: Loose\ndescription: \"x\"\ntools: ['frobnicate']\n---\n\n# Loose\n",
    "empty list": "---\nname: Loose\ndescription: \"x\"\ntools: []\n---\n\n# Loose\n",
}


def _tools_line(rendered: str) -> str:
    return next(line for line in rendered.splitlines() if line.startswith("tools:"))


def test_the_default_is_read_only() -> None:
    assert claude_mod._CLAUDE_DEFAULT_ALLOWED_TOOLS == READ_ONLY


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_unscoped_agent_renders_read_only_and_warns_by_name(case: str) -> None:
    with pytest.warns(UserWarning, match=r"loose-agent.*no usable tool scope") as caught:
        rendered = ClaudeAdapter().render_agent_file(CASES[case], "loose-agent", {})
    assert _tools_line(rendered) == f"tools: {READ_ONLY}"
    for granted in ("Bash", "Write", "Edit"):
        assert granted not in _tools_line(rendered), case
    assert len(caught) == 1


def test_the_warning_says_which_scope_was_missing() -> None:
    reasons = {
        "no front matter": "no front matter",
        "unclosed front matter": "no front matter",
        "no tools list": "no inline `tools: [...]` list",
        "block-form tools": "no inline `tools: [...]` list",
        "unmapped tokens": "maps to no Claude tool",
        "empty list": "maps to no Claude tool",
    }
    for case, reason in reasons.items():
        with pytest.warns(UserWarning) as caught:
            claude_mod._map_allowed_tools(CASES[case], agent="loose-agent")
        assert reason in str(caught[0].message), case


def test_a_scoped_agent_neither_falls_back_nor_warns() -> None:
    scoped = "---\nname: Scoped\ndescription: \"x\"\ntools: ['read', 'edit', 'execute']\n---\n\n# Scoped\n"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        rendered = ClaudeAdapter().render_agent_file(scoped, "scoped", {})
    assert _tools_line(rendered) == "tools: Read, Edit, Write, Bash"


def test_the_front_matter_injector_defaults_to_read_only() -> None:
    out = claude_mod._inject_claude_front_matter("# Body\n", "x", "d")
    assert f"tools: {READ_ONLY}" in out


@pytest.mark.parametrize("builder", [
    "# Team Builder\n\nNo front matter.\n",
    "---\nname: Team Builder\ndescription: \"b\"\n---\n\n# Team Builder\n",
])
def test_an_unscoped_claude_builder_is_refused(builder: str) -> None:
    # @security: render_builder_file returned these unchanged, and Claude gives a sub-agent with no tools: key the
    # whole tool pool. It now fails closed, as Grok's builder does.
    with pytest.raises(ValueError, match="needs front matter with a `tools:` line"):
        ClaudeAdapter().render_builder_file(builder, {})


def test_the_shipped_claude_builder_still_renders() -> None:
    from pathlib import Path

    template = (Path(claude_mod.__file__).resolve().parents[1] / "templates" / "builder"
                / "team-builder-claude.template.md").read_text(encoding="utf-8")
    out = ClaudeAdapter().render_builder_file(template, {})
    assert "tools: Read, Edit, Write, Grep, Glob, Bash" in out


def test_grok_shares_the_read_only_fallback() -> None:
    from agentteams.frameworks.grok import GrokAdapter

    with pytest.warns(UserWarning, match="loose-agent"):
        rendered = GrokAdapter().render_agent_file(CASES["no tools list"], "loose-agent", {})
    assert "Bash" not in _tools_line(rendered) and "Write" not in _tools_line(rendered)
