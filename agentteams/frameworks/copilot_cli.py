"""
copilot_cli.py — Framework adapter for GitHub Copilot CLI.

Agent files:  .github/agents/<slug>.agent.md  (same surface as VS Code)
Instructions: .github/copilot-instructions.md
Format:       YAML front matter + Markdown body, minus handoffs
Handoffs:     Stripped from BOTH the front-matter `handoffs:` block (the real
              generated shape — copilot-vscode's native inline representation,
              confirmed against .github/agents/*.agent.md; there is no
              parallel body heading for it) AND any body `## Handoffs` heading
              (defensive/backward-compat). Extracted handoffs are preserved in
              references/runtime-handoffs.json by the build pipeline, same as
              before this adapter converged onto the VS Code surface.

GitHub Copilot CLI custom-agent specification
----------------------------------------------
Source: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli
Cross-surface reference (shared with VS Code): https://docs.github.com/en/copilot/reference/custom-agents-configuration
(the former about-github-copilot-in-the-cli URL 301s to a generic
responsible-use page — verified 2026-08-15).

P1 CONVERGENCE (2026-08-15): current docs give the CLI the SAME custom-agent
surface as VS Code — `.github/agents/<slug>.agent.md` WITH YAML front matter.
This adapter previously emitted plain-Markdown prompts to `.github/copilot/`,
a path no upstream doc mentions and the modern CLI never reads. Fixed by
delegating rendering to CopilotVSCodeAdapter (see render_agent_file below).
Pre-existing `.github/copilot/*.md` files in already-deployed consumer repos
are left in place as harmless orphans — no auto-deletion.

Deliberately NOT full delegation, unlike agentteams/frameworks/codex.py's
delegation to agents_md.py (which is byte-identical in every respect,
including supports_handoffs()=False on both sides). Handoffs/argument-hint
are documented as unsupported outside the VS Code desktop extension — the
same distinction upstream marks with `target: vscode` — so CLI output must
NOT carry handoff content even though it now shares VS Code's front-matter
shape. supports_handoffs() stays False deliberately: it is correct, not
merely unreconciled, because handoffs are genuinely stripped below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter

from .base import FrameworkAdapter
from .copilot_vscode import CopilotVSCodeAdapter, _HANDOFF_SECTION_RE

_vscode = CopilotVSCodeAdapter()


class CopilotCLIAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "copilot-cli"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Delegate to CopilotVSCodeAdapter for the `.agent.md` shape, then strip handoffs.

        The CLI reads the same front-matter surface as VS Code (P1), but does not support
        handoffs/argument-hint (documented as VS-Code-desktop-only). Two independent strips are
        needed, not one: real generated agents carry their native handoff as a `handoffs:`
        front-matter block (copilot-vscode's own inline representation) with no corresponding
        body heading at all — confirmed directly against `.github/agents/navigator.agent.md`.
        `_strip_handoffs_section`'s regex only matches a body `## Handoffs` heading, so calling
        it alone left the front-matter block completely untouched: `_check_return_handoff_present`
        would report zero findings, but only because it never saw the leaked `agent: orchestrator`
        line search past a front matter it never inspects for THIS purpose — the exact blindness
        this adapter exists to avoid. `_HANDOFF_SECTION_RE` is scoped to the parsed front-matter
        text only (not the full file), matching `copilot_vscode._filter_yaml_team_refs`'s own
        scoping — an unscoped full-content regex risks matching a body line that happens to start
        with the literal text "handoffs:".
        """
        content = _vscode.render_agent_file(content, agent_slug, manifest)
        yaml_text, body_text = _parse_yaml_front_matter(content)
        if yaml_text is not None:
            stripped_yaml = _HANDOFF_SECTION_RE.sub("", yaml_text).rstrip("\n")
            if stripped_yaml != yaml_text.rstrip("\n"):
                content = f"---\n{stripped_yaml}\n---\n{body_text}"
        return self._strip_handoffs_section(content)

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        return _vscode.render_instructions_file(content, manifest)

    def get_file_extension(self, file_type: str) -> str:
        return _vscode.get_file_extension(file_type)

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """Same contract as VS Code — see that adapter for the current upstream keys."""
        return _vscode.required_front_matter_keys()

    def supports_handoffs(self) -> bool:
        """False, and correctly so post-convergence: render_agent_file strips handoffs, so
        there is genuinely nothing for a handoff-presence check to find."""
        return False

    def handoff_delivery_mode(self) -> str:
        return "manifest"

    def get_agents_dir(self, project_path: Path) -> Path:
        """Same physical path as VS Code (P1) — see references/agentteams-remediation-log.csv
        for the multi_sync path-collision analysis this convergence required."""
        return _vscode.get_agents_dir(project_path)
