"""Roadmap B2: the host's DLP context for the containerized gateway (:mod:`agentteams.vulnscan.gateway_context`).

Host names come from a fake socket / scutil / hosts.conf, private names from a fake registry; the secret
value is built at runtime. No Docker, no network, no real names.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_run, gateway, gateway_context, project_policy, registry
from agentteams.vulnscan import gateway_dialects as d
from agentteams.vulnscan.subproc_confine import Unavailable
from tests.test_vulnscan_gateway_dialects import (  # noqa: F401  (upstream: a fixture)
    MODEL,
    Upstream,
    _drive,
    _handler,
    _json,
    _request,
    upstream,
)
from tests.test_vulnscan_gateway_dlp import fake

HOSTS_CONF = """# name | target | notes
web1    | deploy@web1.example.com            | app
api1    | -J admin@jump.example.net:2222 admin@192.0.2.10 | via a jump host
shelf   | shelf                              | an ssh-config alias
"""
SCUTIL = {"ComputerName": "Example Studio", "LocalHostName": "example-studio"}
USAGE = {"usage": {"cost": 0.001, "prompt_tokens": 5, "completion_tokens": 5}}


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake machine: hostname, macOS names, a server kit with hosts.conf, ``ssh -G`` for the alias."""
    monkeypatch.setattr(gateway_context.socket, "gethostname", lambda: "Build-Box-7.corp.example")
    monkeypatch.setattr(gateway_context, "_scutil", lambda key: SCUTIL[key])

    def resolve(args: list[str]) -> dict[str, list[str]]:
        if args == ["shelf"]:
            return {"hostname": ["nas-03.home.example"], "proxyjump": ["none"]}
        raise Unavailable("not an alias")
    monkeypatch.setattr(gateway_context, "resolve", resolve)
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text(HOSTS_CONF, encoding="utf-8")
    return kit


def _entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[registry.Entry]:
    pats = {"a": ("acme-*",), "b": ("@acme/*", "acme-*"), "c": ()}
    monkeypatch.setattr(gateway_context.project_policy, "read_policy",
                        lambda repo: project_policy.ProjectPolicy(level="scan", private_packages=pats[repo.name]))
    return [registry.Entry(identity=f"local:{n}", fix_checkout=str(tmp_path / n)) for n in pats]


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]


# ---------------------------------------------------------------- gathering

def test_hostnames_from_socket_scutil_and_hosts_conf(host: Path) -> None:
    names = gateway_context.hostnames(host)
    assert names == sorted(["build-box-7.corp.example", "build-box-7", "example studio", "example-studio",
                            "example-studio.local", "web1.example.com", "jump.example.net", "192.0.2.10",
                            "nas-03.home.example"])
    assert not {"web1", "api1", "shelf"} & set(names)        # hosts.conf names stay usable as server targets


@pytest.mark.parametrize(("target", "want"), [
    ("deploy@web1.example.com", "web1.example.com"), ("admin@jump.example.net:2222", "jump.example.net"),
    ("ssh://u@h1.example.org:22/x", "h1.example.org"), ("[2001:db8::1]:22", "2001:db8::1"),
    ("root@192.0.2.10", "192.0.2.10"), ("x", ""), ("bad host;rm", "")])
def test_host_part(target: str, want: str) -> None:
    assert gateway_context.host_part(target) == want


def test_scutil_is_best_effort_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_context.sys, "platform", "linux")
    assert gateway_context._scutil("ComputerName") == ""


def test_private_names_are_the_union_over_every_enrolled_project(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    assert gateway_context.private_names(_entries(tmp_path, monkeypatch)) == (["@acme/*", "acme-*"], "")
    monkeypatch.setattr(gateway_context.registry, "load", lambda: (_ for _ in ()).throw(ValueError("bad")))
    names, problem = gateway_context.private_names()
    assert names == [] and "registry cannot be read" in problem


def test_secrets_files_keep_only_mountable_env_names(tmp_path: Path) -> None:
    for name in ("anthropic.env", "open,router.env", "notes.txt", "a:b.env"):
        (tmp_path / name).write_text("X_TOKEN=" + fake(20) + "\n")
    assert gateway_context.secrets_files(tmp_path) == (tmp_path / "anthropic.env",)
    assert gateway_context.secrets_files(tmp_path / "absent") == ()


# ---------------------------------------------------------------- the context files

def test_prepare_writes_private_files_and_refuses_symlinks(host: Path, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    spend, secrets = tmp_path / "run" / "gateway", tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "vendor.env").write_text("VENDOR_TOKEN=" + fake(24) + "\n")
    ctx = gateway_context.prepare(spend, kit_dir=host, secrets_dir=secrets, entries=_entries(tmp_path, monkeypatch))
    assert ctx == gateway_context.Context(spend / "dlp", (secrets / "vendor.env",))
    assert stat.S_IMODE(os.stat(ctx.directory).st_mode) == 0o700
    for name in ("hostnames", "private-names"):
        assert stat.S_IMODE(os.stat(ctx.directory / name).st_mode) == 0o600
    assert "web1.example.com" in _lines(ctx.directory / "hostnames")
    assert _lines(ctx.directory / "private-names") == ["@acme/*", "acme-*"]
    victim = tmp_path / "victim.txt"                     # a planted symlink is replaced, never written through
    victim.write_text("keep")
    (ctx.directory / "hostnames").unlink()
    (ctx.directory / "hostnames").symlink_to(victim)
    gateway_context.prepare(spend, kit_dir=host, secrets_dir=secrets, entries=[])
    assert victim.read_text() == "keep" and not (ctx.directory / "hostnames").is_symlink()
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "dlp").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="not a plain directory"):
        gateway_context.prepare(linked, kit_dir=host, secrets_dir=secrets, entries=[])


