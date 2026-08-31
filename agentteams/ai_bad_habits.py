"""AI coding bad-habits catalog.

A curated catalog of **code-quality, correctness, and process** habits common
across AI agents, each mapped to a corrective pattern.

Scope / allocation of focus:
- This catalog deliberately does NOT cover security-class AI habits
  (insecure-by-default code, injection, secrets exposure, excessive agency,
  supply-chain, unbounded consumption). Those are owned by `@security` (CWE /
  OWASP LLM & Web taxonomies + the S-rules). Keeping them here duplicated
  `@security`'s domain, so they were removed (2026-06-02 refocus).
- Consumed by `@code-hygiene` (rule CH-25). The two correctness entries
  (hallucinated dependencies, unvalidated output) keep ONLY their code-quality
  angle; their security angle (slopsquatting, untrusted-sink injection) is
  `@security`'s.

Architecture (matches this repo's generated-vs-tracked split):
- `agentteams/ai_bad_habits.py` (THIS FILE) is the tracked source of truth for
  the catalog.
- `references/ai-bad-habits-watch.md` is the rendered, **tracked**, committed
  artifact. It is curated and version-controlled (not an upstream watch); it
  changes only when the catalog here changes. Agents consume the per-consumer
  copy via `#file:references/ai-bad-habits-watch.reference.md`.

Mirrors the `framework_research` propose/apply/dedup precedent:

    refresh_snapshot()      build the in-memory catalog snapshot.
    render_watch()          render the stable tracked markdown.
    propose_watch_patch()   diff rendered vs on-disk; allow-listed patch + hash.
    apply_watch_patch()     apply in-place; CI-guarded; allow-list = the one file.

Design constraints (this repo's CH rules):
- CH-22: public functions type-check their inputs.
- CH-23: invalid inputs raise; no silent fallback.
- CH-24: no broad try/except; control flow is dictionary/guard driven.
"""

from __future__ import annotations

import hashlib as _hashlib
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allow-listed write target (the single tracked artifact)
# ---------------------------------------------------------------------------

WATCH_REL = "references/ai-bad-habits-watch.md"
ALLOWED_PATHS = frozenset({WATCH_REL})

# ---------------------------------------------------------------------------
# Curated catalog: BH-NN bad habit -> corrective pattern.
# Code-quality / correctness / process habits ONLY. Security-class AI habits are
# @security's domain and are intentionally absent (see module docstring).
# `cross_links` cite existing CH rules whose text was verified to match.
# ---------------------------------------------------------------------------

LOCAL_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "BH-01", "category": "hygiene",
     "habit": "Tutorial-style over-commenting of obvious syntax",
     "cross_links": (),
     "fix": "Comment the why, not the what; no narrating obvious syntax"},
    {"id": "BH-02", "category": "hygiene",
     "habit": "Stray print / console.log debug statements left in code",
     "cross_links": ("CH-04",),
     "fix": "Use a structured logger, not print; strip debug output before commit"},
    {"id": "BH-03", "category": "hygiene",
     "habit": "Single-use helper functions adding needless indirection",
     "cross_links": (),
     "fix": "Inline single-use helpers; abstract only on the third repetition (rule of three)"},
    {"id": "BH-04", "category": "hygiene",
     "habit": "Duplicated code blocks instead of reuse",
     "cross_links": ("CH-08",),
     "fix": "Reuse existing utilities; extract a shared helper at 3 occurrences (DRY)"},
    {"id": "BH-05", "category": "hygiene",
     "habit": "Tests omitted unless explicitly requested",
     "cross_links": ("CH-21",),
     "fix": "Tests mandatory (happy path + edge/error cases); enforce a coverage gate"},
    {"id": "BH-06", "category": "correctness",
     "habit": "Hallucinated or unresolvable dependencies / imports",
     "cross_links": (),
     "fix": "Verify every import and package resolves against the real registry; "
            "pin + lockfile. (The supply-chain / slopsquatting SECURITY angle is @security's.)"},
    {"id": "BH-07", "category": "correctness",
     "habit": "Model output forwarded without shape-validation",
     "cross_links": ("CH-23",),
     "fix": "Validate/shape-check AI output and fail fast on unexpected shapes (CH-23). "
            "(The untrusted-sink injection angle is @security's.)"},
    {"id": "BH-08", "category": "process",
     "habit": "Forces a solution on ambiguity instead of asking",
     "cross_links": (),
     "fix": "Plan-first; list assumptions and open questions before coding"},
    {"id": "BH-09", "category": "process",
     "habit": "'Make it better' refinement loops accumulate flaws",
     "cross_links": (),
     "fix": "Re-review and re-test after every iteration, not just at the end"},
    {"id": "BH-10", "category": "hygiene",
     "habit": "Wholesale file rewrites / reformatting untouched regions when a scoped edit suffices",
     "cross_links": ("CH-28",),
     "fix": "Make the smallest change that satisfies the task; never reformat or "
            "restructure unrelated lines in the same edit (CH-28). Required guards/"
            "cleanups (CH-10/CH-22/CH-23/CH-24) and sanctioned refactors still apply."},
    {"id": "BH-11", "category": "correctness",
     "habit": "A backstage/utility LLM call silently inherits a user-facing, dynamically-selectable "
              "model (or other generation parameter) choice, without accounting for that model's "
              "behavioral differences",
     "cross_links": ("CH-23",),
     "fix": "Give utility calls (routing, extraction, summarization, auditing) their own default, "
            "independent of a user-facing picker — or explicitly account for the selected model's "
            "behavior (e.g. a reasoning model consuming its fixed token budget on internal thinking "
            "before any requested output). An ambiguous empty/degraded result from such a call must "
            "not be treated as a confident negative (see BH-07/CH-23)."},
)

