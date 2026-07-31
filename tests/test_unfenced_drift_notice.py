"""Merge must say when the template's front matter moved on without it.

**The demonstrated failure.** Agent-file YAML front matter lies outside every `AGENTTEAMS` fence,
so `--update --merge` preserves it verbatim. That is correct and load-bearing — it is how a
project keeps its own edits — but it is also silent. Measured 2026-07-30 against two downstream
repos: adding the `retrieval` token to `tool-doc-researcher` and `reference-manager` updated their
fenced body content and left the tool grant untouched, with nothing in the run output saying so.
Both files had to be hand-edited.

The remediation is therefore **detection, not different merge semantics**. Changing merge to
apply the template's front matter would overwrite user-owned values — the precise failure merge
mode exists to prevent. These tests pin both halves: the notice fires on the real case, and the
on-disk value is still what gets written.

Scope is narrow on purpose (see `_detect_front_matter_drift`): keys only, never values-as-diff,
and `name`/`description` are exempt because they interpolate the project name and would otherwise
fire on every file in every run. A notice that fires on everything gets muted.
"""

from __future__ import annotations

import pytest

from agentteams.fences import (
    _DRIFT_EXEMPT_FRONT_MATTER_KEYS,
    _detect_front_matter_drift,
    _front_matter_keys,
    _merge_fenced_content,
)

_FENCE = (
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\n{body}\n<!-- AGENTTEAMS:END content -->\n"
)


def _doc(tools: str, *, name: str = "Agent — Proj", body: str = "b", extra: str = "") -> str:
    return (
        f"---\nname: {name}\ndescription: \"d\"\ntools: {tools}\nmodel: [\"m\"]\n{extra}---\n\n"
        + _FENCE.format(body=body)
    )


# --- the demonstrated case -------------------------------------------------

def test_the_retrieval_grant_case_is_reported():
    """The exact failure from 2026-07-30, reproduced as a regression guard."""
    on_disk = _doc("['read', 'search']")
    rendered = _doc("['read', 'search', 'retrieval']", body="b2")

    result = _merge_fenced_content(rendered, on_disk)

    assert any("'tools'" in n for n in result.front_matter_drift), result.front_matter_drift
    assert any("retrieval" in n for n in result.front_matter_drift)


def test_the_on_disk_value_is_still_what_gets_written():
    """Detection must not become application — that would overwrite user-owned front matter."""
    on_disk = _doc("['read', 'search']")
    rendered = _doc("['read', 'search', 'retrieval']", body="b2")

    result = _merge_fenced_content(rendered, on_disk)

    assert "tools: ['read', 'search']\n" in result.merged_content
    assert "retrieval" not in result.merged_content.split("---")[1]
    assert "b2" in result.merged_content, "the fenced body must still update"


def test_a_key_added_by_the_template_is_reported():
    on_disk = _doc("['read']")
    rendered = _doc("['read']", extra="agents: ['x']\n")

    notices = _merge_fenced_content(rendered, on_disk).front_matter_drift

    assert any("adds 'agents'" in n for n in notices), notices


# --- no false positives ----------------------------------------------------

def test_identical_front_matter_reports_nothing():
    doc = _doc("['read', 'search']")
    assert _merge_fenced_content(doc, doc).front_matter_drift == []


def test_a_body_only_change_reports_nothing():
    """The overwhelmingly common case: template prose changed, front matter did not."""
    on_disk = _doc("['read']", body="old body")
    rendered = _doc("['read']", body="a substantially rewritten body")

    assert _merge_fenced_content(rendered, on_disk).front_matter_drift == []


@pytest.mark.parametrize("key", sorted(_DRIFT_EXEMPT_FRONT_MATTER_KEYS))
def test_project_interpolated_keys_are_exempt(key):
    """`name`/`description` embed the project name and differ in EVERY generated file.

    Reporting them would make the notice universal, and a universal notice is noise — the same
    silence, wearing a different hat.
    """
    on_disk = _doc("['read']", name="Agent — OldProject")
    rendered = _doc("['read']", name="Agent — NewProject")
    rendered = rendered.replace('description: "d"', 'description: "totally different"')

    notices = _merge_fenced_content(rendered, on_disk).front_matter_drift

    assert not any(repr(key) in n for n in notices), notices


def test_a_file_with_no_front_matter_reports_nothing():
    """References and instruction files carry no front matter; they must not trip this."""
    plain = _FENCE.format(body="x")
    assert _detect_front_matter_drift(_FENCE.format(body="y"), plain) == []
    assert _detect_front_matter_drift(plain, _doc("['read']")) == []


# --- the parser ------------------------------------------------------------

def test_front_matter_keys_reads_flat_scalars_and_inline_lists():
    keys = _front_matter_keys(_doc("['read', 'search']", extra="user-invokable: false\n"))
    assert keys["tools"] == "['read', 'search']"
    assert keys["user-invokable"] == "false"
    assert keys["model"] == '["m"]'


