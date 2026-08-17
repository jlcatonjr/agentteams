"""canonical.py — durable exploded on-disk canonical agent format (plan §5.5).

The Canonical Agent Interface (CAI) dict produced by ``interop.export_to_cai``
is a single JSON document. For durable, human-editable storage this module
explodes it into a directory form and reads it back losslessly:

- ``team.cai.json`` — project-level data: schema_version, created_at,
  source metadata (source_framework / source_dir), instructions_binding,
  mcp_servers, framework_extensions, and any other top-level keys the CAI
  dict carries (forward-compatible pass-through). Agents, skills and
  references are NOT duplicated here — they explode to files.
- ``agents/<slug>.md`` — one file per agent: YAML front matter with the
  canonical capability vocabulary and handoffs, then the body markdown.
  A captured invariant-core fenced span is appended to the body (same
  convention as ``interop.import_from_cai``) so it stays visible, fenced
  markdown content.
- ``skills/<slug>/SKILL.md`` (+ co-located files, written verbatim) — one
  directory per skill.
- ``references/**`` — non-agent reference content carried in the CAI
  ``references`` list, restored at its recorded ``rel_path``.

Default location convention: ``<project>/.agentteams/canonical/``, reusing
the established ``.agentteams/`` control-directory convention
(``.agentteams/brief.json`` is the existing precedent).

Front matter uses Markdown + YAML deliberately: it's the shape every
framework except Goose already uses, diffs and hand-edits far better than
JSON, and matches this project's own template-authoring conventions.

Serialization contract (kept deliberately narrow so the load side needs no
PyYAML — a test-only dependency): every string value is emitted as a JSON
double-quoted scalar (a strict subset of YAML), lists of plain tokens as a
flow list, and handoffs as a block list of one-line scalar entries. The
format is ordinary YAML for external tooling, and trivially lossless for
``load_canonical`` either through PyYAML (when importable, preferred for
hand-edit tolerance) or the built-in minimal subset parser.

All writes go through ``atomicio._atomic_write_text``; ``dry_run=True``
collects the planned writes with zero filesystem side effects (no mkdir).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from agentteams.atomicio import _atomic_write_text
from agentteams.yaml_frontmatter import parse_yaml_front_matter

__all__ = [
    "TEAM_FILE_NAME",
    "DEFAULT_CANONICAL_SUBDIR",
    "CanonicalMaterializeResult",
    "materialize_canonical",
    "load_canonical",
]

TEAM_FILE_NAME = "team.cai.json"
# Default location convention under a project root (plan §5.5).
DEFAULT_CANONICAL_SUBDIR = ".agentteams/canonical"

# Slugs become path components; keep them filesystem-safe and
# traversal-proof (no '/', no '..', no leading dot).
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Top-level CAI keys that explode to files rather than living in
# team.cai.json. Everything else passes through unchanged.
_EXPLODED_KEYS = ("agents", "skills", "references")

# Schema for runtime validation (D.1).  Loaded lazily so the module
# import cost stays minimal when validation is not needed.
_VALIDATOR: Any | None = None


def _get_validator() -> Any:
    """Load and cache a Draft 2020-12 validator for agent-cai.schema.json.

    The schema's ``mcp_servers`` items use ``$ref: "mcp-server.schema.json"``,
    a relative reference resolved against the schema's ``$id`` base URI.
    We pre-register ``mcp-server.schema.json`` under its fully-qualified URI
    so the validator can resolve the cross-schema reference without a
    filesystem-access retriever.
    """
    global _VALIDATOR
    if _VALIDATOR is None:
        schema_dir = Path(__file__).resolve().parent.parent / "schemas"
        cai_schema = json.loads(
            (schema_dir / "agent-cai.schema.json").read_text(encoding="utf-8"))
        mcp_schema = json.loads(
            (schema_dir / "mcp-server.schema.json").read_text(encoding="utf-8"))
        mcp_uri = "https://jlcatonjr.github.io/agentteams/schemas/mcp-server.schema.json"
        from referencing import Registry, Resource
        registry = Registry().with_resource(
            uri=mcp_uri, resource=Resource.from_contents(mcp_schema))
        _VALIDATOR = jsonschema.Draft202012Validator(cai_schema, registry=registry)
    return _VALIDATOR


def _validate_cai(cai: dict[str, Any]) -> None:
    """Validate a CAI dict against the agent-cai schema (D.1).

    Raises ``ValueError`` with a human-readable message if validation fails.
    Called at both the write boundary (``materialize_canonical``) and the read
    boundary (``load_canonical``) so malformed data is caught in both
    directions.
    """
    validator = _get_validator()
    errors = sorted(validator.iter_errors(cai), key=lambda e: list(e.absolute_path))
    if errors:
        exc = errors[0]
        path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ValueError(f"CAI schema validation failed at {path}: {exc.message}")


@dataclass
class CanonicalMaterializeResult:
    """Summary of a canonical materialization run."""

    written: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success(self) -> bool:
        return True


# --------------------------------------------------------------------------
# YAML emission (JSON-quoted scalars: lossless, single-line, valid YAML)
# --------------------------------------------------------------------------


def _yaml_scalar(value: Any) -> str:
    """Emit a scalar as a single-line YAML value.

    Strings go through ``json.dumps`` (double-quoted, escapes intact) which
    is a strict YAML subset — lossless for any content including newlines,
    colons, quotes and leading '---'. Booleans/None/numbers use their YAML
    literals.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value))


