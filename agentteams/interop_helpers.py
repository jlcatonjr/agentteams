"""interop_helpers.py — helper functions extracted from interop.py (CH-07).

A.3/A.4 helpers for escape-hatch capture, reference walking, raw-front-matter
serialization, and sidecar handoff merging. Extracted to keep interop.py under
the 1000-line CH-07 module ceiling.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Any

from agentteams.yaml_frontmatter import (
    parse_yaml_front_matter as _parse_yaml_front_matter,
)


# A.3: Escape-hatch capture for front-matter keys not already modeled.
# Keys that already have dedicated CAI fields and must NOT appear in
# raw_front_matter (they'd be redundant and could drift).
_MODELED_FM_KEYS = frozenset({
    "name", "description", "handoffs",
})


def capture_escape_hatches(
    content: str, framework: str,
) -> tuple[dict[str, Any], str | None, str | None]:
    """Capture the three CAI schema escape hatches from front matter.

    Returns ``(raw_front_matter, capabilities_raw, model_hint)``:
    - ``raw_front_matter``: any front-matter key not in ``_MODELED_FM_KEYS``,
      preserving the original parsed value (dict from YAML).
    - ``capabilities_raw``: the original capability-string for the framework
      (e.g. the raw ``tools:`` line value), or ``None``.
    - ``model_hint``: the ``model:`` front-matter value if present, or ``None``.

    Called only from the standard Markdown path (``parse_agent_source``
    returned ``None``); goose recipes have no front matter.

    Note: ``tools`` and ``allowed-tools`` are NOT in ``_MODELED_FM_KEYS``
    because while they ARE modeled into ``capabilities.tool_scopes``, their
    raw value is needed for ``capabilities.raw`` — so they are intercepted
    here for the raw capture but excluded from ``raw_front_matter``.
    """
    yaml_text, _ = _parse_yaml_front_matter(content)
    if yaml_text is None:
        return {}, None, None

    # Use canonical.py's YAML loader (PyYAML when available, fallback otherwise)
    from agentteams.canonical import _load_yaml_block

    fm = _load_yaml_block(yaml_text)
    if not isinstance(fm, dict):
        return {}, None, None

    raw_front_matter: dict[str, Any] = {}
    cap_raw: str | None = None
    model_hint: str | None = None
    _cap_keys = {"tools", "allowed-tools"}

    for key, value in fm.items():
        if key in _MODELED_FM_KEYS:
            continue
        if key in _cap_keys:
            # Already modeled via capabilities.tool_scopes, but capture the
            # raw string for capabilities.raw.
            if cap_raw is None:
                cap_raw = str(value) if not isinstance(value, str) else value
            continue
        if key == "model":
            # model_hint: keep the first scalar if it's a string, or
            # join a list. Preserve as-is otherwise.
            if isinstance(value, str):
                model_hint = value
            elif isinstance(value, list) and value:
                model_hint = str(value[0]) if len(value) == 1 else json.dumps(value)
            else:
                model_hint = str(value) if value is not None else None
        raw_front_matter[key] = value

    return raw_front_matter, cap_raw, model_hint


def capture_references(source_dir: Path) -> list[dict[str, Any]]:
    """Walk a native team's ``references/`` directory into the CAI ``references``
    list (A.3, report section 4.1).

    Each file becomes ``{"rel_path": str, "content": str}`` — the same shape
    ``canonical.materialize_canonical`` writes and ``load_canonical`` reads back.
    Returns an empty list when no ``references/`` directory exists.
    """
    refs_dir = source_dir / "references"
    if not refs_dir.is_dir():
        return []
    refs: list[dict[str, Any]] = []
    for p in sorted(refs_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_path = str(p.relative_to(source_dir))
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Binary or unreadable file — log and skip rather than crash the export.
            warnings.warn(
                f"references: skipping unreadable file {rel_path}", stacklevel=2
            )
            continue
        refs.append({"rel_path": rel_path, "content": content})
    return refs


#: Characters that end a line for a YAML or line-based front-matter parser.
_LINE_BREAK_RE = re.compile("[\r\n\v\f\x85\u2028\u2029]")
#: A front-matter key interop may restore: a plain identifier, so it can't carry YAML syntax or a line break.
_SAFE_FM_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def one_line(value: str) -> str:
    """A scalar safe to write on one front-matter line: whitespace runs that contain a line break become a space.

    A line break inside a value written into an imported agent's header would start a new line there, which
    every first-match front-matter parser reads as a key (e.g. a ``tools:`` line above the declared one).

    Args:
        value: The value to write.

    Returns:
        *value* unchanged when it has no line break; otherwise its words joined by single spaces.

    Raises:
        Nothing.
    """
    return " ".join(value.split()) if _LINE_BREAK_RE.search(value) else value


def safe_fm_key(key: Any) -> bool:
    """Whether a captured front-matter key may be restored on import (a plain identifier).

    Args:
        key: The captured key.

    Returns:
        True for ``[A-Za-z][A-Za-z0-9_-]*``.

    Raises:
        Nothing.
    """
    return isinstance(key, str) and _SAFE_FM_KEY_RE.fullmatch(key) is not None


#: What forces quoting: a YAML indicator first, ``: `` / `` #`` / a trailing ``:`` anywhere, a quote or backslash,
#: or surrounding whitespace. Anything else (letters in any script, ``—``, ``(copilot)``) is read back unchanged bare.
_NOT_PLAIN_RE = re.compile(r"""^[-?:,\[\]{}#&*!|>'"%@`\s]|: |\s#|:$|["'\\]|\s$""")


def quoted(value: Any) -> str:
    """A string as a one-line double-quoted YAML scalar that can't break out of its quotes.

    The rule interop has always used for descriptions and handoffs (``"`` becomes ``'``), plus line breaks
    collapsed (:func:`one_line`) and backslashes escaped, so a trailing ``\\`` can't swallow the closing quote.

    Args:
        value: The value to write.

    Returns:
        The quoted scalar.

    Raises:
        Nothing.
    """
    return '"' + one_line(str(value)).replace("\\", "\\\\").replace('"', "'") + '"'


def scalar(value: str) -> str:
    """A string as a YAML scalar: bare unless :data:`_NOT_PLAIN_RE` finds YAML syntax in it, else :func:`quoted`.

    Args:
        value: The value to write.

    Returns:
        The scalar text for one header line.

    Raises:
        Nothing.
    """
    value = one_line(value)
    return value if value and not _NOT_PLAIN_RE.search(value) else quoted(value)


def collapsed_prompt_notices(slug: str, handoffs: list[dict[str, Any]]) -> list[str]:
    """Notices for handoff prompts :func:`handoff_header_lines` writes on one line (their line breaks are lost).

    Args:
        slug: The agent's slug.
        handoffs: ``[{label, agent, prompt, send}]``.

    Returns:
        One notice per collapsed prompt.

    Raises:
        Nothing.
    """
    return [f"{slug}: multi-line handoff prompt to {h['agent']} written on one line"
            for h in handoffs if one_line(str(h["prompt"])) != str(h["prompt"])]


def handoff_header_lines(handoffs: list[dict[str, Any]]) -> list[str]:
    """The inline ``handoffs:`` block, in the shape ``FrameworkAdapter.extract_handoffs`` parses.

    Every string is written on one line (:func:`one_line`) with ``"`` replaced by ``'``, so a handoff label,
    agent or prompt can't break out of its quotes or onto a line of its own.

    Args:
        handoffs: ``[{label, agent, prompt, send}]``.

    Returns:
        The header lines, starting with ``handoffs:``.

    Raises:
        Nothing.
    """
    lines = ["handoffs:"]
    for h in handoffs:
        lines += [f"  - label: {quoted(h['label'])}", f"    agent: {quoted(h['agent'])}",
                  f"    prompt: {quoted(h['prompt'])}", f"    send: {'true' if h['send'] else 'false'}"]
    return lines


def serialize_raw_fm_key(key: str, value: Any) -> str:
    """Serialize a raw_front_matter key-value pair into a YAML header line.

    A.2: Restores captured escape-hatch front-matter keys (user-invocable,
    model, agents:, etc.) back into the import-side YAML header so framework
    adapters like _ensure_yaml_front_matter don't overwrite them with defaults.

    Handles scalars (``user-invocable: true``), flow lists
    (``model: ["Claude Opus 4.8 (copilot)"]``), and block lists
    (``agents:\\n  - slug1\\n  - slug2``).
    """
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, str):
        return f"{key}: {scalar(value)}"  # one line; bare only when plainly safe, else quote-safe
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        # Use flow notation for short lists (model), block for longer ones (agents:)
        if len(value) <= 2 and all(isinstance(v, str) for v in value):
            items = ", ".join(quoted(v) for v in value)
            return f"{key}: [{items}]"
        # Block list
        lines = [f"{key}:"]
        for item in value:
            lines.append(f"  - {scalar(item)}" if isinstance(item, str) else f"  - {json.dumps(item)}")
        return "\n".join(lines)
    # Fallback: JSON-encode complex values
    return f"{key}: {json.dumps(value)}"


