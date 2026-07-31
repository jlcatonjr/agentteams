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
