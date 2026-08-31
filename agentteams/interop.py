"""interop.py - Cross-framework agent infrastructure interop pipeline.

This module provides a canonical intermediate representation (CAI) for agent
teams and utilities to convert between supported frameworks through that
representation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Single source of truth for the framework-id -> adapter map (CH-05).
from agentteams import capability_map as _capability_map
from agentteams.frameworks.base import FrameworkAdapter as _FrameworkAdapter
from agentteams.frameworks.registry import FRAMEWORKS as _ADAPTERS
from agentteams.yaml_frontmatter import (
    parse_yaml_front_matter as _parse_yaml_front_matter,
)

_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md", "AGENTS.md"}
# Subdirectories under (or beside) an agents dir whose .md files are NOT agents:
# reference docs, Claude skills (a sibling dir), and backup copies of agents.
# Mirror of convert._PASSTHROUGH_DIRS (kept in sync deliberately — see convert.py).
from agentteams.backup import BACKUP_DIR_NAME as _BACKUP_DIR_NAME

# Own set, shared name (CH-05): this filters agent discovery, not scratch pruning.
_NON_AGENT_DIRS = {"references", "skills", _BACKUP_DIR_NAME}


@dataclass
class InteropResult:
    """Summary of an interop run."""

    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bundle_files: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def detect_framework(source_dir: Path) -> str:
    """Best-effort framework detection from directory shape and file style."""
    # F.5: a canonical directory identifies via its team.cai.json marker —
    # checked first because that file IS the format's identity (plan §5.6).
    if (source_dir / "team.cai.json").is_file():
        return "canonical"
    parts = set(source_dir.parts)
    if ".claude" in parts:
        return "claude"
    if ".goose" in parts:           # .goose/recipes — a Goose-native source team
        return "goose"
    if ".agents" in parts:          # .agents/<name>.md — an agents-md source team (F.1)
        return "agents-md"
    if ".github" in parts and "copilot" in parts:
        return "copilot-cli"

    has_agent_ext = False
    has_claude_front_matter = False
    has_yaml_keys = False
    for p in source_dir.glob("*.md"):
        if p.name.endswith(".agent.md"):
            has_agent_ext = True
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if content.startswith("---\n"):
            # Accept BOTH capability keys. `tools:` is what a Claude subagent file carries
            # since 2026-08-06; `allowed-tools:` is what every previously generated team
            # still carries on disk, and detection must keep working on those. Matched at
            # line start so `allowed-tools:` is not read as a `tools:` hit, and so a
            # copilot-vscode `tools: ['read']` line is excluded by the bracket test below.
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("allowed-tools:"):
                    has_claude_front_matter = True
                elif stripped.startswith("tools:") and "[" not in stripped:
                    # copilot-vscode writes an inline list (`tools: ['read']`); Claude
                    # writes a bare comma-separated scalar. The bracket discriminates.
                    has_claude_front_matter = True
            if "user-invocable:" in content or "handoffs:" in content:
                has_yaml_keys = True

    if has_agent_ext or has_yaml_keys:
        return "copilot-vscode"
    if has_claude_front_matter:
        return "claude"
    return "copilot-cli"


def export_to_cai(source_dir: Path, source_framework: str | None = None) -> dict[str, Any]:
    """Export a source team into canonical agent interface format."""
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    framework = source_framework or detect_framework(source_dir)
    # F.5 guard (plan §5.6): canonical is a deliberate, NAMED exception to
    # registry.py's single-source-of-truth pattern — it is not a rendering
    # adapter, so it dispatches to canonical.py (load) instead of one.
    if framework == "canonical" or framework in _ADAPTERS:
        pass
    else:
        raise ValueError(f"Unknown source framework {framework!r}")
    if framework == "canonical":
        from agentteams.canonical import load_canonical

        return load_canonical(source_dir)

    adapter = _ADAPTERS[framework]()

    agents: list[dict[str, Any]] = []
    instructions_content = ""
    instructions_name = ""
    # F.3: framework-native parse results, handed to the adapter after
    # discovery so it can aggregate project-level framework_extensions.
    parsed_sources: list[dict[str, Any]] = []

    # F.1: agent-file extension is framework-owned — Markdown for every
    # framework except goose, whose agents are recipe YAML
    # (adapter.get_file_extension("agent") == ".yaml").
    agent_ext = adapter.get_file_extension("agent")

    for entry in sorted(source_dir.rglob("*")):
        if entry.is_dir():
            continue
        rel = entry.relative_to(source_dir)
        if rel.parts and rel.parts[0] in _NON_AGENT_DIRS:
            # reference docs / skills / backup copies — never agents (rglob recurses,
            # so without this every references/*.md became a bogus CAI agent)
            continue
        name = entry.name
        if name in _INSTRUCTIONS_NAMES:
            instructions_name = name
            instructions_content = entry.read_text(encoding="utf-8")
            continue
        if name.endswith(agent_ext):
            if name == "SETUP-REQUIRED.md":
                continue
            content = entry.read_text(encoding="utf-8")
            slug = _slug_from_filename(name)
            parsed = adapter.parse_agent_source(content)
            if parsed is not None:
                parsed_sources.append(parsed)  # F.3: aggregate after discovery
                # F.1: framework-native parse (goose recipe YAML) arrives
                # pre-shaped — name/description/body/capabilities/handoffs all
                # extracted by the adapter, which owns the file format.
                body = str(parsed.get("body", ""))
                invariant_core, body = _capture_invariant_core(body)
                capabilities = parsed.get("capabilities") or {}
                raw_handoffs = parsed.get("handoffs") or []
                agent_name = str(parsed.get("name") or "").strip() or slug
                description = str(parsed.get("description") or "")
                # A.3: goose recipes have no YAML front matter, so no
                # raw_front_matter; capabilities.raw is handled below via
                # _capture_escape_hatches for the standard path only.
                raw_front_matter = {}
                cap_raw = None
                model_hint = None
            else:
                body = _strip_framework_wrappers(content)
                # D.4: capture the fenced invariant_core span verbatim as its own
                # field and lift it out of body_markdown so import can re-emit it
                # as a properly fenced section without duplication.
                invariant_core, body = _capture_invariant_core(body)
                # C.2: real capability capture. copilot-vscode's tools list already
                # uses the canonical 7-token vocabulary; claude files carry Claude
                # tool names reverse-mapped via capability_map (with claude.py's
                # execute-subsumes-retrieval dedup rule reproduced). copilot-cli
                # shares copilot-vscode's tools: shape byte-for-byte since the P1
                # convergence (2026-08-15) — same extraction applies. agents-md
                # has no capability channel (plan §5.2); goose's coarse
                # recipe-extension mapping lands with its discovery fix (Phase F).
                if framework in ("copilot-vscode", "copilot-cli"):
                    capabilities = _capability_map.capabilities_from_tokens(
                        _capability_map.canonical_tools_for_copilot_vscode(content), framework
                    )
                elif framework == "claude":
                    capabilities = _capability_map.capabilities_from_tokens(
                        _capability_map.canonical_tools_for_claude(content), framework
                    )
                else:
                    capabilities = {}
                raw_handoffs = adapter.extract_handoffs(content)
                agent_name = _frontmatter_value(content, "name") or _first_heading_or_title(content, slug)
                description = _frontmatter_value(content, "description")
                # A.3: Wire the three CAI schema escape hatches into the
                # standard Markdown capture path (report sections 4.1, 6).
                # 1) raw_front_matter — any front-matter key not already
                #    modeled (name, description, tools, allowed-tools, handoffs).
                # 2) capabilities.raw — per-framework original capability strings.
                # 3) capabilities.model_hint — advisory model string.
                raw_front_matter, cap_raw, model_hint = _capture_escape_hatches(
                    content, framework
                )
                if cap_raw is not None:
                    capabilities = dict(capabilities)
                    capabilities["raw"] = {framework: cap_raw}
                if model_hint is not None:
                    capabilities = dict(capabilities)
                    capabilities["model_hint"] = model_hint
            # C.3: real handoff capture via the shared adapter parser (was
            # hardcoded [] for every framework). Normalized to CAI v2 shape:
            # `to` (required by schema) carries the target slug; label/prompt/
            # send travel as additional properties.
            handoffs = [
                {
                    "to": str(h.get("agent", "")).strip(),
                    "label": h.get("label") or None,
                    "prompt": h.get("prompt", "") or "",
                    "send": bool(h.get("send", False)),
                }
                for h in raw_handoffs
                if str(h.get("agent", "")).strip()
            ]
            agents.append(
                {
                    "slug": slug,
                    "name": agent_name,
                    "description": description,
                    "body_markdown": body.strip() + "\n",
                    "capabilities": capabilities,
                    "handoffs": handoffs,
                    "invariant_core_markdown": invariant_core or None,
                    "source_path": str(rel),
                    # A.3: raw_front_matter escape hatch (None for the
                    # goose parsed path, which has no front matter).
                    **(
                        {"raw_front_matter": raw_front_matter}
                        if raw_front_matter
                        else {}
                    ),
                }
            )

    if not instructions_content:
        # F.1: a goose source dir is .goose/recipes — its AGENTS.md sits at the
        # project root, TWO levels up; every other framework keeps the
        # single-parent lookup.
        parents = [source_dir.parent]
        if framework == "goose":
            parents.append(source_dir.parent.parent)
        for parent in parents:
            found = False
            for candidate_name in sorted(_INSTRUCTIONS_NAMES):
                candidate = parent / candidate_name
                if candidate.exists():
                    instructions_name = candidate_name
                    instructions_content = candidate.read_text(encoding="utf-8")
                    found = True
                    break
            if found:
                break

    # A.4: Read the runtime-handoffs.json sidecar back for manifest-delivery
    # frameworks (claude, copilot-cli, agents-md, codex) so handoffs survive
    # a native→canonical round trip. The sidecar is written by import_from_cai
    # at source_dir.parent / "references" / "runtime-handoffs.json" (report
    # section 4.3, confirmed 4 of 6 frameworks affected).
    if adapter.handoff_delivery_mode() == "manifest":
        _merge_sidecar_handoffs(agents, source_dir)

    cai_doc: dict[str, Any] = {
        "schema_version": "2.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_framework": framework,
        "source_dir": str(source_dir),
        "instructions_binding": {
            "source_name": instructions_name or "",
            "content": instructions_content,
        },
        "agents": sorted(agents, key=lambda a: a["slug"]),
        # D.1: first-class skills survive the round trip (plan §5.4). Only
        # frameworks with a skill concept capture anything here; the apply
        # side in import_from_cai is gated the same way.
        "skills": _capture_skills(source_dir, adapter),
        # D.3: framework-neutral MCP server definitions (plan §5.4). Captured
        # from the pipeline's managed artifact when present; import_from_cai
        # re-validates them against mcp-server.schema.json (allOf security
        # gates included) before applying.
        "mcp_servers": _capture_mcp_servers(source_dir),
        # A.3: references escape hatch — walk the source team's references/
        # directory into the CAI references[] list so non-agent reference
        # content survives the round trip (report section 4.1).
    }
    # A.3: Only add references when non-empty so load_canonical's absence
    # of the key (no references/ dir in canonical) matches exactly.
    captured_refs = _capture_references(source_dir)
    if captured_refs:
        cai_doc["references"] = captured_refs
    # F.3: project-level framework-owned configuration (plan §5.6). goose
    # aggregates recipe parameters/response/retry + non-MCP extension names
    # into framework_extensions.goose; frameworks without project-level
    # config contribute nothing (key absent = honestly none).
    framework_extensions = adapter.framework_extensions_from_sources(parsed_sources)
    if framework_extensions:
        cai_doc["framework_extensions"] = framework_extensions
    return _json_safe(cai_doc)


def _json_safe(obj: Any) -> Any:
    """Recursively coerce a CAI value to JSON-serializable types.

    PyYAML parses an unquoted ISO date in agent front matter (``date: 2026-08-12``)
    as a ``datetime.date`` object, which then rides into ``raw_front_matter``. Left
    alone it raises ``TypeError: Object of type date is not JSON serializable`` at
    the first ``json.dumps`` — canonical materialize, sync baselines, interop
    bundles. Coercing dates/datetimes to ISO strings at the CAI boundary keeps the
    representation JSON-safe by construction for every downstream writer.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _capture_mcp_servers(source_dir: Path) -> list[dict[str, Any]]:
    """Capture MCP servers from the pipeline's managed artifact, if present (D.3).

    ``mcp_emit`` writes ``.claude/mcp-servers.agentteams.json`` at the project
    root. Depending on how deep *source_dir* sits (``.claude/agents`` in-repo vs
    deeper bridge layouts), the artifact is one or two levels up. First hit
    wins; unreadable or absent artifacts capture nothing (honestly degraded).
    """
    candidates = (
        source_dir.parent / "mcp-servers.agentteams.json",
        source_dir.parent / ".claude" / "mcp-servers.agentteams.json",
        source_dir.parent.parent / ".claude" / "mcp-servers.agentteams.json",
    )
    for artifact in candidates:
        if not artifact.is_file():
            continue
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        servers = data.get("servers", [])
        if not isinstance(servers, list):
            return []
        from agentteams.mcp_emit import normalize_mcp_server_defaults

        return [normalize_mcp_server_defaults(s) for s in servers if isinstance(s, dict)]
    return []