def merge_sidecar_handoffs(agents: list[dict[str, Any]], source_dir: Path) -> None:
    """Read ``references/runtime-handoffs.json`` and merge handoffs into agents.

    A.4 (report section 4.3): the sidecar is written by ``import_from_cai`` for
    manifest-delivery frameworks (claude, copilot-cli, agents-md; codex too until it
    moved to native delivery on 2026-09-29) but was never read back by
    ``export_to_cai``, so handoffs vanished on every native→canonical round trip.

    The sidecar sits at ``source_dir.parent / "references" / "runtime-handoffs.json"``
    and has the shape::

        {"agents": [{"agent": "slug", "handoffs": [{"label", "agent", "prompt", "send"}]}]}

    Only merges into agents that currently have **no** inline handoffs — inline
    handoffs (from native-delivery frameworks) always take priority.
    """
    sidecar = source_dir.parent / "references" / "runtime-handoffs.json"
    if not sidecar.is_file():
        return
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    sidecar_agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(sidecar_agents, list):
        return
    # Build a lookup: slug -> handoffs from the sidecar
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for entry in sidecar_agents:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("agent", "")).strip()
        if not slug:
            continue
        raw_handoffs = entry.get("handoffs") or []
        if not isinstance(raw_handoffs, list):
            continue
        normalized = [
            {
                "to": str(h.get("agent", "")).strip(),
                "label": h.get("label") or None,
                "prompt": str(h.get("prompt", "") or ""),
                "send": bool(h.get("send", False)),
            }
            for h in raw_handoffs
            if isinstance(h, dict) and str(h.get("agent", "")).strip()
        ]
        if normalized:
            by_slug[slug] = normalized
    if not by_slug:
        return
    for agent in agents:
        slug = agent.get("slug", "")
        # Only merge if the agent has no inline handoffs (manifest-delivery
        # frameworks write handoffs ONLY to the sidecar, so inline is empty).
        if not agent.get("handoffs") and slug in by_slug:
            agent["handoffs"] = by_slug[slug]


