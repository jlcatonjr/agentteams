"""1.3.2: the --merge-pr route is closed — a pinned brief reader, a required team id, the loaded-module guard and the
default release-check wiring, plus one end-to-end merge through the entry point with the real guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import branch_model as bm
from agentteams.cli import dev_merge as dm
from agentteams.cli import merge_pr_main

_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("present", [(0,), (1,), (2,), (0, 1), (1, 2), (0, 2), (0, 1, 2)])
def test_the_merge_route_brief_order_matches_fleet_discovery(tmp_path, present):
    from agentteams.fleet import _resolve_descriptor

    for i in present:
        p = tmp_path / bm.BRIEF_CANDIDATES[i]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"git_workflow": {"branch_model": "integration", "integration_branch": f"b{i}"}}))
    assert bm.integration_branch_for_merge(tmp_path) == f"b{min(present)}"
    assert _resolve_descriptor(tmp_path) == tmp_path / bm.BRIEF_CANDIDATES[min(present)]


def test_the_merge_route_brief_refuses_a_symlink_and_reads_none_as_trunk(tmp_path):
    assert bm.integration_branch_for_merge(tmp_path) is None
    real = tmp_path / "elsewhere.json"
    real.write_text("{}")
    (tmp_path / ".agentteams").mkdir()
    (tmp_path / ".agentteams" / "brief.json").symlink_to(real)
    with pytest.raises(ValueError, match="symbolic link"):
        bm.integration_branch_for_merge(tmp_path)


def test_the_loaded_module_guard_names_unpinned_modules_in_this_process():
    import agentteams.render  # noqa: F401 - the render pipeline is not pinned

    assert "not integrity-pinned" in (dm.unpinned_loaded() or "")


def test_the_default_release_check_fetches_the_signed_repo_and_the_pull_ref(monkeypatch):
    from agentteams.cli import dev_merge_release as dmr

    seen = {}
    monkeypatch.setattr(dmr, "release_problem", lambda **kw: seen.update(kw) or None)
    assert dm.release_check("acme/consumer", "dev", 7, "a" * 40, ["src/app.py"]) is None
    assert seen == {"repo_url": "https://github.com/acme/consumer.git", "base_branch": "dev",
                    "head_ref": "refs/pull/7/head", "head_sha": "a" * 40, "files": ["src/app.py"]}


def test_the_entry_requires_a_team_id(capsys):
    with pytest.raises(SystemExit):
        merge_pr_main.build_parser().parse_args(["--merge-pr", "7"])
    assert "--team-id" in capsys.readouterr().err


def test_the_entry_refuses_when_the_current_directory_is_first_on_sys_path(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    assert merge_pr_main.main(["--merge-pr", "7", "--team-id", "t"]) == 1
    assert "current directory is first on sys.path" in capsys.readouterr().err


_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from tests.test_dev_merge import _FakeGh
from agentteams.cli import dev_merge as dm
from agentteams.cli import merge_pr_main
root, team = sys.argv[2], sys.argv[3]
gh = _FakeGh()
from agentteams.cli import decision_log as dl
from tests._sandbox_gate_helpers import _stub_sig
# The dev-merge tests' decision-row signature stub (conftest `stub_ed25519`); the authorization file is really signed.
dl.verify_by_scheme = lambda scheme, values, signature, public_pem=None: signature == _stub_sig(values)
dm.run_gh = gh                     # no network: the canned gh of the dev-merge tests
dm.release_check = lambda *a: None  # the release checks need the remote; they have their own tests
rc = merge_pr_main.main(["--merge-pr", "7", "--project", root, "--output", team, "--team-id", "consumer"])
print(json.dumps({"rc": rc, "merged": [c for c in gh.calls if c[:2] == ["pr", "merge"]],
                  "unpinned": dm.unpinned_loaded()}))
"""


def test_an_end_to_end_merge_through_the_entry_point_with_the_real_guard(tmp_path):
    from tests.test_dev_merge import _doc, _pass, _project, _write

    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    (root / ".agentteams" / "brief.json").write_text(
        json.dumps({"git_workflow": {"branch_model": "integration", "integration_branch": "dev"}}))
    proc = subprocess.run([sys.executable, "-I", "-c", _PROBE, str(_ROOT), str(root), str(team)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["unpinned"] is None, out["unpinned"]
    assert out["rc"] == 0, proc.stderr
    assert out["merged"] == [["pr", "merge", "7", "-R", "acme/consumer", "--merge", "--match-head-commit", "a" * 40]]
