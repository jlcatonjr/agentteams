"""
output_plan.py — plan the set of output files (agents, instructions, references,
builder) for a team manifest.

Extracted from analyze.py (CH-07). analyze re-exports ``_plan_output_files`` so
``analyze._plan_output_files`` resolves unchanged. The three analyze-owned symbols
it needs (GOVERNANCE_AGENTS, ALWAYS_INCLUDED_DOMAIN_AGENTS, _dedupe_keep_order) are
imported lazily inside the function to keep the module graph acyclic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["_plan_output_files"]


def _plan_output_files(
    archetypes: list[str],
    tool_agents: list[dict[str, Any]],
    reference_tools: list[dict[str, Any]],
    components: list[dict[str, Any]],
    framework: str,
) -> list[dict[str, Any]]:
    """Plan the list of files the emit phase will generate."""
    # These three live in analyze.py (reused across it); import lazily here so
    # output_plan does not import analyze at module load (avoids a cycle — analyze
    # imports _plan_output_files from this module).
    from agentteams.analyze import (
        ALWAYS_INCLUDED_DOMAIN_AGENTS,
        GOVERNANCE_AGENTS,
        _dedupe_keep_order,
    )
    files: list[dict[str, Any]] = []
    agents_dir = "universal/"
    domain_dir = "domain/"

    # Governance agents (always)
    for slug in ["orchestrator"] + GOVERNANCE_AGENTS:
        files.append({
            "path": f"{slug}.agent.md",
            "template": f"{agents_dir}{slug}.template.md",
            "type": "agent",
            "component_slug": None,
        })

    # Domain agents (always-included operational + selected archetypes)
    for slug in _dedupe_keep_order(ALWAYS_INCLUDED_DOMAIN_AGENTS + list(archetypes)):
        files.append({
            "path": f"{slug}.agent.md",
            "template": f"{domain_dir}{slug}.template.md",
            "type": "agent",
            "component_slug": None,
        })

    # Operational tool docs (specialist tier). Tools are resources agents USE,
    # never agents themselves. For Claude they become skill documents under
    # `.claude/skills/`; for Copilot (no skills concept) they become reference
    # documents under `references/`. Operational depth is preserved via the
    # category-specific `.doc` templates (no agent front matter / handoffs).
    for ta in tool_agents:
        category = ta.get("tool_category", "other")
        category_template = f"{domain_dir}tool-{category}.doc.template.md"
        fallback_template = f"{domain_dir}tool-specific.doc.template.md"
        base = ta["slug"][len("tool-"):] if ta["slug"].startswith("tool-") else ta["slug"]
        if framework == "claude":
            # Flat skill layout, matching the existing recall/todo-from-plan
            # skills. `../skills/` resolves to `.claude/skills/` (agents dir is
            # `.claude/agents/`), mirroring how `../CLAUDE.md` is emitted.
            doc_path = f"../skills/{ta['slug']}.md"
            doc_type = "skill"
        else:
            doc_path = f"references/ref-{base}-reference.md"
            doc_type = "reference"
        files.append({
            "path": doc_path,
            "template": category_template,
            "fallback_template": fallback_template,
            "type": doc_type,
            "component_slug": None,
            "tool_slug": ta["slug"],
        })

    # Reference files (reference tier — lightweight library/framework docs)
    for rt in reference_tools:
        files.append({
            "path": f"references/{rt['slug']}-reference.md",
            "template": f"{domain_dir}tool-reference.template.md",
            "type": "reference",
            "component_slug": None,
            "tool_slug": rt["slug"],
        })

    # Code-hygiene companion reference file (always)
    files.append({
        "path": "references/code-hygiene-rules.reference.md",
        "template": f"{domain_dir}code-hygiene-rules-reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    # Unix-philosophy mapping reference (always — @code-hygiene design-principle
    # reference linked from code-hygiene.template.md's "Philosophical Alignment" section).
    files.append({
        "path": "references/unix-philosophy-mapping.reference.md",
        "template": f"{domain_dir}unix-philosophy-mapping-reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    # Security vulnerability watch references (always)
    files.append({
        "path": "references/security-vulnerability-watch.reference.md",
        "template": f"{agents_dir}security-vulnerability-watch.reference.template.md",
        "type": "artifact",
        "component_slug": None,
    })
    files.append({
        "path": "references/security-vulnerability-watch.json",
        "template": f"{agents_dir}security-vulnerability-watch.json.template",
        "type": "artifact",
        "component_slug": None,
    })
    # OS security hardening references (always — curated platform-hardening
    # baselines linked from security.template.md, each gated to its deployment OS).
    files.append({
        "path": "references/security-linux-hardening.reference.md",
        "template": f"{agents_dir}security-linux-hardening.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })
    files.append({
        "path": "references/security-macos-hardening.reference.md",
        "template": f"{agents_dir}security-macos-hardening.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })
    files.append({
        "path": "references/security-windows-hardening.reference.md",
        "template": f"{agents_dir}security-windows-hardening.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    # AI bad-habits catalog (always — @code-hygiene CH-25 + @security screening)
    files.append({
        "path": "references/ai-bad-habits-watch.reference.md",
        "template": f"{agents_dir}ai-bad-habits-watch.reference.template.md",
        "type": "artifact",
        "component_slug": None,
    })

    # Framework watch reference (always — daily-pipeline transmission target)
    files.append({
        "path": "references/framework-watch.reference.md",
        "template": f"{agents_dir}framework-watch.reference.template.md",
        "type": "artifact",
        "component_slug": None,
    })

    # Adjacent repository registry (always — repo-liaison reference)
    files.append({
        "path": "references/adjacent-repos.md",
        "template": f"{agents_dir}adjacent-repos.reference.template.md",
        "type": "artifact",
        "component_slug": None,
    })

    # Git operations merge workflow reference (always)
    files.append({
        "path": "references/github-workflows-merge.reference.md",
        "template": f"{agents_dir}github-workflows-merge.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    # Work summary references (always — work-summarizer support)
    files.append({
        "path": "references/work-summary-spec.reference.md",
        "template": f"{agents_dir}work-summary-spec.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })
    files.append({
        "path": "references/work-summary-tooling.reference.md",
        "template": f"{agents_dir}work-summary-tooling.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })
    files.append({
        "path": "references/work-summary-backfill.reference.md",
        "template": f"{agents_dir}work-summary-backfill.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    # Parallelization reference (always — @orchestrator Workflow 0A support;
    # documents the independence heuristic + parallel_plan analyzer for all
    # frameworks, including those with no skills concept).
    files.append({
        "path": "references/parallelization.reference.md",
        "template": f"{agents_dir}parallelization.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    # Post-Deliverable Retrospective reference (always — @orchestrator Workflow 1/2/3
    # support; @repo-liaison Protocol 5 support).
    files.append({
        "path": "references/retrospective-remediation.reference.md",
        "template": f"{agents_dir}retrospective-remediation.reference.template.md",
        "type": "reference",
        "component_slug": None,
    })

    if "retrieval-integrator" in archetypes:
        files.append({
            "path": "references/retrieval-integration.reference.md",
            "template": f"{agents_dir}retrieval-integration.reference.template.md",
            "type": "reference",
            "component_slug": None,
        })
        files.append({
            "path": "references/retrieval-trigger-contract.reference.md",
            "template": f"{agents_dir}retrieval-trigger-contract.reference.template.md",
            "type": "reference",
            "component_slug": None,
        })

    # Workstream experts
    for comp in components:
        files.append({
            "path": f"{comp['slug']}-expert.agent.md",
            "template": "workstream-expert.template.md",
            "type": "agent",
            "component_slug": comp["slug"],
        })

    # Framework instructions file in repository root
    instructions_path = "../copilot-instructions.md"
    if framework == "claude":
        instructions_path = "../CLAUDE.md"

    files.append({
        "path": instructions_path,
        "template": "copilot-instructions.template.md",
        "type": "instructions",
        "component_slug": None,
    })

    # Builder agent (framework-native)
    builder_templates = {
        "copilot-vscode": "builder/team-builder-copilot-vscode.template.md",
        "copilot-cli": "builder/team-builder-copilot-cli.template.md",
        "claude": "builder/team-builder-claude.template.md",
        "goose": "builder/team-builder-goose.template.md",
        # agents-md reuses the copilot-vscode builder source; AgentsMdAdapter
        # .render_builder_file neutralizes it into a plain .agents/ Markdown file.
        "agents-md": "builder/team-builder-copilot-vscode.template.md",
    }
    if framework in builder_templates:
        files.append({
            "path": "team-builder.agent.md",
            "template": builder_templates[framework],
            "type": "builder",
            "component_slug": None,
        })

    # SETUP-REQUIRED.md
    files.append({
        "path": "SETUP-REQUIRED.md",
        "template": "",
        "type": "setup-required",
        "component_slug": None,
    })

    # Team topology graph — generated post-render in build_team.py
    files.append({
        "path": "references/pipeline-graph.md",
        "template": "",
        "type": "graph",
        "component_slug": None,
    })
    # Companion SVG diagrams referenced by the graph .md (raw XML, no content fence)
    files.append({
        "path": "references/pipeline-graph.svg",
        "template": "",
        "type": "graph-svg",
        "component_slug": None,
    })
    files.append({
        "path": "references/pipeline-handoffs.svg",
        "template": "",
        "type": "graph-svg",
        "component_slug": None,
    })

    # Content enricher — user-invokable agent for filling MANUAL placeholders
    files.append({
        "path": "content-enricher.agent.md",
        "template": "domain/content-enricher.template.md",
        "type": "agent",
        "component_slug": None,
    })

    return files


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