# ---------------------------------------------------------------------------
# Projection fidelity (baseAgent handoff item 3, 2026-09-29): pinned sync re-projected
# canonical onto every framework with overwrite=True, so an agent nobody changed was still
# re-rendered — narrowing bespoke tools (``runCommands`` dropped), re-ordering front matter —
# and another framework's instructions replaced this framework's instruction file, deleting
# its USER-EDITABLE regions. ``import_from_cai(..., preserve_existing=True)`` uses these.
# ---------------------------------------------------------------------------

def _semantic_agent(agent: dict[str, Any]) -> str:
    # The WHOLE exported record — raw tool strings, raw front matter (model, permissionMode,
    # user-invocable, …), handoff labels/send — minus only where the file was read from.
    # Anything narrower lets a hand-widened grant survive a pin-wins re-projection
    # (@security C1 / @adversarial #2, 2026-09-29): C-3.
    record = {k: v for k, v in agent.items() if k != "source_path"}
    return json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)


def agent_unchanged(canonical_agent: dict[str, Any], native_agent: dict[str, Any] | None) -> bool:
    """Whether projecting *canonical_agent* would change the agent already on disk.

    Compares the complete exported record (everything except ``source_path``): raw tool
    strings, raw front matter, model hint, handoff labels and send flags included. Equal means
    the existing file is left byte-for-byte untouched — in practice the pin framework's own
    unchanged files, whose bespoke front matter a re-render would normalize. Any difference,
    including a hand-edit that widens a grant, is re-projected (pin wins).

    Args:
        canonical_agent: The agent entry from the canonical CAI document.
        native_agent: The same slug as exported from the target directory, or ``None``.

    Returns:
        True when the native file already represents the canonical agent.
    """
    return native_agent is not None and _semantic_agent(canonical_agent) == _semantic_agent(native_agent)


