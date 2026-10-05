"""Per-section shrink overrides (--shrink-allow) and Codex native-body fencing / legacy retrofit.

Origin (2026-10-04 fleet update):
- ``--shrink-policy preserve`` pinned 20 researchteam fences (49 in musicmaker) because a
  template-RETIRED reference counts as "lost"; an operator now releases one named section at a
  time after reviewing it.
- A natively rendered Codex TOML carried its whole template body outside every fence, so
  ``--update --merge`` never refreshed it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agentteams import emit, shrink_allow
from agentteams.fences import _merge_fenced_content
from agentteams.frameworks import codex
from agentteams.frameworks.codex import CodexAdapter, _fence_native_body, retrofit_legacy_toml


# ---------------------------------------------------------------------------
# shrink_allow parsing
# ---------------------------------------------------------------------------


D = "0123456789ab"


def test_parse_entries_normalizes_and_rejects(monkeypatch):
    assert shrink_allow.parse_entries([f"./g.agent.md:content@{D}", " ", f"a/b.md:x_1@{D}"]) == {
        f"g.agent.md:content@{D}", f"a/b.md:x_1@{D}"}
    for bad in ("no-colon", "g.md:content", f"/abs.md:content@{D}", f"../up.md:content@{D}",
                f"f.md:Bad-Id@{D}", f":content@{D}", "g.md:content@xyz"):
        with pytest.raises(shrink_allow.ShrinkAllowError):
            shrink_allow.parse_entries([bad])
    monkeypatch.setenv(shrink_allow.SHRINK_ALLOW_ENV, f"x.md:content@{D};y.md:protocols@{D}")
    assert shrink_allow.resolve([f"z.md:content@{D}"]) == {
        f"x.md:content@{D}", f"y.md:protocols@{D}", f"z.md:content@{D}"}


# ---------------------------------------------------------------------------
# merge: preserve vs override
# ---------------------------------------------------------------------------

_DISK = ("<!-- AGENTTEAMS:BEGIN content v=1 -->\n# Agent\n\n- `references/retired-doc.md`\n"
         "- `references/live.md`\n<!-- AGENTTEAMS:END content -->\n\n## Project-Specific Notes\n\n"
         "my note\n")
_NEW = ("<!-- AGENTTEAMS:BEGIN content v=1 -->\n# Agent\n\n- `references/live.md`\n"
        "- `references/new.md`\n<!-- AGENTTEAMS:END content -->\n")


def test_preserve_pins_a_shrinking_section_by_default():
    mr = _merge_fenced_content(_NEW, _DISK, preserve_on_shrink=True, rel_path="agent.agent.md")
    assert "retired-doc.md" in mr.merged_content and "new.md" not in mr.merged_content
    assert mr.sections_preserved == ["content"] and not mr.shrink_overridden


def _entry(text: str = _DISK, path: str = "agent.agent.md") -> str:
    from agentteams.fences import _extract_fenced_regions

    return shrink_allow.key(path, "content", _extract_fenced_regions(text)["content"])


def test_override_applies_the_update_keeps_notes_and_records_the_lost_body():
    mr = _merge_fenced_content(_NEW, _DISK, preserve_on_shrink=True, rel_path="agent.agent.md",
                               shrink_allow=frozenset({_entry()}))
    assert "new.md" in mr.merged_content and "retired-doc.md" not in mr.merged_content
    assert "my note" in mr.merged_content                      # outside-fence text survives
    assert mr.shrink_overridden == ["content"]
    assert "retired-doc.md" in mr.lost_fence_bodies["content"]  # .lost sidecar material
    assert any("operator override" in n for n in mr.shrink_notices)


def test_override_for_another_file_fence_or_body_changes_nothing():
    good = _entry()
    stale_digest = good[:-12] + "000000000000"
    for allow in ({good.replace("agent.agent.md", "other.agent.md")},
                  {good.replace(":content@", ":protocols@")}, {stale_digest}):
        mr = _merge_fenced_content(_NEW, _DISK, preserve_on_shrink=True,
                                   rel_path="agent.agent.md", shrink_allow=frozenset(allow))
        assert "retired-doc.md" in mr.merged_content and not mr.shrink_overridden


def test_emit_all_applies_override_warns_unused_and_writes_sidecar(tmp_path, capsys):
    out, backup = tmp_path / "team", tmp_path / "bk"
    out.mkdir()
    (out / "agent.agent.md").write_text(_DISK, encoding="utf-8")
    typo = f"typo.agent.md:content@{D}"
    emit.emit_all([("agent.agent.md", _NEW)], output_dir=out, merge=True, yes=True,
                  backup_path=backup, shrink_allow=frozenset({_entry(), typo}))
    err = capsys.readouterr().err
    assert f"{typo}: released nothing" in err and f"{_entry()}: released nothing" not in err
    assert "shrink override active" in err
    assert "new.md" in (out / "agent.agent.md").read_text(encoding="utf-8")
    assert any("retired-doc.md" in p.read_text() for p in backup.rglob("*.lost.*"))


def test_override_refused_without_a_backup_dir(tmp_path, capsys):
    (tmp_path / "agent.agent.md").write_text(_DISK, encoding="utf-8")
    emit.emit_all([("agent.agent.md", _NEW)], output_dir=tmp_path, merge=True, yes=True,
                  backup_path=None, shrink_allow=frozenset({_entry()}))
    assert "refused" in capsys.readouterr().err
    assert "retired-doc.md" in (tmp_path / "agent.agent.md").read_text(encoding="utf-8")


def test_review_report_lists_pinned_sections_with_provenance(tmp_path, monkeypatch):
    out = tmp_path / "team"
    out.mkdir()
    (out / "agent.agent.md").write_text(_DISK, encoding="utf-8")
    report = tmp_path / "review" / "shrink.json"
    monkeypatch.setenv(shrink_allow.SHRINK_REPORT_ENV, str(report))
    emit.emit_all([("agent.agent.md", _NEW)], output_dir=out, merge=True, yes=True,
                  dry_run=True)
    import json

    items = json.loads(report.read_text())
    assert items[0]["entry"] == _entry() and "references/retired-doc.md" in items[0]["lost"]
    assert items[0]["provenance"]["references/retired-doc.md"] in {"never", "unknown"}
    assert report.with_suffix(".md").exists()


def test_token_provenance_uses_template_history():
    checkout = shrink_allow._agentteams_checkout()
    if checkout is None:
        pytest.skip("not running from a full-history agentteams git checkout (CI is shallow)")
    # Retired from git-operations.template.md on 2026-06-20 (703b1e2).
    assert shrink_allow.token_provenance("references/git-procedures.md", checkout) == "retired"
    assert shrink_allow.token_provenance("references/github-workflows-merge.reference.md",
                                         checkout) == "current"
    assert shrink_allow.token_provenance("zz-never-in-any-template-zz", checkout) == "never"


# ---------------------------------------------------------------------------
# Codex: native body fenced; projected body untouched; legacy retrofit
# ---------------------------------------------------------------------------

_CANON = ("---\nname: Git Operations — X\ndescription: d\ntools: ['read', 'execute']\n---\n"
          "# Git Operations — X\n\nUse `references/github-workflows-merge.reference.md`.\n")


def _instructions(toml_text: str) -> str:
    return tomllib.loads(toml_text)["developer_instructions"]


def test_native_render_fences_the_body_and_adds_notes():
    di = _instructions(CodexAdapter().render_agent_file(_CANON, "git-operations", {}))
    assert di.count("AGENTTEAMS:BEGIN content") == 1
    assert di.index("AGENTTEAMS:END content") < di.index("## Project-Specific Notes") < di.index(
        "AGENTTEAMS:BEGIN codex_translation")
    assert codex.h1_count(di) == 1


def test_projected_fenced_body_is_unchanged():
    body = "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\nx\n<!-- AGENTTEAMS:END invariant_core -->"
    assert _fence_native_body(body) == body


def test_retrofit_wraps_only_the_body():
    legacy = CodexAdapter().render_agent_file(_CANON, "git-operations", {})
    # Simulate a pre-fix file: strip the content fence and notes the fixed renderer now adds.
    old = legacy.replace("<!-- AGENTTEAMS:BEGIN content v=1 -->\n", "").replace(
        "<!-- AGENTTEAMS:END content -->\n", "")
    old = old[:old.index("\n## Project-Specific Notes")] + "\n\n" + old[old.index(
        "<!-- AGENTTEAMS:BEGIN codex_translation"):]
    out = retrofit_legacy_toml(old)
    assert out is not None and tomllib.loads(out)["name"] == "git-operations"
    di = _instructions(out)
    assert di.startswith("<!-- AGENTTEAMS:BEGIN content v=1 -->\n# Git Operations")
    assert di.count("AGENTTEAMS:BEGIN codex_translation") == 1
    assert retrofit_legacy_toml(out) is None          # idempotent: already fenced
    assert retrofit_legacy_toml('name = "x"\n') is None


def test_merge_refreshes_a_legacy_native_toml(tmp_path):
    """End to end through emit_all: an unfenced legacy body is retrofitted, then replaced."""
    adapter = CodexAdapter()
    stale_canon = _CANON.replace("Use `references/github-workflows-merge", "Use `references/git-procedures.md` and `references/github-workflows-merge")
    stale = adapter.render_agent_file(stale_canon, "git-operations", {})
    stale = stale.replace("<!-- AGENTTEAMS:BEGIN content v=1 -->\n", "").replace(
        "<!-- AGENTTEAMS:END content -->\n", "")
    stale = stale[:stale.index("\n## Project-Specific Notes")] + "\n\n" + stale[stale.index(
        "<!-- AGENTTEAMS:BEGIN codex_translation"):]
    target = tmp_path / "git-operations.toml"
    target.write_text(stale, encoding="utf-8")
    fresh = adapter.render_agent_file(_CANON, "git-operations", {})
    from agentteams.fences import _extract_fenced_regions

    retro = retrofit_legacy_toml(stale)
    entry = shrink_allow.key("git-operations.toml", "content",
                             _extract_fenced_regions(retro)["content"])
    result = emit.emit_all([("git-operations.toml", fresh)], output_dir=tmp_path, merge=True,
                           yes=True, auto_fence_legacy=True, backup_path=tmp_path / "bk",
                           shrink_allow=frozenset({entry}))
    assert result.fence_injected
    di = _instructions(target.read_text(encoding="utf-8"))
    assert "git-procedures.md" not in di and di.count("AGENTTEAMS:BEGIN content") == 1
    assert di.count("AGENTTEAMS:BEGIN codex_translation") == 1


def test_merge_keeps_hand_written_notes_in_a_fenced_toml(tmp_path):
    adapter = CodexAdapter()
    current = adapter.render_agent_file(_CANON, "git-operations", {})
    with_notes = current.replace("## Project-Specific Notes\n", "## Project-Specific Notes\n\n### Mine\nkeep me\n", 1)
    target = tmp_path / "git-operations.toml"
    target.write_text(with_notes, encoding="utf-8")
    newer = adapter.render_agent_file(_CANON.replace("Use `", "Always use `"), "git-operations", {})
    emit.emit_all([("git-operations.toml", newer)], output_dir=tmp_path, merge=True, yes=True)
    di = _instructions(target.read_text(encoding="utf-8"))
    assert "Always use" in di and "keep me" in di


def test_retrofit_keeps_hand_written_notes_outside_the_fence(tmp_path):
    """Adversarial B1: a legacy TOML with notes must not have them swallowed by the fence."""
    legacy = CodexAdapter().render_agent_file(_CANON, "git-operations", {})
    legacy = legacy.replace("<!-- AGENTTEAMS:BEGIN content v=1 -->\n", "").replace(
        "<!-- AGENTTEAMS:END content -->\n", "").replace(
        "## Project-Specific Notes\n", "## Project-Specific Notes\n\n### Ours\nkeep this\n", 1)
    out = retrofit_legacy_toml(legacy)
    di = _instructions(out)
    assert di.index("AGENTTEAMS:END content") < di.index("## Project-Specific Notes")
    assert "keep this" in di.split("AGENTTEAMS:END content")[1]
    target = tmp_path / "git-operations.toml"
    target.write_text(legacy, encoding="utf-8")
    newer = CodexAdapter().render_agent_file(_CANON.replace("Use `", "Always use `"),
                                             "git-operations", {})
    emit.emit_all([("git-operations.toml", newer)], output_dir=tmp_path, merge=True, yes=True,
                  auto_fence_legacy=True, backup_path=tmp_path / "bk")
    di = _instructions(target.read_text(encoding="utf-8"))
    assert "Always use" in di and "keep this" in di
