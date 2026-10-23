"""Server kit shell scripts (remote.sh, run-host.sh, L1-audit.sh) driven against fakes only.

Every test copies the packaged kit's ``*.sh`` scripts into a tmp dir, puts a fake ``ssh`` first on
PATH and uses a tmp ``hosts.conf`` with ``example.com`` names. No real host is ever contacted.
``/bin/bash`` (3.2 on macOS, the compatibility floor) runs the scripts when it exists.

Items (vulnscan-test-matrix Rev 4): G5, G6, G19, G20, G21, G27, G35, G36.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

KIT_SRC = Path(__file__).resolve().parents[1] / "agentteams" / "data" / "server_kit"
BASH = os.environ.get("AGENTTEAMS_TEST_BASH") or ("/bin/bash" if Path("/bin/bash").is_file() else
                                                   (shutil.which("bash") or "bash"))
L_SCRIPTS = ("L1-audit.sh", "L2-os-patch.sh", "L3-node-runtime.sh", "L4-python-app.sh", "L5-containers.sh")
IOC_END = "__AGENTTEAMS_IOC_DATA_END__"

pytestmark = pytest.mark.skipif(not (Path(BASH).is_file() or shutil.which(BASH)), reason="bash not available")

# The fake ssh records its argv (one per line) and stdin payload per call, then either answers like
# the remote side (FAKE_L1_RC / FAKE_L1_RESULT for the audit, FAKE_OTHER_RC otherwise) or, with
# FAKE_SSH_EXEC set to a PATH, really runs the remote command line locally on that minimal "host".
FAKE_SSH = r"""#!/bin/bash
d="$FAKE_SSH_DIR"
n=$(ls "$d" | grep -c '\.argv$')
for a in "$@"; do printf '%s\n' "$a"; done > "$d/$n.argv"
cat > "$d/$n.payload"
if [ -n "${FAKE_SSH_EXEC:-}" ]; then
  PATH="$FAKE_SSH_EXEC" exec /bin/bash -c "${@: -1}" < "$d/$n.payload"
fi
if grep -qF '# L1 — READ-ONLY' "$d/$n.payload"; then
  [ -n "${FAKE_L1_RESULT-clean}" ] && printf 'RESULT %s\n' "${FAKE_L1_RESULT-clean}"
  exit "${FAKE_L1_RC:-0}"
fi
exit "${FAKE_OTHER_RC:-0}"
"""


class Kit:
    """A tmp copy of the kit plus its config dir, fake bin dir and ssh call recorder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.kit = root / "kit"
        self.conf = root / "conf"
        self.bin = root / "bin"
        self.calls_dir = root / "calls"
        self.cwd = root / "cwd"
        for d in (self.kit, self.conf, self.bin, self.calls_dir, self.cwd):
            d.mkdir()
        for f in KIT_SRC.glob("*.sh"):
            shutil.copy2(f, self.kit / f.name)
        self.tool("ssh", FAKE_SSH)
        self.env = {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(root),
                    "AGENTTEAMS_SERVER_KIT_CONFIG": str(self.conf), "AGENTTEAMS_SERVER_KIT_LOGS": str(root / "logs"),
                    "AGENTTEAMS_VULNSCAN_NO_REPORT": "1", "FAKE_SSH_DIR": str(self.calls_dir), "LC_ALL": "C"}

    def tool(self, name: str, body: str, where: Path | None = None) -> Path:
        p = (where or self.bin) / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    def hosts(self, text: str | bytes) -> None:
        data = text.encode() if isinstance(text, str) else text
        (self.conf / "hosts.conf").write_bytes(data)

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([BASH, str(self.kit / script), *args], capture_output=True, text=True,
                              env={**self.env, **env}, cwd=self.cwd, timeout=120, check=False)

    def calls(self) -> list[dict]:
        """Recorded ssh calls in order: argv, raw payload bytes, which kit script, its remote args."""
        out = []
        n = len(list(self.calls_dir.glob("*.argv")))
        scripts = {name: (self.kit / name).read_bytes() for name in L_SCRIPTS}
        for i in range(n):
            argv = (self.calls_dir / f"{i}.argv").read_text().splitlines()
            payload = (self.calls_dir / f"{i}.payload").read_bytes()
            which = [name for name, body in scripts.items() if payload.endswith(body)]
            out.append({"argv": argv, "payload": payload, "script": which[0] if which else None,
                        "args": remote_args(argv[-1])})
        return out


def remote_args(remote: str) -> list[str]:
    """The script arguments inside remote.sh's `if root; then bash -s -- ARGS; else sudo -n bash -s -- ARGS; fi`."""
    head = "then bash -s --"
    seg = remote[remote.index(head) + len(head): remote.index("; else")]
    rest = remote[remote.index("; else sudo -n bash -s --") + len("; else sudo -n bash -s --"): remote.rindex("; fi")]
    assert seg == rest, remote          # root and sudo branches get the same arguments
    return shlex.split(seg)


SSH_OPTS = ["-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30", "-o", "BatchMode=yes"]


