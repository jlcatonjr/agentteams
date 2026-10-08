"""The MCP catalogue (agentteams/templates/mcp/), its expansion, the PR agents it brings, and the two first-party
servers (agentteams --serve-mcp recall|gitread). Design: references/plans/mcp-catalog.design.md (§7 @security
conditions C1-C7, §8 operator decisions)."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from agentteams import analyze, mcp_catalog
from agentteams.mcp_servers import _stdio, gitread, recall
from agentteams.mcp_servers._stdio import ToolError

_SCHEMA = json.loads((Path(mcp_catalog.__file__).parent / "schemas" / "mcp-server.schema.json").read_text())
_ROSTER = ["orchestrator", "navigator", "work-summarizer", "security", "git-operations", "pr-manager", "pr-notifier"]


def _desc(**kw):
    return {"project_goal": "g", "project_name": "p", **kw}


def _manifest(**kw):
    return {"agent_slug_list": list(_ROSTER), **kw}


# --- the catalogue ------------------------------------------------------------------------------------------------

def test_catalogue_ids():
    assert set(mcp_catalog.load_catalog()) == {
        "agentteams-recall", "agentteams-gitread", "github-read", "github-write", "fetch"}


@pytest.mark.parametrize("sid", sorted(mcp_catalog.load_catalog()))
def test_each_entry_is_schema_valid_once_catalogue_keys_are_stripped(sid):
    jsonschema = pytest.importorskip("jsonschema")
    entry = {k: v for k, v in mcp_catalog.load_catalog()[sid].items() if k not in mcp_catalog.CATALOG_KEYS}
    jsonschema.Draft7Validator(_SCHEMA).validate(entry)


def test_only_first_party_read_only_servers_default_on():
    for sid, e in mcp_catalog.load_catalog().items():
        first_party_read = e["trust_tier"] == "first-party" and all(t["side_effects"] == "read" for t in e["tools"])
        assert e["catalog_default"] is first_party_read, sid
        assert e["security_review"]["required"] is (not first_party_read), sid


def test_no_destructive_tool_and_no_inline_credential():
    for sid, e in mcp_catalog.load_catalog().items():
        assert all(t["side_effects"] != "destructive" for t in e["tools"]), sid
        auth = e.get("auth", {})
        assert auth.get("mechanism") in ("none", "env"), sid
        if auth.get("mechanism") == "env":
            assert auth["credential_ref"] == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_github_write_is_an_exact_allowlist_without_merge_or_delete():
    """C7 + operator decision: --tools allowlist; no merge_pull_request, no delete_file, no delete_repository."""
    e = mcp_catalog.load_catalog()["github-write"]
    args = e["args"]
    assert "--toolsets" not in args and "--read-only" not in args
    allowed = args[args.index("--tools") + 1].split(",")
    assert allowed == [t["name"] for t in e["tools"]]
    for banned in ("merge_pull_request", "delete_file", "delete_repository", "fork_repository"):
        assert banned not in allowed
    # Push tools are git-operations' work: the PR agents read via github-read and write PRs with gh.
    assert e["role_scope"] == ["orchestrator", "git-operations"]


@pytest.mark.parametrize("name", ["pr-manager", "pr-notifier"])
def test_pr_templates_name_only_real_github_tools(name):
    import re
    text = (Path(mcp_catalog.__file__).parent / "templates" / "domain" / f"{name}.template.md").read_text()
    known = {t["name"] for e in mcp_catalog.load_catalog().values() for t in e["tools"]}
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", text)) - {"write_policy", "opt_out", "pr_management", "unknown_login"}
    assert named <= known, named - known


def test_github_read_runs_read_only():
    e = mcp_catalog.load_catalog()["github-read"]
    assert "--read-only" in e["args"]
    assert all(t["side_effects"] == "read" for t in e["tools"])


def test_third_party_entries_are_version_pinned():
    """C4: an exact upstream version; the digest is recorded at vetting."""
    for sid in ("github-read", "github-write", "fetch"):
        pin = mcp_catalog.load_catalog()[sid]["pin"]
        assert pin["version"] and pin["source"].startswith("https://"), sid
    assert "mcp-server-fetch==2026.8.18" in mcp_catalog.load_catalog()["fetch"]["args"]


# --- expansion ----------------------------------------------------------------------------------------------------

def test_nothing_enabled_leaves_the_manifest_untouched():
    m = _manifest(host_features=[])
    before = json.dumps(m, sort_keys=True)
    assert mcp_catalog.expand(m, "claude") == []
    assert json.dumps(m, sort_keys=True) == before


def test_defaults_on_when_mcp_is_enabled_and_scoped_to_the_roster():
    m = _manifest(host_features=["claude:mcp"])
    mcp_catalog.expand(m, "claude")
    by_id = {s["server_id"]: s for s in m["mcp_servers"]}
    assert set(by_id) == {"agentteams-recall", "agentteams-gitread"}
    assert by_id["agentteams-recall"]["scope"] == ["orchestrator", "navigator"]
    assert "pr-notifier" not in by_id["agentteams-gitread"]["scope"]
    for s in by_id.values():
        assert not set(s) & set(mcp_catalog.CATALOG_KEYS)


def test_opt_in_and_exclude():
    m = _manifest(host_features=["goose:mcp"], mcp_catalog=["github-write"], mcp_catalog_exclude=["agentteams-recall"])
    mcp_catalog.expand(m, "goose")
    assert [s["server_id"] for s in m["mcp_servers"]] == ["agentteams-gitread", "github-write"]


def test_opt_in_without_an_mcp_token_still_emits_the_opt_in_only():
    m = _manifest(host_features=[], mcp_catalog=["github-read"])
    mcp_catalog.expand(m, "claude")
    assert [s["server_id"] for s in m["mcp_servers"]] == ["github-read"]


def test_declared_server_wins():
    mine = {"server_id": "agentteams-gitread", "scope": ["navigator"]}
    m = _manifest(host_features=["claude:mcp"], mcp_servers=[mine])
    notices = mcp_catalog.expand(m, "claude")
    assert m["mcp_servers"][0] is mine
    assert [s["server_id"] for s in m["mcp_servers"]].count("agentteams-gitread") == 1
    assert any("declares its own" in n for n in notices)


def test_codex_withholds_defaults_unless_opted_in():
    m = _manifest(host_features=["codex:mcp"])
    notices = mcp_catalog.expand(m, "codex")
    assert "mcp_servers" not in m and any("ignores scope" in n for n in notices)
    m = _manifest(host_features=["codex:mcp"], mcp_catalog=["agentteams-gitread"])
    mcp_catalog.expand(m, "codex")
    assert [s["server_id"] for s in m["mcp_servers"]] == ["agentteams-gitread"]


def test_under_the_switch_nothing_is_emitted():
    m = _manifest(host_features=["goose:mcp"], write_policy="orchestrator-only", mcp_catalog=["github-write"])
    notices = mcp_catalog.expand(m, "goose")
    assert "mcp_servers" not in m
    assert any("github-write" in n and "P5" in n for n in notices)
    assert any("agentteams-gitread" in n and "not emitted" in n for n in notices)


def test_role_scope_with_no_roster_match_is_dropped():
    m = {"agent_slug_list": ["orchestrator"], "host_features": [], "mcp_catalog": ["fetch"]}
    notices = mcp_catalog.expand(m, "claude")
    assert "mcp_servers" not in m and any("fetch" in n for n in notices)


# --- the brief and analyze ----------------------------------------------------------------------------------------

def test_unknown_id_is_an_error():
    with pytest.raises(ValueError, match="unknown MCP catalogue id"):
        analyze.build_manifest(_desc(mcp_catalog=["github-admin"]), framework="claude")


def test_manifest_carries_selection_only_when_set():
    assert "mcp_catalog" not in analyze.build_manifest(_desc(), framework="claude")
    m = analyze.build_manifest(_desc(mcp_catalog=["fetch"], mcp_catalog_exclude=["agentteams-recall"]),
                               framework="claude")
    assert m["mcp_catalog"] == ["fetch"] and m["mcp_catalog_exclude"] == ["agentteams-recall"]


@pytest.mark.parametrize("kw,expected", [
    ({}, False),
    ({"mcp_catalog": ["fetch"]}, False),
    ({"mcp_catalog": ["github-read"]}, True),
    ({"pr_management": True}, True),
])
def test_pr_agents_join_the_roster_only_on_opt_in(kw, expected):
    slugs = analyze.build_manifest(_desc(**kw), framework="claude")["agent_slug_list"]
    assert ("pr-manager" in slugs) is expected and ("pr-notifier" in slugs) is expected


@pytest.mark.parametrize("name", ["pr-manager", "pr-notifier"])
def test_pr_templates_have_an_invariant_core_and_never_merge(name):
    text = (Path(mcp_catalog.__file__).parent / "templates" / "domain" / f"{name}.template.md").read_text()
    assert "## Invariant Core" in text and "⛔" in text
    if name == "pr-manager":
        assert "orchestrator-only" in text and "Never merge" in text
    else:  # no shell at all: it returns commands for pr-manager to run (@security implementation cond. 6)
        assert "tools: ['read', 'search']" in text and "Never propose a merge" in text


# --- the stdio core -----------------------------------------------------------------------------------------------

def _rpc(lines, tools, call, root):
    out = io.StringIO()
    _stdio.serve("t", tools, call, root, stdin=io.StringIO("".join(json.dumps(l) + "\n" for l in lines)), stdout=out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def test_stdio_core_answers_and_isolates_errors(tmp_path):
    def call(name, args, root):
        if args.get("fail"):
            raise ToolError("nope")
        return {"ok": name}
    tools = [{"name": "a", "inputSchema": {"type": "object"}}]
    replies = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "a", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "a", "arguments": {"fail": 1}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "zzz", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
    ], tools, call, tmp_path)
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]
    assert json.loads(replies[1]["result"]["content"][0]["text"]) == {"ok": "a"}
    assert replies[2]["result"]["isError"] and replies[3]["result"]["isError"]
    assert replies[4]["error"]["code"] == -32601


def test_stdio_core_caps_results(tmp_path):
    replies = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "a", "arguments": {}}}],
                   [{"name": "a"}], lambda n, a, r: "x" * (_stdio.MAX_RESULT * 2), tmp_path)
    assert replies[0]["result"]["content"][0]["text"].endswith("[truncated at 64 KiB]")


# --- recall -------------------------------------------------------------------------------------------------------

def test_recall_validates_arguments(tmp_path):
    for bad in ({"query": ""}, {"query": "x", "k": 0}, {"query": "x", "k": 21}, {"query": "x", "k": True}):
        with pytest.raises(ToolError):
            recall.call("query_index", bad, tmp_path)


def test_recall_with_no_index_says_so_and_writes_nothing(tmp_path):
    with pytest.raises(ToolError, match="refresh-index"):
        recall.call("query_index", {"query": "x"}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_recall_hit_fields_are_allowlisted():
    hit = recall._hit({"path": "a", "score": 1, "snippet": "s" * 1000, "body": "secret", "doc_id": 1})
    assert set(hit) == {"path", "score", "snippet", "doc_id"} and len(hit["snippet"]) == recall._SNIPPET_MAX + 1


# --- gitread ------------------------------------------------------------------------------------------------------

def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "first")
    (root / "a.txt").write_text("one\ntwo\n")
    _git(root, "commit", "-qam", "second")
    return root


def test_gitread_tools_work(repo):
    assert "second" in gitread.call("git_log", {"n": 5}, repo)["output"]
    assert "+two" in gitread.call("git_show", {"rev": "HEAD"}, repo)["output"]
    assert "+two" in gitread.call("git_diff", {"base": "HEAD~1", "head": "HEAD", "path": "a.txt"}, repo)["output"]
    assert "two" in gitread.call("git_blame", {"rev": "HEAD", "path": "a.txt"}, repo)["output"]
    (repo / "new.txt").write_text("x")
    assert "?? new.txt" in gitread.call("git_status", {}, repo)["output"]


@pytest.mark.parametrize("tool,args", [
    ("git_show", {"rev": "--output=/tmp/pwned"}),
    ("git_show", {"rev": "HEAD;rm"}),
    ("git_show", {"rev": "HEAD", "path": "../x"}),
    ("git_show", {"rev": "HEAD", "path": "/etc/passwd"}),
    ("git_show", {"rev": "HEAD", "path": ".git/config"}),
    ("git_log", {"n": 201}),
    ("git_log", {"n": "5"}),
    ("git_diff", {"base": "HEAD"}),
    ("git_blame", {"rev": "HEAD"}),
    ("git_blame", {"path": "a.txt"}),
    ("git_nope", {}),
])
def test_gitread_refuses_bad_arguments(repo, tool, args):
    with pytest.raises(ToolError):
        gitread.call(tool, args, repo)
    assert not Path("/tmp/pwned").exists()


def test_gitread_refuses_a_repository_with_filters(repo):
    """C2: clean/process filters run programs on worktree reads; refuse any filter.* config, includes too."""
    marker = repo.parent / "ran"
    inc = repo.parent / "inc.cfg"
    inc.write_text(f"[filter \"x\"]\n\tclean = touch {marker}\n")
    _git(repo, "config", "include.path", str(inc))
    (repo / ".gitattributes").write_text("* filter=x\n")
    with pytest.raises(ToolError, match="filter"):
        gitread.call("git_status", {}, repo)
    assert not marker.exists()


def test_gitread_neutralises_repository_config_that_runs_programs(repo):
    """C2: fsmonitor, external diff, textconv and hooks never run."""
    marker = repo.parent / "ran"
    script = repo.parent / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    (repo / ".gitattributes").write_text("*.txt diff=t\n")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-qm", "attrs")
    # Planted only now: the setup's own git calls above would otherwise run fsmonitor themselves.
    for key in ("core.fsmonitor", "diff.external", "diff.t.textconv", "core.pager", "core.sshCommand"):
        _git(repo, "config", key, str(script))
    assert not marker.exists()
    gitread.call("git_status", {}, repo)
    gitread.call("git_show", {"rev": "HEAD~1"}, repo)
    gitread.call("git_diff", {"base": "HEAD~2", "head": "HEAD"}, repo)
    gitread.call("git_blame", {"rev": "HEAD", "path": "a.txt"}, repo)
    assert not marker.exists()


def test_gitread_environment_is_an_allowlist(repo, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/bin/false")
    env = gitread._env(repo)
    assert "GIT_DIR" not in env and "GIT_EXTERNAL_DIFF" not in env
    assert env["GIT_NO_LAZY_FETCH"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "second" in gitread.call("git_log", {}, repo)["output"]


def test_gitread_status_takes_no_lock(repo):
    index = repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    (repo / "a.txt").touch()
    gitread.call("git_status", {}, repo)
    assert index.stat().st_mtime_ns == before


def test_serve_mcp_cli_runs_gitread(repo):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "git_log", "arguments": {"n": 1}}}) + "\n"
    proc = subprocess.run(["python3", str(Path(__file__).resolve().parents[1] / "build_team.py"),
                           "--serve-mcp", "gitread", "--project", str(repo)],
                          input=req, capture_output=True, text=True, timeout=60)
    reply = json.loads(proc.stdout.splitlines()[0])
    assert "second" in reply["result"]["content"][0]["text"]


def test_stdio_core_answers_malformed_and_oversized_lines_with_parse_errors(tmp_path):
    out = io.StringIO()
    src = io.StringIO("not json\n\n" + "x" * (_stdio.MAX_LINE + 1) + "\n")
    _stdio.serve("t", [], lambda n, a, r: None, tmp_path, stdin=src, stdout=out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [r["error"]["code"] for r in replies] == [-32700, -32700]


@pytest.mark.parametrize("sid,module", [("agentteams-recall", recall), ("agentteams-gitread", gitread)])
def test_first_party_entries_list_exactly_the_servers_tools(sid, module):
    entry = mcp_catalog.load_catalog()[sid]
    assert [t["name"] for t in entry["tools"]] == [t["name"] for t in module.TOOLS]
    assert entry["args"][-1] in __import__("agentteams.mcp_servers", fromlist=["SERVERS"]).SERVERS


def test_stdio_core_rejects_non_object_params(tmp_path):
    replies = _rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]}],
                   [{"name": "a"}], lambda n, a, r: None, tmp_path)
    assert replies[0]["error"]["code"] == -32602


def test_recall_refuses_an_unknown_tool(tmp_path):
    with pytest.raises(ToolError, match="unknown tool"):
        recall.call("query_everything", {"query": "x"}, tmp_path)


def _snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*"))


def test_recall_against_a_real_index_writes_nothing(tmp_path):
    """@security condition 1: the CLI loaders write a .vcache sidecar; recall must not."""
    pytest.importorskip("jsonschema")
    from agentteams.cli.artifacts import _write_memory_index
    from agentteams.cli.code_index_artifacts import _run_refresh_code_index

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text('"""Widget tools."""\n\ndef frobnicate(x):\n    """Frobnicate x."""\n')
    (tmp_path / "README.md").write_text("# Widgets\n\nThe frobnicate pipeline handles widgets.\n")
    manifest = analyze.build_manifest(_desc(existing_project_path=str(tmp_path)), framework="claude")
    _write_memory_index(manifest, tmp_path)
    _run_refresh_code_index(manifest, tmp_path)
    for side in tmp_path.rglob("*.vcache"):
        side.unlink()
    before = _snapshot(tmp_path)
    out = recall.call("query_index", {"query": "widgets"}, tmp_path)
    assert out["may_be_stale"] is True
    recall.call("query_code", {"query": "frobnicate"}, tmp_path)
    assert _snapshot(tmp_path) == before


def test_recall_refuses_paths_outside_the_project(tmp_path):
    """@security condition 2: a symlinked team dir or a partition file escaping the project is refused."""
    outside = tmp_path / "outside"
    (outside / "references").mkdir(parents=True)
    (outside / "references" / "memory-index.json").write_text("{}")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".goose").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError, match="outside the project"):
        recall.call("query_index", {"query": "x"}, proj)


def test_gitread_neutralises_per_protocol_and_mailmap_config(repo):
    """@security conditions 3-4."""
    argv = gitread._base(repo)
    for p in ("file", "git", "ssh", "http", "https", "ext"):
        assert f"protocol.{p}.allow=never" in argv
    for item in ("log.mailmap=false", "mailmap.file=", "mailmap.blob="):
        assert item in argv
    secret = repo.parent / "secret.txt"
    secret.write_text("t <t@example.com> LEAKED <leak@example.com>\n")
    _git(repo, "config", "mailmap.file", str(secret))
    _git(repo, "config", "log.mailmap", "true")
    assert "LEAKED" not in gitread.call("git_log", {}, repo)["output"]
    assert "LEAKED" not in gitread.call("git_blame", {"rev": "HEAD", "path": "a.txt"}, repo)["output"]


def test_gitread_refuses_a_launch_directory_that_is_not_the_work_tree_root(repo):
    """@security condition 5."""
    (repo / "sub").mkdir()
    with pytest.raises(ToolError, match="not the root"):
        gitread.call("git_status", {}, repo / "sub")


def test_workstream_experts_group_resolves_against_the_manifest():
    m = _manifest(host_features=["claude:mcp"], workstream_expert_slugs=["parser-expert", "ghost-expert"])
    m["agent_slug_list"].append("parser-expert")
    mcp_catalog.expand(m, "claude")
    by_id = {s["server_id"]: s for s in m["mcp_servers"]}
    for sid in ("agentteams-recall", "agentteams-gitread"):
        assert "parser-expert" in by_id[sid]["scope"]
        assert "ghost-expert" not in by_id[sid]["scope"]  # not in the roster
        assert "@workstream-experts" not in by_id[sid]["scope"]  # a token is never emitted as a slug


def test_role_groups_are_not_valid_agent_slugs():
    import re
    for token in mcp_catalog.ROLE_GROUPS:
        assert not re.fullmatch(r"[a-z0-9][a-z0-9-]*", token)
