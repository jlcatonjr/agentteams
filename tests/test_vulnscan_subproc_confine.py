"""The tools container for the executor's and daily run's subprocesses (W1, decision D1). Docker is mocked.

No container runs and nothing touches the network: ``subprocess.run`` is replaced by :class:`FakeDocker`, which
answers the ``docker`` and ``gh`` commands :mod:`agentteams.vulnscan.subproc_confine` issues and records every argv.
Host git is used only to create temporary repositories (``git_layout`` / ``git_mounts`` read their ``.git`` files).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import daily, executor, fix, review, tools_image
from agentteams.vulnscan import subproc_confine as sc
from agentteams.vulnscan import subproc_confine_ssh as ssh
from tests._vulnscan_helpers import operator_env

GIT = shutil.which("git")
ON = {"confine": {"subprocesses": True}}
IMAGE_ID = "sha256:" + "ab" * 32
TOKEN = "gho_" + "T" * 36   # a fake token: it must reach stdin only


class FakeDocker:
    """Stand-in for ``subprocess.run``: docker plumbing succeeds; ``op`` decides each confined command's result."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.origin = "https://github.com/octo/repo.git"
        self.op_rc = 0
        self.op_out = ""
        self.raise_on_op: BaseException | None = None

    def ops(self) -> list[tuple[list[str], dict[str, Any]]]:
        return [(a, k) for a, k in self.calls if a[:3] == ["docker", "run", "--rm"]]

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        argv = list(argv)
        self.calls.append((argv, kw))
        text = kw.get("text", False)

        def done(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(argv, rc, out if text else out.encode(), err if text else err.encode())

        if argv[:3] == ["docker", "image", "inspect"]:
            return done(out=IMAGE_ID + "\n")
        if argv[:3] == ["docker", "network", "inspect"]:   # ensure_network: the tools network is --internal
            return done(out="true\n")
        if argv[:2] == ["docker", "logs"]:
            return done(err=f"{sc.PROXY_READY} on 0.0.0.0:3128\n")
        if argv[:2] == ["gh", "auth"]:
            return done(out=TOKEN + "\n")
        if argv[:3] == ["docker", "run", "--rm"]:
            at = next(i for i, a in enumerate(argv) if a == "img" or a.startswith("agentteams-vulnscan-tools:"))
            inner = argv[at + 1:]
            if "get-url" in inner:
                return done(out=self.origin + "\n")
            if self.raise_on_op is not None:
                raise self.raise_on_op
            return done(rc=self.op_rc, out=self.op_out)
        return done()


@pytest.fixture
def host_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolated vulnscan dirs with the real ``subprocess`` (host git builds the test repositories)."""
    return operator_env(tmp_path, monkeypatch)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    w = operator_env(tmp_path, monkeypatch)
    fake = FakeDocker()
    monkeypatch.setattr(subprocess, "run", fake)
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker" if name == "docker"
                        else real_which(name, *a, **k))
    return {**w, "fake": fake}


def _passed_selftest() -> None:
    path = sc.selftest_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": True, "image_id": IMAGE_ID, "flags_sha256": sc.flags_sha256(IMAGE_ID)}))


def _confined(tmp: Path, *scratch: Path) -> Any:
    return sc._activate(sc.Confined(image="img", scratch=tuple(p.resolve() for p in (tmp / "state" / "worktrees",
                                                                                      *scratch))))


def _repo(path: Path, origin: str = "https://github.com/octo/repo.git") -> Path:
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    (path / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {origin}\n')
    return path


def _mounts(argv: list[str]) -> dict[str, str]:
    """``{dst: "rw"|"ro"}`` of a docker run argv."""
    out = {}
    for i, a in enumerate(argv):
        if a == "--mount":
            fields = dict(f.split("=", 1) if "=" in f else (f, "") for f in argv[i + 1].split(","))
            out[fields["dst"]] = "ro" if "readonly" in fields else "rw"
    return out


def _env(argv: list[str]) -> dict[str, str]:
    return dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e")


# ---------------------------------------------------------------- flags and mounts (pure)

def test_container_argv_is_hardened(world: dict) -> None:
    rw, ro = world["tmp"] / "rw", world["tmp"] / "ro"
    rw.mkdir()
    ro.mkdir()
    argv = sc.container_argv("img", ["git", "status"], name="n", mounts=[sc.Mount(rw, writable=True), sc.Mount(ro)],
                             env={"GIT_X": "1"}, uid=501, gid=20)
    for flag in ("--read-only", "--rm"):
        assert flag in argv
    joined = " ".join(argv)
    assert "--cap-drop ALL" in joined and "--security-opt no-new-privileges" in joined and "--user 501:20" in joined
    assert "--network none" in joined and f"{sc.HOME_IN}:rw,size=1g,uid=501,gid=20,mode=0700" in joined
    assert _mounts(argv) == {str(rw.resolve()): "rw", str(ro.resolve()): "ro"}
    assert "-v" not in argv and _env(argv) == {"HOME": sc.HOME_IN, "GIT_X": "1"}
    assert argv[argv.index("img") + 1:] == ["git", "status"]


def test_mount_problem_refuses_home_its_ancestors_and_credential_dirs(world: dict) -> None:
    home = world["home"]
    for bad in (home, home.parent, Path("/"), home / ".ssh", home / ".ssh" / "id_ed25519", home / ".config" / "gh",
                home / ".config" / "agentteams" / "keys" / "k.pem", home / ".aws"):
        assert sc.mount_problem(bad), bad
    assert not sc.mount_problem(home / "code" / "repo") and not sc.mount_problem(world["tmp"] / "state")
    assert not sc.mount_problem(home / ".ssh" / "known_hosts", secret_ok=True)
    with pytest.raises(sc.Unavailable, match="credential location"):
        sc.container_argv("img", ["x"], name="n", mounts=[sc.Mount(home / ".ssh")], env={})


def test_same_path_twice_keeps_one_mount_and_rw_wins(world: dict) -> None:
    p = world["tmp"] / "p"
    p.mkdir()
    argv = sc.container_argv("img", ["x"], name="n", mounts=[sc.Mount(p), sc.Mount(p, writable=True)], env={})
    assert _mounts(argv) == {str(p.resolve()): "rw"} and argv.count("--mount") == 1


def test_config_block_validation_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sc.config_problem({}) == "" and sc.config_problem({"confine": {}}) == ""
    assert "unknown keys" in sc.config_problem({"confine": {"subprocess": False}})
    assert "true or false" in sc.config_problem({"confine": {"subprocesses": "no"}})
    assert "must be an object" in sc.config_problem({"confine": []})
    monkeypatch.setattr(sc, "ENABLED_BY_DEFAULT", True)   # production default (conftest turns it off)
    assert sc.enabled({}) and sc.enabled({"confine": {}}) and not sc.opted_out({})
    assert not sc.enabled({"confine": {"subprocesses": False}}) and sc.opted_out({"confine": {"subprocesses": False}})


def test_daily_load_config_rejects_a_bad_confine_block(world: dict) -> None:
    from agentteams.vulnscan import paths

    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(json.dumps({"confine": {"subprocesses": 1}}))
    with pytest.raises(ValueError, match="confine.subprocesses"):
        daily.load_config()


# ---------------------------------------------------------------- fail closed and opt-out

def test_scope_fails_closed_without_docker(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    with pytest.raises(sc.Unavailable, match="tools container unavailable: docker is not installed"):
        sc.scope(ON)


def test_scope_fails_closed_without_a_passing_selftest_for_this_image(world: dict) -> None:
    with pytest.raises(sc.Unavailable, match="tools-selftest"):
        sc.scope(ON)
    _passed_selftest()
    with sc.scope(ON) as state:
        assert state is not None and sc.active() is state and state.image == sc.image_tag()
    assert sc.active() is None
    rec = json.loads(sc.selftest_record_path().read_text())
    sc.selftest_record_path().write_text(json.dumps({**rec, "flags_sha256": "0" * 64}))   # flags changed since
    with pytest.raises(sc.Unavailable, match="tools-selftest"):
        sc.scope(ON)


def test_executor_refuses_and_runs_no_git_when_the_container_is_unavailable(world: dict,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    repo = _repo(world["tmp"] / "repos" / "r")
    res = executor.act("stage-fix", str(repo), {**ON, "governance_dir": str(world["gov"])})
    assert res["status"] == "refused" and "tools container unavailable" in res["detail"]
    assert not [a for a, _ in world["fake"].calls if a and a[0] == "git"]   # nothing fell back to the host
    from agentteams.vulnscan import ledger

    assert ledger.read()[-1]["kind"] == "refused"


def test_opt_out_runs_on_the_host_and_warns(world: dict, capsys: pytest.CaptureFixture[str]) -> None:
    from agentteams.vulnscan import paths

    with sc.scope({"confine": {"subprocesses": False}}) as state:
        assert state is None and sc.active() is None
    assert sc.OPT_OUT_WARNING in capsys.readouterr().err
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(json.dumps({"confine": {"subprocesses": False}}))
    assert sc.OPT_OUT_WARNING in "\n".join(review._confinement_notice())
    paths.config_file().write_text(json.dumps({}))
    assert review._confinement_notice() == []


def test_outside_a_scope_git_runs_on_the_host_with_the_gh_helper(world: dict) -> None:
    sc.run_git(world["tmp"], ("fetch", "origin"), hardening=("-c", "a=b"), env={}, push=True)
    argv, kw = world["fake"].calls[-1]
    assert argv[0] == "git" and "credential.helper=!gh auth git-credential" in argv and kw["env"] == {}


# ---------------------------------------------------------------- git mounts

@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_git_mounts_read_only_queries_and_writes(host_world: dict) -> None:
    world = host_world
    repo = world["tmp"] / "repos" / "r"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "sub").mkdir()
    lay = sc.git_layout(repo / "sub")
    assert lay.top == repo.resolve() and lay.gitdir == lay.common == (repo / ".git").resolve() and not lay.linked
    scratch = [world["tmp"].resolve() / "state" / "worktrees"]
    rw, ro, _ = sc.git_mounts(repo, ("rev-parse", "HEAD"), readonly=False, scratch=scratch)
    assert rw == [] and ro == [repo.resolve()]
    rw, ro, _ = sc.git_mounts(repo, ("status",), readonly=True, scratch=scratch)
    assert rw == [] and ro == [repo.resolve()]
    rw, ro, _ = sc.git_mounts(repo, ("branch", "-D", "x"), readonly=False, scratch=scratch)
    assert rw == [(repo / ".git").resolve()] and ro == [repo.resolve()]   # the main checkout itself stays read-only
    assert sc.git_reads_only(("config", "--get", "remote.origin.url")) and not sc.git_reads_only(("config", "a", "b"))
    assert sc.git_reads_only(("symbolic-ref", "--short", "refs/remotes/origin/HEAD"))
    assert sc.git_reads_only(("worktree", "list", "--porcelain")) and not sc.git_reads_only(("worktree", "prune"))


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_worktree_paths_must_be_under_a_scratch_root_and_others_stay_visible(host_world: dict) -> None:
    world = host_world
    repo = world["tmp"] / "repos" / "r"
    repo.mkdir(parents=True)
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*g, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    other = world["tmp"] / "elsewhere" / "wt"
    subprocess.run([*g, "worktree", "add", "-q", "--detach", str(other)], check=True)
    scratch = (world["tmp"] / "state" / "worktrees").resolve()
    rw, ro, extra = sc.git_mounts(repo, ("worktree", "add", "-q", "--detach", str(scratch / "new"), "HEAD"),
                                  readonly=False, scratch=[scratch])
    assert scratch in rw and scratch.is_dir() and (repo / ".git").resolve() in rw
    assert sc.Mount(other.resolve() / ".git") in extra   # visible, so never prunable inside
    with pytest.raises(sc.Unavailable, match="outside the confined scratch roots"):
        sc.git_mounts(repo, ("worktree", "add", str(world["tmp"] / "x")), readonly=False, scratch=[scratch])
    lay = sc.git_layout(other)
    assert lay.linked and lay.common == (repo / ".git").resolve() and lay.top == other.resolve()
    rw, ro, _ = sc.git_mounts(other, ("add", "-u"), readonly=False, scratch=[scratch])
    assert other.resolve() in ro   # a linked worktree outside the scratch roots is never written


# ---------------------------------------------------------------- confined git

def test_credentialed_fetch_reaches_only_origin_and_the_token_goes_to_stdin(world: dict) -> None:
    repo = _repo(world["tmp"] / "repos" / "r")
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "PATH": "/host/bin", "AWS_SECRET": "x"}
    with _confined(world["tmp"]):
        sc.run_git(repo, ("fetch", "--upload-pack=git-upload-pack", "-q", "origin"),
                   hardening=executor._GIT_HARDENING, env=env, push=True)
    fake = world["fake"]
    proxies = [a for a, _ in fake.calls if a[:3] == ["docker", "run", "-d"]]
    assert len(proxies) == 1 and proxies[0][-2:] == ["--allow", "github.com:443"] and "--read-only" in proxies[0]
    argv, kw = fake.ops()[-1]
    inner = argv[argv.index("img") + 1:]
    assert inner[:2] == [sc.WITH_TOKEN, "git"] and f"credential.helper={sc.CRED_HELPER}" in inner
    assert "gc.auto=0" in inner and inner[-4:] == ["fetch", "--upload-pack=git-upload-pack", "-q", "origin"]
    assert kw["input"] == TOKEN + "\n" and all(TOKEN not in part for cmd, _ in fake.calls for part in cmd)
    e = _env(argv)
    assert e["VULNSCAN_GIT_HOST"] == "github.com" and e["HTTPS_PROXY"].startswith("http://agentteams-vulnscan-tools-")
    assert "PATH" not in e and "AWS_SECRET" not in e
    assert argv[argv.index("--network") + 1] == sc.NETWORK
    assert _mounts(argv)[str(repo.resolve() / ".git")] == "rw"
    assert [a[:3] for a, _ in fake.calls if a[:3] == ["docker", "rm", "-f"]]   # the proxy is removed afterwards


def test_ssh_origin_is_rewritten_to_https_and_a_port_is_refused(world: dict) -> None:
    repo = _repo(world["tmp"] / "repos" / "r")
    world["fake"].origin = "git@github.com:octo/repo.git"
    with _confined(world["tmp"]):
        sc.run_git(repo, ("ls-remote", "origin"), hardening=(), env={}, push=False)
    inner = world["fake"].ops()[-1][0]
    assert "url.https://github.com/.insteadOf=git@github.com:" in inner and sc.WITH_TOKEN not in inner
    with pytest.raises(sc.Unavailable, match="cannot be reached"):
        sc.classify_remote("ssh://git@github.com:2222/octo/repo.git")
    with pytest.raises(sc.Unavailable):
        sc.classify_remote("ext::sh -c evil")
    assert sc.classify_remote("https://git.example.com:8443/o/r").endpoint == "git.example.com:8443"


def test_local_origin_is_mounted_and_gets_no_network(world: dict) -> None:
    repo = _repo(world["tmp"] / "repos" / "r")
    bare = world["tmp"] / "remote.git"
    bare.mkdir()
    world["fake"].origin = str(bare)
    with _confined(world["tmp"]):
        sc.run_git(repo, ("fetch", "origin"), hardening=(), env={}, push=True)
        fetch = world["fake"].ops()[-1][0]
        sc.run_git(repo, ("push", "origin", "HEAD:refs/heads/x"), hardening=(), env={}, push=True)
        push = world["fake"].ops()[-1][0]
    assert fetch[fetch.index("--network") + 1] == "none" and _mounts(fetch)[str(bare.resolve())] == "ro"
    assert _mounts(push)[str(bare.resolve())] == "rw"
    assert not [a for a, _ in world["fake"].calls if a[:2] == ["gh", "auth"]]   # no token for a local remote


def test_docker_failures_never_read_as_git_answers(world: dict) -> None:
    repo = _repo(world["tmp"] / "repos" / "r")
    world["fake"].op_rc = 125
    with _confined(world["tmp"]), pytest.raises(sc.Unavailable, match="docker exit 125"):
        sc.run_git(repo, ("rev-parse", "--verify", "-q", "refs/heads/x"), hardening=(), env={})


def test_timeout_removes_the_container(world: dict) -> None:
    repo = _repo(world["tmp"] / "repos" / "r")
    world["fake"].raise_on_op = subprocess.TimeoutExpired(["docker"], 1)
    with _confined(world["tmp"]), pytest.raises(subprocess.TimeoutExpired):
        sc.run_git(repo, ("status",), hardening=(), env={}, timeout=1)
    op_name = world["fake"].ops()[-1][0][world["fake"].ops()[-1][0].index("--name") + 1]
    assert ["docker", "rm", "-f", op_name] in [a for a, _ in world["fake"].calls]


def test_gh_stays_on_the_host_but_never_runs_in_the_worktree(world: dict) -> None:
    wt = _repo(world["tmp"] / "state" / "worktrees" / "wt")
    assert executor._open_pr_url(wt, "github.com/octo/repo", "vulnscan/x") == ""
    argv, kw = world["fake"].calls[-1]
    assert argv[:3] == ["gh", "pr", "list"] and Path(kw["cwd"]) != wt and not any(Path(kw["cwd"]).iterdir())
    assert kw["env"]["GIT_CEILING_DIRECTORIES"] == str(world["tmp"] / "state")


def test_identity_files_name_the_operator_uid(world: dict) -> None:
    mounts = sc._identity_mounts()
    passwd = mounts[0].source.read_text()
    assert f":{os.getuid()}:{os.getgid()}:vulnscan:{sc.HOME_IN}:" in passwd and mounts[0].dest == "/etc/passwd"


# ---------------------------------------------------------------- npm

def test_npm_fix_runs_in_the_container_with_only_the_scratch_copy(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = world["tmp"] / "repos" / "p"
    proj.mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    (proj / "package-lock.json").write_text("{}")
    monkeypatch.setenv("npm_config_registry", "https://evil.example/")
    with _confined(world["tmp"]):
        fix.npm_fix(proj, apply=True)
    argv, _ = world["fake"].ops()[-1]
    inner = argv[argv.index("img") + 1:]
    assert inner[:3] == ["npm", "audit", "fix"] and "--ignore-scripts" in inner and "--package-lock-only" in inner
    proxies = [a for a, _ in world["fake"].calls if a[:3] == ["docker", "run", "-d"]]
    assert proxies[-1][-2:] == ["--allow", sc.NPM_ENDPOINT]
    rw = [d for d, m in _mounts(argv).items() if m == "rw"]
    scratch = (world["tmp"] / "state" / "tools-scratch").resolve()
    assert len(rw) == 1 and Path(rw[0]).parent == scratch
    e = _env(argv)
    assert set(e) - set(sc.proxy_env("p")) == {"HOME", "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG"}
    assert str(proj) not in " ".join(argv)   # the repository itself is never mounted for npm


def test_npm_endpoint_is_fix_registry_host() -> None:
    from urllib.parse import urlsplit

    assert sc.NPM_ENDPOINT == f"{urlsplit(fix.NPM_REGISTRY).hostname}:443"


# ---------------------------------------------------------------- egress proxy

def _proxy_module() -> Any:
    spec = importlib.util.spec_from_file_location("egress_proxy", sc.data_dir() / "egress_proxy.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_egress_proxy_allows_exactly_the_listed_endpoints() -> None:
    proxy = _proxy_module()
    allow = frozenset({"github.com:443"})
    assert proxy.decide("CONNECT github.com:443 HTTP/1.1\r\n", allow) == ("github.com", 443)
    assert proxy.decide("CONNECT GitHub.com:443 HTTP/1.0\r\n", allow) == ("github.com", 443)
    for line in ("CONNECT github.com:22 HTTP/1.1\r\n", "CONNECT evil.example:443 HTTP/1.1\r\n",
                 "CONNECT 140.82.112.3:443 HTTP/1.1\r\n", "GET http://github.com/ HTTP/1.1\r\n",
                 "CONNECT github.com:443@evil.example:443 HTTP/1.1\r\n", ""):
        assert proxy.decide(line, allow) is None, line


# ---------------------------------------------------------------- server kit over ssh

def test_split_target_finds_destination_and_jump() -> None:
    assert ssh.split_target(["deploy@web1.example.com"]) == ("deploy@web1.example.com", "")
    assert ssh.split_target(["-J", "admin@bastion.example.com", "admin@192.0.2.10"]) == \
        ("admin@192.0.2.10", "admin@bastion.example.com")
    assert ssh.split_target(["-p2222", "-o", "ConnectTimeout=5", "web1"]) == ("web1", "")


def _ssh_world(world: dict, monkeypatch: pytest.MonkeyPatch, *, proxycommand: str = "none",
               known: str = "web1.example.com ssh-ed25519 AAAAC3Nza\n", name: str = "sshwork") -> Path:
    keydir = world["home"] / ".ssh"
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / "id_ed25519").write_text("not a real key\n")
    (keydir / "known_hosts").write_text("placeholder\n")

    def fake_resolve(args: list[str]) -> dict[str, list[str]]:
        return {"hostname": ["web1.example.com"], "port": ["22"], "user": ["deploy"],
                "identityfile": ["~/.ssh/id_ed25519", "~/.ssh/id_rsa"], "userknownhostsfile": ["~/.ssh/known_hosts"],
                "proxycommand": [proxycommand]}

    monkeypatch.setattr(ssh, "resolve", fake_resolve)
    fake = world["fake"]
    orig = fake.__call__

    def keygen(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        if argv[:2] == ["ssh-keygen", "-F"]:
            return subprocess.CompletedProcess(argv, 0, f"# Host found: line 1\n{known}", "")
        return orig(argv, **kw)

    monkeypatch.setattr(subprocess, "run", keygen)
    work = world["tmp"] / name
    work.mkdir()
    return work


def test_ssh_files_pin_the_target_and_route_through_the_proxy(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _ssh_world(world, monkeypatch)
    mounts, endpoint = ssh.build_ssh_files("deploy@web1.example.com", "", work, "agentteams-vulnscan-tools-egress-1")
    conf = (work / "ssh_config").read_text()
    assert endpoint == "web1.example.com:22"
    for line in ("Host web1.example.com", "StrictHostKeyChecking yes", "BatchMode yes", "IdentitiesOnly yes",
                 "ForwardAgent no", "ProxyCommand nc -X connect -x agentteams-vulnscan-tools-egress-1:3128 %h %p",
                 f"UserKnownHostsFile {ssh.KNOWN_IN}", "IdentityFile /etc/ssh/vulnscan_id_1"):
        assert line in conf, line
    assert (work / "known_hosts").read_text() == "web1.example.com ssh-ed25519 AAAAC3Nza\n"
    keys = [m for m in mounts if m.dest.startswith("/etc/ssh/vulnscan_id_")]
    assert len(keys) == 1 and keys[0].secret_ok and not keys[0].writable   # only the key that exists
    assert {m.dest for m in mounts} >= {ssh.CONFIG_IN, ssh.KNOWN_IN}


def test_ssh_refuses_proxycommand_and_unknown_host_keys(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _ssh_world(world, monkeypatch, proxycommand="ssh -W %h:%p jump")
    with pytest.raises(sc.Unavailable, match="ProxyCommand"):
        ssh.build_ssh_files("web1", "", work, "p")
    work2 = _ssh_world(world, monkeypatch, known="", name="sshwork2")
    with pytest.raises(sc.Unavailable, match="no known_hosts line"):
        ssh.build_ssh_files("web1", "", work2, "p")


def test_server_op_uses_the_container_inside_a_scope(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    kit = world["tmp"] / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com | test\n")
    seen: list[tuple[Any, ...]] = []

    def fake_kit(script: Path, host: str, args: list[str], **kw: Any) -> subprocess.CompletedProcess:
        seen.append((script.name, host, args, kw["logs"]))
        return subprocess.CompletedProcess([], 0, "RESULT clean\n", "")

    monkeypatch.setattr(ssh, "run_kit", fake_kit)
    with _confined(world["tmp"]):
        res = executor.server_op("server-audit", "web1")
    assert seen == [("remote.sh", "web1", ["L1-audit.sh"], world["tmp"] / "state" / "server-logs")]
    assert res["host"] == "web1" and not [a for a, _ in world["fake"].calls if a[:1] == ["bash"]]


def test_daily_server_audit_is_unscanned_when_the_container_fails(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    kit = world["tmp"] / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com | test\n")

    def boom(*a: Any, **k: Any) -> subprocess.CompletedProcess:
        raise sc.Unavailable("tools container unavailable: test")

    monkeypatch.setattr(ssh, "run_kit", boom)
    with _confined(world["tmp"]):
        rep = daily._scan_server("web1", kit, world["tmp"])
    assert rep.feed_errors == ["web1: not scanned: tools container unavailable: test"]


def test_daily_staging_stages_nothing_when_the_container_is_unavailable(world: dict,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    repo = _repo(world["tmp"] / "repos" / "r")
    wt, md = daily.stage_worktree_fixes(repo, {**daily.DEFAULT_CONFIG, **ON}, "2026-10-23")
    assert wt == "" and "nothing staged: tools container unavailable" in md
    assert not (repo.parent / "r.worktrees").exists()


# ---------------------------------------------------------------- the tools image

def test_build_argv_carries_the_node_pins() -> None:
    argv = tools_image.build_argv(Path("/ctx"), "t", "python:3.12-slim@sha256:" + "0" * 64)
    joined = " ".join(argv)
    assert f"NODE_VERSION={tools_image.NODE_VERSION}" in joined
    assert f"NODE_SHA256_AMD64={tools_image.NODE_SHA256['amd64']}" in joined
    assert f"NODE_SHA256_ARM64={tools_image.NODE_SHA256['arm64']}" in joined and argv[-1] == "/ctx"
    assert all(len(v) == 64 for v in tools_image.NODE_SHA256.values())
    with pytest.raises(RuntimeError, match="checksums"):
        tools_image.build_image({}, python_image="p", node_version="0.0.1")


def test_flags_fingerprint_binds_image_and_flags() -> None:
    assert sc.flags_sha256("a") != sc.flags_sha256("b") and sc.flags_sha256("a") == sc.flags_sha256("a")


def test_image_context_ships_what_the_dockerfile_copies() -> None:
    names = {p.name for p in sc.data_dir().iterdir()}
    assert {"Dockerfile", "egress_proxy.py", "selftest.py", "vulnscan-with-token", "git-credential-vulnscan"} <= names
    text = (sc.data_dir() / "Dockerfile").read_text()
    assert "sha256sum -c" in text and "USER 65534:65534" in text and sc.PROXY_SCRIPT.rsplit("/", 1)[0] in text
    helper = (sc.data_dir() / "git-credential-vulnscan").read_text()
    assert 'proto" = https' in helper and "VULNSCAN_GIT_HOST" in helper


def test_selftest_records_a_pass_only_when_every_check_passes(world: dict) -> None:
    checks = {k: True for k in ("uid_is_operator_not_root", "caps_zero", "no_new_privs", "rootfs_read_only",
                                "home_is_empty_tmpfs", "no_credential_paths", "no_token_variables",
                                "rw_mount_writable", "ro_mount_read_only", "tools_present", "direct_egress_refused",
                                "dns_unavailable", "proxy_refuses_unlisted", "proxy_refuses_ip_literal",
                                "proxy_allows_listed")}
    world["fake"].op_out = json.dumps(checks) + "\n"
    res = tools_image.selftest({})
    assert res["passed"] and res["image_id"] == IMAGE_ID and sc.unavailable_reason(sc.image_tag()) == ""
    world["fake"].op_out = json.dumps({**checks, "rootfs_read_only": False}) + "\n"
    res = tools_image.selftest({})
    assert not res["passed"] and "tools-selftest" in sc.unavailable_reason(sc.image_tag())


# ---------------------------------------------------------------- @security W1 review fixes


def test_confinement_is_on_by_default_in_the_source() -> None:
    """conftest turns the default off for legacy host-git tests; the shipped default must stay on (fail closed)."""
    assert "ENABLED_BY_DEFAULT = True" in Path(sc.__file__).read_text(encoding="utf-8")


def test_a_parent_of_a_credential_dir_is_never_mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for d in sc.SECRET_DIRS:
        target = home / d
        target.mkdir(parents=True, exist_ok=True)
        for p in (target, *(q for q in target.parents if q.is_relative_to(home) and q != home)):
            assert sc.mount_problem(p), p
    (home / "code").mkdir()
    assert sc.mount_problem(home / "code") == ""


def test_an_existing_tools_network_that_is_not_internal_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def docker(argv: list[str], *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "false\n", "")

    monkeypatch.setattr(sc, "_docker", docker)
    with pytest.raises(sc.Unavailable, match="not --internal"):
        sc.ensure_network()
    assert not any("create" in c for c in calls)


def test_containers_never_pull_an_image() -> None:
    argv = sc.container_argv("img", ["true"], name="n", mounts=[], env={}, network=sc.NETWORK, stdin=False,
                             uid=1000, gid=1000)
    assert argv[argv.index("--pull") + 1] == "never"
    proxy = sc.proxy_argv("img", "p", ["registry.npmjs.org:443"])
    assert proxy[proxy.index("--pull") + 1] == "never"


def test_git_hardening_names_no_repo_askpass_and_no_ext_transport() -> None:
    h = executor._GIT_HARDENING
    pairs = {h[i + 1] for i in range(len(h) - 1) if h[i] == "-c"}
    assert "core.askPass=" in pairs and "protocol.ext.allow=never" in pairs


def test_every_push_url_of_origin_is_checked(tmp_path: Path) -> None:
    """`git push origin` pushes to every pushurl: a second ext:: URL must be refused, not just the first."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("remote", "add", "origin", "https://example.com/o/r.git")
    env = executor._clean_git_env(str(tmp_path / "gitconfig"))
    assert executor._unsafe_origin(repo, env) == ""
    git("config", "--add", "remote.origin.pushurl", "https://example.com/o/r.git")
    git("config", "--add", "remote.origin.pushurl", "ext::sh -c touch% /tmp/x")
    assert "refusing to contact it" in executor._unsafe_origin(repo, env)
