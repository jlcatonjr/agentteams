"""AI coding bad-habits watch — daily-pipeline catalog stage.

Maintains a living catalog of bad coding habits common across AI agents, mapped
to corrective patterns, drawn ONLY from continuously-maintained upstream
catalogs (CWE Top 25, OWASP Top 10 for LLM Applications, OWASP Web Top 10).

Architecture (matches this repo's generated-vs-tracked split):
- `agentteams/ai_bad_habits.py` (THIS FILE) is the tracked source of truth for
  both the pinned upstream editions and the local BH catalog.
- `references/ai-bad-habits-watch.md` is the rendered, **tracked**, committed
  artifact: catalog + upstream-edition ledger. It is deliberately
  timestamp-free so it changes only on a real edition drift or catalog edit —
  the daily workflow commits it and opens an `awaiting-human` PR only then.
  Agents consume it via `#file:references/ai-bad-habits-watch.md`.

Note: unlike `framework_research`, this stage does NOT write into the gitignored
`.github/agents/` tree — that tree is regenerated from templates and would not
survive a commit. The single tracked artifact is the whole deliverable.

Mirrors the `framework_research` propose/apply/dedup precedent:

    refresh_snapshot()      build the in-memory source+catalog observation.
    render_watch()          render the stable tracked markdown.
    propose_watch_patch()   diff rendered vs on-disk; allow-listed patch + hash.
    apply_watch_patch()     apply in-place; CI-guarded; allow-list = the one file.

Design constraints (this repo's CH rules):
- CH-22: public functions type-check their inputs.
- CH-23: invalid inputs raise; no silent fallback.
- CH-24: `try`/`except` only at the unavoidable network boundary (`_probe`);
  control flow is dictionary/guard driven elsewhere.
"""

from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import socket as _socket
import urllib.error as _urlerror
import urllib.request as _urlrequest
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allow-listed write target (the single tracked artifact)
# ---------------------------------------------------------------------------

WATCH_REL = "references/ai-bad-habits-watch.md"
ALLOWED_PATHS = frozenset({WATCH_REL})

_PROBE_TIMEOUT_SECONDS = 10
# Below this body length a 200 response is almost certainly an interstitial
# (CAPTCHA / Cloudflare / redirect stub) rather than the real catalog page.
# Treat such responses as inconclusive (-> pinned), never as drift, so a
# bot-challenge page cannot manufacture a false "drift-suspected" PR.
_MIN_PROBE_BODY_CHARS = 2000

# ---------------------------------------------------------------------------
# Pinned upstream source registry — maintained, currently-fresh catalogs only.
# `version` is the freshness anchor used offline and for dedup; `freshness_token`
# is a substring whose presence in the fetched page confirms the pinned edition
# is still the live one (a coarse, network-optional drift signal).
# ---------------------------------------------------------------------------

UPSTREAM_SOURCES: tuple[dict[str, str], ...] = (
    {
        "id": "cwe-top-25",
        "label": "MITRE/CISA CWE Top 25 Most Dangerous Software Weaknesses",
        "source_url": "https://cwe.mitre.org/top25/",
        "version": "2025",
        "currency_anchor": "list 2025; page revised 2026-01-29 (inside 6-month window)",
        "freshness_token": "2025",
    },
    {
        "id": "owasp-llm-top-10",
        "label": "OWASP Top 10 for LLM Applications",
        "source_url": "https://genai.owasp.org/llm-top-10/",
        "version": "2025-v2.0",
        "currency_anchor": "v2025 (2026 cycle open); embedded in security.agent.md",
        "freshness_token": "LLM01",
    },
    {
        "id": "owasp-web-top-10",
        "label": "OWASP Top 10 (Web)",
        "source_url": "https://owasp.org/www-project-top-ten/",
        "version": "2021",
        "currency_anchor": "current edition until the next OWASP refresh",
        "freshness_token": "A01",
    },
)

