"""--sync-agent-docs and the regeneration carry on the Codex surface (.codex/agents/*.toml).

A Codex custom agent holds its body in a literal ``developer_instructions = '''...'''`` string. The learned
block lives inside it, before the ``codex_translation`` fence (where ``frameworks.codex`` already expects user
text). Every write must leave the TOML parseable with every other key unchanged, and a block may never contain
``'''`` (it would close the string and let text become TOML keys).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from agentteams import agent_doc_sync as ads
from agentteams import learned_blocks as lb

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX filesystem semantics")

TQ = "'" * 3
GITHUB = ("---\nname: alpha\ndescription: Alpha agent\ntools: ['read', 'search']\n---\n"
          "<!-- AGENTTEAMS:BEGIN content v=1 -->\n# Alpha\n\nDo alpha things.\n<!-- AGENTTEAMS:END content -->\n")
CODEX = (
    'name = "alpha"\ndescription = "Alpha agent"\nsandbox_mode = "read-only"\n'
    f"developer_instructions = {TQ}\n"
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\n# Alpha\n\nDo alpha things.\n<!-- AGENTTEAMS:END content -->\n\n"
    "## Project-Specific Notes\n\n> banner\n\n"
    "<!-- AGENTTEAMS:BEGIN codex_translation v=1 -->\nDeclared tools: read, search\n"
    "<!-- AGENTTEAMS:END codex_translation -->\n"
    f"{TQ}\n"
)
NOTES = "- Run tests with `pytest -p no:cacheprovider`.\n"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    proj = tmp_path / "proj"
    for d in (".github/agents", ".codex/agents"):
        (proj / d).mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    paths = {"proj": proj, "github": proj / ".github/agents/alpha.agent.md",
             "codex": proj / ".codex/agents/alpha.toml"}
    paths["github"].write_text(GITHUB)
    paths["codex"].write_text(CODEX)
    return paths


def _content(path: Path, kind: str) -> str | None:
    parsed = lb.parse(path.read_text(), kind)
    return parsed.block.content if parsed.block else None


def _keys(text: str) -> dict:
    data = tomllib.loads(text)
    data.pop("developer_instructions")
    return data


def test_github_to_codex_inserts_before_the_translation_fence(env):
    env["github"].write_text(GITHUB + "\n" + lb.render_block(NOTES, ""))
    rep = ads.sync_agent_docs(env["proj"], apply=True)
    assert rep.exit_code == 0, rep.lines
    new = env["codex"].read_text()
    assert _content(env["codex"], lb.TOML) == NOTES
    assert _keys(new) == _keys(CODEX)
    instructions = tomllib.loads(new)["developer_instructions"]
    assert instructions.index(lb.BEGIN_MARKER) < instructions.index("AGENTTEAMS:BEGIN codex_translation")


def test_codex_to_github_propagates(env):
    env["github"].write_text(GITHUB + "\n" + lb.render_block(NOTES, ""))
    ads.sync_agent_docs(env["proj"], apply=True)
    updated = NOTES + "- Keep the ledger key out of the session.\n"
    parsed = lb.parse(env["codex"].read_text(), lb.TOML)
    env["codex"].write_text(lb.compose(env["codex"].read_text(), parsed, updated))
    rep = ads.sync_agent_docs(env["proj"], apply=True)
    assert rep.exit_code == 0, rep.lines
    assert _content(env["github"], lb.MARKDOWN) == updated


def test_block_that_would_close_the_string_is_refused(env):
    evil = NOTES + f"{TQ}\nmodel = \"other\"\nx = {TQ}\n"
    env["github"].write_text(GITHUB + "\n" + lb.render_block(evil, ""))
    rep = ads.sync_agent_docs(env["proj"], apply=True)
    assert env["codex"].read_text() == CODEX
    assert tomllib.loads(env["codex"].read_text())["sandbox_mode"] == "read-only"
    assert rep.exit_code != 0 and any("TOML string delimiter" in line for line in rep.lines)


def test_escaped_fallback_form_is_refused():
    escaped = CODEX.replace(f"developer_instructions = {TQ}", 'developer_instructions = """').replace(
        f"\n{TQ}\n", '\n"""\n')
    with pytest.raises(lb.LearnedBlockError, match="literal"):
        lb.parse(escaped, lb.TOML)


def test_verify_rejects_a_changed_key():
    parsed = lb.parse(CODEX, lb.TOML)
    composed = lb.compose(CODEX, parsed, NOTES).replace('sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"')
    assert any("other than developer_instructions" in p for p in lb.verify_composed(CODEX, composed, lb.TOML, NOTES))


def test_regeneration_carries_the_codex_block(tmp_path):
    target = tmp_path / ".codex" / "agents" / "alpha.toml"
    target.parent.mkdir(parents=True)
    on_disk = lb.compose(CODEX, lb.parse(CODEX, lb.TOML), NOTES)
    target.write_text(on_disk)
    fresh, notices = lb.carry_learned_block("alpha.toml", CODEX.replace("Do alpha things.", "Do alpha v2."), target)
    assert not notices, notices
    assert lb.parse(fresh, lb.TOML).block.content == NOTES and "Do alpha v2." in fresh
    tomllib.loads(fresh)



def test_escaped_fallback_agent_is_skipped_not_refused_on_every_run(env):
    escaped = CODEX.replace("developer_instructions = '''", 'developer_instructions = """').replace(
        "\n'''\n", '\n"""\n')
    env["codex"].write_text(escaped)
    env["github"].write_text(GITHUB + "\n" + lb.render_block(NOTES, ""))
    for _ in range(2):
        rep = ads.sync_agent_docs(env["proj"], apply=True)
        assert rep.exit_code == 0, rep.lines
        assert any("SKIPPED .codex/agents/alpha.toml" in line for line in rep.lines)
    assert env["codex"].read_text() == escaped


def test_codex_overwrite_keeps_both_notes_and_learned_block(tmp_path):
    from agentteams import emit

    out = tmp_path / ".codex" / "agents"
    out.mkdir(parents=True)
    with_block = lb.compose(CODEX, lb.parse(CODEX, lb.TOML), NOTES)
    on_disk = with_block.replace("> banner\n", "> banner\n- duty: keep me\n")
    (out / "alpha.toml").write_text(on_disk)
    fresh = CODEX.replace("Do alpha things.", "Do alpha v2.")
    result = emit.emit_all([("alpha.toml", fresh)], output_dir=out, overwrite=True, yes=True)
    text = (out / "alpha.toml").read_text()
    assert not result.errors, result.errors
    assert "Do alpha v2." in text and "- duty: keep me" in text
    assert lb.parse(text, lb.TOML).block.content == NOTES
    tomllib.loads(text)
