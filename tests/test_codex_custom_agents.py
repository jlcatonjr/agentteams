"""Codex custom agents: `.codex/agents/<name>.toml` emission (cross-orchestrator request
from baseAgent, 2026-09-29).

Acceptance criteria covered here:
* TOML round-trip through ``tomllib`` for bodies containing ``\"\"\"``, ``'''``, backslashes,
  non-ASCII characters, control characters and fenced code;
* required keys; unique names; ``sandbox_mode`` only for read-only roles; no model, provider
  or approval keys;
* no duplicate H1;
* the no-overwrite guard for ``AGENTS.md`` and ``AGENTS.override.md``;
* no flat ``.agents/<slug>.md`` specialist files;
* bespoke tools carried verbatim; handoffs translated and parsed back.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agentteams.frameworks.codex import (
    CodexAdapter,
    agents_md_is_codex_owned,
    h1_count,
    toml_multiline_string,
)
from agentteams.frameworks.format_spec import FORMAT_SPECS
from agentteams.interop import detect_framework, export_to_cai, import_from_cai, run_interop

_ALLOWED_KEYS = {"name", "description", "developer_instructions", "sandbox_mode"}
_FORBIDDEN_KEYS = {
    "model", "model_provider", "model_reasoning_effort", "approval_policy",
    "approval_mode", "profile", "model_providers",
}


def _agent(
    name: str,
    tools: str,
    body: str,
    *,
    handoffs: str = "",
    description: str = "Does things",
) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{description}"\n'
        f"tools: {tools}\n"
        'model: ["auto"]\n'
        f"{handoffs}"
        "---\n\n"
        f"{body}"
    )


_HANDOFF_YAML = (
    "handoffs:\n"
    "  - label: Return to Orchestrator\n"
    "    agent: orchestrator\n"
    '    prompt: "Audit complete. Review the findings."\n'
    "    send: false\n"
)


# ---------------------------------------------------------------------------
# TOML serialisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        'has """ triple double quotes and a trailing quote"',
        "has ''' triple single quotes",
        "back\\slash \\n not-a-newline \\u0041",
        "non-ASCII: café — λ → 😀",
        "```python\nprint('''x''')\n```\n",
        "control \x01 char and tab\tand CRLF\r\nline",
        "ends with single quote'",
        "",
    ],
)
def test_multiline_string_round_trips_through_tomllib(text: str) -> None:
    doc = tomllib.loads("v = " + toml_multiline_string(text) + "\n")
    expected = text.replace("\r\n", "\n")
    if not expected.endswith("\n"):
        expected += "\n"
    assert doc["v"] == expected


def test_literal_string_used_when_safe() -> None:
    assert toml_multiline_string("no escapes needed").startswith("'''")
    assert toml_multiline_string("has ''' inside").startswith('"""')


def test_rendered_agent_round_trips_hostile_body() -> None:
    body = (
        "# Hostile Agent\n\n"
        'Quote """ and \'\'\' and back\\slash and café.\n\n'
        "```toml\nkey = \"\"\"x\"\"\"\n```\n"
    )
    out = CodexAdapter().render_agent_file(
        _agent("Hostile Agent", "['read', 'edit']", body), "hostile-agent", {}
    )
    doc = tomllib.loads(out)
    assert doc["name"] == "hostile-agent"
    assert 'Quote """ and \'\'\' and back\\slash and café.' in doc["developer_instructions"]


# ---------------------------------------------------------------------------
# Rendered agent contract
# ---------------------------------------------------------------------------

def test_required_keys_and_no_forbidden_keys() -> None:
    out = CodexAdapter().render_agent_file(
        _agent("Worker — Proj", "['read', 'edit', 'execute']", "# Worker — Proj\n\nBody.\n"),
        "worker",
        {},
    )
    doc = tomllib.loads(out)
    assert {"name", "description", "developer_instructions"} <= set(doc)
    assert set(doc) <= _ALLOWED_KEYS
    assert not (set(doc) & _FORBIDDEN_KEYS)
    assert doc["name"] == "worker"
    assert doc["description"] == "Does things"
    assert "sandbox_mode" not in doc


