"""``api_impact status`` independent of push state, with the ledger read at the range tip (#447 b).

Covers ``status --full`` and the committed ``BASELINE``; the ledger at the tip, not ``HEAD``; batched patch-ids equal
to :func:`api_impact.patch_id`; the git-call budget on a 200-commit history; and the opt-in pre-push check.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import _api_impact_range as rng
from agentteams import api_impact, git_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.version_info < (3, 11),
                                reason="git and Python 3.11+")
_ENV = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
_ENV.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


def _git(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], env=_ENV, capture_output=True, text=True, input=stdin,
                          check=False)


def _commit(repo: Path, msg: str, files: dict[str, str]) -> str:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "--no-verify", "-m", msg).returncode == 0
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def pushed(tmp_path: Path) -> Path:
    """main with a remote; an impactful commit is pushed before any review."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "references").mkdir()
    _commit(repo, "base", {"pkg/__init__.py": "", "references/.keep": ""})
    _commit(repo, "add api", {"pkg/mod.py": "def a():\n    pass\n"})
    _git(repo, "remote", "add", "origin", str(remote))
    assert _git(repo, "push", "-q", "-u", "origin", "main").returncode == 0
    _git(repo, "remote", "set-head", "origin", "main")
    return repo


def _pending(repo: Path, **kw) -> list[str]:
    used, commits = api_impact.status(repo, **kw)
    return [c.subject for c in commits if not c.reviewed]


def test_a_pushed_unreviewed_commit_leaves_the_default_range_but_not_full(pushed: Path):
    assert _pending(pushed) == []  # @{upstream}..HEAD: nothing unpushed
    assert _pending(pushed, full=True) == ["base", "add api"]  # every commit, pushed or not


def test_a_committed_baseline_makes_the_default_range_push_independent(pushed: Path):
    first = _git(pushed, "rev-parse", "HEAD~1").stdout.strip()
    _commit(pushed, "baseline", {"references/api-impact-reviews/BASELINE": f"# reviews start after\n{first}\n"})
    _git(pushed, "push", "-q")
    assert api_impact.resolve_base(pushed) == first
    # The commit that set the baseline is itself impactful: moving it changes what status checks.
    assert _pending(pushed) == ["add api", "baseline"]
    assert _pending(pushed, full=True) == ["add api", "baseline"]


def test_a_baseline_only_commit_can_be_recorded(pushed: Path):
    first = _git(pushed, "rev-parse", "HEAD~1").stdout.strip()
    _commit(pushed, "baseline", {"references/api-impact-reviews/BASELINE": first + "\n"})
    assert api_impact.patch_id(pushed, "HEAD")  # not empty: BASELINE stays in the diff
    api_impact.record(pushed, "reviewed: api and baseline")
    _commit(pushed, "records", {})
    assert _pending(pushed) == []


def test_the_ledger_is_read_at_the_range_tip_not_at_head(pushed: Path):
    api_impact.record(pushed, "reviewed", "HEAD~1..HEAD")
    _commit(pushed, "record", {})
    tip = _git(pushed, "rev-parse", "HEAD").stdout.strip()
    _git(pushed, "checkout", "-q", "HEAD~1")  # HEAD no longer holds the record
    assert _pending(pushed, rev_range=f"HEAD~1..{tip}") == []
    assert _pending(pushed, rev_range="HEAD~1..HEAD") == ["add api"]


def test_batched_patch_ids_equal_the_per_commit_ids(pushed: Path):
    shas = [s for s, _ in rng.commits(pushed, ["HEAD"])]
    ids = rng.patch_ids(pushed, shas, "references/api-impact-reviews")
    for sha in shas:
        assert ids.get(sha, "") == api_impact.patch_id(pushed, sha)


def test_git_calls_stay_bounded_on_a_200_commit_history(tmp_path: Path, monkeypatch):
    repo = tmp_path / "big"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    lines = ["commit refs/heads/main", "mark :1", "committer t <t@x> 1700000000 +0000", "data 4", "base"]
    stream = []
    for i in range(200):
        body = f"def f{i}():\n    pass\n"
        stream += [f"commit refs/heads/main", f"committer t <t@x> {1700000000 + i} +0000", f"data {len(f'c{i}')}",
                   f"c{i}", f"M 100644 inline pkg/m{i}.py", f"data {len(body)}", body]
    assert _git(repo, "fast-import", "--quiet", stdin="\n".join(stream) + "\n").returncode == 0
    _git(repo, "reset", "-q", "--hard", "main")
    calls = 0
    real = subprocess.run

    def counting(cmd, *a, **k):
        nonlocal calls
        if cmd and cmd[0] == "git":
            calls += 1
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", counting)
    _, commits = api_impact.status(repo, full=True)
    assert len(commits) == 200
    assert calls < 200 * 6, calls  # was about 11 per commit before batching


