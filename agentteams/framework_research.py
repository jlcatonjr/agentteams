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
import html as _html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agentteams.frameworks.format_spec import FORMAT_SPECS as _FORMAT_SPECS
from agentteams.frameworks import format_spec as _format_spec

#: Canonical Claude Code sub-agents doc URL. Single-sourced from
#: ``frameworks.format_spec``; kept as a module attribute because tests and
#: ``build_framework_placeholders`` reference ``fr.CLAUDE_DOC_URL``.
CLAUDE_DOC_URL = _format_spec.CLAUDE_DOC_URL
SNAPSHOT_REL = "tmp/daily-pipeline/framework-research/latest.json"  # gitignored — operator-local
try:
    STALE_DAYS = int(os.environ.get("AGENTTEAMS_STALE_DAYS", "7"))
except ValueError:
    raise ValueError(
        "AGENTTEAMS_STALE_DAYS must be a positive integer, "
        f"got {os.environ.get('AGENTTEAMS_STALE_DAYS')!r}"
    ) from None
if STALE_DAYS < 1:
    raise ValueError(
        "AGENTTEAMS_STALE_DAYS must be a positive integer, "
        f"got {STALE_DAYS!r}"
    )

# Single-sourced from frameworks.format_spec (the claude spec). These module
# constants are the legacy single-framework scan vocabulary; the six-wide scan
# reads each framework's tokens from FRAMEWORK_REGISTRY (also derived below).
EXPECTED_FRONT_MATTER_KEYS = list(_FORMAT_SPECS["claude"].expected_doc_tokens)
EXPECTED_LOCATIONS = list(_FORMAT_SPECS["claude"].expected_locations)

#: Host-capability claims the spawn-authority / query-funnel feature relies on but which are NOT
#: verifiable from inside this repository (no such tokens appear in the tree). They are external
#: Claude Code / host facts; recorded here — with their source and the exact claim — so the feature
#: text can point at a tracked entry instead of asserting them as "authoritative." When the host
#: docs move, these are the claims to re-check. See
#: `templates/universal/orchestrator-spawn-authority.reference.template.md` and
#: `references/plans/cross-orchestrator-query-funnel.plan.md` §2.1.
HOST_CAPABILITY_CLAIMS: dict[str, dict[str, str]] = {
    "subagent_spawn_depth": {
        "claim": (
            "Claude Code caps subagent nesting (default ~3 layers; env "
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH) and withholds the spawn tool at the cap. "
            "Budget: a child orchestrator gets one flat team layer beneath it."
        ),
        "source_url": "https://code.claude.com/docs/en/subagents.md",
        "status": "external-unverified-in-repo",
    },
    "subagent_output_containment": {
        "claim": (
            "A subagent's output returns to its spawner, not the user — a delegate cannot "
            "directly prompt the user. This is the only runtime-enforced part of the funnel."
        ),
        "source_url": "https://code.claude.com/docs/en/subagents.md",
        "status": "external-unverified-in-repo",
    },
    "no_nested_teams": {
        "claim": (
            "Agent Teams (experimental): only the lead spawns teammates; teammates cannot spawn "
            "their own teammates ('no nested teams'). Bounds cross-repo/at-scale delegation."
        ),
        "source_url": "https://code.claude.com/docs/en/agent-teams.md",
        "status": "external-unverified-in-repo",
    },
}

