"""vulnscan rc.4 residuals, stream A: the agent definition a run hands its CLI (plan R1, R2, R3, R7, R9).

R2: the source file is read strictly (regular file, under the team, at most 256 KiB, UTF-8; BOM and CRLF
normalised). R1/R3: Copilot reads a vulnscan-owned copy (canonical front matter, the body byte for byte; removed
keys reported) from ``<run>/agentdef``, never the team. R7: goose reads a copy of its recipe, refused unless its
top-level keys are allowed and its ``extensions`` equal the block agentteams renders. R9: a run re-checks the copy
against the fingerprint. Docker-free and offline; the CLIs are not run.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_drivers, agent_recipe, agent_run, docker_mode, executor
from tests._vulnscan_helpers import goose_recipe

COPILOT = agent_drivers.COPILOT
GOOSE = agent_drivers.GOOSE
GENERATED = ("---\nname: Vulnerability Remediation — Demo\n"
             "description: \"Triages the daily scan for Demo\"\nuser-invocable: false\n"
             "tools: ['read', 'search']\nmodel: [\"Some Model (copilot)\"]\nhandoffs:\n"
             "  - label: Return to Orchestrator\n    agent: orchestrator\n    send: false\n"
             "---\n\n# Vulnerability Remediation\n\nYou triage findings.\n")
ROOT = Path(__file__).resolve().parents[1]


def _copilot_team(tmp_path: Path, text: str | bytes = GENERATED, name: str = "vuln-remediation") -> Path:
    agents = tmp_path / "team" / ".github" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / f"{name}.agent.md"
    if isinstance(text, bytes):
        target.write_bytes(text)
    else:
        target.write_text(text, encoding="utf-8")
    return tmp_path / "team"


def _a(team: Path, framework: str = "copilot", **extra: object) -> dict:
    return {**agent_run.DEFAULT_AGENT, "framework": framework, "team_dir": str(team), "key_file": "/k", **extra}


# ---------------------------------------------------------------- R2: reading the source strictly

@pytest.mark.parametrize("sep", [" ", " ", "\x85", "\x0b", "\x0c", "\x00", "\x7f"])
def test_a_hidden_line_break_cannot_smuggle_a_key_past_the_allowlist(tmp_path: Path, sep: str) -> None:
    """@audit #277: goose's YAML reader breaks lines at U+2028/U+2029/NEL, so `title: t<sep>retry: ...` would hide
    a `retry` key from a parser that splits on LF only. Any such character refuses the file, before any parsing."""
    team = tmp_path / "team"
    recipe = goose_recipe(team)
    recipe.write_text(recipe.read_text(encoding="utf-8").replace("title:", f"title: t{sep}retry:{sep}  max_retries: 1\nx_title:", 1),
                      encoding="utf-8")
    with pytest.raises(ValueError, match=r"control character or line separator \(U\+"):
        agent_recipe.make_copy(_a(team, "goose"), agent_drivers.get_driver("goose"))
    copilot = _copilot_team(tmp_path, GENERATED.replace("description:", f"description: a{sep}tools: [shell]\nx:", 1))
    with pytest.raises(ValueError, match="line separator"):
        agent_recipe.make_copy(_a(copilot), agent_drivers.get_driver("copilot"))


def test_crlf_and_bom_are_normalised_before_parsing(tmp_path: Path) -> None:
    team = _copilot_team(tmp_path, b"\xef\xbb\xbf" + GENERATED.replace("\n", "\r\n").encode("utf-8"))
    copy = agent_recipe.make_copy(_a(team), COPILOT)
    assert copy is not None and b"\r" not in copy.data and not copy.data.startswith(b"\xef\xbb\xbf")
    assert copy.data == agent_recipe.copilot_copy(GENERATED, ".github/agents/vuln-remediation.agent.md").data


def test_a_symlinked_agent_file_is_refused(tmp_path: Path) -> None:
    team = _copilot_team(tmp_path)
    real = team / ".github" / "agents" / "vuln-remediation.agent.md"
    elsewhere = tmp_path / "elsewhere.agent.md"
    elsewhere.write_text(GENERATED, encoding="utf-8")
    real.unlink()
    real.symlink_to(elsewhere)
    assert "is a symlink" in agent_recipe.problem(_a(team), COPILOT)
    real.unlink()
    real.symlink_to(team / ".github" / "agents" / "other.agent.md")      # even one inside the team
    (team / ".github" / "agents" / "other.agent.md").write_text(GENERATED, encoding="utf-8")
    assert "is a symlink" in agent_recipe.problem(_a(team), COPILOT)


def test_a_symlinked_parent_directory_is_refused(tmp_path: Path) -> None:
    outside = _copilot_team(tmp_path / "outside")
    team = tmp_path / "team"
    (team / ".github").mkdir(parents=True)
    (team / ".github" / "agents").symlink_to(outside / ".github" / "agents")
    assert "resolves outside the team" in agent_recipe.problem(_a(team), COPILOT)


def test_a_file_over_256_kib_or_not_utf8_or_not_regular_is_refused(tmp_path: Path) -> None:
    team = _copilot_team(tmp_path, GENERATED + "x" * agent_recipe.MAX_BYTES)
    assert "larger than 262144 bytes" in agent_recipe.problem(_a(team), COPILOT)
    _copilot_team(tmp_path, b"---\ndescription: \xff\n---\n")
    assert "cannot be read (UnicodeDecodeError)" in agent_recipe.problem(_a(team), COPILOT)
    path = team / ".github" / "agents" / "vuln-remediation.agent.md"
    path.unlink()
    os.mkfifo(path)                                     # never opened for reading: lstat says it is no regular file
    assert "is not a regular file" in agent_recipe.problem(_a(team), COPILOT)
    path.unlink()
    path.mkdir()
    assert agent_recipe.problem(_a(team), COPILOT)
    assert "agent.team_dir is not set" in agent_recipe.problem({"framework": "copilot"}, COPILOT)


# ---------------------------------------------------------------- R1, R3: the Copilot copy

def test_the_copy_is_canonical_and_reports_the_removed_keys() -> None:
    copy = agent_recipe.copilot_copy(GENERATED, ".github/agents/vuln-remediation.agent.md")
    text = copy.data.decode("utf-8")
    assert text == ('---\nname: vuln-remediation\ndescription: "Triages the daily scan for Demo"\n---\n'
                    "\n# Vulnerability Remediation\n\nYou triage findings.\n")      # the body byte for byte
    assert copy.dropped == ("user-invocable", "tools", "model", "handoffs")
    assert agent_recipe.front_matter_problem(text) == ""                            # what the self-test checks
    assert copy.record() == {"source": ".github/agents/vuln-remediation.agent.md",
                             "copy": "agentdef/.github/agents/vuln-remediation.agent.md",
                             "sha256": hashlib.sha256(copy.data).hexdigest(),
                             "removed_keys": ["user-invocable", "tools", "model", "handoffs"]}
    assert "user-invocable, tools, model, handoffs" in agent_recipe.notice(copy)
    plain = agent_recipe.copilot_copy("---\ndescription: 'it''s \"quoted\"'\n---\nbody")
    assert plain.data.decode() == '---\nname: vuln-remediation\ndescription: "it\'s \\"quoted\\""\n---\nbody'
    assert plain.dropped == () and agent_recipe.notice(plain) == ""


@pytest.mark.parametrize(("text", "why"), [
    ("body only\n", "no front matter"),
    ("---\nname: a\n", "no closing ---"),
    ("---\n\n---\nbody\n", "empty front matter"),
    ("---\nname: a\ntools: [x]\n---\n", "no 'description'"),
    ("---\ndescription: >\n  folded\n---\n", "'description' must be a one-line string"),
    ("---\ndescription: a\n  continued\n---\n", "'description' must be a one-line string"),
    ("---\ndescription: a\ndescription: b\n---\n", "appears twice"),
    ("---\n  indented: first\ndescription: a\n---\n", "a continuation line before any key"),
    ("---\n<<: *base\ndescription: a\n---\n", "unparseable front matter"),
])
def test_the_copy_still_refuses(text: str, why: str) -> None:
    with pytest.raises(ValueError, match=why.replace("(", r"\(")):
        agent_recipe.copilot_copy(text)


def test_the_fingerprint_binds_the_copys_bytes(tmp_path: Path) -> None:
    team = _copilot_team(tmp_path)
    a = _a(team)
    copy = agent_recipe.make_copy(a, COPILOT)
    assert agent_recipe.binding(a, COPILOT) == copy.bound() == {
        "path": ".github/agents/vuln-remediation.agent.md", "sha256": hashlib.sha256(copy.data).hexdigest()}
    before = agent_run.flags_sha256(a, "sha256:img")
    _copilot_team(tmp_path, GENERATED.replace("model: [", "model: [\"Other\", "))   # a removed key only
    assert agent_run.flags_sha256(a, "sha256:img") == before                          # the copy is unchanged
    _copilot_team(tmp_path, GENERATED + "One more prompt line.\n")
    assert agent_run.flags_sha256(a, "sha256:img") != before
    _copilot_team(tmp_path, "no front matter\n")
    assert agent_recipe.binding(a, COPILOT)["sha256"] == "refused"


def test_write_copy_makes_a_fresh_read_only_directory_holding_only_the_copy(tmp_path: Path) -> None:
    team, run = _copilot_team(tmp_path), tmp_path / "run"
    (run / "agentdef" / "stale").mkdir(parents=True)
    (run / "agentdef" / "stale" / "x.agent.md").write_text("old", encoding="utf-8")
    copy = agent_recipe.write_copy(_a(team), COPILOT, run)
    files = sorted(str(p.relative_to(run / "agentdef")) for p in (run / "agentdef").rglob("*") if p.is_file())
    assert files == [".github/agents/vuln-remediation.agent.md"]
    target = run / "agentdef" / ".github" / "agents" / "vuln-remediation.agent.md"
    assert target.read_bytes() == copy.data and stat.S_IMODE(target.stat().st_mode) == 0o444
    assert agent_recipe.write_copy(_a(team), COPILOT, run).sha256 == copy.sha256       # rewritable over 0444
    assert agent_recipe.write_copy(_a(team, "claude"), agent_drivers.CLAUDE, run) is None


@pytest.fixture
def no_team_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: None)
    monkeypatch.setattr(executor, "spool_dir", lambda: Path("/spool"))


def test_the_argv_never_names_the_team_and_overrides_are_refused(no_team_check: None) -> None:
    for recipe in ("", ".github/agents/other.agent.md"):
        a = _a(Path("/team"), recipe=recipe)
        argv = agent_run.agent_argv(a, "img:t", Path("/state/run"), "s", task="T", run_id="R")
        cmd = argv[argv.index("img:t") + 1:]
        assert "--add-dir=/work/run/agentdef" in cmd and "--agent=vuln-remediation" in cmd
        assert not any("/work/team" in part for part in cmd)
    for command in (["copilot", "--add-dir=/work/team"], ["copilot", "-p", "{task}"]):
        with pytest.raises(ValueError, match="overrides are not supported for framework copilot"):
            agent_run.validate_agent({"framework": "copilot", "command": command})


# ---------------------------------------------------------------- R7: the goose recipe

def test_the_recipe_agentteams_generates_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import build_team
    from agentteams.cli import security_gate

    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    brief = json.loads((ROOT / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
    brief["capabilities"] = sorted(set(brief.get("capabilities", [])) | {"vulnerability_remediation"})
    (tmp_path / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
    out = tmp_path / "team"
    assert build_team.main(["--description", str(tmp_path / "brief.json"), "--framework", "goose", "--output",
                            str(out), "--yes", "--no-scan", "--security-offline"]) == 0
    a = _a(out, "goose")
    assert agent_recipe.problem(a, GOOSE) == ""
    copy = agent_recipe.make_copy(a, GOOSE)
    assert copy.data == (out / GOOSE.default_recipe).read_bytes() and copy.path == "recipe.yaml"


def _recipe(tmp_path: Path, edit: tuple[str, str] | None = None, append: str = "") -> Path:
    team = tmp_path / "team"
    path = goose_recipe(team)
    text = path.read_text(encoding="utf-8")
    if edit:
        assert edit[0] in text
        text = text.replace(*edit)
    path.write_text(text + append, encoding="utf-8")
    return team


@pytest.mark.parametrize(("append", "why"), [
    ("settings:\n  goose_provider: openai\n", "'settings'"),
    ("sub_recipes:\n  - name: x\n    path: ./x.yaml\n", "'sub_recipes'"),
    ("retry:\n  max_retries: 1\n", "'retry'"),
    ("response:\n  json_schema: {}\n", "'response'"),
    ('"settings": {}\n', "only plain 'key:' lines"),
    ("<<: *base\n", "only plain 'key:' lines"),
    ("---\n", "only plain 'key:' lines"),
    ("title: again\n", "'title' appears twice"),
])
def test_goose_keys_outside_the_allowlist_are_refused(tmp_path: Path, append: str, why: str) -> None:
    team = _recipe(tmp_path, append=append)
    got = agent_recipe.problem(_a(team, "goose"), GOOSE)
    assert "goose recipe .goose/recipes/vuln-remediation.yaml is refused" in got and why in got


@pytest.mark.parametrize("edit", [
    ("    name: developer\n", "    name: computercontroller\n"),
    ("  - type: platform\n    name: summon\n", ""),
    ("  - type: platform\n    name: summon\n",
     "  - type: platform\n    name: summon\n  - type: stdio\n    name: extra\n    cmd: sh\n"),
    ("    timeout: 300\n", "    timeout: 300\n    available_tools: [shell]\n"),
    ("extensions:\n", "extensions: []\nx_ignored:\n"),
])
def test_goose_extensions_must_equal_the_rendered_block(tmp_path: Path, edit: tuple[str, str]) -> None:
    team = _recipe(tmp_path, edit=edit)
    assert agent_recipe.problem(_a(team, "goose"), GOOSE)


def test_goose_comments_and_blank_lines_are_normalised_away(tmp_path: Path) -> None:
    team = _recipe(tmp_path, edit=("extensions:\n", "# a comment\nextensions:\n\n  # indented comment\n"))
    assert agent_recipe.problem(_a(team, "goose"), GOOSE) == ""
    (team / GOOSE.default_recipe).unlink()
    assert "cannot be read" in agent_recipe.problem(_a(team, "goose"), GOOSE)


def test_goose_runs_and_fingerprints_the_copy(tmp_path: Path, no_team_check: None) -> None:
    team = _recipe(tmp_path)
    a = _a(team, "goose")
    argv = agent_run.agent_argv(a, "img:t", tmp_path / "run", "s", task="T", run_id="R")
    assert argv[argv.index("--recipe") + 1] == "/work/run/agentdef/recipe.yaml"
    before = agent_run.flags_sha256(a, "sha256:img")
    _recipe(tmp_path, edit=("You triage findings.", "You triage findings carefully."))
    assert agent_run.flags_sha256(a, "sha256:img") != before            # goose operators re-run the self-test once
    copy = agent_recipe.write_copy(a, GOOSE, tmp_path / "run")
    assert (tmp_path / "run" / "agentdef" / "recipe.yaml").read_bytes() == copy.data
    assert agent_recipe.binding(a, GOOSE) == copy.bound()


# ---------------------------------------------------------------- run_agent and selftest wiring (R1, R9)

@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(agent_run.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: None)
    # Never the host's real `docker info`: on a Linux CI runner it reports a rootful Engine (R4).
    monkeypatch.setattr(agent_run, "detect_docker_mode", lambda: docker_mode.DockerMode(docker_mode.DESKTOP))
    run = tmp_path / "state" / "2026-10-24"
    run.mkdir(parents=True)
    return run


def test_a_run_records_and_prints_the_removed_keys(tmp_path: Path, state: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    team = _copilot_team(tmp_path)
    cfg = {"agent": _a(team)}
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (True, "ok"))
    seen: dict[str, bytes] = {}

    def launch(a: dict, tag: str, run_dir: Path, *rest: object) -> tuple[bool, int]:
        seen["copy"] = (run_dir / "agentdef" / ".github" / "agents" / "vuln-remediation.agent.md").read_bytes()
        return False, 1
    monkeypatch.setattr(agent_run, "_launch", launch)
    monkeypatch.setattr(agent_run, "_docker", lambda *a, **k: None)
    st = agent_run.run_agent(cfg, state)
    copy = agent_recipe.copilot_copy(GENERATED, ".github/agents/vuln-remediation.agent.md")
    assert seen["copy"] == copy.data and st["agent_definition"] == copy.record()
    assert "removed front-matter key(s) user-invocable, tools, model, handoffs" in capsys.readouterr().err
    status = json.loads((state / "agent" / "status.json").read_text(encoding="utf-8"))
    assert status["agent_definition"]["removed_keys"] == ["user-invocable", "tools", "model", "handoffs"]
    assert "You triage" not in json.dumps(status)                                   # no content in the record


def test_a_run_refuses_a_copy_the_self_test_no_longer_vouches_for(tmp_path: Path, state: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    team = _copilot_team(tmp_path)
    cfg = {"agent": _a(team)}
    calls: list[int] = []

    def selftest_ok(c: dict) -> tuple[bool, str]:      # passes the first check; the file changes before the re-check
        calls.append(1)
        if len(calls) == 1:
            return True, "ok"
        return False, "the runtime ... changed since the last passing self-test"
    monkeypatch.setattr(agent_run, "selftest_ok", selftest_ok)
    monkeypatch.setattr(agent_run, "_launch", lambda *a, **k: pytest.fail("the CLI must not start"))
    st = agent_run.run_agent(cfg, state)
    assert st["run_state"] == "not-run" and "agent definition changed" in st["reason"] and len(calls) == 2


def test_a_run_refuses_when_the_team_file_no_longer_renders_to_the_copy(tmp_path: Path, state: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    team = _copilot_team(tmp_path)
    cfg = {"agent": _a(team)}
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (True, "ok"))
    real = agent_recipe.write_copy

    def write_then_edit(a: dict, d: agent_drivers.Driver, run_dir: Path) -> agent_recipe.AgentCopy | None:
        copy = real(a, d, run_dir)
        _copilot_team(tmp_path, GENERATED + "edited after the copy\n")
        return copy
    monkeypatch.setattr(agent_recipe, "write_copy", write_then_edit)
    monkeypatch.setattr(agent_run, "_launch", lambda *a, **k: pytest.fail("the CLI must not start"))
    st = agent_run.run_agent(cfg, state)
    assert st["run_state"] == "not-run" and "agent definition changed" in st["reason"]


def test_a_refused_definition_ends_the_run_not_run(tmp_path: Path, state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _copilot_team(tmp_path)
    cfg = {"agent": _a(team)}
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (True, "ok"))
    monkeypatch.setattr(agent_run, "_not_run_reason", lambda c, a: "")      # the file breaks after the first check
    _copilot_team(tmp_path, "no front matter\n")
    st = agent_run.run_agent(cfg, state)
    assert st["run_state"] == "not-run" and "cannot be copied" in st["reason"] and "no front matter" in st["reason"]


def test_the_self_test_writes_the_copy_and_records_it(tmp_path: Path, state: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    team = _copilot_team(tmp_path)
    key = tmp_path / "or.env"
    key.write_text("OPENROUTER_API_KEY=sk-x\n", encoding="utf-8")
    cfg = {"agent": {**_a(team), "key_file": str(key)}}
    seen: dict[str, bytes] = {}

    def docker(argv: list[str], **kw: object) -> object:
        import subprocess
        if "-c" in argv:
            seen["copy"] = (tmp_path / "state" / "selftest" / "run" / "agentdef" / ".github" / "agents" /
                            "vuln-remediation.agent.md").read_bytes()
        out = 'SELFTEST-IDENTITY 10001:10001\nSELFTEST {"no_capabilities": true}' if "-c" in argv else "sha256:img"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    monkeypatch.setattr(agent_run, "_docker", docker)
    monkeypatch.setattr(agent_run.time, "sleep", lambda s: None)
    res = agent_run.selftest(cfg)
    copy = agent_recipe.copilot_copy(GENERATED, ".github/agents/vuln-remediation.agent.md")
    assert res["passed"] and seen["copy"] == copy.data and res["agent_definition"] == copy.record()
    _copilot_team(tmp_path, "no front matter\n")
    res = agent_run.selftest(cfg)
    assert res["checks"] == {"agent_definition_valid": False} and "no front matter" in res["error"]


def test_review_lists_the_keys_the_copy_removed(tmp_path: Path) -> None:
    """R1: review shows the removed front-matter keys (names only, identifier-like) from the run's status.json."""
    from agentteams.vulnscan import review

    status = tmp_path / "status.json"
    assert review._removed_key_lines(status) == []                       # no agent run yet
    status.write_text(json.dumps({"agent_definition": {"removed_keys": ["tools", "user-invocable", "x y`z"]}}))
    (line,) = review._removed_key_lines(status)
    assert "removed tools, user-invocable from the front matter" in line and "`" not in line
    status.write_text(json.dumps({"agent_definition": {"removed_keys": []}}))
    assert review._removed_key_lines(status) == []
    status.write_text("not json")
    assert review._removed_key_lines(status) == []


