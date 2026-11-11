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


# --- IM item 02 (G-04-1): a grant beyond the template is flagged even when it is no superset ---------

def test_a_non_superset_grant_beyond_the_template_is_flagged():
    """`Bash, Read, Write, Edit` vs `Read, Grep, Glob`: no superset, yet it grants Bash/Edit/Write."""
    disk = _fm(name="security", tools="Bash, Read, Write, Edit")
    rendered = _fm(name="security", tools="Read, Grep, Glob")
    baseline = {"name": "security", "tools": "Read, Grep, Glob"}
    merged, applied, proposals = _merge_front_matter(rendered, disk, baseline)
    assert merged["tools"] == "Bash, Read, Write, Edit", "the value is never rewritten without consent"
    hit = [p for p in proposals if "GRANTS MORE" in p]
    assert hit and "['Bash', 'Edit', 'Write']" in hit[0], proposals
    assert "`agentteams --reconcile-front-matter`" in hit[0] and "`--reconcile-apply` applies ALL" in hit[0]


@pytest.mark.parametrize("legacy", ["Bash, Edit", "Bash, Read, Write, Edit"])
def test_a_renamed_legacy_grant_is_flagged_whatever_its_provenance(legacy):
    """The VK pattern: `allowed-tools` was ignored; renamed to `tools` it is enforced. Flag it, even untouched."""
    disk = _fm(name="security", **{"allowed-tools": legacy})
    rendered = _fm(name="security", tools="Read, Grep, Glob")
    baseline = {"name": "security", "allowed-tools": legacy}          # untouched since generation
    merged, applied, proposals = _merge_front_matter(rendered, disk, baseline)
    assert merged["tools"] == legacy and "allowed-tools" not in merged
    hit = [p for p in proposals if "GRANTS MORE" in p]
    assert hit, proposals
    assert "ENFORCED from now on" in hit[0]
    assert "`agentteams --reconcile-front-matter`" in hit[0] and "`--reconcile-apply` applies ALL" in hit[0]


def test_a_renamed_grant_within_the_template_is_not_flagged():
    disk = _fm(name="security", **{"allowed-tools": "Read, Grep"})
    rendered = _fm(name="security", tools="Read, Grep, Glob")
    merged, applied, proposals = _merge_front_matter(rendered, disk, {"name": "security", "allowed-tools": "Read, Grep"})
    assert not any("GRANTS MORE" in p for p in proposals)


# --- IM item 02 (G-04-2): no Claude template declares the retired key -------------------------------

def test_no_template_declares_the_retired_allowed_tools_key():
    import pathlib
    import re

    templates = pathlib.Path(__file__).resolve().parents[1] / "agentteams" / "templates"
    hits = [str(p.relative_to(templates)) for p in templates.rglob("*.md")
            if re.search(r"^allowed-tools:", p.read_text(encoding="utf-8"), re.MULTILINE)]
    assert not hits, f"templates still declare `allowed-tools:` (Claude Code ignores it): {hits}"
    guide = (templates / "AUTHORING-GUIDE.md").read_text(encoding="utf-8")
    # The Claude adapter DOES fall back to `Bash, Read, Write, Edit` for an agent with no `tools:`; the guide
    # must say so, and must not claim the bridge's warn-only behaviour for the adapter.
    row = next(ln for ln in guide.splitlines() if ln.startswith("| `tools` | **Mapped**"))
    assert "falls back to `Bash, Read, Write, Edit`" in row and "bridge" in row


def test_an_unparseable_capability_value_is_reported_not_skipped():
    disk = _fm(name="security", tools="Read, Grep, Glob # plus Bash later")
    rendered = _fm(name="security", tools="['read', 'search']")
    merged, applied, proposals = _merge_front_matter(rendered, disk, {"name": "security", "tools": "['read', 'search']"})
    assert any("could not be parsed" in p for p in proposals) or any("GRANTS MORE" in p for p in proposals), proposals


def test_a_capability_key_only_the_file_declares_is_reported():
    disk = _fm(name="security", tools="['read', 'search']", permissionMode="bypassPermissions")
    rendered = _fm(name="security", tools="['read', 'search']")
    merged, applied, proposals = _merge_front_matter(rendered, disk, {"name": "security", "tools": "['read', 'search']"})
    assert any("'permissionMode'" in p and "GRANTS MORE" in p for p in proposals), proposals
    assert merged["permissionMode"] == "bypassPermissions"


def test_a_shrunken_deny_list_is_reported():
    disk = _fm(name="security", tools="['read']", disallowedTools="Bash")
    rendered = _fm(name="security", tools="['read']", disallowedTools="Bash, Write")
    merged, applied, proposals = _merge_front_matter(rendered, disk, {"name": "security", "disallowedTools": "Bash, Write",
                                                                       "tools": "['read']"})
    assert any("'disallowedTools'" in p and "GRANTS MORE" in p and "Write" in p for p in proposals), proposals


def test_grant_notices_print_under_their_own_heading(capsys):
    from agentteams.front_matter_merge import print_merge_notices

    print_merge_notices(["a.md: front matter: 'tools' on disk GRANTS MORE than the template — extra: ['Bash']",
                         "b.md: some region shrank"])
    err = capsys.readouterr().err
    assert "1 capability grant(s) beyond the template" in err
    assert "1 fenced region(s) shrank" in err
    assert err.index("GRANTS MORE") < err.index("fenced region(s) shrank")