@pytest.fixture
def kit(tmp_path: Path) -> Kit:
    k = Kit(tmp_path)
    k.hosts("web1 | deploy@web1.example.com | test host\n")
    return k


def test_every_kit_script_parses_with_bash_n() -> None:
    for f in sorted(KIT_SRC.glob("*.sh")):
        proc = subprocess.run([BASH, "-n", str(f)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, (f.name, proc.stderr)


# ---------------------------------------------------------------- G5: run-host stops when L1 fails

@pytest.mark.parametrize("apply", [False, True])
def test_g5_run_host_stops_on_unknown_host(kit: Kit, apply: bool) -> None:
    proc = kit.run("run-host.sh", "web9", *(["--apply"] if apply else []))
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert proc.stderr.count("unknown host") == 1, proc.stderr      # remote.sh was invoked once: L1 only
    assert "DRY RUN complete" not in proc.stdout and "Post-change" not in proc.stdout
    assert kit.calls() == []


@pytest.mark.parametrize("apply", [False, True])
def test_g5_run_host_stops_without_hosts_conf(kit: Kit, apply: bool) -> None:
    (kit.conf / "hosts.conf").unlink()
    proc = kit.run("run-host.sh", "web1", *(["--apply"] if apply else []))
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert proc.stderr.count("copy hosts.conf.example") == 1 and kit.calls() == []   # remote.sh ran once: L1


@pytest.mark.parametrize("apply", [False, True])
def test_g5_run_host_stops_when_the_inventory_says_unscanned(kit: Kit, apply: bool) -> None:
    fake_py = kit.tool("fakepy", "#!/bin/sh\nexit 3\n", kit.root)
    env = {"AGENTTEAMS_VULNSCAN_NO_REPORT": "", "AGENTTEAMS_PYTHON": str(fake_py)}
    del kit.env["AGENTTEAMS_VULNSCAN_NO_REPORT"]
    proc = kit.run("run-host.sh", "web1", *(["--apply"] if apply else []), **env)
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert [c["script"] for c in kit.calls()] == ["L1-audit.sh"]


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("rc", ["0", "1", "126", "127"])
def test_g5_audit_without_a_result_line_is_incomplete(kit: Kit, apply: bool, rc: str) -> None:
    """sudo missing / refused, or the audit died: no RESULT line is never treated as clean or action-needed."""
    one = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_L1_RESULT="", FAKE_L1_RC=rc)
    assert one.returncode == 3 and "AUDIT INCOMPLETE" in one.stderr, (one.stdout, one.stderr)
    for f in kit.calls_dir.iterdir():
        f.unlink()
    proc = kit.run("run-host.sh", "web1", *(["--apply"] if apply else []), FAKE_L1_RESULT="", FAKE_L1_RC=rc)
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert [c["script"] for c in kit.calls()] == ["L1-audit.sh"]


def test_g5_incomplete_audit_with_indicator_lines_is_compromise(kit: Kit) -> None:
    kit.tool("ssh", "#!/bin/sh\ncat >/dev/null\nprintf 'IOC\\tindicator path present: /tmp/x\\n'\nexit 1\n")
    proc = kit.run("remote.sh", "web1", "L1-audit.sh")
    assert proc.returncode == 4, (proc.stdout, proc.stderr)
    assert kit.run("run-host.sh", "web1", "--apply").returncode == 4


@pytest.mark.parametrize("rc,want", [("0", 0), ("1", 0)])
def test_g5_run_host_continues_after_a_completed_audit(kit: Kit, rc: str, want: int) -> None:
    proc = kit.run("run-host.sh", "web1", FAKE_L1_RC=rc, FAKE_L1_RESULT="action-needed" if rc == "1" else "clean")
    assert proc.returncode == want, (proc.stdout, proc.stderr)
    assert [c["script"] for c in kit.calls()] == ["L1-audit.sh", "L2-os-patch.sh", "L3-node-runtime.sh",
                                                   "L5-containers.sh"]


def test_g5_compromise_and_unreachable_still_stop(kit: Kit) -> None:
    assert kit.run("run-host.sh", "web1", "--apply", FAKE_L1_RC="4", FAKE_L1_RESULT="COMPROMISE").returncode == 4
    assert len(kit.calls()) == 1
    for f in kit.calls_dir.iterdir():
        f.unlink()
    proc = kit.run("run-host.sh", "web1", "--apply", FAKE_L1_RC="255", FAKE_L1_RESULT="")
    assert proc.returncode == 1 and "cannot reach" in proc.stdout and len(kit.calls()) == 1


# ---------------------------------------------------------------- G6: flag propagation

APPS = (
    "# host | unit | dir | python | flags | health | pins\n"
    "web1 | myapp.service | ~deploy/myapp | ~deploy/myapp/.venv/bin/python | --as deploy | "
    "http://127.0.0.1:8000/health | PyJWT==2.10.1 requests==2.32.4\n"
    "web1 | other.service | ~svc/other | /usr/bin/python3 | --user-site --as svc | - |\n"         # no pins: skipped
    "web2 | elsewhere.service | ~x/app | /usr/bin/python3 | | - | flask==3.1.1\n"                  # other host
    "web1 | crlf.service | /srv/c | /usr/bin/python3 |  | - | idna==3.10\r\n"                     # CRLF line
)


def test_g6_apply_flags_reach_the_right_scripts(kit: Kit) -> None:
    (kit.conf / "apps.conf").write_text(APPS)
    proc = kit.run("run-host.sh", "web1", "--apply", "--all-updates", "--node-major", "24", "--restart-services",
                   "--reboot")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    got = [(c["script"], c["args"]) for c in kit.calls()]
    assert got == [
        ("L1-audit.sh", []),
        ("L2-os-patch.sh", ["--apply", "--restart-services", "--all"]),
        ("L3-node-runtime.sh", ["--apply", "--restart-services", "--major", "24"]),
        ("L5-containers.sh", ["--apply"]),
        ("L4-python-app.sh", ["--unit", "myapp.service", "--dir", "~deploy/myapp", "--python",
                              "~deploy/myapp/.venv/bin/python", "--health", "http://127.0.0.1:8000/health",
                              "--as", "deploy", "--pin", "PyJWT==2.10.1", "--pin", "requests==2.32.4", "--apply"]),
        ("L4-python-app.sh", ["--unit", "crlf.service", "--dir", "/srv/c", "--python", "/usr/bin/python3",
                              "--pin", "idna==3.10", "--apply"]),
        ("L2-os-patch.sh", ["--apply", "--reboot"]),
        ("L1-audit.sh", []),
    ], got
    assert "SKIP other.service" in proc.stdout
    for c in kit.calls():
        assert c["argv"][:-1] == [*SSH_OPTS, "deploy@web1.example.com"]


def test_g6_dry_run_passes_no_apply_and_never_reboots(kit: Kit) -> None:
    (kit.conf / "apps.conf").write_text(APPS)
    proc = kit.run("run-host.sh", "web1", "--reboot", "--restart-services", "--node-major", "22")
    assert proc.returncode == 0 and "DRY RUN complete" in proc.stdout, (proc.stdout, proc.stderr)
    got = [(c["script"], c["args"]) for c in kit.calls()]
    assert [s for s, _ in got] == ["L1-audit.sh", "L2-os-patch.sh", "L3-node-runtime.sh", "L5-containers.sh",
                                   "L4-python-app.sh", "L4-python-app.sh"]
    assert all("--apply" not in a and "--reboot" not in a for _, a in got), got
    assert got[1][1] == ["--restart-services"] and got[2][1] == ["--restart-services", "--major", "22"]
    assert got[4][1][-2:] == ["--pin", "requests==2.32.4"]


def test_g6_every_flag_is_accepted_by_its_target_script(kit: Kit) -> None:
    """The args run-host builds parse in the real L2/L3/L5 option loops (no `unknown option`)."""
    for script, args in (("L2-os-patch.sh", ["--apply", "--restart-services", "--all", "--reboot"]),
                         ("L3-node-runtime.sh", ["--apply", "--restart-services", "--major", "24"]),
                         ("L5-containers.sh", ["--apply"])):
        text = (kit.kit / script).read_text()
        loop = text[text.index("while [ $# -gt 0 ]") if "while [ $# -gt 0 ]" in text else text.index('for a in "$@"'):]
        loop = loop[: loop.index("done") + 4]
        probe = f"set -u\n{loop.replace('exit 2', 'echo REJECTED; exit 2')}\necho PARSED\n"
        proc = subprocess.run([BASH, "-c", probe, "x", *args], capture_output=True, text=True, check=False)
        assert proc.stdout.strip() == "PARSED", (script, proc.stdout, proc.stderr)


def test_g6_all_mode_forwards_flags_per_host(kit: Kit) -> None:
    kit.hosts("web1 | deploy@web1.example.com\nweb2 | deploy@web2.example.com\n")
    proc = kit.run("run-host.sh", "all", "--node-major", "24", "--all-updates")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    l3 = [(c["argv"][-2], c["args"]) for c in kit.calls() if c["script"] == "L3-node-runtime.sh"]
    assert l3 == [("deploy@web1.example.com", ["--major", "24"]), ("deploy@web2.example.com", ["--major", "24"])]


# ---------------------------------------------------------------- G19: host-name grammar, no split/glob

def test_g19_remote_all_never_globs_or_splits_host_names(kit: Kit) -> None:
    (kit.cwd / "planted-file").write_text("x")
    kit.hosts("web1 | deploy@web1.example.com\n* | deploy@star.example.com\n")
    proc = kit.run("remote.sh", "all", "L1-audit.sh")
    assert proc.returncode == 2 and "invalid host name" in proc.stderr and "'*'" in proc.stderr, proc.stderr
    assert "planted-file" not in proc.stdout + proc.stderr
    assert kit.calls() == []            # refused before any host is contacted


@pytest.mark.parametrize("bad", ["my host", "all", "-oProxyCommand=x", "a/b", "../x", "$(id)", "x" * 65, "web;1"])
def test_g19_invalid_names_are_refused_by_both_scripts(kit: Kit, bad: str) -> None:
    kit.hosts(f"web1 | deploy@web1.example.com\n{bad} | deploy@bad.example.com\n")
    cases = [("remote.sh", ["all", "L1-audit.sh"]), ("run-host.sh", ["all"])]
    if bad != "all":                   # `all` as the argument means every host; as a hosts.conf name it is refused
        cases += [("remote.sh", [bad, "L1-audit.sh"]), ("run-host.sh", [bad])]
    for script, args in cases:
        proc = kit.run(script, *args)
        assert proc.returncode == 2, (script, args, proc.stdout, proc.stderr)
        assert "invalid host name" in proc.stderr, (script, args, proc.stderr)
        assert kit.calls() == [], (script, args)


def test_g19_all_mode_runs_each_valid_host_exactly(kit: Kit) -> None:
    kit.hosts("web1 | deploy@web1.example.com\napi-2.prod | admin@api2.example.com\nX_3 | x3.example.com\n")
    proc = kit.run("remote.sh", "all", "L1-audit.sh")
    assert proc.returncode == 0, proc.stderr
    assert [c["argv"][-2] for c in kit.calls()] == ["deploy@web1.example.com", "admin@api2.example.com",
                                                     "x3.example.com"]
    logs = sorted(p.name for p in (kit.root / "logs").iterdir())
    assert [n.split("-", 1)[0] for n in logs if n.startswith("X_3")] == ["X_3"] and len(logs) == 3


def test_g19_all_mode_counts_unreachable_and_missing_conf(kit: Kit) -> None:
    kit.hosts("web1 | deploy@web1.example.com\nweb2 | deploy@web2.example.com\n")
    proc = kit.run("remote.sh", "all", "L1-audit.sh", FAKE_L1_RC="255", FAKE_L1_RESULT="")
    assert proc.returncode == 3, (proc.stdout, proc.stderr)       # unreachable is unscanned, never clean
    (kit.conf / "hosts.conf").unlink()
    assert kit.run("run-host.sh", "all").returncode == 2
    assert kit.run("remote.sh", "all", "L1-audit.sh").returncode == 2


def test_g19_run_host_all_propagates_the_worst_exit(kit: Kit) -> None:
    kit.hosts("web1 | deploy@web1.example.com\nweb2 | deploy@web2.example.com\n")
    proc = kit.run("run-host.sh", "all", FAKE_L1_RC="4", FAKE_L1_RESULT="COMPROMISE")
    assert proc.returncode == 4, (proc.stdout, proc.stderr)
    assert [c["script"] for c in kit.calls()] == ["L1-audit.sh", "L1-audit.sh"]


# ---------------------------------------------------------------- G20: SCRIPT confined to L1..L5

@pytest.mark.parametrize("script", ["../evil.sh", "kit/../evil.sh", "./L1-audit.sh", "/bin/sh", "run-host.sh",
                                    "remote.sh", "hosts.conf.example", "L6-nope.sh", "L1-audit.sh/", ""])
def test_g20_remote_script_argument_is_confined(kit: Kit, script: str) -> None:
    (kit.root / "evil.sh").write_text("touch PWNED\n")
    (kit.kit / "hosts.conf.example").write_text("x\n")
    proc = kit.run("remote.sh", "web1", script)
    assert proc.returncode == 2, (script, proc.stdout, proc.stderr)
    assert kit.calls() == [] and not (kit.cwd / "PWNED").exists()


@pytest.mark.parametrize("script", L_SCRIPTS)
def test_g20_each_l_script_is_allowed(kit: Kit, script: str) -> None:
    proc = kit.run("remote.sh", "web1", script)
    assert proc.returncode == 0, (script, proc.stderr)
    assert [c["script"] for c in kit.calls()] == [script]


# ---------------------------------------------------------------- G21: hosts.conf parsing stays inert

HOSTS_TRICKY = (
    b"# comment | not-a-host\r\n"
    b"web1 | deploy@web1.example.com | Ubuntu\r\n"                    # CRLF, three columns
    b"web2|deploy@web2.example.com\r\n"                                # CRLF, two columns, no spaces
    b"web3 | deploy@web3.example.com;touch PWNED3 | semicolon\n"
    b"web4 | $(touch PWNED4) deploy@web4.example.com | dollar-paren\n"
    b"web5 | `touch PWNED5` deploy@web5.example.com | backticks\n"
    b"\tweb6\t|\t-p 2222 deploy@web6.example.com\t| tabs\n"
    b"web7 | -J admin@bastion.example.com admin@192.0.2.10\n"           # no final newline
)
WANT = {
    "web1": ["deploy@web1.example.com"],
    "web2": ["deploy@web2.example.com"],
    "web3": ["deploy@web3.example.com;touch", "PWNED3"],
    "web4": ["$(touch", "PWNED4)", "deploy@web4.example.com"],
    "web5": ["`touch", "PWNED5`", "deploy@web5.example.com"],
    "web6": ["-p", "2222", "deploy@web6.example.com"],
    "web7": ["-J", "admin@bastion.example.com", "admin@192.0.2.10"],
}


def _inert(kit: Kit) -> None:
    for d in (kit.cwd, kit.conf, kit.kit, kit.root):
        assert not list(d.glob("PWNED*")), d


@pytest.mark.parametrize("host", sorted(WANT))
def test_g21_hosts_conf_targets_are_exact_and_inert(kit: Kit, host: str) -> None:
    kit.hosts(HOSTS_TRICKY)
    proc = kit.run("remote.sh", host, "L1-audit.sh")
    assert proc.returncode == 0, (host, proc.stderr)
    (call,) = kit.calls()
    assert call["argv"] == [*SSH_OPTS, *WANT[host], call["argv"][-1]], call["argv"]
    assert "\r" not in "".join(call["argv"])
    _inert(kit)


def test_g21_all_mode_and_run_host_see_the_same_targets(kit: Kit) -> None:
    kit.hosts(HOSTS_TRICKY)
    proc = kit.run("remote.sh", "all", "L1-audit.sh")
    assert proc.returncode == 0, proc.stderr
    assert [c["argv"][len(SSH_OPTS):-1] for c in kit.calls()] == [WANT[h] for h in sorted(WANT)]
    for f in kit.calls_dir.iterdir():
        f.unlink()
    proc = kit.run("run-host.sh", "all")
    assert proc.returncode == 0, proc.stderr
    l1 = [c["argv"][len(SSH_OPTS):-1] for c in kit.calls() if c["script"] == "L1-audit.sh"]
    assert l1 == [WANT[h] for h in sorted(WANT)]
    _inert(kit)


def test_g21_executor_and_kit_agree_on_host_names(kit: Kit, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import executor, paths
    kit.hosts(HOSTS_TRICKY)
    monkeypatch.setattr(paths, "server_kit_dir", lambda: kit.conf)
    assert executor._kit_hosts() == set(WANT)


# ---------------------------------------------------------------- G27: L1 on unusual hosts

HOST_TOOLS = ("hostname", "uname", "date", "readlink", "xargs", "sort", "grep", "awk", "tr", "cut", "head",
              "basename", "sed", "cat", "wc", "tail")
FAKE_FIND = r"""#!/bin/bash
case " $* " in
  *site-packages*) f="${FAKE_FIND_SP:-}";;
  *installed.json*) f="${FAKE_FIND_LOCK:-}";;
  *'*/bin/node'*) f="${FAKE_FIND_NODE:-}";;
  *) f="${FAKE_FIND_IOC:-}";;
esac
[ -n "$f" ] && [ -f "$f" ] && cat "$f"
exit 0
"""


def _host(kit: Kit, *, sudo: bool = True, uid: int = 1000, extra: dict[str, str] | None = None) -> Path:
    """A minimal Alpine/busybox-like host: no apt/dnf/yum, no python3, node, docker, systemctl, ps, ss."""
    hb = kit.root / "hostbin"
    hb.mkdir(exist_ok=True)
    for t in HOST_TOOLS:
        src = shutil.which(t, path="/usr/bin:/bin")
        assert src, t
        (hb / t).unlink(missing_ok=True)
        (hb / t).symlink_to(src)
    (hb / "bash").unlink(missing_ok=True)
    (hb / "bash").symlink_to(BASH if os.path.isabs(BASH) else shutil.which(BASH))
    kit.tool("id", f"#!/bin/sh\necho {uid}\n", hb)
    kit.tool("ls", "#!/bin/sh\nexit 2\n", hb)               # no php/python3 binaries in the usual places
    kit.tool("find", FAKE_FIND, hb)
    if sudo:
        kit.tool("sudo", '#!/bin/bash\n[ "$1" = -n ] && shift\nexec "$@"\n', hb)
    for name, body in (extra or {}).items():
        (hb / name).unlink(missing_ok=True)      # never write through a symlink to a system tool
        kit.tool(name, body, hb)
    return hb


def test_g27_no_sudo_is_incomplete_never_clean(kit: Kit) -> None:
    hb = _host(kit, sudo=False)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb))
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert "RESULT" not in proc.stdout.replace("no RESULT", "") and "AUDIT INCOMPLETE" in proc.stderr
    for f in kit.calls_dir.iterdir():
        f.unlink()
    rh = kit.run("run-host.sh", "web1", "--apply", FAKE_SSH_EXEC=str(hb))
    assert rh.returncode == 3 and len(kit.calls()) == 1


