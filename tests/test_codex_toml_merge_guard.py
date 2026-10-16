"""``--update --merge`` never writes a Codex agent TOML that does not parse (2026-10-16).

**The defect.** A Codex agent whose body was one legacy ``content`` fence (or unfenced, the pre-2026-10-04
adapter format, which the merge wraps in a ``content`` fence in the same run) got a whole-body migration once its
template gained named sections. ``whole_body_migration._body_start`` knew YAML front matter and Goose's
``instructions: |`` but not Codex's ``developer_instructions = '''``, so it returned 0 and the render's whole TOML
header (comments, ``name``, ``description``, a second ``developer_instructions = '''``) was pasted inside the
string. The merge exited 0 and Codex could no longer load the agent.

**The fix.** (a) ``fence_twins._TOML_BODY_RE`` marks the TOML body opener, and ``_body_start`` honours it.
(b) ``emit_all`` parses every merged ``.toml`` before writing it (``codex.toml_write_error``): one that does not
parse is refused with an error naming the file, the on-disk file is left as it was (a retrofit this run made is
undone), and the run fails.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agentteams import emit
from agentteams.fence_twins import _TOML_BODY_RE
from agentteams.frameworks.codex import CodexAdapter, toml_write_error
from agentteams.whole_body_migration import _body_start

_FM = "---\nname: Quality Auditor — X\ndescription: Read-only audit agent\ntools: ['read', 'search']\n---\n"
_INTRO = "# Quality Auditor — X\n\nYou perform read-only quality audits.\n\n"
_SECTIONS = {
    "invariant_core": "## Invariant Core\n\n> Do not modify or omit.\n",
    "output_format": "## Output Format\n\nReturn a ranked findings list.\n",
}
_USER_NOTE = "- Acme audits run on Fridays (hand-written, must survive).\n"


def _fenced_canon() -> str:
    """Today's template: the body split into named fences."""
    body = "".join(f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{text}<!-- AGENTTEAMS:END {sid} -->\n\n"
                   for sid, text in _SECTIONS.items())
    return _FM + _INTRO + body


def _render() -> str:
    return CodexAdapter().render_agent_file(_fenced_canon(), "quality-auditor", {})


def _legacy(collapsed: bool) -> str:
    """The deployed file: the same body with no named fences — one ``content`` fence, or none at all."""
    fresh = _render()
    head, rest = fresh.split("developer_instructions = '''\n", 1)
    tail = rest[rest.index("<!-- AGENTTEAMS:BEGIN codex_translation"):]
    body = _INTRO + "".join(_SECTIONS.values())
    if collapsed:
        body = f"<!-- AGENTTEAMS:BEGIN content v=1 -->\n{body.strip()}\n<!-- AGENTTEAMS:END content -->\n\n"
    notes = f"## Project-Specific Notes\n\n{_USER_NOTE}\n"
    # An older template's description: the header differs from the render's, as on every real upgrade.
    head = head.replace('description = "Read-only audit agent"', 'description = "Audit agent (older wording)"')
    return head + "developer_instructions = '''\n" + body + notes + tail


@pytest.mark.parametrize("collapsed", [True, False], ids=["content-fence", "unfenced-legacy"])
def test_whole_body_migration_of_a_codex_toml_parses_and_keeps_user_content(tmp_path: Path, collapsed: bool):
    target = tmp_path / "quality-auditor.toml"
    target.write_text(_legacy(collapsed), encoding="utf-8")
    result = emit.emit_all([("quality-auditor.toml", _render())], output_dir=tmp_path, merge=True, yes=True,
                           auto_fence_legacy=True, backup_path=tmp_path / "bk")
    assert result.errors == []
    text = target.read_text(encoding="utf-8")
    doc = tomllib.loads(text)                                  # the defect: "Invalid statement" here
    assert doc["name"] == "quality-auditor" and text.count("\nname = ") == 1
    di = doc["developer_instructions"]
    assert _USER_NOTE.strip() in di
    assert "AGENTTEAMS:BEGIN content" not in di                # migrated to the named sections
    for sid in _SECTIONS:
        assert di.count(f"AGENTTEAMS:BEGIN {sid} ") == 1
    assert di.count("## Invariant Core") == 1 and di.count("AGENTTEAMS:BEGIN codex_translation") == 1
    assert "developer_instructions" not in di and "# Codex custom agent" not in di


def test_body_start_finds_the_toml_body_opener():
    lines = _render().splitlines(keepends=True)
    start = _body_start(lines)
    assert lines[start - 1] == "developer_instructions = '''\n"
    assert _TOML_BODY_RE.match("developer_instructions = '''\n") and not _TOML_BODY_RE.match("name = 'x'\n")


def test_the_guard_names_a_broken_toml_and_ignores_other_files():
    broken = "name = \"x\"\ndeveloper_instructions = '''\nbody\n'''\nname = \"x\"\ndeveloper_instructions = '''\n"
    assert "not valid TOML" in toml_write_error("a.toml", broken)
    assert toml_write_error("a.toml", _render()) is None
    assert toml_write_error("a.agent.md", broken) is None


@pytest.mark.parametrize("collapsed", [True, False], ids=["content-fence", "unfenced-legacy"])
def test_a_merge_result_that_does_not_parse_is_refused_and_the_file_left_unchanged(
        tmp_path: Path, monkeypatch, collapsed: bool):
    from agentteams import fences

    target = tmp_path / "quality-auditor.toml"
    original = _legacy(collapsed)
    target.write_text(original, encoding="utf-8")
    real = fences._merge_fenced_content

    def broken_merge(*args, **kwargs):
        res = real(*args, **kwargs)
        res.merged_content = res.merged_content.replace("developer_instructions = '''\n",
                                                        "developer_instructions = '''\nname = 'x'\n'''\n", 1)
        return res

    monkeypatch.setattr(emit, "_merge_fenced_content", broken_merge)
    result = emit.emit_all([("quality-auditor.toml", _render())], output_dir=tmp_path, merge=True, yes=True,
                           auto_fence_legacy=True, backup_path=tmp_path / "bk")
    assert not result.success
    assert any("quality-auditor.toml" in e and "not valid TOML" in e for e in result.errors)
    assert str(target) not in result.merged
    assert target.read_text(encoding="utf-8") == original      # a retrofit this run made is undone too