_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def require_safe_slug(slug: str) -> None:
    """Refuse a CAI slug that could escape the target directory (``../``, ``/``, ``..``).

    The CAI schema puts no pattern on ``slug``, and a slug becomes a file or directory name
    under the target — for codex skills, under the repository root (@security C4, 2026-09-29).

    Args:
        slug: An agent or skill slug from a CAI document.

    Raises:
        ValueError: If the slug is not a single safe path component.
    """
    if not _SAFE_SLUG_RE.match(slug) or ".." in slug:
        raise ValueError(f"unsafe CAI slug {slug!r}: must match {_SAFE_SLUG_RE.pattern} and not contain '..'")


def contained_path(base: Path, rel_path: str) -> Path:
    """Join *rel_path* under *base*, refusing anything that would land outside it.

    A skill's co-located files carry a ``rel_path`` from the CAI document; unchecked, a crafted
    ``../../x`` wrote anywhere (reachable through ``--interop-skills-only``, 2026-09-29).

    Args:
        base: The directory the file must stay within (a skill directory).
        rel_path: The captured relative path.

    Returns:
        The joined path.

    Raises:
        ValueError: If *rel_path* is absolute or escapes *base*.
    """
    rel = Path(rel_path)
    joined = base / rel
    # Lexical check first, then the resolved one (catches a symlink already sitting in the target).
    if rel.is_absolute() or ".." in rel.parts or not joined.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"unsafe skill file path {rel_path!r}: must stay inside {base}")
    return joined


def raw_scopes(raw: str) -> list[str] | None:
    """Canonical tool scopes a raw ``tools:`` string maps to (bracket or comma form).

    Args:
        raw: A captured raw tools value, e.g. ``['read', 'edit']`` or ``Read, Edit``.

    Returns:
        Canonical-order scopes (empty when nothing maps). ``None`` for a value with a line break: written into a
        header it would add a second ``tools:`` line that the first-match check below never sees, so it never
        equals an agent's scopes and callers fall back to the canonical list.
    """
    from agentteams import capability_map

    if _LINE_BREAK_RE.search(raw):
        return None
    fm = f"---\ntools: {raw}\n---\n"
    return (capability_map.canonical_tools_for_copilot_vscode(fm)
            or capability_map.canonical_tools_for_claude(fm) or [])


def merge_instruction_file(rendered: str, existing: str) -> tuple[str | None, str]:
    """Fence-merge a projected instruction file into the one already on disk.

    Fenced (template-owned) regions come from *rendered*; everything outside a fence — the
    USER-EDITABLE Constitutional/Project-Specific rules a framework's own instruction file
    carries — is kept from *existing*. When *existing* has no parseable fences it is not
    replaced at all (it may be project-owned).

    Args:
        rendered: The instruction content being projected.
        existing: The instruction file currently on disk.

    Returns:
        ``(content, notice)``: the content to write (``None`` = leave the file as it is) and a
        human-readable notice ("" when nothing needs saying).
    """
    from agentteams.fences import _merge_fenced_content

    result = _merge_fenced_content(rendered, existing, preserve_on_shrink=True)
    if result.parse_errors or not result.merged_content:
        return None, "existing instruction file has no mergeable AGENTTEAMS fences; left untouched"
    if result.merged_content == existing:
        return None, ""
    return result.merged_content, ""


