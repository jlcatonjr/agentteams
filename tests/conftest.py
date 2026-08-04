"""Shared pytest fixtures/helpers.

Extracted 2026-07-24 (code-hygiene audit, CH-08): `_resolve_openrouter_key()` plus
its two `skipif` gates had drifted into near-identical copies in
`test_goose_live_delegation.py` and `test_goose_run_resilient.py` (a third,
distinct occurrence — `scripts/goose-openrouter-preflight.py`'s own
`resolve_api_key()` — is production code with a different call shape and stays
separate; this file only dedupes test-only skip-gate logic).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest


def resolve_openrouter_key() -> str:
    """Resolve OPENROUTER_API_KEY by reference: env first, then an env-file.

    Mirrors goose-backend.sh / goose-openrouter-preflight.resolve_api_key — extract
    ONLY the key line from the referenced file; never source the whole file. Returns
    the key string for subprocess use ONLY; callers must report presence, never the
    value.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    env_file = os.environ.get("GOOSE_OPENROUTER_ENV_FILE")
    if not env_file:
        return ""
    try:
        text = Path(env_file).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?OPENROUTER_API_KEY=(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


# Resolve ONCE at import. Only the boolean is exposed to skip markers — the key
# string itself never leaves local scope / the eventual subprocess env.
_KEY_PRESENT = bool(resolve_openrouter_key())

skip_no_goose = pytest.mark.skipif(
    shutil.which("goose") is None,
    reason="goose CLI not installed (live test needs the goose binary)",
)

# MANDATORY missing-key skip: keeps CI/this repo offline-green; a missing key is a
# setup condition (goose exits 1 at config-resolution for delegation, or the
# resilient-runner just can't authenticate), never a live-test failure.
skip_no_openrouter_key = pytest.mark.skipif(
    not _KEY_PRESENT,
    reason=(
        "OPENROUTER_API_KEY not resolvable (env or GOOSE_OPENROUTER_ENV_FILE); "
        "live test needs a configured provider key. Skip-by-default keeps "
        "CI/this repo offline-green."
    ),
)


@pytest.fixture(autouse=True)
def _no_research_cache(monkeypatch):
    """Disable the external-retrieval disk cache for every test.

    Two reasons this is autouse rather than opt-in. First, the cache defaults to
    ``references/research-cache`` under the CWD — a test suite run from the repo root would
    otherwise write real files into the working tree. Second, a cached search result would
    leak between tests: a fake transport asserting "the chain was called twice" silently
    passes on a warm entry from an earlier test without calling anything at all.

    ``tests/test_research_cache.py`` exercises the cache directly and clears this itself.
    """
    monkeypatch.setenv("AGENTTEAMS_RESEARCH_NO_CACHE", "1")


# --- live MODEL-BEHAVIOUR gate (2026-07-30) ---------------------------------
#
# Distinct from the credential gates above, and additive to them. Those answer "can this run at
# all"; this answers "should an ordinary suite run depend on what a remote model decides to do
# this minute".
#
# Measured 2026-07-30 over five runs of test_goose_live_delegation: two passes, three failures,
# and it fails identically on a clean tree with every local change stashed. It asserts that a
# live model emits `delegated` rather than `early-stop` — and this repository's own remediation
# log already records that "OpenRouter upstream backends serving the SAME model id differ
# substantially in tool-calling correctness", which is why scripts/goose-run-resilient.py exists.
# The test is measuring the provider, not this project's code.
#
# Left ungated, it fails roughly two runs in three on any machine with a key, and a suite that is
# usually red teaches people to ignore red. Gated, it stays runnable and honest about what it is.

LIVE_MODEL_TESTS_ENV = "AGENTTEAMS_LIVE_MODEL_TESTS"

skip_no_live_model_tests = pytest.mark.skipif(
    os.environ.get(LIVE_MODEL_TESTS_ENV, "").strip().lower() not in ("1", "true", "yes"),
    reason=(
        f"live model-behaviour test; set {LIVE_MODEL_TESTS_ENV}=1 to run. Measured 2026-07-30: "
        "~1 pass in 3 against a live provider, failing identically on an unmodified tree. It "
        "exercises upstream tool-calling reliability rather than this project's code, so it is "
        "opt-in rather than gating every suite run."
    ),
)


# ---------------------------------------------------------------------------
# The suite must leave tracked artifacts alone
#
# On 2026-08-03 one full-suite run rewrote
# `references/bridges/copilot-vscode-to-claude/bridge-manifest.json` — a fresh `generated_at`
# and 12 changed hashes. It has not recurred across seven later full runs, a per-file bisect
# over ~120 test files, or a deliberately-staled manifest, and three hypotheses were eliminated
# in the process: staleness triggers it, one bad test causes it, it reproduces on demand.
#
# Rather than keep hunting something that will not reproduce, this makes it self-identifying:
# when it next happens, the run names the test. The investigation becomes a detector.
#
# Two lessons from the throwaway version are baked in:
#
#   * The snapshot is DEFENSIVE. `tests/test_output_target_safety.py` does
#     `monkeypatch.setattr(Path, "glob", _boom)` — patched on the class — so an unguarded walk
#     raises during teardown, and that exception aborts monkeypatch's own undo, leaving
#     `Path.glob` broken for the rest of the session. One unguarded call produced 2315
#     cascading errors and two instrumented runs that yielded no data.
#   * Skips are COUNTED. A clean run with 0 skips means something different from a clean run
#     with 400, and if the snapshot silently failed on the very run where a mutation happened,
#     the count is what says so.
#
# Overhead measured before adding: 0.53 ms per snapshot, ~1.8 s across the suite (<0.5%).
# ---------------------------------------------------------------------------

_WATCHED_TREE = Path(__file__).resolve().parents[1] / "references" / "bridges"
_tree_watch = {"prev": None, "culprit": None, "skips": 0, "ok": 0}


def _snapshot_watched_tree():
    """Digest every file under the watched tree, or None if the filesystem is not usable."""
    import hashlib

    try:
        return {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(_WATCHED_TREE.rglob("*"))
            if p.is_file()
        }
    except Exception:
        return None


def pytest_sessionstart(session):  # noqa: D103
    _tree_watch["prev"] = _snapshot_watched_tree()


def pytest_runtest_teardown(item, nextitem):  # noqa: D103
    current = _snapshot_watched_tree()
    if current is None:
        _tree_watch["skips"] += 1
        return
    _tree_watch["ok"] += 1
    previous = _tree_watch["prev"]
    if previous is not None and current != previous and _tree_watch["culprit"] is None:
        changed = sorted(k for k in set(previous) | set(current)
                         if previous.get(k) != current.get(k))
        _tree_watch["culprit"] = (item.nodeid, changed)
    _tree_watch["prev"] = current


def pytest_sessionfinish(session, exitstatus):  # noqa: D103
    if _tree_watch["culprit"] is None:
        return
    nodeid, changed = _tree_watch["culprit"]
    names = "\n    ".join(Path(c).name for c in changed)
    print(
        "\n\n*** TRACKED-ARTIFACT MUTATION ***\n"
        f"    first seen after: {nodeid}\n"
        f"    changed:\n    {names}\n"
        f"    ({_tree_watch['ok']} snapshots taken, {_tree_watch['skips']} skipped)\n"
        "    The suite must not modify tracked artifacts. This is the detector for a mutation\n"
        "    observed once on 2026-08-03 that never reproduced on demand.\n",
        flush=True,
    )
    session.exitstatus = 1