def test_g27_root_login_without_sudo_runs(kit: Kit) -> None:
    hb = _host(kit, sudo=False, uid=0)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb))
    assert "\nRESULT " in proc.stdout, (proc.stdout, proc.stderr)


def test_g27_minimal_host_without_a_package_manager_is_not_clean(kit: Kit) -> None:
    hb = _host(kit)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb))
    assert "RESULT action-needed" in proc.stdout, (proc.stdout, proc.stderr)
    assert proc.returncode == 1
    assert "HOSTCHECK\tMODERATE\tunknown package manager" in proc.stdout
    assert "RESULT clean" not in proc.stdout


def test_g27_lockfiles_without_python3_are_reported(kit: Kit) -> None:
    hb = _host(kit)
    lock = kit.root / "find-lock.txt"
    lock.write_text("/srv/app/node_modules/.package-lock.json\n")
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb), FAKE_FIND_LOCK=str(lock))
    assert "HOSTCHECK\tMODERATE\tpython3 missing — 1 npm/composer manifest(s) were not inventoried" in proc.stdout, \
        proc.stdout
    assert proc.returncode == 1


def test_g27_no_lockfiles_and_no_python3_is_not_flagged(kit: Kit) -> None:
    hb = _host(kit)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb))
    assert "python3 missing" not in proc.stdout


