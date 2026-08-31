"""A capability grant may shrink toward the template automatically. It may never grow.

`tools:` is the strongest control in the system — an agent cannot exceed its declared tool
surface — and it is the one thing regeneration could not repair, because front matter must be the
first bytes of a file and therefore cannot be fenced. `_merge_front_matter` refused to apply
`tools`, `model`, or `agents` at all, *however clean their provenance*, on the reasoning that
proving nobody edited `tools:` is not the same as having authority to grant shell access.

That reasoning is right for **widening** and over-broad for **narrowing**. Adopting a template
value that is a strict subset of the on-disk one *removes* a capability; it needs no authority the
merge engine lacks, and refusing it meant a template that tightened a grant reached new teams only.

Two behaviours are guarded here, and the second matters more than the first:

- **Narrowing applies** when the file is unmodified since generation.
- **Widening is named.** When the on-disk grant is *wider* than the template's, the notice now
  says so explicitly — "GRANTS MORE than the template", with the extra tools listed — instead of
  the generic "is a capability declaration" message that made an escalation look like an ordinary
  version difference. The merge still does not touch it: on a file someone has edited, a wider
  grant may be a deliberate project choice, and this path deliberately preserves it.

The subset test runs on **parsed sets**, never substrings. `['read']` is a substring of both
`['read', 'edit']` and `['read', 'ed']`; only the first is a narrowing.
"""

from __future__ import annotations

import pytest

from agentteams.fences import _merge_front_matter, _parse_capability_list


def _fm(**keys) -> str:
    body = "\n".join(f"{k}: {v}" for k, v in keys.items())
    return f"---\n{body}\n---\n\n# Agent\n\nbody\n"


# --- the parser -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("['read', 'search']", frozenset({"read", "search"})),
    ('["read","edit"]', frozenset({"read", "edit"})),
    ("[ 'read' ]", frozenset({"read"})),
])
def test_bracketed_lists_parse(raw, expected):
    assert _parse_capability_list(raw) == expected


@pytest.mark.parametrize("raw", ["opus", "", "[]", "[read, search]", "- read"])
def test_anything_else_is_none_so_callers_fall_through(raw):
    """An unparseable value must never be treated as a narrowing."""
    assert _parse_capability_list(raw) is None


def test_a_substring_is_not_a_subset():
    """The trap this parser exists to avoid."""
    assert not (_parse_capability_list("['read']") < _parse_capability_list("['read', 'ed']")) is False
    assert _parse_capability_list("['read']") < _parse_capability_list("['read', 'edit']")


# --- the merge --------------------------------------------------------------

def test_narrowing_applies_when_the_file_is_untouched():
    disk = _fm(name="A", tools="['read', 'search', 'edit']")
    rendered = _fm(name="A", tools="['read', 'search']")
    baseline = {"name": "A", "tools": "['read', 'search', 'edit']"}   # disk == baseline
    merged, applied, proposals = _merge_front_matter(rendered, disk, baseline)
    assert merged["tools"] == "['read', 'search']"
    assert any("narrowed" in a and "'edit'" in a for a in applied), applied
    assert not proposals


def test_widening_is_never_applied_and_is_named_as_such():
    """The security-relevant half: an escalation must not read like a version difference."""
    disk = _fm(name="A", tools="['read', 'search', 'edit']")
    rendered = _fm(name="A", tools="['read', 'search']")
    baseline = {"name": "A", "tools": "['read', 'search']"}           # disk was EDITED
    merged, applied, proposals = _merge_front_matter(rendered, disk, baseline)
    assert merged["tools"] == "['read', 'search', 'edit']", "the on-disk grant is preserved"
    assert not applied
    assert any("GRANTS MORE" in p and "'edit'" in p for p in proposals), proposals


def test_a_template_that_adds_a_tool_still_only_proposes():
    """Widening from the template's side is the case the original refusal was written for."""
    disk = _fm(name="A", tools="['read']")
    rendered = _fm(name="A", tools="['read', 'edit']")
    baseline = {"name": "A", "tools": "['read']"}
    merged, applied, proposals = _merge_front_matter(rendered, disk, baseline)
    assert merged["tools"] == "['read']"
    assert not applied
    assert any("capability declaration" in p for p in proposals), proposals


def test_a_scalar_capability_key_is_untouched():
    """`model:` is not a list; it must fall through to the pre-existing propose-only path."""
    disk = _fm(name="A", model="opus")
    rendered = _fm(name="A", model="sonnet")
    merged, applied, proposals = _merge_front_matter(rendered, disk, {"name": "A", "model": "opus"})
    assert merged["model"] == "opus"
    assert not applied
    assert any("capability declaration" in p for p in proposals)


def test_no_baseline_means_nothing_is_applied():
    """An unknown baseline is read as 'the project may have edited everything'."""
    disk = _fm(name="A", tools="['read', 'search', 'edit']")
    rendered = _fm(name="A", tools="['read', 'search']")
    merged, applied, _ = _merge_front_matter(rendered, disk, None)
    assert merged["tools"] == "['read', 'search', 'edit']"
    assert not applied
