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
    rendered = _doc(**{**_BASE, "user-invocable": "true"})

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["user-invocable"] == "true"
    assert any("user-invocable" in a for a in applied)
    assert proposals == []


def test_a_key_the_project_edited_is_never_overwritten():
    """The failure the withdrawn design would have caused."""
    baseline = dict(_BASE)
    on_disk = _doc(**{**_BASE, "user-invocable": "false"})       # project chose false
    rendered = _doc(**{**_BASE, "user-invocable": "true"})       # template says true
    baseline["user-invocable"] = "maybe"                          # neither — both moved

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["user-invocable"] == "false", "the project's choice must survive"
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
    rendered = _doc(**{**_BASE, "user-invocable": "true"})

    merged, applied, _ = _merge_front_matter(rendered, on_disk, None)

    assert "user-invocable" not in merged
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


# --- key-succession rename (_migrate_superseded_keys) ----------------------
#
# P3 (2026-08-15): this mechanism previously had zero dedicated coverage at
# this layer — confirmed by two independent audits before it was extended
# for the user-invocable/user-invokable rename. These tests lock down the
# EXISTING tools/allowed-tools entry first, then cover the new entry.

from agentteams.front_matter_merge import _KEY_SUCCESSION, _migrate_superseded_keys  # noqa: E402


def test_key_succession_table_has_the_expected_entries():
    """A change to this tuple is a behavior change; pin its shape."""
    assert ("tools", "allowed-tools") in _KEY_SUCCESSION
    assert ("user-invocable", "user-invokable") in _KEY_SUCCESSION


def test_existing_allowed_tools_rename_still_works():
    """Locks down the pre-existing entry before/after generalizing the mechanism."""
    on_disk = "---\nname: A\nallowed-tools: Read, Grep\n---\n\nbody\n"
    rendered = "---\nname: A\ntools: Read, Grep, Bash\n---\n\nbody\n"

    merged, applied, proposals = _merge_front_matter(rendered, on_disk, None)

    assert merged["tools"] == "Read, Grep"
    assert "allowed-tools" not in merged
    assert any("allowed-tools" in a and "renamed to" in a and "'tools'" in a for a in applied)
    assert any("grant it declared was never enforced" in a for a in applied), (
        "tools/allowed-tools is a real capability key — its notice must still use capability "
        "language after the mechanism was generalized"
    )


def test_user_invocable_rename_old_key_only():
    """Scenario 1: old key only -> new key applied, value preserved."""
    on_disk = "---\nname: A\nuser-invokable: false\n---\n\nbody\n"
    rendered = "---\nname: A\nuser-invocable: true\n---\n\nbody\n"

    merged, applied, _ = _merge_front_matter(rendered, on_disk, None)

    assert merged["user-invocable"] == "false", "the deployed VALUE must survive the rename"
    assert "user-invokable" not in merged
    assert any(
        "user-invokable" in a and "renamed to" in a and "'user-invocable'" in a for a in applied
    )
    assert not any("grant it declared was never enforced" in a for a in applied), (
        "user-invocable is NOT a capability key — its notice must not use capability/grant "
        "language"
    )


def test_user_invocable_rename_hand_edited_value_preserved():
    """Scenario 2: old key hand-edited (differs from baseline) -> new key gets the edited
    value, not silently dropped."""
    baseline = {"name": "A", "user-invokable": "false"}
    on_disk = "---\nname: A\nuser-invokable: true\n---\n\nbody\n"  # project flipped it to true
    rendered = "---\nname: A\nuser-invocable: false\n---\n\nbody\n"  # template still says false

    merged, applied, _ = _merge_front_matter(rendered, on_disk, baseline)

    assert merged["user-invocable"] == "true", "the project's hand-edit must survive the rename"


def test_user_invocable_rename_both_keys_present_is_not_double_declared():
    """Scenario 3: both keys present (a file mid-migration) -> not touched again."""
    on_disk = "---\nname: A\nuser-invokable: false\nuser-invocable: true\n---\n\nbody\n"
    rendered = "---\nname: A\nuser-invocable: true\n---\n\nbody\n"

    merged, applied, _ = _merge_front_matter(rendered, on_disk, None)

    assert merged["user-invocable"] == "true"
    assert not any("user-invokable" in a and "renamed" in a for a in applied), (
        "a file that already carries the new key must not be rewritten again"
    )