# ---------------------------------------------------------------------------
# Local catalog: BH-NN bad habit -> source id -> corrective pattern.
# `cross_links` cite ONLY rules whose text was verified to match (post conflict
# audit, 2026-06-02). Empty tuple where the catalog entry is itself authoritative.
# ---------------------------------------------------------------------------

LOCAL_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "BH-01", "category": "security", "habit": "Unescaped output enables cross-site scripting",
     "source": "CWE-79", "cross_links": (),
     "fix": "Context-aware output encoding; framework auto-escaping on; Content-Security-Policy header"},
    {"id": "BH-02", "category": "security", "habit": "String-built queries enable SQL injection",
     "source": "CWE-89", "cross_links": (),
     "fix": "Parameterized queries / ORM only; never concatenate untrusted input into a query"},
    {"id": "BH-03", "category": "security", "habit": "State-changing routes lack anti-CSRF protection",
     "source": "CWE-352", "cross_links": (),
     "fix": "Framework CSRF tokens; SameSite cookies"},
    {"id": "BH-04", "category": "security", "habit": "Internal services/data accessed without authorization",
     "source": "CWE-862", "cross_links": (),
     "fix": "Centralized, deny-by-default authorization checks at every entry point"},
    {"id": "BH-05", "category": "llm", "habit": "Retrieved/external content treated as instructions (prompt injection)",
     "source": "LLM01", "cross_links": ("S-5", "S-6"),
     "fix": "Treat retrieved content as inert data; input/output guardrails; least-privilege tools"},
    {"id": "BH-06", "category": "llm", "habit": "Secrets, keys, or PII logged or returned",
     "source": "LLM02", "cross_links": ("S-1", "S-8"),
     "fix": "Output filtering; secret scanning in CI; redaction before any sink"},
    {"id": "BH-07", "category": "llm", "habit": "Hallucinated or unverified dependencies pulled in",
     "source": "LLM03", "cross_links": (),
     "fix": "Pin + lockfile; verify every package against the real registry; SCA scan; block unknown deps"},
    {"id": "BH-08", "category": "llm", "habit": "Raw model output passed unsanitized into exec/DB/render sink",
     "source": "LLM05", "cross_links": ("CH-23", "S-5"),
     "fix": "Validate/sanitize model output before any sink; fail fast on unexpected shapes"},
    {"id": "BH-09", "category": "llm", "habit": "Agent granted over-broad tool/file/network scope (excessive agency)",
     "source": "LLM06", "cross_links": ("S-7",),
     "fix": "Least-privilege tools; allowlists; human-in-the-loop on high-impact actions"},
    {"id": "BH-10", "category": "llm", "habit": "Unbounded loops/recursion/token use (unbounded consumption)",
     "source": "LLM10", "cross_links": (),
     "fix": "Iteration, time, and budget caps with explicit termination conditions"},
    {"id": "BH-11", "category": "hygiene", "habit": "Tutorial-style over-commenting of obvious syntax",
     "source": "hygiene", "cross_links": (),
     "fix": "Comment the why, not the what; no narrating obvious syntax"},
    {"id": "BH-12", "category": "hygiene", "habit": "Stray print / console.log debug statements left in code",
     "source": "hygiene", "cross_links": ("CH-04",),
     "fix": "Use a structured logger, not print; strip debug output before commit"},
    {"id": "BH-13", "category": "hygiene", "habit": "Single-use helper functions adding needless indirection",
     "source": "hygiene", "cross_links": (),
     "fix": "Inline single-use helpers; abstract only on the third repetition (rule of three)"},
    {"id": "BH-14", "category": "hygiene", "habit": "Duplicated code blocks instead of reuse",
     "source": "hygiene", "cross_links": ("CH-08",),
     "fix": "Reuse existing utilities; extract a shared helper at 3 occurrences (DRY)"},
    {"id": "BH-15", "category": "hygiene", "habit": "Tests omitted unless explicitly requested",
     "source": "hygiene", "cross_links": ("CH-21",),
     "fix": "Tests mandatory (happy path + edge/error cases); enforce a coverage gate"},
    {"id": "BH-16", "category": "process", "habit": "Forces a solution on ambiguity instead of asking",
     "source": "process", "cross_links": (),
     "fix": "Plan-first; list assumptions and open questions before coding"},
    {"id": "BH-17", "category": "process", "habit": "'Make it better' refinement loops accumulate flaws",
     "source": "process", "cross_links": (),
     "fix": "Re-scan (SAST/SCA) after every iteration, not just at the end"},
)

