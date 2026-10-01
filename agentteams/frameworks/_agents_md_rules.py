"""Fenced Constitutional Rules baseline for repo-root ``AGENTS.md`` (follow-up #16).

The instructions template's ``## Constitutional Rules`` list is USER-EDITABLE and unfenced
(``copilot-instructions.template.md`` section manifest): a fence spanning it would delete
project-added rules on merge. The consequence on the three ``AGENTS.md`` targets (goose, codex,
agents-md) was that a deleted rule never came back — an unfenced rule is preserved-forever.

This helper, shared by those three adapters' ``render_instructions_file`` (and NOT used for
``copilot-instructions.md``, which keeps its design), splits the list in two:

* a fenced ``constitutional_rules_baseline`` section — the template's rules **verbatim**,
  restored on every ``--update --merge`` and template-authoritative
  (``fences._TEMPLATE_AUTHORITATIVE_FENCES``), stating that it governs on conflict; and
* an empty, unfenced ``## Project Constitutional Rules (extensions)`` heading for the project.

**The baseline is sourced from the template only**, resolved with the manifest's
``auto_resolved_placeholders`` — never from the body handed in, which on the convert/interop
paths is an on-disk file. On-disk instruction files are agent-writable (C-4); sourcing the
baseline from one would launder an agent's edit into a fenced, authoritative-looking region.
When the manifest carries no ``auto_resolved_placeholders`` (the convert/interop stub manifests)
the body is returned unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentteams.render import resolve_placeholders

SECTION_ID = "constitutional_rules_baseline"
BASELINE_HEADING = "## Constitutional Rules (baseline)"
EXTENSIONS_HEADING = "## Project Constitutional Rules (extensions)"
_LEGACY_HEADING = "## Constitutional Rules"

_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "copilot-instructions.template.md"

#: Stated inside the fence. Refers to the extensions section by name, never by position: a merge
#: can place this fence anywhere in a deployed file.
GOVERNS_LINE = (
    "> This baseline is template-owned and restored on every `agentteams --update --merge`; it "
    "governs on any conflict. Projects may extend these rules in the Project Constitutional Rules "
    "(extensions) section; extensions may not weaken the Constitutional Core or this baseline."
)

_LEGACY_SECTION_RE = re.compile(
    r"^## Constitutional Rules[ \t]*\n(?P<body>.*?)(?=^---[ \t]*$|^#{1,2} |^<!--\s*AGENTTEAMS:|\Z)",
    re.MULTILINE | re.DOTALL,
)
_FENCE_RE = re.compile(
    rf"^<!--\s*AGENTTEAMS:BEGIN\s+{SECTION_ID}\b[^>]*-->.*?^<!--\s*AGENTTEAMS:END\s+{SECTION_ID}\s*-->[ \t]*\n?",
    re.MULTILINE | re.DOTALL,
)
_ANY_FENCE_RE = re.compile(
    r"^<!--\s*AGENTTEAMS:BEGIN\s+(\S+).*?^<!--\s*AGENTTEAMS:END\s+\1\s*-->",
    re.MULTILINE | re.DOTALL,
)


def _template_rules(placeholders: dict[str, str]) -> str:
    """The template's Constitutional Rules list body, placeholders resolved, stripped."""
    m = _LEGACY_SECTION_RE.search(_TEMPLATE.read_text(encoding="utf-8"))
    if m is None:  # pragma: no cover - guarded by tests on the shipped template
        raise ValueError(f"{_TEMPLATE.name}: no '{_LEGACY_HEADING}' section to baseline")
    return resolve_placeholders(m.group("body"), placeholders).strip()


