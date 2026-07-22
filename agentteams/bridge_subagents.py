"""Active subagent-stub emitter for the copilot-vscode → claude bridge.

Phase 2 of the leak-driven revision. When the user works in Claude on a
copilot-vscode-canonical project (the architecture documented in
CLAUDE.md), this module emits thin Claude-subagent stubs into
``.claude/agents/`` — one per canonical copilot agent — whose body
*delegates* to the source agent body via a Read instruction.

Design constraints
------------------
- The canonical copilot ``.agent.md`` files are never modified.
- Stubs carry a ``source_sha256`` provenance header; drift detection is
  the responsibility of :mod:`agentteams.drift` (handled in a separate
  pass).
- Stubs are emitted only when host feature
  ``bridge:copilot-vscode-to-claude:subagents`` is selected via
  ``--target-host-features``. Default emission is unchanged.
- Workstream-expert slugs (``*-expert``) collapse into a single
  parametric ``workstream-expert.md`` stub that takes ``component`` as
  argument. The N copilot expert files remain as briefs on disk.
- Slug stability: stub filename mirrors the source slug (with the
  ``.agent.md`` suffix stripped to ``.md``).
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Mapping from copilot-vscode tools declaration to Claude allowed-tools names.
# Source: AUTHORING-GUIDE.md tool-mapping table.
_TOOLS_MAP: dict[str, list[str]] = {
    "read":    ["Read"],
    "search":  ["Grep", "Glob"],
    "edit":    ["Edit", "Write"],
    "execute": ["Bash"],
    "todo":    ["TodoWrite"],
    "agent":   ["Task"],
}


def _parse_tools_list(raw: str) -> list[str]:
    """Parse a tools: front-matter value like \"['read', 'edit']\" into a list."""
    raw = raw.strip()
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
    except (ValueError, SyntaxError):
        pass
    return []


def _tools_to_allowed(tools_raw: str) -> list[str]:
    """Map source agent tools list to Claude allowed-tools names, deduplicated and ordered."""
    names = _parse_tools_list(tools_raw)
    seen: set[str] = set()
    result: list[str] = []
    for t in names:
        for ct in _TOOLS_MAP.get(t.lower(), []):
            if ct not in seen:
                seen.add(ct)
                result.append(ct)
    return result

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)
_BLOCK_LIST_LINE_RE = re.compile(r"^\s*-\s+(.+)")
_SOURCE_SUFFIX = ".agent.md"

# Slugs treated as workstream experts (collapsed into a parametric stub).
# Pattern: ends in ``-expert`` (matches the canonical Workstream Expert
# archetype used by agentteams). Matches by slug, not by file content.
_EXPERT_SUFFIX = "-expert"


