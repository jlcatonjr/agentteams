"""Repository aliases for the agent, and grants-first ordering in the executor (PR C).

The gateway's DLP redacts home paths, so the agent names repositories by alias (:mod:`.repo_alias`) and the
MCP server maps a known alias back to the host path. The executor refuses a repo-op target outside every
active grant's ``repo_roots`` before any subprocess runs in it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import daily, executor, mcp, repo_alias
from agentteams.vulnscan.mcp import Server, ToolError

pytest.importorskip("cryptography")
from tests.test_vulnscan_executor import _install, env  # noqa: E402,F401  (env: a fixture)


@pytest.fixture
def run(tmp_path: Path) -> dict[str, Path]:
    d = {"run": tmp_path / "run", "requests": tmp_path / "requests", "results": tmp_path / "results",
         "repo": tmp_path / "home" / "src" / "svc", "sibling": tmp_path / "home" / "src" / "svc2"}
    for k in ("run", "requests", "results"):
        d[k].mkdir()
    repo, sibling = str(d["repo"]), str(d["sibling"])
    (d["run"] / f"fixplan-{repo_alias.alias(repo)}.md").write_text(
        f"# Fix plan\n\nRepository (the `stage-fix` target): `{repo}`\n\n| `{repo}/requirements.txt` | PyJWT |\n")
    (d["run"] / "summary.md").write_text(f"# report\n\n- {repo}: 1 critical\n- {sibling}: 1 low\n")
    (d["run"] / "summary.json").write_text(json.dumps({"findings": [
        {"kind": "package", "target": repo, "name": "PyJWT", "version": "2.3.0", "severity": 4},
        {"kind": "package", "target": sibling, "name": "idna", "version": "3.0", "severity": 1}]}))
    return d


def _server(d: dict[str, Path]) -> Server:
    return Server(d["run"], d["requests"], d["results"])


def _queued(d: dict[str, Path], rid: str) -> dict:
    return json.loads((d["requests"] / f"{rid}.json").read_text())


def test_alias_is_the_plan_file_key_and_the_state_slug() -> None:
    import hashlib
    import re
    path = "/srv/work/my repo"
    assert repo_alias.alias(path) == daily.plan_slug(Path(path)) == executor.state_slug(Path(path))
    assert re.fullmatch(r"my-repo-[0-9a-f]{8}", repo_alias.alias(path))
    digest = hashlib.sha256(b"/").hexdigest()[:8]           # existing state-file names stay byte-identical
    assert executor.state_slug(Path("/")) == f"-{digest}" and daily.plan_slug(Path("/")) == f"root-{digest}"


def test_two_paths_sharing_an_alias_are_both_left_unmapped(run: dict[str, Path],
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_alias, "alias", lambda path, **k: "svc-00000000")
    assert repo_alias.known(run["run"]) == {}
    with pytest.raises(ToolError, match="unknown repository alias"):
        _server(run).call("request", {"op": "stage-fix", "target": "svc-00000000", "rationale": "x"})


def test_executor_results_show_aliases_not_paths(run: dict[str, Path]) -> None:
    repo, name = str(run["repo"]), repo_alias.alias(str(run["repo"]))
    rid = "a" * 32
    (run["results"] / f"{rid}.json").write_text(json.dumps(
        {"id": rid, "status": "refused", "detail": f"not authorized: stage-fix {repo}: no grant",
         "staged": {repo: {"wt": f"{repo}/.wt"}}}))
    for out in (_server(run).call("request_status", {"id": rid}), _server(run).call("list_requests", {})):
        text = json.dumps(out)
        assert repo not in text and f"stage-fix {name}: no grant" in text and f'"{name}"' in text


def test_tools_show_aliases_never_repository_paths(run: dict[str, Path]) -> None:
    s, name = _server(run), repo_alias.alias(str(run["repo"]))
    plans = s.call("get_fix_plans", {})
    assert f"## stage-fix target `{name}`" in plans and f"`{name}/requirements.txt`" in plans
    summary = s.call("get_summary", {})
    findings = s.call("list_findings", {})
    for text in (plans, summary, json.dumps(findings)):
        assert str(run["repo"]) not in text and str(run["sibling"]) not in text
    # a path sharing a prefix gets its own alias, not the shorter path's alias plus a suffix
    assert repo_alias.alias(str(run["sibling"])) in summary and f"{name}2" not in summary
    assert {r["target"] for r in findings["findings"]} == {name, repo_alias.alias(str(run["sibling"]))}
    only = s.call("list_findings", {"target": name})
    assert [r["name"] for r in only["findings"]] == ["PyJWT"]


def test_request_maps_a_known_alias_to_the_host_path(run: dict[str, Path]) -> None:
    s = _server(run)
    out = s.call("request", {"op": "stage-fix", "target": repo_alias.alias(str(run["repo"])), "rationale": "fix"})
    assert _queued(run, out["id"])["target"] == str(run["repo"])
    out = s.call("request", {"op": "open-pr", "target": str(run["repo"]), "rationale": "compat"})   # paths still work
    assert _queued(run, out["id"])["target"] == str(run["repo"])
    esc = s.call("escalate", {"target": repo_alias.alias(str(run["repo"])), "kind": "other", "evidence": "x"})
    assert _queued(run, esc["id"])["target"] == str(run["repo"])


@pytest.mark.parametrize("target", ["svc-00000000", "svc", "~/src/svc", "agenthome/src/svc", "../svc"])
def test_request_refuses_an_unknown_alias(run: dict[str, Path], target: str) -> None:
    with pytest.raises(ToolError, match="unknown repository alias"):
        _server(run).call("request", {"op": "stage-fix", "target": target, "rationale": "x"})
    assert not list(run["requests"].iterdir())


def test_server_targets_are_unchanged(run: dict[str, Path]) -> None:
    out = _server(run).call("request", {"op": "server-audit", "target": "web1", "rationale": "x"})
    assert _queued(run, out["id"])["target"] == "web1"
    assert mcp.SCHEMAS["request"]["inputSchema"]["properties"]["target"]["description"].startswith("repository alias")


# ---------------------------------------------------------------- grants first

def _no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def refuse(argv: object, *a: object, **k: object) -> None:
        calls.append(argv)
        raise AssertionError(f"a subprocess ran: {argv!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(executor, "git_proc", lambda *a, **k: refuse(("git_proc", *a)))
    return calls


@pytest.mark.parametrize("where", ["root", "outside", "symlink", "missing"])
def test_a_target_no_grant_covers_never_reaches_git(env: dict, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                                     where: str) -> None:
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    roots = env["tmp"] / "repos"
    outside = env["tmp"] / "elsewhere"
    outside.mkdir()
    (roots / "link").symlink_to(outside, target_is_directory=True)
    target = {"root": "/root", "outside": str(outside), "symlink": str(roots / "link"),
              "missing": str(roots / "gone")}[where]
    calls = _no_subprocess(monkeypatch)
    cfg = {**env["cfg"], "confine": {"subprocesses": False}}
    for op in ("stage-fix", "open-pr"):
        res = executor.act(op, target, cfg)
        assert res["status"] == "refused", res
        want = "not an existing directory" if where == "missing" else "outside every active grant"
        assert want in res["detail"], res
    assert calls == []


def test_the_checked_resolved_path_is_what_runs(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _install(env, ops=["stage-fix"], hosts=[])
    real = env["tmp"] / "repos" / "svc"
    real.mkdir()
    (env["tmp"] / "repos" / "alias-link").symlink_to(real, target_is_directory=True)
    seen: list[str] = []

    def record(target: str) -> Path:
        seen.append(target)
        raise executor.ExecError("stop here")

    monkeypatch.setattr(executor, "_repo_root", record)
    res = executor.act("stage-fix", str(env["tmp"] / "repos" / "alias-link"),
                       {**env["cfg"], "confine": {"subprocesses": False}})
    assert res["status"] == "refused" and seen == [str(real.resolve())]


def test_open_pr_and_close_superseded_share_one_ledger_target(env: dict,  # noqa: F811
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """close-superseded finds open-pr's PRs by ledger target: both go through the same resolved path."""
    _install(env, ops=["open-pr", "close-superseded"], hosts=[])
    real = env["tmp"] / "repos" / "svc"
    real.mkdir()
    (env["tmp"] / "repos" / "via-link").symlink_to(real, target_is_directory=True)
    seen: list[tuple[str, str]] = []

    def record(op: str, target: str, request_id: str, cfg: dict) -> dict:
        seen.append((op, target))
        raise executor.ExecError("stop here")

    monkeypatch.setattr(executor, "_repo_root", lambda t: Path(t))
    monkeypatch.setattr(executor, "authorize", record)
    cfg = {**env["cfg"], "confine": {"subprocesses": False}}
    for op in ("open-pr", "close-superseded"):
        for target in (str(real), str(env["tmp"] / "repos" / "via-link")):
            executor.act(op, target, cfg)
    assert {t for _, t in seen} == {str(real.resolve())} and len(seen) == 4


