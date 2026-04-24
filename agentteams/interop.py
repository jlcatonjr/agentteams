"""interop.py - Cross-framework agent infrastructure interop pipeline.

This module provides a canonical intermediate representation (CAI) for agent
teams and utilities to convert between supported frameworks through that
representation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.frameworks.base import FrameworkAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

_ADAPTERS: dict[str, type[FrameworkAdapter]] = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
}

_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md"}
_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


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
    parts = set(source_dir.parts)
    if ".claude" in parts:
        return "claude"
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
            if "allowed-tools:" in content:
                has_claude_front_matter = True
            if "user-invokable:" in content or "handoffs:" in content:
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
    if framework not in _ADAPTERS:
        raise ValueError(f"Unknown source framework {framework!r}")

    agents: list[dict[str, Any]] = []
    instructions_content = ""
    instructions_name = ""

    for entry in sorted(source_dir.rglob("*")):
        if entry.is_dir():
            continue
        rel = entry.relative_to(source_dir)
        name = entry.name
        if name in _INSTRUCTIONS_NAMES:
            instructions_name = name
            instructions_content = entry.read_text(encoding="utf-8")
            continue
        if name.endswith(".md"):
            if name == "SETUP-REQUIRED.md":
                continue
            content = entry.read_text(encoding="utf-8")
            slug = _slug_from_filename(name)
            body = _strip_framework_wrappers(content)
            agents.append(
                {
                    "slug": slug,
                    "name": _first_heading_or_title(content, slug),
                    "description": "",
                    "body_markdown": body.strip() + "\n",
                    "capabilities": {},
                    "handoffs": [],
                    "source_path": str(rel),
                }
            )

    if not instructions_content:
        parent = source_dir.parent
        for candidate_name in sorted(_INSTRUCTIONS_NAMES):
            candidate = parent / candidate_name
            if candidate.exists():
                instructions_name = candidate_name
                instructions_content = candidate.read_text(encoding="utf-8")
                break

    return {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_framework": framework,
        "source_dir": str(source_dir),
        "instructions_binding": {
            "source_name": instructions_name or "",
            "content": instructions_content,
        },
        "agents": sorted(agents, key=lambda a: a["slug"]),
    }


def import_from_cai(
    cai: dict[str, Any],
    target_framework: str,
    target_dir: Path,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> InteropResult:
    """Import a CAI document into a target framework directory."""
    if target_framework not in _ADAPTERS:
        raise ValueError(f"Unknown target framework {target_framework!r}")

    adapter = _ADAPTERS[target_framework]()
    result = InteropResult(dry_run=dry_run)
    manifest = {"project_name": "InteropProject", "output_files": []}

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
        rendered = adapter.render_agent_file(body, slug, manifest)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(rendered, encoding="utf-8")
        result.converted.append(str(dest))

    instructions_content = str(cai.get("instructions_binding", {}).get("content", ""))
    if instructions_content:
        instructions_name = "CLAUDE.md" if target_framework == "claude" else "copilot-instructions.md"
        inst_dest = target_dir.parent / instructions_name
        if inst_dest.exists() and not overwrite:
            result.skipped.append(str(inst_dest))
        else:
            if not dry_run:
                inst_dest.parent.mkdir(parents=True, exist_ok=True)
                inst_dest.write_text(instructions_content, encoding="utf-8")
            result.converted.append(str(inst_dest))

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
        "target_name": "CLAUDE.md" if target_framework == "claude" else "copilot-instructions.md",
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
    return name


def _strip_framework_wrappers(content: str) -> str:
    body = _YAML_FRONT_MATTER_RE.sub("", content, count=1)
    body = _strip_handoffs_section(body)
    return body


def _strip_handoffs_section(content: str) -> str:
    handoff_re = re.compile(r"^#{1,3}\s+Handoff.*?(?=^#{1,3}\s|\Z)", re.MULTILINE | re.DOTALL)
    return handoff_re.sub("", content)


def _first_heading_or_title(content: str, slug: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return slug.replace("-", " ").title()