# Carved from interop.py (CH-07, 2026-09-29); re-imported there as _capture_mcp_servers.
def capture_mcp_servers(source_dir: Path) -> list[dict[str, Any]]:
    """Capture MCP servers from the pipeline's managed artifact, if present (D.3).

    ``mcp_emit`` writes ``.claude/mcp-servers.agentteams.json`` at the project
    root. Depending on how deep *source_dir* sits (``.claude/agents`` in-repo vs
    deeper bridge layouts), the artifact is one or two levels up. First hit
    wins; unreadable or absent artifacts capture nothing (honestly degraded).

    Args:
        source_dir: The source team's agents directory.

    Returns:
        The captured server definitions, defaults normalized (empty when none).
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


# Carved from interop.py (CH-07, 2026-09-29); re-exported there unchanged.
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
    if ".codex" in parts:           # .codex/agents/<name>.toml — Codex custom agents
        return "codex"
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


# Carved from interop.py (CH-07, 2026-09-29): shared by the full import and --interop-skills-only.
def import_skills(
    cai: dict[str, Any],
    adapter: Any,
    target_dir: Path,
    result: Any,
    manifest: dict[str, Any],
    *,
    dry_run: bool,
    overwrite: bool,
) -> None:
    """Re-emit the CAI ``skills[]`` through the adapter's skill hook (D.1).

    Placement is the adapter's ``skills_dir`` (Claude ``.claude/skills``, Codex
    ``<root>/.agents/skills``). The body travels verbatim; the front matter is normalized by
    ``render_skill_file``, keeping the skill's authored ``description``. Frameworks without a
    skill concept drop skills honestly (their exports capture none). Slugs and co-located file
    paths are validated so nothing lands outside the skill directory.

    Args:
        cai: The CAI document.
        adapter: The target framework adapter.
        target_dir: The target agents directory.
        result: The ``InteropResult`` to record converted/skipped paths on.
        manifest: Import manifest stub (``project_name``).
        dry_run: Record without writing.
        overwrite: Replace existing skill files.

    Raises:
        ValueError: On an unsafe skill slug or co-located file path.
    """
    cai_skills = [s for s in (cai.get("skills") or []) if str(s.get("slug", "")).strip()]
    if cai_skills and adapter.has_skill_concept():
        for skill in cai_skills:
            slug = str(skill["slug"]).strip()
            require_safe_slug(slug)
            skills_root = adapter.skills_dir(target_dir)
            # Anchor on the skills root: a pre-planted symlink at <slug>/ or <slug>/SKILL.md
            # must not redirect the write (baseAgent @security, 2026-09-29). Co-located files
            # below are then checked against this already-contained directory.
            skill_dir = contained_path(skills_root, slug)
            dest = contained_path(skills_root, f"{slug}/SKILL.md")
            if dest.exists() and not overwrite:
                result.skipped.append(str(dest))
                continue
            captured_fm = skill.get("front_matter") or {}
            captured_name = str(captured_fm.get("name") or "").strip()
            skill_manifest = {
                "project_name": manifest.get("project_name", ""),
                "tool_agents": [{"tool_name": captured_name or slug, "slug": slug}],
                # Keep the skill's authored description (it is what triggers the skill).
                "skill_descriptions": {slug: str(captured_fm.get("description") or "").strip()},
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
                co_dest = contained_path(skill_dir, rel_path)
                if co_dest.exists() and not overwrite:
                    result.skipped.append(str(co_dest))
                    continue
                if not dry_run:
                    co_dest.parent.mkdir(parents=True, exist_ok=True)
                    co_dest.write_text(str(f.get("content", "")), encoding="utf-8")
                result.converted.append(str(co_dest))
