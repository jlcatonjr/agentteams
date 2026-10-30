"""agentteams.vulnscan.daily (T2-D, P2(b)): checkout freshness and the ``fixed upstream`` label, read-only.

Real git against a local bare remote; nothing is fetched by the code under test (the test itself fetches to make
the clone's ``origin/HEAD`` newer than its working tree).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import daily, effectiveness
from agentteams.vulnscan.report import Finding, Report

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required")


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hardened git writes the executor's git config under the state directory."""
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))


def _git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                           "init.defaultBranch=main", *args], cwd=cwd, env=env, check=True, capture_output=True,
                          text=True).stdout


def _stale_clone(tmp: Path, *, fixed_upstream: bool = True) -> Path:
    """A clone at PyJWT==2.3.0 whose origin/HEAD (fetched, not pulled) pins 2.15.0 (or still 2.3.0)."""
    bare = tmp / "origin.git"
    _git(tmp, "init", "-q", "--bare", str(bare))
    work = tmp / "upstream"
    _git(tmp, "clone", "-q", str(bare), str(work))
    (work / "requirements.txt").write_text("PyJWT==2.3.0\nrequests==2.31.0\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "one")
    _git(work, "push", "-q", "origin", "HEAD:main")
    root = tmp / "repos"
    root.mkdir()
    clone = root / "app"
    _git(root, "clone", "-q", str(bare), str(clone))
    (work / "requirements.txt").write_text(("PyJWT==2.15.0" if fixed_upstream else "PyJWT==2.3.0") +
                                           "\nrequests==2.31.0\n# two\n")
    _git(work, "commit", "-q", "-am", "two")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(clone, "fetch", "-q", "origin")   # origin/main moves; the working tree stays behind
    return root


def _report() -> Report:
    rep = Report(target="r")
    for name, ver in (("PyJWT", "2.3.0"), ("requests", "2.31.0")):
        rep.findings.append(Finding(kind="package", target="app", name=name, version=ver, ecosystem="PyPI",
                                    locations=["app/requirements.txt"]))
    return rep


def test_a_stale_working_tree_with_a_fixed_origin_gets_the_label(tmp_path: Path) -> None:
    root = _stale_clone(tmp_path)
    rep = _report()
    rows = daily._freshness(root.resolve(), rep)
    assert rows == [{"repo": "app", "branch": "main", "behind": 1, "fetch_age_h": rows[0]["fetch_age_h"],
                     "fixed_upstream": 1}]
    assert rows[0]["fetch_age_h"] is not None and rows[0]["fetch_age_h"] < 1
    jwt, requests = rep.findings
    assert jwt.fixed_upstream and not requests.fixed_upstream   # requests==2.31.0 is still pinned upstream
    md = rep.markdown()
    assert "; fixed upstream (checkout behind) |" in md and md.count("fixed upstream (checkout behind)") == 1
    section = daily._freshness_section(rows)
    assert section.startswith("## Checkout freshness") and "| `app` | `main` | 1 |" in section


def test_still_vulnerable_upstream_gets_no_label(tmp_path: Path) -> None:
    root = _stale_clone(tmp_path, fixed_upstream=False)
    rep = _report()
    assert daily._freshness(root.resolve(), rep)[0]["fixed_upstream"] == 0
    assert not any(f.fixed_upstream for f in rep.findings)


def test_no_origin_head_means_no_label_and_no_error(tmp_path: Path) -> None:
    root = _stale_clone(tmp_path)
    _git(root / "app", "remote", "set-head", "origin", "--delete")
    rep = _report()
    rows = daily._freshness(root.resolve(), rep)
    assert rows[0]["behind"] is None and rows[0]["fixed_upstream"] == 0
    assert not any(f.fixed_upstream for f in rep.findings)
    assert "no origin/HEAD" in daily._freshness_section(rows)


def test_nested_and_third_party_clones_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _stale_clone(tmp_path)
    monkeypatch.setattr(daily, "foreign_clone", lambda repo: "someone else's")
    rep = _report()
    assert daily._freshness(root.resolve(), rep) == [] and not any(f.fixed_upstream for f in rep.findings)
    monkeypatch.undo()
    outer = root / "app"   # app nested inside a scanned repository: a clone of it under app/vendor
    _git(outer, "clone", "-q", str(tmp_path / "origin.git"), "vendor/lib")
    rep = Report(target="r", findings=[Finding(kind="package", target="app/vendor/lib", name="PyJWT", version="2.3.0",
                                               ecosystem="PyPI", locations=["app/vendor/lib/requirements.txt"])])
    assert [r["repo"] for r in daily._freshness(root.resolve(), rep)] == []


def test_effectiveness_counts_the_label_on_its_own_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(effectiveness, "staged_without_pr", lambda: 0)
    monkeypatch.setattr(effectiveness.retry_guard, "held_count", lambda: 0)
    previous = _report()
    now = _report()
    now.findings[0].fixed_upstream = True
    md, _ = effectiveness.section(now, previous, stats=effectiveness.PrStats())
    unlabelled, _ = effectiveness.section(_report(), previous, stats=effectiveness.PrStats())
    assert "| Findings already fixed upstream (checkout behind; not counted as fixed) | 1 |" in md
    assert "| Findings fixed since the previous run | 0 |" in md   # the label is not a fix
    other = [ln for ln in md.splitlines() if "already fixed upstream" not in ln]
    assert other == [ln for ln in unlabelled.splitlines() if "already fixed upstream" not in ln]


def test_a_failed_git_query_is_shown_not_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import checkout_freshness

    def hung(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr(checkout_freshness, "_repo_freshness", hung)
    rep = _report()
    rows = checkout_freshness._freshness(tmp_path, rep)
    assert rows == [{"repo": "app", "branch": "?", "behind": None, "fetch_age_h": None, "fixed_upstream": 0,
                     "error": "git query failed (TimeoutExpired)"}]
    assert not any(f.fixed_upstream for f in rep.findings)
    assert "| git query failed (TimeoutExpired) |" in checkout_freshness._freshness_section(rows)
