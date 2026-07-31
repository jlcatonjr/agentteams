"""Three-way merge of YAML front matter — the half of updatability fences cannot reach.

Front matter must be the literal first bytes of a file, before any HTML comment, so it can never
be wrapped in an `AGENTTEAMS` fence. Measured 2026-07-31, that is 770 lines across this project's
own 34 agent files that `--update --merge` could never touch — including the case that motivated
all of this: adding the `retrieval` token to two templates reached no already-generated team.

**Why three-way and not the design that was withdrawn.** An earlier `--sync-front-matter` would
have applied the template's value whenever it differed. That was withdrawn because a difference
does not tell you *who* caused it — overwriting a project's deliberate choice is exactly what
merge mode exists to prevent. With a baseline recorded at last emission, "the project never
touched this key" becomes provable rather than assumed, and only that provable subset is applied.

**Capability keys are still never applied.** Proving nobody edited `tools:` is not the same as
having authority to grant a downstream agent shell access. Those are reported as proposals
however clean their provenance — the security carve-out from the plan audit.
"""

from __future__ import annotations

import pytest

from agentteams.fences import (
    _CAPABILITY_FRONT_MATTER_KEYS,
    _merge_front_matter,
    _render_front_matter,
)


def _doc(**keys: str) -> str:
    body = "\n".join(f"{k}: {v}" for k, v in keys.items())
    return f"---\n{body}\n---\n\nbody\n"


_BASE = {"name": "A", "description": '"d"', "tools": "['read']", "model": '["m"]'}


# --- the safe subset: metadata the project never touched -------------------

def test_untouched_metadata_key_is_applied():
    """Template moved, project never touched it, not a capability ⇒ apply."""
    baseline = dict(_BASE)
    on_disk = _doc(**_BASE)
    rendered = _doc(**{**_BASE, "user-invokable": "true"})

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["user-invokable"] == "true"
    assert any("user-invokable" in a for a in applied)
    assert proposals == []


def test_a_key_the_project_edited_is_never_overwritten():
    """The failure the withdrawn design would have caused."""
    baseline = dict(_BASE)
    on_disk = _doc(**{**_BASE, "user-invokable": "false"})       # project chose false
    rendered = _doc(**{**_BASE, "user-invokable": "true"})       # template says true
    baseline["user-invokable"] = "maybe"                          # neither — both moved

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["user-invokable"] == "false", "the project's choice must survive"
    assert applied == []
    assert any("BOTH" in p for p in proposals)


def test_an_unchanged_template_never_touches_the_projects_value():
    baseline = dict(_BASE)
    on_disk = _doc(**{**_BASE, "model": '["project-choice"]'})
    rendered = _doc(**_BASE)

    merged, applied, _ = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["model"] == '["project-choice"]'
    assert applied == []


# --- the capability carve-out ---------------------------------------------

def test_tools_is_never_applied_even_with_clean_provenance():
    """The motivating case — and it still does not auto-apply, by design."""
    baseline = dict(_BASE)
    on_disk = _doc(**_BASE)                                       # untouched since generation
    rendered = _doc(**{**_BASE, "tools": "['read', 'retrieval']"})

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["tools"] == "['read']", "capability must not change unattended"
    assert applied == []
    assert any("capability declaration" in p and "retrieval" in p for p in proposals)


def test_the_capability_proposal_names_the_exact_line_to_apply():
    baseline = dict(_BASE)
    _, _, proposals = _merge_front_matter(
        _doc(**{**_BASE, "tools": "['read', 'retrieval']"}), _doc(**_BASE), baseline
    )
    assert any("set `tools: ['read', 'retrieval']`" in p for p in proposals)


@pytest.mark.parametrize("key", sorted(_CAPABILITY_FRONT_MATTER_KEYS))
def test_every_capability_key_is_carved_out(key):
    baseline = {**_BASE, key: "old"}
    on_disk = _doc(**{**_BASE, key: "old"})
    rendered = _doc(**{**_BASE, key: "new"})

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)

    assert merged[key] == "old"
    assert applied == []
    assert proposals


# --- unknown provenance is conservative ------------------------------------

def test_no_baseline_means_nothing_is_applied():
    """An older build log predates the baseline field; unknown must mean 'assume edited'."""
    on_disk = _doc(**_BASE)
    rendered = _doc(**{**_BASE, "user-invokable": "true"})

    merged, applied, _ = _merge_front_matter(rendered, on_disk, None)

    assert "user-invokable" not in merged
    assert applied == []


def test_a_file_without_front_matter_is_left_alone():
    merged, applied, proposals = _merge_front_matter("no front matter\n", "none either\n", {})
    assert (merged, applied, proposals) == ({}, [], [])


# --- rendering back out ----------------------------------------------------

def test_rendering_preserves_existing_key_order():
    """A merge must not reshuffle a file the project reads."""
    content = _doc(**_BASE)
    out = _render_front_matter(content, {**_BASE, "extra": "1"})
    block = out.split("---")[1]
    keys = [ln.split(":")[0] for ln in block.splitlines() if ln.strip()]
    assert keys[: len(_BASE)] == list(_BASE), "existing order must be stable"
    assert keys[-1] == "extra", "new keys append"


def test_rendering_keeps_the_body_untouched():
    content = _doc(**_BASE)
    out = _render_front_matter(content, _BASE)
    assert out.endswith("\nbody\n")


def test_rendering_a_document_without_front_matter_is_a_no_op():
    assert _render_front_matter("plain\n", {"a": "1"}) == "plain\n"