_INVARIANT_CORE_SPAN_RE = re.compile(
    r"<!--\s*AGENTTEAMS:BEGIN\s+invariant_core\s+v=\d+\s*-->.*?<!--\s*AGENTTEAMS:END\s+invariant_core\s*-->",
    re.DOTALL,
)


def _capture_invariant_core(content: str) -> tuple[str, str]:
    """Capture the fenced invariant_core span verbatim. Returns (span, rest) (D.4).

    Detection goes through ``fences.py::_extract_fenced_regions`` — the
    authoritative fence scanner; when the region is present the full
    BEGIN..END span is extracted (markers included, so the ``v=N`` version
    marker travels verbatim) and removed from the body so import can re-emit
    it as a properly fenced section without duplication. Honest degradation:
    a scan error (returned as a message string) or an absent region leaves
    the content untouched.
    """
    from agentteams.fences import _extract_fenced_regions

    regions = _extract_fenced_regions(content)
    if isinstance(regions, str) or "invariant_core" not in regions:
        return "", content
    m = _INVARIANT_CORE_SPAN_RE.search(content)
    if not m:
        return "", content
    rest = content[: m.start()] + content[m.end():]
    rest = re.sub(r"\n{3,}", "\n\n", rest)
    return m.group(0).strip(), rest


