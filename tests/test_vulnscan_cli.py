"""``python -m agentteams.vulnscan`` through ``main()``: exit codes, argument validation, documented
command lines and config snippets, the publish-grant signing path and the GitHub note paths.

Network-free. HOME and the vulnscan dirs point into tmp_path (:func:`operator_home`); ``gh`` is a fake
on PATH answering from a JSON file; docker-backed calls (``agent_run.selftest``/``run_agent``) and ssh
(``executor.server_op``) are replaced by fakes; signing uses a fake signer holding a real Ed25519 key.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from agentteams.cli.signed_ledger import ed25519_sign
from agentteams.vulnscan import __main__ as cli
from agentteams.vulnscan import (
    agent_run,
    daily,
    debrief,
    executor,
    grant,
    ledger,
    notes,
    paths,
    publish,
)
from tests._vulnscan_helpers import operator_home, sign_grant

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "docs_src" / "vulnscan-automation-walkthrough.md", ROOT / "docs_src" / "api-reference" / "vulnscan.md",
        ROOT / "docs_src" / "vulnerability-remediation-guide.md"]
GIT = shutil.which("git") is not None
NODE = "R_kgDOtest01"
OWNER = 4242
PR = "https://github.com/o/svc/pull/7"
COMMENT = PR + "#issuecomment-345"

FAKE_GH = """#!{py}
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
answers = json.load(open(os.path.join(here, "gh.json")))
a = sys.argv[1:]
key = "repo view" if a[:2] == ["repo", "view"] else a[-1].split("?")[0]
if key not in answers:
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
print(json.dumps(answers[key]))
"""


class _TTY(io.StringIO):
    """A stdin that claims to be a terminal and answers from a string."""

    def isatty(self) -> bool:
        """Return True (an interactive terminal)."""
        return True


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolated operator home, a fake ``gh`` on PATH, and a non-terminal stdin."""
    world = operator_home(tmp_path, monkeypatch)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH.replace("{py}", sys.executable))
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    world["bin"] = bindir
    _gh(world, {})
    return world


def _gh(world: dict[str, Any], answers: dict[str, Any]) -> None:
    (world["bin"] / "gh.json").write_text(json.dumps(answers))


def _config(world: dict[str, Any], **over: Any) -> Path:
    cfg = paths.config_file()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"governance_dir": str(world["gov"]), **over}))
    return cfg


def _run(tmp: Path, date: str = "2026-10-22") -> Path:
    run = tmp / "state" / date
    run.mkdir(parents=True, exist_ok=True)
    (run / "summary.json").write_text(json.dumps({"findings": []}))
    return run


def _install_grant(world: dict[str, Any], **kw: Any) -> dict[str, Any]:
    args = {"ops": ["server-audit"], "repo_roots": [], "hosts": ["web1"], "days": 7, "max_uses": 5,
            "key_id": "op", "governance_dir": str(world["gov"])}
    args.update(kw)
    signed = sign_grant(world["pem"], grant.build(**args))
    grant.install(signed)
    return signed


def _ledger_bytes() -> bytes:
    return ledger.ledger_path().read_bytes() if ledger.ledger_path().exists() else b""


def _exit(argv: list[str]) -> int:
    """Return main()'s exit code, counting an argparse rejection (SystemExit) as its code."""
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)


# ---------------------------------------------------------------- G1, G17, G15/X12, G30: bad arguments

@pytest.mark.parametrize("cmd", ["scan", "fix"])
def test_missing_scan_or_fix_path_exits_2(env: dict, cmd: str, capsys: pytest.CaptureFixture) -> None:
    assert _exit([cmd, str(env["tmp"] / "no-such-dir")]) == 2
    assert "no-such-dir" in capsys.readouterr().err


def test_inventory_with_a_missing_log_exits_2(env: dict, capsys: pytest.CaptureFixture) -> None:
    assert _exit(["inventory", str(env["tmp"] / "missing.log")]) == 2
    assert "missing.log" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ["publish", "agent-chat"])
