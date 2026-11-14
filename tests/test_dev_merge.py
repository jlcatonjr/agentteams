"""The operator-signed integration-branch merge authorization and ``agentteams --merge-pr`` (operator rulings,
2026-11-14: signed dev-merge exception; first-party scope; no expiry; checks green, merge commit, @security PASS).

The file is really Ed25519-signed; decision rows (lineage parents, per-merge PASS) verify through the stub. ``gh`` is
replaced by a fake that answers from a dict, so each merge condition is exercised on its own.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams.cli import dev_merge as dm
from agentteams.cli import exception_registry as er
from tests._sandbox_gate_helpers import _keypair, _log, _row

pytest.importorskip("cryptography")

_REPO = "acme/consumer"
_TEAM = "consumer"
_BRANCH = "dev"
_ROOT_ROW = "integration-merge-root"
_SHA = "a" * 40


@pytest.fixture(autouse=True)
def _decisions_verify(stub_ed25519):
    """Decision rows verify by the stub; the authorization file itself is really Ed25519-signed."""


def _project(tmp_path: Path, *, governed: bool = True, key_id: str = "op-2026") -> tuple[Path, Path, str]:
    root = tmp_path / "proj"
    team = root / ".github" / "agents"
    (team / "references" / "authorized-verify-keys").mkdir(parents=True)
    private, public = _keypair()
    (team / "references" / "authorized-verify-keys" / f"{key_id}.pub.pem").write_text(public)
    (team / "references" / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": governed}))
    _log(team, _row(_ROOT_ROW))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", f"https://github.com/{_REPO}.git"], check=True)
    return root, team, private


def _doc(private: str, *, branch: str = _BRANCH, workspaces=None, sequence: int = 1,
         derives_from: str = _ROOT_ROW, checks=("test",)) -> dict:
    from agentteams.cli import signed_ledger as sl

    doc = {"version": dm.FORMAT_VERSION, "scope": dm.SCOPE, "branch": branch, "required_checks": list(checks),
           "workspaces": workspaces or [{"repo": f"repo:{_REPO}", "team": f"team:{_TEAM}"}],
           "purpose": dm.PURPOSE, "sequence": sequence, "key_id": "op-2026", "derives_from": derives_from}
    doc["signature"] = sl.ed25519_sign(private, dm.signed_values(doc))
    return doc


def _write(root: Path, doc: dict) -> Path:
    path = root / dm.AUTH_FILE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return path


def _load(root: Path, team: Path, **kw) -> dm.AuthResult:
    return dm.load_authorization(root, team, team_id=_TEAM, origin=kw.pop("origin", _REPO), **kw)


# --- the authorization file ---------------------------------------------------------------------------------------

def test_a_signed_file_verifies_for_its_workspace(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    result = _load(root, team)
    assert (result.problem, result.repo, result.branch) == (None, _REPO, _BRANCH)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(branch="staging"),
    lambda d: d.update(required_checks=["lint"]),
    lambda d: d["required_checks"].append("lint"),
    lambda d: d.update(sequence=2),
    lambda d: d["workspaces"].append({"repo": "repo:acme/other", "team": "team:other"}),
    lambda d: d.update(signature=("0" if d["signature"][0] != "0" else "1") + d["signature"][1:]),
])
def test_a_tampered_file_does_not_verify(tmp_path, mutate) -> None:
    root, team, private = _project(tmp_path)
    doc = _doc(private)
    mutate(doc)
    _write(root, doc)
    assert _load(root, team).problem


@pytest.mark.parametrize("branch", ["main", "master", "release/1.2", "HEAD", "Main"])
def test_no_default_or_release_branch_can_be_signed(tmp_path, branch) -> None:
    root, team, private = _project(tmp_path)
    with pytest.raises(dm.DevMergeError):
        dm.validate({**_doc(private), "branch": branch})


def test_the_signed_repository_must_match_the_checkout(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    assert "not the signed repository" in _load(root, team, origin="acme/elsewhere").problem
    other = dm.load_authorization(root, team, team_id="someone-else", origin=_REPO)
    assert "not listed" in other.problem


@pytest.mark.parametrize("action", [dm.FAMILY, f"{dm.ACTION_FAMILY}:{_REPO}:pr-7-{_SHA}"])
def test_a_halt_on_the_family_or_any_merge_ends_it(tmp_path, action) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _log(team, _row(action, verdict="HALT", stamp="2026-11-14"))
    assert "HALT" in _load(root, team).problem


def test_a_halt_on_the_root_decision_ends_it(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _log(team, _row(_ROOT_ROW, verdict="HALT", stamp="2026-11-14"))
    assert _load(root, team).problem


def test_a_rolled_back_sequence_is_refused(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, sequence=3))
    assert _load(root, team, consume=True).problem is None
    assert er.load_registry(team)[dm.HIGH_WATER_KEY] == 3
    _write(root, _doc(private, sequence=2))
    assert "rollback refused" in _load(root, team).problem


def test_the_file_is_a_trust_root_and_its_payload_is_domain_separated() -> None:
    from agentteams.cli.governance_targets import target_is_governance_root

    assert target_is_governance_root(dm.AUTH_FILE_REL)
    assert not target_is_governance_root(dm.merge_action_id(_REPO, 3, _SHA))
    assert dm.PURPOSE.startswith("agentteams-") and dm.PURPOSE != "agentteams-toolchain-group-v1"


def test_signing_refuses_inside_an_agent_session(tmp_path, monkeypatch, capsys) -> None:
    root, team, private = _project(tmp_path)
    path = _write(root, {k: v for k, v in _doc(private).items() if k != "signature"})
    monkeypatch.setenv("CLAUDECODE", "1")
    assert dm.sign_file(path, team) == 1
    assert "agent session" in capsys.readouterr().err


def test_the_operator_signs_the_file_in_place(tmp_path, monkeypatch) -> None:
    from agentteams.cli import operator_signing as ops
    from agentteams.update_lock import INVOKER_ENV_MARKERS

    for var, _who in INVOKER_ENV_MARKERS:
        monkeypatch.delenv(var, raising=False)
    root, team, private = _project(tmp_path)
    keyfile = tmp_path / "op.pem"
    keyfile.write_text(private)
    path = _write(root, {k: v for k, v in _doc(private).items() if k != "signature"})
    monkeypatch.setattr(ops, "_signing_preflight", lambda *_a, **_k: (str(keyfile), False))
    monkeypatch.setattr(ops, "_confirm", lambda *_a, **_k: True)
    assert dm.sign_file(path, team) == 0
    assert _load(root, team).problem is None


# --- the merge conditions -----------------------------------------------------------------------------------------

def _pull(**over) -> dict:
    pull = {"state": "open", "merged": False, "draft": False, "changed_files": 1,
            "base": {"ref": _BRANCH, "repo": {"full_name": _REPO}},
            "head": {"sha": _SHA, "repo": {"full_name": _REPO}}}
    for key, value in over.items():
        pull[key] = value
    return pull


def _view(**over) -> dict:
    view = {"mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
            "statusCheckRollup": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED",
                                   "conclusion": "SUCCESS"}]}
    view.update(over)
    return view


_RUN = {"name": "CI", "status": "completed", "conclusion": "success"}


def _problem(pull=None, view=None, required=None, files=None, default="main", signed=("test",),
             runs=None) -> str | None:
    return dm.pr_problem(repo=_REPO, branch=_BRANCH, default_branch=default, pull=pull or _pull(),
                         view=view or _view(), required=required or [], files=files or ["src/app.py"],
                         signed_checks=list(signed), runs=[_RUN] if runs is None else runs)


def test_a_partial_rollup_is_not_the_full_ci() -> None:
    """IM F1: right after a push only the fast jobs may have reported; the signed required set must all be there."""
    assert "not reported yet" in _problem(signed=("test", "test (ubuntu-26.04, 3.12)"))


@pytest.mark.parametrize("runs, why", [
    ([], "no workflow run"),
    ([_RUN, {"name": "CI full", "status": "in_progress", "conclusion": None}], "not completed with success"),
    ([{"name": "CI", "status": "completed", "conclusion": "skipped"}], "not completed with success"),
])
def test_every_workflow_run_for_the_head_must_succeed(runs, why) -> None:
    assert why in _problem(runs=runs)


@pytest.mark.parametrize("checks", [[], ["test", "test"], [" test"], ["x" * 201], ["a\nb"]])
def test_the_signed_required_checks_are_validated(tmp_path, checks) -> None:
    root, team, private = _project(tmp_path)
    with pytest.raises(dm.DevMergeError):
        dm.validate({**_doc(private), "required_checks": checks})


def test_check_names_with_commas_are_signed_unambiguously(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    a = dm.signed_values({**_doc(private), "required_checks": ["a, b"]})
    b = dm.signed_values({**_doc(private), "required_checks": ["a", " b"]})
    assert a != b


def test_a_qualifying_pull_request_has_no_problem() -> None:
    assert _problem() is None


@pytest.mark.parametrize("pull, why", [
    (_pull(base={"ref": "main", "repo": {"full_name": _REPO}}), "base branch"),
    (_pull(base={"ref": _BRANCH, "repo": {"full_name": "acme/other"}}), "base repository"),
    (_pull(head={"sha": _SHA, "repo": {"full_name": "fork/consumer"}}), "fork"),
    (_pull(state="closed"), "not open"),
    (_pull(draft=True), "draft"),
    (_pull(head={"sha": "short", "repo": {"full_name": _REPO}}), "head commit"),
])
def test_pull_request_shape_refusals(pull, why) -> None:
    assert why in _problem(pull=pull)


def test_the_integration_branch_may_not_be_the_default_branch() -> None:
    assert "default branch" in _problem(default=_BRANCH)


@pytest.mark.parametrize("view, why", [
    (_view(mergeStateStatus="BLOCKED"), "CLEAN"),
    (_view(reviewDecision="CHANGES_REQUESTED"), "requested changes"),
    (_view(statusCheckRollup=[]), "no checks"),
    (_view(statusCheckRollup=[{"name": "t", "status": "COMPLETED", "conclusion": "SKIPPED"}]), "not passed"),
    (_view(statusCheckRollup=[{"name": "t", "status": "IN_PROGRESS", "conclusion": ""}]), "not passed"),
    (_view(statusCheckRollup=[{"name": "t", "status": "COMPLETED", "conclusion": "NEUTRAL"}]), "not passed"),
    (_view(statusCheckRollup=[{"__typename": "StatusContext", "context": "ci", "state": "PENDING"}]), "not passed"),
])
def test_check_and_review_refusals(view, why) -> None:
    assert why in _problem(view=view)


def test_a_required_check_must_be_reported_and_passed() -> None:
    assert "required check" in _problem(required=[{"name": "lint", "bucket": "pass"}])
    assert "required check" in _problem(required=[{"name": "test", "bucket": "fail"}])
    assert _problem(required=[{"name": "test", "bucket": "pass"}]) is None


@pytest.mark.parametrize("path", [".github/agents/security.agent.md", ".claude/settings.json",
                                  ".github/workflows/ci.yml", ".agentteams/dev-merge-authorization.json",
                                  "./.claude/hooks/x.py", "CLAUDE.md", "references/enforcement-integrity.json",
                                  ".github/actions/setup/action.yml", ".github/CODEOWNERS", "AGENTS.md",
                                  ".pre-commit-config.yaml", ".githooks/pre-commit"])
def test_governance_agent_and_workflow_paths_are_refused(path) -> None:
    assert "touches" in _problem(files=["src/ok.py", path])


# --- the verb, end to end with a fake gh --------------------------------------------------------------------------

class _FakeGh:
    def __init__(self, *, pull=None, view=None, files=None, parents=None, late_view=None, runs=None):
        self.calls: list[list[str]] = []
        self.pull, self.view, self.late_view = pull or _pull(), view or _view(), late_view
        self.files = files if files is not None else ["src/app.py"]
        self.parents = parents
        self.runs = [_RUN] if runs is None else runs
        self.merged = False
        self.views = 0

    def __call__(self, argv):
        self.calls.append(argv)

        def out(obj, code=0):
            return subprocess.CompletedProcess(argv, code, json.dumps(obj), "")

        if argv[:2] == ["api", f"repos/{_REPO}"]:
            return out({"default_branch": "main"})
        if argv[:2] == ["api", f"repos/{_REPO}/pulls/7"]:
            if self.merged:
                return out({**self.pull, "merged": True, "merge_commit_sha": "m" * 40})
            return out({**self.pull, "changed_files": self.pull.get("changed_files", len(self.files))})
        if argv[0] == "api" and argv[1].startswith(f"repos/{_REPO}/actions/runs?head_sha="):
            return out({"total_count": len(self.runs), "workflow_runs": self.runs})
        if argv[:2] == ["api", "--paginate"]:
            return subprocess.CompletedProcess(argv, 0, "\n".join(self.files) + "\n", "")
        if argv[:2] == ["pr", "view"]:
            self.views += 1
            return out(self.late_view if self.late_view is not None and self.views > 1 else self.view)
        if argv[:2] == ["pr", "checks"]:
            return subprocess.CompletedProcess(argv, 1, "", "no required checks reported on the 'x' branch")
        if argv[:2] == ["pr", "merge"]:
            self.merged = True
            return out({})
        if argv[:2] == ["api", f"repos/{_REPO}/commits/{'m' * 40}"]:
            return out({"parents": [{"sha": p} for p in (self.parents or ["p" * 40, _SHA])]})
        if argv[:2] == ["api", f"repos/{_REPO}/branches/{_BRANCH}"]:
            return out({"commit": {"sha": "m" * 40}})
        return subprocess.CompletedProcess(argv, 1, "", f"unexpected {argv}")


def _merge(root, team, monkeypatch, gh, branch=_BRANCH) -> int:
    return dm.merge_pr(root, team, team_id=_TEAM, number=7, integration_branch=branch, origin=_REPO, gh=gh)


def _pass(team: Path) -> None:
    _log(team, _row(dm.merge_action_id(_REPO, 7, _SHA)))


def test_a_qualifying_merge_runs_exactly_the_merge_commit_command_and_spends_the_pass(tmp_path, monkeypatch) -> None:
    from agentteams.cli import security_gate

    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    gh = _FakeGh()
    assert _merge(root, team, monkeypatch, gh) == 0
    merge = [c for c in gh.calls if c[:2] == ["pr", "merge"]]
    assert merge == [["pr", "merge", "7", "-R", _REPO, "--merge", "--match-head-commit", _SHA]]
    assert not set(merge[0]) & set(dm.FORBIDDEN_MERGE_FLAGS)
    allowed, _why = security_gate.check_clearance(team, action=dm.merge_action_id(_REPO, 7, _SHA))
    assert not allowed   # one PASS, one merge


def test_without_a_pass_nothing_is_merged(tmp_path, monkeypatch, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    gh = _FakeGh()
    assert _merge(root, team, monkeypatch, gh) == 2
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]
    assert "@security PASS" in capsys.readouterr().err


def test_without_the_file_or_on_a_trunk_team_nothing_is_merged(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    gh = _FakeGh()
    assert _merge(root, team, monkeypatch, gh) == 2
    _write(root, _doc(private))
    _pass(team)
    assert _merge(root, team, monkeypatch, gh, branch=None) == 2
    assert _merge(root, team, monkeypatch, gh, branch="staging") == 2
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]


def test_a_head_that_moves_after_the_checks_is_not_merged(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    gh = _FakeGh()
    reads = {"n": 0}
    original = gh.__call__

    def moving(argv):
        if argv[:2] == ["api", f"repos/{_REPO}/pulls/7"]:
            reads["n"] += 1
            if reads["n"] > 1:
                return subprocess.CompletedProcess(argv, 0, json.dumps(_pull(head={"sha": "b" * 40,
                                                                                   "repo": {"full_name": _REPO}})), "")
        return original(argv)

    assert _merge(root, team, monkeypatch, moving) == 2
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]


def test_a_merge_that_is_not_a_merge_commit_raises_an_alarm(tmp_path, monkeypatch, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    assert _merge(root, team, monkeypatch, _FakeGh(parents=[_SHA])) == 1
    assert "ALARM" in capsys.readouterr().err


def test_a_merge_commit_of_another_head_raises_an_alarm(tmp_path, monkeypatch, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    assert _merge(root, team, monkeypatch, _FakeGh(parents=["p" * 40, "c" * 40])) == 1
    assert "ALARM" in capsys.readouterr().err


def test_a_large_pull_request_is_checked_across_pages(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    files = [f"src/m{i}.py" for i in range(120)]
    assert _merge(root, team, monkeypatch, _FakeGh(files=files)) == 0
    _pass(team)
    sneaky = _FakeGh(files=[*files, ".github/workflows/x.yml"])
    assert _merge(root, team, monkeypatch, sneaky) == 2


@pytest.mark.parametrize("changed", [5, dm.MAX_PR_FILES + 1, None])
def test_an_incomplete_or_oversized_file_list_is_an_error(tmp_path, monkeypatch, changed) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    gh = _FakeGh(pull=_pull(changed_files=changed))
    assert _merge(root, team, monkeypatch, gh) == 1
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]


def test_a_check_that_stops_passing_at_merge_time_is_not_merged(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    failing = _view(statusCheckRollup=[{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}])
    gh = _FakeGh(late_view=failing)
    assert _merge(root, team, monkeypatch, gh) == 2
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]


def test_a_non_object_response_is_an_error_not_a_crash(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)

    def weird(argv):
        return subprocess.CompletedProcess(argv, 0, "[1, 2]", "")

    assert _merge(root, team, monkeypatch, weird) == 1


def test_merge_action_id_rejects_malformed_input() -> None:
    for bad in (("a/b", 0, _SHA), ("not a repo", 1, _SHA), ("a/b", 1, "short"), ("a/b", True, _SHA)):
        with pytest.raises(ValueError):
            dm.merge_action_id(*bad)


def test_a_sensitive_file_sends_the_merge_back_to_the_operator(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    gh = _FakeGh(files=["src/a.py", ".github/workflows/ci.yml"])
    assert _merge(root, team, monkeypatch, gh) == 2
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]


def test_the_gate_still_asks_on_a_direct_gh_pr_merge() -> None:
    """The authorization lifts no gate prompt: every direct merge form, the verb's own argv included, still matches."""
    import importlib.util
    import re

    gate_path = Path(dm.__file__).resolve().parents[1] / "templates" / "universal" / "hooks" / "constitutional-gate.py"
    spec = importlib.util.spec_from_file_location("_gate_under_test", gate_path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)  # type: ignore[union-attr]
    pattern = {cid: rx for cid, rx, _why in gate._BASH_REVIEW_TRIGGERS}["pr-merge"]
    for command in ("gh pr merge 7 --merge", f"gh pr merge 7 -R {_REPO} --merge --match-head-commit {_SHA}",
                    f"gh api -X PUT repos/{_REPO}/pulls/7/merge"):
        assert re.search(pattern, command), command


