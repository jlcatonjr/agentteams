"""Runtime drivers (vulnscan-agent-runtimes plan, phase 1): the goose driver extracted with no behaviour change.

The ``OLD_*`` literals are the full ``docker run`` argv the launcher built *before* the driver refactor
(captured 2026-10-23 from ``agent_run.agent_argv`` / ``selftest_argv`` / ``debrief.debrief_argv`` /
``gateway_argv`` with fixed placeholder paths). The goose driver must reproduce them exactly, except for the
two revision-7 changes, which the tests spell out: the working directory is an empty tmpfs (``/work/cwd``,
was the image's ``WORKDIR`` ``/work``), and ``XDG_CONFIG_HOME`` is set to goose's own default
(``$HOME/.config``, inside the tmpfs home). The self-test probe gains one check, ``cwd_empty``. The gateway
argv additionally carries the roadmap-B2 DLP context mounts and flags (:func:`_b2`; deliberate). Docker-free.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import (
    agent_drivers,
    agent_run,
    debrief,
    executor,
    selftest_probe,
)

RUN = Path("/state/2026-10-22")
SESSION = "vulnscan-2026-10-22"

_PREFIX = ["--network", "agentteams-vulnscan-net", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
           "--tmpfs", "/agenthome:rw,size=256m,uid=10001,gid=10001", "--cap-drop", "ALL", "--security-opt",
           "no-new-privileges", "--user", "10001:10001", "--pids-limit", "256", "--memory", "2g",
           "-v", "/state/2026-10-22:/work/run:ro"]


def _old_env(gw: str) -> list[str]:
    return ["-e", "GOOSE_PROVIDER=openrouter", "-e", f"OPENROUTER_HOST=http://{gw}:8080", "-e",
            "OPENROUTER_API_KEY=injected-by-gateway", "-e", "GOOSE_MODE=auto", "-e", "GOOSE_DISABLE_KEYRING=1",
            "-e", "XDG_DATA_HOME=/agenthome/.local/share", "-e", "XDG_STATE_HOME=/agenthome/.local/share/goose/state",
            "-e", "VULNSCAN_RUN_DIR=/work/run", "-e", "VULNSCAN_SPOOL=/work/spool/requests", "-e",
            "VULNSCAN_RESULTS=/work/spool/results"]


def _old_mounts(spool_mode: str) -> list[str]:
    return ["-v", f"/spool/requests:/work/spool/requests:{spool_mode}", "-v", "/spool/results:/work/spool/results:ro",
            "-v", "/team:/work/team:ro", "-v", "/state/2026-10-22/agent/goose-data:/agenthome/.local/share/goose:rw"]


OLD_AGENT = ["docker", "run", "--rm", "--name", "agentteams-vulnscan-agent", *_PREFIX, *_old_mounts("rw"),
             *_old_env("agentteams-vulnscan-gw"), "-e", "GOOSE_MODEL=m/x", "img:t",
             "goose", "run", "--recipe", "/work/team/.goose/recipes/vuln-remediation.yaml", "--name", SESSION,
             "--with-extension", "python -m agentteams.vulnscan.mcp", "--params", "task=TASK TEXT"]
OLD_SELFTEST = ["docker", "run", "--rm", "--name", "agentteams-vulnscan-selftest", *_PREFIX, *_old_mounts("rw"),
                *_old_env("agentteams-vulnscan-gw"), "img:t", "python", "-I", "-c", "<probe>"]
OLD_DEBRIEF = ["docker", "run", "--rm", "-it", "--name", "agentteams-vulnscan-debrief", *_PREFIX, *_old_mounts("ro"),
               *_old_env("agentteams-vulnscan-gw-debrief"), "-e", "GOOSE_MODEL=m/x", "img:t",
               "goose", "session", "--resume", "--name", SESSION, "--with-extension", "python -m agentteams.vulnscan.mcp"]
OLD_GATEWAY = ["docker", "run", "-d", "--rm", "--name", "agentteams-vulnscan-gw", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", "10001:10001", "--pids-limit", "64", "--memory", "256m",
               "--add-host", "host.docker.internal:host-gateway", "-v", "/key:/run/secrets/openrouter.env:ro",
               "-v", "/spend:/work/out:rw", "img:t", "python", "-m", "agentteams.vulnscan.gateway", "--listen",
               "0.0.0.0:8080", "--model", "m/x", "--key-file", "/run/secrets/openrouter.env", "--upstream",
               "http://host.docker.internal:8791", "--budget-usd", "2.0", "--spend-file", "/work/out/spend.json",
               "--max-tokens-total", "10000000", "--max-unmetered-calls", "20"]
#: sha256 of the self-test probe text before the refactor.
OLD_PROBE_SHA256 = "b24c4c7c6b70f214b35ea1e10a2c8c9788bfd5b98f0e28e6391864d989fc856f"
CWD_LINE = 'r["cwd_empty"] = os.getcwd() == "/work/cwd" and os.listdir(".") == []   # revision 7: never the team mount\n'


def _revision_7(old: list[str]) -> list[str]:
    """The old argv plus exactly the two revision-7 changes."""
    new = list(old)
    i = new.index("/agenthome:rw,size=256m,uid=10001,gid=10001") + 1
    new[i:i] = ["--tmpfs", "/work/cwd:rw,size=16m,uid=10001,gid=10001,mode=0700", "-w", "/work/cwd"]
    j = new.index("XDG_STATE_HOME=/agenthome/.local/share/goose/state") + 1
    new[j:j] = ["-e", "XDG_CONFIG_HOME=/agenthome/.config"]
    return _r6(new)


def _r6(old: list[str]) -> list[str]:
    """The old argv plus exactly the rc.4 R6 change (deliberate): two empty read-only tmpfs masks over the
    gateway's files under ``/work/run``, right after the request-spool mount."""
    new = list(old)
    i = next(k for k, x in enumerate(new) if x.startswith("/spool/requests:/work/spool/requests:")) + 1
    new[i:i] = ["--tmpfs", "/work/run/gateway:ro,size=1k", "--tmpfs", "/work/run/debrief:ro,size=1k"]
    return new