_CATEGORY_LABELS = {
    "hygiene": "Code hygiene",
    "correctness": "AI-specific correctness",
    "process": "Process",
}
_CATEGORY_ORDER = ("hygiene", "correctness", "process")


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


def _catalog_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": entry["id"],
            "category": entry["category"],
            "habit": entry["habit"],
            "cross_links": list(entry["cross_links"]),
            "fix": entry["fix"],
        }
        for entry in LOCAL_CATALOG
    ]


def refresh_snapshot(repo_root: Path) -> dict[str, Any]:
    """Build the in-memory catalog snapshot (curated; no network, no upstream)."""
    _require_repo_root(repo_root)
    return {"catalog": _catalog_rows()}


# ---------------------------------------------------------------------------
# Render (stable — no timestamps, so identical state renders identically)
# ---------------------------------------------------------------------------

def render_watch(snapshot: dict[str, Any]) -> str:
    """Render the stable, tracked catalog markdown (root artifact)."""
    if not isinstance(snapshot, dict):
        raise TypeError(f"snapshot must be a dict, got {type(snapshot).__name__}")
    if "catalog" not in snapshot:
        raise ValueError("snapshot is missing required 'catalog' key")

    lines: list[str] = [
        "<!-- GENERATED FILE — do not hand-edit. Source of truth: agentteams/ai_bad_habits.py -->",
        "# AI Coding Bad-Habits Catalog",
        "",
        "> Curated catalog of **code-quality, correctness, and process** habits",
        "> common across AI agents, mapped to corrective patterns. Consumed by",
        "> `@code-hygiene` (rule CH-25).",
        ">",
        "> **Security-class AI habits are NOT here — they are owned by `@security`**",
        "> (CWE / OWASP LLM & Web taxonomies + S-rules). This catalog does not",
        "> duplicate them.",
        ">",
        "> **Source of truth:** `agentteams/ai_bad_habits.py` (edit there, not here).",
        "> Version-controlled and curated — regenerated by `build_team` and",
        "> `scripts/research_ai_bad_habits.py`. It is not an upstream watch.",
        "",
        _render_catalog_body(snapshot).rstrip("\n"),
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_catalog_body(snapshot: dict[str, Any]) -> str:
    """Render ONLY the bad-habit catalog tables.

    This is what each consumer receives via `build_catalog_placeholders`, so the
    per-consumer reference and the root artifact share ONE catalog rendering
    (CH-05 / CH-14 single-source-of-truth).
    """
    if not isinstance(snapshot, dict) or "catalog" not in snapshot:
        raise ValueError("snapshot is missing required 'catalog' key")
    lines: list[str] = [
        "**Scope:** code-quality, correctness, and process habits specific to AI",
        "agents. Security-class habits (injection, secrets, excessive agency,",
        "supply chain, unbounded consumption) are owned by `@security` and are",
        "deliberately not catalogued here.",
        "",
        "## Bad-habit catalog (BH-NN → corrective pattern)",
        "",
    ]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot["catalog"]:
        by_category.setdefault(row["category"], []).append(row)
    for category in _CATEGORY_ORDER:
        rows = by_category.get(category, [])
        if not rows:
            continue
        lines += [
            f"### {_CATEGORY_LABELS.get(category, category)}",
            "",
            "| BH | Bad habit | Cross-link | Corrective pattern |",
            "|----|-----------|------------|--------------------|",
        ]
        for row in rows:
            xlink = ", ".join(row["cross_links"]) if row["cross_links"] else "—"
            lines.append(
                f"| {row['id']} | {row['habit']} | {xlink} | {row['fix']} |"
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
    """Date-independent signature == hash of the rendered (stable) catalog body."""
    return _hashlib.sha256(render_watch(snapshot).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Propose / apply (mirrors framework_research)
# ---------------------------------------------------------------------------

def propose_watch_patch(repo_root: Path) -> dict[str, Any]:
    """Diff the freshly-rendered catalog against disk; return an allow-listed patch.

    `dedup_hash` is the rendered content hash; identical state yields the same
    signature so the workflow skips a duplicate PR.
    """
    _require_repo_root(repo_root)
    snapshot = refresh_snapshot(repo_root)
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


def write_watch(repo_root: Path) -> Path:
    """Render and write the tracked catalog artifact in-place. Returns the path."""
    _require_repo_root(repo_root)
    snapshot = refresh_snapshot(repo_root)
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