def _safe_rel_path(rel_path: str, out_dir: Path) -> Path:
    """Resolve *rel_path* under *out_dir*, refusing escape attempts."""
    if not rel_path or rel_path.startswith("/"):
        raise ValueError(f"Reference rel_path must be relative and non-empty: {rel_path!r}")
    norm = Path(rel_path.replace("\\", "/"))
    if norm.is_absolute() or ".." in norm.parts:
        raise ValueError(f"Reference rel_path escapes the canonical dir: {rel_path!r}")
    return out_dir / norm


def _agent_front_matter(agent: dict[str, Any]) -> str:
    """Render the YAML front-matter block for one canonical agent file.

    Key order is fixed for stable diffs. Every string is JSON-quoted (see
    module docstring); capabilities use a flow list of the canonical
    tool-scope tokens; handoffs are a block list of flat scalar entries.
    """
    lines = ["---"]
    lines.append(f"slug: {_yaml_scalar(agent.get('slug', ''))}")
    lines.append(f"name: {_yaml_scalar(agent.get('name', ''))}")
    lines.append(f"description: {_yaml_scalar(agent.get('description', ''))}")
    lines.append(f"source_path: {_yaml_scalar(agent.get('source_path', ''))}")

    capabilities = agent.get("capabilities") or {}
    tool_scopes = [str(t) for t in (capabilities.get("tool_scopes") or [])]
    if tool_scopes or capabilities.get("raw") or capabilities.get("model_hint"):
        lines.append("capabilities:")
        if tool_scopes:
            lines.append(f"  tool_scopes: [{', '.join(tool_scopes)}]")
        # A.3: preserve the escape-hatch fields raw and model_hint so they
        # survive the canonical materialize→load round trip.
        cap_raw = capabilities.get("raw")
        if isinstance(cap_raw, dict) and cap_raw:
            lines.append(f"  raw: {json.dumps(cap_raw, sort_keys=True)}")
        model_hint = capabilities.get("model_hint")
        if model_hint is not None:
            lines.append(f"  model_hint: {_yaml_scalar(model_hint)}")
    else:
        lines.append("capabilities: {}")

    handoffs = [h for h in (agent.get("handoffs") or []) if isinstance(h, dict)]
    if handoffs:
        lines.append("handoffs:")
        for h in handoffs:
            lines.append(f"  - to: {_yaml_scalar(h.get('to', ''))}")
            if h.get("label") is not None:
                lines.append(f"    label: {_yaml_scalar(h.get('label'))}")
            lines.append(f"    prompt: {_yaml_scalar(h.get('prompt', ''))}")
            lines.append(f"    send: {_yaml_scalar(bool(h.get('send', False)))}")
    else:
        lines.append("handoffs: []")

    raw_fm = agent.get("raw_front_matter")
    if isinstance(raw_fm, dict) and raw_fm:
        # Passthrough bucket for framework-specific keys (schema property
        # raw_front_matter). Single-line JSON keeps it lossless and simple.
        lines.append(f"raw_front_matter: {json.dumps(raw_fm, sort_keys=True)}")

    lines.append("---")
    return "\n".join(lines) + "\n"


