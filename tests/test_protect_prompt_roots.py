"""Follow-up #8 phase 2 (P4b): opt-in prompt-root prevention (``protect_prompt_roots``).

Covers the Claude block (unconditional ``Edit`` rules, present-only ``denyWrite``), the launcher's
``--protect-prompt-roots`` (``--check`` listing and a raw-bwrap EROFS probe), the goose Linux
runner wiring, the RELAXATION notice, the generation advisory, and that the flag off (false or
absent) changes nothing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.analyze import build_manifest
from agentteams.cli import write_root_policy as policy
from agentteams.frameworks._goose_sandbox_emit import _build_linux_goose_runner
from agentteams.frameworks._prompt_root_protect import (
    ALL_PROMPT_ROOT_EDIT_RULES,
    PROMPT_ROOT_DIRS,
    PROMPT_ROOT_FILES,
    PROMPT_ROOT_PRESENT_ONLY_DIRS,
    present_prompt_roots,
    prompt_root_edit_rules,
)
from agentteams.frameworks._sandbox_emit import _inject_sandbox_block
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.claude import ClaudeAdapter

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
EXAMPLE = (REPO / "agentteams" / "templates" / "universal" / "hooks" / "settings.hooks.example.json").read_text()
LINUX_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def _project(tmp_path: Path, *, files=(), dirs=()) -> Path:
    p = (tmp_path / "proj").resolve()
    p.mkdir()
    for d in dirs:
        (p / d).mkdir(parents=True)
    for f in files:
        (p / f).parent.mkdir(parents=True, exist_ok=True)
        (p / f).write_text("x\n")
    return p


def _render(project: Path, **kw) -> dict:
    return json.loads(_inject_sandbox_block(EXAMPLE, None, project_root=str(project), **kw))


def test_flag_on_adds_edit_rules_and_present_only_deny_write(tmp_path):
    p = _project(tmp_path, files=[".github/copilot-instructions.md", "AGENTS.md"],
                 dirs=[".github/prompts", ".github/workflows"])
    data = _render(p, protect_prompt_roots=True)
    deny = data["permissions"]["deny"]
    for rule in [f"Edit(/{f})" for f in PROMPT_ROOT_FILES] + [f"Edit(/{d}/**)" for d in PROMPT_ROOT_DIRS]:
        assert rule in deny, rule
    assert "Edit(/.agentteams/**)" not in deny  # present-only
    assert not any("workflows" in r for r in deny)
    dw = data["sandbox"]["filesystem"]["denyWrite"]
    added = [e for e in dw if e in (*PROMPT_ROOT_FILES, *PROMPT_ROOT_DIRS, *PROMPT_ROOT_PRESENT_ONLY_DIRS)]
    assert added == [".github/copilot-instructions.md", "AGENTS.md", ".github/prompts"]
    assert ".github/workflows" not in dw and ".github" not in dw
    assert any("protect_prompt_roots: true" in line for line in data["_comment"])


def test_present_only_root_and_symlinks(tmp_path):
    p = _project(tmp_path, dirs=[".agentteams", "real"])
    (p / "CLAUDE.md").symlink_to(p / "real")
    (p / ".github").symlink_to(p / "real")
    (p / "real" / "copilot-instructions.md").write_text("x")
    assert present_prompt_roots(str(p)) == (".agentteams",)
    assert "Edit(/.agentteams/**)" in prompt_root_edit_rules(str(p))
    assert present_prompt_roots(None) == ()
    assert set(prompt_root_edit_rules(str(p))) <= ALL_PROMPT_ROOT_EDIT_RULES


def test_flag_off_is_byte_identical_to_absent(tmp_path):
    p = _project(tmp_path, files=["AGENTS.md", ".github/copilot-instructions.md"], dirs=[".codex"])
    absent = _inject_sandbox_block(EXAMPLE, None, project_root=str(p))
    assert _inject_sandbox_block(EXAMPLE, None, project_root=str(p), protect_prompt_roots=False) == absent
    assert not any(r in ALL_PROMPT_ROOT_EDIT_RULES for r in json.loads(absent)["permissions"]["deny"])
    base = {"project_name": "X", "framework": "claude", "privilege_profile": "confined",
            PROJECT_ROOT_KEY: p}
    out = {k: dict(ClaudeAdapter().extra_output_files({**base, **extra}))
           for k, extra in (("absent", {}), ("false", {"protect_prompt_roots": False}),
                            ("string", {"protect_prompt_roots": "true"}))}
    assert out["absent"] == out["false"] == out["string"]


def test_adapter_threads_the_flag(tmp_path):
    p = _project(tmp_path, files=["AGENTS.md"])
    m = {"project_name": "X", "framework": "claude", "privilege_profile": "confined",
         PROJECT_ROOT_KEY: p, "protect_prompt_roots": True}
    example = json.loads(dict(ClaudeAdapter().extra_output_files(m))["../settings.hooks.example.json"])
    assert "AGENTS.md" in example["sandbox"]["filesystem"]["denyWrite"]
    assert "Edit(/AGENTS.md)" in example["permissions"]["deny"]


def test_brief_flag_reaches_the_manifest_only_when_true():
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    assert "protect_prompt_roots" not in build_manifest(brief)
    assert "protect_prompt_roots" not in build_manifest({**brief, "protect_prompt_roots": False})
    assert build_manifest({**brief, "protect_prompt_roots": True})["protect_prompt_roots"] is True
    schema = json.loads((REPO / "agentteams/schemas/project-description.schema.json").read_text())
    assert schema["properties"]["protect_prompt_roots"]["type"] == "boolean"


def test_goose_runner_passes_the_flag_only_when_opted_in(tmp_path):
    m = {"project_name": "X", "privilege_profile": "confined", PROJECT_ROOT_KEY: tmp_path.resolve()}
    assert "--protect-prompt-roots" not in _build_linux_goose_runner(m)
    assert _build_linux_goose_runner({**m, "protect_prompt_roots": False}) == _build_linux_goose_runner(m)
    assert " --protect-prompt-roots " in _build_linux_goose_runner({**m, "protect_prompt_roots": True})


def test_launcher_list_is_locked_to_the_emitter():
    text = LAUNCHER.read_text(encoding="utf-8")
    body = re.search(r"PROMPT_ROOTS_REL=\(([^)]*)\)", text).group(1).split()
    assert body == [*PROMPT_ROOT_FILES, *PROMPT_ROOT_DIRS, *PROMPT_ROOT_PRESENT_ONLY_DIRS]
    assert ".github/workflows" not in body
    assert text.isascii()


@pytest.mark.skipif(not LINUX_BWRAP, reason="Linux + bubblewrap only (--check builds the bwrap argv)")
def test_launcher_check_lists_present_roots_and_tolerates_none(tmp_path):
    p = _project(tmp_path, files=[".github/copilot-instructions.md", "AGENTS.md"], dirs=[".github/prompts"])
    res = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--protect-prompt-roots", "--check",
                          "--", "true"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    line = next(ln for ln in res.stdout.splitlines() if ln.strip().startswith("prompt-roots (ro)"))
    for rel in (".github/copilot-instructions.md", "AGENTS.md", ".github/prompts"):
        assert str(p / rel) in line
        assert f"--ro-bind {p / rel} {p / rel}" in res.stdout
    assert ".goosehints" not in line
    empty = (tmp_path / "empty").resolve()
    empty.mkdir()
    res = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(empty), "--protect-prompt-roots", "--check",
                          "--", "true"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "prompt-roots (ro) : <none present>" in res.stdout
    off = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--check", "--", "true"],
                         capture_output=True, text=True)
    assert off.returncode == 0 and "prompt-roots" not in off.stdout and "AGENTS.md" not in off.stdout


@pytest.mark.skipif(not LINUX_BWRAP, reason="Linux + bubblewrap only")
def test_launcher_refuses_a_symlinked_prompt_root(tmp_path):
    p = _project(tmp_path, files=["AGENTS.md"])
    (p / "CLAUDE.md").symlink_to(p / "AGENTS.md")
    res = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--protect-prompt-roots", "--check",
                          "--", "true"], capture_output=True, text=True)
    assert res.returncode == 2 and "symlink" in res.stderr


_PROBE = r"""
import errno, os, sys
os.chdir(sys.argv[1])
def attempt(label, fn):
    try:
        fn(); print(label, "OK")
    except OSError as e:
        print(label, errno.errorcode.get(e.errno, e.errno))
