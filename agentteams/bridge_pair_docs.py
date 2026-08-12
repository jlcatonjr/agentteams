"""bridge_pair_docs.py — prose renderers for the bridge's framework-agnostic
pair-dir artifacts (quickstart-snippet.md, entrypoint.md, domain-boundary.md).

Carved from bridge.py (CH-07 line ceiling, 2026-08-11) so I3's generic-target
branching had headroom — same pattern already established by bridge_skills.py
and bridge_sources.py's own earlier carves. bridge.py re-exports these so
importers resolve them from agentteams.bridge unchanged.

Deliberately NOT moved here: `_render_target_files` (renders actual target-
framework entry files like CLAUDE.md — orchestration-coupled, stays in
bridge.py) and `_wrap_fence` (used by `_render_target_files`, not by anything
in this module).
"""

from __future__ import annotations

from agentteams.canonical import DEFAULT_CANONICAL_SUBDIR


def _render_quickstart(source_framework: str, target_framework: str) -> str:
    generic_note = ""
    if target_framework == "generic":
        # OPEN-3: a generic target has no native adapter of its own — point it
        # at the durable canonical tree (framework-neutral, hand-editable) so a
        # system with zero agentteams integration can still consume the team.
        generic_note = (
            "\n## No native adapter\n\n"
            f"This target has no dedicated agentteams framework adapter. The full\n"
            f"team is available in durable, hand-editable form under\n"
            f"`{DEFAULT_CANONICAL_SUBDIR}/` (if present) — `team.cai.json` plus one\n"
            "`agents/<slug>.md` file per agent. Read that tree directly"
        )
        if source_framework == "canonical":
            # The source itself already IS a canonical tree — suggesting a
            # command to regenerate it (from itself) is a degenerate,
            # self-referential no-op, not a useful instruction.
            generic_note += " (it is the source of this bridge).\n"
        else:
            generic_note += (
                ", or generate\nit with:\n"
                f"`agentteams --interop-from <source> --interop-source-framework {source_framework} "
                f"--framework canonical --output {DEFAULT_CANONICAL_SUBDIR}`\n"
            )
    goose_check_note = ""
    if target_framework == "goose":
        # W5: clarify that --bridge-check only validates source-side hashes, not
        # generated recipe YAML content.  Users sometimes assume bridge-check covers
        # the full output; this callout prevents false confidence.
        goose_check_note = (
            "\n## Bridge check scope\n\n"
            "`--bridge-check` verifies that source `.agent.md` files match their\n"
            "SHA-256 hashes recorded at bridge-generation time. It does NOT validate\n"
            "generated recipe YAML files, `.goosehints` enrichment, or AGENTS.md content.\n"
            "To validate recipe structure: `agentteams --framework goose --recipe-check --output <recipes-dir>`\n"
            "checks version string, no model: key, sub_recipe path resolution, and non-empty instructions.\n"
            "For full recipe generation (alternative to bridge): "
            "`agentteams --convert-from .github/agents --framework goose --output .goose/recipes`\n"
            "\n## CLI + MCP entry recipe\n\n"
            "The bridge emits `.goose/recipes/bridge-orchestrator.yaml` — run it with\n"
            "`goose run --recipe .goose/recipes/bridge-orchestrator.yaml` to start the\n"
            "bridged team WITH the `developer` (CLI) extension by default. Pass\n"
            "`--target-host-features bridge:<source>-to-goose:mcp` and build the source\n"
            "with an MCP token first to also wire the selected (first-party, read-only,\n"
            "orchestrator-scoped) MCP servers into that recipe.\n"
        )
    # 2026-08-10 finding: the retrieval-first paragraph below instructs the
    # reader to run an agentteams CLI command — appropriate when agentteams is
    # installed on the consumer side, wrong for `generic`, whose whole point is
    # a consumer with none. Swap it for framework-neutral guidance there.
    if target_framework == "generic":
        retrieval_paragraph = (
            "\n"
            "For 'where is X' / 'have we seen Y before' / thematic questions,\n"
            "check references/memory-index.json directly (durable prose: work\n"
            "summaries, plans, CHANGELOG) before grep, or ask a maintainer with\n"
            "agentteams installed to run a query on your behalf. See\n"
            "references/bridges/<src>-to-<target>/domain-boundary.md for the\n"
            "boundary vs project-level retrieval contracts.\n"
        )
    else:
        retrieval_paragraph = (
            "\n"
            "Retrieval-first: for 'where is X' / 'have we seen Y before' / thematic\n"
            "questions, run `agentteams --query-index \"<question>\" --query-strategy vector`\n"
            "before grep. The memory-index covers durable prose (work summaries,\n"
            "plans, CHANGELOG). See references/bridges/<src>-to-<target>/domain-boundary.md\n"
            "for the boundary vs project-level retrieval contracts.\n"
        )
    return (
        "# Bridge Quickstart Snippet\n\n"
        "Use this as your first prompt:\n\n"
        "```text\n"
        f"Use the {source_framework} agent infrastructure through this {target_framework} bridge.\n"
        "Start with the source orchestrator and follow source governance rules.\n"
        "Do not bypass orchestrator for multi-step, destructive, or cross-repo work.\n"
        + retrieval_paragraph
        + "```\n"
        + goose_check_note
        + generic_note
    )


