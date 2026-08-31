"""An agent template whose charter claims external retrieval must be granted a tool that can do it.

**The defect this generalises.** `tool-doc-researcher`'s description said it "Locates and verifies
official documentation… for tools"; its tools were `['read', 'search']`, where `search` maps to
Grep/Glob — *local file* search. It could not fetch documentation. `reference-manager` claimed
"citation verification" with the same grant. Both shipped that way for the life of the project.

Nothing caught it because the description and the tool grant are authored in the same file, four
lines apart, and were never compared. A charter is a promise about what an agent can do; a tool
list is what it can actually do. When they disagree, the generated team contains an agent that
will confidently attempt a task it has no means to complete.

**Scope, deliberately narrow.** This reads the `description:` front-matter line only — not body
prose, which is long, discursive, and would make the check unstable. It fires only on an explicit
*external* indicator, and is suppressed by an explicit locality qualifier, because "verify",
"source", and "documentation" are ambient vocabulary in this template library and matching them
bare would flag most of it.

This check does **not** auto-grant anything. Which agents hold `retrieval` is a least-privilege
decision recorded in `references/retrieval-transport-policy.md`; the right response to a finding
here is a human choosing between granting the tool and rewording the charter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates"

#: Phrases that assert reaching something OUTSIDE the working tree. Deliberately specific:
#: "official documentation", not "documentation"; "web search", not "search".
_EXTERNAL_INDICATORS = (
    "official documentation",
    "official docs",
    "web search",
    "citation verification",
    "verifies citations",
    "bibliograph",
    "upstream documentation",
    "third-party documentation",
    "external source",
    "externally-retrieved",
)

#: Phrases that scope a claim back to the local tree. An indicator co-occurring with one of these
#: is not a promise of network access — `technical-validator` verifies API references "match what
#: exists on disk", which is exactly the case that must stay clean.
_LOCALITY_QUALIFIERS = (
    "on disk",
    "in the repository",
    "in this repository",
    "project's source materials",
    "already in",
    "local",
)

#: Tokens that can actually reach the network. `retrieval` grants the scoped research CLI;
#: `execute` grants unrestricted Bash, which subsumes it.
_RETRIEVAL_CAPABLE = ("retrieval", "execute")

_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)
_TOOLS_RE = re.compile(r"^tools:\s*\[([^\]]*)\]", re.MULTILINE)


def _front_matter(text: str) -> tuple[str, list[str]] | None:
    """Extract ``(description, tools)`` from a template's YAML front matter.

    Args:
        text: Full template file contents.

    Returns:
        ``(description, tools)`` when both keys are present in the first front-matter block,
        else ``None`` — a template without both is not something this check can reason about.
    """
    head = text[:2000]
    desc_match = _DESCRIPTION_RE.search(head)
    tools_match = _TOOLS_RE.search(head)
    if not desc_match or not tools_match:
        return None
    description = desc_match.group(1).strip().strip('"').strip("'").lower()
    tools = [t.strip().strip("'\"").lower() for t in tools_match.group(1).split(",") if t.strip()]
    return description, tools


def claims_external_retrieval(description: str) -> bool:
    """Whether a description promises reaching outside the working tree.

    Args:
        description: The lowercased ``description:`` front-matter value.

    Returns:
        True when an external indicator is present and no locality qualifier scopes it back
        to local files.
    """
    if not any(ind in description for ind in _EXTERNAL_INDICATORS):
        return False
    return not any(q in description for q in _LOCALITY_QUALIFIERS)


def _offenders() -> list[tuple[str, str]]:
    """Return ``(template_stem, description)`` for every charter/tool mismatch."""
    found: list[tuple[str, str]] = []
    for path in sorted(_TEMPLATES.rglob("*.template.md")):
        parsed = _front_matter(path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        description, tools = parsed
        if claims_external_retrieval(description) and not any(
            t in tools for t in _RETRIEVAL_CAPABLE
        ):
            found.append((path.name.replace(".template.md", ""), description))
    return found


def test_no_template_claims_external_retrieval_without_a_tool_for_it():
    offenders = _offenders()
    assert not offenders, (
        "These agent templates promise external retrieval in their description but hold no "
        "tool that can reach the network (need 'retrieval' or 'execute'):\n"
        + "\n".join(f"  - {stem}: {desc[:110]}" for stem, desc in offenders)
        + "\n\nFix by EITHER granting the 'retrieval' token (and recording the grantee in "
          "references/retrieval-transport-policy.md) OR rewording the charter to describe what "
          "the agent can actually do."
    )


# --- the detector must be able to fire ------------------------------------
#
# A guard test that cannot fail is decoration. These pin both directions of the discrimination.

@pytest.mark.parametrize("description", [
    "locates and verifies official documentation, api surfaces, and usage patterns for tools",
    "manages the bibliography and reference database — citation verification",
    "orchestrates web search and reputable-source rating",
])
def test_detector_fires_on_external_charters(description):
    assert claims_external_retrieval(description) is True


@pytest.mark.parametrize("description", [
    # The real technical-validator charter — local by construction, must stay clean.
    ("read-only audit agent that verifies technical accuracy — code examples, file excerpts, "
     "api references, and tool invocations match what exists on disk"),
    # Ambient vocabulary that must not trip it.
    ("detects logical conflicts across deliverables, agent documentation, reference files, "
     "and source material"),
    "fills in default template placeholders using the project's source materials",
    "read-only auditor that enforces modular architecture, file hygiene, and anti-sprawl rules",
])
def test_detector_stays_quiet_on_local_charters(description):
    assert claims_external_retrieval(description) is False


def test_locality_qualifier_suppresses_an_otherwise_matching_indicator():
    """The suppression rule itself, isolated — an indicator plus a qualifier is not a finding."""
    assert claims_external_retrieval("verifies official documentation") is True
    assert claims_external_retrieval("verifies official documentation on disk") is False


def test_the_guard_would_catch_a_regression(tmp_path):
    """Prove the file-walking half fires too, not just the string predicate.

    Reintroduces the exact historical defect — an external charter with `['read','search']` — in
    a scratch template tree and asserts it is detected.
    """
    template = tmp_path / "regressed.template.md"
    template.write_text(
        '---\n'
        'name: Regressed\n'
        'description: "Locates and verifies official documentation for tools"\n'
        "tools: ['read', 'search']\n"
        'model: ["m"]\n'
        '---\n\nBody.\n',
        encoding="utf-8",
    )
    parsed = _front_matter(template.read_text(encoding="utf-8"))
    assert parsed is not None
    description, tools = parsed
    assert claims_external_retrieval(description)
    assert not any(t in tools for t in _RETRIEVAL_CAPABLE)


def test_the_two_known_grantees_are_the_reason_this_passes():
    """Pins WHY the suite is currently clean, so silently revoking a grant fails loudly here
    rather than only in the transport-policy test."""
    for stem in ("tool-doc-researcher", "reference-manager"):
        matches = list(_TEMPLATES.rglob(f"{stem}.template.md"))
        assert matches, f"{stem} template not found"
        parsed = _front_matter(matches[0].read_text(encoding="utf-8"))
        assert parsed is not None
        description, tools = parsed
        assert claims_external_retrieval(description), (
            f"{stem}'s charter no longer reads as external — if that is intentional, this "
            f"assertion and its retrieval grant should both be revisited."
        )
        assert any(t in tools for t in _RETRIEVAL_CAPABLE)
