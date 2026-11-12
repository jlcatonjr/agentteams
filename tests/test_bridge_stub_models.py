"""Bridge stubs carry a Claude ``model:``: the default, or the model the existing stub already declares."""

from __future__ import annotations

import re
from pathlib import Path

from agentteams import bridge_subagents as bs


def _src(dir_path: Path, slug: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{slug}.agent.md").write_text(
        f"---\nname: {slug}\ndescription: d {slug}\ntools: ['read', 'search']\n"
        "model: ['Claude Opus 4.8 (copilot)']\n---\n\nbody\n",
        encoding="utf-8",
    )


def _model(path: Path) -> list[str]:
    head = path.read_text(encoding="utf-8").split("\n---", 1)[0]
    return re.findall(r"^model:\s*(.*)$", head, re.M)


def _team(tmp_path: Path) -> Path:
    src = tmp_path / ".github" / "agents"
    for slug in ("orchestrator", "security", "alpha-expert", "beta-expert"):
        _src(src, slug)
    return src


def test_fresh_stubs_get_the_defaults(tmp_path: Path) -> None:
    src = _team(tmp_path)
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    out = tmp_path / ".claude" / "agents"
    assert _model(out / "orchestrator.md") == ["claude-fable-5-2"]
    assert _model(out / "security.md") == ["opus"]
    assert _model(out / "workstream-expert.md") == ["opus"]
    assert len(result.models_defaulted) == 3


def test_reemit_keeps_an_existing_stub_model(tmp_path: Path) -> None:
    src = _team(tmp_path)
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    out = tmp_path / ".claude" / "agents"
    for name, value in (("orchestrator.md", "fable"), ("security.md", "inherit"), ("workstream-expert.md", "sonnet")):
        p = out / name
        p.write_text(p.read_text(encoding="utf-8").replace(_model(p)[0], value, 1), encoding="utf-8")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path, preserve_non_stubs=True)
    assert _model(out / "orchestrator.md") == ["fable"]
    assert _model(out / "security.md") == ["inherit"]
    assert _model(out / "workstream-expert.md") == ["sonnet"]
    assert result.models_defaulted == []


def test_old_stub_without_model_gains_the_default(tmp_path: Path) -> None:
    src = _team(tmp_path)
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    p = tmp_path / ".claude" / "agents" / "security.md"
    p.write_text(re.sub(r"^model:.*\n", "", p.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
    assert _model(p) == []
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert _model(p) == ["opus"]
    assert result.models_defaulted == [str(p.resolve())]


def test_unreadable_stub_model_is_replaced_by_the_default(tmp_path: Path) -> None:
    src = _team(tmp_path)
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    p = tmp_path / ".claude" / "agents" / "orchestrator.md"
    p.write_text(p.read_text(encoding="utf-8").replace("claude-fable-5-2", "Claude Opus 4.8 (copilot)"), encoding="utf-8")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert _model(p) == ["claude-fable-5-2"]


def test_native_body_is_never_touched(tmp_path: Path) -> None:
    src = _team(tmp_path)
    out = tmp_path / ".claude" / "agents"
    out.mkdir(parents=True)
    native = "---\nname: security\ndescription: native\ntools: Read\n---\n\nnative body\n"
    (out / "security.md").write_text(native, encoding="utf-8")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path, preserve_non_stubs=True)
    assert (out / "security.md").read_text(encoding="utf-8") == native
    assert str((out / "security.md").resolve()) not in result.models_defaulted


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    src = _team(tmp_path)
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path, dry_run=True)
    assert len(result.models_defaulted) == 3
    assert not (tmp_path / ".claude" / "agents").exists()


def test_trailing_comment_on_model_is_ignored(tmp_path: Path) -> None:
    src = _team(tmp_path)
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    p = tmp_path / ".claude" / "agents" / "security.md"
    p.write_text(p.read_text(encoding="utf-8").replace("model: opus", "model: 'sonnet'  # cheap"), encoding="utf-8")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert _model(p) == ["sonnet"]
    assert result.models_defaulted == []


def test_refresh_over_a_native_body_keeps_its_model(tmp_path: Path) -> None:
    src = _team(tmp_path)
    out = tmp_path / ".claude" / "agents"
    out.mkdir(parents=True)
    (out / "security.md").write_text("---\nname: security\nmodel: haiku\n---\n\nnative\n", encoding="utf-8")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)  # refresh semantics: replaces native bodies
    assert _model(out / "security.md") == ["haiku"]


def test_bridge_orchestrator_slug_gets_the_orchestrator_default(tmp_path: Path) -> None:
    src = tmp_path / ".github" / "agents"
    _src(src, "bridge-orchestrator")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert _model(tmp_path / ".claude" / "agents" / "bridge-orchestrator.md") == ["claude-fable-5-2"]


def test_notice_names_the_defaults_and_says_would_on_dry_run() -> None:
    from agentteams.frameworks._claude_model import CLAUDE_AGENT_MODEL_DEFAULT, CLAUDE_ORCHESTRATOR_MODEL_DEFAULT

    live = bs.models_defaulted_notice([".claude/agents/a.md"], dry_run=False)
    dry = bs.models_defaulted_notice([".claude/agents/a.md"], dry_run=True)
    assert live.startswith("Gave 1 ") and dry.startswith("Would give 1 ")
    for text in (live, dry):
        assert CLAUDE_ORCHESTRATOR_MODEL_DEFAULT in text and CLAUDE_AGENT_MODEL_DEFAULT in text
        assert text.endswith(".claude/agents/a.md")


def test_model_helpers() -> None:
    from agentteams.frameworks._claude_model import default_claude_model, is_claude_model_value

    assert default_claude_model("orchestrator") == "claude-fable-5-2"
    assert default_claude_model("bridge-orchestrator") == "claude-fable-5-2"
    assert default_claude_model("security") == "opus"
    for ok in ("opus", "inherit", "claude-fable-5-2", "opus[1m]"):
        assert is_claude_model_value(ok)
    for bad in (None, "", 5, "Claude Opus 4.8 (copilot)", "['opus']", "opus\nx: y"):
        assert not is_claude_model_value(bad)


def test_bridge_merge_reports_the_defaulted_stubs(tmp_path: Path) -> None:
    """End to end through bridge.run_bridge: the notice is on the BridgeResult."""
    from agentteams.bridge import run_bridge

    src = _team(tmp_path)
    result = run_bridge(
        source_dir=src, source_framework="copilot-vscode", target_framework="claude", output_root=tmp_path,
        merge_only=True, emit_skills=False, host_features=["bridge:copilot-vscode-to-claude:subagents"],
    )
    notes = [n for n in result.notices if "default `model:`" in n]
    assert len(notes) == 1 and notes[0].startswith("Gave 3 ")
    assert _model(tmp_path / ".claude" / "agents" / "orchestrator.md") == ["claude-fable-5-2"]
