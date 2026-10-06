"""Routing rows for adopted ("bespoke") agents in the orchestrator body.

``--adopt-orphans`` registers pre-existing agent files into the roster. The roster alone
reaches only the Copilot orchestrator's front matter (``agents:``/``handoffs:``), which the
Claude adapter strips, so on Claude the orchestrator never routed to an adopted agent. This
module renders one routing-table row per adopted agent into the framework-neutral
``{ADOPTED_AGENT_ROUTING_ROWS}`` placeholder, so every orchestrator body names them.

An adopted file is content, not instruction (C-4). Only its ``name:`` and ``description:``
are read, and the description is flattened to one inert line before it is rendered. Nothing
else from the file reaches the orchestrator. Rows are sorted by slug so the output is
byte-stable across runs and frameworks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentteams.yaml_frontmatter import parse_yaml_front_matter

#: Maximum rendered length of an adopted agent's trigger text.
MAX_TRIGGER_CHARS = 200

#: Rendered when an adopted file carries no ``description:``.
NO_DESCRIPTION = "(no description; add a description: to the agent file)"

# A provenance comment an exporting harness writes right after the front matter,
# e.g. ``<!-- mathagents-export: ... -->``. Its prefix names the upstream maintainer.
_EXPORT_MARKER_RE = re.compile(r"^\s*<!--\s*([A-Za-z0-9_-]+)-export\s*:", re.MULTILINE)
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][0-9+-]*$")
_TOML_KEY_RE = re.compile(r'^(name|description)\s*=\s*"((?:[^"\\]|\\.)*)"\s*$', re.MULTILINE)
# A routing row an earlier adopt run rendered into an on-disk orchestrator.
_ADOPTED_ROW_RE = re.compile(r"`@([a-z0-9][a-z0-9-]*)` \*\(adopted\)\*")


def _top_level_scalars(yaml_text: str, keys: frozenset[str]) -> dict[str, str]:
    """Return the requested top-level scalar values from flat YAML text.

    Handles plain, quoted and block (``|``/``>``) scalars; block lines are joined
    with spaces. Nested keys and sequences are ignored.
    """
    found: dict[str, str] = {}
    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        m = _TOP_LEVEL_KEY_RE.match(lines[i])
        i += 1
        if not m or m.group(1) not in keys or m.group(1) in found:
            continue
        val = m.group(2).strip()
        if _BLOCK_SCALAR_RE.match(val):
            block: list[str] = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                block.append(lines[i].strip())
                i += 1
            val = " ".join(part for part in block if part)
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        found[m.group(1)] = val
    return found


def read_adopted_agent_metadata(path: Path) -> dict[str, str]:
    """Read the routing metadata of one adopted agent file.

    Args:
        path: An adopted agent file — Markdown with YAML front matter, a Goose
            recipe (``.yaml``, top level read directly) or a Codex ``.toml`` agent.

    Returns:
        A dict with ``name`` and ``description`` (each ``""`` when absent),
        ``upstream`` (the exporting harness named by a ``<!-- X-export: -->``
        provenance comment leading the body, else ``""``) and ``bridge`` (the
        ``bridge:`` front-matter value of a bridge-generated file, else ``""``).

    Raises:
        Nothing; an unreadable file yields all-empty values.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"name": "", "description": "", "upstream": "", "bridge": ""}
    if path.suffix == ".toml":
        toml = dict(_TOML_KEY_RE.findall(text))
        return {"name": toml.get("name", ""), "description": toml.get("description", ""), "upstream": "", "bridge": ""}
    if path.suffix in (".yaml", ".yml"):
        yaml_text, body = text, ""
    else:
        yaml_text, body = parse_yaml_front_matter(text)
        yaml_text = yaml_text or ""
    scalars = _top_level_scalars(yaml_text, frozenset({"name", "title", "description", "bridge", "source_dir"}))
    # Only a marker leading the body counts as provenance — not one quoted further down.
    head = "\n".join(body.lstrip("\n").splitlines()[:3])
    marker = _EXPORT_MARKER_RE.match(head)
    return {
        "name": scalars.get("name") or scalars.get("title", ""),
        "description": scalars.get("description", ""),
        "upstream": marker.group(1) if marker else "",
        # Bridge-generated (``bridge:`` key): its value, else its source dir, else "bridge".
        "bridge": (scalars.get("bridge") or scalars.get("source_dir") or "bridge") if "bridge" in scalars else "",
    }