def test_g27_ioc_check_on_a_minimal_host(kit: Kit) -> None:
    hb = _host(kit)
    drop = kit.root / "host-fs"
    drop.mkdir()
    (drop / "dropped-payload.bin").write_text("x")
    (drop / "evil.pth").write_text("import os")
    (drop / "conf.py").write_text("TOKEN = 'exfil.example.com'\n")
    (drop / "innocent.py").write_text("print(1)\n")
    found = kit.root / "find-ioc.txt"
    found.write_text(f"{drop / 'evil.pth'}\n{drop / 'conf.py'}\n{drop / 'innocent.py'}\n")
    (kit.conf / "ioc-test.txt").write_text(
        f"# test rules\r\nfile {drop}/dropped-*\r\nname evil.pth\r\ngrep conf.py exfil\\.example\\.com\r\n")
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb), FAKE_FIND_IOC=str(found))
    assert proc.returncode == 4, (proc.stdout, proc.stderr)
    assert "(1 path, 1 name, 0 env, 1 content rules)" in proc.stdout
    assert f"IOC\tindicator path present: {drop}/dropped-payload.bin" in proc.stdout
    assert f"IOC\tindicator file: {drop}/evil.pth" in proc.stdout
    assert f"IOC\tindicator content in {drop}/conf.py" in proc.stdout
    assert "innocent" not in proc.stdout and "RESULT COMPROMISE" in proc.stdout


