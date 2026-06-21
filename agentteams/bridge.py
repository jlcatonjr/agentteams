"""bridge.py - Lightweight cross-framework bridge artifacts.

This module creates compatibility bridge artifacts that let one framework use
another framework's canonical agent infrastructure without regenerating all
agent documentation.

Three write modes:

- `--bridge-refresh` (overwrite=True): regenerate all bridge artifacts AND
  unconditionally overwrite target-framework entry files (CLAUDE.md,
  .claude/agent-team.md, etc.). Destructive at the target. Use for initial
  generation or when consumer entry files are known-disposable.
- `--bridge-merge` (merge_only=True): regenerate bridge-internal artifacts;
  for target-framework entry files, only re-render content inside
  `<!-- AGENTTEAMS-BRIDGE:BEGIN <region> v=N -->...
  <!-- AGENTTEAMS-BRIDGE:END <region> -->` fences. Content outside fences is
  preserved. Files lacking any bridge fence are skipped with a notice in
  `bridge-merge.report.md`. First-time consumers should use `--bridge-refresh`.
- `--bridge-check` (check_only=True): read-only; verify bridge freshness.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams import backup
from agentteams.interop import detect_framework

_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md"}

_FENCE_BEGIN_RE = re.compile(
    r"<!--\s*AGENTTEAMS-BRIDGE:BEGIN\s+(?P<region>[A-Za-z0-9_-]+)\s+v=(?P<ver>\d+)\s*-->",
)
_FENCE_END_TPL = "<!-- AGENTTEAMS-BRIDGE:END {region} -->"


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
    manifest_missing: bool = False
    notices: list[str] = field(default_factory=list)

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
    merge_only: bool = False,
    emit_skills: bool = True,
    host_features: list[str] | None = None,
) -> BridgeResult:
    """Generate or validate lightweight bridge artifacts.

    Args:
        source_dir: Canonical source agents directory.
        target_framework: Framework receiving the bridge.
        output_root: Root directory where bridge artifacts are written.
        source_framework: Optional explicit source framework.
        dry_run: When True, report writes without writing.
        overwrite: Overwrite existing bridge files when True (--bridge-refresh).
        check_only: Validate existing bridge freshness without writing.
        merge_only: Non-destructive update of target-framework entry files
            (--bridge-merge). For files containing `AGENTTEAMS-BRIDGE` fences,
            only fenced regions are re-rendered; content outside fences is
            preserved. Files without fences are skipped with notices.
        emit_skills: For claude target only — emit the recall skill template
            at `.claude/skills/recall.md`. Default True. Has no effect on
            non-claude targets.

    Returns:
        BridgeResult.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    src_fw = source_framework or detect_framework(source_dir)
    if src_fw not in {"copilot-vscode", "copilot-cli", "claude"}:
        raise ValueError(f"Unknown source framework {src_fw!r}")
    if target_framework not in {"copilot-vscode", "copilot-cli", "claude", "goose"}:
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
        result.manifest_missing = not manifest_path.exists()
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

    # Bridge-internal artifacts: always regenerated regardless of mode.
    bridge_files: list[tuple[Path, str]] = []
    bridge_files.append((manifest_path, json.dumps(manifest, indent=2) + "\n"))
    bridge_files.append((pair_dir / "agent-inventory.md", _render_inventory_md(inventory)))
    bridge_files.append((pair_dir / "quickstart-snippet.md", _render_quickstart(src_fw, target_framework)))
    bridge_files.append((pair_dir / "entrypoint.md", _render_entrypoint(src_fw, target_framework)))
    bridge_files.append((pair_dir / "domain-boundary.md", _render_domain_boundary(src_fw, target_framework)))

    # Target-framework entry files: subject to mode (refresh vs merge).
    target_files = _render_target_files(
        source_framework=src_fw,
        target_framework=target_framework,
        pair_dir=pair_dir,
    )

    # Phase 4: replace the default CLAUDE.md entry with a cache-aware split
    # that inlines the canonical copilot-instructions.md as a stable
    # preamble. Opt-in via host-feature subselector. Only meaningful for
    # the copilot-vscode → claude direction.
    _features_early = host_features or []
    if (
        target_framework == "claude"
        and src_fw == "copilot-vscode"
        and "bridge:copilot-vscode-to-claude:cache-split" in _features_early
    ):
        from agentteams.instructions_split import render_cache_split

        # Source copilot-instructions.md sits at <project>/.github/copilot-instructions.md
        project_root = source_dir.parent.parent
        copilot_instr_path = project_root / ".github" / "copilot-instructions.md"
        if copilot_instr_path.exists():
            instr_body = copilot_instr_path.read_text(encoding="utf-8")
            cache_split = render_cache_split(copilot_instructions=instr_body)
            # Replace the CLAUDE.md tuple (first tuple ending in CLAUDE.md).
            target_files = [
                (p, cache_split) if p.name == "CLAUDE.md" else (p, c)
                for (p, c) in target_files
            ]
            result.notices.append(
                "CLAUDE.md emitted with cache-aware stable/dynamic split "
                "from .github/copilot-instructions.md."
            )
        else:
            result.notices.append(
                "cache-split subselector active but "
                f"{copilot_instr_path} not found; default CLAUDE.md retained."
            )
    if target_framework == "claude" and emit_skills:
        target_files.append(
            (output_root / ".claude" / "skills" / "recall.md", _render_recall_skill()),
        )

    # Goose target: emit a bridge-orchestrator recipe so the bridged project has the
    # `developer` (CLI) extension by default and, opt-in, the operator-selected MCP
    # servers wired as extensions. Servers are read from the SOURCE project's inert
    # `.claude/mcp-servers.agentteams.json` (the cache-split precedent reads the source
    # root the same way). The recipe is YAML (no AGENTTEAMS-BRIDGE fence): absent →
    # written in any mode; present → preserved under --bridge-merge, overwritten under
    # --bridge-refresh.
    if target_framework == "goose":
        import json as _json

        from agentteams.frameworks.goose import build_bridge_recipe

        mcp_token = f"bridge:{src_fw}-to-goose:mcp"
        mcp_on = mcp_token in _features_early
        servers: list = []
        if mcp_on:
            artifact = source_dir.parent.parent / ".claude" / "mcp-servers.agentteams.json"
            if artifact.exists():
                try:
                    servers = _json.loads(artifact.read_text(encoding="utf-8")).get("servers", []) or []
                except (OSError, ValueError):
                    result.notices.append(
                        f"{mcp_token} set but {artifact} was unreadable; bridge recipe "
                        "emitted with developer (CLI) only."
                    )
            else:
                result.notices.append(
                    f"{mcp_token} set but {artifact} not found; bridge recipe emitted "
                    "with developer (CLI) only. Build the source with an MCP host-feature "
                    "token to persist selected servers."
                )
        rel_pair = pair_dir.relative_to(output_root)
        recipe_yaml, recipe_notes = build_bridge_recipe(
            source_framework=src_fw,
            rel_inventory=str(rel_pair / "agent-inventory.md"),
            rel_quickstart=str(rel_pair / "quickstart-snippet.md"),
            mcp_servers=servers,
            mcp_enabled=mcp_on,
        )
        target_files.append(
            (output_root / ".goose" / "recipes" / "bridge-orchestrator.yaml", recipe_yaml)
        )
        for note in recipe_notes:
            result.notices.append(f"Goose bridge recipe: {note}")

    # Bridge-internal artifacts: refresh and merge both regenerate these.
    # (Skip and overwrite policies do not apply to bridge-owned files.)
    for path, content in bridge_files:
        # Refresh-or-overwrite: write unconditionally.
        # Merge: also write unconditionally — these are bridge-owned.
        # Otherwise (initial generation): write only if missing.
        if path.exists() and not overwrite and not merge_only:
            result.skipped.append(str(path))
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result.written.append(str(path))

    # Back up existing target entry files before any merge/overwrite write. The
    # merge path is fence-scoped (non-destructive) and overwrite is destructive,
    # but either way a recoverable copy must exist — fleet advertises
    # `.agentteams-backups` recovery for (non-git) bridge consumers, so produce it.
    if (overwrite or merge_only) and not dry_run:
        _to_backup = [
            str(path.relative_to(output_root))
            for path, _ in target_files
            if path.exists() and path.is_relative_to(output_root)
        ]
        if _to_backup:
            backup.backup_output_dir(
                output_root,
                files_to_backup=_to_backup,
                reason=f"bridge-{'overwrite' if overwrite else 'merge'}",
                framework=target_framework,
            )

    # Target-framework entry files: dispatched by mode.
    merge_report_lines: list[str] = []
    for path, content in target_files:
        if not path.exists():
            # First-time creation: write the rendered content regardless of mode.
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            result.written.append(str(path))
            if target_framework == "goose" and path.name == "AGENTS.md":
                # AGENTS.md is created in EVERY mode when absent (incl. --bridge-merge).
                # Surface the shared-namespace plant — other tools also read AGENTS.md.
                result.notices.append(
                    f"Created shared AGENTS.md at {path}; other tools (Cursor/Codex/"
                    "Cline) also read this file — confirm none of them owns it before "
                    "committing. See references/bridge-refresh-safety.md."
                )
            continue

        if merge_only:
            existing = path.read_text(encoding="utf-8")
            merged, status = _merge_target_file(existing=existing, rendered=content)
            if status == "merged":
                if not dry_run:
                    path.write_text(merged, encoding="utf-8")
                result.written.append(str(path))
                merge_report_lines.append(f"- merged: {path}")
            elif status == "no-fence":
                # W2: distinguish between a truly unmanaged file and one that was
                # written by --bridge-refresh (AGENTTEAMS-BRIDGE namespace).  The
                # latter silently skipped before; now emit an actionable notice.
                if _FENCE_BEGIN_RE.search(existing):
                    result.notices.append(
                        f"Notice: {path} contains AGENTTEAMS-BRIDGE fences (written by "
                        "--bridge-refresh) but no AGENTTEAMS fences recognized by --merge. "
                        "Run --bridge-refresh to regenerate, or add AGENTTEAMS fence markers "
                        "to enable future --merge updates."
                    )
                    merge_report_lines.append(
                        f"- skipped (AGENTTEAMS-BRIDGE fence present but no AGENTTEAMS fence; see notices): {path}"
                    )
                else:
                    merge_report_lines.append(
                        f"- skipped (no AGENTTEAMS-BRIDGE fence in existing file): {path}"
                    )
                result.skipped.append(str(path))
            else:
                result.skipped.append(str(path))
                merge_report_lines.append(f"- skipped ({status}): {path}")
            continue

        if not overwrite:
            result.skipped.append(str(path))
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result.written.append(str(path))

    if merge_only:
        report_path = pair_dir / "bridge-merge.report.md"
        report_body = "# Bridge Merge Report\n\n" + (
            "\n".join(merge_report_lines) if merge_report_lines else "- (no target files processed)"
        ) + "\n"
        if not dry_run:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_body, encoding="utf-8")
        result.written.append(str(report_path))

    if result.skipped and not overwrite:
        result.notices.append(
            f"{len(result.skipped)} existing bridge file(s) were not overwritten. "
            "Pass --bridge-refresh to regenerate the full bridge artifact set "
            "(recommended when bridge state is incomplete or stale)."
        )

    # Phase 2: emit Claude subagent stubs delegating to copilot-vscode source.
    # Opt-in via host feature subselector; default emission is unchanged.
    features = host_features or []
    if (
        target_framework == "claude"
        and src_fw == "copilot-vscode"
        and "bridge:copilot-vscode-to-claude:subagents" in features
    ):
        from agentteams.bridge_subagents import emit_subagent_stubs

        stub_result = emit_subagent_stubs(
            source_dir=source_dir,
            output_root=output_root,
            dry_run=dry_run,
            overwrite=overwrite or merge_only,  # bridge-refresh / -merge both regenerate stubs
        )
        result.written.extend(stub_result.written)
        result.skipped.extend(stub_result.skipped)
        result.errors.extend(stub_result.errors)
        if stub_result.experts_collapsed:
            result.notices.append(
                f"Collapsed {len(stub_result.experts_collapsed)} workstream-expert "
                f"agent(s) into a single parametric stub: "
                f"{', '.join(stub_result.experts_collapsed)}"
            )

    # Phase 1: emit the todo-from-plan skill so the bridged orchestrator
    # can project the canonical plan-steps CSV into TodoWrite on activation.
    if (
        target_framework == "claude"
        and src_fw == "copilot-vscode"
        and "bridge:copilot-vscode-to-claude:todo-projection" in features
    ):
        from agentteams.plan_steps_todo import render_skill as _render_todo_skill

        skill_path = output_root / ".claude" / "skills" / "todo-from-plan.md"
        if skill_path.exists() and not (overwrite or merge_only):
            result.skipped.append(str(skill_path))
        else:
            if not dry_run:
                skill_path.parent.mkdir(parents=True, exist_ok=True)
                skill_path.write_text(_render_todo_skill(), encoding="utf-8")
            result.written.append(str(skill_path))

    # Phase 3: emit Claude hooks example + recursion-guarded guard script.
    # Opt-in; emits .claude/settings.agentteams.example.json (user merges
    # into their own settings.json) and .claude/hook-guard.sh.
    if (
        target_framework == "claude"
        and src_fw == "copilot-vscode"
        and "bridge:copilot-vscode-to-claude:hooks" in features
    ):
        from agentteams.hooks_emit import emit_hooks_artifacts

        hook_result = emit_hooks_artifacts(
            source_dir=source_dir,
            output_root=output_root,
            dry_run=dry_run,
            overwrite=overwrite or merge_only,
        )
        result.written.extend(hook_result.written)
        result.skipped.extend(hook_result.skipped)
        result.errors.extend(hook_result.errors)
        if hook_result.written:
            result.notices.append(
                "Hooks artifacts emitted. Merge "
                ".claude/settings.agentteams.example.json into your own "
                ".claude/settings.json to activate notification hooks."
            )

    # Phase 5: emit recurring routine specs for Claude's /schedule skill.
    if (
        target_framework == "claude"
        and src_fw == "copilot-vscode"
        and "bridge:copilot-vscode-to-claude:schedule" in features
    ):
        from agentteams.schedule_emit import emit_schedule_artifact

        sched_result = emit_schedule_artifact(
            source_dir=source_dir,
            output_root=output_root,
            dry_run=dry_run,
            overwrite=overwrite or merge_only,
        )
        result.written.extend(sched_result.written)
        result.skipped.extend(sched_result.skipped)
        result.errors.extend(sched_result.errors)
        if sched_result.omitted_routines:
            result.notices.append(
                "Omitted schedule routines (no matching canonical agent in source): "
                + ", ".join(sched_result.omitted_routines)
            )

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
    goose_check_note = ""
    if target_framework == "goose":
        # W5: clarify that --bridge-check only validates source-side hashes, not
        # generated recipe YAML content.  Users sometimes assume bridge-check covers
        # the full output; this callout prevents false confidence.
        goose_check_note = (
            "\n## Bridge check scope\n\n"
            "`--bridge-check` verifies that source `.agent.md` files match their\n"
            "SHA-256 hashes recorded at bridge-generation time. It does NOT validate\n"
            "generated recipe YAML files, `.goosehints` enrichment, or AGENTS.md content.\n"
            "To validate recipe structure: `agentteams --framework goose --recipe-check --output <recipes-dir>`\n"
            "checks version string, no model: key, sub_recipe path resolution, and non-empty instructions.\n"
            "For full recipe generation (alternative to bridge): "
            "`agentteams --convert-from .github/agents --framework goose --output .goose/recipes`\n"
            "\n## CLI + MCP entry recipe\n\n"
            "The bridge emits `.goose/recipes/bridge-orchestrator.yaml` — run it with\n"
            "`goose run --recipe .goose/recipes/bridge-orchestrator.yaml` to start the\n"
            "bridged team WITH the `developer` (CLI) extension by default. Pass\n"
            "`--target-host-features bridge:<source>-to-goose:mcp` and build the source\n"
            "with an MCP token first to also wire the selected (first-party, read-only,\n"
            "orchestrator-scoped) MCP servers into that recipe.\n"
        )
    return (
        "# Bridge Quickstart Snippet\n\n"
        "Use this as your first prompt:\n\n"
        "```text\n"
        f"Use the {source_framework} agent infrastructure through this {target_framework} bridge.\n"
        "Start with the source orchestrator and follow source governance rules.\n"
        "Do not bypass orchestrator for multi-step, destructive, or cross-repo work.\n"
        "\n"
        "Retrieval-first: for 'where is X' / 'have we seen Y before' / thematic\n"
        "questions, run `agentteams --query-index \"<question>\" --query-strategy vector`\n"
        "before grep. The memory-index covers durable prose (work summaries,\n"
        "plans, CHANGELOG). See references/bridges/<src>-to-<target>/domain-boundary.md\n"
        "for the boundary vs project-level retrieval contracts.\n"
        "```\n"
        + goose_check_note
    )