def _push_hook(repo: Path) -> None:
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text("#!/bin/sh\n" + git_hooks._render_pre_push_block(
        str(REPO_ROOT), recorded_python=sys.executable))
    (hooks / "pre-push").chmod(0o755)


def test_pre_push_is_off_until_configured_then_warns_then_blocks(pushed: Path):
    _push_hook(pushed)
    _commit(pushed, "more api", {"pkg/mod.py": "def a(x):\n    pass\n"})
    proc = _git(pushed, "push", "-q")
    assert proc.returncode == 0 and "API impact review" not in proc.stderr
    _commit(pushed, "even more", {"pkg/mod.py": "def a(x, y):\n    pass\n"})
    _git(pushed, "config", rng.PRE_PUSH_KEY, "warn")
    proc = _git(pushed, "push", "-q")
    assert proc.returncode == 0 and "even more" in proc.stderr and "more api" not in proc.stderr
    _commit(pushed, "blocked one", {"pkg/mod.py": "def a():\n    pass\n"})
    _git(pushed, "config", rng.PRE_PUSH_KEY, "block")
    proc = _git(pushed, "push", "-q")
    assert proc.returncode != 0 and "push blocked" in proc.stderr
    api_impact.record(pushed, "reviewed", "origin/main..HEAD")
    _commit(pushed, "record", {})
    assert _git(pushed, "push", "-q").returncode == 0


def test_pre_push_ranges_for_new_updated_unknown_deleted_and_malformed_refs(pushed: Path):
    z = "0" * 40
    known = _git(pushed, "rev-parse", "HEAD~1").stdout.strip()
    got = rng.pre_push_ranges(pushed, f"refs/heads/x {'a' * 40} refs/heads/x {z}\n"
                                      f"refs/heads/y {'b' * 40} refs/heads/y {known}\n"
                                      f"refs/heads/w {'e' * 40} refs/heads/w {'c' * 40}\n"
                                      f"(delete) {z} refs/heads/z {'d' * 40}\n"
                                      f"refs/heads/v --output=/tmp/x refs/heads/v {z}\n", "origin")
    assert got == [["a" * 40, "--not", "--remotes=origin"], [f"{known}..{'b' * 40}"],
                   ["e" * 40, "--not", "--remotes=origin"]]  # c… is not in this clone


def test_a_range_starting_with_a_dash_is_refused(pushed: Path, capsys):
    with pytest.raises(ValueError):
        api_impact.status(pushed, "--output=/tmp/x")
    assert api_impact.main(["--repo", str(pushed), "status", "--range=--output=x"]) == 2
    assert "refused" in capsys.readouterr().err
    assert not (pushed / "x").exists()


def test_a_baseline_line_starting_with_a_dash_is_ignored(pushed: Path):
    _commit(pushed, "bad baseline", {"references/api-impact-reviews/BASELINE": "--output=/tmp/x\n"})
    assert rng.baseline(pushed, "references/api-impact-reviews") is None


def test_status_prints_the_baseline_it_used(pushed: Path, capsys):
    first = _git(pushed, "rev-parse", "HEAD~1").stdout.strip()
    _commit(pushed, "baseline", {"references/api-impact-reviews/BASELINE": first + "\n"})
    api_impact.main(["--repo", str(pushed), "status"])
    assert f"baseline: {first[:12]}" in capsys.readouterr().out


def test_range_tip_forms():
    assert rng.range_tip("A..B") == "B" and rng.range_tip("A...B") == "B" and rng.range_tip("A..") == "HEAD"
    assert rng.range_tip("B ^A") == "B" and rng.range_tip("B --not --remotes=origin") == "B"
    assert rng.range_tip("HEAD") == "HEAD"


def test_status_full_cli_json(pushed: Path, capsys):
    assert api_impact.main(["--repo", str(pushed), "status", "--full", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["range"] == "HEAD" and [c["subject"] for c in out["commits"]] == ["base", "add api"]
