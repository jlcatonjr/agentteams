"""
goose.py — Framework adapter for Block / AAIF "Goose" recipes.

Agent files:  .goose/recipes/<slug>.yaml   (Goose recipe format)
Instructions: AGENTS.md (repo root) + .goosehints (repo root, integrates AGENTS.md)
Format:       Recipe YAML (title, description, instructions, extensions, sub_recipes)
Delegation:   orchestrator handoffs -> sub_recipes  (one delegation layer, native);
              every other agent's handoffs -> `summon` `load(...)` references.

Why this mapping
----------------
agentteams produces an orchestrator that delegates to specialist agents, and
specialists in turn hand off to cross-cutting agents (security, git-operations,
cleanup, ...). Goose caps delegation at ONE layer — a sub-recipe/subagent cannot
spawn another (source: SessionType::SubAgent guard in goose's summon platform
extension). We therefore reinterpret the agentteams handoff DAG faithfully:

  * Depth 0 (orchestrator) -> a recipe declaring `summon` + `sub_recipes` of its
    direct handoff targets. This is TRUE delegation (isolated child sessions).
  * Depth 1+ (every other agent) -> a recipe. Its own handoffs are depth-2 by
    construction, so they are rewritten as `summon` `load("<slug>")` directives
    inside the recipe instructions: the agent loads the referenced recipe's
    content into ITS OWN context instead of spawning a nested delegate. No
    handoff edge is dropped; deeper structure is preserved as references.

See references/plans/goose-integration.plan.md §10 for the full standardized model. The Goose
recipe schema is pinned at version 1.0.0; YAML is emitted by hand (the codebase
intentionally avoids a YAML dependency and parses front matter with regex).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import FrameworkAdapter

# ---------------------------------------------------------------------------
# Recipe constants
# ---------------------------------------------------------------------------

_RECIPE_VERSION = "1.0.0"

# Default probe prompt emitted in the orchestrator recipe so `goose run --recipe`
# can be invoked non-interactively in CI without combining with --text (which is
# mutually exclusive with --recipe in Goose CLI).  See W6 in the integration plan.
_ORCHESTRATOR_PROBE_PROMPT = (
    "State your role. For the request 'produce a deliverable for this team', "
    "name the correct workflow and the first agent you would route to."
)

# Regex patterns for structural recipe validation (_validate_recipe_yaml).
_RECIPE_VERSION_RE = re.compile(r'^version:\s*"1\.0\.0"', re.MULTILINE)
_RECIPE_MODEL_KEY_RE = re.compile(r"^\s*model:", re.MULTILINE)
_RECIPE_TITLE_RE = re.compile(r'^title:\s*".+?"', re.MULTILINE)
_RECIPE_INSTRUCTIONS_RE = re.compile(r"^instructions:\s*\|", re.MULTILINE)
_RECIPE_SUB_PATH_RE = re.compile(r'path:\s*"([^"]+)"')
# Phase-4a: a `parameters:` block (when present) must list `- key:` entries.
_RECIPE_PARAMETERS_RE = re.compile(r"^parameters:\s*$", re.MULTILINE)
_RECIPE_PARAM_KEY_RE = re.compile(r'^\s+-\s+key:\s*"', re.MULTILINE)
# Phase-4b: a `response:` block (when present) must carry a non-empty `json_schema:`.
_RECIPE_RESPONSE_RE = re.compile(r"^response:\s*$", re.MULTILINE)
_RECIPE_JSON_SCHEMA_RE = re.compile(r"^\s+json_schema:\s*\S", re.MULTILINE)

# Forbidden-shape guards for emitted recipes (goose-integration.plan §6.5 gotchas).
# These have no other validator backing, so a typo would otherwise pass silently.
# The optional ``-\s*`` prefix matches a key that is the first entry of a list item
# (``  - type: sse``); ``\b`` after ``sse`` avoids matching a uri ending in ``/sse``.
_RECIPE_FORBIDDEN_ENVS_RE = re.compile(r"^\s*(-\s*)?envs:", re.MULTILINE)        # use env_keys
_RECIPE_FORBIDDEN_SSE_RE = re.compile(r'^\s*(-\s*)?type:\s*["\']?sse\b', re.MULTILINE)  # use streamable_http
_RECIPE_FORBIDDEN_CONTEXT_RE = re.compile(r"^\s*context:", re.MULTILINE)     # not a recipe field

# MCP-extension wiring (opt-in via the goose:mcp host-feature token).
_GOOSE_MCP_TOKEN = "goose:mcp"
_MCP_EXT_TIMEOUT = 300

# Regex to locate AGENTTEAMS authority_hierarchy HTML-comment fences in body text.
# These appear in copilot-instructions.md and may appear in orchestrator bodies.
_AUTH_HIER_FENCE_RE = re.compile(
    r"<!--\s*AGENTTEAMS:BEGIN\s+authority_hierarchy\s*-->(.*?)"
    r"<!--\s*AGENTTEAMS:END\s+authority_hierarchy\s*-->",
    re.DOTALL,
)


def _extract_authority_hierarchy(source_instructions: str) -> str:
    """Extract authority_hierarchy fenced block content from copilot-instructions.md."""
    m = _AUTH_HIER_FENCE_RE.search(source_instructions)
    return m.group(1) if m else ""


def _substitute_authority_hierarchy(body: str, hierarchy_content: str) -> str:
    """Replace the authority_hierarchy fenced block in body with project-specific content.

    No-op when hierarchy_content is empty or no fence is found in body.
    """
    if not hierarchy_content:
        return body
    replacement = (
        "<!-- AGENTTEAMS:BEGIN authority_hierarchy -->"
        + hierarchy_content
        + "<!-- AGENTTEAMS:END authority_hierarchy -->"
    )
    new_body, n = _AUTH_HIER_FENCE_RE.subn(replacement, body, count=1)
    return new_body if n > 0 else body


def _goosehints_content(project_name: str) -> str:
    """Return the .goosehints integrator content, parameterized to the project name.

    The Session Startup block (below the managed fence) instructs Goose to
    self-load the orchestrator recipe at plain-session start — VSCode extension,
    `goose session`, or any ACP session — without requiring `goose run --recipe`.
    It lives outside the managed fence so it survives --bridge-merge and --merge
    cycles unchanged.  See W7 in the integration plan.
    """
    return (
        "@AGENTS.md\n\n"
        "<!-- AGENTTEAMS:BEGIN goose-operational-notes -->\n"
        "## Goose operational notes (generated by agentteams)\n\n"
        "This team's canonical brief is AGENTS.md (included above). Entry point:\n\n"
        "    goose run --recipe .goose/recipes/orchestrator.yaml\n\n"
        "Delegation model:\n"
        "- The orchestrator recipe delegates to specialist recipes via `sub_recipes`\n"
        "  (each runs in its own isolated session).\n"
        "- Goose forbids nested delegation, so specialists reference cross-cutting\n"
        '  agents in-context with the `summon` `load("<recipe-slug>")` tool rather\n'
        "  than spawning a sub-agent. The relevant `load(...)` calls are listed in\n"
        '  each specialist recipe under "Delegation & references (Goose)".\n'
        "<!-- AGENTTEAMS:END goose-operational-notes -->\n\n"
        "### Session Startup (Mandatory)\n\n"
        f"**At the start of every session** in {project_name}, before responding to any user request:\n\n"
        "1. Read `.goose/recipes/orchestrator.yaml` using your file tool — this is your complete\n"
        "   role definition, constitutional rules, routing table, and all workflows\n"
        "2. Adopt the Orchestrator identity, constitutional rules, and routing logic from that\n"
        "   file for the entire session\n"
        "3. You are the **Orchestrator** for this project; do not perform domain work directly —\n"
        "   route all requests through the appropriate specialist agent\n\n"
        "This startup read applies to plain interactive sessions and IDE extension sessions equally.\n"
        "`goose run --recipe .goose/recipes/orchestrator.yaml` is the alternative that pre-loads\n"
        "automatically without the startup read.\n"
    )


def _validate_recipe_yaml(yaml_text: str, recipes_dir: Path | None = None) -> list[str]:
    """Return structural violations found in a Goose recipe YAML string.

    Uses regex-only parsing — the codebase intentionally avoids a YAML dependency.
    Pass ``recipes_dir`` to also resolve ``sub_recipes`` path references on disk.
    """
    violations: list[str] = []
    if not _RECIPE_VERSION_RE.search(yaml_text):
        violations.append('missing or wrong version: field (expected version: "1.0.0")')
    if _RECIPE_MODEL_KEY_RE.search(yaml_text):
        violations.append("forbidden model: key (Goose infers model from session config)")
    if not _RECIPE_TITLE_RE.search(yaml_text):
        violations.append("missing or empty title: field")
    if not _RECIPE_INSTRUCTIONS_RE.search(yaml_text):
        violations.append("missing instructions: literal block scalar (instructions: |)")
    if _RECIPE_FORBIDDEN_ENVS_RE.search(yaml_text):
        violations.append("forbidden envs: key (recipe extensions use env_keys, not envs)")
    if _RECIPE_FORBIDDEN_SSE_RE.search(yaml_text):
        violations.append("forbidden type: sse (use streamable_http; sse is deprecated)")
    if _RECIPE_FORBIDDEN_CONTEXT_RE.search(yaml_text):
        violations.append("forbidden context: field (not a recipe field)")
    if _RECIPE_PARAMETERS_RE.search(yaml_text) and not _RECIPE_PARAM_KEY_RE.search(yaml_text):
        violations.append("parameters: block present but lists no '- key:' entries")
    if _RECIPE_RESPONSE_RE.search(yaml_text) and not _RECIPE_JSON_SCHEMA_RE.search(yaml_text):
        violations.append("response: block present but has no non-empty json_schema: value")
    if recipes_dir is not None:
        for path_val in _RECIPE_SUB_PATH_RE.findall(yaml_text):
            resolved = (recipes_dir / path_val).resolve()
            if not resolved.exists():
                violations.append(f"sub_recipe path not found: {path_val}")
    return violations


class GooseAdapter(FrameworkAdapter):

    @property
    def framework_id(self) -> str:
        return "goose"

    def render_agent_file(self, content: str, agent_slug: str, manifest: dict[str, Any]) -> str:
        """Transform a rendered agent (VS Code-style markdown) into a Goose recipe."""
        name, description = _extract_name_description(content, agent_slug, manifest)
        handoffs = self.extract_handoffs(content)

        body = self._strip_yaml_front_matter(content)
        body = self._strip_handoffs_section(body).strip()
        if not body:
            body = description or name

        # W3: substitute project-specific authority hierarchy when available.
        source_instructions = manifest.get("_source_instructions_content", "")
        if source_instructions:
            hierarchy = _extract_authority_hierarchy(source_instructions)
            body = _substitute_authority_hierarchy(body, hierarchy)

        team = _team_slugs(manifest)
        targets = _dedupe_by_agent(
            h for h in handoffs
            if h.get("agent") in team and h.get("agent") != agent_slug
        )

        extensions = ["developer"]
        # Opt-in MCP servers scoped to this agent (empty unless goose:mcp is on).
        mcp_exts, mcp_notes = _mcp_recipe_extensions(manifest, agent_slug)

        if agent_slug == "orchestrator":
            sub_recipes = [
                {
                    "name": _tool_name(h["agent"]),
                    "path": f"./{h['agent']}.yaml",
                    "description": h.get("label") or h.get("prompt") or "",
                }
                for h in targets
            ]
            # W4: supplement sub_recipes with team agents absent from handoffs:.
            # Agents in the team roster but missing from the handoffs: block are still
            # valid delegation targets; include them at the end with empty descriptions.
            target_slugs = frozenset(h["agent"] for h in targets)
            for slug in sorted(team - target_slugs - frozenset([agent_slug])):
                sub_recipes.append({
                    "name": _tool_name(slug),
                    "path": f"./{slug}.yaml",
                    "description": "",
                })
            if sub_recipes:
                extensions.append("summon")
            # Phase-4a (opt-in): emit declared recipe parameters on the orchestrator
            # (the team entry point) and reference each key in a controlled prompt
            # suffix so the Goose params<->{{ template }} coupling stays valid.
            recipe_parameters = manifest.get("recipe_parameters") or None
            prompt = _ORCHESTRATOR_PROBE_PROMPT
            if recipe_parameters:
                refs = "; ".join(
                    f"{p['key']}={{{{ {p['key']} }}}}" for p in recipe_parameters
                )
                prompt = f"{prompt}\n\nRuntime inputs: {refs}"
            # Phase-4b (opt-in): emit a declared response json_schema so goose
            # validates the orchestrator's final output against it.
            recipe_response = manifest.get("recipe_response") or None
            return _emit_recipe(
                title=name,
                description=description,
                instructions=body,
                extensions=extensions,
                sub_recipes=sub_recipes or None,
                # W6: probe prompt enables non-interactive `goose run --recipe` in CI.
                prompt=prompt,
                parameters=recipe_parameters,
                response=recipe_response,
                mcp_extensions=mcp_exts,
                mcp_notes=mcp_notes,
            )

        # Non-orchestrator agent: depth-1 delegate whose own handoffs are
        # depth-2 -> represent them as `load(...)` references, not delegation.
        if targets:
            body = body + "\n\n" + _load_section(targets)
            extensions.append("summon")
        return _emit_recipe(
            title=name,
            description=description,
            instructions=body,
            extensions=extensions,
            mcp_extensions=mcp_exts,
            mcp_notes=mcp_notes,
        )

    def render_instructions_file(self, content: str, manifest: dict[str, Any]) -> str:
        """The team brief becomes AGENTS.md verbatim (strip any stray front matter).

        W3: If source copilot-instructions.md content is available in the manifest,
        propagate its project-specific authority_hierarchy into the AGENTS.md body.
        """
        body = self._strip_yaml_front_matter(content)
        source_instructions = manifest.get("_source_instructions_content", "")
        if source_instructions:
            hierarchy = _extract_authority_hierarchy(source_instructions)
            body = _substitute_authority_hierarchy(body, hierarchy)
        return body

    def render_builder_file(self, content: str, manifest: dict[str, Any]) -> str:
        """Wrap the team-builder meta-agent as a runnable Goose recipe.

        Goose agents are recipe YAML, so the builder cannot ship as a stray
        markdown file in ``.goose/recipes/``. It is a standalone recipe (no
        ``sub_recipes`` — it is not the orchestrator) with the ``developer``
        extension so it can write the description file and invoke build_team.
        Run it with ``goose run --recipe .goose/recipes/team-builder.yaml``.
        """
        name, description = _extract_name_description(content, "team-builder", manifest)
        body = self._strip_yaml_front_matter(content)
        body = self._strip_handoffs_section(body).strip() or description or name
        return _emit_recipe(
            title=name or "Team Builder",
            description=description,
            instructions=body,
            extensions=["developer"],
        )

    def get_file_extension(self, file_type: str) -> str:
        if file_type in {"agent", "builder"}:
            return ".yaml"
        return ".md"

    def supports_handoffs(self) -> bool:
        return True

    def handoff_delivery_mode(self) -> str:
        # Handoffs are encoded directly into recipes (sub_recipes / load), so no
        # sidecar manifest is needed.
        return "native"

    def get_agents_dir(self, project_path: Path) -> Path:
        return project_path / ".goose" / "recipes"

    def vscode_tasks_rel_path(self) -> str | None:
        return "../../.vscode/tasks.json"

    def normalize_output_path(self, output: Path) -> Path:
        """Normalize a user-supplied --output path for the Goose framework.

        The Goose agents directory is <project>/.goose/recipes/. When a user
        passes --output <project-root> (including `--output .`), the path does
        not end in .goose/recipes and agentteams must append the suffix so that
        the relative paths emitted by finalize_output_path (../../AGENTS.md, etc.)
        resolve correctly within the project tree.

        If the path already ends in `.goose/recipes` or `.goose` it is returned
        as-is (or with `/recipes` appended for the `.goose` case).
        """
        parts = output.parts
        if len(parts) >= 2 and parts[-2] == ".goose" and parts[-1] == "recipes":
            return output  # already the recipes directory
        if len(parts) >= 1 and parts[-1] == ".goose":
            return output / "recipes"
        # Treat as project root → derive the agents dir.
        return output / ".goose" / "recipes"

    def finalize_output_path(self, rel_path: str, file_type: str) -> str:
        """Map the generic instructions path to repo-root AGENTS.md.

        The planned instructions path is ``../copilot-instructions.md`` (relative
        to the agents dir). For Goose the agents dir is ``.goose/recipes``, so the
        repo root is two levels up.
        """
        if file_type == "instructions" and rel_path.endswith("copilot-instructions.md"):
            return "../../AGENTS.md"
        return super().finalize_output_path(rel_path, file_type)

    def extra_output_files(self, manifest: dict[str, Any]) -> list[tuple[str, str]]:
        """Emit the repo-root .goosehints integrator alongside AGENTS.md.

        W7: The content now includes a Session Startup block (below the managed
        fence) so plain `goose session` and VSCode extension sessions automatically
        adopt the Orchestrator role without requiring `goose run --recipe`.
        """
        project_name = manifest.get("project_name", "this project")
        return [("../../.goosehints", _goosehints_content(project_name))]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_SCALAR_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


def _tool_name(slug: str) -> str:
    """Goose generates a tool name per sub-recipe; keep it identifier-safe."""
    return re.sub(r"[^a-z0-9_]", "_", slug.replace("-", "_").lower())


def _dedupe_by_agent(handoffs: Any) -> list[dict[str, Any]]:
    """Keep one handoff per target agent (first wins, i.e. the YAML block entry).

    The base ``extract_handoffs`` dedupes on (agent, prompt), so an agent named
    in both the YAML ``handoffs:`` block and the body ``## Handoff Instructions``
    survives twice. Goose needs exactly one sub_recipe / load per target agent.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for h in handoffs:
        agent = str(h.get("agent", ""))
        if not agent or agent in seen:
            continue
        seen.add(agent)
        out.append(h)
    return out