def test_g27_ioc_check_clean_host_matches_nothing(kit: Kit) -> None:
    hb = _host(kit)
    (kit.conf / "ioc-test.txt").write_text(f"file {kit.root}/nothing-here-*\nname evil.pth\n")
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb))
    assert "no compromise indicators matched" in proc.stdout and "IOC\t" not in proc.stdout
    assert proc.returncode == 1          # still action-needed: the package manager is unknown


def test_g27_l1_strips_cr_from_indicator_data_itself(kit: Kit) -> None:
    """Data that reaches L1 another way (by hand: AGENTTEAMS_IOC_DATA=... bash L1) still parses with CRLF."""
    hb = _host(kit)
    marker = kit.root / "marker-file"
    marker.write_text("x")
    proc = subprocess.run([BASH, str(kit.kit / "L1-audit.sh")], capture_output=True, text=True, timeout=120,
                          env={"PATH": str(hb), "AGENTTEAMS_IOC_DATA": f"file {marker}\r\nname x.pth\r\n"},
                          check=False)
    assert f"IOC\tindicator path present: {marker}\n" in proc.stdout, (proc.stdout, proc.stderr)
    assert proc.returncode == 4


# ---------------------------------------------------------------- G35: --node-major needs a value

@pytest.mark.parametrize("args", [["--node-major"], ["--apply", "--node-major"], ["--node-major", "--apply"],
                                  ["--node-major", ""], ["--node-major", "24x"], ["--node-major", "-1"]])
