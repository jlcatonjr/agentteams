"""test_docs_freshness_watch.py — the documentation-freshness trigger and its reliability contract.

Two things are tested here and they are different in kind.

**The condition** is ordinary logic: source moved inside the window, documentation did not.
Tested against synthetic git repositories with a pinned clock, so every cell of the truth
table is reachable.

**The reliability contract** is the part the user asked for explicitly: *a failed trigger
must not prevent future activation of the trigger*. That property is not established by any
single assertion — it comes from the verdict being a pure function of git history and the
clock. What the tests can do, and do below, is pin the invariants that property depends on,
so a later edit that quietly introduces state fails here rather than in production six
months later when a run is dropped and nothing ever fires again.

The three prohibitions (also stated in `references/documentation-refresh.procedure.md`):

1. No `concurrency:` with `cancel-in-progress` on the watcher workflow.
2. No state file written by one run and read back by a later one.
3. Reporting steps stay `if: always()`.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "docs_freshness_watch.py"
WATCHER = REPO_ROOT / ".github" / "workflows" / "docs-freshness-watch.yml"
WATCHDOG = REPO_ROOT / ".github" / "workflows" / "docs-freshness-watchdog.yml"

HOUR = 3600


def _load():
    spec = importlib.util.spec_from_file_location("docs_freshness_watch", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dfw():
    return _load()


# ---------------------------------------------------------------------------
# Synthetic repository helper
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str, epoch: int | None = None) -> None:
    env = None
    if epoch is not None:
        stamp = f"{epoch} +0000"
        env = {
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": str(root),
        }
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with `git` configured and an initial commit far in the past."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    return root


def _commit(root: Path, rel: str, text: str, *, epoch: int) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    _git(root, "add", rel, epoch=epoch)
    _git(root, "commit", "-q", "-m", f"touch {rel}", epoch=epoch)


#: Pinned clock for `evaluate()` tests, which accept `now_epoch`.
NOW = 1_800_000_000

#: The CLI reads the real clock — it has no `now_epoch` seam, and adding one purely for
#: tests would put a test-only branch in the production path. CLI tests therefore build
#: commits relative to *now*, which is also a truer end-to-end exercise.
REAL_NOW = int(__import__("time").time())


def _yaml_body(path: Path) -> str:
    """Workflow text with `#` comment lines stripped.

    The prohibition tests assert on YAML *directives*. A comment that names a forbidden
    directive in order to explain why it is forbidden is documentation, not a violation —
    and matching raw text would make those workflows undocumentable.
    """
    return "\n".join(
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# The condition — all four cells of the truth table
# ---------------------------------------------------------------------------


def test_fires_when_source_moved_and_docs_did_not(dfw, repo):
    """The whole point: source today, docs three days ago."""
    _commit(repo, "README.md", "docs", epoch=NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - 2 * HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is True
    assert v.docs_age_hours == pytest.approx(72, abs=0.1)
    assert v.src_age_hours == pytest.approx(2, abs=0.1)
    assert "did not" in v.reason


def test_does_not_fire_when_docs_also_moved(dfw, repo):
    """Docs inside the window — nothing owed."""
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - 5 * HOUR)
    _commit(repo, "README.md", "docs", epoch=NOW - 3 * HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is False
    assert "documentation moved" in v.reason


def test_does_not_fire_when_source_is_also_quiet(dfw, repo):
    """Stale docs alone are not the trigger. A dormant repo must stay silent."""
    _commit(repo, "README.md", "docs", epoch=NOW - 200 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - 100 * HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is False
    assert "also quiet" in v.reason


def test_does_not_fire_when_nothing_moved_at_all(dfw, repo):
    _commit(repo, "README.md", "docs", epoch=NOW - 500 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - 500 * HOUR)
    assert dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW).fires is False


def test_boundary_just_inside_the_window_does_not_fire(dfw, repo):
    """23h59m of doc age is inside a 24h window. Off-by-one here means daily false alarms."""
    _commit(repo, "README.md", "docs", epoch=NOW - (24 * HOUR - 60))
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - HOUR)
    assert dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW).fires is False


def test_boundary_just_outside_the_window_fires(dfw, repo):
    _commit(repo, "README.md", "docs", epoch=NOW - (24 * HOUR + 60))
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - HOUR)
    assert dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW).fires is True


def test_window_is_configurable(dfw, repo):
    """A 48h window forgives what a 24h window flags."""
    _commit(repo, "README.md", "docs", epoch=NOW - 30 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - HOUR)
    assert dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW, window_hours=24).fires is True
    assert dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW, window_hours=48).fires is False


# ---------------------------------------------------------------------------
# Set derivation — derived from the tree, never declared
# ---------------------------------------------------------------------------


def test_a_new_doc_is_watched_without_being_registered(dfw, repo):
    """The anti-stale-list property: a guide added today is watched today.

    If the doc set were a hand-written list, this file would be invisible until someone
    remembered to add it — the failure mode one level up.
    """
    _commit(repo, "docs_src/brand-new-guide.md", "new", epoch=NOW - 2 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is False, "the brand-new guide should count as fresh documentation"
    assert v.doc_count == 1


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs_src/guide.md",
        "docs_src/api-reference/emit.md",
        "SECURITY.md",
        "agentteams.1",
        "mkdocs.yml",
    ],
)
def test_documentation_paths_classify_as_docs(dfw, path):
    assert dfw._is_doc(path) is True
    assert dfw._is_source(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "docs/cli-reference/index.html",   # build output
        "tmp/by-week/2026-W32/x.plan.md",  # working artifact
        "workSummaries/daily/2026-08-06.md",
        "references/plans/some-audit.report.md",
        "examples/software-project/expected/orchestrator.agent.md",
        ".github/agents/orchestrator.agent.md",
        "agentteams/templates/universal/security.template.md",
    ],
)
def test_excluded_markdown_is_not_documentation(dfw, path):
    assert dfw._is_doc(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "agentteams/emit.py",
        "agentteams/cli/parser.py",
        "pyproject.toml",
        "schemas/x.json",
        ".github/workflows/ci.yml",
        # Product artifacts: not docs this watcher judges, but changing one can genuinely
        # oblige a doc update, so they must raise the obligation rather than go inert.
        "agentteams/templates/universal/security.template.md",
        "examples/software-project/brief.json",
        "tests/test_emit.py",
    ],
)
def test_source_paths_classify_as_source(dfw, path):
    assert dfw._is_source(path) is True
    assert dfw._is_doc(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "CHANGELOG.md",
        "references/x-2026-06-22.conflict-audit.md",
        "references/x-2026-06-22.adversarial.md",
        "references/plans/some.plan.md",
        "workSummaries/daily/2026-08-06.md",
        "docs/index.html",
    ],
)
def test_inert_paths_are_neither_doc_nor_source(dfw, path):
    """Neither raises the obligation nor discharges it.

    CHANGELOG.md is the one that matters. `changelog-link.yml` forces a changelog edit on
    nearly every code PR, so counting it as documentation would let it act as a freshness
    alibi for every other doc — measured at 62% of firings suppressed over a 90-day
    backtest. Counting it as *source* would be just as wrong: a changelog-only commit is
    not a code change owing a doc update.
    """
    assert dfw._is_doc(path) is False, f"{path} must not count as documentation"
    assert dfw._is_source(path) is False, f"{path} must not count as source"


def test_changelog_does_not_discharge_the_obligation(dfw, repo):
    """End-to-end form of the same defect: a changelog-only commit must not silence it."""
    _commit(repo, "README.md", "docs", epoch=NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - 3 * HOUR)
    _commit(repo, "CHANGELOG.md", "entry", epoch=NOW - 1 * HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is True, "a changelog edit must not reset docs_age"
    assert v.docs_age_hours == pytest.approx(72, abs=0.1)


def test_changelog_alone_does_not_raise_the_obligation(dfw, repo):
    """The symmetric case: changelog churn with no code change must stay silent."""
    _commit(repo, "README.md", "docs", epoch=NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - 100 * HOUR)
    _commit(repo, "CHANGELOG.md", "entry", epoch=NOW - 1 * HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is False
    assert "also quiet" in v.reason


def test_build_output_counts_as_neither(dfw):
    """`docs/` is a build artifact. Counting it as source would fire on every rebuild;
    counting it as docs would let a rebuild reset docs_age and mask real staleness."""
    p = "docs/index.html"
    assert dfw._is_doc(p) is False
    assert dfw._is_source(p) is False


# ---------------------------------------------------------------------------
# Reliability — statelessness and the indeterminate branch
# ---------------------------------------------------------------------------


def test_repeated_evaluation_is_idempotent(dfw, repo):
    """No run may influence the next. Ten evaluations, one answer.

    This is the property the whole reliability claim rests on: if evaluation had a side
    effect on its own inputs, a failed run could leave the trigger latched off.
    """
    _commit(repo, "README.md", "docs", epoch=NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - HOUR)
    verdicts = [dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW) for _ in range(10)]
    assert all(v.fires for v in verdicts)
    assert len({v.reason for v in verdicts}) == 1


def test_evaluation_writes_nothing_to_the_repo(dfw, repo):
    """A watcher that writes state is a watcher that can poison its own future runs."""
    _commit(repo, "README.md", "docs", epoch=NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=NOW - HOUR)
    before = {p.relative_to(repo) for p in repo.rglob("*") if p.is_file()}
    dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    after = {p.relative_to(repo) for p in repo.rglob("*") if p.is_file()}
    assert before == after, f"evaluation created files: {sorted(after - before)}"


def test_non_repo_is_indeterminate_not_clean(dfw, tmp_path):
    """The load-bearing distinction: 'could not tell' must never render as 'fine'."""
    v = dfw.evaluate(root=tmp_path, ref="HEAD", now_epoch=NOW)
    assert v.fires is False
    assert v.error, "a failed git query must populate `error`"
    assert "indeterminate" in v.reason
    assert "INDETERMINATE" in dfw.render_markdown(v)
    assert "not a pass" in dfw.render_markdown(v)


def test_empty_derived_set_is_indeterminate(dfw, repo):
    """A derivation regression must not read as a clean tree.

    Without this branch, an exclusion-prefix typo that emptied both sets would evaluate
    cleanly to False forever — a silent, permanent, green failure.
    """
    _commit(repo, "tmp/only-excluded-content.md", "x", epoch=NOW - HOUR)
    v = dfw.evaluate(root=repo, ref="HEAD", now_epoch=NOW)
    assert v.fires is False
    assert v.error == "empty derived set"
    assert "derivation regressed" in v.reason


def test_evaluate_never_raises_on_a_hostile_root(dfw, tmp_path):
    """A watcher that raises is a watcher that stops watching."""
    missing = tmp_path / "does" / "not" / "exist"
    v = dfw.evaluate(root=missing, ref="HEAD", now_epoch=NOW)
    assert v.fires is False and v.error


def test_committer_date_is_used_not_author_date(dfw, repo):
    """A rebased commit carries an old author date; 'when did it land' is the question.

    Using %at here would understate src_age and suppress a legitimate fire.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "%ct" in src
    assert "--format=%at" not in src


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def test_cli_exits_zero_when_firing(dfw, repo, monkeypatch):
    """Advisory, not a gate — the same contract as check_session_obligations.py."""
    monkeypatch.setattr(dfw, "ROOT", repo)
    _commit(repo, "README.md", "docs", epoch=REAL_NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=REAL_NOW - HOUR)
    assert dfw.main([]) == 0


