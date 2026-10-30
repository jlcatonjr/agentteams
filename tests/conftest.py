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
import sys
from pathlib import Path

import pytest

# --- import THIS tree's agentteams (research-hardening #13, 2026-10-30) -------
#
# The CI venv carries an editable install of the main checkout, so a suite run from a git worktree
# silently imported ``~/githubrepositories/agentteams/agentteams`` and tested code it was not
# looking at. Put this tree first on ``sys.path`` before anything imports ``agentteams``, and first
# on ``PYTHONPATH`` so subprocess tests (``sys.executable -m agentteams...``, ``build_team.py``)
# import the same tree. ``pytest_sessionstart`` refuses the run if a plugin imported another copy
# before this file loaded.
#
# Opt-out: ``AGENTTEAMS_TEST_INSTALLED=1`` skips both the path insertion and the guard, so a run that
# deliberately tests an installed wheel (tox, nox, a release smoke test) is not silently redirected
# to the source tree.
_ROOT = Path(__file__).resolve().parents[1]
_TEST_INSTALLED = os.environ.get("AGENTTEAMS_TEST_INSTALLED", "") == "1"
if not _TEST_INSTALLED and (_ROOT / "agentteams").is_dir():
    _root_str = str(_ROOT)
    if not sys.path or sys.path[0] != _root_str:
        sys.path.insert(0, _root_str)
    _pythonpath = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if not _pythonpath or _pythonpath[0] != _root_str:
        os.environ["PYTHONPATH"] = os.pathsep.join([_root_str, *_pythonpath])


def _assert_agentteams_from_this_tree() -> None:
    """Exit the session if ``agentteams`` was imported from a different checkout.

    Raises:
        pytest.exit.Exception: ``agentteams.__file__`` lies outside this tree (exit code 4).
    """
    import agentteams

    location = Path(agentteams.__file__).resolve()
    if not location.is_relative_to(_ROOT):
        pytest.exit(
            f"agentteams was imported from {location.parent}, not this tree ({_ROOT}); "
            f"run with PYTHONPATH={_ROOT} so the suite tests the code in front of you.",
            returncode=4,
        )


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


@pytest.fixture(autouse=True)
def _vulnscan_subprocesses_on_host(monkeypatch):
    """Run vulnscan's git, npm and server-kit subprocesses on the host in tests, EXPLICITLY.

    In production ``confine.subprocesses`` defaults to true: executor ops and apply-worktree staging run them in
    the tools container and refuse when Docker is unavailable. The existing executor / daily / fix tests drive
    real host git against temporary repositories, and CI has no tools image, so here the *default* is off. A
    config that sets ``confine.subprocesses`` itself still wins, which is how
    ``tests/test_vulnscan_subproc_confine.py`` exercises the confined path (with Docker mocked).
    """
    from agentteams.vulnscan import subproc_confine

    monkeypatch.setattr(subproc_confine, "ENABLED_BY_DEFAULT", False)


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


def _report_stale_integrity_manifest(session: pytest.Session) -> None:
    """Print ``integrity.stale_summary`` once, from the controller process, when the manifest is stale.

    INTEGRITY-MANIFEST-STALE-PER-PIN-EDIT: an edit to a pinned module stales
    ``references/enforcement-integrity.json``, and tests asserting ``--check``'s exit code then fail in
    a way that reads like a regression. Naming the stale pins and the re-record command up front says
    why. Informational only: it never changes the session's outcome. xdist workers skip it (they
    carry ``workerinput``) so a parallel run prints it once, not once per worker.

    Args:
        session: The pytest session.
    """
    if hasattr(session.config, "workerinput"):
        return
    import sys

    from agentteams import integrity

    try:
        summary = integrity.stale_summary(Path(__file__).resolve().parents[1])
    except (RuntimeError, OSError) as exc:
        summary = f"[INTEGRITY] could not check the enforcement manifest: {exc}"
    if not summary:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(summary, yellow=True)
    else:
        print(summary, file=sys.stderr, flush=True)


def pytest_sessionstart(session):  # noqa: D103
    if not _TEST_INSTALLED:
        _assert_agentteams_from_this_tree()
    else:
        # Opt-out active: say which copy is under test, so a stray export is visible.
        import agentteams
        print(f"AGENTTEAMS_TEST_INSTALLED=1: testing agentteams from {agentteams.__file__}",
              file=sys.stderr, flush=True)
    _tree_watch["prev"] = _snapshot_watched_tree()
    _report_stale_integrity_manifest(session)


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


@pytest.fixture
def signing_preapproved(monkeypatch):
    """Pre-approve the #9 confirm gate and the #10 install-location refusal for tests that exercise
    the signing pipeline itself. Opt-in per module; ``tests/test_sign_confirm.py`` tests the real
    gate and location check without this fixture."""
    from agentteams.cli import operator_signing, signer_location

    monkeypatch.setattr(operator_signing, "_confirm", lambda digest, confirm: True)
    monkeypatch.setattr(signer_location, "verify_install_location", lambda *a, **k: [])


@pytest.fixture(scope="session", autouse=True)
def _goose_switch_machinery_under_test():
    """Keep the Goose write-policy machinery under test while the switch is refused on Goose.

    agentteams ships ``write_policy.GOOSE_SWITCH_REFUSED = True``; the suite sets ``AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH=1`` (biographies pilot 1, G1: Goose delegates inherit
    the orchestrator's extensions, so per-recipe narrowing isn't enforced). Session-scoped so module-scoped render
    fixtures see it too. ``tests/test_write_policy_instructions.py`` asserts the shipped default refuses.
    """
    import os

    from agentteams import write_policy

    previous = os.environ.get(write_policy.GOOSE_OVERRIDE_ENV)
    os.environ[write_policy.GOOSE_OVERRIDE_ENV] = "1"  # inherited by the build_team.py subprocesses too
    yield
    if previous is None:
        os.environ.pop(write_policy.GOOSE_OVERRIDE_ENV, None)
    else:
        os.environ[write_policy.GOOSE_OVERRIDE_ENV] = previous
