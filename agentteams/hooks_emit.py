"""Claude hooks emission for the copilot-vscode → claude bridge (Phase 3).

Generates two artifacts when host feature
``bridge:copilot-vscode-to-claude:hooks`` is selected:

1. ``.claude/settings.agentteams.example.json`` — a sample settings.json
   block the user merges into their own ``settings.json``. We never write
   ``settings.json`` directly to avoid clobbering user-authored config.
2. ``.claude/hook-guard.sh`` — a recursion-depth-guarded wrapper script
   the example hooks invoke. The guard records a notice to
   ``.claude/hook-notices/<YYYY-MM-DD>.log`` and refuses re-entry beyond
   depth 2 (override via ``AGENTTEAMS_HOOK_MAX_DEPTH``).

The hook mapping is data-driven: a small table maps each canonical
governance agent slug to the Claude hook event(s) that should surface a
notice when relevant. Default mode is *notification* — hooks log to disk
and let the user or orchestrator decide whether to invoke the subagent
stub. This is intentional: an automatic subagent fanout from every file
write is too aggressive for a first cut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Hook-event mapping. Keyed by canonical copilot-vscode agent slug; value
# is a list of (event, matcher) pairs the slug should be notified about.
# ``matcher`` follows Claude's PreToolUse/PostToolUse pattern (regex over
# tool names). ``None`` means the event accepts no matcher (e.g., Stop).
_HOOK_MAP: dict[str, list[tuple[str, str | None]]] = {
    "cleanup": [("PostToolUse", "Write|Edit")],
    "agent-updater": [("PostToolUse", "Write|Edit")],
    "code-hygiene": [("PostToolUse", "Write|Edit")],
    "security": [("PreToolUse", "Bash|Write|Edit")],
    "work-summarizer": [("Stop", None)],
    "post-production-auditor": [("Stop", None)],
    "drift": [("PostToolUse", "Write|Edit")],
}

_GUARD_SCRIPT = """#!/usr/bin/env bash
# agentteams hook-guard (Phase 3)
# Records a notice when a Claude hook fires for a bridged governance
# agent slug. Refuses re-entry beyond AGENTTEAMS_HOOK_MAX_DEPTH (default 2)
# to bound any agent → write → hook cascade.
set -u

event="${1:-unknown-event}"
slug="${2:-unknown-slug}"

max_depth="${AGENTTEAMS_HOOK_MAX_DEPTH:-2}"
current_depth="${AGENTTEAMS_HOOK_DEPTH:-0}"

if [ "$current_depth" -ge "$max_depth" ]; then
  # Silent no-op; do NOT escalate (exit 0 lets Claude continue).
  exit 0
fi

# Locate project root from script location: .claude/hook-guard.sh ⇒ root = parent of .claude
script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$(dirname "$script_dir")"
notice_dir="$project_root/.claude/hook-notices"
mkdir -p "$notice_dir"
date_stamp="$(date -u +%Y-%m-%d)"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[$ts] event=$event slug=$slug depth=$current_depth" >> "$notice_dir/$date_stamp.log"

# Increment depth for any downstream invocation triggered by this hook.
export AGENTTEAMS_HOOK_DEPTH="$((current_depth + 1))"
exit 0
"""


@dataclass
class HooksEmissionResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def _collect_active_slugs(source_dir: Path) -> set[str]:
    """Return slugs present in the canonical copilot-vscode source dir."""
    slugs: set[str] = set()
    if not source_dir.is_dir():
        return slugs
    for p in source_dir.iterdir():
        if p.is_file() and p.name.endswith(".agent.md"):
            slugs.add(p.name[: -len(".agent.md")])
    return slugs


def build_settings_dict(source_dir: Path) -> dict[str, Any]:
    """Build the Claude settings.json hooks block for active slugs."""
    active = _collect_active_slugs(source_dir)
    events: dict[str, list[dict[str, Any]]] = {}
    for slug, pairs in sorted(_HOOK_MAP.items()):
        if slug not in active:
            continue
        for event, matcher in pairs:
            cmd = f"bash .claude/hook-guard.sh {event} {slug}"
            entry: dict[str, Any] = {"hooks": [{"type": "command", "command": cmd}]}
            if matcher is not None:
                entry["matcher"] = matcher
            events.setdefault(event, []).append(entry)
    return {
        "_agentteams_managed": (
            "Example hooks block emitted by the agentteams copilot-vscode→claude "
            "bridge. Merge into your own .claude/settings.json; do not overwrite "
            "blindly. Hooks fire .claude/hook-guard.sh which logs to "
            ".claude/hook-notices/. See bridge:copilot-vscode-to-claude:hooks."
        ),
        "hooks": events,
    }


def emit_hooks_artifacts(
    *,
    source_dir: Path,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = True,
) -> HooksEmissionResult:
    """Write the example settings file and the hook-guard script.

    Files land under ``<output_root>/.claude/``:

    - ``settings.agentteams.example.json`` — sample hooks block.
    - ``hook-guard.sh`` — recursion-bounded notification wrapper.
    """
    result = HooksEmissionResult()
    claude_dir = output_root / ".claude"
    settings_path = claude_dir / "settings.agentteams.example.json"
    guard_path = claude_dir / "hook-guard.sh"

    settings = build_settings_dict(source_dir)
    settings_content = json.dumps(settings, indent=2, sort_keys=True) + "\n"

    for path, content, mode in (
        (settings_path, settings_content, 0o644),
        (guard_path, _GUARD_SCRIPT, 0o755),
    ):
        if path.exists() and not overwrite:
            result.skipped.append(str(path))
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            try:
                path.chmod(mode)
            except OSError:
                pass
        result.written.append(str(path))

    return result


__all__ = [
    "HooksEmissionResult",
    "build_settings_dict",
    "emit_hooks_artifacts",
]
