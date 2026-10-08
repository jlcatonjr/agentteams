"""Orchestrator-only-writes pilot, P1 (agentteams/proposals.py), after the security/adversarial/hygiene reviews.

Identity comes from a signed dispatch nonce; commands match per-agent argv prefixes and argument patterns;
gates and commands get a scrubbed environment; undeclared writes fail the run; the ledger is HMAC-signed,
chained and head-anchored.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentteams import proposals as P

PY = sys.executable
KEY = "test-ledger-key"


@pytest.fixture(autouse=True)
def _ledger_key(monkeypatch):
    monkeypatch.setenv("AGENTTEAMS_PROPOSAL_LEDGER_KEY", KEY)
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", "decision-secret")


def _brief() -> dict:
    return {
        "agent_policies": {
            "lean-prover": {
                "write_scopes": ["lean/MathAgentsWIP/", "lean/MathAgents.lean"],
                "commands": [
                    {"prefix": [PY, "-c", "import sys; print(len(sys.stdin.read()))"], "args": [],
                     "stdin_gates": ["no-sorry"]},
                    {"prefix": [PY, "-c", "import os; print(os.environ.get('AGENTTEAMS_DECISION_SIGNING_KEY'))"],
                     "args": []},
                    {"prefix": [PY, "-c", "import sys; open(sys.argv[1], 'w').write('x')"],
                     "args": ["^[a-z]+\\.txt$"]},
                ],
            },
            "counterexample-hunter": {
                "commands": [{"prefix": [PY, "tools/record.py", "add"], "args": ["^[a-z0-9][a-z0-9-]*$", "^CX-[0-9]+$"],
                              "writes": ["reports/dossiers/*/counterexamples.json"]}],
            },
        },
        "proposal_gates": {"no-sorry": {"glob": "lean/**.lean", "argv": [PY, "tools/gate.py", "{file}"]}},
        "protected_paths": ["reports/dossiers/*/counterexamples.json", "reports/kernel"],
    }


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "lean" / "MathAgentsWIP").mkdir(parents=True)
    (root / "lean" / "MathAgentsWIP" / "Foo.lean").write_text("theorem foo : True := trivial\n")
    (root / "reports" / "dossiers" / "x").mkdir(parents=True)
    (root / "reports" / "kernel").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "tools" / "gate.py").write_text("import sys\nsys.exit(1 if 'sorry' in open(sys.argv[1]).read() else 0)\n")
    (root / "tools" / "record.py").write_text(
        "import sys, json\nopen('reports/dossiers/x/counterexamples.json','w').write(json.dumps(sys.argv[1:]))\n")
    _git(root)
    return root, P.load_policy(_brief(), brief_rel="brief.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prop(root, who, path, base, content="theorem foo : True := by trivial\n", **extra):
    return {"kind": "change-proposal", "dispatch": P.issue_dispatch(root, who), "path": path,
            "base_sha256": base, "content": content, "rationale": "tidy the proof", **extra}


def _req(root, who, argv, **extra):
    return {"kind": "command-request", "dispatch": P.issue_dispatch(root, who), "argv": argv,
            "purpose": "test", **extra}


# --- identity ----------------------------------------------------------------------------------


def test_identity_comes_from_the_dispatch_nonce(project):
    root, policy = project
    target = root / "lean/MathAgentsWIP/Foo.lean"
    hunter = _prop(root, "counterexample-hunter", "lean/MathAgentsWIP/Foo.lean", _sha(target))
    with pytest.raises(P.ProposalError, match="counterexample-hunter's write_scopes"):
        P.apply_proposal(hunter, root=root, policy=policy)


@pytest.mark.parametrize("nonce, needle", [("0" * 32, "unknown dispatch nonce"), (None, "no valid dispatch nonce"),
                                           ("not-hex", "no valid dispatch nonce")])
def test_missing_or_forged_nonce_refused(project, nonce, needle):
    root, policy = project
    proposal = _prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent")
    proposal["dispatch"] = nonce
    with pytest.raises(P.ProposalError, match=needle):
        P.apply_proposal(proposal, root=root, policy=policy)


def test_claimed_agent_mismatch_refused(project):
    root, policy = project
    proposal = _prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent", agent="statement-formalizer")
    with pytest.raises(P.ProposalError, match="claims agent"):
        P.apply_proposal(proposal, root=root, policy=policy)


def test_unsigned_dispatch_row_is_not_trusted(project):
    root, policy = project
    (root / P.DISPATCH_REL).parent.mkdir(parents=True, exist_ok=True)
    forged = json.dumps({"nonce": "a" * 32, "agent": "lean-prover", "expires": "2999-01-01T00:00:00+00:00", "mac": "0" * 64})
    with (root / P.DISPATCH_REL).open("a") as fh:
        fh.write(forged + "\n")
    proposal = _prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent")
    proposal["dispatch"] = "a" * 32
    with pytest.raises(P.ProposalError, match="unknown dispatch nonce"):
        P.apply_proposal(proposal, root=root, policy=policy)


def test_no_key_refuses_everything(project, monkeypatch):
    root, _ = project
    monkeypatch.delenv("AGENTTEAMS_PROPOSAL_LEDGER_KEY")
    monkeypatch.delenv("AGENTTEAMS_DECISION_SIGNING_KEY")
    with pytest.raises(P.ProposalError, match="no ledger key"):
        P.issue_dispatch(root, "lean-prover")


# --- change and deletion proposals --------------------------------------------------------------


def test_apply_writes_records_and_keeps_mode(project):
    root, policy = project
    target = root / "lean/MathAgentsWIP/Foo.lean"
    target.chmod(0o644)
    result = P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Foo.lean", _sha(target)),
                              root=root, policy=policy)
    assert result["written"] and result["agent"] == "lean-prover"
    assert target.read_text() == "theorem foo : True := by trivial\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert P.verify_ledger(root) == []


def test_gate_runs_on_the_proposed_content_including_the_top_level_file(project):
    root, policy = project
    with pytest.raises(P.ProposalError, match="no-sorry refused"):
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgents.lean", "absent", "x := sorry\n"),
                         root=root, policy=policy)
    with pytest.raises(P.ProposalError, match="no-sorry refused"):  # case-folded glob
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/X.LEAN", "absent", "sorry\n"),
                         root=root, policy=policy)


def test_stale_base_and_new_file(project):
    root, policy = project
    P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent"), root=root, policy=policy)
    with pytest.raises(P.ProposalError, match="stale base"):
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent"), root=root, policy=policy)


@pytest.mark.parametrize("path, needle", [
    ("lean/Other/Foo.lean", "outside lean-prover's write_scopes"),
    ("reports/dossiers/x/counterexamples.json", "protected path"),
    ("reports/kernel/latest.json", "protected path"),            # bare directory pattern covers below it
    (".claude/agents/x.md", "control plane"),
    (".AGENTTEAMS/proposal-ledger.jsonl", "control plane"),      # case-folded
    ("brief.json", "the brief"),
    ("../escape.lean", "outside the project"),
])
def test_destination_refusals(project, path, needle):
    root, policy = project
    with pytest.raises(P.ProposalError, match=needle):
        P.apply_proposal(_prop(root, "lean-prover", path, "absent"), root=root, policy=policy)


def test_symlink_into_a_protected_path_is_refused_by_its_target(project):
    root, policy = project
    (root / "reports/kernel/latest.json").write_text("{}")
    (root / "lean/MathAgentsWIP/link.lean").symlink_to(root / "reports/kernel/latest.json")
    with pytest.raises(P.ProposalError, match="protected path"):
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/link.lean", "absent"), root=root, policy=policy)


def test_delete_proposal(project):
    root, policy = project
    target = root / "lean/MathAgentsWIP/Foo.lean"
    delete = {"kind": "delete-proposal", "dispatch": P.issue_dispatch(root, "lean-prover"),
              "path": "lean/MathAgentsWIP/Foo.lean", "base_sha256": _sha(target), "rationale": "obsolete"}
    P.apply_proposal(delete, root=root, policy=policy)
    assert not target.exists() and P.verify_ledger(root) == []


def test_non_utf8_target_and_size_cap_refused(project):
    root, policy = project
    target = root / "lean/MathAgentsWIP/Bin.lean"
    target.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(P.ProposalError, match="not UTF-8"):
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Bin.lean", _sha(target)), root=root, policy=policy)
    policy.size_cap = 4
    with pytest.raises(P.ProposalError, match="cap"):
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent"), root=root, policy=policy)


def test_string_gates_are_not_split_into_characters(project):
    root, policy = project
    with pytest.raises(P.ProposalError, match="list of gate names"):
        P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Bar.lean", "absent", gates="no-sorry"),
                         root=root, policy=policy)


# --- command requests ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["lake", "env", "bash", "-c", "rm -rf /"],
    [PY, "tools/record.py", "add", "x; rm -rf /", "CX-1"],
    [PY, "tools/record.py", "add", "slug"],
    ["bash", "-c", "echo hi"],
])
def test_command_not_on_the_agents_allowlist_is_refused(project, argv):
    root, policy = project
    with pytest.raises(P.ProposalError, match="allowlist"):
        P.run_request(_req(root, "counterexample-hunter", argv), root=root, policy=policy)


def test_commands_get_a_scrubbed_environment(project):
    root, policy = project
    argv = [PY, "-c", "import os; print(os.environ.get('AGENTTEAMS_DECISION_SIGNING_KEY'))"]
    result = P.run_request(_req(root, "lean-prover", argv), root=root, policy=policy)
    assert result["stdout"].strip() == "None"


def test_cwd_is_pinned_and_program_must_not_live_in_the_project(project, tmp_path):
    root, policy = project
    argv = [PY, "-c", "import sys; print(len(sys.stdin.read()))"]
    with pytest.raises(P.ProposalError, match="pinned cwd"):
        P.run_request(_req(root, "lean-prover", argv, cwd="lean"), root=root, policy=policy)
    local = P.load_policy({"agent_policies": {"a": {"commands": [{"prefix": ["./x.sh"], "args": []}]}}})
    with pytest.raises(P.ProposalError, match="relative path"):
        P.run_request(_req(root, "a", ["./x.sh"]), root=root, policy=local)


def test_stdin_content_needs_registered_gates(project):
    root, policy = project
    argv = [PY, "-c", "import sys; print(len(sys.stdin.read()))"]
    ok = P.run_request(_req(root, "lean-prover", argv, stdin_from_content={"path": "Any.txt", "content": "abc"}),
                       root=root, policy=policy)
    assert ok["stdout"].strip() == "3"
    with pytest.raises(P.ProposalError, match="no-sorry refused"):  # gated by the entry, whatever the path
        P.run_request(_req(root, "lean-prover", argv, stdin_from_content={"path": "Any.txt", "content": "sorry"}),
                      root=root, policy=policy)
    other = [PY, "-c", "import os; print(os.environ.get('AGENTTEAMS_DECISION_SIGNING_KEY'))"]
    with pytest.raises(P.ProposalError, match="accepts no stdin"):
        P.run_request(_req(root, "lean-prover", other, stdin_from_content={"path": "a", "content": "b"}),
                      root=root, policy=policy)


def _git(root):
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)


def test_owned_write_allowed_and_undeclared_write_fails(project):
    root, policy = project
    ok = P.run_request(_req(root, "counterexample-hunter", [PY, "tools/record.py", "add", "slug", "CX-3"],
                            expected_writes=["reports/dossiers/x/counterexamples.json"]), root=root, policy=policy)
    assert ok["exit"] == 0 and ok["undeclared_writes"] == []
    writer = [PY, "-c", "import sys; open(sys.argv[1], 'w').write('x')", "stray.txt"]
    with pytest.raises(P.UndeclaredWritesError, match="stray.txt") as exc:
        P.run_request(_req(root, "lean-prover", writer), root=root, policy=policy)
    assert exc.value.result["ran"] is True
    assert P.verify_ledger(root) == []


def test_expected_writes_cannot_hide_a_control_plane_or_unowned_protected_write(project):
    root, policy = project
    writer = [PY, "-c", "import sys; open(sys.argv[1], 'w').write('x')", "stray.txt"]
    for hidden in (".claude/x.md", "reports/kernel/latest.json"):
        with pytest.raises(P.ProposalError):
            P.run_request(_req(root, "lean-prover", writer, expected_writes=[hidden]), root=root, policy=policy)


# --- policy lint --------------------------------------------------------------------------------


@pytest.mark.parametrize("brief, needle", [
    ({"agent_policies": {"a": {"commands": [{"prefix": ["lake", "env"], "args": [".*"]}]}}}, "admits an option"),
    ({"agent_policies": {"a": {"commands": [{"prefix": ["x"], "args": ["[^ ]+"]}]}}}, "admits"),
    ({"agent_policies": {"a": {"commands": [{"prefix": ["bash"], "args": []}]}}}, "shell"),
    ({"proposal_gates": {"g": {"argv": ["sh", "-c", "check {path}"]}}}, "shell"),
    ({"proposal_gates": {"g": {"argv": ["check", "--file={file}"]}}}, "whole arguments"),
    ({"agent_policies": {"a": {"write_scopes": ["./"]}}}, None),
    ({"agent_policies": {"a": {"write_scopes": ["brief.json"]}}}, "covers the brief"),
])
def test_policy_lint(brief, needle):
    if needle is None:
        P.load_policy(brief, brief_rel="brief.json")  # "./" never widens to the root
        return
    with pytest.raises(P.ProposalError, match=needle):
        P.load_policy(brief, brief_rel="brief.json")


# --- ledger -------------------------------------------------------------------------------------


@pytest.mark.parametrize("tamper", ["edit_first", "drop_last", "drop_first", "recompute_unkeyed", "drop_head"])
def test_ledger_tampering_is_detected(project, tamper):
    root, policy = project
    target = root / "lean/MathAgentsWIP/Foo.lean"
    P.apply_proposal(_prop(root, "lean-prover", "lean/MathAgentsWIP/Foo.lean", _sha(target)), root=root, policy=policy)
    ledger, head = root / P.LEDGER_REL, root / P.HEAD_REL
    rows = ledger.read_text().splitlines()
    assert P.verify_ledger(root) == [] and len(rows) == 2  # issue-dispatch + apply-proposal
    if tamper == "edit_first":
        ledger.write_text("\n".join([rows[0].replace("lean-prover", "someone-else")] + rows[1:]) + "\n")
    elif tamper == "drop_last":
        ledger.write_text("\n".join(rows[:-1]) + "\n")
    elif tamper == "drop_first":
        ledger.write_text("\n".join(rows[1:]) + "\n")
    elif tamper == "recompute_unkeyed":
        ledger.write_text(json.dumps({"action": "x", "prev": "", "mac": "0" * 64}) + "\n")
    else:
        head.unlink()
    assert P.verify_ledger(root)


def test_refusals_are_recorded(project):
    root, policy = project
    with pytest.raises(P.ProposalError):
        P.apply_proposal(_prop(root, "lean-prover", ".claude/x.md", "absent"), root=root, policy=policy)
    assert "apply-proposal-refused" in (root / P.LEDGER_REL).read_text() and P.verify_ledger(root) == []


# --- single source of truth with the schemas ----------------------------------------------------


@pytest.mark.parametrize("kind", ["change-proposal", "delete-proposal", "command-request"])
def test_module_keys_match_the_schema_files(kind):
    schema = json.loads((Path(P.__file__).parent / "schemas" / f"{kind}.schema.json").read_text())
    required, optional = P._KEYS[kind]
    assert set(schema["required"]) == required
    assert set(schema["properties"]) == required | optional


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_round_trip(project, tmp_path):
    root, _ = project
    brief = {"project_name": "P", "project_goal": "x" * 20, **_brief()}
    (tmp_path / "brief.json").write_text(json.dumps(brief))
    cli = Path(__file__).resolve().parent.parent / "build_team.py"
    env = dict(os.environ, AGENTTEAMS_PROPOSAL_LEDGER_KEY=KEY)
    nonce = subprocess.run([PY, str(cli), "--issue-dispatch", "--agent", "lean-prover", "--project", str(root)],
                           capture_output=True, text=True, timeout=120, env=env)
    assert nonce.returncode == 0, nonce.stderr
    target = root / "lean/MathAgentsWIP/Foo.lean"
    proposal = {"kind": "change-proposal", "dispatch": nonce.stdout.strip(), "path": "lean/MathAgentsWIP/Foo.lean",
                "base_sha256": _sha(target), "content": "theorem foo : True := by trivial\n", "rationale": "r"}
    (tmp_path / "p.json").write_text(json.dumps(proposal))
    proc = subprocess.run([PY, str(cli), "--apply-proposal", str(tmp_path / "p.json"),
                           "--description", str(tmp_path / "brief.json"), "--project", str(root)],
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["agent"] == "lean-prover"
    proc = subprocess.run([PY, str(cli), "--verify-proposal-ledger", "--project", str(root)],
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0 and "OK" in proc.stdout


# --- second-round review fixes ------------------------------------------------------------------


def test_dispatch_file_holds_no_usable_nonce(project):
    root, _ = project
    nonce = P.issue_dispatch(root, "lean-prover")
    assert nonce not in (root / P.DISPATCH_REL).read_text()


def test_nonce_use_limit_and_dry_runs_do_not_consume(project):
    root, policy = project
    nonce = P.issue_dispatch(root, "lean-prover", max_uses=1)
    proposal = {"kind": "change-proposal", "dispatch": nonce, "path": "lean/MathAgentsWIP/Bar.lean",
                "base_sha256": "absent", "content": "x\n", "rationale": "r"}
    P.apply_proposal(proposal, root=root, policy=policy, dry_run=True)       # does not count
    P.apply_proposal(proposal, root=root, policy=policy)                     # the one use
    second = dict(proposal, path="lean/MathAgentsWIP/Baz.lean")
    with pytest.raises(P.ProposalError, match="its limit"):
        P.apply_proposal(second, root=root, policy=policy)


def test_command_that_erases_the_ledger_is_caught_and_not_chained_onto(project):
    root, _ = project
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c",
                            "import os; [os.remove(p) for p in ('.agentteams/proposal-ledger.jsonl', '.agentteams/proposal-ledger.head')]"],
                            "args": []}]}}})
    request = _req(root, "x", [PY, "-c", "import os; [os.remove(p) for p in ('.agentteams/proposal-ledger.jsonl', '.agentteams/proposal-ledger.head')]"])
    with pytest.raises(P.LedgerTamperedError):
        P.run_request(request, root=root, policy=policy)
    assert not (root / P.LEDGER_REL).exists()           # nothing was re-chained onto the erased ledger
    assert P.verify_ledger(root)                         # dispatch records without a ledger: reported


def test_git_hook_write_is_an_undeclared_write(project):
    root, _ = project
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c",
                            "open('.git/hooks/post-commit', 'w').write('evil')"], "args": []}]}}})
    with pytest.raises(P.UndeclaredWritesError, match=".git/hooks/post-commit"):
        P.run_request(_req(root, "x", [PY, "-c", "open('.git/hooks/post-commit', 'w').write('evil')"]),
                      root=root, policy=policy)


def test_no_git_worktree_refuses_to_run(project, tmp_path):
    _root, policy = project
    root = tmp_path / "nogit"
    root.mkdir()
    argv = [PY, "-c", "import os; print(os.environ.get('AGENTTEAMS_DECISION_SIGNING_KEY'))"]
    with pytest.raises(P.ProposalError, match="not a git worktree"):
        P.run_request(_req(root, "lean-prover", argv), root=root, policy=policy)


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 file names")
def test_non_utf8_filename_does_not_break_the_check(project):
    root, _ = project
    code = "open(b'caf\\xe9.txt', 'w').write('x')"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    with pytest.raises(P.UndeclaredWritesError):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy)


@pytest.mark.parametrize("pattern", ["[a-z].*", ".+\\.lean", "[^ ]+", "\\S+"])
def test_broad_patterns_refused(pattern):
    with pytest.raises(P.ProposalError, match="admits"):
        P.load_policy({"agent_policies": {"a": {"commands": [{"prefix": ["lake", "build"], "args": [pattern]}]}}})


@pytest.mark.parametrize("glob, needle", [("*", "literal project directory"), ("**/x.json", "literal project directory"),
                                          (".claude/**", "control plane"), ("brief.json", "covers the brief")])
def test_writes_globs_linted(glob, needle):
    with pytest.raises(P.ProposalError, match=needle):
        P.load_policy({"agent_policies": {"a": {"commands": [{"prefix": ["x"], "args": [], "writes": [glob]}]}}},
                      brief_rel="brief.json")


# --- third-round review fixes -------------------------------------------------------------------


def test_timed_out_command_is_still_checked(project, monkeypatch):
    root, _ = project
    code = "import time; open('late.txt', 'w').write('x'); time.sleep(30)"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    monkeypatch.setattr(P, "COMMAND_TIMEOUT", 2)
    with pytest.raises(P.UndeclaredWritesError, match="timed out.*late.txt"):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy)
    assert "run-request-timeout" in (root / P.LEDGER_REL).read_text() and P.verify_ledger(root) == []


def test_same_size_rewrite_with_mtime_restored_is_caught(project):
    root, _ = project
    (root / ".claude").mkdir()
    (root / ".claude" / "agent.md").write_text("aaaa")
    code = ("import os; p='.claude/agent.md'; st=os.stat(p); open(p,'w').write('bbbb'); "
            "os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))")
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    with pytest.raises(P.UndeclaredWritesError, match=".claude/agent.md"):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy)


def test_hook_planted_from_a_linked_worktree_is_caught(project, tmp_path):
    root, _ = project
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "-q", str(linked)], cwd=root, check=True)
    hooks = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"], cwd=linked,
                           capture_output=True, text=True, check=True).stdout.strip()
    code = f"open({os.path.join(hooks, 'pre-commit')!r}, 'w').write('evil')"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    with pytest.raises(P.UndeclaredWritesError, match="pre-commit"):
        P.run_request(_req(linked, "x", [PY, "-c", code]), root=linked, policy=policy)


def test_compaction_drops_expired_dispatches_and_their_uses(project):
    root, policy = project
    old = P.issue_dispatch(root, "lean-prover", ttl_hours=-1)
    with pytest.raises(P.ProposalError, match="expired"):
        P.agent_for(root, old)
    P.issue_dispatch(root, "lean-prover")
    rows = (root / P.DISPATCH_REL).read_text().splitlines()
    assert len(rows) == 1  # the expired dispatch is gone; only the fresh one remains


def test_parent_probe_varies_the_leading_segment():
    with pytest.raises(P.ProposalError, match="admits"):
        P.load_policy({"agent_policies": {"a": {"commands": [{"prefix": ["x"], "args": ["[b-z]/\\.\\..*"]}]}}})


# --- final-review fixes -------------------------------------------------------------------------


def test_timeout_kills_the_whole_process_group(project, monkeypatch):
    root, _ = project
    child = "import time; time.sleep(4); open('orphan.txt', 'w').write('x')"
    code = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    monkeypatch.setattr(P, "COMMAND_TIMEOUT", 2)
    with pytest.raises(P.UndeclaredWritesError, match="timed out"):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy)
    time.sleep(4)
    assert not (root / "orphan.txt").exists()


def test_unresolvable_git_path_fails_closed(project, monkeypatch):
    root, _ = project
    real = P._git

    def garbled(r, env, *args):
        if "--git-path" in args:
            return b"--path-format=absolute\nhooks\n"  # what a git without --path-format may print
        return real(r, env, *args)

    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", "pass"], "args": []}]}}})
    monkeypatch.setattr(P, "_git", garbled)
    with pytest.raises(P.ProposalError, match="fail-closed"):
        P.run_request(_req(root, "x", [PY, "-c", "pass"]), root=root, policy=policy)


def test_parent_segment_argument_refused_at_run_time(project):
    root, _ = project
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": ["cat"], "args": ["[c-w]/\\.\\..*"]}]}}})
    with pytest.raises(P.ProposalError, match="allowlist"):
        P.run_request(_req(root, "x", ["cat", "d/../secret"]), root=root, policy=policy, dry_run=True)


@pytest.mark.parametrize("schema, build", [
    ("change-proposal.schema.json", lambda root: _prop(root, "lean-prover", "lean/MathAgentsWIP/Foo.lean", "absent")),
    ("delete-proposal.schema.json", lambda root: {
        "kind": "delete-proposal", "dispatch": P.issue_dispatch(root, "lean-prover"),
        "path": "lean/MathAgentsWIP/Foo.lean", "base_sha256": "0" * 64, "rationale": "obsolete"}),
    ("command-request.schema.json", lambda root: _req(root, "lean-prover", ["lake", "build", "MathAgentsWIP"],
                                                      expected_writes=["lean/MathAgentsWIP/Foo.olean"])),
])
def test_published_schemas_accept_the_artifacts_the_cli_accepts(project, schema, build):
    """The JSON schemas agents are told to follow must accept what the CLI itself takes (drift guard)."""
    jsonschema = pytest.importorskip("jsonschema")
    root, _ = project
    contract = json.loads((Path(P.__file__).parent / "schemas" / schema).read_text(encoding="utf-8"))
    jsonschema.validate(build(root), contract)
    bad = {**build(root), "dispatch": "not-a-nonce"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, contract)


def test_empty_stdin_gates_refused():
    with pytest.raises(P.ProposalError, match="stdin_gates is empty"):
        P.load_policy({"agent_policies": {"a": {"commands": [{"prefix": ["x"], "args": [], "stdin_gates": []}]}}})


# --- P5a: per-entry timeout and measurement ------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 7201, "600", 1.5])
def test_entry_timeout_linted(bad):
    with pytest.raises(P.ProposalError, match="timeout"):
        P.load_policy({"agent_policies": {"a": {"commands": [{"prefix": ["x"], "args": [], "timeout": bad}]}}})


def test_entry_timeout_applies_and_duration_is_recorded(project, monkeypatch):
    root, _ = project
    code = "import time; time.sleep(3)"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": [],
                                                                    "timeout": 1}]}}})
    with pytest.raises(P.UndeclaredWritesError, match="timed out after 1s"):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy)
    fast = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", "pass"], "args": []}]}}})
    result = P.run_request(_req(root, "x", [PY, "-c", "pass"]), root=root, policy=fast)
    assert isinstance(result["duration_ms"], int)
    assert '"duration_ms"' in (root / P.LEDGER_REL).read_text()


# --- R1 (mcp-mediated-agent-writes): uses counted after validation, output caps, MCP timeout cap -------------


def test_refused_artifacts_do_not_spend_nonce_uses(project):
    """An agent iterating on a rejected write isn't locked out: only artifacts that act count a use."""
    root, policy = project
    nonce = P.issue_dispatch(root, "lean-prover", max_uses=2)
    target = root / "lean/MathAgentsWIP/Foo.lean"
    bad = {"kind": "change-proposal", "dispatch": nonce, "path": "lean/MathAgentsWIP/Foo.lean",
           "base_sha256": "0" * 64, "content": "x\n", "rationale": "stale base on purpose"}
    for _ in range(5):
        with pytest.raises(P.ProposalError, match="stale base"):
            P.apply_proposal(bad, root=root, policy=policy)
    good = {**bad, "base_sha256": _sha(target), "content": "theorem foo : True := by trivial\n"}
    assert P.apply_proposal(good, root=root, policy=policy)["written"]
    good2 = {**good, "base_sha256": _sha(target), "content": "theorem foo : True := trivial\n"}
    assert P.apply_proposal(good2, root=root, policy=policy)["written"]
    with pytest.raises(P.ProposalError, match="used 2 times"):
        P.apply_proposal({**good2, "base_sha256": _sha(target)}, root=root, policy=policy)


