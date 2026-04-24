"""bridge.py - Lightweight cross-framework bridge artifacts.

This module creates compatibility bridge artifacts that let one framework use
another framework's canonical agent infrastructure without regenerating all
agent documentation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.interop import detect_framework

_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md"}


@dataclass
class BridgeResult:
    """Summary of a bridge generation/check run."""

    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    check_only: bool = False
    check_ok: bool = True
    check_report_path: str = ""

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and (self.check_ok or not self.check_only)


def run_bridge(
    *,
    source_dir: Path,
    target_framework: str,
    output_root: Path,
    source_framework: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
    check_only: bool = False,
) -> BridgeResult:
    """Generate or validate lightweight bridge artifacts.

    Args:
        source_dir: Canonical source agents directory.
        target_framework: Framework receiving the bridge.
        output_root: Root directory where bridge artifacts are written.
        source_framework: Optional explicit source framework.
        dry_run: When True, report writes without writing.
        overwrite: Overwrite existing bridge files when True.
        check_only: Validate existing bridge freshness without writing.

    Returns:
        BridgeResult.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    src_fw = source_framework or detect_framework(source_dir)
    if src_fw not in {"copilot-vscode", "copilot-cli", "claude"}:
        raise ValueError(f"Unknown source framework {src_fw!r}")
    if target_framework not in {"copilot-vscode", "copilot-cli", "claude"}:
        raise ValueError(f"Unknown target framework {target_framework!r}")

    result = BridgeResult(dry_run=dry_run, check_only=check_only)
    inventory = _extract_inventory(source_dir, src_fw)
    source_files = _collect_source_files(source_dir)
    source_hashes = _compute_hash_rows(source_files, source_dir)

    pair_dir = output_root / "references" / "bridges" / f"{src_fw}-to-{target_framework}"
    manifest_path = pair_dir / "bridge-manifest.json"

    if check_only:
        ok, report = _run_bridge_check(manifest_path=manifest_path, source_hash_rows=source_hashes)
        report_path = pair_dir / "bridge-check.report.md"
        result.check_ok = ok
        result.check_report_path = str(report_path)
        if not dry_run:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report, encoding="utf-8")
        result.written.append(str(report_path))
        if not ok:
            result.errors.append("bridge-check detected stale or missing bridge artifacts")
        return result

    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_framework": src_fw,
        "target_framework": target_framework,
        "source_dir": str(source_dir),
        "source_hashes": source_hashes,
        "inventory_count": len(inventory),
        "bridge_version": "1",
    }

    files: list[tuple[Path, str]] = []
    files.append((manifest_path, json.dumps(manifest, indent=2) + "\n"))
    files.append((pair_dir / "agent-inventory.md", _render_inventory_md(inventory)))
    files.append((pair_dir / "quickstart-snippet.md", _render_quickstart(src_fw, target_framework)))
    files.append((pair_dir / "entrypoint.md", _render_entrypoint(src_fw, target_framework)))

    target_files = _render_target_files(
        source_framework=src_fw,
        target_framework=target_framework,
        pair_dir=pair_dir,
    )
    files.extend(target_files)

    for path, content in files:
        if path.exists() and not overwrite:
            result.skipped.append(str(path))
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result.written.append(str(path))

    return result


def _extract_inventory(source_dir: Path, source_framework: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for file in sorted(source_dir.iterdir()):
        if not file.is_file():
            continue
        name = file.name
        if name in _INSTRUCTIONS_NAMES or name == "SETUP-REQUIRED.md":
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


def _collect_source_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(source_dir.iterdir()):
        if p.is_file() and p.name != "SETUP-REQUIRED.md":
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

    ok = not stale_paths and not missing_paths and not extra_paths

    lines = ["# Bridge Check Report", "", f"Result: {'PASS' if ok else 'FAIL'}", ""]
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


def _render_quickstart(source_framework: str, target_framework: str) -> str:
    return (
        "# Bridge Quickstart Snippet\n\n"
        "Use this as your first prompt:\n\n"
        "```text\n"
        f"Use the {source_framework} agent infrastructure through this {target_framework} bridge.\n"
        "Start with the source orchestrator and follow source governance rules.\n"
        "Do not bypass orchestrator for multi-step, destructive, or cross-repo work.\n"
        "```\n"
    )


def _render_entrypoint(source_framework: str, target_framework: str) -> str:
    return (
        f"# Bridge Entrypoint: {source_framework} -> {target_framework}\n\n"
        "This is a lightweight interface bridge.\n"
        "Canonical agent definitions remain in source framework files.\n"
        "Use orchestrator-first routing for team-based work.\n"
    )


def _render_target_files(
    *,
    source_framework: str,
    target_framework: str,
    pair_dir: Path,
) -> list[tuple[Path, str]]:
    root = pair_dir.parents[2]  # <output_root>
    rel_inventory = pair_dir.relative_to(root) / "agent-inventory.md"
    rel_quickstart = pair_dir.relative_to(root) / "quickstart-snippet.md"

    if target_framework == "claude":
        claude_md = root / "CLAUDE.md"
        claude_dir = root / ".claude"
        entry = (
            "# Claude Bridge Entry Point\n\n"
            f"Use source framework `{source_framework}` as canonical agent infrastructure.\n"
            f"Read `{rel_inventory}` and `{rel_quickstart}`.\n"
            "Start with orchestrator routing.\n"
        )
        return [
            (claude_md, entry),
            (claude_dir / "agent-team.md", f"See `{rel_inventory}` for bridge inventory.\n"),
            (claude_dir / "quickstart-snippet.md", f"See `{rel_quickstart}` for bridge quickstart.\n"),
            (claude_dir / "README.md", "# Claude Bridge\n\nLightweight bridge; source files are canonical.\n"),
        ]

    if target_framework == "copilot-vscode":
        gh_dir = root / ".github"
        agents_dir = gh_dir / "agents"
        instructions = (
            "# Copilot VS Code Bridge Instructions\n\n"
            f"Source framework: `{source_framework}`.\n"
            f"Bridge inventory: `{rel_inventory}`.\n"
            "Route through source orchestrator first.\n"
        )
        bridge_agent = (
            "---\n"
            "name: Bridge Orchestrator\n"
            "description: \"Bridge entrypoint into source framework agent team\"\n"
            "user-invokable: true\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "---\n\n"
            "# Bridge Orchestrator\n\n"
            f"Read `{rel_inventory}` and route work through source orchestrator.\n"
        )
        return [
            (gh_dir / "copilot-instructions.md", instructions),
            (agents_dir / "bridge-orchestrator.agent.md", bridge_agent),
        ]

    gh_dir = root / ".github"
    copilot_dir = gh_dir / "copilot"
    instructions = (
        "# Copilot CLI Bridge Instructions\n\n"
        f"Source framework: `{source_framework}`.\n"
        f"Bridge inventory: `{rel_inventory}`.\n"
        "Route through source orchestrator first.\n"
    )
    entry = (
        "# Bridge Entry\n\n"
        f"Use source framework `{source_framework}` through this bridge.\n"
        f"Read `{rel_inventory}` and `{rel_quickstart}`.\n"
    )
    return [
        (gh_dir / "copilot-instructions.md", instructions),
        (copilot_dir / "bridge-entry.md", entry),
    ]


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