def _team_slugs(manifest: dict[str, Any]) -> frozenset[str]:
    """Slugs of agents generated for this team (valid handoff/load targets)."""
    slugs: set[str] = {"orchestrator"}
    slugs.update(manifest.get("adopted_agents", []))
    for f in manifest.get("output_files", []):
        name = Path(f.get("path", "")).name
        if name.endswith(".agent.md"):
            slugs.add(name[: -len(".agent.md")])
    return frozenset(slugs)


# ---------------------------------------------------------------------------
# MCP-extension wiring (opt-in; report §5.4/§6 + goose-integration.plan §6.5)
# ---------------------------------------------------------------------------

def _goose_wirable(server: dict[str, Any]) -> bool:
    """True iff a specified MCP server is safe to AUTO-WIRE as a runnable Goose
    extension. Wiring makes the server runnable (an activation step), so the bar is
    report §5.4's named auto-activation candidate — STRICTER than the inert Claude
    emitter's ``_requires_authorization`` (which also allows ``write``):

      first-party  AND  every tool side_effects == "read"  AND  no review required.

    Anything else (third-party, any write/destructive tool, security_review.required)
    is skipped and surfaced for explicit operator handling — never silently activated.
    """
    if server.get("trust_tier") != "first-party":
        return False
    if (server.get("security_review") or {}).get("required") is True:
        return False
    tools = server.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("side_effects") != "read":
            return False
    return True