def _render_entrypoint(source_framework: str, target_framework: str) -> str:
    # 2026-08-10 finding: the CLI-invocation retrieval section below assumes
    # agentteams is installed on the consumer side — wrong for `generic`.
    if target_framework == "generic":
        retrieval_section = (
            "## Retrieval Surface\n\n"
            "Before falling back to grep / filesystem search for thematic or\n"
            "cross-summary questions, check `references/memory-index.json`\n"
            "directly, or ask a maintainer with agentteams tooling installed to\n"
            "run an index query on your behalf. The index covers durable prose\n"
            "(work summaries, plans, CHANGELOG, references), NOT\n"
            "code. For code-symbol lookups, grep remains primary.\n\n"
        )
    else:
        retrieval_section = (
            "## Retrieval Surface\n\n"
            "Before falling back to grep / filesystem search for thematic or\n"
            "cross-summary questions, query the agentteams memory-index:\n\n"
            "```\n"
            "agentteams --query-index \"<the user's question>\" --query-strategy vector --query-k 5\n"
            "```\n\n"
            "Some installations require `--description PATH` for read-only queries —\n"
            "pass the project brief if so. The index covers durable prose (work\n"
            "summaries, plans, CHANGELOG, references), NOT code. For code-symbol\n"
            "lookups, grep remains primary.\n\n"
        )
    return (
        f"# Bridge Entrypoint: {source_framework} -> {target_framework}\n\n"
        "This is a lightweight interface bridge.\n"
        "Canonical agent definitions remain in source framework files.\n"
        "Use orchestrator-first routing for team-based work.\n"
        "\n"
        + retrieval_section
        + "See `domain-boundary.md` (this directory) for the boundary between the\n"
        "memory-index vector mode and project-level retrieval-integrator\n"
        "validation contracts — they address different questions and must not\n"
        "be conflated.\n"
    )


def _render_domain_boundary(source_framework: str, target_framework: str) -> str:
    return (
        "# Domain Boundary — Three Retrieval Surfaces\n\n"
        "AgentTeams exposes three **distinct** retrieval surfaces that address "
        "different questions and **must not be conflated**:\n\n"
        "1. **Memory-index** (`memory_index`, `--query-index`) — a stdlib-only "
        "sparse tf-idf vector-space ranking over **durable prose** (work "
        "summaries, CHANGELOG, durable plans). `vector_runtime_mode: "
        "sparse-tfidf-cosine`.\n"
        "2. **Code index** (`code_index`, `--query-code`) — a stdlib-only sparse "
        "tf-idf ranking over **code**: local scripts (`local-script`), the "
        "external API modules they import (`api-module`), and API documentation "
        "(`api-doc`), filterable with `--code-kind`. A **gitignored local "
        "cache** (`references/code-index/`), never committed.\n"
        "3. **Project retrieval-integrator** — a project-level validation "
        "contract (e.g. `mode: relational-metadata` against project data "
        "tables). Independent of both indexes above.\n\n"
        "The memory-index (prose) and the code-index (code) are siblings but "
        "cover disjoint content; neither participates in the single-slot "
        "project retrieval-integrator contract.\n\n"
        f"Bridge direction: `{source_framework}` → `{target_framework}`.\n"
    )