def _agent_file_text(agent: dict[str, Any]) -> str:
    """Full text of ``agents/<slug>.md``: front matter + body (+ invariant core).

    The invariant-core span (captured verbatim with its BEGIN/END fence
    markers) is appended after the body — the same convention as
    ``interop.import_from_cai`` — and ``load_canonical`` re-lifts it with the
    same helper so the round trip reproduces the export shape exactly.
    """
    body = str(agent.get("body_markdown", "")).strip() + "\n"
    invariant_core = str(agent.get("invariant_core_markdown") or "").strip()
    if invariant_core:
        body = body.rstrip("\n") + "\n\n" + invariant_core + "\n"
    return _agent_front_matter(agent) + body


def _skill_fallback_text(skill: dict[str, Any]) -> str:
    """Synthesize SKILL.md when a skill entry carries no verbatim files.

    Normal capture always records ``files`` (including the original
    SKILL.md); this path exists for hand-built CAI dicts so materialization
    still yields a readable skill file.
    """
    fm = skill.get("front_matter") or {}
    body = str(skill.get("body_markdown", "")).strip() + "\n"
    return (
        "---\n"
        f"name: {_yaml_scalar(fm.get('name') or skill.get('slug', ''))}\n"
        f"description: {_yaml_scalar(fm.get('description', ''))}\n"
        "---\n"
        + body
    )


# --------------------------------------------------------------------------
# materialize_canonical
# --------------------------------------------------------------------------


def materialize_canonical(
    cai: dict[str, Any],
    out_dir: Path,
    *,
    dry_run: bool = False,
) -> CanonicalMaterializeResult:
    """Explode a CAI dict into the durable on-disk canonical directory form.

    Writes ``team.cai.json`` + ``agents/<slug>.md`` + ``skills/<slug>/`` (+
    co-located files) + ``references/**``, all through
    ``atomicio._atomic_write_text``. With ``dry_run=True`` the planned writes
    are returned with zero filesystem side effects (not even mkdir).

    Raises ValueError on a malformed CAI dict (missing/invalid agents,
    unsafe slugs, escaping reference paths) so bad input never half-writes.
    """
    if not isinstance(cai, dict):
        raise ValueError("CAI document must be a dict")
    # D.1: Validate against the agent-cai JSON schema before any writes.
    _validate_cai(cai)
    agents = cai.get("agents")
    if not isinstance(agents, list):
        raise ValueError("CAI document missing 'agents' list")
    skills = [s for s in (cai.get("skills") or []) if isinstance(s, dict)]
    references = [r for r in (cai.get("references") or []) if isinstance(r, dict)]
    out_dir = Path(out_dir)

    # (relative path under out_dir, text) — collected first so dry_run is
    # genuinely side-effect free and writes happen in one atomic pass.
    planned: list[tuple[str, str]] = []

    team_doc = {k: v for k, v in cai.items() if k not in _EXPLODED_KEYS}
    planned.append((TEAM_FILE_NAME, json.dumps(team_doc, indent=2) + "\n"))

    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError(f"Agent entry must be a dict, got: {type(agent).__name__}")
        slug = str(agent.get("slug", "")).strip()
        if not _SAFE_SLUG_RE.fullmatch(slug):
            raise ValueError(f"Unsafe or empty agent slug: {slug!r}")
        planned.append((f"agents/{slug}.md", _agent_file_text(agent)))

    for skill in skills:
        slug = str(skill.get("slug", "")).strip()
        if not _SAFE_SLUG_RE.fullmatch(slug):
            raise ValueError(f"Unsafe or empty skill slug: {slug!r}")
        files = [f for f in (skill.get("files") or []) if isinstance(f, dict)]
        wrote_skill_md = False
        for f in files:
            rel_path = str(f.get("rel_path", "")).strip()
            if not rel_path:
                continue
            dest_rel = f"skills/{slug}/{rel_path}"
            # Traversal guard within the skill dir.
            norm = Path(rel_path.replace("\\", "/"))
            if norm.is_absolute() or ".." in norm.parts:
                raise ValueError(f"Skill file escapes its skill dir: {rel_path!r}")
            planned.append((dest_rel, str(f.get("content", ""))))
            if rel_path == "SKILL.md":
                wrote_skill_md = True
        if not wrote_skill_md:
            planned.append((f"skills/{slug}/SKILL.md", _skill_fallback_text(skill)))

    for ref in references:
        rel_path = str(ref.get("rel_path", "")).strip()
        dest = _safe_rel_path(rel_path, out_dir)
        planned.append((str(dest.relative_to(out_dir)), str(ref.get("content", ""))))

    result = CanonicalMaterializeResult(dry_run=dry_run)
    if dry_run:
        result.written = [rel for rel, _ in planned]
        return result

    for rel, text in planned:
        _atomic_write_text(out_dir / rel, text)
        result.written.append(rel)
    return result


