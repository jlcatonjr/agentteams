"""Grok/Claude agent name parity (operator decision D4; plan tmp/by-week/2026-W42 §9 S1/S2).

Grok Build always loads ``.claude/agents/*.md`` and lets a ``.grok/agents`` file with the same ``name:`` shadow
it. So a Grok team rendered beside a Claude team must reproduce every Claude ``name:`` byte for byte (and the
same file names), or Grok loads both copies. Covers brief checklist items 22–23.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.frameworks.grok import GrokAdapter, _front_matter_name


def _render(framework: str, out: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    import build_team
    from agentteams.cli import security_gate

    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *a, **k: None)
    brief = Path("examples/software-project/brief.json").resolve()
    assert build_team.main(["--description", str(brief), "--framework", framework, "--output", str(out),
                            "--yes", "--no-scan", "--security-offline"]) == 0
    from agentteams.frameworks.registry import FRAMEWORKS

    agents = FRAMEWORKS[framework]().normalize_output_path(out)
    names = {}
    for path in sorted(agents.glob("*.md")):
        name = _front_matter_name(path.read_text(encoding="utf-8"))
        if name is not None:
            names[path.name] = name
    return names


def test_grok_render_reproduces_every_claude_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claude = _render("claude", tmp_path / "c", monkeypatch)
    grok = _render("grok", tmp_path / "g", monkeypatch)
    assert claude and set(grok) == set(claude)
    assert "team-builder.md" in grok
    for filename, name in claude.items():
        assert grok[filename] == name, filename


def _write_agent(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: \"d\"\ntools: Read\n---\nbody\n", encoding="utf-8")


def test_guard_notices_mismatch_orphans_and_builtins(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    agents = root / ".grok" / "agents"
    agents.mkdir(parents=True)
    _write_agent(root / ".claude" / "agents" / "navigator.md", "Navigator — Old Name")
    _write_agent(root / ".claude" / "agents" / "only-claude.md", "Only Claude")
    rendered = [
        ("navigator.md", "---\nname: Navigator — Demo\ndescription: \"d\"\ntools: Read\n---\nb\n"),
        ("plan.md", "---\nname: plan\ndescription: \"d\"\ntools: Read\n---\nb\n"),
    ]
    _, notices = GrokAdapter().guard_rendered_files(rendered, agents)
    text = "\n".join(notices)
    assert "differs from the Claude twin" in text
    assert "have no Grok twin" in text and "only-claude.md" in text
    assert "built-in agent type" in text