def is_agent_file(path: Path) -> bool:
    """Return whether ``path`` is an agent definition eligible for adoption.

    A Goose recipe must declare ``version: "1.0.0"``; a Markdown agent must open with
    YAML front matter (Copilot agents may omit ``name:``, so any front matter counts);
    any other agent extension (Codex ``.toml``) is accepted as is. This keeps
    same-extension non-agent files such as ``SETUP-REQUIRED.md`` out of the roster.

    Args:
        path: A file in the framework agent directory.

    Returns:
        True when the file is an agent definition; False otherwise.

    Raises:
        Nothing; an unreadable file yields False.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if path.suffix in (".yaml", ".yml"):
        return 'version: "1.0.0"' in text
    if path.suffix == ".md":
        return parse_yaml_front_matter(text)[0] is not None
    return True


def _inert(text: str, limit: int) -> str:
    """Flatten text to one table-safe line of at most ``limit`` characters.

    Angle brackets are dropped outright, not just ``<!--``/``-->`` sequences, so no
    overlapping input such as ``<<!--!--`` can reassemble an HTML comment or an
    ``AGENTTEAMS`` fence marker that would corrupt the next merge.
    """
    text = re.sub(r"[<>`\"]", "", text).replace("|", "/")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def format_adopted_routing_rows(
    slugs: list[str],
    metadata: dict[str, dict[str, str]] | None = None,
    *,
    agent_dir: str = "",
    agent_ext: str = "",
) -> str:
    """Render the ``{ADOPTED_AGENT_ROUTING_ROWS}`` value for the orchestrator routing table.

    Args:
        slugs: Adopted agent slugs; rendered sorted and de-duplicated.
        metadata: Per-slug output of :func:`read_adopted_agent_metadata`. Missing
            slugs render with :data:`NO_DESCRIPTION`.
        agent_dir: Repository-relative directory holding the adopted files, used to
            point each row at its canonical file (e.g. ``.claude/agents``).
        agent_ext: The framework's agent file extension (e.g. ``.md``).

    Returns:
        ``""`` when there are no slugs (so a team without adopted agents renders
        byte-identically); otherwise one ``\\n``-prefixed Markdown table row per
        slug, ready to follow the routing table's last fixed row.

    Raises:
        Nothing.
    """
    metadata = metadata or {}
    rows: list[str] = []
    for slug in sorted(set(s for s in slugs if s)):
        meta = metadata.get(slug, {})
        trigger = _inert(meta.get("description", ""), MAX_TRIGGER_CHARS)
        name = _inert(meta.get("name", "").split(" — ")[0], 80)
        area = f"{name}: {trigger or NO_DESCRIPTION}" if name else (trigger or NO_DESCRIPTION)
        location = f"`{agent_dir.rstrip('/')}/{slug}{agent_ext}`" if agent_dir and agent_ext else "its own agent file"
        upstream = _inert(meta.get("upstream", ""), 40)
        bridge = _inert(meta.get("bridge", ""), 60)
        # An export marker wins over a bridge key: the upstream is canonical, the bridge projects it.
        if upstream:
            note = f"Adopted agent; canonical file {location}, maintained upstream in {upstream} — do not hand-edit"
        elif bridge:
            note = (f"Adopted agent; {location} is generated by the agentteams bridge from {bridge}"
                    " — regenerate via the bridge, do not hand-edit")
        else:
            note = f"Adopted agent; canonical file {location} — edit it there, never via a template"
        rows.append(f"\n| {area} | `@{slug}` *(adopted)* | {note} |")
    return "".join(rows)


def adoption_exclusions(manifest: dict[str, Any]) -> set[str]:
    """Return the slugs ``--adopt-orphans`` must never adopt for this build.

    These are the agent files this build EMITS (from ``output_files``, not the roster,
    so generated-but-non-roster files like team-builder are not mistaken for orphans)
    plus legacy ``tool-<slug>`` agents, which migrate to docs/skills instead.

    Args:
        manifest: The team manifest.

    Returns:
        The set of excluded slugs.

    Raises:
        Nothing.
    """
    suffix = ".agent.md"  # the source manifest always uses .agent.md paths
    emitted = {
        Path(f["path"]).name[: -len(suffix)]
        for f in manifest.get("output_files", [])
        if isinstance(f, dict) and str(f.get("path", "")).endswith(suffix)
    }
    return emitted | {ta["slug"] for ta in manifest.get("tool_agents", [])}

def discover_orphans(
    output_dir: Path, agent_ext: str, exclude: set[str]
) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Find adoptable agent files in the output dir being generated, with their metadata.

    Only files already on disk in ``output_dir`` are read — never fetched or unmerged
    content (an adopted file joins the team only once it is there).

    Args:
        output_dir: The framework agent directory this build renders into.
        agent_ext: The framework's agent file extension.
        exclude: Slugs this build emits itself, or must never adopt.

    Returns:
        ``(sorted orphan slugs, {slug: read_adopted_agent_metadata(...)})``.

    Raises:
        Nothing; unreadable files are skipped.
    """
    files = {
        p.name[: -len(agent_ext)]: p
        for p in output_dir.glob(f"*{agent_ext}")
        if p.name[: -len(agent_ext)] not in exclude and is_agent_file(p)
    }
    return sorted(files), {slug: read_adopted_agent_metadata(p) for slug, p in files.items()}


