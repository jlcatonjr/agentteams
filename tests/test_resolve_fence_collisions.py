"""The collision resolver deletes content, so what authorises each delete is the subject here.

`scripts/resolve_fence_collisions.py` removes a deployed file's pre-fencing copy of a section its
template now fences. The estimate that sized the job — "35 of 40 are short, single-occurrence and
unfenced" — is a heuristic, and a heuristic must never be what permits a write. The proof is per
collision: the deployed unfenced section must equal the incoming fenced body once whitespace is
collapsed.

The refusals matter more than the resolutions. Each of these was a way to destroy content:

- A trailing section runs to end-of-file, so deleting it would also take `## Project-Specific
  Notes` — the region `_split_at_last_fence_end` exists to protect, destroyed by the tool built to
  clean up after it.
- A heading appearing twice makes the boundaries ambiguous.
- A deployed copy that differs from the template's is exactly the case a human must look at.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_resolver", Path(__file__).resolve().parents[1] / "scripts" / "resolve_fence_collisions.py"
)
resolver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(resolver)


#: A deployed file that collides always has SOME fences — that is what "partial adoption" means.
#: With none at all the merge bails as a legacy file before collision detection ever runs, so a
#: fixture without `kept` would test nothing. Found by this test failing on its first draft.
_KEPT_FENCE = (
    "<!-- AGENTTEAMS:BEGIN kept v=1 -->\n## Kept\n\nalready fenced.\n"
    "<!-- AGENTTEAMS:END kept -->\n\n"
)

_FRESH = (
    "---\nname: A\ndescription: x\n---\n\n"
    + _KEPT_FENCE +
    "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
    "## Invariant Core\n\nThe contract. Do not modify.\n"
    "<!-- AGENTTEAMS:END invariant_core -->\n\n"
    "## Tail\n\ntrailing template section.\n"
)


#: The deployed file carries the `invariant_core` fence **and** the pre-fencing unfenced copy.
#:
#: That is the state this script exists for, and these fixtures did not model it. They built a
#: file with the unfenced copy and no `invariant_core` fence, so every assertion below described
#: removing the section's ONLY copy — `test_an_identical_pre_fencing_copy_is_removed` asserted
#: the count reached zero. The tool's own docstring says the merge ADDS the fenced block while
#: the unfenced copy is preserved: two copies, one stale. One copy is not a collision.
#:
#: Encoding the pre-merge state as the expected input is what let a real run delete `## Rules`
#: from `conflict-auditor.md` on 2026-08-03 and 331 lines from `security.md` on 2026-08-01,
#: with a green suite both times.
_DEPLOYED_FENCE = (
    "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
    "## Invariant Core\n\nThe contract. Do not modify.\n"
    "<!-- AGENTTEAMS:END invariant_core -->\n\n"
)


def _deployed(core_body: str, *, trailing: str = "## Tail\n\ntrailing.\n") -> str:
    return (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE + _DEPLOYED_FENCE +
        f"## Invariant Core\n\n{core_body}\n\n"
        f"{trailing}"
        "\n## Project-Specific Notes\n\n- operator content that must survive.\n"
    )


def _run(tmp_path: Path, deployed_text: str, fresh: str = _FRESH):
    f = tmp_path / "a.agent.md"
    f.write_text(deployed_text, encoding="utf-8")
    return resolver._resolve_file(f, fresh)


def test_an_identical_pre_fencing_copy_is_removed(tmp_path):
    new_text, resolved, skipped = _run(tmp_path, _deployed("The contract. Do not modify."))
    assert resolved == ["## Invariant Core  [equality]"], (resolved, skipped)
    assert new_text is not None
    # Exactly one copy survives, and it is the FENCED one. This asserted `== 0` while the
    # fixture had no `invariant_core` fence — i.e. it required the tool to delete the section
    # entirely and called that success.
    assert new_text.count("## Invariant Core") == 1, "the fenced copy must survive"
    assert "<!-- AGENTTEAMS:BEGIN invariant_core" in new_text
    assert "operator content that must survive" in new_text


def test_whitespace_differences_do_not_block_a_resolution(tmp_path):
    _, resolved, _ = _run(tmp_path, _deployed("The   contract.\n   Do not modify."))
    assert resolved == ["## Invariant Core  [equality]"]


def test_a_differing_copy_is_refused_and_reported(tmp_path):
    new_text, resolved, skipped = _run(tmp_path, _deployed("The contract. Modified by the project."))
    assert resolved == []
    assert new_text is None, "nothing may be written when nothing was proved"
    assert any("differs from the template" in s for s in skipped), skipped


def test_a_trailing_section_is_refused(tmp_path):
    """The sharp one: bounding at EOF would delete the operator's region below it."""
    trailing_only = (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE +
        "## Invariant Core\n\nThe contract. Do not modify.\n"
        "\n- operator content with no following heading, which a bound-at-EOF would delete.\n"
    )
    new_text, resolved, skipped = _run(tmp_path, trailing_only)
    assert resolved == []
    assert new_text is None
    assert any("trailing at end-of-file" in s or "cannot bound" in s for s in skipped), skipped