# --- the exempt set and the shipped text --------------------------------------------------------------------------

def test_only_two_authorizations_have_no_sunset_and_no_cap_slot() -> None:
    template = (Path(dm.__file__).resolve().parents[1] / "templates" / "copilot-instructions.template.md").read_text(
        encoding="utf-8")
    assert template.count("No sunset and no cap slot") == 2
    assert "These two are\n  the only such authorizations" in template or "These two are the only" in " ".join(
        template.split())


def test_the_shipped_rule_names_no_specific_repository() -> None:
    template = (Path(dm.__file__).resolve().parents[1] / "templates" / "copilot-instructions.template.md").read_text(
        encoding="utf-8")
    start = template.index("BEGIN dev_merge_rule")
    body = template[start:template.index("END dev_merge_rule")]
    import re

    assert "jlcatonjr" not in body
    assert not re.search(r"repo:[A-Za-z0-9]", body)          # no signed workspace pair is shipped
    assert not re.search(r"github\.com/[A-Za-z0-9]", body)    # no repository URL


def test_the_verify_verb_reports_what_merge_pr_would_do(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    monkeypatch.setattr(dm.tg, "workspace_repo", lambda _root: _REPO)
    assert dm.verify_command(root, team, _TEAM, integration_branch=_BRANCH) == 0
    assert dm.verify_command(root, team, _TEAM, integration_branch="staging") == 1
    assert dm.verify_command(root, team, _TEAM, integration_branch=None) == 1


def test_a_merge_waits_for_every_head_workflow_run(tmp_path, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _pass(team)
    gh = _FakeGh(runs=[_RUN, {"name": "CI", "status": "queued", "conclusion": None}])
    assert _merge(root, team, monkeypatch, gh) == 2
    assert not [c for c in gh.calls if c[:2] == ["pr", "merge"]]