def previously_adopted(output_dir: Path, agent_ext: str) -> list[str]:
    """Return slugs an earlier adopt run rendered into the on-disk orchestrator.

    A later ``--update`` without ``--adopt-orphans`` rebuilds a manifest with no
    ``adopted_agents``; without this carry-forward it would re-render the routing
    fence empty and silently drop those rows. Only slugs whose agent file still
    exists are returned, so ``--prune`` stays the removal path.

    Args:
        output_dir: The framework agent directory.
        agent_ext: The framework's agent file extension.

    Returns:
        Sorted slugs; ``[]`` when there is no orchestrator or no adopted row.

    Raises:
        Nothing; an unreadable orchestrator yields ``[]``.
    """
    try:
        text = (output_dir / f"orchestrator{agent_ext}").read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted(
        slug for slug in set(_ADOPTED_ROW_RE.findall(text))
        if (output_dir / f"{slug}{agent_ext}").is_file() and is_agent_file(output_dir / f"{slug}{agent_ext}")
    )


def agent_dir_label(output_dir: Path, project_root: Path) -> str:
    """Return ``output_dir`` relative to ``project_root``, for citing canonical files.

    Args:
        output_dir: The framework agent directory being generated.
        project_root: The target project's root.

    Returns:
        The POSIX relative path (e.g. ``.claude/agents``), or the directory name
        when ``output_dir`` lies outside ``project_root``.

    Raises:
        Nothing.
    """
    try:
        return output_dir.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return output_dir.name


def set_adopted_rows(
    manifest: dict[str, Any],
    slugs: list[str],
    metadata: dict[str, dict[str, str]],
    *,
    agent_dir: str,
    agent_ext: str,
) -> None:
    """Render routing rows for ``slugs`` WITHOUT adding them to the roster.

    The merge-mode and carry-forward paths use this. Rows are advisory body text; the roster
    (``agent_slug_list`` → front-matter ``agents:``) is a capability grant that only the gated
    ``--overwrite --adopt-orphans`` path or ``cli.adopt_merge_gate`` may extend. In particular, rows
    re-read from an on-disk orchestrator are file content, not a clearance (security review
    condition 5), so they must never reach ``agents:``.

    Args:
        manifest: The team manifest; only ``auto_resolved_placeholders`` changes.
        slugs: Slugs to render rows for.
        metadata: Per-slug :func:`read_adopted_agent_metadata` output.
        agent_dir: Repository-relative agent directory cited in each row.
        agent_ext: The framework's agent file extension.

    Returns:
        None.

    Raises:
        Nothing.
    """
    manifest.setdefault("auto_resolved_placeholders", {})["ADOPTED_AGENT_ROUTING_ROWS"] = (
        format_adopted_routing_rows(slugs, metadata, agent_dir=agent_dir, agent_ext=agent_ext)
    )