def test_check_flag_exits_two_when_firing(dfw, repo, monkeypatch):
    monkeypatch.setattr(dfw, "ROOT", repo)
    _commit(repo, "README.md", "docs", epoch=REAL_NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=REAL_NOW - HOUR)
    assert dfw.main(["--check"]) == 2


def test_check_flag_exits_zero_when_clear(dfw, repo, monkeypatch):
    monkeypatch.setattr(dfw, "ROOT", repo)
    _commit(repo, "agentteams/mod.py", "code", epoch=REAL_NOW - 5 * HOUR)
    _commit(repo, "README.md", "docs", epoch=REAL_NOW - HOUR)
    assert dfw.main(["--check"]) == 0


def test_check_flag_exits_zero_when_indeterminate(dfw, tmp_path, monkeypatch):
    """--check signals 'stale', never 'broken'. Conflating them would let a runner
    without git masquerade as a staleness finding."""
    monkeypatch.setattr(dfw, "ROOT", tmp_path)
    assert dfw.main(["--check"]) == 0


def test_out_write_failure_does_not_change_the_exit_code(dfw, repo, monkeypatch, capsys):
    """A cosmetic side-channel must never turn a good evaluation into a crash."""
    monkeypatch.setattr(dfw, "ROOT", repo)
    _commit(repo, "agentteams/mod.py", "code", epoch=REAL_NOW - 5 * HOUR)
    _commit(repo, "README.md", "docs", epoch=REAL_NOW - HOUR)
    # A path whose parent is an existing *file* cannot be created.
    blocker = repo / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert dfw.main(["--out", str(blocker / "sub" / "body.md")]) == 0
    assert "could not write" in capsys.readouterr().err


