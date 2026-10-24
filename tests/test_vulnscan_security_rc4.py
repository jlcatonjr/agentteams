"""The @security review of the rc.4 candidate: one regression test (or more) per finding.

Medium 1 (the Copilot agent file is configuration), Medium 2 (Copilot's home and code cache), Low 3-10. Docker-free
and offline: the CLIs are fakes or not run, host names and secrets are made up.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from agentteams.vulnscan import (
    agent_drivers,
    agent_drivers_codex,
    agent_drivers_copilot,
    agent_recipe,
    agent_run,
    daily,
    gateway,
    gateway_context,
    osv,
    review,
    selftest_cli,
)
from agentteams.vulnscan import gateway_dialects as d
from tests.test_vulnscan_gateway_context import host  # noqa: F401  (fixture)
from tests.test_vulnscan_registry import xdg  # noqa: F401  (fixture)
from tests.test_vulnscan_reliability import _beat, _recorders

ROOT = Path(__file__).resolve().parents[1]
CLEAN = "---\nname: vuln-remediation\ndescription: \"Triages the daily scan\"\n---\nYou triage findings.\n"
GENERATED = ("---\nname: Vulnerability Remediation\ndescription: \"x\"\nuser-invocable: false\n"
             "tools: ['read', 'search']\nmodel: [\"Some Model (copilot)\"]\n---\nbody\n")


# ---------------------------------------------------------------- Medium 1: the Copilot agent file

@pytest.mark.parametrize("text", [
    CLEAN,
    "no front matter at all: the whole file is the prompt\n",
    "---\nname: plain value\n# a comment\n\ndescription: 'single ''quoted'' value'\n---\nbody\n",
    "---\n---\nempty front matter\n",
    "--- \nname: a\n---\n",                                          # trailing blank on the delimiter
])
def test_front_matter_with_name_and_description_only_is_accepted(text: str) -> None:
    assert agent_recipe.front_matter_problem(text) == ""


@pytest.mark.parametrize(("text", "why"), [
    (GENERATED, "'user-invocable' is configuration"),
    ("---\nname: a\ntools: ['vulnscan/*']\n---\n", "'tools' is configuration"),
    ("---\nmodel: x\n---\n", "'model' is configuration"),
    ("---\nmcp-servers:\n  evil:\n    command: sh\n---\n", "'mcp-servers' is configuration"),
    ("---\nname: a\n", "no closing ---"),
    ("---x\nname: a\n---\n", "opening line must be exactly ---"),
    ("\n\n---\ntools: [x]\n---\n", "'tools' is configuration"),     # leading blank lines do not hide it
    ("---\nname: a\n  tools: [x]\n---\n", "unparseable front matter (line 3"),
    ("---\nname: a\ndescription: >\n  folded\n---\n", "'description' must be a one-line string"),
    ("---\nname: [a, b]\n---\n", "'name' must be a one-line string"),
    ("---\nname: &anchor a\n---\n", "'name' must be a one-line string"),
    ("---\nname: a: b\n---\n", "'name' must be a one-line string"),
    ("---\nname: \"unterminated\n---\n", "unparseable double-quoted value"),
    ("---\nname: a\nname: b\n---\n", "appears twice"),
    ("---\n<<: *base\n---\n", "unparseable front matter"),
])
def test_any_other_front_matter_is_refused(text: str, why: str) -> None:
    assert why in agent_recipe.front_matter_problem(text)


def _team(tmp_path: Path, text: str, name: str = "vuln-remediation") -> Path:
    agents = tmp_path / "team" / ".github" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{name}.agent.md").write_text(text, encoding="utf-8")
    return tmp_path / "team"


def test_a_run_refuses_a_copilot_agent_file_with_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_run, "selftest_ok", lambda cfg: (True, "ok"))
    monkeypatch.setattr(agent_run.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: "")
    team = _team(tmp_path, GENERATED)
    a = {**agent_run.DEFAULT_AGENT, "framework": "copilot", "team_dir": str(team), "key_file": "/k"}
    why = agent_run._not_run_reason({"agent": a}, a)
    assert "copilot agent file .github/agents/vuln-remediation.agent.md is refused" in why and "'user-invocable'" in why
    _team(tmp_path, CLEAN)
    assert agent_run._not_run_reason({"agent": a}, a) == ""
    gone = {**a, "recipe": ".github/agents/missing.agent.md"}
    assert "cannot be read" in agent_run._not_run_reason({"agent": gone}, gone)
    goose = {**a, "framework": "goose"}                  # the check is copilot's (its front matter is configuration)
    assert agent_drivers.recipe_problem(goose, agent_run.driver_of(goose)) == ""


def test_the_copilot_self_test_refuses_the_file_without_running_the_cli(tmp_path: Path) -> None:
    team = _team(tmp_path, GENERATED)
    argv = ["/must/not/run", "-p", "x", "--agent=vuln-remediation", "--add-dir={team}"]
    for recipe in (team / ".github" / "agents" / "vuln-remediation.agent.md", tmp_path / "absent.agent.md"):
        checks, detail = selftest_cli.copilot_checks(argv, {}, recipe=str(recipe), dialect="chat", timeout=5)
        assert checks == dict.fromkeys(selftest_cli.COPILOT_CHECKS, False) and "rc" not in detail
        assert detail["agent_file_problem"]


def test_the_self_test_runs_with_the_selected_agent_file() -> None:
    for fw, recipe in (("copilot", ".github/agents/other.agent.md"), ("claude", ".claude/agents/other.md"),
                       ("codex", ".codex/agents/other.toml")):
        a = {**agent_run.DEFAULT_AGENT, "framework": fw, "recipe": recipe, "accept_residual_tools": ["apply_patch"]}
        src = agent_run.selftest_script(agent_run.driver_of(a))
        assert f"/work/team/{recipe}" in src and agent_run.driver_of(a).default_recipe not in src, fw
    a = {**agent_run.DEFAULT_AGENT, "framework": "copilot", "recipe": ".github/agents/other.agent.md"}
    assert "--agent=other" in agent_run.driver_of(a).command_template()


def _fp(team: Path, **a: object) -> str:
    return agent_run.flags_sha256({"team_dir": str(team), "accept_residual_tools": ["apply_patch"], **a}, "sha256:img")


def test_the_fingerprint_binds_copilot_content_and_every_framework_path(tmp_path: Path) -> None:
    team = _team(tmp_path, CLEAN)
    agent = team / ".github" / "agents" / "vuln-remediation.agent.md"
    before = _fp(team, framework="copilot")
    agent.write_text(CLEAN + "One more prompt line.\n", encoding="utf-8")
    assert _fp(team, framework="copilot") != before                     # copilot: any edit asks for a new self-test
    binding = agent_recipe.binding({"team_dir": str(team), "framework": "copilot"}, agent_drivers.COPILOT)
    assert binding == {"path": ".github/agents/vuln-remediation.agent.md",
                       "sha256": hashlib.sha256(agent.read_bytes()).hexdigest()}
    for fw, default in (("claude", ".claude/agents/vuln-remediation.md"), ("codex", ".codex/agents/vuln-remediation.toml"),
                        ("goose", ".goose/recipes/vuln-remediation.yaml")):
        (team / default).parent.mkdir(parents=True, exist_ok=True)
        (team / default).write_text("prompt v1\n", encoding="utf-8")
        first = _fp(team, framework=fw)
        (team / default).write_text("prompt v2: edited\n", encoding="utf-8")
        assert _fp(team, framework=fw) == first, fw                      # prompt text only: no churn
        assert _fp(team, framework=fw, recipe="other/file.md") != first, fw   # but the selected path is bound
        assert agent_recipe.binding({"framework": fw}, agent_drivers.get_driver(fw)) == default   # unchanged value


# ---------------------------------------------------------------- Medium 2: Copilot's home and code cache

@pytest.mark.skipif(os.geteuid() == 0, reason="root can write a 0555 directory")
def test_copilot_home_fresh(tmp_path: Path) -> None:
    home, cache = tmp_path / "home", tmp_path / "cache"
    (cache / "pkg").mkdir(parents=True)
    home.mkdir()
    cfg_home = f"{home}/.copilot"
    env = {"COPILOT_HOME": cfg_home, "COPILOT_CACHE_HOME": str(cache)}
    os.chmod(cache, 0o555)
    try:
        assert selftest_cli.copilot_home_fresh(env, str(home), cfg_home, str(cache))           # absent: fresh
        Path(cfg_home).mkdir()
        assert selftest_cli.copilot_home_fresh(env, str(home), cfg_home, str(cache))           # empty: fresh
        (Path(cfg_home) / "mcp-config.json").write_text("{}")
        assert not selftest_cli.copilot_home_fresh(env, str(home), cfg_home, str(cache))       # planted
        (Path(cfg_home) / "mcp-config.json").unlink()
        assert not selftest_cli.copilot_home_fresh({**env, "COPILOT_HOME": "/elsewhere"}, str(home), cfg_home, str(cache))
        os.chmod(cache, 0o755)
        assert not selftest_cli.copilot_home_fresh(env, str(home), cfg_home, str(cache))       # writable cache
        (cache / "pkg").rmdir()
        os.chmod(cache, 0o555)
        assert not selftest_cli.copilot_home_fresh(env, str(home), cfg_home, str(cache))       # not populated
    finally:
        os.chmod(cache, 0o755)


def test_the_image_pre_unpacks_the_copilot_cache_read_only() -> None:
    text = (agent_run.dockerfile_dir() / "Dockerfile").read_text(encoding="utf-8")
    stage = text.split("FROM base AS cli-copilot", 1)[1].split("\nFROM ", 1)[0]
    assert f"COPILOT_CACHE_HOME={agent_drivers_copilot.COPILOT_CACHE} copilot --version" in stage
    assert 'ls -d /opt/copilot-cache/pkg/linux-*/"${CLI_VERSION}"' in stage and "chmod -R a+rX,go-w /opt/copilot-cache" in stage
    src = agent_run.selftest_script(agent_drivers.COPILOT)
    assert 'r["copilot_home_fresh"] = _cli.copilot_home_fresh(os.environ, home, ' in src
    assert repr(agent_drivers_copilot.COPILOT_HOME) in src and repr(agent_drivers_copilot.COPILOT_CACHE) in src


# ---------------------------------------------------------------- Low 3: Codex's write test

def test_the_codex_patch_targets_the_spool_and_the_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[Path]] = []
    real = selftest_cli._patch_reply
    monkeypatch.setattr(selftest_cli, "_patch_reply", lambda out, extra: seen.append(extra) or real(out, extra))
    monkeypatch.setattr(selftest_cli.stub, "stub_run",
                        lambda *a, **k: selftest_cli.stub.StubRun(0, "", "", [], []))
    selftest_cli.codex_checks(["codex"], recipe="/none", argv_url="x")
    assert [str(p.parent) for p in seen[0]] == [agent_drivers.MCP_ENV["VULNSCAN_SPOOL"], agent_drivers_codex.CODEX_STATE]
    _ctype, body = real(Path("/tmp/out"), seen[0])({"model": "m", "tools": [{"type": "custom", "name": "apply_patch"}]}, 0)
    assert all(f"*** Add File: {p}" in body.decode().replace("\\n", "\n")
               for p in ("/tmp/out/abs.txt", "rel-canary.txt", *map(str, seen[0])))


# ---------------------------------------------------------------- Low 4: the Responses reserve

def test_the_responses_reserve_is_written_into_the_forwarded_body() -> None:
    raw = json.dumps({"model": "m", "input": "x", "store": False}).encode()
    checked = d.check_request(d.RESPONSES, raw, models={"m"}, max_output_tokens=32_000, responses_reserve_tokens=900)
    assert checked.max_output == 900 and json.loads(checked.body)["max_output_tokens"] == 900
    named = json.dumps({"model": "m", "input": "x", "max_output_tokens": 50}).encode()
    assert json.loads(d.check_request(d.RESPONSES, named, models={"m"}, max_output_tokens=32_000,
                                      responses_reserve_tokens=900).body)["max_output_tokens"] == 50


# ---------------------------------------------------------------- Low 5: unreadable DLP secrets file

def test_an_unreadable_dlp_secrets_file_stops_the_gateway(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key = tmp_path / "openrouter.env"
    key.write_text("OPENROUTER_API_KEY=placeholder-key-value\n")
    rc = gateway.main(["--model", "m", "--key-file", str(key), "--dlp-secrets-dir", "",
                       "--dlp-secrets-file", str(tmp_path / "missing.env")])
    err = capsys.readouterr().err
    assert rc == 2 and "1 DLP secrets file(s) could not be read; refusing to start" in err and "uid 10001" in err
    assert "placeholder-key-value" not in err


def test_prepare_explains_the_linux_uid_limit(host: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(gateway_context.sys, "platform", "linux")
    gateway_context.prepare(tmp_path / "gw", kit_dir=host, secrets_dir=tmp_path / "none", entries=[])
    assert gateway_context.LINUX_NOTE in capsys.readouterr().err
    monkeypatch.setattr(gateway_context.sys, "platform", "darwin")
    gateway_context.prepare(tmp_path / "gw", kit_dir=host, secrets_dir=tmp_path / "none", entries=[])
    assert gateway_context.LINUX_NOTE not in capsys.readouterr().err


# ---------------------------------------------------------------- Low 6: hosts.conf names that reveal a host

def test_revealing_hosts_conf_names_are_warned_and_noted(host: Path, tmp_path: Path,  # noqa: F811
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    (host / "hosts.conf").write_text("web1 | deploy@web1.example.com\napp.example.org | app\n"
                                     "build-box-7 | localhost\n", encoding="utf-8")
    assert gateway_context.revealing_names({"web1", "app.example.org", "build-box-7", "10.0.0.5"}, {"build-box-7"}) == \
        ["10.0.0.5", "app.example.org", "build-box-7"]
    ctx = gateway_context.prepare(tmp_path / "gw", kit_dir=host, secrets_dir=tmp_path / "none", entries=[])
    err = capsys.readouterr().err
    assert err.count("2 hosts.conf name(s)") == 1 and "app.example.org" not in err   # a count, never the names
    notes = ctx.directory / gateway_context.NOTES
    assert "hosts_conf_unredacted=2" in notes.read_text(encoding="utf-8")
    assert review._gateway_note_lines(notes) == [f"- gateway DLP: {gateway_context.unredacted_note(2)}"]
    (host / "hosts.conf").write_text("web1 | deploy@web1.example.com\n", encoding="utf-8")
    gateway_context.prepare(tmp_path / "gw", kit_dir=host, secrets_dir=tmp_path / "none", entries=[])
    assert "hosts.conf name(s)" not in capsys.readouterr().err and review._gateway_note_lines(notes) == []
    assert review._gateway_note_lines(tmp_path / "absent") == []


# ---------------------------------------------------------------- Low 7: CVSS robustness

@pytest.mark.parametrize("record", [
    {"severity": "HIGH"}, {"severity": 7.5}, {"severity": {"type": "CVSS_V3"}}, {"severity": None},
    {"database_specific": "HIGH"}, {"database_specific": ["HIGH"]}, {"database_specific": {"severity": 4}},
    {"affected": "x"}, {"affected": [{"severity": "HIGH"}, 3, None]}, {"affected": [{"severity": 9}]},
    {"severity": [None, "x", {"type": "CVSS_V3", "score": 9}]}, [], "advisory",
])
def test_malformed_severity_fields_are_unscored_not_a_crash(record: object) -> None:
    assert osv.severity(record) == 0   # type: ignore[arg-type]


def test_a_good_label_still_scores_beside_malformed_fields() -> None:
    assert osv.severity({"severity": "junk", "database_specific": {"severity": "high"}}) == osv.SEVERITY_RANK["HIGH"]


# ---------------------------------------------------------------- Low 8: catch-up checks the lock first

def test_catch_up_does_not_notify_while_a_daily_run_holds_the_lock(xdg: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    sent, runs = _recorders(monkeypatch)
    _beat(None)                                          # no heartbeat: a catch-up would be due
    daily.run_lock_path().parent.mkdir(parents=True, exist_ok=True)
    with open(daily.run_lock_path(), "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert daily.run_in_progress()
        assert daily.catch_up({**daily.DEFAULT_CONFIG}) is None
    assert sent == [] and runs == [] and "a daily run is in progress" in capsys.readouterr().err
    assert not daily.run_in_progress()
    assert daily.catch_up({**daily.DEFAULT_CONFIG}) is not None and [k for k, _ in sent] == ["run-missing"]


# ---------------------------------------------------------------- Low 9 and 10: template row, image script

def test_the_s_vm_conditional_row_names_unsupported_manifests() -> None:
    text = (ROOT / "agentteams" / "templates" / "universal" / "security.template.md").read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith("| Daily vulnerability scan missing"))
    assert "`unsupported_manifests` listed in an otherwise scanned project" in row and "CONDITIONAL PASS" in row
    assert int(re.search(r"security_rules_invariant v=(\d+)", text).group(1)) >= 16
    for example in ("data-pipeline", "software-project"):
        snap = (ROOT / "examples" / example / "expected" / "security.agent.md").read_text(encoding="utf-8")
        assert "`unsupported_manifests` listed in an otherwise scanned project" in snap


def test_the_image_script_says_when_only_the_sha256_was_checked() -> None:
    text = (ROOT / "scripts" / "vulnscan-agent-image.sh").read_text(encoding="utf-8")
    branch = text.split("if command -v cosign >/dev/null; then", 1)[1].split("\n  fi\n", 1)[0]
    assert "NOTICE: cosign is not installed" in branch and "only the sha256 pin above was checked" in branch
