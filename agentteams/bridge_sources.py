"""
bridge_sources.py — source-team inventory extraction, file collection, and hashing
for the lightweight bridge, plus the bridge-freshness check. Carved from bridge.py
(CH-07 line ceiling) to create headroom for source-framework extensions. bridge.py
re-exports these so importers resolve them from agentteams.bridge unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.canonical import TEAM_FILE_NAME, _load_agent_file
from agentteams.yaml_frontmatter import parse_yaml_front_matter


_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md"}


def _require_canonical_team_file(source_dir: Path) -> None:
    """Raise clearly when *source_dir* isn't actually a canonical directory.

    Without this, a dir with no `team.cai.json` but some coincidentally
    `.md`-shaped files under `agents/` silently produced a plausible-looking
    partial bridge instead of erroring (2026-08-10 finding) — inconsistent
    with how canonical-ness is checked everywhere else in this feature
    (`detect_framework`'s own marker check, `load_canonical`'s hard
    `FileNotFoundError`).
    """
    if not (source_dir / TEAM_FILE_NAME).is_file():
        raise FileNotFoundError(
            f"{source_dir} is not a canonical directory: no {TEAM_FILE_NAME} found. "
            "Point --bridge-source-framework canonical at the directory holding "
            f"{TEAM_FILE_NAME}, not a plain agents/*.md-shaped folder."
        )

# Goose-source recipe metadata (hand-built YAML; regex parse, no YAML dep).
_RECIPE_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_RECIPE_DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)
_RECIPE_PROMPT_RE = re.compile(r"^prompt:", re.MULTILINE)


def _parse_recipe_meta(text: str) -> tuple[str, str, str]:
    """Return (title, description, invokable) from a Goose recipe YAML.

    ``invokable`` is "yes" for entry recipes — those with ``sub_recipes:`` (an
    orchestrator) or a ``prompt:`` (a non-interactive entry) — else "no".
    """
    t = _RECIPE_TITLE_RE.search(text)
    d = _RECIPE_DESC_RE.search(text)
    title = t.group(1).strip().strip('"') if t else ""
    desc = d.group(1).strip().strip('"') if d else ""
    invokable = "yes" if ("sub_recipes:" in text or _RECIPE_PROMPT_RE.search(text)) else "no"
    return title, desc, invokable


def _extract_inventory(source_dir: Path, source_framework: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    if source_framework == "canonical":
        _require_canonical_team_file(source_dir)
        # Canonical agent files live under agents/*.md, not flat in source_dir.
        agents_dir = source_dir / "agents"
        candidates = sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    else:
        candidates = sorted(source_dir.iterdir())

    for file in candidates:
        if not file.is_file():
            continue
        name = file.name
        if name in _INSTRUCTIONS_NAMES or name == "SETUP-REQUIRED.md":
            continue

        if source_framework == "goose":
            if not name.endswith(".yaml"):
                continue
            text = file.read_text(encoding="utf-8")
            title, desc, invokable = _parse_recipe_meta(text)
            display_name = title or _slug_to_name(name[: -len(".yaml")])
            rows.append(
                {
                    "display_name": display_name,
                    "invokable": invokable,
                    "role": desc,
                    "source_file": str(file),
                }
            )
            continue

        if source_framework == "canonical":
            # Canonical front matter uses JSON-escaped string values (canonical.py's
            # own narrow YAML subset) - _parse_front_matter below only strips quotes,
            # it doesn't decode escapes, so non-ASCII/quoted content would come back
            # mangled: the literal 6 characters backslash-u-2-0-1-4 instead of a
            # real em dash. Reuse canonical.py's own reader, which already decodes
            # this correctly.
            entry = _load_agent_file(file)
            rows.append(
                {
                    "display_name": entry["name"] or _slug_to_name(_slug_from_name(name)),
                    # No user-invokable concept in canonical front matter — honest
                    # "no" rather than a guess.
                    "invokable": "no",
                    "role": entry["description"] or "",
                    "source_file": str(file),
                }
            )
            continue

        if source_framework == "copilot-vscode" and not name.endswith(".agent.md"):
            continue
        if source_framework != "copilot-vscode" and not name.endswith(".md"):
            continue

        text = file.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(text)
        display_name = str(meta.get("name") or _first_heading(body) or _slug_to_name(_slug_from_name(name)))
        role = str(meta.get("description") or _first_non_heading_line(body) or "")
        invokable = "yes" if _is_invokable(meta.get("user-invokable")) else "no"
        rows.append(
            {
                "display_name": display_name,
                "invokable": invokable,
                "role": role,
                "source_file": str(file),
            }
        )

    rows.sort(key=lambda r: (0 if "orchestrator" in r["source_file"] else 1, r["display_name"].lower()))
    return rows


def _collect_source_files(source_dir: Path, source_framework: str = "copilot-vscode") -> list[Path]:
    # Hash only the source framework's agent-definition files: markdown for
    # claude/copilot (`.md`, incl. `.agent.md`), recipe YAML for a Goose source
    # (`.yaml`). This excludes build-tool artifacts (`_build-description.json`,
    # a `.json`), OS/editor junk (`.DS_Store`), and any other file that would
    # otherwise enter the manifest and trip `--bridge-check` on changes unrelated
    # to the agent team — for every source framework.
    if source_framework == "canonical":
        _require_canonical_team_file(source_dir)
        # A canonical root has a different shape entirely: agent files live under
        # agents/*.md (not flat in source_dir), and instructions/MCP/framework
        # extensions live inside team.cai.json rather than as sibling files — so
        # team.cai.json itself must be hashed too, or a hand-edit there would be
        # invisible to --bridge-check.
        agents_dir = source_dir / "agents"
        files: list[Path] = sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []
        team_file = source_dir / TEAM_FILE_NAME
        if team_file.is_file():
            files.append(team_file)
        return files
    ext = ".yaml" if source_framework == "goose" else ".md"
    files: list[Path] = []
    for p in sorted(source_dir.iterdir()):
        if p.is_file() and p.name.endswith(ext) and p.name != "SETUP-REQUIRED.md":
            files.append(p)
    for name in sorted(_INSTRUCTIONS_NAMES):
        parent_candidate = source_dir.parent / name
        if parent_candidate.exists():
            files.append(parent_candidate)
    return files


def _compute_hash_rows(files: list[Path], source_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for p in files:
        try:
            rel = str(p.relative_to(source_dir.parent))
        except ValueError:
            rel = str(p.name)
        rows.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
        )
    return rows


def source_state_digest(source_hash_rows: list[dict[str, str]]) -> str:
    """Digest the source state a bridge verdict was computed from.

    Deterministic in the source tree alone: the same files always produce the same
    digest, on any machine, at any time. That is what lets a check report be
    byte-stable across re-runs *and* machine-comparable against the current tree —
    the two properties a wall-clock timestamp cannot provide together.

    Sorted by path so row ordering cannot perturb it. Same construction as
    ``memory_index._documents_fingerprint``, which is this repository's existing
    precedent for a path/hash fingerprint.

    Args:
        source_hash_rows: ``{"path", "sha256"}`` rows as recorded in the manifest.

    Returns:
        Hex sha256 over the sorted ``path:sha256`` pairs.
    """
    parts = [
        f"{row.get('path', '')}:{row.get('sha256', '')}"
        for row in sorted(source_hash_rows, key=lambda r: str(r.get("path", "")))
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _run_bridge_check(*, manifest_path: Path, source_hash_rows: list[dict[str, str]]) -> tuple[bool, str]:
    if not manifest_path.exists():
        report = (
            "# Bridge Check Report\n\n"
            "Result: FAIL\n\n"
            "- bridge-manifest.json is missing.\n"
            "- No bridge has been generated yet. Run with --bridge-refresh "
            "(omit --bridge-check) to generate the initial bridge artifacts, "
            "then re-run --bridge-check to validate them.\n"
        )
        return False, report

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        report = (
            "# Bridge Check Report\n\n"
            "Result: FAIL\n\n"
            "- bridge-manifest.json is not valid JSON.\n"
        )
        return False, report

    expected = {row["path"]: row["sha256"] for row in manifest.get("source_hashes", [])}
    actual = {row["path"]: row["sha256"] for row in source_hash_rows}

    stale_paths: list[str] = []
    for path, sha in actual.items():
        if expected.get(path) != sha:
            stale_paths.append(path)

    missing_paths = sorted(set(expected.keys()) - set(actual.keys()))
    extra_paths = sorted(set(actual.keys()) - set(expected.keys()))

    # A 0-agent manifest is a broken bridge (almost always a wrong --bridge-from):
    # fail the freshness check so a non-functional bridge cannot pass silently even
    # when its source hashes are self-consistent.
    empty_inventory = manifest.get("inventory_count") == 0

    ok = not stale_paths and not missing_paths and not extra_paths and not empty_inventory

    # A committed check report is a CACHED verdict, and the defect it had was not a
    # missing date — it was that nothing detected the cache going stale. The
    # copilot-cli report sat at PASS for a week while six sources drifted.
    #
    # The first fix recorded a wall-clock "checked at", which conveys staleness only
    # to a human who opens the file and does the arithmetic, and made every
    # --bridge-check rewrite a tracked file (a documented read-only command mutating
    # the tree). Recording the digest of the *inputs* instead is deterministic, so a
    # re-check of unchanged sources produces identical bytes, and it is machine
    # comparable — tests/test_bridge_mode_safety.py checks the committed digest
    # against the working tree, which is what actually catches the drift.
    lines = [
        "# Bridge Check Report",
        "",
        f"Result: {'PASS' if ok else 'FAIL'}",
        "",
        f"- Source state: {source_state_digest(source_hash_rows)}",
        f"- Manifest generated at: {manifest.get('generated_at', '(not recorded)')}",
        "",
        "> `Source state` is a digest of the source files this verdict was computed"
        " from. It is not a timestamp: if it no longer matches the current tree, the"
        " verdict is stale regardless of when it ran. Re-run `--bridge-check`.",
        "",
    ]
    if empty_inventory:
        lines.append("## Empty Inventory")
        lines.append(
            "- bridge-manifest.json records inventory_count: 0 — the bridge has no "
            "agents to route to. Regenerate with --bridge-from pointing at the agents "
            "directory (e.g. <project>/.github/agents)."
        )
        lines.append("")
    if stale_paths:
        lines.append("## Changed Source Files")
        lines.extend([f"- {p}" for p in stale_paths])
        lines.append("")
    if missing_paths:
        lines.append("## Missing Source Files")
        lines.extend([f"- {p}" for p in missing_paths])
        lines.append("")
    if extra_paths:
        lines.append("## New Source Files")
        lines.extend([f"- {p}" for p in extra_paths])
        lines.append("")
    if ok:
        lines.append("- Bridge artifacts are fresh and consistent with source files.")

    return ok, "\n".join(lines) + "\n"


def _render_inventory_md(rows: list[dict[str, str]], output_root: Path | None = None) -> str:
    """Render the bridge inventory table.

    Args:
        rows: Inventory rows from ``_extract_inventory``.
        output_root: Repository root. When given, ``source_file`` is rendered
            relative to it — this artifact is committed, and an absolute path
            leaks the operator's home directory and username. ``1937cbc`` fixed
            that for the manifest's ``source_dir`` and missed this column.
            Optional so existing callers keep working; they get absolute paths.

    Returns:
        The inventory markdown.
    """
    lines = [
        "# Agent Team Bridge Inventory",
        "",
        "Lightweight compatibility inventory generated from source canonical files.",
        "",
        "| Agent | Invokable | Role | Source file |",
        "|---|---|---|---|",
    ]
    for row in rows:
        role = row["role"].replace("|", "\\|")
        source = row["source_file"]
        if output_root is not None:
            candidate = Path(source)
            if candidate.is_absolute() and candidate.is_relative_to(output_root):
                source = str(candidate.relative_to(output_root))
        lines.append(
            f"| {row['display_name']} | {row['invokable']} | {role} | `{source}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML front matter into a flat dict plus body.

    Boundary detection delegates to ``yaml_frontmatter.parse_yaml_front_matter``
    — the shared line-anchored, block-scalar-aware scanner — replacing the old
    naive ``text.split("\\n---\\n", 1)`` boundary scan. That naive split was the
    one remaining MAP-06-class bug in production (it fired on any ``---`` line
    anywhere in the text, including inside block scalars) and is fixed here by
    the durable-canonical-agent-format plan (step B.2).

    Block-style sequences (``key:`` followed by indented ``- item`` lines) are
    captured as lists, matching ``bridge_subagents._parse_front_matter``'s
    MAP-05 behavior. The call signature and scalar-value semantics (strings,
    ``true``/``false`` → bool) are unchanged.
    """
    yaml_block, body = parse_yaml_front_matter(text)
    if yaml_block is None:
        return {}, text
    data: dict[str, Any] = {}
    lines = yaml_block.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", lines[i].strip())
        if not m:
            i += 1
            continue
        key = m.group(1)
        value = m.group(2).strip()
        if not value:
            # Possibly a block-style sequence: consume subsequent indented
            # ``- item`` lines and store them as a list.
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                bm = re.match(r"^\s+-\s*(.*)$", lines[j])
                if bm:
                    items.append(bm.group(1).strip().strip('"\''))
                    j += 1
                else:
                    break
            if items:
                data[key] = items
                i = j
                continue
            data[key] = ""
            i += 1
            continue
        value = value.strip('"\'')
        if value.lower() in {"true", "false"}:
            data[key] = value.lower() == "true"
        else:
            data[key] = value
        i += 1
    return data, body


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return re.sub(r"^#{1,6}\s+", "", s)
    return ""


def _first_non_heading_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("-"):
            continue
        return s
    return ""


def _slug_from_name(name: str) -> str:
    if name.endswith(".agent.md"):
        return name[: -len(".agent.md")]
    if name.endswith(".md"):
        return name[: -len(".md")]
    return name


def _slug_to_name(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-"))


def _is_invokable(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return False
