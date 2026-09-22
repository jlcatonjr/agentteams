"""
format_spec.py — Single source of truth for per-provider FORMAT FACTS.

Two kinds of provider-format knowledge used to be duplicated and scattered:

* the **emit contract** — what each adapter writes (agent-file extension, the
  instructions filename, the front-matter keys) — encoded in
  ``agentteams/frameworks/*.py`` and, for the instructions filename / agent
  extension, repeated as bare string literals across roughly two dozen modules
  (≈45 occurrences of the instructions filename); and
* the **upstream-watch contract** — the provider's published-docs URL and the
  tokens/locations the freshness watcher looks for — encoded a *second* time in
  ``agentteams/framework_research.py``'s ``FRAMEWORK_REGISTRY``.

The two are related but genuinely distinct: a watch token like goose's
``instructions`` or codex's ``mcp_servers`` is a *documentation* signal, not a
front-matter key this project emits. This module holds BOTH, per provider, in
one typed table so the watcher can no longer silently drift from the emitter and
a provider-format update has a single home.

Purity rule: this module imports NOTHING from the adapter classes (they import
*from* here), so there is no cycle. ``registry.py`` continues to own the
framework-id → adapter-class map; this module owns the format facts.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Canonical filename / extension literals.
#
# These strings appear as bare literals across roughly two dozen modules (≈45
# occurrences of the instructions filename). They remain literals at most *match*
# sites (rewriting every ``endswith("copilot-instructions.md")`` carries regression
# risk for no behaviour change), but this module is now the CANONICAL definition:
# the authoritative EMIT sites use these constants — ``output_plan.py`` for the
# filename, the Copilot adapter for the extension — and
# ``tests/test_format_spec_single_source.py`` guards that they stay single-sourced.
# ---------------------------------------------------------------------------

#: The GitHub Copilot custom-instructions filename (emitted at ``.github/copilot-instructions.md``
#: and remapped per framework — e.g. Claude → ``CLAUDE.md``, agents-md/codex → ``AGENTS.md``).
COPILOT_INSTRUCTIONS_FILENAME = "copilot-instructions.md"

#: The Copilot/VS-Code agent-file extension (``<slug>.agent.md``). The neutral base and every
#: adapter whose agent files are Markdown use it; goose overrides to recipe YAML.
AGENT_FILE_EXTENSION = ".agent.md"


@dataclass(frozen=True)
class FormatSpec:
    """Per-provider format facts shared by the emit adapter and the doc watcher.

    Attributes:
        research_id: The id used by ``framework_research`` snapshots and
            ``FRAMEWORK_REGISTRY`` (underscored, e.g. ``copilot_vscode``).
        adapter_id: The id used by ``registry.FRAMEWORKS`` (hyphenated, e.g.
            ``copilot-vscode``). Links a spec to its live adapter class.
        label: Human-readable framework name for reports.
        source_url: The provider's published documentation URL the watcher fetches.
        expert_ref: Repo-relative path of the expert reference the watcher annotates.
        expected_doc_tokens: Tokens the watcher expects to see in the provider's
            *published docs* (a documentation signal — NOT necessarily the keys
            this project emits). Empty when the standard has no schema (agents-md).
        expected_locations: File/dir path strings the watcher expects the docs to mention.
        emitted_front_matter_keys: The front-matter keys this project's adapter
            actually WRITES. Cross-checked against the adapter's
            ``required_front_matter_keys()`` by the single-source guard test.
            Empty for frameworks whose agent files carry no front matter
            (goose recipes, agents-md/codex plain Markdown).
    """

    research_id: str
    adapter_id: str
    label: str
    source_url: str
    expert_ref: str
    expected_doc_tokens: tuple[str, ...] = ()
    expected_locations: tuple[str, ...] = ()
    emitted_front_matter_keys: tuple[str, ...] = ()


#: Canonical Claude Code sub-agents doc URL (re-exported by ``framework_research``
#: as ``CLAUDE_DOC_URL`` for back-compat).
CLAUDE_DOC_URL = "https://docs.anthropic.com/en/docs/claude-code/sub-agents"


FORMAT_SPECS: dict[str, FormatSpec] = {
    "claude": FormatSpec(
        research_id="claude",
        adapter_id="claude",
        label="Claude Code Sub-Agents",
        source_url=CLAUDE_DOC_URL,
        expert_ref="references/claude-agent-infrastructure-expert.md",
        expected_doc_tokens=("name", "description", "tools", "model"),
        expected_locations=(".claude/agents", "CLAUDE.md"),
        emitted_front_matter_keys=("name", "description", "tools"),
    ),
    "copilot_vscode": FormatSpec(
        research_id="copilot_vscode",
        adapter_id="copilot-vscode",
        label="GitHub Copilot — VS Code Custom Agents",
        # custom-agents is the current page; custom-chat-modes is explicitly legacy
        # (verified 2026-08-15, agent-doc-optimal-structure plan).
        source_url="https://code.visualstudio.com/docs/copilot/customization/custom-agents",
        expert_ref="references/copilot-vscode-agent-infrastructure-expert.md",
        expected_doc_tokens=("description", "tools", "model"),
        expected_locations=(".github/agents",),
        emitted_front_matter_keys=("name", "description", "user-invocable", "tools", "model"),
    ),
    "copilot_cli": FormatSpec(
        research_id="copilot_cli",
        adapter_id="copilot-cli",
        label="GitHub Copilot — CLI Custom Agents",
        # The old about-github-copilot-in-the-cli URL 301s to a generic
        # responsible-use page (verified 2026-08-15). The CLI's agent surface is
        # .github/agents/*.agent.md — same directory as copilot_vscode. Our
        # adapter converged onto it 2026-08-15 (P1, closed — see the expert
        # reference); .github/copilot/ is no longer emitted.
        source_url="https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli",
        expert_ref="references/copilot-cli-agent-infrastructure-expert.md",
        expected_doc_tokens=("description", "tools", "model"),
        expected_locations=(".github/agents",),
        emitted_front_matter_keys=("name", "description", "user-invocable", "tools", "model"),
    ),
    "goose": FormatSpec(
        research_id="goose",
        adapter_id="goose",
        label="Goose (AAIF) Recipes",
        # Docs moved to goose-docs.ai when Goose joined AAIF (2026-04);
        # block.github.io URLs are dead.
        source_url="https://goose-docs.ai/docs/guides/recipes/recipe-reference/",
        expert_ref="references/goose-agent-infrastructure-expert.md",
        expected_doc_tokens=("title", "description", "instructions", "prompt"),
        expected_locations=(".goose/recipes", "AGENTS.md", ".goosehints"),
        emitted_front_matter_keys=(),  # recipe YAML, not a Markdown header
    ),
    "agents_md": FormatSpec(
        research_id="agents_md",
        adapter_id="agents-md",
        label="AGENTS.md Cross-Tool Standard",
        source_url="https://agents.md",
        expert_ref="references/agents-md-agent-infrastructure-expert.md",
        # The standard has no schema; watch for structural locations instead.
        expected_doc_tokens=(),
        expected_locations=("AGENTS.md",),
        emitted_front_matter_keys=(),  # plain Markdown, no front matter
    ),
    "codex": FormatSpec(
        research_id="codex",
        adapter_id="codex",
        label="OpenAI Codex CLI",
        # developers.openai.com/codex 308s to learn.chatgpt.com (verified 2026-08-15).
        source_url="https://learn.chatgpt.com/docs/agent-configuration/agents-md",
        expert_ref="references/codex-agent-infrastructure-expert.md",
        expected_doc_tokens=("project_doc_max_bytes", "mcp_servers"),
        expected_locations=("AGENTS.md", ".codex"),
        emitted_front_matter_keys=(),  # AGENTS.md rendering inherited from agents-md
    ),
}


def spec_by_adapter_id(adapter_id: str) -> FormatSpec | None:
    """Return the FormatSpec whose ``adapter_id`` matches, or ``None``."""
    for spec in FORMAT_SPECS.values():
        if spec.adapter_id == adapter_id:
            return spec
    return None