# Registry: each entry produces an advisory snapshot. Token allow-lists are
# intentionally small and prose-survivable; see plan
# references/plans/daily-pipeline-deferred-followups-2026-05-25.plan.md A6.
# Derived from frameworks.format_spec.FORMAT_SPECS (single source of truth).
# Shape and insertion order are preserved for the snapshot schema and every
# existing consumer/test: {label, source_url, expert_ref, expected_keys,
# expected_locations}. ``expected_keys`` are the UPSTREAM-DOC watch tokens
# (spec.expected_doc_tokens) — a documentation signal, NOT necessarily the
# front-matter keys this project emits (spec.emitted_front_matter_keys); the
# two are cross-checked by tests/test_format_spec_single_source.py. The URL
# history that used to be commented here now lives beside each FormatSpec.
FRAMEWORK_REGISTRY = {
    fid: {
        "label": spec.label,
        "source_url": spec.source_url,
        "expert_ref": spec.expert_ref,
        "expected_keys": list(spec.expected_doc_tokens),
        "expected_locations": list(spec.expected_locations),
    }
    for fid, spec in _FORMAT_SPECS.items()
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


#: Substrings that would indicate upstream documents command-SCOPED tool permissions — i.e.
#: `Bash(cmd:*)` rather than bare `Bash`. Matched case-insensitively against the sub-agent doc.
_SCOPED_TOOL_MARKERS = (
    "bash(",
    "allowed-tools: bash(",
    "tools: bash(",
)


def _scan_scoped_tool_support(text: str) -> dict[str, Any]:
    """Look for upstream evidence that sub-agent front matter honours command-scoped tools.

    ``agentteams/frameworks/claude.py`` maps the ``retrieval`` vocabulary token to
    ``Bash(python -m agentteams.research:*)`` — a scoped permission granting one command rather
    than a shell. Whether a given Claude Code version honours that parenthesised scope inside
    *sub-agent* front matter (as opposed to slash-command front matter, where it is
    long-established) cannot be determined from inside this repository, and the failure direction
    is unsafe: a host ignoring the scope reads the entry as plain ``Bash`` and grants MORE than
    intended. ``references/retrieval-transport-policy.md`` records that uncertainty; without this
    probe, nothing would ever notice if the answer changed.

    Reports **evidence, never a verdict**. A marker appearing somewhere in the page is not proof
    that sub-agent front matter honours scoping — the doc might be describing slash commands or
    settings-file permissions — so the status is ``"evidence-found"``, not ``"supported"``. Only a
    human confirming behaviour against a real host may change the policy's language.

    Args:
        text: The fetched upstream documentation page.

    Returns:
        A dict with ``status`` (``"evidence-found"`` / ``"no-evidence"``) and the markers seen.
    """
    lower = text.lower()
    seen = sorted({m for m in _SCOPED_TOOL_MARKERS if m in lower})
    return {
        "status": "evidence-found" if seen else "no-evidence",
        "markers_seen": seen,
        "claim": (
            "Presence of a scoped-tool marker in upstream docs is EVIDENCE ONLY, not "
            "confirmation that sub-agent front matter honours command scoping. See "
            "references/retrieval-transport-policy.md."
        ),
    }


def _scan_tokens(text: str) -> dict[str, Any]:
    lower = text.lower()
    found_keys = sorted({k for k in EXPECTED_FRONT_MATTER_KEYS if re.search(rf"\b{k}\b\s*:", text)})
    found_locations = sorted({loc for loc in EXPECTED_LOCATIONS if loc.lower() in lower})
    return {
        "front_matter_keys_present": found_keys,
        "locations_present": found_locations,
        "scoped_tool_permissions": _scan_scoped_tool_support(text),
    }


def _diff_keys(expected: list[str], observed: list[str]) -> dict[str, list[str]]:
    e, o = set(expected), set(observed)
    return {"missing_upstream": sorted(e - o), "new_upstream": sorted(o - e), "matched": sorted(e & o)}


def _fetch(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return its decoded body (back-compat: text only)."""
    text, _meta = _fetch_with_meta(url, timeout=timeout)
    return text


#: A provider doc that resolves but returns less than this many bytes is almost
#: certainly not the real documentation page (a redirect stub, an error shell, an
#: empty SPA container). Scanning it for zero tokens would read as "no drift" — a
#: silent false-negative — so it is flagged as ``empty`` instead of ``ok``.
_MIN_DOC_BYTES = 500


def _fetch_with_meta(url: str, timeout: int = 10) -> tuple[str, dict[str, Any]]:
    """Fetch a URL, returning ``(body_text, meta)``.

    ``meta`` carries fetch-integrity signals the scan needs so a moved/empty page
    is not mistaken for "no drift":

    * ``status`` — final HTTP status code.
    * ``final_url`` — the URL after redirects (``urlopen`` follows them).
    * ``host_changed`` — True when the final host differs from the requested host,
      i.e. the doc has relocated to a different site (a finding, not "ok").
    """
    req = urllib.request.Request(url, headers={"User-Agent": "agentteams-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        final_url = resp.geturl()
        status = getattr(resp, "status", None) or resp.getcode()
    req_host = urllib.parse.urlsplit(url).netloc.lower()
    final_host = urllib.parse.urlsplit(final_url).netloc.lower()
    return body, {
        "status": status,
        "final_url": final_url,
        "host_changed": bool(final_host) and final_host != req_host,
    }


_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _html_to_text(raw: str) -> str:
    """Strip HTML to visible text before token scanning.

    Raw provider docs are HTML (often multi-MB, JS-heavy). Tags interleaved with
    text hid real tokens from the ``\\bkey\\b\\s*:`` scan and inflated byte counts
    (the assessment recorded a false ``missing: description`` on a 2 MB Claude
    page). This drops ``<script>``/``<style>`` bodies, removes tags, unescapes
    entities, and collapses horizontal whitespace — a best-effort text view, not a
    parser. Input that has no tags (already text, e.g. a test fixture) passes
    through essentially unchanged.
    """
    without_blocks = _SCRIPT_STYLE_RE.sub(" ", raw)
    without_tags = _TAG_RE.sub(" ", without_blocks)
    unescaped = _html.unescape(without_tags)
    return _WS_RE.sub(" ", unescaped)


def _load_snapshot(snapshot_path: Path) -> dict[str, Any] | None:
    if not snapshot_path.exists():
        return None
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _snapshot_age_hours(snapshot: dict[str, Any], now: _dt.datetime | None = None) -> float | None:
    """Age of the snapshot in hours. ``now`` is injectable so callers that thread a fixed clock
    (e.g. :func:`run_agent_conformance_check` under test) judge snapshot freshness against the same
    instant they use for the ledger gate, rather than against wall-clock time."""
    ts = snapshot.get("generated_at", "")
    try:
        dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return ((now or _utcnow()) - dt).total_seconds() / 3600.0


def _scan_tokens_for(text: str, expected_keys: list[str], expected_locations: list[str]) -> dict[str, list[str]]:
    lower = text.lower()
    found_keys = sorted({k for k in expected_keys if re.search(rf"\b{re.escape(k)}\b\s*:", text)})
    found_locations = sorted({loc for loc in expected_locations if loc.lower() in lower})
    return {"front_matter_keys_present": found_keys, "locations_present": found_locations}


def _scan_framework(entry: dict[str, Any], offline: bool) -> dict[str, Any]:
    """Fetch + scan one framework's upstream doc, with fetch-integrity signals.

    ``fetch_status`` distinguishes ``ok`` (a real page was scanned) from the two
    silent-false-negative cases the assessment flagged — ``moved`` (redirected to
    a different host: the doc relocated) and ``empty`` (resolved but implausibly
    small) — as well as ``failed`` (network error) and ``skipped`` (offline). A
    per-framework ``keys_diff`` compares this framework's expected watch tokens
    against what was observed, so drift is reported for all six frameworks, not
    just Claude.
    """
    result: dict[str, Any] = {
        "label": entry["label"],
        "source_url": entry["source_url"],
        "expert_ref": entry["expert_ref"],
        "expected_keys": entry["expected_keys"],
        "fetch_status": "skipped" if offline else "ok",
        "fetch_error": "",
        "final_url": "",
        "host_changed": False,
        "raw_bytes": 0,
        "text_bytes": 0,
        "upstream_tokens": {},
        "keys_diff": _diff_keys(entry["expected_keys"], []),
    }
    if offline:
        return result
    try:
        raw, meta = _fetch_with_meta(entry["source_url"])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result["fetch_status"] = "failed"
        result["fetch_error"] = f"{type(exc).__name__}: {exc}"
        return result

    text = _html_to_text(raw)
    result["raw_bytes"] = len(raw)
    result["text_bytes"] = len(text)
    result["final_url"] = meta["final_url"]
    result["host_changed"] = meta["host_changed"]
    if meta["host_changed"]:
        # Relocated to a different site. Do not scan the (likely generic) landing
        # page and report zero tokens as "no drift" — flag it for human re-check.
        result["fetch_status"] = "moved"
        # Render only the redirect HOST (netloc), never the full attacker-influenceable
        # final_url, since fetch_error is emitted into the reference table (audit 2026-W39
        # adversarial F7 / C-4: keep untrusted redirect content out of published markdown).
        # A hostname is markdown-safe; the full final_url stays in the gitignored snapshot only.
        _moved_host = urllib.parse.urlsplit(meta["final_url"]).netloc or "(unparseable host)"
        result["fetch_error"] = f"redirected to different host: {_moved_host}"
        return result
    if len(text.strip()) < _MIN_DOC_BYTES:
        result["fetch_status"] = "empty"
        result["fetch_error"] = f"page text only {len(text.strip())} bytes (<{_MIN_DOC_BYTES})"
        return result

    tokens = _scan_tokens_for(text, entry["expected_keys"], entry["expected_locations"])
    result["upstream_tokens"] = tokens
    result["keys_diff"] = _diff_keys(entry["expected_keys"], tokens.get("front_matter_keys_present", []))
    return result


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

    # Fall back to the cached snapshot only on TRANSIENT non-fetches (offline runs
    # or network errors). A 'moved' or 'empty' status is a real, 200-level integrity
    # finding — never swallow a fleet-wide relocation or stub-page interposition into
    # yesterday's all-clear (adversarial F4). If any framework is moved/empty we write
    # the fresh snapshot so the regression surfaces.
    _TRANSIENT = {"skipped", "failed"}
    all_transient = all(p["fetch_status"] in _TRANSIENT for p in per_framework.values())
    if all_transient:
        prev = _load_snapshot(snapshot_path)
        if prev:
            return prev

    claude = per_framework["claude"]
    claude_tokens = claude.get("upstream_tokens", {})
    # Single-source the top-level (legacy) keys_diff from the per-framework claude diff,
    # which is computed against the claude spec's expected_doc_tokens (name, description,
    # tools, model). Previously this diffed against the 2-key _CLAUDE_REQUIRED_KEYS regex
    # constant, so 'tools' — a key we emit and the doc documents — rendered as new_upstream
    # drift on EVERY run, and disagreed with the table's per-framework claude diff
    # (adversarial F1/F2, conflict-auditor F2). Both artifacts now show one signal.
    keys_diff = claude.get("keys_diff") or _diff_keys(
        claude.get("expected_keys", []), claude_tokens.get("front_matter_keys_present", [])
    )

    snapshot = {
        # 1.2: each frameworks[id] now carries a per-framework keys_diff and
        # fetch-integrity fields (final_url, host_changed, text_bytes) and
        # fetch_status can be moved/empty, not just ok/failed/skipped.
        "schema_version": "1.2",
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
        # Drift watches REMOVED expected keys (missing↓) + fetch-integrity (moved/empty).
        # New-upstream-key detection is NOT performed (the expected-token scan cannot see a
        # key it does not already expect — audit 2026-W39 adversarial F1); open-vocabulary
        # detection is a tracked follow-up (remediation-log). Do not read a blank Drift as
        # "the provider added no keys" — only as "no EXPECTED key went missing."
        "| Framework | Fetch | Tokens observed | Locations observed | Drift (missing keys ↓) |",
        "|---|---|---|---|---|",
    ]
    for fid, entry in frameworks.items():
        tokens = entry.get("upstream_tokens", {})
        fdiff = entry.get("keys_diff", {})
        drift_parts = []
        if fdiff.get("missing_upstream"):
            drift_parts.append("↓ " + ", ".join(fdiff["missing_upstream"]))
        drift = "; ".join(drift_parts) or "—"
        status = entry.get("fetch_status", "?")
        # Surface a relocated/empty doc inline so it is never read as "no drift". Render only
        # the HOST, never the full (attacker-influenceable) final_url (F7 / C-4): this string
        # is emitted into the reference table.
        if status in {"moved", "empty"} and entry.get("final_url"):
            _host = urllib.parse.urlsplit(entry["final_url"]).netloc or "(unparseable host)"
            status = f"{status} → {_host}"
        lines.append(
            f"| {fid} ({entry.get('label', fid)}) "
            f"| `{status}` "
            f"| {', '.join(tokens.get('front_matter_keys_present', [])) or '—'} "
            f"| {', '.join(tokens.get('locations_present', [])) or '—'} "
            f"| {drift} |"
        )
    lines.append("")
    lines.append("Local Claude adapter constants (parsed from claude.py source):")
    lines.append(
        f"- strictly-required keys (_CLAUDE_REQUIRED_KEYS): "
        f"{', '.join(adapter.get('required_front_matter_keys', [])) or '—'}"
    )
    lines.append(
        f"- default_allowed_tools: {', '.join(adapter.get('default_allowed_tools', [])) or '—'}"
    )
    diff = snapshot.get("keys_diff", {})
    lines.append(
        f"- claude diff (vs expected_doc_tokens) — matched: {', '.join(diff.get('matched', [])) or '—'}; "
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


#: Opt-in operational block appended to the framework-watch reference when a team maintains
#: framework adapters. Kept here (not in the template) so the entrypoint/procedure references stay
#: single-sourced with the check's own implementation. Rendered into the
#: ``{FRAMEWORK_CONFORMANCE_STANDARD_CHECK}`` placeholder; empty string when the capability is off.
_CONFORMANCE_CHECK_SECTION = """
## Standard Conformance Check (≤24h)

This team maintains framework adapters, so keeping them conformant to upstream provider docs is a
standard, cadence-gated check — not a task that waits on someone noticing a drift PR. Full semantics:
`references/provider-adapter-refresh.procedure.md` §7.

- **Run it (never fetches, never edits adapter code — it records only a small local ledger):**
  `agentteams --agent-check` (or, in the AgentTeamsModule repo, `python scripts/research_claude_code_docs.py --agent-check`).
  It prints `STATUS=` / `NEEDS_AGENT_ACTION=` / `ROUTE=`; add `--force` to bypass the 24h ledger gate.
  It is most useful in an AgentTeamsModule repository/clone, where the daily pipeline keeps a fresh
  snapshot; in a plain package install with no refreshed snapshot it reports `STATUS=stale-snapshot`
  until one is produced (it degrades safely and never crashes on a read-only install).
- **On `ROUTE=true`:** route the frameworks in `DRIFT_FRAMEWORKS=` / `FETCH_ISSUE_FRAMEWORKS=` to
  `@framework-adapters-expert` to work the §5 Stage-2 edit-site table as a **human-reviewed PR**
  (Constitutional C-4: the check routes a triage, it does not authorize an unattended adapter edit).
- **Cadence:** a per-checkout ledger (`tmp/daily-pipeline/framework-research/agent-check-ledger.json`)
  records `last_checked`; the check runs at most once per 24h and re-routes only when the drift changes.
  An inconclusive/offline run does not advance the clock. `@orchestrator` may consult it during request
  intake as an optional adjunct.
""".strip() + "\n"


def _conformance_check_section(enabled: bool) -> str:
    """Return the opt-in standard-check block, or the empty string when the capability is off."""
    return _CONFORMANCE_CHECK_SECTION if enabled else ""


def build_framework_placeholders(
    output_dir: Path, offline: bool = True, *, conformance_check_enabled: bool = False
) -> dict[str, str]:
    """Return placeholders for the framework-watch reference template.

    Reads the existing snapshot under `tmp/daily-pipeline/framework-research/` (gitignored).
    Set `offline=False` to refresh from the network first (daily-pipeline use).

    Args:
        output_dir: The team's agents output directory (unused for the snapshot, which lives under
            the module tree; kept for signature symmetry with the other placeholder builders).
        offline: When False, refresh the snapshot from the network first (daily-pipeline use).
        conformance_check_enabled: When True, resolve ``FRAMEWORK_CONFORMANCE_STANDARD_CHECK`` to the
            ≤24h standard-check operational block; when False, to the empty string. Gated by the
            caller on whether the team maintains framework adapters (see ``cli/generate.py``).
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
        "FRAMEWORK_CONFORMANCE_STANDARD_CHECK": _conformance_check_section(conformance_check_enabled),
    }