def _render_entrypoint(source_framework: str, target_framework: str) -> str:
    return (
        f"# Bridge Entrypoint: {source_framework} -> {target_framework}\n\n"
        "This is a lightweight interface bridge.\n"
        "Canonical agent definitions remain in source framework files.\n"
        "Use orchestrator-first routing for team-based work.\n"
        "\n"
        "## Retrieval Surface\n\n"
        "Before falling back to grep / filesystem search for thematic or\n"
        "cross-summary questions, query the agentteams memory-index:\n\n"
        "```\n"
        "agentteams --query-index \"<the user's question>\" --query-strategy vector --query-k 5\n"
        "```\n\n"
        "Some installations require `--description PATH` for read-only queries —\n"
        "pass the project brief if so. The index covers durable prose (work\n"
        "summaries, plans, CHANGELOG, references), NOT code. For code-symbol\n"
        "lookups, grep remains primary.\n\n"
        "See `domain-boundary.md` (this directory) for the boundary between the\n"
        "memory-index vector mode and project-level retrieval-integrator\n"
        "validation contracts — they address different questions and must not\n"
        "be conflated.\n"
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
        entry_body = (
            f"Use source framework `{source_framework}` as canonical agent infrastructure.\n"
            f"Read `{rel_inventory}` and `{rel_quickstart}`.\n"
            "Start with orchestrator routing.\n"
        )
        entry = (
            "# Claude Bridge Entry Point\n\n"
            + _wrap_fence("claude-bridge-entry", entry_body)
        )
        return [
            (claude_md, entry),
            (
                claude_dir / "agent-team.md",
                _wrap_fence(
                    "claude-bridge-pointer",
                    f"See `{rel_inventory}` for bridge inventory.\n",
                ),
            ),
            (
                claude_dir / "quickstart-snippet.md",
                _wrap_fence(
                    "claude-bridge-quickstart",
                    f"See `{rel_quickstart}` for bridge quickstart.\n",
                ),
            ),
            (
                claude_dir / "README.md",
                "# Claude Bridge\n\n"
                + _wrap_fence(
                    "claude-bridge-readme",
                    "Lightweight bridge; source files are canonical.\n",
                ),
            ),
        ]

    if target_framework == "goose":
        goose_dir = root / ".goose"
        # AGENTS.md is a SHARED, multi-tool standard file (Cursor/Codex/Cline also
        # read it). It is emitted fenced so --bridge-merge updates only the fence
        # and leaves an existing unfenced AGENTS.md untouched (no-fence -> skip).
        # See references/bridge-refresh-safety.md before --bridge-refresh.
        agents_entry_body = (
            f"Use source framework `{source_framework}` as canonical agent infrastructure.\n"
            f"Read `{rel_inventory}` and `{rel_quickstart}`.\n"
            "Start with orchestrator routing.\n"
        )
        agents_md = (
            "# Agent Team (Goose bridge)\n\n"
            + _wrap_fence("goose-bridge-entry", agents_entry_body)
        )
        # .goosehints pulls the bridged AGENTS.md into Goose's prompt via @AGENTS.md
        # (Goose's default CONTEXT_FILE_NAMES is ['.goosehints', 'AGENTS.md']).
        goosehints = _wrap_fence(
            "goose-bridge-hints",
            "@AGENTS.md\n\n"
            f"This project bridges the `{source_framework}` agent team; source files are canonical.\n",
        )
        return [
            (root / "AGENTS.md", agents_md),
            (root / ".goosehints", goosehints),
            (
                goose_dir / "README.md",
                "# Goose Bridge\n\n"
                + _wrap_fence(
                    "goose-bridge-readme",
                    "Lightweight bridge; source files are canonical.\n",
                ),
            ),
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


def _wrap_fence(region_id: str, body: str, version: int = 1) -> str:
    """Wrap body in an AGENTTEAMS-BRIDGE fence the merge logic can find."""
    body = body if body.endswith("\n") else body + "\n"
    return (
        f"<!-- AGENTTEAMS-BRIDGE:BEGIN {region_id} v={version} -->\n"
        f"{body}"
        f"<!-- AGENTTEAMS-BRIDGE:END {region_id} -->\n"
    )


def _render_domain_boundary(source_framework: str, target_framework: str) -> str:
    return (
        "# Domain Boundary — Memory-Index Vector vs Retrieval-Integrator\n\n"
        "The agentteams `memory_index` module ships a `--query-strategy vector` "
        "mode that is a **local sparse tf-idf vector-space ranking for "
        "memory-history retrieval only**. It is stdlib-only, deterministic, and "
        "scoped to durable text sources (work summaries, CHANGELOG, durable "
        "plans).\n\n"
        "It is **separate** from any project-level retrieval-integrator "
        "validation contract (e.g., relational metadata retrieval against "
        "project data tables). When a project's retrieval contract has "
        "`mode: relational-metadata`, that is independent from the memory-"
        "index's `vector_runtime_mode: sparse-tfidf-cosine` — the two retrieval "
        "surfaces address different questions and must not be conflated.\n\n"
        f"Bridge direction: `{source_framework}` → `{target_framework}`.\n"
    )


def _render_recall_skill() -> str:
    return (
        "---\n"
        "name: recall\n"
        "description: Memory-index retrieval via agentteams --query-index. "
        "Use BEFORE grep for broad 'where' or thematic questions about this project.\n"
        "---\n\n"
        "# /recall — Memory-Index Retrieval\n\n"
        "For broad 'where is X' or thematic questions, query the agentteams "
        "memory-index before falling back to grep:\n\n"
        "```\n"
        "agentteams --query-index \"<the user's question, quoted>\" "
        "--query-strategy vector --query-k 5\n"
        "```\n\n"
        "(Some installations require `--description PATH` for read-only "
        "queries — pass the project brief if so.)\n\n"
        "## Fallback policy\n\n"
        "`non-blocking-file-read-then-search` (declared in the index): if "
        "vector returns no/weak hits, try `--query-strategy lexical`, then "
        "fall back to Grep / Glob. Never block on the index.\n\n"
        "## Caveats\n\n"
        "- Index mode is `sparse-tfidf-cosine` — keyword-aware, NOT semantic "
        "  embeddings. Synonyms and paraphrases may miss.\n"
        "- Index covers durable sources (work summaries, CHANGELOG, plans), "
        "  NOT code or the gitignored `tmp/` scratch tree.\n"
        "- Index is rebuilt explicitly via `--refresh-index`, not on file save.\n"
    )


def _merge_target_file(*, existing: str, rendered: str) -> tuple[str, str]:
    """Merge bridge-rendered content into an existing target file.

    For each AGENTTEAMS-BRIDGE fence in `rendered`, locate the matching fence
    region in `existing` and substitute the rendered content. Content outside
    fences in `existing` is preserved verbatim.

    Returns:
        (merged_text, status). Status is one of:
        - 'merged': at least one fence region updated; merged_text returned.
        - 'no-fence': existing file lacks any matching AGENTTEAMS-BRIDGE fence.
            Caller should skip with notice; merged_text is the original.
        - 'no-rendered-fence': rendered content has no fences (programmer
            error in caller); merged_text is original.
    """
    rendered_regions = _extract_fence_regions(rendered)
    if not rendered_regions:
        return existing, "no-rendered-fence"

    out = existing
    any_replaced = False
    for region_id, region_text in rendered_regions.items():
        pattern = re.compile(
            r"<!--\s*AGENTTEAMS-BRIDGE:BEGIN\s+"
            + re.escape(region_id)
            + r"\s+v=\d+\s*-->.*?<!--\s*AGENTTEAMS-BRIDGE:END\s+"
            + re.escape(region_id)
            + r"\s*-->",
            re.DOTALL,
        )
        new_out, n = pattern.subn(region_text.rstrip("\n"), out)
        if n > 0:
            out = new_out
            any_replaced = True

    if not any_replaced:
        return existing, "no-fence"
    return out, "merged"


def _extract_fence_regions(text: str) -> dict[str, str]:
    """Extract AGENTTEAMS-BRIDGE fenced regions from text.

    Returns a dict mapping region_id to the full fence block including the
    BEGIN/END markers.
    """
    regions: dict[str, str] = {}
    for match in _FENCE_BEGIN_RE.finditer(text):
        region_id = match.group("region")
        end_marker = _FENCE_END_TPL.format(region=region_id)
        end_idx = text.find(end_marker, match.end())
        if end_idx == -1:
            continue
        block = text[match.start() : end_idx + len(end_marker)]
        regions[region_id] = block
    return regions


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