# --------------------------------------------------------------------------
# load_canonical (the inverse)
# --------------------------------------------------------------------------


def _decode_scalar(raw: str) -> Any:
    """Decode one YAML scalar written by the emitter above.

    JSON-first: everything the emitter produces for strings/bools/null/
    numbers/flow collections is a JSON token. Bare tokens (e.g. flow-list
    items like ``read``) fall back to plain strings after quote stripping.
    """
    raw = raw.strip()
    if raw == "":
        return ""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw


def _parse_flow_list(raw: str) -> list[Any]:
    """Parse a flow list ``[a, b, c]`` (bare or JSON-quoted items)."""
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items: list[Any] = []
    for part in inner.split(","):
        items.append(_decode_scalar(part))
    return items


def _minimal_yaml_load(yaml_text: str) -> dict[str, Any]:
    """Parse the exact YAML subset ``_agent_front_matter`` emits.

    Fallback used only when PyYAML is unavailable (it is a test-only
    dependency). Handles: top-level ``key: <scalar>``, one-level nested
    dicts with scalar or flow-list values, and block lists whose items are
    flat dicts (``- key: value`` + indented continuation keys) or plain
    scalars (``- name``). Comments and blank lines are ignored.
    """
    result: dict[str, Any] = {}
    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not line[:1].strip() == line[:1]:
            # blank, comment, or (defensively) indented line at top level
            i += 1
            continue
        if ":" not in stripped:
            i += 1
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            if rest.startswith("["):
                result[key] = _parse_flow_list(rest)
            else:
                result[key] = _decode_scalar(rest)
            i += 1
            continue
        # Block value: collect the indented run that follows.
        block: list[str] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip() == "" or nxt.startswith((" ", "\t")):
                block.append(nxt)
                j += 1
                continue
            break
        block_content = [b for b in block if b.strip() and not b.strip().startswith("#")]
        if block_content and block_content[0].lstrip().startswith("- "):
            # Block list: items are flat dicts ("- key: value" + indented
            # continuation keys) or plain scalars ("- name", e.g. an agents:
            # roster). Scalar items previously collapsed to {} here, silently
            # destroying the roster on any PyYAML-less round trip.
            items: list[Any] = []
            current: dict[str, Any] | None = None
            for b in block_content:
                content = b.strip()
                if content.startswith("- "):
                    if current is not None:
                        items.append(current)
                        current = None
                    content = content[2:].strip()
                    if ":" in content:
                        current = {}
                        k, _, v = content.partition(":")
                        current[k.strip()] = _decode_scalar(v)
                    elif content:
                        items.append(_decode_scalar(content))
                    else:
                        current = {}
                    continue
                if current is not None and ":" in content:
                    k, _, v = content.partition(":")
                    current[k.strip()] = _decode_scalar(v)
            if current is not None:
                items.append(current)
            result[key] = items
        else:
            sub: dict[str, Any] = {}
            for b in block_content:
                content = b.strip()
                if ":" in content:
                    k, _, v = content.partition(":")
                    v = v.strip()
                    sub[k.strip()] = _parse_flow_list(v) if v.startswith("[") else _decode_scalar(v)
            result[key] = sub
        i = j
    return result


