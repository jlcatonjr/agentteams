"""#635: a universal reference template names only agents every team has, unless it says the mention is conditional.

Universal reference templates are rendered into every team. An example that names an agent only some teams get
(`@pr-manager` exists only with GitHub management on) reads as a dangling pointer in the others. Each backticked
`@slug` in a universal reference template must therefore be a universal agent, sit in a sentence that marks it
conditional, or be one of the mentions listed in ``_ALLOWED`` (today's existing ones, kept so this test guards new
text without rewriting unrelated templates).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_UNIVERSAL = _ROOT / "agentteams" / "templates" / "universal"

_SLUG = re.compile(r"`@([a-z0-9-]+)`")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`*(])")
_CONDITIONAL = ("where the team has one", "when present", "if in team")

# (template file, slug) pairs that predate this test. Each is a domain agent most teams have, or a reference that
# describes a specific agent's workflow. Review an entry before adding one: it exempts that slug in the whole file.
_ALLOWED = frozenset({
    ("api-impact.reference.template.md", "module-doc-author"),
    ("api-impact.reference.template.md", "module-doc-validator"),
    ("api-impact.reference.template.md", "technical-validator"),
    ("framework-watch.reference.template.md", "docs-research-expert"),
    ("framework-watch.reference.template.md", "framework-adapters-expert"),
    ("security-infrastructure-layers.reference.template.md", "technical-validator"),
    ("security-vulnerability-watch.reference.template.md", "technical-validator"),
    ("work-summary-backfill.reference.template.md", "work-summarizer"),
    ("work-summary-spec.reference.template.md", "technical-validator"),
    ("work-summary-trigger-catalog.reference.template.md", "work-summarizer"),
})


def _universal_agents() -> set[str]:
    return {p.name[: -len(".template.md")] for p in _UNIVERSAL.glob("*.template.md")
            if not p.name.endswith(".reference.template.md")}


def _violations(name: str, text: str, universal: set[str]) -> list[tuple[str, str]]:
    """Return (slug, sentence) for each unconditional mention of a non-universal agent not in ``_ALLOWED``."""
    found = []
    for sentence in _SENTENCE_END.split(" ".join(text.split())):
        lowered = sentence.lower()
        for slug in _SLUG.findall(sentence):
            if slug in universal or (name, slug) in _ALLOWED:
                continue
            if any(marker in lowered for marker in _CONDITIONAL):
                continue
            found.append((slug, sentence))
    return found


def test_universal_reference_slugs_are_universal_or_conditional():
    universal = _universal_agents()
    templates = sorted(_UNIVERSAL.glob("*.reference.template.md"))
    assert "orchestrator" in universal and "git-operations" in universal
    assert templates, "no universal reference templates found: the check would pass vacuously"
    assert any(_SLUG.search(p.read_text(encoding="utf-8")) for p in templates)
    bad = {p.name: v for p in templates if (v := _violations(p.name, p.read_text(encoding="utf-8"), universal))}
    assert not bad, f"non-universal agent named unconditionally: {bad}"


def test_an_unconditional_unknown_agent_fails_and_a_conditional_one_passes():
    universal = _universal_agents()
    assert _violations("synthetic.reference.template.md", "Ask `@not-an-agent` first.", universal) == [
        ("not-an-agent", "Ask `@not-an-agent` first.")]
    assert _violations("synthetic.reference.template.md",
                       "Ask `@not-an-agent` where the team has one. Then `@orchestrator` decides.", universal) == []


def test_operator_decision_example_is_universal():
    tpl = (_UNIVERSAL / "operator-decision.reference.template.md").read_text(encoding="utf-8")
    goldens = sorted(_ROOT.glob("examples/*/expected/references/operator-decision.reference.md"))
    assert goldens
    for text in [tpl] + [g.read_text(encoding="utf-8") for g in goldens]:
        flat = " ".join(text.split())
        assert "(for example `@git-operations`, or `@pr-manager`'s disposition prompt where the team has one)" in flat