def test_g35_node_major_without_a_number_is_a_usage_error(kit: Kit, args: list[str]) -> None:
    proc = kit.run("run-host.sh", "web1", *args)
    assert proc.returncode == 2, (args, proc.stdout, proc.stderr)
    assert "--node-major" in proc.stderr and "run-host.sh <host>" in proc.stderr + proc.stdout
    assert "unbound variable" not in proc.stderr and kit.calls() == []


# ---------------------------------------------------------------- G36: IOC file encodings

def _expected_payload(kit: Kit, data: bytes) -> bytes:
    return (f"AGENTTEAMS_IOC_DATA=$(cat <<'{IOC_END}'\n").encode() + data + f"{IOC_END}\n)\n".encode() \
        + (kit.kit / "L1-audit.sh").read_bytes()


def test_g36_crlf_and_empty_indicator_files_embed_byte_for_byte(kit: Kit) -> None:
    (kit.conf / "ioc-a-crlf.txt").write_bytes(b"# crlf rules\r\nname evil.pth\r\nfile /tmp/drop-*\r\n")
    (kit.conf / "ioc-b-empty.txt").write_bytes(b"")
    (kit.conf / "ioc-c-lone-cr.txt").write_bytes(b"env LITELLM_EXFIL=1\r")            # CR, no final LF
    (kit.conf / "ioc-d-plain.txt").write_bytes(b"grep conf.py exfil\\.example\\.com")  # no final newline
    proc = kit.run("remote.sh", "web1", "L1-audit.sh")
    assert proc.returncode == 0, proc.stderr
    (call,) = kit.calls()
    want = (b"# crlf rules\nname evil.pth\nfile /tmp/drop-*\n"
            b"env LITELLM_EXFIL=1\n"
            b"grep conf.py exfil\\.example\\.com\n")
    assert call["payload"] == _expected_payload(kit, want)