attempt("copilot", lambda: open(".github/copilot-instructions.md", "w").write("pwned"))
attempt("agents", lambda: open("AGENTS.md", "a").write("pwned"))
attempt("prompt", lambda: open(".github/prompts/new.prompt.md", "w").write("pwned"))
attempt("workflow", lambda: open(".github/workflows/ci.yml", "w").write("x"))
attempt("normal", lambda: open("src.txt", "w").write("x"))
attempt("rename", lambda: os.rename(".github", ".github.moved"))
"""


@pytest.mark.skipif(not LINUX_BWRAP, reason="Linux + bubblewrap only")
def test_launcher_mechanism_probe_gives_erofs(tmp_path):
    p = _project(tmp_path, files=[".github/copilot-instructions.md", "AGENTS.md"],
                 dirs=[".github/prompts", ".github/workflows"])
    res = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--protect-prompt-roots", "--",
                          sys.executable, "-c", _PROBE, str(p)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = dict(line.split(" ", 1) for line in res.stdout.splitlines())
    assert out["copilot"] == out["agents"] == out["prompt"] == "EROFS", out
    assert out["workflow"] == "OK" and out["normal"] == "OK", out  # workflows stay writable
    assert out["rename"] == "EBUSY", out
    assert (p / ".github/copilot-instructions.md").read_text() == "x\n"
    unprotected = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--", sys.executable, "-c",
                                  _PROBE, str(p)], capture_output=True, text=True)
    assert "copilot OK" in unprotected.stdout  # the flag, not something else, made it read-only


def _settings(project: Path, deny: list[str]) -> None:
    (project / ".claude").mkdir(exist_ok=True)
    (project / ".claude" / "settings.json").write_text(json.dumps(
        {"sandbox": {"enabled": True, "filesystem": {"allowWrite": ["."]}}, "permissions": {"deny": deny}}))


def _enforce(project: Path, manifest: dict, framework: str = "claude", confined: bool = True) -> None:
    policy.begin(manifest, project)
    policy.enforce(manifest, framework_id=framework, brief_roots=[], grant_roots=[],
                   coordination_roots=[], accepted=None, confined=confined)


def test_relaxation_notice_on_true_to_false(tmp_path, capsys):
    p = _project(tmp_path)
    _settings(p, ["Read(~/.config/agentteams/keys/**)", *prompt_root_edit_rules(str(p))])
    _enforce(p, {"protect_prompt_roots": True})
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err
    _enforce(p, {"protect_prompt_roots": False})
    err = capsys.readouterr().err
    assert "SANDBOX RELAXATION: prompt-root protection removed" in err
    assert "Edit(/AGENTS.md)" in err and "Edit(/.github/prompts/**)" in err
    _enforce(p, {})  # flag removed from the brief
    assert "SANDBOX RELAXATION: prompt-root protection removed" in capsys.readouterr().err
    _enforce(p, {"protect_prompt_roots": True}, confined=False)  # sandbox turned off
    assert "SANDBOX RELAXATION" in capsys.readouterr().err
    _enforce(p, {}, framework="goose")  # a goose render does not touch .claude/settings.json
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err


def test_no_relaxation_notice_without_live_prompt_rules(tmp_path, capsys):
    p = _project(tmp_path)
    _enforce(p, {})
    _settings(p, ["Read(~/.config/agentteams/keys/**)", "Edit(/.codex/config.toml)"])
    _enforce(p, {"protect_prompt_roots": False})
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err


def test_generation_advisory(tmp_path, capsys):
    from agentteams.cli.generate_helpers import _advise_protect_prompt_roots

    p = _project(tmp_path)
    m = {"framework": "claude", "privilege_profile": "confined"}
    assert _advise_protect_prompt_roots(m, p, (".codex",)) is True
    assert "protect_prompt_roots" in capsys.readouterr().err
    assert _advise_protect_prompt_roots({**m, "protect_prompt_roots": True}, p, (".codex",)) is False
    assert _advise_protect_prompt_roots(m, p, ()) is False  # no non-Claude team
    assert _advise_protect_prompt_roots({**m, "privilege_profile": "cooperative"}, p, (".codex",)) is False
    _settings(p, [])  # a merged, enabled Claude sandbox; this run generates a goose team
    assert _advise_protect_prompt_roots({"framework": "goose"}, p, ()) is True


def _generate(project: Path, monkeypatch, brief_extra: dict) -> int:
    import build_team
    from agentteams.cli import security_gate

    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *a, **k: None)
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    brief.update({"privilege_profile": "confined", **brief_extra})
    path = project / "brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    return build_team.main(["--description", str(path), "--framework", "claude", "--output",
                            str(project / ".claude/agents"), "--yes", "--no-scan", "--security-offline"])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="claude sandbox emitted on linux")
def test_generation_end_to_end(tmp_path, monkeypatch):
    p = _project(tmp_path, files=["AGENTS.md"])
    assert _generate(p, monkeypatch, {"protect_prompt_roots": True}) == 0
    on = json.loads((p / ".claude/settings.hooks.example.json").read_text())
    assert "AGENTS.md" in on["sandbox"]["filesystem"]["denyWrite"]
    assert "Edit(/.github/copilot-instructions.md)" in on["permissions"]["deny"]
    assert _generate(p, monkeypatch, {"protect_prompt_roots": False}) == 0
    off = json.loads((p / ".claude/settings.hooks.example.json").read_text())
    assert not any(r in ALL_PROMPT_ROOT_EDIT_RULES for r in off["permissions"]["deny"])
    assert "AGENTS.md" not in off["sandbox"]["filesystem"]["denyWrite"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="claude sandbox emitted on linux")
def test_generation_relaxation_notice_reads_the_project_settings(tmp_path, monkeypatch, capsys):
    p = _project(tmp_path, files=["AGENTS.md"])
    assert _generate(p, monkeypatch, {"protect_prompt_roots": True}) == 0
    example = (p / ".claude/settings.hooks.example.json").read_text()
    (p / ".claude/settings.json").write_text(example)  # the operator merged it
    capsys.readouterr()
    assert _generate(p, monkeypatch, {}) == 0
    assert "SANDBOX RELAXATION: prompt-root protection removed" in capsys.readouterr().err