def test_gateway_argv_mounts_read_only_and_holds_no_machine_values(host: Path, tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    value = fake(24)
    (secrets / "vendor.env").write_text(f"VENDOR_TOKEN={value}\n")
    spend = tmp_path / "gateway"
    ctx = gateway_context.prepare(spend, kit_dir=host, secrets_dir=secrets, entries=_entries(tmp_path, monkeypatch))
    a = {**agent_run.DEFAULT_AGENT, "key_file": str(tmp_path / "or.env")}
    argv = agent_run.gateway_argv(a, "img:t", spend, dlp=ctx)
    mounts = [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]
    assert f"{spend / 'dlp'}:/run/dlp:ro" in mounts and f"{secrets / 'vendor.env'}:/run/secrets/dlp/vendor.env:ro" in mounts
    assert [m for m in mounts if not m.endswith(":ro")] == [f"{spend}:/work/out:rw"]   # only spend.json is writable
    joined = " ".join(argv).lower()
    for name in [*gateway_context.hostnames(host), "acme", value.lower()]:
        assert name not in joined, name
    tail = argv[argv.index("--max-unmetered-calls") + 2:]
    assert tail == ["--dlp-hostnames-file", "/run/dlp/hostnames", "--dlp-private-names-file", "/run/dlp/private-names",
                    "--dlp-secrets-file", "/run/secrets/dlp/vendor.env"]


def test_flags_sha256_covers_the_flags_but_not_the_machine(host: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = agent_run.flags_sha256()
    monkeypatch.setattr(gateway_context.socket, "gethostname", lambda: "other-machine.example")
    monkeypatch.setattr(gateway_context, "secrets_files", lambda directory=None: ())
    assert agent_run.flags_sha256() == base               # machine values never move it (no daily churn)
    monkeypatch.setattr(gateway_context, "IN_GW", "/run/elsewhere")
    assert agent_run.flags_sha256() != base               # the flags' paths do


def test_start_gateway_prepares_the_context(host: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(host))
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_run, "_docker", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(agent_run.time, "sleep", lambda s: None)
    a = {**agent_run.DEFAULT_AGENT, "key_file": str(tmp_path / "or.env")}
    agent_run._start_gateway(a, "img:t", tmp_path / "gateway")
    run = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    assert "--dlp-hostnames-file" in run and "web1.example.com" in _lines(tmp_path / "gateway" / "dlp" / "hostnames")


# ---------------------------------------------------------------- end to end through gateway.Handler

def test_the_context_file_drives_redaction_and_blocking(host: Path, tmp_path: Path, upstream: Upstream,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    value = fake(24, 5)
    (secrets / "vendor.env").write_text(f"VENDOR_TOKEN={value}\n")
    ctx = gateway_context.prepare(tmp_path / "gateway", kit_dir=host, secrets_dir=secrets,
                                  entries=_entries(tmp_path, monkeypatch))
    # the gateway's own flags, with the in-container paths mapped back to the host files
    _, flags = gateway_context.argv_parts(ctx)
    host_paths = {f"{gateway_context.IN_GW}/{n}": str(ctx.directory / n) for n in ("hostnames", "private-names")}
    host_paths |= {f"{gateway_context.SECRETS_IN_GW}/{f.name}": str(f) for f in ctx.secrets_files}
    a = gateway._parser().parse_args([host_paths.get(x, x) for x in flags] + ["--dlp-secrets-dir", ""])
    scanner = gateway._dlp_filter(a, [], [])
    meter = gateway.Meter(1.0, tmp_path / "spend.json")

    def chat(text: str) -> bytes:
        return _request(d.ROUTE, json.dumps({"model": MODEL, "messages": [{"role": "user", "content": text}]}).encode())

    upstream.script.append(_json(USAGE))
    status, _ = _drive(_handler(meter, dlp_filter=scanner), chat("built on build-box-7 for web1.example.com: acme-core"))
    assert status == 200
    assert upstream.received[-1]["body"]["messages"][0]["content"] == \
        "built on [REDACTED:hostname] for [REDACTED:hostname]: [REDACTED:private-package]"
    status, reply = _drive(_handler(meter, dlp_filter=scanner), chat(f"the token is {value}"))
    assert status == 400 and json.loads(reply)["error"]["message"].startswith("dlp: known-secret")
    assert len(upstream.received) == 1 and value not in (tmp_path / "spend.json").read_text()
    upstream.script.append(_json(USAGE))                  # control: without the context the value passes
    status, _ = _drive(_handler(gateway.Meter(1.0, None), dlp_filter=gateway._dlp_filter(
        gateway._parser().parse_args(["--dlp-secrets-dir", ""]), [], [])), chat(f"the token is {value}"))
    assert status == 200