_CATEGORY_LABELS = {
    "security": "Security (CWE Top 25)",
    "llm": "LLM/agent (OWASP LLM Top 10)",
    "hygiene": "Code hygiene",
    "process": "Process",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _watch_path(repo_root: Path) -> Path:
    return repo_root / WATCH_REL


def _require_repo_root(repo_root: Path) -> Path:
    """CH-22/CH-23: validate the argument and fail fast on anything unexpected."""
    if not isinstance(repo_root, Path):
        raise TypeError(f"repo_root must be a Path, got {type(repo_root).__name__}")
    if not repo_root.is_dir():
        raise ValueError(f"repo_root is not a directory: {repo_root}")
    return repo_root


def _probe(url: str) -> dict[str, str]:
    """Probe an upstream URL for a freshness signal.

    The network call is the one genuinely-unavoidable external-failure boundary
    in this module, so a narrow `URLError`/timeout catch here is CH-24 compliant
    — it records an expected operational condition, it does not mask a logic bug.
    """
    request = _urlrequest.Request(url, headers={"User-Agent": "agentteams-bad-habits-watch"})
    try:
        with _urlrequest.urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
        return {"status": "ok", "body": body}
    except (_urlerror.URLError, _socket.timeout, TimeoutError) as exc:
        return {"status": "offline", "body": "", "error": str(exc)}


def _source_observation(source: dict[str, str], offline: bool) -> dict[str, str]:
    """Compute the per-source observation row."""
    if offline:
        freshness, fetch_status = "pinned", "offline"
    else:
        probe = _probe(source["source_url"])
        fetch_status = probe["status"]
        if probe["status"] != "ok":
            freshness = "pinned"
        elif len(probe["body"]) < _MIN_PROBE_BODY_CHARS:
            # 200 but too small to be the real page (interstitial / bot
            # challenge / redirect stub) — inconclusive, not drift.
            freshness = "pinned"
            fetch_status = "interstitial"
        elif source["freshness_token"] in probe["body"]:
            freshness = "confirmed"
        else:
            freshness = "drift-suspected"
    return {
        "id": source["id"],
        "label": source["label"],
        "source_url": source["source_url"],
        "pinned_version": source["version"],
        "currency_anchor": source["currency_anchor"],
        "fetch_status": fetch_status,
        "freshness": freshness,
    }


def _catalog_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": entry["id"],
            "category": entry["category"],
            "habit": entry["habit"],
            "source": entry["source"],
            "cross_links": list(entry["cross_links"]),
            "fix": entry["fix"],
        }
        for entry in LOCAL_CATALOG
    ]


def refresh_snapshot(repo_root: Path, offline: bool = True) -> dict[str, Any]:
    """Build the in-memory source+catalog observation.

    Offline (default, CI-safe) reports pinned versions only and never touches the
    network. Online probes each source for a freshness signal. Never raises on a
    network failure — a failed probe degrades to `pinned`.
    """
    _require_repo_root(repo_root)
    if not isinstance(offline, bool):
        raise TypeError(f"offline must be a bool, got {type(offline).__name__}")
    return {
        "offline": offline,
        "sources": [_source_observation(src, offline) for src in UPSTREAM_SOURCES],
        "catalog": _catalog_rows(),
    }