def _goose_extension_for(server: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Map one mcp-server.schema.json entry to a Goose recipe extension dict.

    Returns ``(extension, None)`` when runnable, or ``(None, reason)`` when the
    server is in-scope but cannot be faithfully/ safely wired (surfaced as a recipe
    comment, never silently dropped). Credentials are referenced by name via
    ``env_keys`` — never inlined.
    """
    if not _goose_wirable(server):
        return None, "needs operator authorization (not first-party read-only)"

    auth = server.get("auth") or {}
    mechanism = auth.get("mechanism", "none")
    env_keys: list[str] = []
    if mechanism == "env":
        ref = auth.get("credential_ref")
        if ref:
            env_keys = [str(ref)]
    elif mechanism in ("secret-store", "oauth"):
        return None, f"credential mechanism '{mechanism}' not expressible as Goose env_keys"
    # mechanism == "none" -> no env_keys

    name = _tool_name(str(server.get("server_id", "")))
    transport = server.get("transport")
    if transport == "stdio":
        command = server.get("command")
        if not command:
            return None, "stdio server has no 'command' to launch"
        return {
            "type": "stdio",
            "name": name,
            "cmd": str(command),
            "args": [str(a) for a in (server.get("args") or [])],
            "env_keys": env_keys,
            "timeout": _MCP_EXT_TIMEOUT,
        }, None
    if transport == "http":
        uri = auth.get("url")
        if not uri:
            return None, "http server has no auth.url (uri) endpoint"
        return {
            "type": "streamable_http",
            "name": name,
            "uri": str(uri),
            "env_keys": env_keys,
            "timeout": _MCP_EXT_TIMEOUT,
        }, None
    return None, f"unknown transport {transport!r}"


def _mcp_recipe_extensions(
    manifest: dict[str, Any], agent_slug: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (extensions, skip_notes) for the MCP servers scoped to ``agent_slug``.

    OPT-IN: returns ``([], [])`` unless the ``goose:mcp`` host-feature token is
    active, so a default Goose build is byte-identical to the developer-only
    baseline. Least-privilege: only servers whose ``scope`` lists this agent.
    """
    if _GOOSE_MCP_TOKEN not in (manifest.get("host_features") or []):
        return [], []
    extensions: list[dict[str, Any]] = []
    notes: list[str] = []
    for server in manifest.get("mcp_servers") or []:
        if not isinstance(server, dict):
            continue
        if agent_slug not in (server.get("scope") or []):
            continue
        ext, reason = _goose_extension_for(server)
        if ext is not None:
            extensions.append(ext)
        else:
            notes.append(f"{server.get('server_id', '<unknown>')} not wired ({reason})")
    return extensions, notes


def build_bridge_recipe(
    *,
    source_framework: str,
    rel_inventory: str,
    rel_quickstart: str,
    mcp_servers: list[dict[str, Any]],
    mcp_enabled: bool,
) -> tuple[str, list[str]]:
    """Build the goose-target bridge entry recipe (`bridge-orchestrator.yaml`).

    Always declares the ``developer`` builtin so a bridged Goose project has CLI
    access by default (req 1). When ``mcp_enabled`` (the
    ``bridge:<src>-to-goose:mcp`` token), additionally wires the operator-selected
    servers whose ``scope`` includes ``"orchestrator"`` as recipe extensions (req 2),
    reusing the same fail-closed mapper as the direct path (`_goose_extension_for` →
    first-party read-only only). Servers scoped only to specialists, or non-wirable
    ones, are surfaced as ``# agentteams MCP`` comments — the pointer bridge has no
    per-specialist recipes, so they cannot be wired here (use direct/convert for
    full per-agent MCP). Returns ``(recipe_yaml, skip_notes)``.

    The recipe is an ENTRY point: it guarantees the extensions at session start and
    instructs the agent to treat the source framework's files as canonical and route
    orchestrator-first — it does not natively delegate (no ``sub_recipes``); routing
    is prompt-level, identical to the ``.goosehints`` pointer.
    """
    instructions = (
        f"You are the orchestrator entry point for a team bridged from "
        f"`{source_framework}`.\n"
        f"Canonical agent definitions live in the source framework's files — read "
        f"`{rel_inventory}` and `{rel_quickstart}`, adopt the orchestrator identity "
        f"and constitutional rules, and route work orchestrator-first.\n"
        "Do not bypass the orchestrator for multi-step, destructive, or cross-repo work."
    )
    mcp_exts: list[dict[str, Any]] = []
    notes: list[str] = []
    if mcp_enabled:
        for server in mcp_servers or []:
            if not isinstance(server, dict):
                continue
            scope = server.get("scope") or []
            sid = server.get("server_id", "<unknown>")
            if "orchestrator" not in scope:
                notes.append(
                    f"{sid} not wired (scope={scope or 'none'}; the bridge recipe wires "
                    "orchestrator-scoped servers only — use direct/convert for specialists)"
                )
                continue
            ext, reason = _goose_extension_for(server)
            if ext is not None:
                mcp_exts.append(ext)
            else:
                notes.append(f"{sid} not wired ({reason})")
    recipe = _emit_recipe(
        title=f"Bridge Orchestrator ({source_framework} → goose)",
        description=(
            f"Entry recipe for the {source_framework}-bridged team; guarantees the "
            "developer (CLI) extension and any opted-in MCP server extensions."
        ),
        instructions=instructions,
        extensions=["developer"],
        prompt=_ORCHESTRATOR_PROBE_PROMPT,
        mcp_extensions=mcp_exts,
        mcp_notes=notes,
    )
    return recipe, notes


def _extract_name_description(
    content: str,
    agent_slug: str,
    manifest: dict[str, Any],
) -> tuple[str, str]:
    """Pull (name, description) from VS Code YAML front matter, with fallbacks."""
    name = ""
    description = ""
    match = _YAML_FRONT_MATTER_RE.match(content)
    if match:
        for key_match in _YAML_SCALAR_RE.finditer(match.group(1)):
            key = key_match.group(1).strip()
            val = key_match.group(2).strip().strip("\"'")
            if key == "name" and not name:
                name = val
            elif key == "description" and not description:
                description = val
    if not name:
        project_name = manifest.get("project_name", "")
        agent_name = FrameworkAdapter._slug_to_name(agent_slug)
        name = f"{agent_name} — {project_name}" if project_name else agent_name
    return name, description


def _load_section(targets: list[dict[str, Any]]) -> str:
    """Render the depth-2 reference block (load instead of nested delegation)."""
    lines = [
        "## Delegation & references (Goose)",
        "",
        "Goose forbids nested delegation, so when you need another specialist's "
        "guidance, load that recipe into your own context with the `summon` "
        "`load` tool (do not try to spawn a sub-agent):",
        "",
    ]
    for h in targets:
        slug = h["agent"]
        label = h.get("label") or h.get("prompt") or slug
        lines.append(
            f'- **{label}** — call `load("{slug}")` to bring `{slug}`\'s '
            f"instructions into context, then act on them here."
        )
    return "\n".join(lines)


def _yaml_dq(value: str) -> str:
    """Return a single-line double-quoted YAML scalar."""
    s = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", " ").replace("\n", " ").strip()
    return f'"{s}"'


def _indent_block(body: str, indent: str = "  ") -> str:
    """Indent body for a YAML literal block scalar (blank lines stay empty)."""
    out: list[str] = []
    for line in body.split("\n"):
        out.append(indent + line if line.strip() else "")
    return "\n".join(out)


def _emit_recipe(
    *,
    title: str,
    description: str,
    instructions: str,
    extensions: list[str],
    sub_recipes: list[dict[str, str]] | None = None,
    prompt: str | None = None,
    parameters: list[dict[str, str]] | None = None,
    response: dict[str, Any] | None = None,
    mcp_extensions: list[dict[str, Any]] | None = None,
    mcp_notes: list[str] | None = None,
) -> str:
    """Serialize a Goose recipe to YAML (hand-built, schema version 1.0.0).

    W6: The optional `prompt` field, when provided, is emitted after `description`
    and enables non-interactive `goose run --recipe` execution in CI pipelines
    (the --recipe and --text flags are mutually exclusive in Goose CLI).

    Phase-4a: ``parameters`` (opt-in), when non-empty, is emitted as a `parameters:`
    block (Goose recipe runtime inputs). Each entry is a normalized dict with `key`,
    `input_type`, `requirement`, optional `default`, optional `description` — every
    scalar double-quoted so special chars cannot break the hand-built YAML. Callers
    that reference the keys via ``{{ key }}`` (e.g. the orchestrator prompt) keep the
    Goose params↔template coupling valid. Defaults to None → byte-identical baseline.

    Phase-4b: ``response`` (opt-in), when truthy, is a JSON Schema emitted as a
    `response:` block whose ``json_schema:`` value is single-line compact JSON (valid
    YAML, since YAML is a JSON superset) so goose validates the agent's final output.
    Defaults to None → byte-identical baseline.

    ``mcp_extensions`` (opt-in) are operator-specified MCP servers rendered as
    ``stdio``/``streamable_http`` extensions after the builtin/platform ones; every
    scalar (incl. list items) is double-quoted so special chars cannot break the
    hand-built YAML. ``mcp_notes`` are operator-visible, Goose-ignored ``#`` comments
    for in-scope servers that were NOT wired (skipped for safety/launch reasons).
    Both default to empty, so a non-opted-in build is byte-identical to baseline.
    """
    lines: list[str] = [
        f'version: "{_RECIPE_VERSION}"',
        f"title: {_yaml_dq(title)}",
        f"description: {_yaml_dq(description or title)}",
    ]
    if prompt is not None:
        lines.append(f"prompt: {_yaml_dq(prompt)}")
    lines += [
        "instructions: |",
        _indent_block(instructions),
    ]
    for note in mcp_notes or []:
        # Column-0 comment terminates the instructions block scalar; Goose ignores it.
        lines.append(f"# agentteams MCP: {note}")
    if parameters:
        lines.append("parameters:")
        for p in parameters:
            lines.append(f"  - key: {_yaml_dq(p['key'])}")
            lines.append(f"    input_type: {_yaml_dq(p.get('input_type', 'string'))}")
            lines.append(f"    requirement: {_yaml_dq(p.get('requirement', 'optional'))}")
            if "default" in p:
                lines.append(f"    default: {_yaml_dq(p['default'])}")
            if p.get("description"):
                lines.append(f"    description: {_yaml_dq(p['description'])}")
    if response:
        # YAML is a JSON superset, so the arbitrary-depth JSON Schema is emitted as a
        # single-line compact flow mapping (json.dumps). Being mid-line, none of its
        # keys can column-0 match the _RECIPE_FORBIDDEN_* guards. sort_keys → stable.
        lines.append("response:")
        lines.append(
            f"  json_schema: {json.dumps(response, sort_keys=True, separators=(',', ':'))}"
        )
    lines.append("extensions:")
    for ext in extensions:
        if ext == "developer":
            lines += [
                "  - type: builtin",
                "    name: developer",
                "    bundled: true",
                "    timeout: 300",
            ]
        elif ext == "summon":
            lines += [
                "  - type: platform",
                "    name: summon",
            ]
    for mx in mcp_extensions or []:
        lines.append(f"  - type: {mx['type']}")
        lines.append(f"    name: {_yaml_dq(mx['name'])}")
        if mx["type"] == "stdio":
            lines.append(f"    cmd: {_yaml_dq(mx['cmd'])}")
            if mx.get("args"):
                lines.append("    args:")
                lines += [f"      - {_yaml_dq(a)}" for a in mx["args"]]
        else:  # streamable_http
            lines.append(f"    uri: {_yaml_dq(mx['uri'])}")
        if mx.get("env_keys"):
            lines.append("    env_keys:")
            lines += [f"      - {_yaml_dq(k)}" for k in mx["env_keys"]]
        lines.append(f"    timeout: {int(mx.get('timeout', _MCP_EXT_TIMEOUT))}")
    if sub_recipes:
        lines.append("sub_recipes:")
        for sr in sub_recipes:
            lines.append(f"  - name: {_yaml_dq(sr['name'])}")
            lines.append(f"    path: {_yaml_dq(sr['path'])}")
            if sr.get("description"):
                lines.append(f"    description: {_yaml_dq(sr['description'])}")
    return "\n".join(lines).rstrip() + "\n"