def baseline_block(manifest: dict[str, Any]) -> str:
    """Render the fenced baseline section (BEGIN/END markers included, trailing newline).

    Args:
        manifest: Team manifest; ``auto_resolved_placeholders`` resolves the template rules.

    Returns:
        The fenced ``constitutional_rules_baseline`` block.

    Raises:
        ValueError: if the shipped template has lost its Constitutional Rules section.
    """
    rules = _template_rules(dict(manifest.get("auto_resolved_placeholders") or {}))
    return (
        f"<!-- AGENTTEAMS:BEGIN {SECTION_ID} v=1 -->\n"
        f"{BASELINE_HEADING}\n\n{GOVERNS_LINE}\n\n{rules}\n"
        f"<!-- AGENTTEAMS:END {SECTION_ID} -->\n"
    )


def apply_constitutional_rules_baseline(body: str, manifest: dict[str, Any]) -> str:
    """Replace the unfenced rules list in an ``AGENTS.md`` body with the fenced baseline.

    * An existing baseline fence is re-rendered from the template (its on-disk body is
      never trusted), and nothing else changes.
    * Otherwise the unfenced ``## Constitutional Rules`` section is replaced by the fenced
      baseline plus the empty extensions heading. When that section's rules differ from the
      template's (a customised list arriving on a convert path) it is kept, unfenced, after
      the fence rather than discarded.
    * No section and no fence: the body is returned unchanged.

    Args:
        body: The rendered instructions body.
        manifest: Team manifest. Without ``auto_resolved_placeholders`` (a convert/interop
            stub) the body is returned unchanged.

    Returns:
        The transformed body.

    Raises:
        ValueError: if the shipped template has lost its Constitutional Rules section.
    """
    if "auto_resolved_placeholders" not in manifest:
        return body
    block = baseline_block(manifest)
    if _FENCE_RE.search(body):
        return _FENCE_RE.sub(lambda _m: block, body, count=1)
    m = _LEGACY_SECTION_RE.search(body)
    if m is None or _inside_fence(body, m.start()):
        return body
    replacement = f"{block}\n{EXTENSIONS_HEADING}\n\n"
    own_rules = m.group("body").strip()
    template_rules = _template_rules(dict(manifest.get("auto_resolved_placeholders") or {}))
    if own_rules and own_rules != template_rules:
        replacement += f"{own_rules}\n\n"
    return body[: m.start()] + replacement + body[m.end():]


def _inside_fence(text: str, pos: int) -> bool:
    """True when *pos* falls inside any AGENTTEAMS fence in *text*."""
    return any(f.start() <= pos < f.end() for f in _ANY_FENCE_RE.finditer(text))


def duplicate_rules_notice(fresh: str, merged: str) -> str | None:
    """Notice for an ``AGENTS.md`` carrying both the baseline fence and the old unfenced list.

    Fires on EVERY run while the duplication remains (there is deliberately no state store
    recording that it was already shown). The old list is preserved because a project may have
    customised it; the baseline governs on conflict, so the operator should dedupe by hand.

    Args:
        fresh: The fresh render (the notice applies only when it carries the baseline fence).
        merged: The merged content about to be written.

    Returns:
        The notice text, or None when there is nothing to report.
    """
    if not _FENCE_RE.search(fresh) or not _FENCE_RE.search(merged):
        return None
    for m in re.finditer(rf"^{re.escape(_LEGACY_HEADING)}[ \t]*$", merged, re.MULTILINE):
        if not _inside_fence(merged, m.start()):
            return (
                f"duplicate Constitutional Rules list: the unfenced '{_LEGACY_HEADING}' section "
                f"remains beside the fenced '{BASELINE_HEADING}'. The baseline governs on "
                "conflict. Move any project-specific rules into a section after the baseline fence "
                f"(add a '{EXTENSIONS_HEADING}' heading if the file has none) and delete the old "
                "list (this notice repeats every run until you do)."
            )
    return None


__all__ = [
    "BASELINE_HEADING",
    "EXTENSIONS_HEADING",
    "SECTION_ID",
    "apply_constitutional_rules_baseline",
    "baseline_block",
    "duplicate_rules_notice",
]