@pytest.mark.parametrize(
    ("tools", "read_only"),
    [
        ("['read', 'search']", True),
        ("['read']", True),
        ("['read', 'search', 'edit']", False),
        ("['read', 'execute']", False),
        ("['read', 'search', 'runCommands']", False),  # unknown bespoke tool: not read-only
        ("['read', 'search', 'agent']", False),
    ],
)
def test_sandbox_mode_only_for_read_only_roles(tools: str, read_only: bool) -> None:
    doc = tomllib.loads(
        CodexAdapter().render_agent_file(_agent("A", tools, "# A\n\nBody.\n"), "a", {})
    )
    assert ("sandbox_mode" in doc) is read_only
    if read_only:
        assert doc["sandbox_mode"] == "read-only"
        assert "not a ceiling" in doc["developer_instructions"]


def test_no_tools_declared_means_no_sandbox_mode() -> None:
    content = "---\nname: A\ndescription: \"d\"\n---\n\n# A\n\nBody.\n"
    doc = tomllib.loads(CodexAdapter().render_agent_file(content, "a", {}))
    assert "sandbox_mode" not in doc


def test_emitter_documents_sandbox_mode_is_not_a_ceiling() -> None:
    out = CodexAdapter().render_agent_file(_agent("A", "['read']", "# A\n\nB\n"), "a", {})
    header = out.split("name = ", 1)[0]
    assert "not a ceiling" in header


@pytest.mark.parametrize(
    "body",
    [
        "# Worker — Proj\n\nBody.\n",  # body carries its own H1
        "Body without a heading.\n",  # no H1: one is added
        "<!-- AGENTTEAMS:BEGIN content v=1 -->\n# Worker\n\nBody.\n<!-- AGENTTEAMS:END content -->\n",
        "# Worker\n\n```md\n# not a heading (in code)\n```\n",
    ],
)
def test_exactly_one_h1(body: str) -> None:
    doc = tomllib.loads(
        CodexAdapter().render_agent_file(_agent("Worker — Proj", "['read']", body), "worker", {})
    )
    assert h1_count(doc["developer_instructions"]) == 1


def test_bespoke_tools_carried_verbatim_as_self_limit() -> None:
    doc = tomllib.loads(
        CodexAdapter().render_agent_file(
            _agent("Wasm", "['read', 'search', 'edit', 'runCommands']", "# Wasm\n\nB\n"),
            "wasm-wat-expert",
            {},
        )
    )
    instr = doc["developer_instructions"]
    assert "Declared tools: `read`, `search`, `edit`, `runCommands`." in instr
    assert "Codex does not enforce these grants" in instr


def test_handoffs_translated_and_fences_and_notes_carried() -> None:
    body = (
        "# Auditor\n\n"
        "<!-- AGENTTEAMS:BEGIN rules v=1 -->\nRule one.\n<!-- AGENTTEAMS:END rules -->\n\n"
        "## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — keep me.\n"
    )
    doc = tomllib.loads(
        CodexAdapter().render_agent_file(
            _agent("Auditor", "['read', 'search']", body, handoffs=_HANDOFF_YAML), "auditor", {}
        )
    )
    instr = doc["developer_instructions"]
    assert "<!-- AGENTTEAMS:BEGIN rules v=1 -->" in instr
    assert "USER-EDITABLE" in instr and "keep me." in instr
    assert '- Hand off to `orchestrator` ("Return to Orchestrator")' in instr
    assert "handoffs:" not in instr  # front matter translated, not copied


def test_native_render_rewrites_paths_interop_keeps_them() -> None:
    content = _agent("A", "['read']", "# A\n\nSee .github/agents/references/x.md\n")
    native = tomllib.loads(CodexAdapter().render_agent_file(content, "a", {}))
    interop = tomllib.loads(
        CodexAdapter().render_agent_file(content, "a", {"interop_source_framework": "copilot-vscode"})
    )
    assert ".codex/agents/references/x.md" in native["developer_instructions"]
    assert ".github/agents/references/x.md" in interop["developer_instructions"]