def test_a_duplicated_heading_is_refused(tmp_path):
    doubled = _deployed("The contract. Do not modify.").replace(
        "## Tail\n\ntrailing.\n", "## Invariant Core\n\nanother copy.\n\n## Tail\n\ntrailing.\n"
    )
    _, resolved, skipped = _run(tmp_path, doubled)
    assert resolved == []
    assert any("cannot bound" in s for s in skipped), skipped


def test_a_file_with_no_collision_is_untouched(tmp_path):
    """Already-clean files must produce no plan at all, not an empty rewrite."""
    new_text, resolved, skipped = _run(tmp_path, _FRESH)
    assert (new_text, resolved, skipped) == (None, [], [])


@pytest.mark.parametrize("norm_in, norm_out", [
    ("a   b\n\nc", "a b c"),
    ("  leading and trailing  ", "leading and trailing"),
])
def test_normalisation_is_whitespace_only(norm_in, norm_out):
    """No case folding, no punctuation stripping — a real difference cannot be normalised away."""
    assert resolver._norm(norm_in) == norm_out
    assert resolver._norm("Do not modify.") != resolver._norm("do not modify")


# --- the provenance authorisation ------------------------------------------
# Wider than the equality proof: it removes a copy that does NOT match the template, on the
# grounds that a file still hashing to its build-log entry contains no project edit anywhere, so
# an unfenced copy of a now-fenced section is necessarily the stale pre-fencing version.
# Measured before building it: of the 20 collisions the equality proof refused, 11 were in
# unmodified files, 9 in files this session's own deletion-only run had touched, and ZERO were
# genuine project edits.

def _with_build_log(tmp_path: Path, text: str, *, record_hash: bool) -> Path:
    import hashlib
    import json

    agents = tmp_path / "agents"
    (agents / "references").mkdir(parents=True)
    f = agents / "a.agent.md"
    f.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if record_hash else "0" * 64
    (agents / "references" / "build-log.json").write_text(
        json.dumps({"file_hashes": {"a.agent.md": digest}}), encoding="utf-8"
    )
    return agents


def test_a_differing_copy_resolves_when_the_file_is_pristine(tmp_path):
    """The whole point: refusing here left 20 collisions that carried no project edit at all."""
    text = _deployed("A stale earlier version of the contract.")
    agents = _with_build_log(tmp_path, text, record_hash=True)
    new_text, resolved, skipped = resolver._resolve_file(
        agents / "a.agent.md", _FRESH, agents_dir=agents, trust_provenance=True
    )
    assert resolved == ["## Invariant Core  [provenance]"], (resolved, skipped)
    assert new_text is not None and "operator content that must survive" in new_text


def test_an_edited_file_is_never_resolved_on_provenance(tmp_path):
    """A hash that does not match declines. This is the guard the whole authorisation rests on."""
    text = _deployed("A stale earlier version of the contract.")
    agents = _with_build_log(tmp_path, text, record_hash=False)
    new_text, resolved, skipped = resolver._resolve_file(
        agents / "a.agent.md", _FRESH, agents_dir=agents, trust_provenance=True
    )
    assert resolved == []
    assert new_text is None
    assert any("differs from the template" in s for s in skipped), skipped


def test_provenance_is_off_unless_asked_for(tmp_path):
    """A wider authorisation must never be reachable by default."""
    text = _deployed("A stale earlier version of the contract.")
    agents = _with_build_log(tmp_path, text, record_hash=True)
    _, resolved, skipped = resolver._resolve_file(
        agents / "a.agent.md", _FRESH, agents_dir=agents, trust_provenance=False
    )
    assert resolved == []
    assert any("differs from the template" in s for s in skipped)


