"""framework_research.py — Build live framework-drift placeholders for rendering.

Mirrors the contract of `agentteams.security_refs.build_security_placeholders`
so the daily-pipeline's upstream-research stage is transmitted to consumer
repositories through `--update --merge`.

Snapshot path (gitignored — operator-local state, regenerated daily):
    tmp/daily-pipeline/framework-research/latest.json — gitignored.

`refresh_snapshot()` (re)fetches upstream docs and writes the snapshot.
`build_framework_placeholders()` reads whatever snapshot exists and returns
placeholders ready to be merged into `manifest["auto_resolved_placeholders"]`.
The build path defaults to offline reuse so that consumer repos do not need
network access on every `--update --merge`.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_DOC_URL = "https://docs.anthropic.com/en/docs/claude-code/sub-agents"
SNAPSHOT_REL = "tmp/daily-pipeline/framework-research/latest.json"  # gitignored — operator-local
STALE_DAYS = 7

EXPECTED_FRONT_MATTER_KEYS = ["name", "description", "tools", "model"]
EXPECTED_LOCATIONS = [".claude/agents", "CLAUDE.md"]

# Registry: each entry produces an advisory snapshot. Token allow-lists are
# intentionally small and prose-survivable; see plan
# references/plans/daily-pipeline-deferred-followups-2026-05-25.plan.md A6.
FRAMEWORK_REGISTRY = {
    "claude": {
        "label": "Claude Code Sub-Agents",
        "source_url": CLAUDE_DOC_URL,
        "expert_ref": "references/claude-agent-infrastructure-expert.md",
        "expected_keys": ["name", "description", "tools", "model"],
        "expected_locations": [".claude/agents", "CLAUDE.md"],
    },
    "copilot_vscode": {
        "label": "GitHub Copilot — VS Code Chat Modes",
        "source_url": "https://code.visualstudio.com/docs/copilot/customization/custom-chat-modes",
        "expert_ref": "references/copilot-agent-infrastructure-expert.md",
        "expected_keys": ["description", "tools", "model"],
        "expected_locations": [".github/agents", ".github/chatmodes"],
    },
    "copilot_cli": {
        "label": "GitHub Copilot — CLI",
        "source_url": "https://docs.github.com/en/copilot/github-copilot-in-the-cli/about-github-copilot-in-the-cli",
        "expert_ref": "references/copilot-agent-infrastructure-expert.md",
        "expected_keys": ["gh", "copilot"],
        "expected_locations": [".github/copilot"],
    },
}

_KEY_LIST_RE = re.compile(r"_CLAUDE_REQUIRED_KEYS\s*=\s*\{([^}]*)\}")
_DEFAULT_TOOLS_RE = re.compile(r'_CLAUDE_DEFAULT_ALLOWED_TOOLS\s*=\s*"([^"]+)"')


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _snapshot_path(repo_root: Path) -> Path:
    return repo_root / SNAPSHOT_REL


def _load_local_adapter_constants(repo_root: Path) -> dict[str, list[str]]:
    src_path = repo_root / "agentteams" / "frameworks" / "claude.py"
    try:
        src = src_path.read_text(encoding="utf-8")
    except OSError:
        return {"required_front_matter_keys": [], "default_allowed_tools": []}
    keys_match = _KEY_LIST_RE.search(src)
    required = sorted(re.findall(r'"([a-z_-]+)"', keys_match.group(1))) if keys_match else []
    tools_match = _DEFAULT_TOOLS_RE.search(src)
    tools = [t.strip() for t in (tools_match.group(1) if tools_match else "").split(",") if t.strip()]
    return {"required_front_matter_keys": required, "default_allowed_tools": tools}


def _scan_tokens(text: str) -> dict[str, list[str]]:
    lower = text.lower()
    found_keys = sorted({k for k in EXPECTED_FRONT_MATTER_KEYS if re.search(rf"\b{k}\b\s*:", text)})
    found_locations = sorted({loc for loc in EXPECTED_LOCATIONS if loc.lower() in lower})
    return {"front_matter_keys_present": found_keys, "locations_present": found_locations}


def _diff_keys(expected: list[str], observed: list[str]) -> dict[str, list[str]]:
    e, o = set(expected), set(observed)
    return {"missing_upstream": sorted(e - o), "new_upstream": sorted(o - e), "matched": sorted(e & o)}


def _fetch(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "agentteams-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _load_snapshot(snapshot_path: Path) -> dict[str, Any] | None:
    if not snapshot_path.exists():
        return None
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _snapshot_age_hours(snapshot: dict[str, Any]) -> float | None:
    ts = snapshot.get("generated_at", "")
    try:
        dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (_utcnow() - dt).total_seconds() / 3600.0


def _scan_tokens_for(text: str, expected_keys: list[str], expected_locations: list[str]) -> dict[str, list[str]]:
    lower = text.lower()
    found_keys = sorted({k for k in expected_keys if re.search(rf"\b{re.escape(k)}\b\s*:", text)})
    found_locations = sorted({loc for loc in expected_locations if loc.lower() in lower})
    return {"front_matter_keys_present": found_keys, "locations_present": found_locations}


def _scan_framework(entry: dict[str, Any], offline: bool) -> dict[str, Any]:
    fetch_status = "skipped" if offline else "ok"
    fetch_error = ""
    tokens: dict[str, list[str]] = {}
    raw_len = 0
    if not offline:
        try:
            text = _fetch(entry["source_url"])
            raw_len = len(text)
            tokens = _scan_tokens_for(text, entry["expected_keys"], entry["expected_locations"])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fetch_status = "failed"
            fetch_error = f"{type(exc).__name__}: {exc}"
    return {
        "label": entry["label"],
        "source_url": entry["source_url"],
        "expert_ref": entry["expert_ref"],
        "expected_keys": entry["expected_keys"],
        "fetch_status": fetch_status,
        "fetch_error": fetch_error,
        "raw_bytes": raw_len,
        "upstream_tokens": tokens,
    }


def refresh_snapshot(repo_root: Path, offline: bool = False) -> dict[str, Any]:
    """Fetch (or reuse) upstream framework docs snapshots.

    Writes the multi-framework snapshot. Claude entries remain at the
    top level for backward compatibility with the prior single-framework
    schema; per-framework details live under `frameworks[id]`.
    """
    snapshot_path = _snapshot_path(repo_root)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    now = _utcnow()
    adapter = _load_local_adapter_constants(repo_root)

    per_framework: dict[str, dict[str, Any]] = {}
    for fid, entry in FRAMEWORK_REGISTRY.items():
        per_framework[fid] = _scan_framework(entry, offline=offline)

    # If everything was skipped or failed, prefer the prior cached snapshot.
    all_unfetched = all(p["fetch_status"] != "ok" for p in per_framework.values())
    if all_unfetched:
        prev = _load_snapshot(snapshot_path)
        if prev:
            return prev

    claude = per_framework["claude"]
    claude_tokens = claude.get("upstream_tokens", {})
    keys_diff = _diff_keys(adapter["required_front_matter_keys"], claude_tokens.get("front_matter_keys_present", []))

    snapshot = {
        "schema_version": "1.1",
        "framework": "claude",  # legacy top-level for back-compat
        "source_url": CLAUDE_DOC_URL,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_on": now.strftime("%Y-%m-%d"),
        "fetch_status": claude["fetch_status"],
        "fetch_error": claude["fetch_error"],
        "raw_bytes": claude["raw_bytes"],
        "upstream_tokens": claude_tokens,
        "local_adapter": adapter,
        "keys_diff": keys_diff,
        "frameworks": per_framework,
    }
    snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return snapshot


def _render_table(snapshot: dict[str, Any]) -> str:
    frameworks = snapshot.get("frameworks") or {
        "claude": {
            "label": "Claude Code Sub-Agents",
            "source_url": snapshot.get("source_url", ""),
            "fetch_status": snapshot.get("fetch_status", "?"),
            "upstream_tokens": snapshot.get("upstream_tokens", {}),
            "expected_keys": EXPECTED_FRONT_MATTER_KEYS,
        }
    }
    adapter = snapshot.get("local_adapter", {})
    lines = [
        "| Framework | Fetch | Tokens observed | Locations observed |",
        "|---|---|---|---|",
    ]
    for fid, entry in frameworks.items():
        tokens = entry.get("upstream_tokens", {})
        lines.append(
            f"| {fid} ({entry.get('label', fid)}) "
            f"| `{entry.get('fetch_status', '?')}` "
            f"| {', '.join(tokens.get('front_matter_keys_present', [])) or '—'} "
            f"| {', '.join(tokens.get('locations_present', [])) or '—'} |"
        )
    lines.append("")
    lines.append("Local Claude adapter constants:")
    lines.append(
        f"- required_front_matter_keys: {', '.join(adapter.get('required_front_matter_keys', [])) or '—'}"
    )
    lines.append(
        f"- default_allowed_tools: {', '.join(adapter.get('default_allowed_tools', [])) or '—'}"
    )
    diff = snapshot.get("keys_diff", {})
    lines.append(
        f"- claude diff — matched: {', '.join(diff.get('matched', [])) or '—'}; "
        f"new_upstream: {', '.join(diff.get('new_upstream', [])) or '—'}; "
        f"missing_upstream: {', '.join(diff.get('missing_upstream', [])) or '—'}"
    )
    return "\n".join(lines)


def _staleness_banner(snapshot: dict[str, Any]) -> str:
    age = _snapshot_age_hours(snapshot)
    if age is None:
        return "> ⚠️ **STALE DATA** — snapshot timestamp could not be parsed."
    if age >= STALE_DAYS * 24:
        return (
            f"> ⚠️ **STALE DATA** — snapshot is {age / 24:.1f} days old "
            f"(threshold {STALE_DAYS} days). Run the daily research stage online."
        )
    return ""


def build_framework_placeholders(output_dir: Path, offline: bool = True) -> dict[str, str]:
    """Return placeholders for the framework-watch reference template.

    Reads the existing snapshot under `tmp/daily-pipeline/framework-research/` (gitignored).
    Set `offline=False` to refresh from the network first (daily-pipeline use).
    """
    # The snapshot lives under the *module* tree, not the output tree.
    repo_root = Path(__file__).resolve().parents[1]
    snapshot_path = _snapshot_path(repo_root)
    snapshot = _load_snapshot(snapshot_path)
    if snapshot is None or not offline:
        snapshot = refresh_snapshot(repo_root, offline=offline)

    table = _render_table(snapshot)
    banner = _staleness_banner(snapshot)
    summary_parts = [
        f"matched={len(snapshot.get('keys_diff', {}).get('matched', []))}",
        f"new_upstream={len(snapshot.get('keys_diff', {}).get('new_upstream', []))}",
        f"missing_upstream={len(snapshot.get('keys_diff', {}).get('missing_upstream', []))}",
    ]
    return {
        "FRAMEWORK_RESEARCH_FRAMEWORK": str(snapshot.get("framework", "claude")),
        "FRAMEWORK_RESEARCH_SOURCE_URL": str(snapshot.get("source_url", CLAUDE_DOC_URL)),
        "FRAMEWORK_RESEARCH_GENERATED_ON": str(snapshot.get("generated_on", "")),
        "FRAMEWORK_RESEARCH_FETCH_STATUS": str(snapshot.get("fetch_status", "unknown")),
        "FRAMEWORK_RESEARCH_TABLE": table,
        "FRAMEWORK_RESEARCH_STALE_BANNER": banner,
        "FRAMEWORK_RESEARCH_DIFF_SUMMARY": " ".join(summary_parts),
    }


# ---------------------------------------------------------------------------
# Module-core update path (human-invoked; never run by cron).
# ---------------------------------------------------------------------------

EXPERT_REF_REL = "references/claude-agent-infrastructure-expert.md"
COPILOT_EXPERT_REF_REL = "references/copilot-agent-infrastructure-expert.md"
ALLOWED_EXPERT_REFS = {EXPERT_REF_REL, COPILOT_EXPERT_REF_REL}


def propose_module_patch(repo_root: Path) -> dict[str, Any]:
    """Produce a v1 module-core patch proposal across all frameworks.

    Targets: append/refresh a dated observation stanza in each
    framework's expert reference. Constants in `agentteams/frameworks/`
    are NOT proposed for mutation — the upstream token scan does not
    distinguish required from supported keys.
    """
    snapshot = _load_snapshot(_snapshot_path(repo_root)) or {}
    if not snapshot.get("generated_on"):
        return {"changes": [], "reason": "no snapshot"}

    frameworks = snapshot.get("frameworks") or {"claude": {
        "label": "Claude Code Sub-Agents",
        "source_url": snapshot.get("source_url", CLAUDE_DOC_URL),
        "expert_ref": EXPERT_REF_REL,
        "upstream_tokens": snapshot.get("upstream_tokens", {}),
    }}

    # Group frameworks by target expert-ref so each file gets one change.
    by_path: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for fid, entry in frameworks.items():
        ref_path = entry.get("expert_ref", "")
        if not ref_path:
            continue
        by_path.setdefault(ref_path, []).append((fid, entry))

    changes: list[dict[str, Any]] = []
    for ref_path, entries in by_path.items():
        target = repo_root / ref_path
        original_text = target.read_text(encoding="utf-8") if target.exists() else ""
        current_text = original_text
        framework_ids: list[str] = []
        for fid, entry in entries:
            block = _render_observation_block_for(
                fid=fid,
                entry=entry,
                generated_on=snapshot.get("generated_on", ""),
                claude_diff=snapshot.get("keys_diff", {}) if fid == "claude" else {},
            )
            current_text = _splice_observation_block(current_text, block, fid=fid)
            framework_ids.append(fid)
        if current_text == original_text:
            continue
        changes.append({
            "frameworks": framework_ids,
            "path": ref_path,
            "operation": "append_or_replace_section",
            "section_heading": ", ".join(_observation_heading(fid) for fid in framework_ids),
            "old_text": original_text,
            "new_text": current_text,
        })

    if not changes:
        return {"changes": [], "reason": "no drift to record"}

    return {
        "schema_version": "1.1",
        "generated_at": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_generated_on": snapshot.get("generated_on", ""),
        "frameworks": list(frameworks.keys()),
        "changes": changes,
    }


def _observation_heading(fid: str) -> str:
    return f"## Observed Upstream Tokens — `{fid}` (Daily Pipeline)"


def _observation_re_for(fid: str) -> re.Pattern:
    return re.compile(
        rf"\n## Observed Upstream Tokens — `{re.escape(fid)}` \(Daily Pipeline\).*?(?=\n## |\Z)",
        re.DOTALL,
    )


def _render_observation_block_for(
    fid: str,
    entry: dict[str, Any],
    generated_on: str,
    claude_diff: dict[str, list[str]] | None = None,
) -> str:
    tokens = entry.get("upstream_tokens", {})
    keys = tokens.get("front_matter_keys_present", [])
    locations = tokens.get("locations_present", [])
    lines = [
        "",
        _observation_heading(fid),
        "",
        f"Recorded by the daily pipeline on `{generated_on}` "
        f"from `{entry.get('source_url', '')}`.",
        "",
        f"- Upstream tokens observed: {', '.join(keys) or '—'}",
        f"- Upstream locations observed: {', '.join(locations) or '—'}",
        f"- Fetch status: `{entry.get('fetch_status', '?')}`",
    ]
    if claude_diff:
        lines.extend([
            f"- Matched against local required keys: {', '.join(claude_diff.get('matched', [])) or '—'}",
            f"- Documented locally but not seen upstream: "
            f"{', '.join(claude_diff.get('missing_upstream', [])) or '—'}",
            f"- Seen upstream but not in local required set "
            f"(advisory only — may be optional keys): "
            f"{', '.join(claude_diff.get('new_upstream', [])) or '—'}",
        ])
    return "\n".join(lines) + "\n"


_LEGACY_OBSERVATION_RE = re.compile(
    r"\n## Observed Upstream Tokens \(Daily Pipeline\).*?(?=\n## |\Z)",
    re.DOTALL,
)


def _splice_observation_block(current: str, block: str, fid: str = "claude") -> str:
    # Strip the legacy (single-framework) heading once for the Claude file.
    if fid == "claude":
        current = _LEGACY_OBSERVATION_RE.sub("", current, count=1)
    pat = _observation_re_for(fid)
    if pat.search(current):
        return pat.sub("\n" + block.rstrip() + "\n", current, count=1).rstrip() + "\n"
    return (current.rstrip() + "\n" + block).rstrip() + "\n"


def apply_module_patch(proposal: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Apply a v1 proposal in-place; refuses to run in CI.

    Allow-listed operations only. The caller is responsible for
    git-committing on success and running the relevant tests.
    """
    import os

    if os.environ.get("CI") and not os.environ.get("AGENTTEAMS_ALLOW_CI_APPLY"):
        raise RuntimeError(
            "apply_module_patch refuses to run in CI without an explicit "
            "AGENTTEAMS_ALLOW_CI_APPLY=1 marker. The auto-PR workflow at "
            ".github/workflows/framework-auto-update.yml sets this guard "
            "intentionally; never set it elsewhere."
        )

    changes = proposal.get("changes", [])
    if not changes:
        return {"applied": [], "reason": "nothing to apply"}

    allowed_paths = set(ALLOWED_EXPERT_REFS)
    allowed_ops = {"append_or_replace_section"}
    applied: list[str] = []
    for change in changes:
        path = change.get("path", "")
        op = change.get("operation", "")
        if path not in allowed_paths or op not in allowed_ops:
            raise RuntimeError(f"refusing change outside allow-list: {op} {path}")
        target = repo_root / path
        target.write_text(change["new_text"], encoding="utf-8")
        applied.append(path)
    return {"applied": applied}

