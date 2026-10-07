"""Which agentteams code ran: recorded in build-log.json and printed by --version (CA-033 follow-up)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import source_provenance as sp

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clear_cache():
    sp._cached.cache_clear()
    yield
    sp._cached.cache_clear()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "at"
    (repo / "agentteams").mkdir(parents=True)
    (repo / "agentteams" / "__init__.py").write_text("")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo


def test_checkout_records_commit_branch_and_dirty(tmp_path):
    repo = _repo(tmp_path)
    clean = sp._from_checkout(repo / "agentteams")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert clean == {"kind": "checkout", "commit": head, "branch": "main", "dirty": False}
    (repo / "untracked.csv").write_text("x")          # untracked residue counts as dirty
    assert sp._from_checkout(repo / "agentteams")["dirty"] is True


def test_not_a_checkout_falls_through(tmp_path):
    (tmp_path / "agentteams").mkdir()
    assert sp._from_checkout(tmp_path / "agentteams") is None


@pytest.mark.parametrize("direct_url, expected", [
    ({"url": "https://github.com/x/agentteams", "vcs_info": {"vcs": "git", "commit_id": "abc1234def"}},
     {"kind": "vcs-pin", "commit": "abc1234def", "branch": None, "dirty": False}),
    ({"url": "file:///somewhere/agentteams", "dir_info": {}},
     {"kind": "local-snapshot", "commit": None, "branch": None, "dirty": None}),
    (None, {"kind": "package", "commit": None, "branch": None, "dirty": None}),
])
def test_installed_kinds(monkeypatch, direct_url, expected):
    class _Dist:
        def read_text(self, name):
            return json.dumps(direct_url) if direct_url is not None else None

    import importlib.metadata as md
    monkeypatch.setattr(md, "distribution", lambda name: _Dist())
    assert sp._from_distribution() == expected


def test_describe():
    assert sp.describe({"kind": "checkout", "commit": "729a0add38f16e2d", "branch": "main", "dirty": True}) == \
        "(checkout 729a0add38f1 main, dirty)"
    assert sp.describe({"kind": "local-snapshot", "commit": None, "branch": None, "dirty": None}) == "(local-snapshot)"


def test_version_flag_reports_the_source():
    out = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--version"], capture_output=True,
                         text=True, cwd="/", timeout=60)
    import re
    assert out.returncode == 0
    assert re.match(r"^\S+ \S+ \((checkout|vcs-pin|local-snapshot|package|unknown)\b[^)]*\)$", out.stdout.strip())


def test_build_log_records_the_source_without_paths(tmp_path):
    brief = REPO / "examples" / "software-project" / "brief.json"
    out = tmp_path / ".claude" / "agents"
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(brief),
                           "--project", str(tmp_path), "--framework", "claude", "--output", str(out),
                           "--no-scan", "--yes"], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    log = json.loads((out / "references" / "build-log.json").read_text())
    source = log["agentteams_source"]
    assert set(source) == {"kind", "commit", "branch", "dirty"}
    assert str(REPO) not in json.dumps(source) and "/Users/" not in json.dumps(source)



@pytest.mark.parametrize("bad", ['"not a dict"', '{"vcs_info": "x"}', '{"vcs_info": {"commit_id": "not-hex!"}}'])
def test_malformed_direct_url_never_raises(monkeypatch, bad):
    class _Dist:
        def read_text(self, name):
            return bad

    import importlib.metadata as md
    monkeypatch.setattr(md, "distribution", lambda name: _Dist())
    assert sp._from_distribution()["commit"] is None


def test_any_surprise_reports_unknown(monkeypatch):
    monkeypatch.setattr(sp, "_from_checkout", lambda p: (_ for _ in ()).throw(PermissionError("nope")))
    assert sp.source_provenance()["kind"] == "unknown"