def test_last_use_cannot_be_spent_twice(project):
    """Validation no longer consumes, so the cap is re-checked at the moment of acting."""
    root, policy = project
    nonce = P.issue_dispatch(root, "lean-prover", max_uses=1)
    target = root / "lean/MathAgentsWIP/Foo.lean"
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "lean/MathAgentsWIP/Foo.lean",
           "base_sha256": _sha(target), "content": "theorem foo : True := by trivial\n", "rationale": "r"}
    assert P.apply_proposal(art, root=root, policy=policy)["written"]
    with pytest.raises(P.ProposalError, match="used 1 times"):
        P.apply_proposal({**art, "base_sha256": _sha(target)}, root=root, policy=policy)


def test_command_output_is_capped_and_flagged(project, monkeypatch):
    root, _ = project
    code = "import sys; sys.stdout.write('y' * 5000)"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    monkeypatch.setattr(P, "MAX_OUTPUT_BYTES", 1000)
    result = P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy)
    assert len(result["stdout"]) == 1000 and result["truncated"] is True


def test_short_output_is_not_flagged(project):
    root, policy = project
    argv = [PY, "-c", "import os; print(os.environ.get('AGENTTEAMS_DECISION_SIGNING_KEY'))"]
    assert P.run_request(_req(root, "lean-prover", argv), root=root, policy=policy)["truncated"] is False


