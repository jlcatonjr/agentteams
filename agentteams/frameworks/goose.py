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
from agentteams import capability_map as _capability_map
from agentteams.yaml_frontmatter import parse_yaml_front_matter as _parse_yaml_front_matter

# 2026-08-12 A.1: Strip a pre-existing "## Delegation & references (Goose)" block
# before appending a fresh one, so the block doesn't compound on every
# native→canonical→native cycle for non-orchestrator agents with their own
# handoffs (depth-2 delegation). Mirrors agents_md.py's
# _strip_leading_synthesized_header fix for the same class of bug.
_DELEGATION_REF_HEADING_RE = re.compile(
    r"^#{1,3}\s+Delegation & references \(Goose\).*?(?=^#{1,3}\s|^<!--\s*AGENTTEAMS(?:-BRIDGE)?:|\Z)",
    re.MULTILINE | re.DOTALL,
)


# Document-content generators live in goose_docs.py (CH-07 carve, 2026-07-31): they build files
# the adapter emits, while everything here is adapter behaviour. Re-exported so existing
# `from agentteams.frameworks.goose import _goose_capabilities_content` keeps working.
from agentteams.frameworks.goose_docs import (
    _RESILIENT_RUNNER_SOURCE,
    _goose_capabilities_content,
    _goosehints_content,
    _resilient_runner_content,
)

# Recipe READ-side parse carved to goose_recipe_read.py (CH-07 carve, F.1/F.3
# of the durable-canonical-agent-format plan): parse_recipe_fields stayed
# importable from this module for compatibility, and _RECIPE_SUB_PATH_RE is
# shared with _validate_recipe_yaml's sub-recipe existence check.
from agentteams.frameworks.goose_recipe_read import (
    _RECIPE_SUB_PATH_RE,
    parse_recipe_fields,
)
# _validate_recipe_yaml carved to goose_recipe_validate.py (CH-07); re-imported so
# `from agentteams.frameworks.goose import _validate_recipe_yaml` keeps working.
from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml

__all__ = [
    "GooseAdapter",
    "build_bridge_recipe",
    "_RESILIENT_RUNNER_SOURCE",
    "_goose_capabilities_content",
    "_goosehints_content",
    "_resilient_runner_content",
]

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

# Recipe structural-validation regexes + _validate_recipe_yaml carved to
# goose_recipe_validate.py (CH-07). Re-imported above so call sites keep working.

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


def _scoped_builtin_extensions(manifest: dict[str, Any]) -> list[str]:
    """Resolve the builtin extension list for an agent's recipe (Gap 1).

    Backward-compatible: with no manifest declaration, returns the historical
    ``["developer"]`` default (byte-identical baseline). Opt-in scoping:

      * ``recipe_extensions``: explicit list of builtin extension names.
      * ``recipe_extensions_mode``: ``"append"`` (default — ``["developer"]`` plus
        the declared names) or ``"replace"`` (use ONLY the declared list, which may
        be empty — this is how a least-privilege task-agent excludes ``developer``/
        shell and relies solely on a scoped MCP, e.g. a fetch-only server).

    Rationale: a task-agent that processes untrusted external input (web pages)
    must be able to run with NO shell. Changing the global default would break
    every existing agent, so exclusion is opt-in per agent.
    """
    declared = manifest.get("recipe_extensions")
    mode = str(manifest.get("recipe_extensions_mode") or "append").lower()
    if declared is not None and mode == "replace":
        return [str(e) for e in declared]  # may be [] → no developer/shell
    base = ["developer"]
    if declared:
        base = base + [str(e) for e in declared if str(e) not in base]
    return base