def test_adapter_layout() -> None:
    adapter = CodexAdapter()
    assert adapter.get_agents_dir(Path("/p")) == Path("/p/.codex/agents")
    assert adapter.get_file_extension("agent") == ".toml"
    assert adapter.finalize_output_path("x.agent.md", "agent") == "x.toml"
    assert adapter.finalize_output_path("../copilot-instructions.md", "instructions") == "../../AGENTS.md"
    assert adapter.handoff_delivery_mode() == "native"
    assert adapter.normalize_output_path(Path("/p")) == Path("/p/.codex/agents")
    assert adapter.normalize_output_path(Path("/p/.codex/agents")) == Path("/p/.codex/agents")


# ---------------------------------------------------------------------------
# Interop: canonical copilot-vscode team -> codex
# ---------------------------------------------------------------------------

def _source_team(root: Path) -> Path:
    agents = root / ".github" / "agents"
    agents.mkdir(parents=True)
    (root / ".github" / "copilot-instructions.md").write_text("# Team\n\nRules.\n", encoding="utf-8")
    (agents / "orchestrator.agent.md").write_text(
        _agent("Orchestrator", "['read', 'edit', 'agent']", "# Orchestrator\n\nRoutes.\n"),
        encoding="utf-8",
    )
    (agents / "security.agent.md").write_text(
        _agent("Security", "['read', 'search']", "# Security\n\nAudits.\n", handoffs=_HANDOFF_YAML),
        encoding="utf-8",
    )
    (agents / "wasm-wat-expert.agent.md").write_text(
        _agent(
            "Wasm", "['read', 'search', 'edit', 'runCommands']",
            "<!-- Bespoke custom agent -->\n# Wasm\n\nVerbatim body — é.\n",
        ),
        encoding="utf-8",
    )
    return agents