def test_json_output_is_parseable(dfw, repo, monkeypatch, capsys):
    import json

    monkeypatch.setattr(dfw, "ROOT", repo)
    _commit(repo, "README.md", "docs", epoch=REAL_NOW - 72 * HOUR)
    _commit(repo, "agentteams/mod.py", "code", epoch=REAL_NOW - HOUR)
    dfw.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["fires"] is True
    assert set(payload) >= {"fires", "reason", "window_hours", "docs_age_hours", "error"}


# ---------------------------------------------------------------------------
# The three prohibitions — asserted against the workflow files
# ---------------------------------------------------------------------------


def test_watcher_workflow_exists():
    assert WATCHER.is_file()
    assert WATCHDOG.is_file()


def test_prohibition_1_no_cancel_in_progress():
    """A cancelled run leaves no verdict. On a busy push day that is most runs."""
    assert "cancel-in-progress" not in _yaml_body(WATCHER)


def test_prohibition_2_no_state_file_is_read_back():
    """Nothing the watcher writes may be read by a later run.

    verdict.json / body.md live only inside one job's workspace and are gone when it ends;
    what this forbids is a *cache* or *committed* artifact, either of which would let one
    bad run latch the trigger off.
    """
    text = _yaml_body(WATCHER)
    assert "actions/cache" not in text
    assert "git commit" not in text
    assert "git push" not in text