def test_a_trailing_section_is_still_refused_when_a_user_region_follows(tmp_path):
    """The safety-critical case. Clean provenance does NOT license bounding at end-of-file here.

    A trailing section can only swallow a user region if one exists below it — so the allowance
    requires BOTH no `## Project-Specific Notes` and clean provenance. This fixture has the region,
    so it must refuse however pristine the file is.
    """
    with_notes = (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE +
        "## Invariant Core\n\nA stale earlier version.\n"
        "\n## Project-Specific Notes\n\n- operator content that must survive.\n"
    )
    agents = _with_build_log(tmp_path, with_notes, record_hash=True)
    new_text, resolved, skipped = resolver._resolve_file(
        agents / "a.agent.md", _FRESH, agents_dir=agents, trust_provenance=True
    )
    assert new_text is None or "operator content that must survive" in new_text
    assert not any("[provenance]" in r and "Invariant Core" in r for r in resolved) or (
        "operator content that must survive" in (new_text or "")
    ), "the user region below a trailing section must never be swallowed"


def test_provenance_cannot_delete_a_section_with_no_survivor_on_disk(tmp_path):
    """Pristine provenance is not a licence to remove the only copy.

    This previously asserted the removal SUCCEEDED. The fixture has no `invariant_core` fence,
    so "resolving" it left the file with no invariant core at all — and `_file_is_pristine` was
    the exact authorisation that took `security.md` from 363 lines to 32 on 2026-08-01.

    Provenance answers "has anyone edited this file?". That is a real question and the answer
    is used elsewhere. It is not the question that makes a delete safe, which is "does a copy
    remain afterwards?" — and the two were being conflated.

    The trailing case is still resolvable when a survivor exists: see
    `test_trailing_collision_equality.py`, where equality against a deployed fence authorises
    EOF-bounding with no provenance at all.
    """
    trailing_only = (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE +
        "## Invariant Core\n\nA stale earlier version.\n"
    )
    agents = _with_build_log(tmp_path, trailing_only, record_hash=True)
    new_text, resolved, skipped = resolver._resolve_file(
        agents / "a.agent.md", _FRESH, agents_dir=agents, trust_provenance=True
    )
    assert resolved == [], f"provenance authorised deleting the only copy: {resolved}"
    assert any("no fence carrying that section" in s for s in skipped), skipped
    if new_text is not None:
        assert "## Invariant Core" in new_text


def test_a_trailing_section_is_refused_without_provenance(tmp_path):
    """The allowance needs BOTH conditions; a dirty file with no notes region still refuses."""
    trailing_only = (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE +
        "## Invariant Core\n\nA stale earlier version.\n"
    )
    agents = _with_build_log(tmp_path, trailing_only, record_hash=False)
    _, resolved, skipped = resolver._resolve_file(
        agents / "a.agent.md", _FRESH, agents_dir=agents, trust_provenance=True
    )
    assert resolved == []
    assert any("trailing" in s for s in skipped), skipped


def test_a_missing_build_log_declines(tmp_path):
    """No baseline means no provenance claim; it must not default to trusting."""
    agents = tmp_path / "agents"
    agents.mkdir()
    f = agents / "a.agent.md"
    f.write_text(_deployed("A stale earlier version."), encoding="utf-8")
    assert resolver._file_is_pristine(agents, f) is False


# ---------------------------------------------------------------------------
# A removal must never take a fenced region with it
#
# `--trust-provenance` removed `## Invariant Core` from the deployed security.md on the
# strength of the TEMPLATE's `invariant_core` fence — which the deployed file does not
# have. The section is level-2 with only level-3 subsections, so its span ran to EOF:
# 363 lines -> 32, and the file's own two fenced regions (security_rules_invariant,
# threat_intelligence) went with it. Post-removal the file had 0 fences and no
# Invariant Core.
#
# The authorisation was inverted. Provenance answers "who wrote this"; it cannot answer
# "is this content safely carried elsewhere". A span containing a live fence is managed
# content by definition, and removing it is deletion, not deduplication.
# ---------------------------------------------------------------------------