# ---------------------------------------------------------------------------
# Unified provider-freshness view (Phase 3): reconcile the automated watcher
# (latest snapshot: last-fetched + fetch_status + drift) with the manual
# verification register (references/agent-provider-docs.reference.md: when a human
# last opened the doc). One view over all six frameworks so "is provider X fresh?"
# has a single answer, and coverage gaps (a framework we emit but never registered
# for human verification) are visible instead of silent.
# ---------------------------------------------------------------------------

PROVIDER_REGISTER_REL = "references/agent-provider-docs.reference.md"
_REGISTER_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _register_verifications(repo_root: Path) -> dict[str, str | None]:
    """Best-effort parse of the manual register: {source_url: last_verified|None}.

    The register is a Markdown table whose rows carry a URL cell and a
    ``last_verified`` date cell. This maps each registered URL to its date so the
    freshness view can say when a human last verified that provider's doc. Parsing
    is deliberately forgiving (the register is prose-owned, not a schema).
    """
    path = repo_root / PROVIDER_REGISTER_REL
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str | None] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|") or "http" not in line:
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        url = next((c for c in cells if c.startswith("http")), None)
        if not url:
            continue
        date = next((c for c in cells if _REGISTER_DATE_RE.match(c)), None)
        out[url] = date
    return out