def test_prohibition_3_reporting_runs_even_when_the_detector_fails():
    """A failure that reports nothing is indistinguishable from a pass."""
    text = _yaml_body(WATCHER)
    assert text.count("if: always()") >= 2


def test_watcher_has_all_three_trigger_channels():
    """push and cron are both load-bearing: push cannot see the window roll over with no
    new commit, and cron alone delays the push case by up to its period."""
    text = _yaml_body(WATCHER)
    assert "push:" in text
    assert "schedule:" in text
    assert "workflow_dispatch:" in text


def test_watcher_uses_full_history():
    """A shallow clone resolves every path to the graft commit, making both ages ~0 and
    the condition permanently false — a silent total failure with a green run."""
    assert "fetch-depth: 0" in _yaml_body(WATCHER)


def test_watchdog_is_independent_of_the_watcher():
    """Separate file, separate cron, no `needs:` — otherwise one failure takes out both."""
    watcher = _yaml_body(WATCHER)
    watchdog = _yaml_body(WATCHDOG)
    assert "needs:" not in watchdog
    watcher_cron = [ln.strip() for ln in watcher.splitlines() if "cron:" in ln]
    watchdog_cron = [ln.strip() for ln in watchdog.splitlines() if "cron:" in ln]
    assert watcher_cron and watchdog_cron
    assert set(watcher_cron).isdisjoint(watchdog_cron), "crons must not coincide"


def test_daily_driver_provides_a_third_execution_path():
    """If the watcher's own workflow is disabled or deleted, the daily driver still runs
    the detector from a different workflow file on a different cron."""
    driver = (REPO_ROOT / "scripts" / "run_daily_bridge_maintenance.sh").read_text(encoding="utf-8")
    assert "docs_freshness_watch.py" in driver
    assert "run_noncritical" in driver


# ---------------------------------------------------------------------------
# Documentation of the procedure itself
# ---------------------------------------------------------------------------


def test_the_procedure_document_exists_and_is_reachable():
    """The trigger points at the procedure by path; a rename that broke it would leave
    every alert telling a reader to open a file that does not exist."""
    proc = REPO_ROOT / "references" / "documentation-refresh.procedure.md"
    assert proc.is_file()
    for referrer in (SCRIPT, WATCHER, WATCHDOG):
        assert "documentation-refresh.procedure.md" in referrer.read_text(encoding="utf-8")


def test_the_procedure_covers_every_surface_named_in_the_audit():
    """The procedure must systematically cover each document the audit named — that was
    the requirement, and a procedure silently missing one is the audit's finding again."""
    text = (REPO_ROOT / "references" / "documentation-refresh.procedure.md").read_text(
        encoding="utf-8"
    )
    for surface in (
        "docs_src/cli-reference.md",
        "README.md",
        "docs_src/api-reference/",
        "mkdocs.yml",
        ".claude/CLAUDE.md",
        "agentteams.1",
        "CHANGELOG.md",
        "SECURITY.md",
        "STABILITY.md",
    ):
        assert surface in text, f"procedure does not cover {surface}"
