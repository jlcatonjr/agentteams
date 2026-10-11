"""An operator's acknowledgement of a kept pre-fencing twin silences REVIEW, and does nothing else.

``agentteams.fence_twins`` keeps every twin it cannot prove is template text and ``fleet`` reports it
as REVIEW on every run. ``agentteams.fence_twin_acks`` lets an operator record "this one stays", bound
to the twin's exact text. These tests pin the four properties that make that safe:

* an acknowledged twin produces no REVIEW, but still prints a notice;
* any change to the twin's text (or its file, or its heading) brings REVIEW back;
* an acknowledgement never changes what the merge writes (there is no drop path through it);
* a reason under 40 characters is rejected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentteams import emit, fence_twin_acks, fence_twins, fleet
from agentteams.cli.render_pipeline import twin_sources
from agentteams.fence_twin_acks import TwinAck
from agentteams.fence_twins import TwinSource
from agentteams.fences import _merge_fenced_content

TEMPLATE = (
    "# X — {PROJECT_NAME}\n\n"
    "<!-- AGENTTEAMS:BEGIN rules v=1 -->\n## Rules\n\n- New rule A for {PROJECT_NAME}.\n"
    "<!-- AGENTTEAMS:END rules -->\n"
)
PLACEHOLDERS = {"PROJECT_NAME": "Acme"}
NOTES = "## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — project rules.\n"
RENDER = fence_twins.resolve_placeholders(TEMPLATE, PLACEHOLDERS) + "\n" + NOTES
#: Hand-written rows no template ever emitted: the twin is kept, and the operator wants it kept.
HAND_TWIN = "## Rules\n\n- CH-08 Every claim needs a cited source in references/.\n\n"
OLD_TEMPLATE_TWIN = "## Rules\n\n- Old rule for Acme: you must never skip the review step here.\n\n"
REL = "x.agent.md"
REASON = "CH-08 is a project rule the template does not carry; kept until it moves to notes."


@pytest.fixture(autouse=True)
def _synthetic_lineage(tmp_path, monkeypatch):
    """A one-template library whose corpus knows OLD_TEMPLATE_TWIN (and nothing hand-written)."""
    tdir = tmp_path / "templates"
    (tdir / "domain").mkdir(parents=True)
    (tdir / "domain" / "x.template.md").write_text(TEMPLATE, encoding="utf-8")
    corpus = {"domain/x.template.md": {"## Rules": [
        {"commit": "abc1234def", "path": "templates/domain/x.template.md",
         "text": OLD_TEMPLATE_TWIN.replace("Acme", "{PROJECT_NAME}")}]}}
    monkeypatch.setattr(fence_twins, "_TEMPLATES_DIR", tdir)
    monkeypatch.setattr(fence_twins, "load_corpus", lambda *a, **k: corpus)


def _deployed(twin: str = HAND_TWIN) -> str:
    return fence_twins.resolve_placeholders(TEMPLATE, PLACEHOLDERS) + "\n" + twin + NOTES


def _digest(twin: str = HAND_TWIN, rel: str = REL) -> str:
    return fence_twins.ack_digest(rel, "## Rules", twin)


def _source(*acks: TwinAck, rel: str = REL) -> TwinSource:
    return TwinSource("domain/x.template.md", PLACEHOLDERS, rel_path=rel, acks=acks)


def _ack(digest: str | None = None, *, heading: str = "## Rules", reason: str = REASON) -> TwinAck:
    return TwinAck(REL, heading, digest or _digest(), reason)


def _merge(existing: str, source: TwinSource, drop: bool = True):
    return _merge_fenced_content(RENDER, existing, rel_path=REL, twin_source=source, twin_drop=drop)


def _notices(res) -> str:
    return " ".join(res.duplicate_section_notices)


# --- an acknowledged twin is a notice, not a REVIEW ---------------------------------------------

def test_an_acknowledged_twin_produces_no_review_but_still_a_notice():
    res = _merge(_deployed(), _source(_ack()))
    notices = _notices(res)
    assert fence_twins.KEPT_PREFIX not in notices
    assert fence_twins.ACKED_PREFIX in notices and REASON in notices and _digest() in notices
    assert "CH-08" in res.merged_content, "an acknowledged twin is kept"
    assert res.twins_kept == ["## Rules"] and not res.twins_dropped


def test_fleet_does_not_count_an_acknowledged_twin(tmp_path):
    res = _merge(_deployed(), _source(_ack()))
    out = "\n".join(f"  Notice: {REL}: {n}" for n in res.duplicate_section_notices)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    cls = fleet._classify(tmp_path, "HEAD", "", out)
    assert cls["twins_kept"] == [] and cls["shrink_notices"] == [] and cls["twins_retired"] == []
    # Not silenced invisibly: fleet lists it, reason included, in its report row.
    assert len(cls["twins_acknowledged"]) == 1 and REASON in cls["twins_acknowledged"][0]
    assert "twins_acknowledged" in fleet.TargetResult.__dataclass_fields__


def test_fleet_still_counts_an_unacknowledged_twin(tmp_path):
    res = _merge(_deployed(), _source())
    out = "\n".join(f"  Notice: {REL}: {n}" for n in res.duplicate_section_notices)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert len(fleet._classify(tmp_path, "HEAD", "", out)["twins_kept"]) == 1


def test_the_kept_notice_prints_the_row_to_record():
    notices = _notices(_merge(_deployed(), _source()))
    assert fence_twin_acks.LEDGER_REL in notices
    assert f"{REL},## Rules,{_digest()}," in notices


# --- the acknowledgement binds to the exact twin -----------------------------------------------

@pytest.mark.parametrize("changed", [
    HAND_TWIN.replace("cited source", "cited sources"),               # one-word edit
    HAND_TWIN + "- CH-09 A second hand rule added after the review.\n\n",  # a row added later
])
def test_a_changed_twin_brings_review_back(changed):
    res = _merge(_deployed(changed), _source(_ack()))
    notices = _notices(res)
    assert fence_twins.KEPT_PREFIX in notices and fence_twins.ACKED_PREFIX not in notices
    assert "no longer matches" in notices and _digest() in notices


def test_whitespace_alone_does_not_invalidate_an_acknowledgement():
    res = _merge(_deployed(HAND_TWIN.replace("needs a", "needs   a")), _source(_ack()))
    assert fence_twins.ACKED_PREFIX in _notices(res)


def test_an_acknowledgement_for_another_file_or_heading_does_not_apply():
    other_file = TwinAck(REL, "## Rules", _digest(rel="y.agent.md"), REASON)
    other_heading = _ack(heading="## Other")
    for ack in (other_file, other_heading):
        assert fence_twins.KEPT_PREFIX in _notices(_merge(_deployed(), _source(ack)))


# --- there is no drop path ---------------------------------------------------------------------

@pytest.mark.parametrize("twin, provable", [(HAND_TWIN, False), (OLD_TEMPLATE_TWIN, True)])
@pytest.mark.parametrize("drop", [True, False])
def test_an_acknowledgement_never_changes_what_is_written(twin, provable, drop):
    """Same content, same drops, with or without the row — for kept and provable twins alike."""
    deployed = _deployed(twin)
    without = _merge(deployed, _source(), drop=drop)
    with_ack = _merge(deployed, _source(_ack(_digest(twin))), drop=drop)
    assert with_ack.merged_content == without.merged_content
    assert with_ack.twins_dropped == without.twins_dropped
    assert bool(without.twins_dropped) is (provable and drop)


def test_an_acknowledged_twin_survives_a_merge_through_emit(tmp_path):
    out = tmp_path / "agents"
    (out / "references").mkdir(parents=True)
    (out / REL).write_text(_deployed(), encoding="utf-8")
    (out / fence_twin_acks.LEDGER_REL).write_text(
        f"file,heading,digest,reason\n{REL},## Rules,{_digest()},{REASON}\n", encoding="utf-8")
    acks = fence_twin_acks.load(out)
    result = emit.emit_all([(REL, RENDER)], output_dir=out, merge=True, yes=True,
                           backup_path=tmp_path / "backup", twin_sources={REL: _source(*acks[REL])})
    assert (out / REL).read_text(encoding="utf-8") == _deployed()
    assert any(fence_twins.ACKED_PREFIX in n for n in result.notices)
    assert not any(fence_twins.KEPT_PREFIX in n for n in result.notices)


def test_twin_sources_reads_the_ledger_from_the_output_dir(tmp_path):
    class _Adapter:
        def finalize_output_path(self, path, _kind):
            return path

    manifest = {"auto_resolved_placeholders": {},
                "output_files": [{"path": "agents/x.agent.md", "template": "universal/orchestrator.template.md"}]}
    (tmp_path / "references").mkdir()
    (tmp_path / fence_twin_acks.LEDGER_REL).write_text(
        f"file,heading,digest,reason\nagents/x.agent.md,## Rules,{_digest()},{REASON}\n", encoding="utf-8")
    source = twin_sources(manifest, _Adapter(), tmp_path)["agents/x.agent.md"]
    assert source.rel_path == "agents/x.agent.md" and len(source.acks) == 1
    assert twin_sources(manifest, _Adapter())["agents/x.agent.md"].acks == ()


def test_a_row_for_a_file_the_run_does_not_render_is_reported(tmp_path, capsys):
    class _Adapter:
        def finalize_output_path(self, path, _kind):
            return path

    (tmp_path / "references").mkdir()
    (tmp_path / fence_twin_acks.LEDGER_REL).write_text(
        f"file,heading,digest,reason\nagents/gone.agent.md,## Rules,{_digest()},{REASON}\n", encoding="utf-8")
    twin_sources({"auto_resolved_placeholders": {}, "output_files": []}, _Adapter(), tmp_path)
    assert "agents/gone.agent.md" in capsys.readouterr().err


# --- the ledger --------------------------------------------------------------------------------

def _parse(rows: str, header: str = "file,heading,digest,reason") -> dict:
    return fence_twin_acks.parse(f"{header}\n{rows}")


def test_a_short_reason_is_rejected(capsys):
    short = "x" * (fence_twin_acks.MIN_REASON_CHARS - 1)
    assert _parse(f"{REL},## Rules,{_digest()},{short}\n") == {}
    assert "at least 40" in capsys.readouterr().err
    assert _parse(f"{REL},## Rules,{_digest()},{'x' * 40}\n")[REL][0].reason == "x" * 40


def test_a_reason_padded_with_whitespace_is_still_short(capsys):
    assert _parse(f'{REL},## Rules,{_digest()},"too short{" " * 60}really"\n') == {}


def test_a_short_reason_leaves_the_twin_under_review(tmp_path):
    (tmp_path / "references").mkdir()
    (tmp_path / fence_twin_acks.LEDGER_REL).write_text(
        f"file,heading,digest,reason\n{REL},## Rules,{_digest()},too short to count\n", encoding="utf-8")
    acks = fence_twin_acks.load(tmp_path).get(REL, ())
    assert fence_twins.KEPT_PREFIX in _notices(_merge(_deployed(), _source(*acks)))


@pytest.mark.parametrize("row", [
    f"{REL},## Rules,{'A' * 16},{REASON}",                 # uppercase digest
    f"{REL},## Rules,{'a' * 12},{REASON}",                 # shrink-allow length, not ours
    f"/abs/{REL},## Rules,{'a' * 16},{REASON}",            # absolute path
    f"{REL},Rules,{'a' * 16},{REASON}",                    # not a heading line
    f"{REL},# Title,{'a' * 16},{REASON}",                  # level-1 headings are never twins
])
def test_malformed_rows_are_skipped(row, capsys):
    assert _parse(row + "\n") == {}
    assert "skipped" in capsys.readouterr().err


def test_a_wrong_header_applies_nothing(capsys):
    assert _parse(f"{REL},## Rules,{_digest()},{REASON}\n", header="file,heading,digest") == {}
    assert "header must be" in capsys.readouterr().err


def test_no_ledger_and_no_output_dir_read_nothing(tmp_path):
    assert fence_twin_acks.load(None) == {} and fence_twin_acks.load(tmp_path) == {}


def test_the_ledger_row_round_trips_a_heading_with_a_comma():
    row = fence_twin_acks.ledger_row(REL, "## Rules, extended", "a" * 16)
    parsed = _parse(row.replace(f"<why this twin stays: at least 40 characters>", REASON) + "\n")
    assert parsed[REL][0].heading == "## Rules, extended"


def test_the_reason_bar_matches_the_other_ledgers():
    from agentteams.redteam.registry import MIN_REASON_CHARS

    assert fence_twin_acks.MIN_REASON_CHARS == MIN_REASON_CHARS == 40


def test_the_acknowledgement_module_is_integrity_pinned():
    from agentteams import integrity

    assert "agentteams/fence_twin_acks.py" in integrity.ENFORCEMENT_MODULES
    assert "agentteams/fence_twins.py" in integrity.ENFORCEMENT_MODULES


def test_the_acknowledgement_module_cannot_write():
    """It reads a ledger and returns rows; nothing in it opens a file for writing or deletes one."""
    src = (Path(fence_twin_acks.__file__)).read_text(encoding="utf-8")
    for token in ("write_text", "write_bytes", "unlink", "rmtree", "open(", ".rename(", ".replace("):
        assert token not in src, token
