"""Placeholder documentation must not drift from the templates (issue #392 item 9).

Two checks:

* Every ``{MANUAL:NAME}`` that ``PLACEHOLDER-CONVENTIONS.md`` documents is used by a template or by the
  pipeline, except a pinned set of illustrative examples.
* Every agent that ``agent-updater``'s Change-to-Agent Mapping lists for a placeholder path actually
  references that placeholder in its template.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "agentteams" / "templates"
CONVENTIONS = TEMPLATES / "PLACEHOLDER-CONVENTIONS.md"
AGENT_UPDATER = TEMPLATES / "universal" / "agent-updater.template.md"

# Illustrative examples in the conventions file: syntax and kinds of manual input, not placeholders any
# template emits. The size is pinned so a dead placeholder can't be added here unnoticed.
ILLUSTRATIVE = frozenset({"PLACEHOLDER_NAME", "SPECIFIC_FILE_PATHS", "API_ENDPOINT_NAMES", "EXTERNAL_REPO_PATHS"})
_DOC_FILES = {CONVENTIONS.name, "AUTHORING-GUIDE.md"}


def _usage_corpus() -> str:
    parts = [p.read_text(encoding="utf-8", errors="ignore") for p in TEMPLATES.rglob("*")
             if p.is_file() and p.name not in _DOC_FILES and "__pycache__" not in p.parts]
    parts += [p.read_text(encoding="utf-8") for p in (REPO / "agentteams").rglob("*.py")]
    return "\n".join(parts)


def test_illustrative_allowlist_is_pinned() -> None:
    assert len(ILLUSTRATIVE) == 4


def test_documented_manual_placeholders_are_used() -> None:
    documented = set(re.findall(r"\{MANUAL:([A-Z0-9_]+)\}", CONVENTIONS.read_text(encoding="utf-8")))
    assert ILLUSTRATIVE <= documented, "an allowlisted example is no longer documented; shrink the allowlist"
    corpus = _usage_corpus()
    dead = sorted(n for n in documented - ILLUSTRATIVE if not re.search(rf"\b{n}\b", corpus))
    assert not dead, f"PLACEHOLDER-CONVENTIONS.md documents placeholders nothing uses: {dead}"


def _template_for(slug: str) -> Path | None:
    for sub in ("universal", "domain"):
        path = TEMPLATES / sub / f"{slug}.template.md"
        if path.is_file():
            return path
    return None


def test_change_to_agent_mapping_names_only_agents_that_use_the_placeholder() -> None:
    text = AGENT_UPDATER.read_text(encoding="utf-8")
    section = text.split("## Change-to-Agent Mapping", 1)[1].split("\n## ", 1)[0]
    checked = 0
    for line in section.splitlines():
        m = re.match(r"\|\s*`\{([A-Z0-9_]+)\}`\s*\|(.*)\|\s*$", line)
        if not m:
            continue
        token, agents = "{" + m.group(1) + "}", re.findall(r"`@([a-z0-9-]+)`", m.group(2))
        assert agents, f"row for {token} lists no agents"
        for slug in agents:
            template = _template_for(slug)
            assert template is not None, f"{token} row names @{slug}, which has no template"
            assert token in template.read_text(encoding="utf-8"), (
                f"agent-updater says @{slug} uses {token}, but {template.relative_to(REPO)} does not reference it")
            checked += 1
    assert checked, "no placeholder rows found in the Change-to-Agent Mapping"