def test_escalate_runs_git_only_inside_a_grant(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from agentteams.vulnscan import executor_guard, notify
    monkeypatch.setattr(notify, "send", lambda *a, **k: True)
    _install(env, ops=["stage-fix"], hosts=[])
    outside = env["tmp"] / "elsewhere"
    outside.mkdir()
    popen, git_proc = subprocess.Popen, executor.git_proc
    calls = _no_subprocess(monkeypatch)
    for target in ("/root", str(outside), "relative/path"):
        assert executor_guard.canonical_if_granted(target) is None
    res = executor.act("escalate", str(outside), env["cfg"], payload={"kind": "other", "evidence": "x"}, run_id="r")
    assert res["status"] == "done" and res["scope"] == "*" and calls == []        # fail-safe: blocks everything
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(executor, "git_proc", git_proc)
    inside = env["tmp"] / "repos" / "svc"
    subprocess.run(["git", "init", "-q", str(inside)], check=True)
    assert executor_guard.canonical_if_granted(str(inside / ".")) == str(inside.resolve())


def test_without_any_grant_the_refusal_names_the_grant_problem(env: dict,  # noqa: F811
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _no_subprocess(monkeypatch)
    res = executor.act("stage-fix", "/root", {**env["cfg"], "confine": {"subprocesses": False}})
    assert res["status"] == "refused" and "not authorized" in res["detail"] and calls == []