def _task_prompt(manifest: dict[str, Any],
                 recipe_parameters: list[dict[str, str]] | None) -> str | None:
    """Prompt for a non-orchestrator task-agent (Gap 2).

    Uses an explicit ``recipe_prompt`` when declared; when the agent has
    ``recipe_parameters`` it appends a ``Runtime inputs:`` line referencing each
    key via ``{{ key }}`` so the Goose params↔template coupling stays valid (the
    same coupling the orchestrator emits). Returns None when neither applies, so
    an ordinary agent's recipe is unchanged.
    """
    explicit = manifest.get("recipe_prompt")
    if not recipe_parameters:
        return str(explicit) if explicit else None
    refs = "; ".join(f"{p['key']}={{{{ {p['key']} }}}}" for p in recipe_parameters)
    runtime = f"Runtime inputs: {refs}"
    return f"{explicit}\n\n{runtime}" if explicit else runtime


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
        # A.1: Strip any pre-existing "## Delegation & references (Goose)" block
        # so it doesn't compound on re-render (native→canonical→native cycle).
        body = _DELEGATION_REF_HEADING_RE.sub("", body).strip()
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

        # Gap 1: builtin extension set, opt-in scoped per agent (default ["developer"]).
        extensions = _scoped_builtin_extensions(manifest)
        # Opt-in MCP servers scoped to this agent (empty unless goose:mcp is on).
        mcp_exts, mcp_notes = _mcp_recipe_extensions(manifest, agent_slug)

        # Gap 2: parameters / response / retry are available to ANY agent whose
        # manifest declares them (previously orchestrator-only). An ordinary agent
        # declares none → these are None → its recipe is unchanged.
        recipe_parameters = manifest.get("recipe_parameters") or None
        recipe_response = manifest.get("recipe_response") or None
        recipe_retry = manifest.get("recipe_retry") or None

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
            # Phase-4a: reference declared parameter keys in the orchestrator's
            # probe prompt so the Goose params<->{{ template }} coupling stays valid.
            # (recipe_parameters / recipe_response / recipe_retry are resolved above,
            # now for every agent — Gap 2 — not just the orchestrator.)
            prompt = _ORCHESTRATOR_PROBE_PROMPT
            if recipe_parameters:
                refs = "; ".join(
                    f"{p['key']}={{{{ {p['key']} }}}}" for p in recipe_parameters
                )
                prompt = f"{prompt}\n\nRuntime inputs: {refs}"
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
                retry=recipe_retry,
                mcp_extensions=mcp_exts,
                mcp_notes=mcp_notes,
            )

        # Non-orchestrator agent: depth-1 delegate whose own handoffs are
        # depth-2 -> represent them as `load(...)` references, not delegation.
        if targets:
            body = body + "\n\n" + _load_section(targets)
            extensions.append("summon")
        # Gap 2: a non-orchestrator agent that declares parameters/response/retry is
        # a task-agent; emit them (and a params-coupled prompt). An agent declaring
        # none passes all-None → byte-identical to the prior baseline.
        prompt = _task_prompt(manifest, recipe_parameters)
        return _emit_recipe(
            title=name,
            description=description,
            instructions=body,
            extensions=extensions,
            prompt=prompt,
            parameters=recipe_parameters,
            response=recipe_response,
            retry=recipe_retry,
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

    def required_front_matter_keys(self) -> tuple[str, ...]:
        """None: a Goose agent is a recipe YAML, not Markdown with a header.

        There is no front-matter block to require keys of. Stated explicitly rather than
        inherited so the absence is a recorded finding, not an unexamined default.
        """
        return ()

    def supports_handoffs(self) -> bool:
        return True

    def handoff_delivery_mode(self) -> str:
        # Handoffs are encoded directly into recipes (sub_recipes / load), so no
        # sidecar manifest is needed.
        return "native"

    def parse_agent_source(self, content: str) -> dict[str, Any]:
        """Parse a goose recipe YAML into CAI export fields (F.1).

        Recipes are YAML, not front-matter Markdown, so the interop default
        Markdown path cannot extract fields from them. Mapping: title -> name,
        description -> description, instructions -> body; extension names map
        coarse to the canonical capability vocabulary
        (``capability_map.goose_extensions_to_canonical``); sub_recipes become
        true-delegation handoffs (send=True) and ``load("<slug>")`` references
        become context-load handoffs (send=False), deduped one-per-agent with
        sub_recipes winning.
        """
        fields = parse_recipe_fields(content)
        tokens = _capability_map.goose_extensions_to_canonical(fields["extension_names"])
        handoffs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for slug in fields["sub_recipe_slugs"]:
            if slug in seen:
                continue
            seen.add(slug)
            handoffs.append({"agent": slug, "label": None, "prompt": "", "send": True})
        for slug in fields["load_refs"]:
            if slug in seen:
                continue
            seen.add(slug)
            handoffs.append({"agent": slug, "label": None, "prompt": "", "send": False})
        return {
            "name": fields["title"],
            "description": fields["description"],
            "body": fields["instructions"],
            "capabilities": _capability_map.capabilities_from_tokens(tokens, "goose"),
            "handoffs": handoffs,
            # F.3: recipe-level configuration travels to the adapter's
            # framework_extensions_from_sources aggregation.
            "recipe_config": {
                "builtin_extension_names": fields["builtin_extension_names"],
                "parameters": fields["parameters"],
                "response": fields["response"],
                "retry": fields["retry"],
            },
        }

    def framework_extensions_from_sources(
        self, parsed_sources: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Aggregate captured recipe config into framework_extensions.goose (F.3).

        Builtin extension names union across recipes; parameters/response/
        retry take the first recipe that declares each (Gap 2 allows any
        agent to declare them; the orchestrator conventionally does). A
        recipe set WITHOUT developer signals the source ran in replace mode
        (least-privilege, no shell) — recorded so import re-applies it
        instead of silently re-adding developer.
        """
        ext_names: dict[str, None] = {}
        parameters: list[dict[str, str]] | None = None
        response: dict[str, Any] | None = None
        retry: dict[str, Any] | None = None
        for parsed in parsed_sources:
            cfg = parsed.get("recipe_config") if isinstance(parsed, dict) else None
            if not cfg:
                continue
            for n in cfg.get("builtin_extension_names") or []:
                ext_names.setdefault(str(n), None)
            if parameters is None and cfg.get("parameters"):
                parameters = cfg["parameters"]
            if response is None and cfg.get("response"):
                response = cfg["response"]
            if retry is None and cfg.get("retry"):
                retry = cfg["retry"]
        bucket: dict[str, Any] = {}
        if ext_names:
            bucket["recipe_extensions"] = list(ext_names)
            if "developer" not in ext_names:
                bucket["recipe_extensions_mode"] = "replace"
        if parameters is not None:
            bucket["recipe_parameters"] = parameters
        if response is not None:
            bucket["recipe_response"] = response
        if retry is not None:
            bucket["recipe_retry"] = retry
        return {"goose": bucket} if bucket else {}

    def apply_framework_extensions(self, manifest: dict[str, Any], cai: dict[str, Any]) -> None:
        """Merge the CAI framework_extensions.goose bucket into the import
        manifest stub (F.3) so render_agent_file re-emits the captured
        recipe configuration instead of silently dropping it."""
        bucket = (cai.get("framework_extensions") or {}).get("goose")
        if not isinstance(bucket, dict):
            return
        for key in (
            "recipe_parameters",
            "recipe_response",
            "recipe_retry",
            "recipe_extensions",
            "recipe_extensions_mode",
        ):
            if key in bucket:
                manifest[key] = bucket[key]

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

        Also emits a capabilities reference (bare `references/...` path, landing at
        `.goose/recipes/references/` alongside every other generated reference doc —
        unlike `.goosehints`, this file has no reason to sit at repo root).

        Gap 3: also emits the resilient-runner script (`../../scripts/...` — a
        runnable tool, so repo-root `scripts/` like `.goosehints`, not the
        reference-doc landing spot) unconditionally, for every project regardless
        of provider. See `_resilient_runner_content` for the full rationale.

        P1-1: on macOS a confined/exclusive profile also emits the Seatbelt `sandbox.sb`
        + inert `config.yaml.agentteams.example`; nothing extra off macOS (honest
        fail-closed). See `_goose_sandbox_emit.goose_sandbox_output_files`.
        """
        from agentteams.frameworks._goose_sandbox_emit import goose_sandbox_output_files

        project_name = manifest.get("project_name", "this project")
        # Framework-neutral baseline first (the Linux confinement launcher on linux). The
        # goose-specific sidecars + the macOS Seatbelt path are layered on top. On Linux the
        # neutral launcher is the boundary; the darwin-guarded Seatbelt path emits nothing there.
        files: list[tuple[str, str]] = super().extra_output_files(manifest)
        files.extend([
            ("../../.goosehints", _goosehints_content(project_name)),
            ("references/goose-capabilities-reference.md", _goose_capabilities_content(project_name)),
            ("../../scripts/goose-run-resilient.py", _resilient_runner_content()),
        ])
        files.extend(goose_sandbox_output_files(manifest))
        return files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Slugs of agents that are valid handoff/load targets for this team.

    Union of adopted orphans, the authoritative brief-defined team
    (``agent_slug_list``), agents already deployed ON DISK (``existing_agent_slugs``,
    populated during ``--update``), and this run's ``output_files`` — so an ``--update``
    that regenerates only part of the team does not silently drop deployed teammates'
    cross-refs (D1; mirrors ``copilot_vscode._get_team_slugs``).
    """
    slugs: set[str] = {"orchestrator"}
    slugs.update(manifest.get("adopted_agents", []))
    slugs.update(manifest.get("agent_slug_list", []))
    slugs.update(manifest.get("existing_agent_slugs", []))
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
    yaml_text, _ = _parse_yaml_front_matter(content)
    if yaml_text is not None:
        for key_match in _YAML_SCALAR_RE.finditer(yaml_text):
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
    retry: dict[str, Any] | None = None,
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

    Phase-4c: ``retry`` (opt-in), when truthy, is a normalized dict emitted as a
    `retry:` block (bounded ``max_retries`` + ``timeout_seconds`` + shell ``checks``)
    so goose re-runs until the checks pass. Defaults to None → byte-identical baseline.

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
    if retry:
        # Hand-built (flat): ints unquoted, command/on_failure strings via _yaml_dq
        # (which collapses newlines + escapes quotes → no YAML-structure breakout).
        lines.append("retry:")
        lines.append(f"  max_retries: {int(retry['max_retries'])}")
        lines.append(f"  timeout_seconds: {int(retry['timeout_seconds'])}")
        if "on_failure_timeout_seconds" in retry:
            lines.append(f"  on_failure_timeout_seconds: {int(retry['on_failure_timeout_seconds'])}")
        lines.append("  checks:")
        for check in retry["checks"]:
            lines.append(f"    - type: {_yaml_dq(check.get('type', 'shell'))}")
            lines.append(f"      command: {_yaml_dq(check['command'])}")
        if retry.get("on_failure"):
            lines.append(f"  on_failure: {_yaml_dq(retry['on_failure'])}")
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
        else:
            # Generic builtin (Gap 1: scoped recipe_extensions may name other
            # bundled servers, e.g. memory). Rendered with the same bundled/timeout
            # shape as developer so goose treats it as a builtin extension.
            lines += [
                "  - type: builtin",
                f"    name: {_yaml_dq(ext)}",
                "    bundled: true",
                "    timeout: 300",
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