@pytest.mark.parametrize("run", ["../x", "2026-10-22/..", "x", "2026-13-45", "/2026-10-22"])
def test_run_must_be_a_date(env: dict, cmd: str, run: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(env)
    (env["tmp"] / "x").mkdir(exist_ok=True)        # state/../x exists: a traversal would find it
    _run(env["tmp"])
    called: list[Path] = []
    monkeypatch.setattr(daily, "publish_run", lambda cfg, run_dir, run_id: called.append(run_dir) or {"status": "done"})
    monkeypatch.setattr(debrief, "run_debrief", lambda cfg, run_dir: called.append(run_dir) or 0)
    assert _exit([cmd, "--run", run]) == 2 and not called


def test_review_since_must_be_a_date(env: dict) -> None:
    for bad in ("yesterday", "2026-13-45", "2026-1-2"):
        assert _exit(["review", "--since", bad]) == 2
    assert _exit(["review", "--since", "2026-10-22"]) == 0


# ---------------------------------------------------------------- G31: malformed status.json

@pytest.mark.parametrize("status", ["[1, 2]", "{bad", '{"run_id": {"a": 1}}', '"text"'])
def test_publish_with_a_malformed_status_uses_a_new_run_id(env: dict, status: str,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _config(env)
    run = _run(env["tmp"])
    (run / "agent").mkdir()
    (run / "agent" / "status.json").write_text(status)
    seen: list[str] = []
    monkeypatch.setattr(daily, "publish_run", lambda cfg, run_dir, run_id: seen.append(run_id) or {"status": "done"})
    assert _exit(["publish"]) == 0
    assert re.fullmatch(r"2026-10-22-[0-9a-f]{6}", seen[0])
    (run / "agent" / "status.json").write_text(json.dumps({"run_id": "2026-10-22-abc123"}))
    assert _exit(["publish"]) == 0 and seen[1] == "2026-10-22-abc123"


# ---------------------------------------------------------------- G22: the exit-code table through main()

def _setup_none(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
    """No config, no grant."""


def _setup_cfg(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
    _config(world)


def _setup_grant(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
    _config(world)
    _install_grant(world)


def _setup_server_ok(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
    _setup_grant(world, mp)
    mp.setattr(executor, "server_op", lambda op, host: {"host": host, "report": "r.md"})


def _setup_server_fails(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
    _setup_grant(world, mp)

    def boom(op: str, host: str) -> dict[str, Any]:
        raise executor.ExecError("ssh: connect to host web1: Connection refused")
    mp.setattr(executor, "server_op", boom)


def _fake_grant_signer(world: dict[str, Any], mp: pytest.MonkeyPatch, *, result: str) -> None:
    def signer(rec: dict[str, Any], **kw: Any) -> dict[str, Any] | None:
        if result == "declined":
            return None
        if result == "bad":
            return {**rec, "signature": ed25519_sign(_other_pem(), grant.signed_values(rec))}
        return sign_grant(world["pem"], rec)
    mp.setattr(grant, "sign", signer)


def _selftest(passed: bool) -> Callable[[dict[str, Any], pytest.MonkeyPatch], None]:
    def setup(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
        _config(world)
        mp.setattr(agent_run, "selftest", lambda cfg: {"passed": passed, "checks": {}})
    return setup


def _agent_run(state: str | None) -> Callable[[dict[str, Any], pytest.MonkeyPatch], None]:
    def setup(world: dict[str, Any], mp: pytest.MonkeyPatch) -> None:
        _config(world)
        if state is not None:
            _run(world["tmp"])
        mp.setattr(agent_run, "run_agent", lambda cfg, run_dir: {"state": state or "never"})
    return setup


def _other_pem() -> str:
    return Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()).decode()


SIGN = ["grant", "sign", "--op", "server-audit", "--host", "web1", "--days", "1", "--max-uses", "5", "--key-id", "op"]

EXIT_TABLE: list[tuple[str, Callable[..., None], Callable[[dict[str, Any]], list[str]], int]] = [
    ("grant-list", _setup_grant, lambda w: ["grant", "list"], 0),
    ("grant-revoke-ok", _setup_none, lambda w: ["grant", "revoke", "--grant-id", _install_grant(w)["grant_id"]], 0),
    ("grant-revoke-none", _setup_none, lambda w: ["grant", "revoke", "--grant-id", "vg-0123456789abcdef"], 1),
    ("grant-sign-ok", lambda w, mp: _fake_grant_signer(w, mp, result="ok"),
     lambda w: [*SIGN, "--governance-dir", str(w["gov"])], 0),
    ("grant-sign-declined", lambda w, mp: _fake_grant_signer(w, mp, result="declined"),
     lambda w: [*SIGN, "--governance-dir", str(w["gov"])], 1),
    ("grant-sign-bounds", lambda w, mp: _fake_grant_signer(w, mp, result="ok"),
     lambda w: [*SIGN, "--governance-dir", str(w["gov"]), "--days", "31"], 2),
    ("grant-sign-no-verify", lambda w, mp: _fake_grant_signer(w, mp, result="bad"),
     lambda w: [*SIGN, "--governance-dir", str(w["gov"])], 2),
    ("act-done", _setup_server_ok, lambda w: ["act", "server-audit", "web1"], 0),
    ("act-refused", _setup_server_ok, lambda w: ["act", "server-audit", "web2"], 1),
    ("act-failed", _setup_server_fails, lambda w: ["act", "server-audit", "web1"], 3),
    ("act-no-config", _setup_none, lambda w: ["act", "server-audit", "web1"], 2),
    ("serve-requests-no-config", _setup_none, lambda w: ["serve-requests"], 2),
    ("agent-image-no-config", _setup_none,
     lambda w: ["agent-image", "build", "--goose-version", "1.0.0", "--goose-sha256", "0" * 64], 2),
    ("agent-selftest-pass", _selftest(True), lambda w: ["agent-selftest"], 0),
    ("agent-selftest-fail", _selftest(False), lambda w: ["agent-selftest"], 1),
    ("agent-selftest-no-config", _setup_none, lambda w: ["agent-selftest"], 2),
    ("agent-run-completed", _agent_run("completed"), lambda w: ["agent-run"], 0),
    ("agent-run-error", _agent_run("error"), lambda w: ["agent-run"], 3),
    ("agent-run-no-scan", _agent_run(None), lambda w: ["agent-run"], 2),
    ("agent-run-no-config", _setup_none, lambda w: ["agent-run"], 2),
    ("review", _setup_none, lambda w: ["review"], 0),
    ("review-ack", _setup_none, lambda w: ["review", "--ack", "--note", "checked"], 0),
    ("halts-no-config", _setup_none, lambda w: ["halts"], 0),
    ("publish-no-config", _setup_none, lambda w: ["publish"], 2),
    ("agent-chat-no-config", _setup_none, lambda w: ["agent-chat"], 2),
    ("server-kit-dest", _setup_none, lambda w: ["server-kit", str(w["tmp"] / "kit-out")], 0),
    ("init-server", _setup_none, lambda w: ["init", "--server", "web1", "--repo-root", "~/code"], 0),
]


@pytest.mark.parametrize("setup,argv,code", [row[1:] for row in EXIT_TABLE], ids=[row[0] for row in EXIT_TABLE])
def test_exit_code_table(env: dict, monkeypatch: pytest.MonkeyPatch, setup: Callable[..., None],
                         argv: Callable[[dict[str, Any]], list[str]], code: int) -> None:
    setup(env, monkeypatch)
    assert _exit(argv(env)) == code


def test_exit_table_side_effects(env: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """The rows' effects, not only their codes: what was installed, ledgered or written."""
    _fake_grant_signer(env, monkeypatch, result="declined")
    assert _exit([*SIGN, "--governance-dir", str(env["gov"])]) == 1 and not grant.load_active()[0]
    _fake_grant_signer(env, monkeypatch, result="bad")
    assert _exit([*SIGN, "--governance-dir", str(env["gov"])]) == 2
    assert not grant.grants_dir().exists() or not list(grant.grants_dir().glob("vg-*.json"))
    _fake_grant_signer(env, monkeypatch, result="ok")
    assert _exit([*SIGN, "--governance-dir", str(env["gov"])]) == 0
    [g] = grant.load_active()[0]
    capsys.readouterr()
    assert _exit(["grant", "list"]) == 0 and json.loads(capsys.readouterr().out)["grant_id"] == g["grant_id"]
    _config(env)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host})
    assert _exit(["act", "server-audit", "web1", "--rationale", "operator check"]) == 0
    assert [r["kind"] for r in ledger.read()][-2:] == ["begun", "done"]
    assert _exit(["review", "--ack", "--note", "seen"]) == 0 and ledger.read()[-1]["kind"] == "ack"
    assert _exit(["server-kit", str(env["tmp"] / "kit-out")]) == 0
    assert (env["tmp"] / "kit-out" / "hosts.conf").is_file() and (env["tmp"] / "kit-out" / "remote.sh").is_file()


def test_init_server_writes_the_config(env: dict) -> None:
    assert _exit(["init", "--server", "web1", "--repo-root", "~/code"]) == 0
    cfg = json.loads(paths.config_file().read_text())
    assert cfg["servers"] == ["web1"] and cfg["repo_roots"] == ["~/code"]
    assert _exit(["init", "--server", "web2"]) == 0                       # never overwrites
    assert json.loads(paths.config_file().read_text())["servers"] == ["web1"]


@pytest.mark.parametrize("argv", [["act", "server-audit", "web1"], ["serve-requests"], ["agent-run"], ["agent-selftest"],
                                  ["halts"], ["publish"], ["agent-chat"], ["daily"], ["schedule", "install"]])
def test_malformed_config_json_exits_2(env: dict, argv: list[str], capsys: pytest.CaptureFixture) -> None:
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text("{not json")
    assert _exit(argv) == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("over", [{"agent": "yes"}, {"servers": "web1"}, {"agent": {"enabled": "false"}}])
def test_wrongly_typed_config_exits_2(env: dict, over: dict) -> None:
    _config(env, **over)
    assert _exit(["act", "server-audit", "web1"]) == 2


# ---------------------------------------------------------------- G23: documented command lines parse

_PLACEHOLDER = "PLACEHOLDER"
_INT_OPTS = {"--owner-id", "--days", "--max-uses", "--hour", "--minute", "--cooldown-days"}
_STOP = {";", "|", "||", "&&", "&", ")"}


def _documented_commands() -> list[tuple[str, str]]:
    """Return ``(doc:line, command text)`` for every ``python -m agentteams.vulnscan`` line in the docs."""
    found: list[tuple[str, str]] = []
    for doc in DOCS:
        lines = doc.read_text(encoding="utf-8").splitlines()
        fenced = False
        i = 0
        while i < len(lines):
            start, line = i, lines[i]
            if line.lstrip().startswith("```"):
                fenced = not fenced
                i += 1
                continue
            while fenced and line.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                line = line.rstrip()[:-1] + " " + lines[i].strip()
            for m in re.finditer(r"python (?:-P )?-m agentteams\.vulnscan(?![.\w-])", line):
                rest = line[m.end():]
                if not fenced:
                    rest = rest.split("`")[0]
                found.append((f"{doc.name}:{start + 1}", rest))
            i += 1
    return found


def _argv(text: str) -> list[str] | None:
    """Turn documented command text into argv (placeholders filled), or None for a generic mention."""
    text = re.sub(r'"\$\([^)]*\)"', "1", text)
    text = re.sub(r"<[^>]*>", _PLACEHOLDER, text)
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    argv: list[str] = []
    for tok in lex:
        if tok in _STOP:
            break
        argv.append(tok)
    if not argv or argv[0] == _PLACEHOLDER:
        return None
    return [("1" if prev in _INT_OPTS else "x") if tok == _PLACEHOLDER else tok.replace(_PLACEHOLDER, "x")
            for prev, tok in zip(["", *argv[:-1]], argv, strict=True)]


def test_every_documented_command_line_parses() -> None:
    parser = cli.build_parser()
    checked, bad = 0, []
    for where, text in _documented_commands():
        argv = _argv(text)
        if argv is None:
            continue
        checked += 1
        if argv == ["--help"]:
            continue
        try:
            ns = parser.parse_args(argv)
        except SystemExit:
            bad.append(f"{where}: {text.strip()}")
            continue
        assert isinstance(ns, argparse.Namespace) and ns.cmd == argv[0], where
    assert checked >= 40, checked                     # the extractor found the docs' commands
    assert not bad, "documented commands that do not parse:\n" + "\n".join(bad)


def test_the_extractor_sees_continuations_and_inline_commands() -> None:
    texts = [t for _, t in _documented_commands()]
    assert any("--governance-dir" in t and "grant sign" in t for t in texts)   # backslash continuation joined
    assert any(t.strip().startswith("runtimes;") for t in texts)                # an inline table command
    assert _argv(' publish-grant sign --owner-id "$(gh api users/<owner> --jq .id)" --key-id <id>') == [
        "publish-grant", "sign", "--owner-id", "1", "--key-id", "x"]


# ---------------------------------------------------------------- G24: documented config + defaults tables

def _table(doc: str, heading: str) -> dict[str, str]:
    """Return ``{key: default cell}`` of the first table after ``heading``."""
    body = doc.split(heading, 1)[1]
    rows: dict[str, str] = {}
    started = False
    for line in body.splitlines():
        m = re.match(r"^\| `([a-z_]+)` \| (.*?) \| .* \|$", line)
        if m:
            rows[m.group(1)] = m.group(2)
            started = True
        elif started and not line.startswith("|"):
            break
    return rows


def _cell(text: str) -> Any:
    return json.loads(text.strip().strip("`"))


def test_documented_agent_snippet_loads(env: dict) -> None:
    doc = DOCS[0].read_text(encoding="utf-8")
    snippet = re.search(r"```json\n(\"agent\": \{.*?\n\})\n```", doc, re.DOTALL)
    assert snippet, "the walkthrough's agent block moved"
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text("{" + snippet.group(1) + "}")
    cfg = daily.load_config()
    a = agent_run.agent_cfg(cfg)
    assert a["upstream"] == "https://openrouter.ai" and a["enabled"] is False and a["max_minutes"] == 45
    assert a["recipe"] == agent_run.DEFAULT_AGENT["recipe"]          # unspecified keys keep their defaults
    assert cfg["fail_on"] == daily.DEFAULT_CONFIG["fail_on"]


def test_config_defaults_table_matches_default_config() -> None:
    rows = _table(DOCS[1].read_text(encoding="utf-8"), "## Configuration: `vulnscan.json`")
    assert set(rows) == set(daily.DEFAULT_CONFIG)
    for key, cell in rows.items():
        if key != "agent":
            assert _cell(cell) == daily.DEFAULT_CONFIG[key], key
    for key, value in daily.DEFAULT_CONFIG["agent"].items():          # the starter agent block agrees too
        assert agent_run.DEFAULT_AGENT[key] == value, key


def test_agent_defaults_table_matches_default_agent() -> None:
    rows = _table(DOCS[1].read_text(encoding="utf-8"), "The `agent` block")
    debrief_defaults = {"debrief_budget_usd": debrief.DEFAULT_DEBRIEF_BUDGET_USD,
                        "debrief_max_minutes": debrief.DEFAULT_DEBRIEF_MAX_MINUTES,
                        "debrief_max_tokens": debrief.DEFAULT_DEBRIEF_MAX_TOKENS}
    assert set(rows) == set(agent_run.DEFAULT_AGENT) | set(debrief_defaults)
    for key, cell in rows.items():
        assert _cell(cell) == {**agent_run.DEFAULT_AGENT, **debrief_defaults}[key], key


# ---------------------------------------------------------------- G25/V24: publish-grant sign via the CLI

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def ops(env: dict) -> Path:
    """A real ops checkout whose ``origin`` is a local bare repository, and a fake gh that passes preflight."""
    remote = env["tmp"] / "ops.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = env["tmp"] / "ops"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("ops\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    _gh(env, _ops_answers())
    return repo


def _ops_answers(**over: Any) -> dict[str, Any]:
    answers = {"repo view": {"id": NODE, "isPrivate": True, "nameWithOwner": "alice/ops"},
               "repos/alice/ops": {"node_id": NODE, "private": True, "owner": {"id": OWNER, "login": "alice"}},
               "user": {"id": OWNER, "login": "alice"},
               "repos/alice/ops/actions/permissions": {"enabled": False},
               "repos/alice/ops/collaborators": [{"id": OWNER, "login": "alice"}],
               "repos/alice/ops/invitations": [], "repos/alice/ops/keys": []}
    answers.update(over)
    return answers


def _publish_signer(world: dict[str, Any], mp: pytest.MonkeyPatch, result: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def signer(rec: dict[str, Any], **kw: Any) -> dict[str, Any] | None:
        calls.append(rec)
        if result == "declined":
            return None
        pem = _other_pem() if result == "bad" else world["pem"]
        return {**rec, "signature": ed25519_sign(pem, publish.signed_values(rec))}
    mp.setattr(publish, "sign", signer)
    return calls


def _publish_grant_files() -> list[Path]:
    return sorted(publish.grants_dir().iterdir()) if publish.grants_dir().exists() else []


@pytest.mark.skipif(not GIT, reason="git not available")
def test_publish_grant_sign_through_the_cli(env: dict, ops: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture) -> None:
    argv = ["publish-grant", "sign", "--ops-repo", str(ops), "--node-id", NODE, "--owner-id", str(OWNER),
            "--governance-dir", str(env["gov"]), "--key-id", "op", "--days", "7"]
    _publish_signer(env, monkeypatch, "declined")
    assert _exit(argv) == 1 and _publish_grant_files() == []
    _publish_signer(env, monkeypatch, "bad")
    assert _exit(argv) == 2 and _publish_grant_files() == []
    assert "does not verify" in capsys.readouterr().err
    _gh(env, _ops_answers(**{"repo view": {"id": NODE, "isPrivate": False, "nameWithOwner": "alice/ops"}}))
    calls = _publish_signer(env, monkeypatch, "ok")
    assert _exit(argv) == 2 and _publish_grant_files() == [] and calls == []      # refused before signing
    assert "not private" in capsys.readouterr().err
    assert _exit([*argv, "--days", "31"]) == 2 and _publish_grant_files() == [] and calls == []
    _gh(env, _ops_answers())
    assert _exit(argv) == 0
    [path] = _publish_grant_files()
    assert calls[0]["remote_url"] == str(env["tmp"] / "ops.git")                   # default: the checkout's origin
    [g] = publish.load_active()[0]
    assert path.name == f"{g['grant_id']}.json" and g["ops_repo"] == str(ops.resolve())
    capsys.readouterr()
    assert _exit(["publish-grant", "list"]) == 0 and json.loads(capsys.readouterr().out)["uses"] == 0
    assert _exit(["publish-grant", "revoke", "--grant-id", g["grant_id"]]) == 0 and _publish_grant_files() == []
    assert _exit(["publish-grant", "revoke", "--grant-id", g["grant_id"]]) == 1


# ---------------------------------------------------------------- G26: say --from-gh and review --gh-replies

def _comment_answers(author_id: int = OWNER) -> dict[str, Any]:
    return {"repos/o/svc/issues/comments/345": {"body": "/vulnscan note hold the lodash bump", "html_url": COMMENT,
                                                "user": {"login": "alice", "id": author_id}},
            "user": {"id": OWNER, "login": "alice"},
            "repos/o/svc/issues/7/comments": [{"id": 345, "body": "/vulnscan note hold it", "html_url": COMMENT,
                                               "created_at": "2026-10-22T01:02:03Z",
                                               "user": {"login": "bob", "id": 7}}]}


def test_say_from_gh_through_main(env: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    _gh(env, _comment_answers())
    assert _exit(["say", "--from-gh", COMMENT]) == 2                              # no terminal: refused
    assert "interactive confirmation" in capsys.readouterr().err and not notes.active()[0]
    monkeypatch.setattr(sys, "stdin", _TTY("n\n"))
    assert _exit(["say", "--from-gh", COMMENT]) == 2 and not notes.active()[0]    # declined: nothing stored
    monkeypatch.setattr(sys, "stdin", _TTY("y\n"))
    assert _exit(["say", "--from-gh", "345", "--repo", "o/svc", "--days", "3"]) == 0
    [note] = notes.active()[0]
    assert note["source"] == f"gh:{COMMENT}" and note["text"].strip() == "hold the lodash bump"
    _gh(env, _comment_answers(author_id=99))                                      # someone else's comment
    monkeypatch.setattr(sys, "stdin", _TTY("y\n"))
    assert _exit(["say", "--from-gh", COMMENT]) == 2 and len(notes.active()[0]) == 1


def test_review_gh_replies_through_main(env: dict, capsys: pytest.CaptureFixture) -> None:
    _gh(env, _comment_answers())
    with ledger.locked():
        ledger.append("done", op="open-pr", target="/r", request_id="r1", detail=json.dumps({"pr": PR}))
    assert _exit(["review"]) == 0 and "## GitHub replies" not in capsys.readouterr().out
    assert _exit(["review", "--gh-replies"]) == 0
    section = capsys.readouterr().out.split("## GitHub replies")[1]
    assert "author: bob" in section and "note prefix: yes" in section
    _gh(env, {})
    assert _exit(["review", "--gh-replies"]) == 0 and "could not list replies" in capsys.readouterr().out