def test_interop_emits_toml_only_and_guards_shared_files(tmp_path: Path) -> None:
    agents = _source_team(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Goose bridge entry\n", encoding="utf-8")
    (tmp_path / "AGENTS.override.md").write_text("# project-owned\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    target = tmp_path / ".codex" / "agents"
    result = run_interop(agents, "codex", target, overwrite=True)

    assert result.success
    tomls = sorted(target.glob("*.toml"))
    assert [p.stem for p in tomls] == ["orchestrator", "security", "wasm-wat-expert"]
    # Zero writes outside .codex/agents: every pre-existing file is byte-identical and the
    # only new files are the TOML agents.
    for path, data in before.items():
        assert path.read_bytes() == data, f"{path} was modified"
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - set(before)
    assert new == set(tomls)
    assert not (tmp_path / ".agents").exists()
    assert any("AGENTS.md" in n for n in result.notices)

    docs = [tomllib.loads(p.read_text(encoding="utf-8")) for p in tomls]
    names = [d["name"] for d in docs]
    assert len(names) == len(set(names))
    by_name = {d["name"]: d for d in docs}
    assert by_name["security"]["sandbox_mode"] == "read-only"
    assert "sandbox_mode" not in by_name["orchestrator"]
    assert "sandbox_mode" not in by_name["wasm-wat-expert"]
    wasm = by_name["wasm-wat-expert"]["developer_instructions"]
    assert wasm.startswith("<!-- Bespoke custom agent -->\n# Wasm\n\nVerbatim body — é.\n")
    assert "`runCommands`" in wasm
    for d in docs:
        assert h1_count(d["developer_instructions"]) == 1
        assert set(d) <= _ALLOWED_KEYS


def test_interop_writes_agents_md_when_absent_and_refreshes_own(tmp_path: Path) -> None:
    agents = _source_team(tmp_path)
    target = tmp_path / ".codex" / "agents"
    run_interop(agents, "codex", target)
    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.is_file()
    assert agents_md_is_codex_owned(agents_md)
    # A second run may refresh a Codex-generated AGENTS.md.
    result = run_interop(agents, "codex", target, overwrite=True)
    assert str(agents_md) in result.converted
    assert not (tmp_path / "AGENTS.override.md").exists()


def test_native_guard_drops_foreign_agents_md(tmp_path: Path) -> None:
    out = tmp_path / ".codex" / "agents"
    out.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("# hand-written\n", encoding="utf-8")
    rendered = [("a.toml", "x"), ("../../AGENTS.md", "y")]
    kept, notices = CodexAdapter().guard_rendered_files(rendered, out)
    assert kept == [("a.toml", "x")]
    assert len(notices) == 1


def test_codex_round_trip_keeps_handoffs_and_tools(tmp_path: Path) -> None:
    agents = _source_team(tmp_path)
    cai = export_to_cai(agents, "copilot-vscode")
    target = tmp_path / "out" / ".codex" / "agents"
    import_from_cai(cai, "codex", target)
    assert detect_framework(target) == "codex"
    back = {a["slug"]: a for a in export_to_cai(target)["agents"]}
    assert back["security"]["handoffs"][0]["to"] == "orchestrator"
    assert back["security"]["handoffs"][0]["prompt"] == "Audit complete. Review the findings."
    assert back["security"]["capabilities"]["tool_scopes"] == ["read", "search"]
    assert back["security"]["description"] == "Does things"
    # translation block lifted back out of the body
    assert "Codex Runtime Notes" not in back["security"]["body_markdown"]


def test_format_spec_watches_subagents_and_skills_pages() -> None:
    spec = FORMAT_SPECS["codex"]
    urls = (spec.source_url, *spec.extra_source_urls)
    assert all(u.endswith(".md") for u in urls)
    assert any("subagents" in u for u in urls)
    assert any("skills" in u for u in urls)
    assert "developer_instructions" in spec.expected_doc_tokens
    assert {".codex/agents", ".agents/skills", "SKILL.md"} <= set(spec.expected_locations)


def test_agents_md_notice_not_stacked_on_rerender() -> None:
    adapter = CodexAdapter()
    once = adapter.render_instructions_file("# Team\n\nRules.\n", {})
    twice = adapter.render_instructions_file(once, {})
    assert twice == once


def test_backups_of_toml_agents_are_not_loadable_and_restore(tmp_path: Path) -> None:
    """Codex loads .codex/agents/**/*.toml recursively; a backed-up agent must not load twice."""
    from agentteams.backup import backup_output_dir, restore_backup

    out = tmp_path / ".codex" / "agents"
    out.mkdir(parents=True)
    (out / "security.toml").write_text('name = "security"\n', encoding="utf-8")
    res = backup_output_dir(out, files_to_backup=["security.toml"], framework="codex")
    assert res.backup_path is not None
    assert [p.name for p in out.rglob("*.toml")] == ["security.toml"]

    (out / "security.toml").write_text('name = "changed"\n', encoding="utf-8")
    restore_backup(res.backup_path, out, remove_extra=True)
    assert (out / "security.toml").read_text(encoding="utf-8") == 'name = "security"\n'


def test_handoff_with_quotes_and_unicode_round_trips(tmp_path: Path) -> None:
    content = _agent(
        "A", "['read']", "# A\n\nB\n",
        handoffs=(
            "handoffs:\n"
            "  - label: Say café\n"
            "    agent: worker\n"
            "    prompt: 'He said \"go\" — now'\n"
            "    send: true\n"
        ),
    )
    parsed = CodexAdapter().parse_agent_source(CodexAdapter().render_agent_file(content, "a", {}))
    assert parsed is not None
    assert parsed["handoffs"] == [
        {"label": "Say café", "agent": "worker", "prompt": 'He said "go" — now', "send": True}
    ]


def test_symlinked_agents_md_is_never_written(tmp_path: Path) -> None:
    """Security review 2026-09-29: a symlink (live or dangling) must not redirect the write."""
    out = tmp_path / ".codex" / "agents"
    out.mkdir(parents=True)
    victim = tmp_path / "CLAUDE.md"
    victim.write_text("keep\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(victim)
    kept, notices = CodexAdapter().guard_rendered_files([("../../AGENTS.md", "x")], out)
    assert kept == [] and notices
    (tmp_path / "AGENTS.md").unlink()
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / "nowhere.md")  # dangling
    assert not agents_md_is_codex_owned(tmp_path / "AGENTS.md")

    agents = _source_team(tmp_path / "proj")
    link = tmp_path / "proj" / "AGENTS.md"
    link.symlink_to(tmp_path / "outside.md")  # dangling, outside the project
    result = run_interop(agents, "codex", tmp_path / "proj" / ".codex" / "agents", overwrite=True)
    assert str(link) in result.skipped
    assert not (tmp_path / "outside.md").exists()
    assert victim.read_text(encoding="utf-8") == "keep\n"


def test_non_utf8_agents_md_is_foreign(tmp_path: Path) -> None:
    p = tmp_path / "AGENTS.md"
    p.write_bytes(b"\xff\xfe not utf-8")
    assert agents_md_is_codex_owned(p) is False


def test_malformed_codex_toml_fails_loudly() -> None:
    with pytest.raises(ValueError):
        CodexAdapter().parse_agent_source("name = \n")
    with pytest.raises(ValueError):
        CodexAdapter().parse_agent_source('name = "a"\ndescription = "b"\n')


def test_agents_md_paths_follow_codex_layout() -> None:
    body = "# Team\n\nSee .github/agents/references/x.md\n"
    native = CodexAdapter().render_instructions_file(body, {})
    interop = CodexAdapter().render_instructions_file(body, {"interop_source_framework": "copilot-vscode"})
    assert ".codex/agents/references/x.md" in native
    assert ".github/agents/references/x.md" in interop
    assert "](.agents/" not in native and " .agents/" not in native


def test_display_name_round_trips_even_when_h1_differs(tmp_path: Path) -> None:
    content = _agent("Canonical Name — Proj", "['read']", "# A Different Heading\n\nB\n")
    parsed = CodexAdapter().parse_agent_source(CodexAdapter().render_agent_file(content, "slug", {}))
    assert parsed["name"] == "Canonical Name — Proj"


def test_block_list_tools_are_read() -> None:
    content = "---\nname: A\ndescription: \"d\"\ntools:\n  - read\n  - search\n---\n\n# A\n\nB\n"
    doc = tomllib.loads(CodexAdapter().render_agent_file(content, "a", {}))
    assert "Declared tools: `read`, `search`." in doc["developer_instructions"]
    assert doc["sandbox_mode"] == "read-only"


def test_codex_notice_not_carried_into_other_targets(tmp_path: Path) -> None:
    agents = _source_team(tmp_path)
    run_interop(agents, "codex", tmp_path / ".codex" / "agents")
    goose_dir = tmp_path / "g" / ".goose" / "recipes"
    run_interop(tmp_path / ".codex" / "agents", "goose", goose_dir)
    goose_agents_md = tmp_path / "g" / "AGENTS.md"
    assert goose_agents_md.is_file()
    assert not agents_md_is_codex_owned(goose_agents_md)


def test_legacy_output_and_notice(tmp_path: Path) -> None:
    assert CodexAdapter().normalize_output_path(tmp_path / ".agents") == tmp_path / ".codex" / "agents"
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "orchestrator.md").write_text("# old\n", encoding="utf-8")
    out = tmp_path / ".codex" / "agents"
    _, notices = CodexAdapter().guard_rendered_files([("a.toml", "x")], out)
    assert any("pre-2026-09-29" in n for n in notices)


def test_toml_key_scan_is_opt_in() -> None:
    from agentteams.framework_research import _scan_tokens_for

    text = "set `model` or model = x"
    assert _scan_tokens_for(text, ["model"], [])["front_matter_keys_present"] == []
    assert _scan_tokens_for(text, ["model"], [], toml_keys=True)["front_matter_keys_present"] == ["model"]


_BASEAGENT_AGENTS = Path(__file__).resolve().parents[2] / "baseAgent" / ".github" / "agents"


@pytest.mark.skipif(not _BASEAGENT_AGENTS.is_dir(), reason="adjacent baseAgent checkout not present")
def test_acceptance_against_baseagent_copy(tmp_path: Path) -> None:
    """Acceptance (cross-orchestrator request): a copy of baseAgent's canonical team yields one
    TOML per canonical agent, bespoke included, and writes nothing outside .codex/agents."""
    import shutil

    root = tmp_path / "ba"
    shutil.copytree(_BASEAGENT_AGENTS, root / ".github" / "agents")
    for name in ("AGENTS.md", "AGENTS.override.md"):
        src = _BASEAGENT_AGENTS.parents[1] / name
        if src.is_file():
            shutil.copy2(src, root / name)
    instr = _BASEAGENT_AGENTS.parent / "copilot-instructions.md"
    if instr.is_file():
        shutil.copy2(instr, root / ".github" / "copilot-instructions.md")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    expected = sorted(p.name[: -len(".agent.md")] for p in (root / ".github" / "agents").glob("*.agent.md"))

    result = run_interop(root / ".github" / "agents", "codex", root / ".codex" / "agents", overwrite=True)
    assert result.success
    tomls = sorted((root / ".codex" / "agents").glob("*.toml"))
    assert [p.stem for p in tomls] == expected
    for path, data in before.items():
        assert path.read_bytes() == data, f"{path} was modified"
    new = {p for p in root.rglob("*") if p.is_file()} - set(before)
    if (root / "AGENTS.md") in before:
        assert new == set(tomls)
    for p in tomls:
        d = tomllib.loads(p.read_text(encoding="utf-8"))
        assert set(d) <= _ALLOWED_KEYS
        assert h1_count(d["developer_instructions"]) == 1


def test_codex_output_is_module_owned_for_the_scanner() -> None:
    from agentteams.scan import _is_module_owned_path

    assert _is_module_owned_path(".codex/agents/x.toml")
    assert _is_module_owned_path("proj/.codex/agents/references/r.md")


# ---------------------------------------------------------------------------
# X7: Codex skills (.agents/skills/<name>/SKILL.md)
# ---------------------------------------------------------------------------

def test_codex_skill_placement_hooks() -> None:
    adapter = CodexAdapter()
    assert adapter.has_skill_concept()
    assert adapter.skill_output_rel_path("tool-pg") == "../../.agents/skills/tool-pg/SKILL.md"
    assert adapter.skills_dir(Path("/p/.codex/agents")) == Path("/p/.agents/skills")


def test_codex_skill_front_matter() -> None:
    out = CodexAdapter().render_skill_file("# PG\n\nUse it.\n", "tool-pg", {"project_name": "P"})
    assert out.startswith("---\nname: tool-pg\ndescription: ")
    assert "# PG" in out


def test_output_plan_places_tool_docs_as_codex_skills() -> None:
    from agentteams.output_plan import _skill_adapter

    assert _skill_adapter("codex") is not None
    assert _skill_adapter("claude").skill_output_rel_path("tool-x") == "../skills/tool-x/SKILL.md"
    assert _skill_adapter("copilot-vscode") is None


def test_claude_skills_carry_into_codex_via_interop(tmp_path: Path) -> None:
    claude_agents = tmp_path / "src" / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "orchestrator.md").write_text(
        "---\nname: Orchestrator\ndescription: \"d\"\ntools: Read, Grep\n---\n\n# Orchestrator\n\nB\n",
        encoding="utf-8",
    )
    skill = tmp_path / "src" / ".claude" / "skills" / "recall"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: recall\ndescription: \"Recall\"\n---\n\nQuery the index.\n",
                                    encoding="utf-8")
    out_root = tmp_path / "dst"
    run_interop(claude_agents, "codex", out_root / ".codex" / "agents")
    emitted = out_root / ".agents" / "skills" / "recall" / "SKILL.md"
    assert emitted.is_file()
    text = emitted.read_text(encoding="utf-8")
    assert text.startswith("---\nname: recall\n") and "Query the index." in text


def test_generic_bridge_points_at_the_real_memory_index(tmp_path: Path) -> None:
    """Item 9: a .github/agents team keeps its index under the agents dir, not the root."""
    from agentteams.bridge_pair_docs import _render_entrypoint, _render_quickstart, memory_index_rel_path

    src = tmp_path / ".github" / "agents"
    (src / "references").mkdir(parents=True)
    (src / "references" / "memory-index.json").write_text("{}", encoding="utf-8")
    rel = memory_index_rel_path(src, tmp_path)
    assert rel == ".github/agents/references/memory-index.json"
    assert rel in _render_entrypoint("copilot-vscode", "generic", rel)
    assert rel in _render_quickstart("copilot-vscode", "generic", rel)