def _capture_skills(source_dir: Path, adapter: Any) -> list[dict[str, Any]]:
    """Capture first-class skills for frameworks that have them (D.1).

    Skill roots checked in order: a ``skills/`` subdirectory of the agents
    dir, then the sibling ``skills/`` directory (Claude's layout puts
    ``.claude/skills/`` beside ``.claude/agents/``). Each skill is a
    directory containing ``SKILL.md``; the directory name is the slug and
    the invocable command name. Capture records the front matter, the body
    with front matter stripped, and every co-located file verbatim so
    ``import_from_cai`` can re-emit the skill faithfully.
    """
    if not adapter.has_skill_concept():
        return []
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in (source_dir / "skills", source_dir.parent / "skills"):
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            skill_dir = skill_md.parent
            slug = skill_dir.name
            if slug in seen:
                continue
            seen.add(slug)
            content = skill_md.read_text(encoding="utf-8")
            _, body = _parse_yaml_front_matter(content)
            files = [
                {
                    "rel_path": str(p.relative_to(skill_dir)),
                    "content": p.read_text(encoding="utf-8"),
                }
                for p in sorted(skill_dir.rglob("*"))
                if p.is_file()
            ]
            skills.append(
                {
                    "slug": slug,
                    "front_matter": {
                        "name": _frontmatter_value(content, "name"),
                        "description": _frontmatter_value(content, "description"),
                    },
                    "body_markdown": body.strip() + "\n",
                    "files": files,
                }
            )
    return sorted(skills, key=lambda s: s["slug"])