def test_a_goose_command_override_may_not_name_the_team() -> None:
    """R7: an override naming /work/team would run the team's recipe, not the checked copy {recipe} names."""
    goose = agent_drivers.get_driver("goose")
    assert goose.command_problem(["goose", "run", "--recipe", "{recipe}", "--name", "{session}"]) == ""
    bad = ["goose", "run", "--recipe", "/work/team/.goose/recipes/vuln-remediation.yaml"]
    assert "must not name /work/team" in goose.command_problem(bad)
    with pytest.raises(ValueError, match="must not name /work/team"):
        agent_run.validate_agent({"framework": "goose", "command": bad})
    for sneaky in ("/work/./team/r.yaml", "//work/team", "/work/x/../team/r.yaml", "--recipe=/work/team/r.yaml"):
        assert goose.command_problem(["goose", "run", sneaky]), sneaky
    assert goose.command_problem(["goose", "run", "/work/teams/r.yaml", "/work/run/x"]) == ""


def test_version_ordering_never_raises_on_hostile_osv_versions() -> None:
    """R8 residual (@security rc.4 re-review): non-ASCII digits and huge numbers sort, never raise."""
    from agentteams.vulnscan.versions import major, version_key

    for v in ("²!1", "1²", "1." + "9" * 5000, "1!" + "9" * 5000, "١٢٣"):
        version_key(v)
        major(v)
    assert major("9" * 5000) is not None and major("²") is None and major("v3.2") == 3
    assert version_key("1.0") < version_key("1.1") < version_key("1." + "9" * 5000)