def test_timeout_cap_bounds_the_entrys_timeout(project):
    root, _ = project
    code = "import time; time.sleep(30)"
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": [],
                                                                    "timeout": 600}]}}})
    started = time.monotonic()
    with pytest.raises(P.UndeclaredWritesError, match="timed out after 2s"):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy, timeout_cap=2)
    assert time.monotonic() - started < 20


def test_attempts_are_bounded_even_though_refusals_are_free(project, monkeypatch):
    """@security R1 condition 1: refusals and dry runs don't spend uses, but they do spend a wider attempt budget."""
    root, policy = project
    monkeypatch.setattr(P, "DISPATCH_MAX_ATTEMPTS", 3)
    nonce = P.issue_dispatch(root, "lean-prover")
    bad = {"kind": "change-proposal", "dispatch": nonce, "path": "lean/MathAgentsWIP/Foo.lean",
           "base_sha256": "0" * 64, "content": "x\n", "rationale": "stale"}
    for _ in range(3):
        with pytest.raises(P.ProposalError, match="stale base"):
            P.apply_proposal(bad, root=root, policy=policy)
    with pytest.raises(P.ProposalError, match="3 attempts"):
        P.apply_proposal(bad, root=root, policy=policy, dry_run=True)


def test_a_grandchild_holding_the_pipes_cannot_hang_the_runner(project, monkeypatch):
    """@security R1 condition 2: after the group kill, draining the pipes is bounded."""
    root, _ = project
    code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "start_new_session=True); time.sleep(60)")
    policy = P.load_policy({"agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": []}]}}})
    monkeypatch.setattr(P, "POST_KILL_DRAIN_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(P.UndeclaredWritesError, match="timed out"):
        P.run_request(_req(root, "x", [PY, "-c", code]), root=root, policy=policy, timeout_cap=1)
    assert time.monotonic() - started < 30