def _instructions_target_name(target_framework: str) -> str:
    """Instructions filename a framework owns at its project root (F.2).

    claude -> CLAUDE.md, agents-md/goose -> AGENTS.md, everything else
    copilot-instructions.md. Placement differs (goose's lives two levels
    above ``.goose/recipes``); the caller resolves the directory.
    """
    if target_framework == "claude":
        return "CLAUDE.md"
    if target_framework in ("agents-md", "goose", "codex"):
        return "AGENTS.md"
    return "copilot-instructions.md"


def import_from_cai(
    cai: dict[str, Any],
    target_framework: str,
    target_dir: Path,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> InteropResult:
    """Import a CAI document into a target framework directory."""
    # F.5 guard (plan §5.6): same named exception as export_to_cai — the
    # canonical target dispatches to canonical.py (materialize) instead of a
    # registry adapter.
    if target_framework == "canonical" or target_framework in _ADAPTERS:
        pass
    else:
        raise ValueError(f"Unknown target framework {target_framework!r}")
    if target_framework == "canonical":
        from agentteams.canonical import materialize_canonical

        # Note: materialize overwrites atomically by design (the canonical
        # dir is a derived artifact); the `overwrite` flag's skip-on-exist
        # semantics apply to framework agent files, not to it.
        result = InteropResult(dry_run=dry_run)
        mat = materialize_canonical(cai, target_dir, dry_run=dry_run)
        result.converted.extend(mat.written)
        return result

    adapter = _ADAPTERS[target_framework]()
    result = InteropResult(dry_run=dry_run)
    # Populate output_files with the roster being imported: render-time team-ref
    # filters (copilot-vscode strips handoff/agents targets outside the team)
    # must recognize the imported team as its own roster, or every cross-agent
    # handoff except ones targeting orchestrator gets silently deleted.
    manifest = {
        "project_name": "InteropProject",
        "output_files": [
            {"path": f"{str(a.get('slug', '')).strip()}.agent.md"}
            for a in cai.get("agents", [])
            if str(a.get("slug", "")).strip()
        ],
    }
    # F.3: restore project-level framework-owned config (goose recipe
    # parameters/response/retry + extension scoping) into the import
    # manifest stub so render-time re-emits it instead of dropping it.
    adapter.apply_framework_extensions(manifest, cai)
    # The project-level framework_extensions.goose bucket's own contribution
    # (just set above), captured once before the loop — 2026-08-11 fix below
    # unions this with each agent's own tool_scopes-derived extensions, fresh
    # per agent, rather than letting one clobber the other or leak across agents.
    _bucket_recipe_extensions = list(manifest.get("recipe_extensions") or [])
    # C.3: apply captured handoffs per the adapter's delivery mode. `native`
    # frameworks (copilot-vscode, goose) keep handoffs inline in the agent
    # file's front matter (goose encodes them into sub_recipes at recipe render
    # time); `manifest` frameworks (claude, copilot-cli, agents-md) receive the
    # references/runtime-handoffs.json sidecar — the same shape
    # cli/render_pipeline.py emits; `none` gets nothing (honestly degraded).
    delivery = adapter.handoff_delivery_mode()
    runtime_handoff_agents: list[dict[str, Any]] = []

    for agent in cai.get("agents", []):
        slug = str(agent.get("slug", "")).strip()
        if not slug:
            continue
        rel_name = slug + adapter.get_file_extension("agent")
        dest = target_dir / rel_name
        if dest.exists() and not overwrite:
            result.skipped.append(str(dest))
            continue

        body = str(agent.get("body_markdown", "")).strip() + "\n"
        # D.4: re-emit the captured invariant_core as a properly fenced
        # section (the span already carries its BEGIN/END markers verbatim,
        # including the version marker). Appended before framework rendering
        # so every adapter sees it as ordinary fenced body content.
        invariant_core = str(agent.get("invariant_core_markdown") or "").strip()
        if invariant_core:
            body = body.rstrip("\n") + "\n\n" + invariant_core + "\n"
        # Re-attach the CAI name/description as front matter so adapters that
        # derive them from front matter (claude / copilot-vscode / copilot-cli /
        # goose) preserve the metadata instead of falling back to a slug-derived
        # name. Since the P1 convergence (2026-08-15) copilot-cli shares
        # copilot-vscode's front-matter channel; it no longer strips it.
        cai_name = str(agent.get("name", "")).strip()
        cai_desc = str(agent.get("description", "")).strip()
        cai_handoffs = [
            {
                "label": str(h.get("label") or ""),
                "agent": str(h.get("to", "")).strip(),
                "prompt": str(h.get("prompt", "") or ""),
                "send": bool(h.get("send", False)),
            }
            for h in agent.get("handoffs", [])
            if str(h.get("to", "")).strip()
        ]
        # Thread capabilities.tool_scopes through to the frameworks with a
        # native per-agent tool-scope channel (2026-08-11 finding: this was
        # captured on export but never applied on import, so every target
        # silently fell back to its own default tool grant regardless of what
        # the CAI actually specified — a least-privilege-relevant gap when the
        # source restricts an agent's tools deliberately). copilot-cli joined
        # this set at the P1 convergence (2026-08-15) — omitting it here would
        # silently re-default every copilot-cli import's tools/user-invocable/
        # model to _YAML_DEFAULTS regardless of what the CAI specified, the
        # exact gap this comment describes, just for a framework added later.
        # goose's channel is the recipe-level `extensions:` list, wired
        # separately below via manifest['recipe_extensions'], not through this
        # front-matter header.
        cai_tool_scopes = [
            str(t) for t in (agent.get("capabilities") or {}).get("tool_scopes") or []
        ]
        if target_framework == "goose":
            agent_exts = _capability_map.canonical_to_goose_extensions(cai_tool_scopes)
            union_exts = dict.fromkeys(_bucket_recipe_extensions)
            union_exts.update(dict.fromkeys(agent_exts))
            if union_exts:
                manifest["recipe_extensions"] = list(union_exts)
            else:
                manifest.pop("recipe_extensions", None)
        cai_tools_line: str | None = None
        if cai_tool_scopes and target_framework in ("copilot-vscode", "copilot-cli", "claude"):
            # Both adapters' own render_agent_file already knows how to turn a
            # VS Code-shaped bracket list of canonical tokens into their native
            # tool declaration (copilot-vscode: pass-through, since its own
            # vocabulary already is canonical; claude: claude.py's existing
            # _map_allowed_tools/_VSCODE_TO_CLAUDE_TOOLS). Writing the same
            # bracket-format line for both and letting each adapter's already-
            # correct, already-tested logic do the rest is simpler and less
            # error-prone than pre-translating per framework here — confirmed
            # by direct testing: an earlier version of this fix pre-translated
            # to Claude-native names in comma format, which claude.py's own
            # _YAML_TOOLS_RE (bracket-only) silently failed to recognize,
            # falling back to _CLAUDE_DEFAULT_ALLOWED_TOOLS — the same bug this
            # fix exists to close, just relocated.
            cai_tools_line = "tools: [" + ", ".join(f"'{t}'" for t in cai_tool_scopes) + "]"
        if cai_name or cai_desc or cai_tools_line or (delivery == "native" and cai_handoffs):
            header = ["---"]
            if cai_name:
                header.append(f"name: {cai_name}")
            if cai_desc:
                header.append(f'description: "{cai_desc.replace(chr(34), chr(39))}"')
            if cai_tools_line:
                header.append(cai_tools_line)
            if delivery == "native" and cai_handoffs:
                # Inline handoff block in the exact shape
                # FrameworkAdapter.extract_handoffs parses, so the imported
                # file round-trips through the same parser.
                header.append("handoffs:")
                for h in cai_handoffs:
                    header.append(f'  - label: "{h["label"].replace(chr(34), chr(39))}"')
                    header.append(f'    agent: "{h["agent"]}"')
                    header.append(f'    prompt: "{h["prompt"].replace(chr(34), chr(39))}"')
                    header.append(f'    send: {"true" if h["send"] else "false"}')
            # A.2: Restore raw_front_matter escape-hatch keys into the header
            # for frameworks with a front-matter channel (copilot-vscode,
            # copilot-cli since the P1 convergence, claude). This closes the
            # user-invocable/model/agents:roster gaps by writing the captured
            # values back so _ensure_yaml_front_matter doesn't overwrite them
            # with hardcoded defaults — the same defaulting CopilotCLIAdapter
            # would otherwise silently apply on every cross-framework import,
            # since its render_agent_file delegates straight to
            # CopilotVSCodeAdapter's _ensure_yaml_front_matter.
            cai_raw_fm = agent.get("raw_front_matter")
            if isinstance(cai_raw_fm, dict) and cai_raw_fm and target_framework in ("copilot-vscode", "copilot-cli", "claude"):
                # Sort keys so direct import and via-canonical import produce
                # byte-identical output regardless of dict insertion order
                # (export preserves YAML order; canonical load is JSON-sorted).
                for rfm_key in sorted(cai_raw_fm):
                    rfm_val = cai_raw_fm[rfm_key]
                    if rfm_key in ("name", "description", "tools", "allowed-tools", "handoffs"):
                        continue  # already written above
                    header.append(_serialize_raw_fm_key(rfm_key, rfm_val))
            header.append("---")
            body = "\n".join(header) + "\n\n" + body
        if delivery == "manifest" and cai_handoffs:
            runtime_handoff_agents.append({"agent": slug, "handoffs": cai_handoffs})
        rendered = adapter.render_agent_file(body, slug, manifest)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(rendered, encoding="utf-8")
        result.converted.append(str(dest))

    instructions_content = str(cai.get("instructions_binding", {}).get("content", ""))
    if instructions_content:
        # F.2: instructions filename + placement is framework-owned. goose's
        # AGENTS.md lives at the PROJECT ROOT (two levels above .goose/recipes);
        # every other framework keeps it beside the agents dir.
        instructions_name = _instructions_target_name(target_framework)
        if target_framework == "goose":
            inst_dest = target_dir.parent.parent / instructions_name
        else:
            inst_dest = target_dir.parent / instructions_name
        if inst_dest.exists() and not overwrite:
            result.skipped.append(str(inst_dest))
        else:
            if not dry_run:
                inst_dest.parent.mkdir(parents=True, exist_ok=True)
                inst_dest.write_text(instructions_content, encoding="utf-8")
            result.converted.append(str(inst_dest))

    # D.1: re-emit captured skills through the adapter's skill hook.
    # Placement mirrors the render pipeline (`../skills/<slug>/SKILL.md`
    # relative to the agents dir — Claude: `.claude/skills/<slug>/`). The
    # body travels verbatim; the front matter is normalized by
    # render_skill_file exactly as the render pipeline does, with the
    # captured name driving the description label. Frameworks without a
    # skill concept drop skills honestly (their exports capture none).
    cai_skills = [s for s in (cai.get("skills") or []) if str(s.get("slug", "")).strip()]
    if cai_skills and adapter.has_skill_concept():
        for skill in cai_skills:
            slug = str(skill["slug"]).strip()
            skill_dir = target_dir.parent / "skills" / slug
            dest = skill_dir / "SKILL.md"
            if dest.exists() and not overwrite:
                result.skipped.append(str(dest))
                continue
            captured_name = str((skill.get("front_matter") or {}).get("name") or "").strip()
            skill_manifest = {
                "project_name": manifest.get("project_name", ""),
                "tool_agents": [{"tool_name": captured_name or slug, "slug": slug}],
            }
            body = str(skill.get("body_markdown", "")).strip() + "\n"
            rendered = adapter.render_skill_file(body, slug, skill_manifest)
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(rendered, encoding="utf-8")
            result.converted.append(str(dest))
            for f in skill.get("files") or []:
                rel_path = str(f.get("rel_path", "")).strip()
                if not rel_path or rel_path == "SKILL.md":
                    continue
                co_dest = skill_dir / rel_path
                if co_dest.exists() and not overwrite:
                    result.skipped.append(str(co_dest))
                    continue
                if not dry_run:
                    co_dest.parent.mkdir(parents=True, exist_ok=True)
                    co_dest.write_text(str(f.get("content", "")), encoding="utf-8")
                result.converted.append(str(co_dest))

    # D.3: MCP servers re-validation and apply (plan §5.4/§8). Every server in
    # the CAI must re-validate against mcp-server.schema.json at IMPORT time —
    # including the allOf hard gates forcing security_review.required=true for
    # third-party or destructive-tool servers. A hand-edited canonical
    # team.cai.json that weakens a security-review flag must fail re-import,
    # not silently round-trip. _inert_problems runs full Draft7 validation
    # (allOf included) when jsonschema is available.
    cai_mcp = [s for s in (cai.get("mcp_servers") or []) if isinstance(s, dict)]
    if cai_mcp:
        from agentteams.mcp_emit import _inert_problems, emit_mcp_artifact

        for server in cai_mcp:
            problems = _inert_problems(server)
            if problems:
                sid = server.get("server_id", "<unknown>")
                raise ValueError(
                    f"MCP server {sid!r} fails mcp-server.schema.json re-validation "
                    f"on import (security_review hard gates included): "
                    + "; ".join(problems)
                )
        if target_framework == "claude":
            # claude's agents dir is .claude/agents -> project root two levels up;
            # emit_mcp_artifact writes .claude/mcp-servers.agentteams.json there,
            # preserving authorization records via its own skip/atomic semantics.
            mcp_res = emit_mcp_artifact(
                servers=cai_mcp,
                features=["claude:mcp"],
                output_root=target_dir.parent.parent,
                dry_run=dry_run,
                overwrite=overwrite,
            )
            result.converted.extend(mcp_res.written)
            result.skipped.extend(mcp_res.skipped)
            result.errors.extend(mcp_res.errors)
        # Other targets: servers persist in the CAI document itself (Phase E
        # canonical keeps them); goose render-time wiring needs the goose:mcp
        # opt-in token and stays a manifest-path concern.

    if runtime_handoff_agents:
        # Manifest-delivery sidecar — identical shape to the one
        # cli/render_pipeline.py emits for the same frameworks.
        sidecar = target_dir.parent / "references" / "runtime-handoffs.json"
        if sidecar.exists() and not overwrite:
            result.skipped.append(str(sidecar))
        else:
            payload = json.dumps(
                {
                    "schema_version": "1.0",
                    "framework": target_framework,
                    "project_name": "InteropProject",
                    "agents": runtime_handoff_agents,
                },
                indent=2,
            ) + "\n"
            if not dry_run:
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                sidecar.write_text(payload, encoding="utf-8")
            result.converted.append(str(sidecar))

    return result


def run_interop(
    source_dir: Path,
    target_framework: str,
    target_dir: Path,
    *,
    source_framework: str | None = None,
    mode: str = "direct",
    dry_run: bool = False,
    overwrite: bool = False,
) -> InteropResult:
    """Run interop conversion with optional bundle artifact generation."""
    if mode not in {"direct", "bundle"}:
        raise ValueError("interop mode must be 'direct' or 'bundle'")

    cai = export_to_cai(source_dir, source_framework=source_framework)
    result = import_from_cai(
        cai,
        target_framework,
        target_dir,
        dry_run=dry_run,
        overwrite=overwrite,
    )

    if mode == "bundle":
        # F.5: bundle artifacts land under target_dir/references/ — inside a
        # canonical directory they would be re-read as captured references on
        # load_canonical, silently corrupting the round trip. Refuse clearly
        # rather than invent a placement convention the plan doesn't define.
        if target_framework == "canonical":
            raise ValueError(
                "bundle mode is not supported for the canonical target: bundle "
                "artifacts would land inside the canonical directory and corrupt "
                "its references/ tree on load. Use --interop-mode direct."
            )
        _write_bundle_artifacts(
            cai=cai,
            target_framework=target_framework,
            target_dir=target_dir,
            dry_run=dry_run,
            result=result,
        )

    return result


def _write_bundle_artifacts(
    *,
    cai: dict[str, Any],
    target_framework: str,
    target_dir: Path,
    dry_run: bool,
    result: InteropResult,
) -> None:
    source_framework = str(cai.get("source_framework", "unknown"))
    bundle_dir = target_dir / "references" / "interop" / f"{source_framework}-to-{target_framework}"

    routing_map = {
        "source_framework": source_framework,
        "target_framework": target_framework,
        "agents": [
            {"slug": a.get("slug", ""), "source_path": a.get("source_path", "")}
            for a in cai.get("agents", [])
        ],
    }
    instructions_map = {
        "source_name": cai.get("instructions_binding", {}).get("source_name", ""),
        "target_name": _instructions_target_name(target_framework),
    }
    interop_manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_framework": source_framework,
        "target_framework": target_framework,
        "agent_count": len(cai.get("agents", [])),
    }
    compatibility_report = (
        "# Compatibility Report\n\n"
        "- Body prose is preserved.\n"
        "- Framework wrappers/front matter are translated to target conventions.\n"
        "- Handoff metadata may be reduced for non-handoff targets.\n"
    )

    bundle_files = {
        "team-manifest.cai.json": json.dumps(cai, indent=2),
        "interop-manifest.json": json.dumps(interop_manifest, indent=2),
        "routing-map.json": json.dumps(routing_map, indent=2),
        "instructions-map.json": json.dumps(instructions_map, indent=2),
        "compatibility-report.md": compatibility_report,
    }

    for name, content in bundle_files.items():
        p = bundle_dir / name
        if not dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")
        result.bundle_files.append(str(p))


