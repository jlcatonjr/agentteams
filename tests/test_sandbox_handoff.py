"""Tests for the sandbox operator handoff: agentteams.sandbox_handoff, its reference, and its wiring into
the orchestrator's fenced workflows and @git-operations."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import sandbox_handoff as sh

_TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "universal"
_BASH = shutil.which("bash")


def _spec(**over: object) -> dict:
    data: dict = {
        "slug": "push-feat-x",
        "purpose": "Push feat/x",
        "blocked_by": "sandbox network allowlist excludes github.com",
        "steps": [{"title": "say hi", "run": "echo hi > out.txt"}],
        "verify": [{"title": "check", "run": "test -s out.txt"}],
        "requires": ["bash"],
        "touches": ["out.txt"],
    }
    data.update(over)
    return data


def _run(command: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    base = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(cwd), "AGENTTEAMS_HANDOFF_YES": "1"}
    base.update(env)
    return subprocess.run([_BASH, "-c", command], cwd=cwd, env=base, capture_output=True, text=True,
                          check=False, stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def test_detect_reports_claude_and_codex_markers():
    info = sh.detect_sandbox({"SANDBOX_RUNTIME": "1", "CODEX_SANDBOX": "seatbelt",
                              "CODEX_SANDBOX_NETWORK_DISABLED": "1"})
    assert info.sandboxed
    assert info.kinds == ("claude-code", "codex")
    assert info.network_disabled


def test_detect_without_markers_is_not_a_claim_of_no_sandbox():
    info = sh.detect_sandbox({})
    assert not info.sandboxed and info.kinds == ()


# ---------------------------------------------------------------------------
# spec validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("over, needle", [
    ({"slug": "Bad Slug"}, "slug"),
    ({"purpose": ""}, "purpose"),
    ({"blocked_by": ""}, "blocked_by"),
    ({"steps": []}, "steps"),
    ({"destructive": True}, "clearance"),
    ({"cwd": "../elsewhere"}, "cwd"),
    ({"requires": ["gh; rm"]}, "requires"),
    ({"purpose": "two\nlines"}, "single lines"),
])
def test_invalid_specs_are_refused(over, needle):
    with pytest.raises(sh.HandoffError, match=needle):
        sh.spec_from_dict(_spec(**over))


@pytest.mark.parametrize("run", [
    "curl -H 'Authorization: token ghp_" + "a" * 36 + "' https://api.github.com",
    "export API_KEY=abcd1234efgh5678",
    "aws configure set aws_access_key_id AKIA" + "B" * 16,
])
def test_credential_shaped_content_is_refused(run):
    with pytest.raises(sh.HandoffError, match="credential"):
        sh.spec_from_dict(_spec(steps=[{"title": "x", "run": run}]))


def test_environment_variable_references_are_allowed():
    spec = sh.spec_from_dict(_spec(steps=[{"title": "x", "run": 'gh auth login --with-token <<< "$GITHUB_TOKEN"'},
                                          {"title": "y", "run": "export TOKEN=$GITHUB_TOKEN"}]))
    assert len(spec.steps) == 2


@pytest.mark.parametrize("token", [".claude/settings.json", "authorized-managers.txt",
                                   "AGENTTEAMS_MANAGEMENT_SIGNING_KEY", "enforcement-integrity.json"])
def test_trust_roots_are_refused_in_commands_and_touches(token):
    with pytest.raises(sh.HandoffError, match="trust root"):
        sh.spec_from_dict(_spec(steps=[{"title": "x", "run": f"cp a {token}"}]))
    with pytest.raises(sh.HandoffError, match="trust root"):
        sh.spec_from_dict(_spec(touches=[token]))


def test_destructive_with_clearance_forces_confirmation(tmp_path):
    spec = sh.spec_from_dict(_spec(destructive=True, clearance="security-decisions row 7", confirm=False))
    script, log = sh.handoff_paths(tmp_path, spec.slug, "2026-W43")
    text = sh.render_script(spec, root=tmp_path, log=log, script=script)
    assert "# destructive: YES — clearance: security-decisions row 7" in text
    assert "Proceed? [y/N]" in text


def test_confirmation_is_off_by_default_and_can_be_turned_on(tmp_path):
    # Running a non-destructive handoff is the operator's approval: no prompt unless asked for.
    script, log = sh.handoff_paths(tmp_path, "push-feat-x", "2026-W43")
    default = sh.render_script(sh.spec_from_dict(_spec()), root=tmp_path, log=log, script=script)
    on = sh.render_script(sh.spec_from_dict(_spec(confirm=True)), root=tmp_path, log=log, script=script)
    assert "Proceed? [y/N]" not in default and "Proceed? [y/N]" in on
    assert sh.HandoffSpec(slug="s", purpose="p", blocked_by="b", steps=()).confirm is False


def test_destructive_without_confirm_key_still_prompts_once(tmp_path):
    spec = sh.spec_from_dict(_spec(destructive=True, clearance="security-decisions row 7"))
    script, log = sh.handoff_paths(tmp_path, spec.slug, "2026-W43")
    text = sh.render_script(spec, root=tmp_path, log=log, script=script)
    assert text.count("Proceed? [y/N]") == 1


# ---------------------------------------------------------------------------
# writing and running
# ---------------------------------------------------------------------------

def test_write_places_script_in_week_folder_with_header(tmp_path):
    result = sh.write_handoff(sh.spec_from_dict(_spec()), tmp_path, week="2026-W43")
    assert result.script == (tmp_path / "tmp/by-week/2026-W43/push-feat-x.handoff.sh").resolve()
    assert result.log.name == "push-feat-x.handoff.log"
    text = result.script.read_text()
    assert text.splitlines()[:2] == ["#!/usr/bin/env bash", sh.MARKER]
    for field in ("# purpose:", "# blocked-by:", "# destructive: no", "# touches:", "set -euo pipefail"):
        assert field in text
    assert result.script.stat().st_mode & 0o111
    assert result.sha256 in result.command and str(result.script) in result.command


def test_write_refuses_to_clobber_without_overwrite(tmp_path):
    spec = sh.spec_from_dict(_spec())
    sh.write_handoff(spec, tmp_path, week="2026-W43")
    with pytest.raises(sh.HandoffError, match="already exists"):
        sh.write_handoff(spec, tmp_path, week="2026-W43")
    sh.write_handoff(spec, tmp_path, week="2026-W43", overwrite=True)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_write_rejects_a_script_that_fails_bash_syntax_check(tmp_path):
    spec = sh.spec_from_dict(_spec(steps=[{"title": "broken", "run": "if then fi ("}]))
    with pytest.raises(sh.HandoffError, match="bash -n"):
        sh.write_handoff(spec, tmp_path, week="2026-W43")
    assert not (tmp_path / "tmp/by-week/2026-W43/push-feat-x.handoff.sh").exists()


@pytest.mark.skipif(_BASH is None or not (shutil.which("shasum") or shutil.which("sha256sum")),
                    reason="bash and a sha256 tool are required")
def test_complete_command_runs_the_script_and_status_reads_ok(tmp_path):
    result = sh.write_handoff(sh.spec_from_dict(_spec()), tmp_path, week="2026-W43")
    assert sh.handoff_status(result.script).state == "not-run"
    proc = _run(result.command, tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (tmp_path / "out.txt").read_text().strip() == "hi"
    status = sh.handoff_status(result.script)
    assert status.state == "ok", status
    assert "$ echo hi > out.txt" in result.log.read_text()


@pytest.mark.skipif(_BASH is None or not (shutil.which("shasum") or shutil.which("sha256sum")),
                    reason="bash and a sha256 tool are required")
def test_failure_reports_step_and_rc(tmp_path):
    spec = sh.spec_from_dict(_spec(steps=[{"title": "ok", "run": "true"}, {"title": "boom", "run": "exit 7"}],
                                   verify=[]))
    result = sh.write_handoff(spec, tmp_path, week="2026-W43")
    proc = _run(result.command, tmp_path)
    assert proc.returncode != 0
    status = sh.handoff_status(result.log)
    assert (status.state, status.rc, status.step) == ("failed", 7, 2)


@pytest.mark.skipif(_BASH is None or not (shutil.which("shasum") or shutil.which("sha256sum")),
                    reason="bash and a sha256 tool are required")
def test_command_refuses_a_script_edited_after_it_was_written(tmp_path):
    result = sh.write_handoff(sh.spec_from_dict(_spec()), tmp_path, week="2026-W43")
    result.script.write_text(result.script.read_text() + "\necho injected\n")
    proc = _run(result.command, tmp_path)
    assert proc.returncode != 0
    assert "changed since it was written" in proc.stderr
    assert not (tmp_path / "out.txt").exists()


@pytest.mark.skipif(_BASH is None or not (shutil.which("shasum") or shutil.which("sha256sum")),
                    reason="bash and a sha256 tool are required")
def test_script_refuses_to_run_inside_a_sandbox(tmp_path):
    result = sh.write_handoff(sh.spec_from_dict(_spec()), tmp_path, week="2026-W43")
    proc = _run(result.command, tmp_path, SANDBOX_RUNTIME="1")
    assert proc.returncode == 64
    assert "outside the agent sandbox" in proc.stderr
    assert not (tmp_path / "out.txt").exists()


def test_status_flags_a_log_from_a_different_script_version(tmp_path):
    result = sh.write_handoff(sh.spec_from_dict(_spec()), tmp_path, week="2026-W43")
    result.log.write_text("HANDOFF-SCRIPT-SHA256: " + "0" * 64 + "\nHANDOFF-RESULT: ok\n")
    assert sh.handoff_status(result.script).state == "stale"


def test_status_without_result_line_is_incomplete(tmp_path):
    result = sh.write_handoff(sh.spec_from_dict(_spec()), tmp_path, week="2026-W43")
    result.log.write_text("partial output\n")
    assert sh.handoff_status(result.script).state == "incomplete"


def test_cli_write_and_status(tmp_path, capsys):
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(_spec()))
    assert sh.main(["write", str(spec_file), "--root", str(tmp_path), "--week", "2026-W43", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"].endswith(f"bash {out['script']}")
    assert sh.main(["status", out["script"]]) == 1  # not-run
    assert "not-run" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_spec(destructive=True)))
    assert sh.main(["write", str(bad), "--root", str(tmp_path)]) == 1


# ---------------------------------------------------------------------------
# template wiring
# ---------------------------------------------------------------------------

def _between(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


def test_reference_template_is_fenced_and_names_the_contract():
    text = (_TEMPLATES / "sandbox-handoff.reference.template.md").read_text()
    body = _between(text, "<!-- AGENTTEAMS:BEGIN sandbox_handoff", "<!-- AGENTTEAMS:END sandbox_handoff")
    for needle in ("python -m agentteams.sandbox_handoff write", "complete command", "proposal runner",
                   "is a stop", "C-5", "C-4", "needs_user_review=yes", "`operator`"):
        assert needle in body, needle


def test_orchestrator_sub_protocol_and_augmentations_are_inside_the_workflows_fence():
    orch = (_TEMPLATES / "orchestrator.template.md").read_text()
    fenced = _between(orch, "<!-- AGENTTEAMS:BEGIN available_workflows", "<!-- AGENTTEAMS:END available_workflows")
    assert "### Sandbox Operator Handoff" in fenced
    assert "**Sandbox augmentations of the numbered workflows.**" in fenced
    assert "**Sandbox branch:**" in _between(fenced, "### Workflow 0: Request Intake", "### Workflow 0A:")
    assert "7c. **Pending handoffs (closeout gate).**" in fenced
    assert "references/sandbox-handoff.reference.md" in fenced
    # A named sub-protocol, not a numbered workflow that could collide with a project's own numbering.
    assert "Workflow 15" not in orch


def test_git_operations_hands_off_refused_operations():
    text = (_TEMPLATES / "git-operations.template.md").read_text()
    assert "10. **Sandbox refusals become operator handoffs.**" in text
    assert "## Sandbox Augmentation" in text
    assert "references/sandbox-handoff.reference.md" in text


# ---------------------------------------------------------------------------
# audit-driven hardening (2026-10-21 code-hygiene + conflict-auditor pass)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("over, needle", [
    ({"destructive": "true"}, "boolean"),
    ({"confirm": "no"}, "boolean"),
    ({"destrucitve": True}, "unknown spec key"),
])
def test_mistyped_or_misspelled_keys_are_refused(over, needle):
    with pytest.raises(sh.HandoffError, match=needle):
        sh.spec_from_dict(_spec(**over))


@pytest.mark.parametrize("target", ["CLAUDE.md", ".Claude/Settings.json", "references/instruction-authority.reference.md",
                                    "AGENTS.md", ".github/copilot-instructions.md"])
def test_constitution_and_case_variants_are_trust_roots(target):
    with pytest.raises(sh.HandoffError, match="trust root"):
        sh.spec_from_dict(_spec(steps=[{"title": "x", "run": f"echo hi > {target}"}]))


def test_clearance_may_cite_the_security_decision_log():
    spec = sh.spec_from_dict(_spec(destructive=True, clearance="references/security-decisions.log.csv row 12"))
    assert spec.destructive


def test_status_refuses_a_path_that_is_not_a_handoff(tmp_path):
    with pytest.raises(sh.HandoffError):
        sh.handoff_status(tmp_path / "notes.txt")


@pytest.mark.skipif(_BASH is None or not (shutil.which("shasum") or shutil.which("sha256sum")),
                    reason="bash and a sha256 tool are required")
def test_result_line_survives_output_without_trailing_newline_and_odd_purpose(tmp_path):
    spec = sh.spec_from_dict(_spec(purpose='ends with a backslash \\ and a "quote" and $HOME',
                                   steps=[{"title": "no newline", "run": "printf 'partial'"}], verify=[]))
    result = sh.write_handoff(spec, tmp_path, week="2026-W43")
    proc = _run(result.command, tmp_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert 'ends with a backslash \\ and a "quote" and $HOME' in proc.stdout
    assert sh.handoff_status(result.script).state == "ok"


def test_parallel_plan_isolates_operator_steps():
    from agentteams.parallel_plan import PlanStep, compute_waves

    steps = [PlanStep(step="1", agent="primary-producer", action="edit", inputs="", outputs="docs/a.md"),
             PlanStep(step="2", agent="operator", action="Run handoff push", inputs="", outputs="docs/b.md")]
    schedule = compute_waves(steps)
    assert "operator handoff" in schedule.reasons["2"]
    assert all(len(w) == 1 for w in schedule.waves if "2" in w)


def test_reference_template_limits_scripts_to_refused_work_and_running_as_approval():
    ref = (Path(__file__).parent.parent / "agentteams" / "templates" / "universal"
           / "sandbox-handoff.reference.template.md").read_text(encoding="utf-8")
    assert "**only** the operations the sandbox refused" in ref
    assert "**Running the script is the operator's approval**" in ref
    assert "`confirm` (default `false`" in ref


@pytest.mark.parametrize("cmd", [
    "rm -rf build", "git push --force origin x", "git push origin --delete feat/x", "git push origin :feat/x",
    "git reset --hard origin/main", "git clean -fdx", "git branch -D feat/x", "git worktree remove ../wt",
    "git checkout -- .", "git rebase main", "gh repo delete me/x --yes", "find . -name '*.pyc' -delete",
    "agentteams --self --update --overwrite --yes", "agentteams --branch-post-merge feat/x --apply",
    "cd x && rm out.txt", "git -C ../wt push --force origin x", "git -C ../wt reset --hard HEAD~1",
    "git push -fu origin x", "git push --mirror backup", "bash -c \"rm -rf build\"", "/bin/rm x",
    "gh pr merge 5 --squash --delete-branch", "gh api -X DELETE repos/me/x/git/refs/heads/y",
    "git switch main --discard-changes", "git checkout -f main", "python -m agentteams --project . --bridge-refresh",
])
def test_destructive_command_without_destructive_flag_is_refused(cmd):
    # Running a non-destructive script is unprompted, so destructiveness is derived from the commands.
    with pytest.raises(sh.HandoffError, match="destructive"):
        sh.spec_from_dict(_spec(steps=[{"title": "x", "run": cmd}]))
    spec = sh.spec_from_dict(_spec(steps=[{"title": "x", "run": cmd}], destructive=True, clearance="row 9"))
    assert sh.destructive_commands(spec)


@pytest.mark.parametrize("cmd", [
    "git push -u origin feat/x", "gh pr create --fill --base main", "git worktree add ../wt feat/x",
    "agentteams --self --update --merge --yes", "python -m pip install pkg==1.2.3", "git branch -vv",
    "echo format > out.txt", "git fetch --prune origin", "git -C ../wt push -u origin feat/x",
    "gh pr view feat/x --json mergeable,mergeStateStatus", "git checkout -b feat/y",
    "agentteams --branch-inventory", "git branch -vv --list",
])
def test_ordinary_handoff_commands_are_not_flagged(cmd):
    assert sh.destructive_commands(sh.spec_from_dict(_spec(steps=[{"title": "x", "run": cmd}]))) == []