def test_g36_crlf_indicators_are_live_on_the_host(kit: Kit) -> None:
    hb = _host(kit)
    marker = kit.root / "crlf-marker"
    marker.write_text("x")
    (kit.conf / "ioc-crlf.txt").write_bytes(f"file {marker}\r\n".encode())
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb))
    assert f"IOC\tindicator path present: {marker}\n" in proc.stdout, (proc.stdout, proc.stderr)
    assert proc.returncode == 4


@pytest.mark.parametrize("content", [b"name a\x00b\n", b"\x00\x01\x02\xff", b"name ok\n\x00"])
def test_g36_binary_indicator_file_is_refused_before_ssh(kit: Kit, content: bytes) -> None:
    (kit.conf / "ioc-bin.txt").write_bytes(content)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh")
    assert proc.returncode == 2 and "binary" in proc.stderr, (proc.stdout, proc.stderr)
    assert kit.calls() == []


def test_g36_crlf_terminator_is_still_refused(kit: Kit) -> None:
    (kit.conf / "ioc-evil.txt").write_bytes(f"name x\r\n{IOC_END}\r\nrm -rf /\r\n".encode())
    proc = kit.run("remote.sh", "web1", "L1-audit.sh")
    assert proc.returncode == 2 and "terminator" in proc.stderr and kit.calls() == []


def test_g36_non_audit_scripts_carry_no_indicator_data(kit: Kit) -> None:
    (kit.conf / "ioc-x.txt").write_bytes(b"name x\n\x00")     # would be refused for L1, irrelevant for L2
    proc = kit.run("remote.sh", "web1", "L2-os-patch.sh")
    assert proc.returncode == 0, proc.stderr
    (call,) = kit.calls()
    assert call["payload"] == (kit.kit / "L2-os-patch.sh").read_bytes()


# ---------------------------------------------------------------- round 2: step results and option values

@pytest.mark.parametrize("other,want", [("0", 0), ("1", 1), ("3", 3), ("126", 3), ("4", 4)])
def test_run_host_exit_is_the_worst_step(kit: Kit, other: str, want: int) -> None:
    """A failed L2/L3/L5 step is no longer hidden behind exit 0; every step still runs."""
    proc = kit.run("run-host.sh", "web1", FAKE_OTHER_RC=other)
    assert proc.returncode == want, (proc.stdout, proc.stderr)
    assert [c["script"] for c in kit.calls()] == ["L1-audit.sh", "L2-os-patch.sh", "L3-node-runtime.sh",
                                                   "L5-containers.sh"]
    assert ("STEP FAILED" in proc.stderr) is (want == 3)


def test_run_host_counts_l4_failures(kit: Kit) -> None:
    """apps.conf entries run in the main shell, so an L4 failure reaches the exit status."""
    (kit.conf / "apps.conf").write_text("web1 | app.service | /srv/app | python3 | | - | idna==3.10\r\n")
    proc = kit.run("run-host.sh", "web1", FAKE_OTHER_RC="3")
    assert proc.returncode == 3
    l4 = [c for c in kit.calls() if c["script"] == "L4-python-app.sh"]
    assert len(l4) == 1 and l4[0]["args"][-2:] == ["--pin", "idna==3.10"]


@pytest.mark.parametrize("script,args", [
    ("L3-node-runtime.sh", ["--major"]), ("L3-node-runtime.sh", ["--major", "--apply"]),
    ("L3-node-runtime.sh", ["--cooldown-days", ""]), ("L4-python-app.sh", ["--unit"]),
    ("L4-python-app.sh", ["--pin", "--apply"]), ("L5-containers.sh", ["--cooldown-days"]),
])
def test_l_script_option_without_value_is_a_usage_error(kit: Kit, script: str, args: list[str]) -> None:
    """A missing option value is exit 2 with a message, never an unbound-variable crash (exit 1)."""
    proc = kit.run(script, *args)
    assert proc.returncode == 2 and "needs a value" in proc.stderr, (proc.stdout, proc.stderr)


@pytest.mark.parametrize("flags", ["--as app --apply", "--as app --restart-services", "--as app --reboot",
                                   "--as\tapp\t--apply", "--apply", "--user-site\t--reboot"])
def test_r8_2_apps_conf_flags_cannot_turn_a_dry_run_into_a_change(kit: Kit, flags: str) -> None:
    """The flags column tunes L4; it may not carry apply/restart/reboot, however it is separated (R10: tabs)."""
    (kit.conf / "apps.conf").write_text(f"web1 | app.service | /srv/app | python3 | {flags} | - | idna==3.10\n")
    proc = kit.run("run-host.sh", "web1")
    assert proc.returncode == 3 and "may not contain --apply" in proc.stderr, (proc.stdout, proc.stderr)
    assert "L4-python-app.sh" not in [c["script"] for c in kit.calls()]
    assert all("--apply" not in c["args"] for c in kit.calls())