def test_front_matter_keys_ignores_body_content_after_the_block():
    """A `key:`-shaped line in the body must not be mistaken for front matter."""
    doc = _doc("['read']", body="note: this looks like a key but is body text")
    assert "note" not in _front_matter_keys(doc)


def test_front_matter_keys_on_a_document_without_a_block():
    assert _front_matter_keys("no front matter here\n") == {}


# --- integration with the notice channel -----------------------------------

def test_drift_reaches_the_emit_notice_channel(tmp_path):
    """The notice is only useful if it surfaces where an operator reads run output."""
    from agentteams import emit

    target = tmp_path / "agent.agent.md"
    target.write_text(_doc("['read', 'search']"), encoding="utf-8")

    result = emit.emit_all(
        [("agent.agent.md", _doc("['read', 'search', 'retrieval']", body="b2"))],
        output_dir=tmp_path,
        merge=True,
    )

    assert any("front matter" in n and "tools" in n for n in result.notices), result.notices
    assert any("preserved on-disk value" in n for n in result.notices)


# --- unfenced prose drift, gated on build-log provenance (2026-07-30) -------
#
# The previous round deferred this, reasoning that "an edit cannot be told apart from intended
# authorship without a provenance mechanism the format does not have". That was wrong:
# references/build-log.json records a per-file hash of what was last emitted, and
# drift.detect_user_customizations already compares it. When the hash still matches, nobody has
# edited the file, so divergence in its unfenced prose is template drift by elimination.

from agentteams.fences import _detect_unfenced_drift, _unfenced_regions  # noqa: E402


def _prose_doc(prose: str, body: str = "b") -> str:
    return (
        "---\nname: A\ndescription: \"d\"\ntools: ['read']\nmodel: [\"m\"]\n---\n\n"
        f"{prose}\n\n" + _FENCE.format(body=body)
    )


def test_untouched_file_reports_prose_drift():
    on_disk = _prose_doc("The original template guidance paragraph, reasonably long.")
    rendered = _prose_doc("A substantially rewritten template guidance paragraph, also long.")

    notices = _detect_unfenced_drift(rendered, on_disk, file_is_unmodified=True)

    assert notices and "unfenced prose" in notices[0]
    assert "unmodified since generation" in notices[0]


def test_a_user_edited_file_reports_nothing():
    """The silence that is correct: that prose is the operator's, and nagging teaches muting."""
    on_disk = _prose_doc("The operator rewrote this paragraph themselves.")
    rendered = _prose_doc("A substantially rewritten template guidance paragraph, also long.")

    assert _detect_unfenced_drift(rendered, on_disk, file_is_unmodified=False) == []


def test_identical_prose_reports_nothing_even_when_untouched():
    doc = _prose_doc("Same guidance on both sides, long enough to clear the floor.")
    assert _detect_unfenced_drift(doc, doc, file_is_unmodified=True) == []


def test_a_body_only_change_does_not_count_as_prose_drift():
    """Fenced content is what merge DOES update — it must not be reported as skipped."""
    on_disk = _prose_doc("Identical prose on both sides, long enough to clear the floor.", body="old")
    rendered = _prose_doc("Identical prose on both sides, long enough to clear the floor.", body="new")

    assert _detect_unfenced_drift(rendered, on_disk, file_is_unmodified=True) == []


def test_front_matter_is_not_double_reported_as_prose():
    """`_detect_front_matter_drift` already reports it at key level; twice is noise."""
    on_disk = _prose_doc("Identical prose, long enough to clear the minimum-length floor.")
    rendered = on_disk.replace("tools: ['read']", "tools: ['read', 'retrieval']")

    assert _detect_unfenced_drift(rendered, on_disk, file_is_unmodified=True) == []


def test_unfenced_regions_excludes_fences_and_front_matter():
    extracted = _unfenced_regions(_prose_doc("VISIBLE PROSE", body="FENCED BODY"))
    assert "VISIBLE PROSE" in extracted
    assert "FENCED BODY" not in extracted
    assert "tools:" not in extracted


def test_the_provenance_helper_defaults_to_assume_modified(tmp_path):
    """No build log ⇒ empty set ⇒ nothing reported. The conservative direction."""
    from agentteams.emit import _unmodified_since_build

    assert _unmodified_since_build(tmp_path) == frozenset()


def test_merge_defaults_to_not_reporting_prose_drift():
    """`file_is_unmodified` defaults False, so every pre-existing caller is unaffected."""
    on_disk = _prose_doc("Original prose, comfortably past the minimum length floor.")
    rendered = _prose_doc("Rewritten prose, also comfortably past the minimum length floor.")

    result = _merge_fenced_content(rendered, on_disk)

    assert not any("unfenced prose" in n for n in result.front_matter_drift)