def test_removal_refuses_a_span_containing_a_fence(tmp_path, monkeypatch):
    """The exact shape of the security.md loss, reduced.

    Reproducing it needs all three conditions the real file had, and the first draft of
    this test had none of them — it passed against the buggy code, which is why it was
    rewritten:

    * a **bounded** span (a later same-level heading), not a trailing one;
    * deployed content that **differs** from the template's fence body, so the equality
      proof refuses and only provenance can authorise;
    * ``_file_is_pristine`` True, which the real deployed file was.

    The span then encloses the file's own fenced region, and removing it deletes managed
    content. 363 lines -> 32 in the live incident, 2 fences -> 0.
    """
    r = resolver
    monkeypatch.setattr(r, "_file_is_pristine", lambda *a, **k: True)

    deployed = tmp_path / "security.md"
    deployed.write_text(
        "---\nname: security\n---\n\n"
        "# Security\n\n"
        "## Invariant Core\n\n"
        "### Rules\n\nS-1 something.\n\n"
        "<!-- AGENTTEAMS:BEGIN security_rules_invariant v=1 -->\n"
        "managed content the operator must not lose\n"
        "<!-- AGENTTEAMS:END security_rules_invariant -->\n\n"
        "## Later Section\n\nbounds the span above.\n",
        encoding="utf-8",
    )
    fresh = (
        "---\nname: security\n---\n\n"
        "# Security\n\n"
        "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
        "## Invariant Core\n\ndifferent body\n"
        "<!-- AGENTTEAMS:END invariant_core -->\n\n"
        "## Later Section\n\nbounds the span above.\n"
    )

    original = deployed.read_text(encoding="utf-8")
    span = r._unfenced_section_span(original, "## Invariant Core")
    assert not isinstance(span, str), f"fixture must produce a BOUNDED span, got {span!r}"
    assert "AGENTTEAMS:BEGIN" in original[span[0]:span[1]], (
        "fixture is wrong: the span must enclose a fence"
    )

    new_text, resolved, skipped = r._resolve_file(
        deployed, fresh, agents_dir=tmp_path, trust_provenance=True
    )
    surviving = new_text if new_text is not None else original
    assert "AGENTTEAMS:BEGIN security_rules_invariant" in surviving, (
        "the removal deleted a fenced region — that is deletion of managed content, not "
        f"deduplication. resolved={resolved} skipped={skipped}"
    )


# ---------------------------------------------------------------------------
# Collisions must be detectable AFTER the fence exists, not only while it is added
#
# `duplicate_section_notices` fire only while `_merge_fenced_content` is ADDING a fence.
# Once added, the pre-existing unfenced twin is still in the file but produces no notice,
# so the resolver reports 0 collisions against a tree carrying 23 duplicate headings in
# 17 files. The merge creates the duplicate and blinds the only detector for it in the
# same step.
# ---------------------------------------------------------------------------


def test_duplicate_is_found_when_the_fence_is_already_present(tmp_path):
    """A heading appearing both inside a fence and outside one is a collision.

    The merge-notice path cannot see this: re-merging an already-fenced file produces no
    notices at all.
    """
    r = resolver
    deployed = tmp_path / "navigator.md"
    deployed.write_text(
        "---\nname: navigator\n---\n\n"
        "# Navigator\n\n"
        "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
        "## Invariant Core\n\nfenced body\n"
        "<!-- AGENTTEAMS:END invariant_core -->\n\n"
        "## Invariant Core\n\nthe stale unfenced twin\n\n"
        "## Later\n\nbounds it.\n",
        encoding="utf-8",
    )
    text = deployed.read_text(encoding="utf-8")

    # Precondition: the merge-notice path is blind here — that is the defect.
    assert not r._merge_fenced_content(text, text).duplicate_section_notices, (
        "fixture invalid: the notice path still sees this, so it proves nothing"
    )

    found = r._duplicate_headings_in_file(text)
    assert "## Invariant Core" in found, (
        f"the already-fenced duplicate was not detected; found={found}"
    )
    assert "## Later" not in found, "a heading appearing once must not be reported"


def test_span_bounds_the_unfenced_copy_when_a_fenced_twin_exists(tmp_path):
    """`_unfenced_section_span` must ignore the fenced occurrence, not count it.

    Its filter checks whether the heading appears among unfenced lines AT ALL, then keeps
    every regex match — fenced ones included. With a fenced twin present that is always
    two matches, so it returns "duplicated" and every one of the 20 real collisions was
    refused as unboundable. The filter has to be per-occurrence.
    """
    r = resolver
    text = (
        "---\nname: a\n---\n\n"
        "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
        "## Invariant Core\n\nfenced body\n"
        "<!-- AGENTTEAMS:END invariant_core -->\n\n"
        "## Invariant Core\n\nthe stale unfenced twin\n\n"
        "## Later\n\nbounds it.\n"
    )
    span = r._unfenced_section_span(text, "## Invariant Core")
    assert not isinstance(span, str), (
        f"expected a bounded span for the unfenced copy, got refusal {span!r}"
    )
    start, end = span
    body = text[start:end]
    assert "the stale unfenced twin" in body, "bounded the wrong occurrence"
    assert "fenced body" not in body, "the span swallowed the fenced twin"
    assert "AGENTTEAMS:BEGIN" not in body, "the span encloses a fence marker"
    assert "## Later" not in body, "the span ran past the next heading"