def _slug_from_filename(name: str) -> str:
    if name.endswith(".agent.md"):
        return name[: -len(".agent.md")]
    if name.endswith(".md"):
        return name[: -len(".md")]
    if name.endswith(".yaml"):  # goose recipes (F.1)
        return name[: -len(".yaml")]
    return name


def _strip_framework_wrappers(content: str) -> str:
    _, body = _parse_yaml_front_matter(content)
    body = _strip_handoffs_section(body)
    return body


def _strip_handoffs_section(content: str) -> str:
    """Strip a literal ``## Handoffs``-style section from *content*.

    Reuses ``FrameworkAdapter._strip_handoffs_section`` (``agentteams/frameworks/
    base.py``) rather than a locally re-derived regex. That shared implementation
    was hardened (see its ``_HANDOFFS_HEADING_RE`` docstring) after its own naive
    predecessor over-matched any heading merely starting with the word "Handoff"
    -- e.g. `conflict-auditor.template.md`'s `## Handoff Payload Conflict Codes` --
    and, lacking a fence-marker stop condition, ran past the intended heading and
    consumed an entire adjacent fenced section. This module had re-introduced
    exactly that same naive pattern independently; delegating to the shared,
    already-fixed helper closes the regression and prevents the two copies from
    drifting apart again.
    """
    return _FrameworkAdapter._strip_handoffs_section(content)


def _first_heading_or_title(content: str, slug: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return slug.replace("-", " ").title()


def _frontmatter_value(content: str, key: str) -> str:
    """Extract a single-line scalar value for *key* from the YAML front matter.

    Returns "" when there is no front matter or no such key. Surrounding quotes
    are stripped. Used so CAI export captures the agent's real name/description
    (not just the first heading) for round-trip fidelity.
    """
    yaml_text, _ = _parse_yaml_front_matter(content)
    if yaml_text is None:
        return ""
    m = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", yaml_text, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


# A.3/A.4 helpers extracted to interop_helpers.py (CH-07 module-size compliance)
from agentteams.interop_helpers import (
    capture_escape_hatches as _capture_escape_hatches,
    capture_references as _capture_references,
    serialize_raw_fm_key as _serialize_raw_fm_key,
    merge_sidecar_handoffs as _merge_sidecar_handoffs,
)
