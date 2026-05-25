"""framework_research.py — Build live framework-drift placeholders for rendering.

Mirrors the contract of `agentteams.security_refs.build_security_placeholders`
so the daily-pipeline's upstream-research stage is transmitted to consumer
repositories through `--update --merge`.

Snapshot path:
    tmp/daily-pipeline/framework-research/latest.json

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
SNAPSHOT_REL = "tmp/daily-pipeline/framework-research/latest.json"
STALE_DAYS = 7

EXPECTED_FRONT_MATTER_KEYS = ["name", "description", "tools", "model"]
EXPECTED_LOCATIONS = [".claude/agents", "CLAUDE.md"]

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


def refresh_snapshot(repo_root: Path, offline: bool = False) -> dict[str, Any]:
    """Fetch (or reuse) the upstream Claude Code docs snapshot.

    Returns the snapshot dict that was written (or the prior cache if the
    refresh was skipped). Used by the daily-pipeline research stage.
    """
    snapshot_path = _snapshot_path(repo_root)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    now = _utcnow()
    adapter = _load_local_adapter_constants(repo_root)
    fetch_status = "skipped" if offline else "ok"
    fetch_error = ""
    upstream_tokens: dict[str, list[str]] = {}
    raw_len = 0

    if not offline:
        try:
            text = _fetch(CLAUDE_DOC_URL)
            raw_len = len(text)
            upstream_tokens = _scan_tokens(text)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fetch_status = "failed"
            fetch_error = f"{type(exc).__name__}: {exc}"

    if fetch_status in {"skipped", "failed"}:
        prev = _load_snapshot(snapshot_path)
        if prev:
            return prev
        upstream_tokens = {}

    keys_diff = _diff_keys(adapter["required_front_matter_keys"], upstream_tokens.get("front_matter_keys_present", []))
    snapshot = {
        "schema_version": "1.0",
        "framework": "claude",
        "source_url": CLAUDE_DOC_URL,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_on": now.strftime("%Y-%m-%d"),
        "fetch_status": fetch_status,
        "fetch_error": fetch_error,
        "raw_bytes": raw_len,
        "upstream_tokens": upstream_tokens,
        "local_adapter": adapter,
        "keys_diff": keys_diff,
    }
    snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return snapshot


def _render_table(snapshot: dict[str, Any]) -> str:
    tokens = snapshot.get("upstream_tokens", {})
    adapter = snapshot.get("local_adapter", {})
    diff = snapshot.get("keys_diff", {})
    lines = [
        "| Field | Value |",
        "|---|---|",
        f"| framework | {snapshot.get('framework', 'claude')} |",
        f"| source_url | {snapshot.get('source_url', '')} |",
        f"| upstream_front_matter_keys | {', '.join(tokens.get('front_matter_keys_present', [])) or '—'} |",
        f"| upstream_locations | {', '.join(tokens.get('locations_present', [])) or '—'} |",
        f"| local_required_keys | {', '.join(adapter.get('required_front_matter_keys', [])) or '—'} |",
        f"| local_default_allowed_tools | {', '.join(adapter.get('default_allowed_tools', [])) or '—'} |",
        f"| matched | {', '.join(diff.get('matched', [])) or '—'} |",
        f"| documented_locally_not_upstream | {', '.join(diff.get('missing_upstream', [])) or '—'} |",
        f"| new_upstream | {', '.join(diff.get('new_upstream', [])) or '—'} |",
    ]
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

    Reads the existing snapshot under `tmp/daily-pipeline/framework-research/`.
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