@dataclass
class StubEmissionResult:
    """Summary of a stub-emission run."""

    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    experts_collapsed: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Very small YAML-flat parser. Handles ``key: value`` on individual lines
    and block-style sequences (``key:`` followed by indented ``- item`` lines).

    Avoids a YAML dep. The block-sequence path handles ``tools:`` authored in
    block style, which is legal YAML and produced by editors and agent files
    created outside agentteams tooling.  Block-style items are converted to a
    Python list literal (``['read', 'edit']``) so that ``_parse_tools_list()``
    can process them unchanged via ``ast.literal_eval``.

    Inline flow-sequence style (``tools: ['read', 'edit']``) is unaffected.
    """
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    lines = m.group("body").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        val = val.strip().strip("'").strip('"')
        if not val:
            # Possibly a block-style sequence key. Consume all subsequent
            # indented list lines (``  - item``) and convert to a Python list
            # literal so that _parse_tools_list() can handle the result
            # without modification.
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                bm = _BLOCK_LIST_LINE_RE.match(lines[j])
                if bm:
                    items.append(bm.group(1).strip().strip("'").strip('"'))
                    j += 1
                else:
                    break
            if items:
                val = repr(items)
                i = j
            else:
                i += 1
        else:
            i += 1
        meta[key.strip()] = val
    return meta, text[m.end():]


def _union_tools_from_sources(sources: Iterable[Path]) -> list[str]:
    """Return the ordered union of Claude allowed-tool names from a set of source agent files.

    Reads each file's front matter, extracts the ``tools:`` field (if present),
    maps it through ``_tools_to_allowed``, and merges results preserving first-seen
    order while deduplicating.
    """
    seen: set[str] = set()
    result: list[str] = []
    for src in sources:
        text = src.read_text(encoding="utf-8")
        meta, _ = _parse_front_matter(text)
        raw = meta.get("tools", "")
        if not raw:
            continue
        for ct in _tools_to_allowed(raw):
            if ct not in seen:
                seen.add(ct)
                result.append(ct)
    return result


def _slug_from_source(name: str) -> str:
    """Map ``cleanup.agent.md`` → ``cleanup``; safe across .md fallback."""
    if name.endswith(_SOURCE_SUFFIX):
        return name[: -len(_SOURCE_SUFFIX)]
    if name.endswith(".md"):
        return name[: -3]
    return name


def _short_description(meta: dict[str, str], slug: str) -> str:
    desc = meta.get("description") or meta.get("name") or slug
    # Claude subagent descriptions are short; collapse whitespace and trim.
    desc = " ".join(desc.split())
    if len(desc) > 200:
        desc = desc[:197].rstrip() + "..."
    return desc


def _stub_body(*, source_rel_path: str, role_desc: str) -> str:
    """Render the body that delegates to the canonical source agent."""
    return (
        f"# Bridged agent (copilot-vscode → claude)\n\n"
        f"This is a Claude subagent stub. The canonical agent definition lives at:\n\n"
        f"    {source_rel_path}\n\n"
        f"**On invocation, first read the source file at the path above** (relative to "
        f"this repository's root), then perform the work it describes. Honor every "
        f"constraint and protocol stated in the canonical body; the stub adds no policy "
        f"of its own.\n\n"
        f"- Source role: {role_desc}\n\n"
        f"Runtime context note: you are invoked via the copilot-vscode → claude bridge "
        f"from a Claude runtime. Where the source body refers to chat-mode invocations "
        f"or Copilot-specific UI affordances, translate to the equivalent Claude tool "
        f"surface (Read/Edit/Bash/Agent) while preserving the intent.\n"
    )


def _render_subagent_stub(
    *,
    slug: str,
    description: str,
    source_rel_path: str,
    source_sha256: str,
    tools_raw: str | None = None,
) -> str:
    """Render a complete Claude subagent stub file."""
    fm_lines = [
        "---",
        f"name: {slug}",
        f"description: {description}",
        f"source: {source_rel_path}",
        f"source_sha256: {source_sha256}",
        "bridge: copilot-vscode-to-claude",
    ]
    if tools_raw:
        allowed = _tools_to_allowed(tools_raw)
        if allowed:
            fm_lines.append(f"allowed-tools: {', '.join(allowed)}")
    fm_lines += ["---", ""]
    body = _stub_body(
        source_rel_path=source_rel_path,
        role_desc=description,
    )
    return "\n".join(fm_lines) + body


def _render_workstream_expert_stub(
    *, expert_count: int, source_dir_rel: str, tools_union: list[str] | None = None
) -> str:
    """Render the single parametric workstream-expert stub.

    Replaces the N individual ``*-expert.md`` stubs with one subagent that
    takes a ``component`` argument identifying which canonical brief to
    load.
    """
    fm_lines = [
        "---",
        "name: workstream-expert",
        "description: Parametric workstream-expert subagent; takes a component slug and loads the corresponding canonical brief.",
        f"source_dir: {source_dir_rel}",
        f"collapsed_experts: {expert_count}",
        "bridge: copilot-vscode-to-claude",
    ]
    if tools_union:
        fm_lines.append(f"allowed-tools: {', '.join(tools_union)}")
    fm_lines += ["---", ""]
    body = (
        "# Workstream Expert (parametric, bridged)\n\n"
        "This subagent stands in for N component-specific Workstream Expert agents in "
        "the canonical copilot-vscode source. Each invocation must include a "
        "`component` slug identifying which brief to load.\n\n"
        "**On invocation:**\n\n"
        "1. Read the canonical brief at "
        f"`{source_dir_rel}/<component>-expert.agent.md`.\n"
        "2. Follow every constraint and protocol stated in that body.\n"
        "3. Translate Copilot-runtime affordances (chat-mode invocations, etc.) to the "
        "equivalent Claude tool surface (Read/Edit/Bash/Agent).\n"
        "4. Treat the brief as authoritative; the stub adds no policy of its own.\n\n"
        "Drift detection: the bridge maintains per-brief SHA-256 provenance separately; "
        "if a brief changes, re-emit the bridge.\n"
    )
    return "\n".join(fm_lines) + body


def collect_source_agents(source_dir: Path) -> list[Path]:
    """Return canonical ``*.agent.md`` files from the source directory."""
    if not source_dir.is_dir():
        return []
    return sorted(p for p in source_dir.iterdir() if p.is_file() and p.name.endswith(_SOURCE_SUFFIX))


def emit_subagent_stubs(
    *,
    source_dir: Path,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = True,
) -> StubEmissionResult:
    """Emit Claude subagent stubs delegating to copilot-vscode source agents.

    Stubs land in ``<output_root>/.claude/agents/``. Workstream-experts
    (``*-expert.agent.md``) are collapsed into one parametric stub.

    Parameters
    ----------
    source_dir : Path
        Canonical copilot-vscode agents directory (typically
        ``<project>/.github/agents/``).
    output_root : Path
        Project root; stubs land under ``output_root / .claude / agents``.
    dry_run : bool
        Compute and report the action set without writing.
    overwrite : bool
        When False, existing stub files are skipped (idempotent re-runs).
        When True, stubs are unconditionally regenerated (default; matches
        ``--bridge-refresh`` semantics).
    """
    result = StubEmissionResult()
    source_dir = source_dir.resolve()
    target_dir = (output_root / ".claude" / "agents").resolve()
    sources = collect_source_agents(source_dir)
    if not sources:
        return result

    experts: list[Path] = []
    non_experts: list[Path] = []
    for src in sources:
        slug = _slug_from_source(src.name)
        if slug.endswith(_EXPERT_SUFFIX):
            experts.append(src)
        else:
            non_experts.append(src)

    for src in non_experts:
        slug = _slug_from_source(src.name)
        text = src.read_text(encoding="utf-8")
        meta, _ = _parse_front_matter(text)
        description = _short_description(meta, slug)
        sha = _file_sha256(src)
        try:
            rel = src.relative_to(output_root)
            source_rel = rel.as_posix()
        except ValueError:
            source_rel = src.as_posix()
        stub_content = _render_subagent_stub(
            slug=slug,
            description=description,
            source_rel_path=source_rel,
            source_sha256=sha,
            tools_raw=meta.get("tools"),
        )
        out_path = target_dir / f"{slug}.md"
        if out_path.exists() and not overwrite:
            result.skipped.append(str(out_path))
            continue
        if not dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(stub_content, encoding="utf-8")
        result.written.append(str(out_path))

    if experts:
        # Parametric collapse.
        try:
            source_dir_rel = source_dir.relative_to(output_root).as_posix()
        except ValueError:
            source_dir_rel = source_dir.as_posix()
        expert_slugs = sorted(_slug_from_source(e.name) for e in experts)
        result.experts_collapsed = expert_slugs
        tools_union = _union_tools_from_sources(experts)
        stub_content = _render_workstream_expert_stub(
            expert_count=len(experts),
            source_dir_rel=source_dir_rel,
            tools_union=tools_union,
        )
        out_path = target_dir / "workstream-expert.md"
        if out_path.exists() and not overwrite:
            result.skipped.append(str(out_path))
        else:
            if not dry_run:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(stub_content, encoding="utf-8")
            result.written.append(str(out_path))

    return result


def detect_stub_drift(
    *,
    source_dir: Path,
    output_root: Path,
) -> list[dict[str, str]]:
    """Return drift records (one per stub whose source SHA changed).

    Each record: ``{slug, stub_path, source_path, recorded_sha, current_sha}``.
    Empty list means no drift.
    """
    drift: list[dict[str, str]] = []
    target_dir = (output_root / ".claude" / "agents").resolve()
    if not target_dir.is_dir():
        return drift
    for stub in sorted(target_dir.glob("*.md")):
        text = stub.read_text(encoding="utf-8")
        meta, _ = _parse_front_matter(text)
        if meta.get("bridge") != "copilot-vscode-to-claude":
            continue
        recorded = meta.get("source_sha256", "")
        src_rel = meta.get("source", "")
        if not src_rel:
            continue
        src_path = (output_root / src_rel).resolve()
        if not src_path.exists():
            drift.append(
                {
                    "slug": meta.get("name", stub.stem),
                    "stub_path": str(stub),
                    "source_path": str(src_path),
                    "recorded_sha": recorded,
                    "current_sha": "<missing>",
                }
            )
            continue
        current = _file_sha256(src_path)
        if current != recorded:
            drift.append(
                {
                    "slug": meta.get("name", stub.stem),
                    "stub_path": str(stub),
                    "source_path": str(src_path),
                    "recorded_sha": recorded,
                    "current_sha": current,
                }
            )
    return drift


__all__ = [
    "StubEmissionResult",
    "_parse_tools_list",
    "_tools_to_allowed",
    "_union_tools_from_sources",
    "collect_source_agents",
    "detect_stub_drift",
    "emit_subagent_stubs",
]