FAKE_PY = r"""#!/bin/bash
case "$*" in
  *"sys.prefix==sys.base_prefix"*) exit 0;;                 # a system interpreter (not a venv)
  *"sysconfig.get_path"*) echo /nonexistent-stdlib;;
  "-V") echo "Python 3.12.0";;
  "-m pip show"*) printf 'Name: idna\nVersion: 3.6\n';;
  "-m pip"*) echo "Would install idna-3.10";;
esac
exit 0
"""


def test_l4_system_pip_path_runs_on_old_bash(kit: Kit) -> None:
    """--system-pip empties the runuser prefix array: no 'unbound variable' under set -u on bash < 4.4."""
    kit.tool("systemctl", "#!/bin/sh\nexit 0\n")
    kit.tool("getent", "#!/bin/sh\necho 'svc:x:1000:1000::/srv/app-home:/bin/sh'\n")
    py = kit.tool("python3", FAKE_PY)
    app = kit.root / "app"
    app.mkdir()
    proc = kit.run("L4-python-app.sh", "--unit", "app.service", "--dir", str(app), "--python", str(py),
                   "--as", "svc", "--system-pip", "--pin", "idna==3.10")
    assert "unbound variable" not in proc.stderr, proc.stderr
    assert "Current versions of the targeted packages" in proc.stdout and "idna 3.6" in proc.stdout, proc.stdout



def test_apps_conf_ordinary_flags_still_reach_l4_word_by_word(kit: Kit) -> None:
    """Tab- or space-separated tuning flags are passed to L4 as separate words, unchanged."""
    (kit.conf / "apps.conf").write_text("web1 | app.service | /srv/app | python3 | --as\tapp --user-site | - | idna==3.10\n")
    assert kit.run("run-host.sh", "web1").returncode == 0
    l4 = [c for c in kit.calls() if c["script"] == "L4-python-app.sh"]
    assert len(l4) == 1 and l4[0]["args"][6:9] == ["--as", "app", "--user-site"], l4[0]["args"]


# ---------------------------------------------------------------- secret-bearing surfaces stay unread by default

FAKE_SYSTEMCTL = r"""#!/bin/bash
case "$1" in
  list-units) echo "app-worker.service loaded active running App worker";;
  show) echo "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 worker.py --db-password=hunter2 TOKEN=s3cr3t ; ignore_errors=no }";;
esac
"""
# Records every grep that names a process environment, then runs the real grep.
LOGGING_GREP = r"""#!/bin/bash
case "$*" in *environ*) echo "$*" >> "$GREP_LOG";; esac
exec REAL_GREP "$@"
"""


def _secret_host(kit: Kit) -> tuple[Path, Path]:
    log = kit.root / "grep-environ.log"
    grep_body = LOGGING_GREP.replace("REAL_GREP", shutil.which("grep", path="/usr/bin:/bin"))
    hb = _host(kit, extra={"systemctl": FAKE_SYSTEMCTL, "grep": grep_body})
    (kit.conf / "ioc-test.txt").write_text("env LITELLM_EXFIL=1\nname evil.pth\n")
    return hb, log


def test_l1_shows_a_service_by_its_executable_never_its_arguments(kit: Kit) -> None:
    hb, log = _secret_host(kit)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb), GREP_LOG=str(log))
    assert "app-worker.service → /usr/bin/python3\n" in proc.stdout, (proc.stdout, proc.stderr)
    for secret in ("hunter2", "s3cr3t", "worker.py", "--db-password"):
        assert secret not in proc.stdout + proc.stderr


def test_l1_reads_no_process_environment_by_default(kit: Kit) -> None:
    hb, log = _secret_host(kit)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", FAKE_SSH_EXEC=str(hb), GREP_LOG=str(log))
    assert "\nRESULT " in proc.stdout, (proc.stdout, proc.stderr)
    assert not log.exists(), log.read_text()
    assert "1 env indicator rule(s) not checked" in proc.stdout and "--deep" in proc.stdout


def test_l1_deep_checks_env_rules(kit: Kit) -> None:
    hb, log = _secret_host(kit)
    proc = kit.run("remote.sh", "web1", "L1-audit.sh", "--deep", FAKE_SSH_EXEC=str(hb), GREP_LOG=str(log))
    assert "\nRESULT " in proc.stdout, (proc.stdout, proc.stderr)
    assert kit.calls()[0]["args"] == ["--deep"]
    assert "LITELLM_EXFIL=1" in log.read_text() and "not checked" not in proc.stdout


@pytest.mark.parametrize("bad", ["--apply", "deep", "--deep=1"])
def test_l1_refuses_unknown_options(kit: Kit, bad: str) -> None:
    proc = subprocess.run([BASH, str(kit.kit / "L1-audit.sh"), bad], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin"}, check=False)
    assert proc.returncode == 2 and "L1 takes only --deep" in proc.stderr
