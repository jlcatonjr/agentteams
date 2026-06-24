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
from pathlib import Path
from typing import Any


_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md"}

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

    for file in sorted(source_dir.iterdir()):
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

    lines = ["# Bridge Check Report", "", f"Result: {'PASS' if ok else 'FAIL'}", ""]
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


def _render_inventory_md(rows: list[dict[str, str]]) -> str:
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
        lines.append(
            f"| {row['display_name']} | {row['invokable']} | {role} | `{row['source_file']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    raw = parts[0][4:]
    body = parts[1]
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", line.strip())
        if not m:
            continue
        key = m.group(1)
        value = m.group(2).strip().strip('"\'')
        if value.lower() in {"true", "false"}:
            data[key] = value.lower() == "true"
        else:
            data[key] = value
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