def build_provider_freshness_view(repo_root: Path) -> list[dict[str, Any]]:
    """One row per emitted framework, joining the watcher and the manual register.

    Reconciles the two staleness systems the assessment found running in parallel
    (report §3.3): the automated snapshot and the human-verification register.
    Reads only existing state — never fetches. ``in_register=False`` marks a
    coverage gap (a framework we emit/watch but that has no human-verification entry).
    """
    snapshot = _load_snapshot(_snapshot_path(repo_root)) or {}
    frameworks = snapshot.get("frameworks") or {}
    register = _register_verifications(repo_root)
    last_fetched = snapshot.get("generated_on", "")

    rows: list[dict[str, Any]] = []
    for rid, spec in _FORMAT_SPECS.items():
        snap = frameworks.get(rid, {})
        fdiff = snap.get("keys_diff", {})
        rows.append({
            "provider": rid,
            "adapter_id": spec.adapter_id,
            "label": spec.label,
            "source_url": spec.source_url,
            "last_fetched": last_fetched,
            "fetch_status": snap.get("fetch_status", "never"),
            "drift_missing": list(fdiff.get("missing_upstream", [])),
            "drift_new": list(fdiff.get("new_upstream", [])),
            "in_register": spec.source_url in register,
            "register_last_verified": register.get(spec.source_url),
        })
    return rows