def test_user_invocable_fresh_file_neither_key_present():
    """Scenario 4: brand-new file, neither key present -> template default applies via the
    ordinary (non-rename) path, not the migration path.

    This exercises copilot_vscode.py's _ensure_yaml_front_matter fallback, a genuinely
    different code path from _merge_front_matter (which only runs once a file already has
    SOME front matter) — confirmed via emit.py's dispatch before writing this test.
    """
    from agentteams.frameworks.copilot_vscode import _ensure_yaml_front_matter

    fresh_body = "no front matter here\n"
    out = _ensure_yaml_front_matter(fresh_body, "some-agent", {"project_name": "P"})
    assert "user-invocable: false" in out
    assert "user-invokable" not in out


def test_rendering_a_document_without_front_matter_is_a_no_op():
    assert _render_front_matter("plain\n", {"a": "1"}) == "plain\n"


# --- block-style values (agents:/handoffs:) — issue #131 -------------------
#
# A block-style list (`key:` alone on its line, items indented below) is the shape `agents:`
# and `handoffs:` render in whenever the list is non-trivial. The single-line key/value regex
# used to capture only the `key:` line itself, reading the value as `""` and silently dropping
# every indented line under it — which then round-tripped straight back out through
# `_render_front_matter` and blanked the block on disk the moment ANY other front-matter key
# changed on the same file. These tests change a *scalar* key alongside the *block-style* key in
# the same file, since the destructive path only triggers when `_fm_applied` is non-empty for
# some key — a test that only varies the block-style key wouldn't reach `_render_front_matter()`
# at all and would miss the bug.

from agentteams.front_matter_merge import _front_matter_keys  # noqa: E402


_BLOCK_DOC = """---
name: Orchestrator
user-invokable: true
tools: ['read', 'edit']
agents:
  - orchestrator
  - navigator
  - security
handoffs:
  - label: Produce
    agent: primary-producer
    prompt: "do the thing"
    send: false
model: ["auto"]
---
body text unchanged
"""


def test_front_matter_keys_captures_block_style_value_in_full():
    """The regression at the root: a block value must not read as ''."""
    keys = _front_matter_keys(_BLOCK_DOC)
    assert keys["agents"] != ""
    assert "orchestrator" in keys["agents"]
    assert "navigator" in keys["agents"]
    assert "security" in keys["agents"]
    assert keys["handoffs"] != ""
    assert "primary-producer" in keys["handoffs"]


def test_unrelated_scalar_rename_does_not_blank_block_style_agents_and_handoffs():
    """End-to-end reproduction of issue #131's real-world trigger: a rename to one unrelated
    scalar key (user-invokable -> user-invocable) must not blank agents:/handoffs:."""
    on_disk = _BLOCK_DOC
    rendered = on_disk.replace("user-invokable: true", "user-invocable: true")
    baseline = _front_matter_keys(on_disk)

    merged_keys, applied, proposals = _merge_front_matter(rendered, on_disk, baseline)
    assert any("user-invokable" in a and "renamed" in a for a in applied), (
        "the rename itself must still be detected — otherwise this test can't reach the "
        "render path that triggered the bug"
    )
    final = _render_front_matter(on_disk, merged_keys) if applied else on_disk

    assert "agents:\n  - orchestrator" in final, "agents: must not render as an empty block"
    assert "  - orchestrator" in final
    assert "  - navigator" in final
    assert "  - security" in final
    assert "agent: primary-producer" in final, "handoffs: must not be blanked"


def test_render_front_matter_round_trips_block_style_value_unchanged():
    keys = _front_matter_keys(_BLOCK_DOC)
    out = _render_front_matter(_BLOCK_DOC, keys)
    assert out == _BLOCK_DOC, "re-rendering with no merge changes must reproduce the file byte-for-byte"


def test_a_blank_line_after_a_scalar_key_does_not_blank_it():
    """Regression found under adversarial review of the first version of this fix: a blank
    line trailing a same-line scalar (`name: A\\n\\ntools: [...]`) was read as `[""]` —
    truthy — so `_flush()` treated it as proof of block-style and silently discarded the
    scalar's real value, reproducing issue #131's failure mode under a different trigger."""
    doc = '---\nname: A\n\ntools: ["read"]\n---\nbody\n'
    keys = _front_matter_keys(doc)
    assert keys["name"] == "A"
    assert keys["tools"] == '["read"]'


def test_an_unindented_comment_line_after_a_scalar_key_does_not_blank_it():
    """Same failure mode as the blank-line case, for a comment line instead: a `#`-prefixed
    line at column 0 is non-blank but not part of any block (a genuine block item is always
    indented), so it must not count as proof this key is block-style either."""
    doc = '---\nname: A\n# a comment line\ntools: ["read"]\n---\nbody\n'
    keys = _front_matter_keys(doc)
    assert keys["name"] == "A"
    assert keys["tools"] == '["read"]'