# ---------------------------------------------------------------------------
# Render (stable — no timestamps, so identical state renders identically)
# ---------------------------------------------------------------------------

def _watch_status_cell(freshness: str) -> str:
    # Normalize so offline (pinned) and online-confirmed render identically;
    # only a genuine drift produces a different cell → zero day-over-day noise.
    return "⚠️ review (drift suspected)" if freshness == "drift-suspected" else "tracking"


def render_watch(snapshot: dict[str, Any]) -> str:
    """Render the stable, tracked watch markdown from a snapshot."""
    if not isinstance(snapshot, dict):
        raise TypeError(f"snapshot must be a dict, got {type(snapshot).__name__}")
    if "catalog" not in snapshot or "sources" not in snapshot:
        raise ValueError("snapshot is missing required 'catalog'/'sources' keys")

    lines: list[str] = [
        "<!-- GENERATED FILE — do not hand-edit. Source of truth: agentteams/ai_bad_habits.py -->",
        "# AI Coding Bad-Habits Watch",
        "",
        "> Living catalog of bad coding habits common across AI agents, mapped to",
        "> corrective patterns, drawn ONLY from continuously-maintained upstream",
        "> catalogs. Consumed by `@code-hygiene` (CH-25) and `@security`.",
        ">",
        "> **Source of truth:** `agentteams/ai_bad_habits.py` (edit there, not here).",
        "> **Refreshed by:** `scripts/research_ai_bad_habits.py` / the daily",
        "> `ai-bad-habits-watch` workflow. Intentionally timestamp-free so it",
        "> changes only on a real edition drift or catalog edit.",
        "",
        "## Tracked upstream sources (maintained, currently-fresh)",
        "",
        "| Source | Pinned edition | Currency anchor | Watch status |",
        "|--------|----------------|-----------------|--------------|",
    ]
    for src in snapshot["sources"]:
        lines.append(
            f"| [{src['label']}]({src['source_url']}) | `{src['pinned_version']}` "
            f"| {src['currency_anchor']} | {_watch_status_cell(src['freshness'])} |"
        )
    lines += [
        "",
        "A `⚠️ review` status means the daily probe no longer found the pinned",
        "edition's marker on the live page. A maintainer should confirm the new",
        "edition and bump the pinned `version` in `agentteams/ai_bad_habits.py`.",
        "",
        _render_catalog_body(snapshot).rstrip("\n"),
        "",
        "## How this is checked daily",
        "",
        "`.github/workflows/ai-bad-habits-watch.yml` probes the tracked upstream",
        "editions every day and opens an `awaiting-human` PR when an edition drifts",
        "from its pinned version or this catalog changes. The PR is reviewed by the",
        "operator (no auto-merge) — guidance changes require human review.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_catalog_body(snapshot: dict[str, Any]) -> str:
    """Render ONLY the bad-habit catalog (BH tables + single-source note).

    No upstream-edition ledger and no daily-watch footer — those are exclusive
    to the repo-root daily-watch artifact (`render_watch`). This catalog-only
    body is what each consumer receives via `build_catalog_placeholders`, so the
    per-consumer reference and the root watch share ONE catalog rendering
    (CH-05 / CH-14 single-source-of-truth) without duplicating the ledger.
    """
    if not isinstance(snapshot, dict) or "catalog" not in snapshot:
        raise ValueError("snapshot is missing required 'catalog' key")
    lines: list[str] = [
        "OWASP LLM Top 10 risk *names* are NOT restated here — they live in the",
        "`@security` threat-intelligence fence. This catalog references the `LLMxx`",
        "ids only (CH-05 / CH-14 single-source-of-truth).",
        "",
        "## Bad-habit catalog (BH-NN → corrective pattern)",
        "",
    ]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot["catalog"]:
        by_category.setdefault(row["category"], []).append(row)
    for category in ("security", "llm", "hygiene", "process"):
        rows = by_category.get(category, [])
        if not rows:
            continue
        lines += [
            f"### {_CATEGORY_LABELS.get(category, category)}",
            "",
            "| BH | Bad habit | Source | Verified cross-link | Corrective pattern |",
            "|----|-----------|--------|---------------------|--------------------|",
        ]
        for row in rows:
            xlink = ", ".join(row["cross_links"]) if row["cross_links"] else "—"
            lines.append(
                f"| {row['id']} | {row['habit']} | `{row['source']}` | {xlink} | {row['fix']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def build_catalog_placeholders() -> dict[str, str]:
    """Per-consumer placeholder map for the bad-habits catalog reference.

    Mirrors `security_refs.build_security_placeholders`: returns the resolved
    placeholder(s) that `build_team.py` injects into `auto_resolved_placeholders`
    so the consumer reference template renders with no manual completion. The
    catalog is static (`LOCAL_CATALOG`), so this is offline and network-free.
    """
    snapshot = {"catalog": _catalog_rows()}
    return {"AI_BAD_HABITS_CATALOG": _render_catalog_body(snapshot).rstrip("\n")}


def content_hash(snapshot: dict[str, Any]) -> str:
    """Date-independent signature == hash of the rendered (stable) watch body."""
    return _hashlib.sha256(render_watch(snapshot).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Propose / apply (mirrors framework_research)
# ---------------------------------------------------------------------------

def propose_watch_patch(repo_root: Path, offline: bool = True) -> dict[str, Any]:
    """Diff the freshly-rendered watch against disk; return an allow-listed patch.

    `dedup_hash` is the rendered content hash; identical day-over-day state yields
    the same signature so the workflow skips a duplicate PR.
    """
    _require_repo_root(repo_root)
    snapshot = refresh_snapshot(repo_root, offline=offline)
    new_text = render_watch(snapshot)
    dedup_hash = content_hash(snapshot)

    target = _watch_path(repo_root)
    old_text = target.read_text(encoding="utf-8") if target.exists() else ""
    if new_text == old_text:
        return {"changes": [], "reason": "no drift", "dedup_hash": dedup_hash}

    return {
        "schema_version": "1.0",
        "dedup_hash": dedup_hash,
        "changes": [{
            "path": WATCH_REL,
            "operation": "replace_file",
            "old_text": old_text,
            "new_text": new_text,
        }],
    }


def write_watch(repo_root: Path, offline: bool = True) -> Path:
    """Render and write the tracked watch artifact in-place. Returns the path."""
    _require_repo_root(repo_root)
    snapshot = refresh_snapshot(repo_root, offline=offline)
    target = _watch_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_watch(snapshot), encoding="utf-8")
    return target


def apply_watch_patch(proposal: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Apply an allow-listed proposal in-place; refuses to run in CI unguarded."""
    import os

    if not isinstance(proposal, dict):
        raise TypeError(f"proposal must be a dict, got {type(proposal).__name__}")
    _require_repo_root(repo_root)

    if os.environ.get("CI") and not os.environ.get("AGENTTEAMS_ALLOW_CI_APPLY"):
        raise RuntimeError(
            "apply_watch_patch refuses to run in CI without an explicit "
            "AGENTTEAMS_ALLOW_CI_APPLY=1 marker. The auto-PR workflow at "
            ".github/workflows/ai-bad-habits-watch.yml sets this guard "
            "intentionally; never set it elsewhere."
        )

    changes = proposal.get("changes", [])
    if not changes:
        return {"applied": [], "reason": "nothing to apply"}

    applied: list[str] = []
    for change in changes:
        path = change.get("path", "")
        operation = change.get("operation", "")
        if path not in ALLOWED_PATHS:
            raise RuntimeError(f"refusing change outside allow-list: {path}")
        if operation != "replace_file":
            raise RuntimeError(f"refusing unsupported operation: {operation}")
        target = repo_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change["new_text"], encoding="utf-8")
        applied.append(path)

    return {"applied": applied}