def render_provider_freshness_view(rows: list[dict[str, Any]]) -> str:
    """Render :func:`build_provider_freshness_view` rows as a Markdown table."""
    lines = [
        "| Provider | Last fetched | Fetch | Drift (↓missing/↑new) | In register | Last verified |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        drift = []
        if r["drift_missing"]:
            drift.append("↓" + str(len(r["drift_missing"])))
        if r["drift_new"]:
            drift.append("↑" + str(len(r["drift_new"])))
        lines.append(
            f"| {r['provider']} ({r['adapter_id']}) "
            f"| {r['last_fetched'] or '—'} "
            f"| `{r['fetch_status']}` "
            f"| {' '.join(drift) or '—'} "
            f"| {'yes' if r['in_register'] else '**NO (coverage gap)**'} "
            f"| {r['register_last_verified'] or '—'} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-core update path (human-invoked; never run by cron).
# ---------------------------------------------------------------------------

EXPERT_REF_REL = "references/claude-agent-infrastructure-expert.md"
# Superseded shared pointer (references/copilot-agent-infrastructure-expert.md
# — see that file). Kept here, not as a live target, but so a patch proposal
# built from a stale cached snapshot (pre-2026-08-15, before FRAMEWORK_REGISTRY
# split copilot into copilot_vscode/copilot_cli) still resolves.
COPILOT_EXPERT_REF_REL = "references/copilot-agent-infrastructure-expert.md"
# Derived from FRAMEWORK_REGISTRY (CH-05 single-source-of-truth) rather than
# hand-listed: a hand-maintained copy silently fell 4 frameworks behind the
# registry once goose/agents_md/codex/copilot_cli got their own expert_ref
# files (2026-08-15 agent-doc-optimal-structure closeout) — every proposal
# for those frameworks would have been rejected by apply_module_patch's
# allow-list check below despite being legitimate.
ALLOWED_EXPERT_REFS = frozenset(
    {EXPERT_REF_REL, COPILOT_EXPERT_REF_REL}
    | {entry["expert_ref"] for entry in FRAMEWORK_REGISTRY.values()}
)


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

    # Dedup signature: hash over upstream tokens + adapter constants only.
    # Excludes generated_on and the rendered new_text so day-over-day runs
    # with identical observations produce the SAME signature, letting the
    # auto-PR workflow recognise "no real drift" and skip a duplicate PR.
    import hashlib as _hashlib

    dedup_payload = {
        "frameworks": {
            fid: {
                "upstream_tokens": entry.get("upstream_tokens", {}),
                "expected_keys": entry.get("expected_keys", []),
            }
            for fid, entry in frameworks.items()
        },
        "local_adapter": snapshot.get("local_adapter", {}),
        "claude_keys_diff": snapshot.get("keys_diff", {}),
    }
    dedup_hash = _hashlib.sha256(
        json.dumps(dedup_payload, sort_keys=True).encode()
    ).hexdigest()[:12]

    return {
        "schema_version": "1.2",
        "generated_at": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_generated_on": snapshot.get("generated_on", ""),
        "frameworks": list(frameworks.keys()),
        "dedup_hash": dedup_hash,
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
        out = pat.sub("\n" + block.rstrip() + "\n", current, count=1).rstrip() + "\n"
    else:
        out = (current.rstrip() + "\n" + block).rstrip() + "\n"
    # Collapse any run of >=3 blank lines that the splice may have produced
    # between adjacent sections to exactly one blank line. Prevents the
    # blank-line accumulation observed in the daily auto-update PR diff.
    return re.sub(r"\n{3,}", "\n\n", out)


def apply_module_patch(proposal: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Apply a v1 proposal in-place; refuses to run in CI.

    Allow-listed operations only. The caller is responsible for
    git-committing on success and running the relevant tests.
    """
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


# ---------------------------------------------------------------------------
# Standard agent-infrastructure conformance check (no-fetch, ≤24h cadence).
#
# The agent-side complement to ``.github/workflows/framework-auto-update.yml``: where the cron
# *detects and records* upstream drift into an observation-stanza PR, this lets the agent
# infrastructure (``@orchestrator`` → ``@framework-adapters-expert``) decide, at most once per
# window, whether observed drift warrants routing the Stage-2 adapter triage in
# ``references/provider-adapter-refresh.procedure.md``. It NEVER fetches (that is the cron's job)
# and NEVER edits adapter code — detection + routing only (Constitutional C-4).
# ---------------------------------------------------------------------------

#: Agent-conformance-check ledger (gitignored — operator-local, one per checkout). ``last_checked``
#: records when the AGENT infrastructure last ran this triage; it is a DISTINCT axis from the
#: snapshot's ``generated_at`` (when upstream docs were last fetched, restamped daily by the cron),
#: because gating on snapshot age alone could never signal "the agent has not acted."
AGENT_CHECK_LEDGER_REL = "tmp/daily-pipeline/framework-research/agent-check-ledger.json"

#: Default ≤24h cadence for the standard agent conformance check.
AGENT_CHECK_WINDOW_HOURS = 24.0

#: Short retry throttle applied ONLY to inconclusive (stale-snapshot) outcomes, so an offline run
#: does not advance the 24h clock (which would mask real drift for a full day) yet also does not
#: re-attempt on every session.
AGENT_CHECK_ATTEMPT_THROTTLE_HOURS = 1.0

#: fetch_status values that are a real 200-level integrity finding (relocated / stub page), as
#: opposed to the transient skipped/failed states. Mirrors ``provider-adapter-refresh`` §3.
_FETCH_ISSUE_STATES = {"moved", "empty"}


def _agent_check_ledger_path(repo_root: Path) -> Path:
    return repo_root / AGENT_CHECK_LEDGER_REL


def _load_agent_check_ledger(repo_root: Path) -> dict[str, Any]:
    path = _agent_check_ledger_path(repo_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_agent_check_ledger(repo_root: Path, ledger: dict[str, Any]) -> bool:
    """Persist the ledger. Best-effort: a read-only module tree (e.g. an ``agentteams`` package
    installed into read-only ``site-packages``) must not crash the check — it degrades to a
    non-persisted result. Returns True when the ledger was written, False when it could not be."""
    path = _agent_check_ledger_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _hours_since(iso_ts: str | None, now: _dt.datetime) -> float | None:
    """Whole+fractional hours between an ISO-8601 UTC stamp and ``now``; ``None`` if unparseable."""
    if not iso_ts:
        return None
    try:
        dt = _dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return (now - dt).total_seconds() / 3600.0


def run_agent_conformance_check(
    repo_root: Path,
    *,
    window_hours: float = AGENT_CHECK_WINDOW_HOURS,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Run the standard agent-infrastructure conformance check (no-fetch, ≤``window_hours`` cadence).

    Reads the already-produced upstream snapshot and decides whether observed drift or a relocated
    doc warrants routing ``@framework-adapters-expert`` to the procedure's Stage 2. It never fetches
    (``refresh_snapshot`` is the cron's job) and never writes ``agentteams/frameworks/*.py``. The
    decision is recorded in a per-checkout ledger so the check runs at most once per ``window_hours``.

    Classification (see ``provider-adapter-refresh.procedure.md`` §3/§5): a framework is *drifting*
    when its ``keys_diff.missing_upstream`` is non-empty (a token we rely on vanished), and a
    *fetch-issue* when its ``fetch_status`` is ``moved``/``empty`` (relocated/stub doc).
    ``new_upstream`` is excluded — the scan only searches ``expected_keys`` so it is structurally
    always empty. Routing is idempotent per distinct drift: a locally-computed ``drift_signature``
    over the actionable ``missing_upstream`` tokens + fetch-issue set is stored, so the same drift
    is not re-routed every window (deduping against the cron's own PR handling).

    Args:
        repo_root: Repository root holding the snapshot and ledger under ``tmp/daily-pipeline/``.
        window_hours: Skip when the ledger's ``last_checked`` is younger than this. ``0`` forces a
            check regardless of the ledger (used by ``--force`` and the daily cron step).
        now: Injectable current UTC time (for tests); defaults to :func:`_utcnow`.

    Returns:
        A JSON-safe dict with ``status`` (``fresh-skip`` | ``attempt-throttled`` | ``stale-snapshot``
        | ``no-drift`` | ``drift``), ``ran``, ``needs_agent_action``, ``route`` (action needed AND
        not already routed for this ``drift_signature``), ``already_routed``, ``drift_frameworks``,
        ``fetch_issue_frameworks``, ``drift_signature``, ``snapshot_generated_at``,
        ``hours_since_last_check`` and a human ``message``. The payload carries only status codes,
        framework ids and configured source URLs — never fetched page text (Constitutional C-4).
    """
    now = now or _utcnow()
    ledger = _load_agent_check_ledger(repo_root)
    since_check = _hours_since(ledger.get("last_checked"), now)

    # 1) 24h gate — measured against the AGENT ledger, not the snapshot's generated_at.
    if window_hours > 0 and since_check is not None and since_check < window_hours:
        return {
            "status": "fresh-skip",
            "ran": False,
            "needs_agent_action": False,
            "route": False,
            "already_routed": False,
            "drift_frameworks": ledger.get("drift_frameworks", []),
            "fetch_issue_frameworks": ledger.get("fetch_issue_frameworks", []),
            "drift_signature": ledger.get("last_routed_hash"),
            "snapshot_generated_at": ledger.get("snapshot_generated_at"),
            "hours_since_last_check": since_check,
            "message": f"conformance check ran {since_check:.1f}h ago (< {window_hours:.0f}h window); skipping.",
        }

    # 2) Load the snapshot the cron produced — NEVER fetch here (read-only in-session, C-4/adv F4).
    #    Snapshot freshness is judged against a bound INDEPENDENT of the force/ledger window: --force
    #    bypasses the 24h *ledger* gate (re-check now) but must not launder a month-old snapshot into
    #    an authoritative "no-drift" verdict. A window<=0 (force) still requires a snapshot younger
    #    than the default 24h to be usable.
    snapshot = _load_snapshot(_snapshot_path(repo_root))
    snapshot_age = _snapshot_age_hours(snapshot, now=now) if snapshot else None
    snapshot_max_age = window_hours if window_hours > 0 else AGENT_CHECK_WINDOW_HOURS
    snapshot_usable = (
        snapshot is not None
        and snapshot_age is not None
        and snapshot_age <= snapshot_max_age
    )

    # 3) Inconclusive: no fresh snapshot to judge (cron down / offline). Do NOT advance the 24h
    #    clock — that would buy 24h of silence exactly when the safety net is needed (adv F2). Use a
    #    short ``last_attempted`` throttle so we also do not re-attempt on every session.
    if not snapshot_usable:
        since_attempt = _hours_since(ledger.get("last_attempted"), now)
        if (
            window_hours > 0
            and since_attempt is not None
            and since_attempt < AGENT_CHECK_ATTEMPT_THROTTLE_HOURS
        ):
            return {
                "status": "attempt-throttled",
                "ran": False,
                "needs_agent_action": False,
                "route": False,
                "already_routed": False,
                "drift_frameworks": [],
                "fetch_issue_frameworks": [],
                "drift_signature": None,
                "snapshot_generated_at": snapshot.get("generated_at") if snapshot else None,
                "hours_since_last_check": since_check,
                "message": "no usable snapshot; retry throttled (recently attempted).",
            }
        ledger["last_attempted"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_agent_check_ledger(repo_root, ledger)
        return {
            "status": "stale-snapshot",
            "ran": True,
            "needs_agent_action": False,
            "route": False,
            "already_routed": False,
            "drift_frameworks": [],
            "fetch_issue_frameworks": [],
            "drift_signature": None,
            "snapshot_generated_at": snapshot.get("generated_at") if snapshot else None,
            "hours_since_last_check": since_check,
            "message": (
                "no snapshot younger than the window; the 24h clock was NOT advanced. Rely on the "
                "daily cron, or refresh explicitly with `python scripts/research_claude_code_docs.py`."
            ),
        }

    # 4) Fresh snapshot → classify actionable drift.
    frameworks = snapshot.get("frameworks") or {}
    drift_frameworks = sorted(
        fid for fid, entry in frameworks.items()
        if (entry.get("keys_diff") or {}).get("missing_upstream")
    )
    fetch_issue_frameworks = sorted(
        fid for fid, entry in frameworks.items()
        if entry.get("fetch_status") in _FETCH_ISSUE_STATES
    )
    needs_action = bool(drift_frameworks or fetch_issue_frameworks)

    drift_signature: str | None = None
    if needs_action:
        import hashlib as _hashlib

        sig_payload = {
            "drift": {
                fid: sorted((frameworks[fid].get("keys_diff") or {}).get("missing_upstream", []))
                for fid in drift_frameworks
            },
            "fetch_issue": fetch_issue_frameworks,
        }
        drift_signature = _hashlib.sha256(
            json.dumps(sig_payload, sort_keys=True).encode()
        ).hexdigest()[:12]

    already_routed = bool(
        needs_action and drift_signature and ledger.get("last_routed_hash") == drift_signature
    )
    route = bool(needs_action and not already_routed)

    ledger.update({
        "schema": 1,
        "last_checked": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_generated_at": snapshot.get("generated_at", ""),
        "drift_frameworks": drift_frameworks,
        "fetch_issue_frameworks": fetch_issue_frameworks,
    })
    if route:
        ledger["last_routed_hash"] = drift_signature
    _write_agent_check_ledger(repo_root, ledger)

    if not needs_action:
        message = "no upstream drift or relocation observed across the six frameworks; no action."
    elif already_routed:
        message = f"drift already routed for signature {drift_signature}; no new routing needed."
    else:
        affected = sorted(set(drift_frameworks) | set(fetch_issue_frameworks))
        sources = {fid: frameworks[fid].get("source_url", "") for fid in affected}
        message = (
            "ROUTE @framework-adapters-expert → provider-adapter-refresh.procedure.md §5 Stage 2 "
            f"for: {', '.join(affected)}. Sources: {sources}"
        )

    return {
        "status": "drift" if needs_action else "no-drift",
        "ran": True,
        "needs_agent_action": needs_action,
        "route": route,
        "already_routed": already_routed,
        "drift_frameworks": drift_frameworks,
        "fetch_issue_frameworks": fetch_issue_frameworks,
        "drift_signature": drift_signature,
        "snapshot_generated_at": snapshot.get("generated_at", ""),
        "hours_since_last_check": since_check,
        "message": message,
    }


def render_agent_check_report(result: dict[str, Any]) -> str:
    """Render the human + machine-readable lines for a :func:`run_agent_conformance_check` result.

    Shared by ``scripts/research_claude_code_docs.py --agent-check`` (the repo daily-pipeline wrapper)
    and the portable ``agentteams --agent-check`` CLI so both surfaces print identical
    ``STATUS=`` / ``NEEDS_AGENT_ACTION=`` / ``ROUTE=`` lines. The output carries only status codes,
    framework ids and the procedure anchor — never fetched page text (Constitutional C-4).
    """
    lines = [
        result["message"],
        f"STATUS={result['status']}",
        f"NEEDS_AGENT_ACTION={'true' if result['needs_agent_action'] else 'false'}",
        f"ROUTE={'true' if result['route'] else 'false'}",
    ]
    if result["route"]:
        lines += [
            "ROUTE_TARGET=@framework-adapters-expert",
            f"DRIFT_FRAMEWORKS={','.join(result['drift_frameworks'])}",
            f"FETCH_ISSUE_FRAMEWORKS={','.join(result['fetch_issue_frameworks'])}",
            "PROCEDURE=references/provider-adapter-refresh.procedure.md#7-the-agent-infrastructure-standard-check-24h-trigger",
        ]
    return "\n".join(lines)