@pytest.fixture
def fixed(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(agent_run, "team_dir_problem", lambda *a, **k: None)
    monkeypatch.setattr(executor, "spool_dir", lambda: Path("/spool"))
    return {**agent_run.DEFAULT_AGENT, "team_dir": "/team", "key_file": "/key", "model": "m/x"}


# ---------------------------------------------------------------- golden: no goose behaviour change

def test_golden_agent_argv_is_the_old_one_plus_revision_7(fixed: dict) -> None:
    assert agent_run.agent_argv(fixed, "img:t", RUN, SESSION, task="TASK TEXT") == _revision_7(OLD_AGENT)


def test_golden_selftest_argv_and_probe(fixed: dict) -> None:
    argv = agent_run.selftest_argv(fixed, "img:t", RUN)
    assert argv[:-1] + ["<probe>"] == _revision_7(OLD_SELFTEST)
    probe = argv[-1]
    assert probe == agent_run.SELFTEST and probe.count(CWD_LINE) == 1
    # Phase 3 (deliberate): an HTTP error status now counts as reachable (server_answers), and
    # provider_apis_blocked also tries Anthropic, OpenAI and xAI. Reverting exactly those lines gives the old probe.
    start, end = probe.index("def server_answers"), probe.index('r["gateway_reachable"]')
    assert '"https://api.anthropic.com/v1/models"' in probe[start:end]
    old = (probe[:start] + 'r["internet_blocked"] = not reach("https://example.com") and not '
           'reach("https://openrouter.ai/api/v1/models")\n' + probe[end:])
    old = old.replace("os, urllib.error, urllib.request", "os, urllib.request").replace(CWD_LINE, "")
    assert hashlib.sha256(old.encode()).hexdigest() == OLD_PROBE_SHA256


def test_golden_debrief_argv(fixed: dict) -> None:
    assert debrief.debrief_argv(fixed, "img:t", RUN, SESSION) == _revision_7(OLD_DEBRIEF)


def _b2(old: list[str], secrets: tuple[str, ...] = ()) -> list[str]:
    """The old gateway argv plus exactly the roadmap-B2 change (deliberate, 2026-10-24): the host's DLP
    context. One read-only mount of ``<spend dir>/dlp`` at ``/run/dlp`` (after the spend mount) and one per
    secrets directory (rc.4 R5), and the flags naming only fixed in-container paths, appended at the end. No host name,
    package name or secret value is in the argv."""
    new = list(old)
    i = new.index("img:t")
    mounts = ["-v", "/spend/dlp:/run/dlp:ro"]
    flags = ["--dlp-hostnames-file", "/run/dlp/hostnames", "--dlp-private-names-file", "/run/dlp/private-names"]
    for n, path in enumerate(secrets):   # rc.4 R5 (deliberate): one read-only directory mount per secrets dir
        mounts += ["--mount", f"type=bind,source={path},target=/run/secrets/dlp/{n},readonly"]
        flags += ["--dlp-secrets-mount", f"/run/secrets/dlp/{n}"]
    if secrets:
        flags += ["--dlp-skip-ok-file", "/run/dlp/skip-ok"]
    new[i:i] = mounts
    return new + flags


def test_golden_gateway_argv_is_the_old_one_plus_b2(fixed: dict) -> None:
    assert agent_run.gateway_argv(fixed, "img:t", Path("/spend")) == _b2(OLD_GATEWAY)
    ctx = agent_run.gateway_context.Context(Path("/spend/dlp"), (Path("/s/a"), Path("/s/b")))
    assert agent_run.gateway_argv(fixed, "img:t", Path("/spend"), dlp=ctx) == _b2(OLD_GATEWAY, ("/s/a", "/s/b"))


def test_the_command_override_still_substitutes_its_placeholders(fixed: dict) -> None:
    a = {**fixed, "command": ["goose", "run", "--recipe", "{recipe}", "--name", "{session}", "--x", "{run_id}",
                              "--params", "task={task}"], "recipe": "custom/r.yaml"}
    argv = agent_run.agent_argv(a, "img:t", RUN, SESSION, task="T {run_id}", run_id="R1")
    assert argv[argv.index("img:t") + 1:] == ["goose", "run", "--recipe", "/work/team/custom/r.yaml", "--name",
                                             SESSION, "--x", "R1", "--params", "task=T {run_id}"]


# ---------------------------------------------------------------- the driver API

def test_get_driver_and_validate_agent_refuse_unknown_and_unavailable_frameworks() -> None:
    assert agent_drivers.get_driver("goose") is agent_drivers.GOOSE
    assert agent_drivers.get_driver("claude") is agent_drivers.CLAUDE   # phase 3
    assert agent_drivers.get_driver("copilot") is agent_drivers.COPILOT  # phase 4
    assert agent_drivers.get_driver("codex") is agent_drivers.CODEX      # phase 4
    for fw in ("grok",):
        with pytest.raises(ValueError, match=f"framework {fw}: driver not available in this release"):
            agent_drivers.get_driver(fw)
        with pytest.raises(ValueError, match="driver not available"):
            agent_run.validate_agent({"framework": fw})
    with pytest.raises(ValueError, match="agent.framework must be one of"):
        agent_run.validate_agent({"framework": "gooses"})
    agent_run.validate_agent({"framework": "goose", "recipe": "", "command": []})


def test_the_goose_driver_contract() -> None:
    g = agent_drivers.GOOSE
    assert (g.name, g.dialect, g.default_recipe) == ("goose", "chat", ".goose/recipes/vuln-remediation.yaml")
    assert g.state_dirs() == [agent_run.GOOSE_SESSIONS_IN_CONTAINER]
    assert g.mcp_attach() == agent_drivers.McpAttach("goose-extension", "python -m agentteams.vulnscan.mcp")
    assert g.command_template() == ["goose", "run", "--recipe", "{recipe}", "--name", "{session}",
                                    "--with-extension", "python -m agentteams.vulnscan.mcp", "--params", "task={task}"]
    env = g.env({}, "http://gw:1")
    assert env["OPENROUTER_API_KEY"] == agent_drivers.DUMMY_KEY and env["OPENROUTER_HOST"] == "http://gw:1"
    assert all(v.startswith(agent_drivers.AGENT_HOME) for k, v in env.items() if k.startswith("XDG_"))
    out = g.parse_outcome("ok\nRan into this error: boom\nbye\n")
    assert out == agent_drivers.Outcome(error="Ran into this error: boom") and out.model_calls is None
    assert g.parse_outcome("all fine\n").error == ""
    assert any('r["goose_dirs_writable"]' in e for e in g.selftest_entries())
    assert g.debrief().resume("s1") == ["goose", "session", "--resume", "--name", "s1",
                                        "--with-extension", "python -m agentteams.vulnscan.mcp"]
    assert dict(g.image.sha256).keys() == {"amd64", "arm64"}
    assert all(len(v) == 64 for _, v in g.image.sha256)


def test_flags_sha256_binds_the_runtime_and_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    base = agent_run.flags_sha256()
    assert base == agent_run.flags_sha256({}) == agent_run.flags_sha256(agent_run.DEFAULT_AGENT)
    assert agent_run.flags_sha256(image_id="sha256:a") != base != agent_run.flags_sha256(image_id="sha256:b")
    assert agent_run.flags_sha256({"command": ["goose", "run"]}) != base          # an operator command override
    assert agent_run.flags_sha256({"model": "other/model", "budget_usd": 9}) == base   # config, not runtime
    other = dataclasses.replace(agent_drivers.GOOSE, name="claude")
    monkeypatch.setitem(agent_drivers.DRIVERS, "claude", other)
    assert agent_run.flags_sha256({"framework": "claude"}) != base                 # another runtime
    newer = dataclasses.replace(agent_drivers.GOOSE, image=dataclasses.replace(agent_drivers.GOOSE.image,
                                                                               version="9.9.9"))
    monkeypatch.setitem(agent_drivers.DRIVERS, "goose", newer)
    assert agent_run.flags_sha256() != base                                         # another CLI version


def test_a_goose_selftest_record_does_not_vouch_for_another_framework(monkeypatch: pytest.MonkeyPatch,
                                                                     tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent_run, "_image_id", lambda tag: "sha256:img")
    rec = {"passed": True, "image": "sha256:img", "checks": {}, "flags_sha256": agent_run.flags_sha256(image_id="sha256:img")}
    (tmp_path / "agent-selftest.json").write_text(json.dumps(rec))
    assert agent_run.selftest_ok({}) == (True, "ok")
    monkeypatch.setitem(agent_drivers.DRIVERS, "claude", dataclasses.replace(agent_drivers.GOOSE, name="claude"))
    ok, why = agent_run.selftest_ok({"agent": {"framework": "claude"}})
    assert not ok and "re-run agent-selftest" in why
    monkeypatch.delitem(agent_drivers.DRIVERS, "claude")
    ok, why = agent_run.selftest_ok({"agent": {"framework": "claude"}})
    assert not ok and "driver not available" in why


def test_an_unavailable_framework_is_not_run_and_has_no_debrief(tmp_path: Path) -> None:
    a = {**agent_run.DEFAULT_AGENT, "framework": "grok", "team_dir": str(tmp_path), "key_file": "/k"}
    assert "driver not available" in agent_run._not_run_reason({"agent": a}, a)
    assert "driver not available" in debrief._preflight({"agent": a}, a, tmp_path)
    fields = {f.name: getattr(agent_drivers.GOOSE, f.name) for f in dataclasses.fields(agent_drivers.GOOSE)}
    nodebrief = type("NoDebrief", (agent_drivers.GooseDriver,), {"debrief": lambda self: None})(**{**fields, "name": "x"})
    with pytest.raises(ValueError, match="no debrief for x"):
        debrief.resume_command("s1", nodebrief)


def test_state_dirs_are_host_backed_under_the_framework_data_dir() -> None:
    assert agent_run.sessions_dir(RUN) == RUN / "agent" / "goose-data"
    two = type("Two", (agent_drivers.GooseDriver,), {"state_dirs": lambda self: ["/a", "/b"]})(
        **{f.name: getattr(agent_drivers.GOOSE, f.name) for f in dataclasses.fields(agent_drivers.GOOSE)})
    assert agent_run.state_mounts(RUN / "agent" / "goose-data", two) == [
        (RUN / "agent" / "goose-data", "/a"), (RUN / "agent" / "goose-data-2", "/b")]


def test_the_probe_renders_the_driver_key_and_entries() -> None:
    src = selftest_probe.render("gw", 1, key_env="ANTHROPIC_API_KEY", dummy="d", entries=["r['x'] = True"])
    assert 'os.environ.get("ANTHROPIC_API_KEY") == "d"' in src and "\nr['x'] = True\n" in src
    assert 'reach("http://gw:1/healthz")' in src and '"/work/cwd"' in src
    compile(src, "probe", "exec")


# ---------------------------------------------------------------- image

def test_build_argv_is_driver_parameterised() -> None:
    argv = agent_run.build_argv(Path("/ctx"), "img:t", "1.56.0", "ab" * 32, "py@sha256:1")
    for arg in ("PYTHON_IMAGE=py@sha256:1", "AGENT_CLI=goose", "CLI_VERSION=1.56.0", f"CLI_SHA256={'ab' * 32}"):
        assert argv[argv.index(arg) - 1] == "--build-arg"
    with pytest.raises(ValueError, match="driver not available"):
        agent_run.build_argv(Path("/ctx"), "img:t", "1", "x", "py", framework="grok")
    assert agent_run.image_tag({"framework": "goose"}).startswith("agentteams-vulnscan-agent:")
    assert agent_run.image_tag({"framework": "claude"}).startswith("agentteams-vulnscan-agent-claude:")


def test_the_dockerfile_has_one_install_stage_per_framework() -> None:
    text = (agent_run.dockerfile_dir() / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG AGENT_CLI=goose" in text and "FROM cli-${AGENT_CLI}" in text
    for fw in agent_drivers.FRAMEWORKS:
        assert f"FROM base AS cli-{fw}" in text, fw
    for fw in set(agent_drivers.FRAMEWORKS) - set(agent_drivers.DRIVERS):
        assert f'echo "AGENT_CLI={fw}: driver not available in this release" >&2; exit 1' in text
    assert 'echo "${CLI_SHA256}  /tmp/goose.tar.bz2" | sha256sum -c -' in text
    assert 'echo "${CLI_SHA256}  /tmp/claude" | sha256sum -c -' in text   # phase 3
    assert "AGENT_CLI=claude: driver not available" not in text


def test_the_image_script_refuses_frameworks_without_a_driver() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "vulnscan-agent-image.sh"
    for fw, msg in (("grok", "driver not available"), ("nope", "FRAMEWORK must be one of")):
        proc = subprocess.run(["bash", str(script)], env={"FRAMEWORK": fw, "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, timeout=60, check=False)
        assert proc.returncode == 2 and msg in proc.stderr, proc.stderr


# Canned `gpg --status-fd 1 --verify` output (security review item 5). Placeholder fingerprints, not real keys.
FPR = "0123456789ABCDEF0123456789ABCDEF01234567"
OTHER = "FEDCBA9876543210FEDCBA9876543210FEDCBA98"
GOOD = f"[GNUPG:] GOODSIG {FPR[-16:]} Release Signing <release@example.invalid>"
VALID = f"[GNUPG:] VALIDSIG {FPR} 2026-10-09 1791561600 0 4 0 1 10 00 {FPR}"


@pytest.mark.parametrize(("lines", "ok"), [
    (["[GNUPG:] NEWSIG", GOOD, VALID, "[GNUPG:] TRUST_UNDEFINED 0 pgp"], True),
    ([GOOD], False),                                                         # no VALIDSIG
    ([VALID], False),                                                        # no GOODSIG
    ([GOOD.replace(FPR[-16:], OTHER[-16:]), VALID.replace(FPR, OTHER, 1)], False),   # another key
    ([GOOD, VALID.replace(FPR, OTHER, 1)], False),
    ([f"[GNUPG:] EXPKEYSIG {FPR[-16:]} Release", VALID], False),
    ([f"[GNUPG:] REVKEYSIG {FPR[-16:]} Release", GOOD, VALID], False),
    ([f"[GNUPG:] EXPSIG {FPR[-16:]} Release", GOOD, VALID], False),
    ([f"[GNUPG:] BADSIG {FPR[-16:]} Release", GOOD, VALID], False),
    (["[GNUPG:] ERRSIG 0123456789ABCDEF 1 10 00 1791561600 9 -", GOOD, VALID], False),
    ([], False),
])
def test_the_image_script_requires_goodsig_and_validsig_from_the_pinned_key(lines: list[str], ok: bool) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "vulnscan-agent-image.sh"
    proc = subprocess.run(["bash", "-c", 'source "$1" && gpg_status_ok "$2"', "test", str(script), FPR],
                          input="\n".join(lines) + "\n", env={"PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=60, check=False)
    assert (proc.returncode == 0) is ok, (lines, proc.stdout, proc.stderr)
    assert proc.stdout == ""                                                 # sourcing defines, never runs the build


def test_the_acceptance_script_strips_terminal_escapes(tmp_path: Path) -> None:
    """Security review item 7: transcript lines, refusal reasons and the self-test record print without escapes."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "vulnscan-operator" / "09-accept-framework.sh"
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", script.read_text(encoding="utf-8"), flags=re.DOTALL)
    outcome = next(b for b in blocks if "refusal_reasons" in b)
    selftest = next(b for b in blocks if "agent-selftest.json" in b)
    ctrl = re.search(r"^_CTRL = .*$", outcome, flags=re.MULTILINE)
    assert ctrl and ctrl.group(0) in selftest and "clean(k)" in selftest and "clean(str(detail" in selftest
    run = tmp_path / "2026-10-23"
    (run / "agent").mkdir(parents=True)
    (run / "gateway").mkdir()
    esc = "\x1b]8;;https://x.invalid\x07link\x1b]8;;\x07 \x1b[31mred\x1b[0m \x1b[2J\rover\x08\x07\x00\x9b"
    (run / "agent" / "status.json").write_text(json.dumps({"run_state": "failed", "reason": "boom " + esc}))
    (run / "gateway" / "spend.json").write_text(json.dumps({"calls": 1, "refusal_reasons": ["chat: " + esc]}))
    (run / "agent" / "transcript.log").write_text(f"error one {esc}\nfine line\nError two \x1b[1mbold\n")
    proc = subprocess.run([sys.executable, "-I", "-", str(run)], input=outcome, capture_output=True, text=True,
                          timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", proc.stdout), repr(proc.stdout)
    assert "boom" in proc.stdout and "red" in proc.stdout and "Error two bold" in proc.stdout
