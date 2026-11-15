"""A render from a commit no remote has is visible (IM item 29, G-19-3).

A render from the shared editable checkout on a local branch recorded a commit no CI could fetch: Tracers and
MarketForge got pins that existed only on a local branch, and ``source_provenance`` recorded it and did nothing more.
Now a ``checkout`` source records ``on_remote``, ``--update`` warns, and ``--doctor`` reports it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams import doctor, source_provenance as sp

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


@pytest.fixture(autouse=True)
def _clear_cache():
    sp._cached.cache_clear()
    yield
    sp._cached.cache_clear()


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _checkout_with_remote(tmp_path: Path) -> Path:
    """A checkout whose first commit is pushed to a bare remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "at"
    (repo / "agentteams").mkdir(parents=True)
    (repo / "agentteams" / "__init__.py").write_text("")
    _run(repo.parent, "init", "-q", "-b", "main", str(repo))
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "pushed")
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-q", "origin", "main")
    return repo


def test_a_pushed_commit_is_on_a_remote_and_a_local_only_one_is_not(tmp_path):
    repo = _checkout_with_remote(tmp_path)
    assert sp._from_checkout(repo / "agentteams")["on_remote"] is True
    _run(repo, "checkout", "-q", "-b", "feat/local-only")
    (repo / "agentteams" / "x.py").write_text("x = 1\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "local only")
    prov = sp._from_checkout(repo / "agentteams")
    assert prov["on_remote"] is False and prov["branch"] == "feat/local-only"


def test_a_checkout_with_no_remote_at_all_is_not_on_a_remote(tmp_path):
    repo = tmp_path / "at"
    (repo / "agentteams").mkdir(parents=True)
    (repo / "agentteams" / "__init__.py").write_text("")
    _run(repo.parent, "init", "-q", "-b", "main", str(repo))
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "c")
    assert sp._from_checkout(repo / "agentteams")["on_remote"] is False


LOCAL = {"kind": "checkout", "commit": "09dcf0d5" + "0" * 32, "branch": "feat/feature-audit-effectiveness",
         "dirty": False, "on_remote": False}


def test_the_warning_names_the_commit_and_branch_and_only_fires_for_an_unpushed_checkout():
    line = sp.unpushed_line(LOCAL)
    assert line and "09dcf0d50000" in line and "feat/feature-audit-effectiveness" in line and "no remote" in line
    for prov in ({**LOCAL, "on_remote": True}, {**LOCAL, "on_remote": None},
                 {"kind": "vcs-pin", "commit": "abc1234", "on_remote": None}, {"kind": "package"}):
        assert sp.unpushed_line(prov) is None, prov
    assert sp.describe(LOCAL).endswith(", not on a remote)")


def test_warn_if_unpushed_prints_and_returns_the_record(capsys):
    assert sp.warn_if_unpushed(LOCAL) is LOCAL
    assert "[WARN] agentteams commit 09dcf0d50000" in capsys.readouterr().err
    sp.warn_if_unpushed({**LOCAL, "on_remote": True})
    assert capsys.readouterr().err == ""


def test_update_warns_and_records_on_remote(tmp_path, monkeypatch, capsys):
    from tests._fresh_intel import seed_offline_intel

    import build_team

    monkeypatch.setattr(sp, "source_provenance", lambda: dict(LOCAL))   # build_team imports it at call time
    brief = Path(__file__).resolve().parents[1] / "examples" / "software-project" / "brief.json"
    out = tmp_path / ".claude" / "agents"
    rc = build_team.main(["--description", str(brief), "--project", str(tmp_path), "--framework", "claude",
                          "--output", str(out), "--no-scan", "--yes", *seed_offline_intel(out, "claude")])
    assert rc == 0
    assert "is on no remote-tracking branch" in capsys.readouterr().err
    source = json.loads((out / "references" / "build-log.json").read_text(encoding="utf-8"))["agentteams_source"]
    assert source["on_remote"] is False


def _project_with_log(tmp_path: Path, source: dict) -> Path:
    refs = tmp_path / "proj" / ".github" / "agents" / "references"
    refs.mkdir(parents=True)
    (refs / "build-log.json").write_text(json.dumps({"agentteams_source": source}), encoding="utf-8")
    return tmp_path / "proj"


def test_doctor_warns_on_a_recorded_unpushed_commit(tmp_path):
    lines, failed = doctor._agentteams_source(_project_with_log(tmp_path, LOCAL))
    assert failed == 0 and len(lines) == 1
    assert ".github/agents/references/build-log.json" in lines[0] and "WARN" in lines[0]


def test_doctor_checks_an_older_log_against_the_running_checkout(tmp_path, monkeypatch):
    # Logs from before item 29 have no on_remote: the Tracers/MarketForge pins are caught by asking the checkout.
    older = {k: v for k, v in LOCAL.items() if k != "on_remote"}
    monkeypatch.setattr(sp, "commit_on_remote", lambda commit: False)
    assert doctor._agentteams_source(_project_with_log(tmp_path, older))[0]
    monkeypatch.setattr(sp, "commit_on_remote", lambda commit: None)   # the commit is unknown here: no claim
    assert doctor._agentteams_source(_project_with_log(tmp_path / "b", older))[0] == []


def test_doctor_is_quiet_for_a_pushed_or_non_checkout_source(tmp_path):
    for i, source in enumerate(({**LOCAL, "on_remote": True}, {"kind": "vcs-pin", "commit": "abc1234"})):
        assert doctor._agentteams_source(_project_with_log(tmp_path / str(i), source)) == ([], 0)


def test_commit_on_remote_is_none_for_an_unknown_or_malformed_commit():
    assert sp.commit_on_remote("not-a-sha") is None
    assert sp.commit_on_remote("f" * 40) is None