def _load_yaml_block(yaml_text: str) -> dict[str, Any]:
    """Parse front-matter YAML: PyYAML when importable (hand-edit tolerant),
    else the minimal subset parser for what this module emits."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return _minimal_yaml_load(yaml_text)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        # Hand-edit broke the YAML: fall back to the subset parser, which
        # still recovers every key this module itself emits.
        return _minimal_yaml_load(yaml_text)
    return data if isinstance(data, dict) else {}


def _frontmatter_string(fm: dict[str, Any], key: str) -> str:
    value = fm.get(key)
    return "" if value is None else str(value)


def _load_agent_file(path: Path) -> dict[str, Any]:
    """Read one ``agents/<slug>.md`` back into the CAI agent-entry shape."""
    # Local import: interop imports the registry which imports adapters —
    # keeping this lazy avoids any import-order coupling with canonical.py.
    from agentteams.interop import _capture_invariant_core

    content = path.read_text(encoding="utf-8")
    yaml_text, body = parse_yaml_front_matter(content)
    fm = _load_yaml_block(yaml_text) if yaml_text is not None else {}

    # Re-lift the invariant-core span exactly the way export captured it.
    invariant_core, body = _capture_invariant_core(body)

    capabilities = fm.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}
    tool_scopes = capabilities.get("tool_scopes") or []
    # A.3: preserve the escape-hatch fields raw and model_hint on load
    # so they survive the canonical materialize→load round trip.
    cap: dict[str, Any] = {}
    if tool_scopes:
        cap["tool_scopes"] = [str(t) for t in tool_scopes]
    cap_raw = capabilities.get("raw")
    if isinstance(cap_raw, dict) and cap_raw:
        cap["raw"] = cap_raw
    model_hint = capabilities.get("model_hint")
    if model_hint is not None:
        cap["model_hint"] = str(model_hint)
    capabilities = cap

    handoffs: list[dict[str, Any]] = []
    for h in fm.get("handoffs") or []:
        if not isinstance(h, dict):
            continue
        to = str(h.get("to", "")).strip()
        if not to:
            continue
        handoffs.append(
            {
                "to": to,
                "label": None if h.get("label") is None else str(h.get("label")),
                "prompt": str(h.get("prompt", "") or ""),
                "send": bool(h.get("send", False)),
            }
        )

    entry: dict[str, Any] = {
        "slug": _frontmatter_string(fm, "slug") or path.stem,
        "name": _frontmatter_string(fm, "name") or path.stem,
        "description": _frontmatter_string(fm, "description"),
        "body_markdown": body.strip() + "\n",
        "capabilities": capabilities,
        "handoffs": handoffs,
        "invariant_core_markdown": invariant_core or None,
        "source_path": _frontmatter_string(fm, "source_path"),
    }
    raw_fm = fm.get("raw_front_matter")
    if isinstance(raw_fm, dict) and raw_fm:
        entry["raw_front_matter"] = raw_fm
    return entry


def _load_skill_dir(skill_dir: Path) -> dict[str, Any]:
    """Read one ``skills/<slug>/`` directory back into the CAI skill shape."""
    skill_md = skill_dir / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    yaml_text, body = parse_yaml_front_matter(content)
    fm = _load_yaml_block(yaml_text) if yaml_text is not None else {}
    files = [
        {
            "rel_path": str(p.relative_to(skill_dir)),
            "content": p.read_text(encoding="utf-8"),
        }
        for p in sorted(skill_dir.rglob("*"))
        if p.is_file()
    ]
    return {
        "slug": skill_dir.name,
        "front_matter": {
            "name": _frontmatter_string(fm, "name"),
            "description": _frontmatter_string(fm, "description"),
        },
        "body_markdown": body.strip() + "\n",
        "files": files,
    }


def load_canonical(dir_path: Path) -> dict[str, Any]:
    """Read the exploded canonical directory back into the CAI dict shape.

    The inverse of ``materialize_canonical``: produces exactly the shape
    ``interop.export_to_cai`` emits (schema_version/created_at/source
    metadata/instructions_binding/mcp_servers/framework_extensions from
    ``team.cai.json``; agents and skills reassembled from files; references
    from the ``references/`` tree when present). Agents and skills are
    sorted by slug. Raises FileNotFoundError when ``team.cai.json`` is
    absent — that file is the format's identity marker (F.5 detection keys
    on it).
    """
    dir_path = Path(dir_path)
    team_file = dir_path / TEAM_FILE_NAME
    if not team_file.is_file():
        raise FileNotFoundError(f"Not a canonical directory (no {TEAM_FILE_NAME}): {dir_path}")
    cai: dict[str, Any] = json.loads(team_file.read_text(encoding="utf-8"))

    agents_dir = dir_path / "agents"
    agents = [
        _load_agent_file(p)
        for p in sorted(agents_dir.glob("*.md"))
        if p.is_file()
    ] if agents_dir.is_dir() else []
    cai["agents"] = sorted(agents, key=lambda a: a["slug"])

    skills_root = dir_path / "skills"
    skills: list[dict[str, Any]] = []
    if skills_root.is_dir():
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            skills.append(_load_skill_dir(skill_md.parent))
    cai["skills"] = sorted(skills, key=lambda s: s["slug"])

    references_root = dir_path / "references"
    if references_root.is_dir():
        cai["references"] = [
            {
                "rel_path": str(p.relative_to(dir_path)),
                "content": p.read_text(encoding="utf-8"),
            }
            for p in sorted(references_root.rglob("*"))
            if p.is_file()
        ]

    # D.1: Validate the reassembled CAI against the schema.
    _validate_cai(cai)
    return cai
