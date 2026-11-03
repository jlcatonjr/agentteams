"""server-apply levels in the server kit (run-host.sh --level, L2/L4/L6/L7 level mode), against fakes only.

A fake ``ssh`` answers each kit script by a marker in its payload; ``apt-get``, ``systemctl``, ``pip`` and friends are
fakes on PATH. No real host, package manager or service is touched. L7 runs locally on a tmp tree with its
quarantine root moved into tmp.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import grant_v2, server_apply

KIT_SRC = Path(__file__).resolve().parents[1] / "agentteams" / "data" / "server_kit"
BASH = os.environ.get("AGENTTEAMS_TEST_BASH") or ("/bin/bash" if Path("/bin/bash").is_file() else
                                                   (shutil.which("bash") or "bash"))
pytestmark = pytest.mark.skipif(not (Path(BASH).is_file() or shutil.which(BASH)), reason="bash not available")
MARKERS = {"L1-audit.sh": "# L1 — READ-ONLY", "L2-os-patch.sh": "# L2 — OS security", "L3-node-runtime.sh": "# L3",
           "L4-python-app.sh": "# L4 — Upgrade", "L5-containers.sh": "# L5", "L6-py-venv.sh": "# L6 — server-apply",
           "L7-remove-path.sh": "# L7 — server-apply"}
PLAN = "PLAN\tos\tapt\tlibssl3\t3.0.2-1\t3.0.2-2\nPLAN\tos\tapt\topenssl\t3.0.2-1\t3.0.2-2\n"

# The fake ssh: records argv + payload; identifies the script; prints $FAKE_PLAN for a plan call of a level script
# and exits $FAKE_APPLY_RC for an --apply call; the L1 audit prints RESULT clean.
FAKE_SSH = r"""#!/bin/bash
d="$FAKE_SSH_DIR"
n=$(ls "$d" | grep -c '\.argv$')
for a in "$@"; do printf '%s\n' "$a"; done > "$d/$n.argv"
cat > "$d/$n.payload"
if grep -qF '# L1 — READ-ONLY' "$d/$n.payload"; then echo 'RESULT clean'; exit "${FAKE_L1_RC:-0}"; fi
case "${@: -1}" in *--apply*) echo applied; exit "${FAKE_APPLY_RC:-0}";; esac
[ -n "${FAKE_PLAN_FILE:-}" ] && cat "$FAKE_PLAN_FILE"
exit "${FAKE_PLAN_RC:-0}"
"""


class Kit:
    def __init__(self, root: Path) -> None:
        self.root, self.kit, self.conf, self.bin, self.calls_dir = root, root / "kit", root / "conf", root / "bin", root / "calls"
        for d in (self.kit, self.conf, self.bin, self.calls_dir):
            d.mkdir()
        for f in KIT_SRC.glob("*.sh"):
            shutil.copy2(f, self.kit / f.name)
        tool(self.bin, "ssh", FAKE_SSH)
        (self.conf / "hosts.conf").write_text("web1 | deploy@web1.example.com\n")
        self.plan_file = root / "plan.txt"
        self.plan_file.write_text(PLAN)
        self.env = {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(root), "LC_ALL": "C",
                    "AGENTTEAMS_SERVER_KIT_CONFIG": str(self.conf), "AGENTTEAMS_SERVER_KIT_LOGS": str(root / "logs"),
                    "AGENTTEAMS_VULNSCAN_NO_REPORT": "1", "FAKE_SSH_DIR": str(self.calls_dir),
                    "FAKE_PLAN_FILE": str(self.plan_file), "TMPDIR": str(root)}

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([BASH, str(self.kit / "run-host.sh"), "web1", *args], capture_output=True, text=True,
                              env={**self.env, **env}, timeout=120, check=False)

    def calls(self) -> list[tuple[str | None, list[str]]]:
        out = []
        for i in range(len(list(self.calls_dir.glob("*.argv")))):
            argv = (self.calls_dir / f"{i}.argv").read_text().splitlines()
            payload = (self.calls_dir / f"{i}.payload").read_text()
            which = next((s for s, m in MARKERS.items() if m in payload), None)
            remote = argv[-1]
            head = "then bash -s --"
            out.append((which, shlex.split(remote[remote.index(head) + len(head): remote.index("; else")])))
        return out


def tool(where: Path, name: str, body: str) -> Path:
    p = where / name
    p.write_text(body)
    p.chmod(0o755)
    return p


@pytest.fixture
def kit(tmp_path: Path) -> Kit:
    return Kit(tmp_path)


# ---------------------------------------------------------------- run-host.sh: exactly one level, nothing that widens it

@pytest.mark.parametrize("args,match", [
    (["--level", "l2-security", "--level", "l4-pins"], "exactly one --level"),
    (["--level", "l9"], "unknown level"),
    (["--level", "l2-security", "--reboot"], "never takes"),
    (["--level", "l2-security", "--all-updates"], "never takes"),
    (["--level", "l2-security", "--node-major", "24"], "never takes"),
    (["--level", "l2-security", "--allow-fresh"], "never takes"),
    (["--level", "l2-security", "--restart-services"], "never takes"),
    (["--level", "l2-security", "--apply"], "needs --expect-digest"),
    (["--level", "l2-security", "--expect-digest", "a" * 64], "is for --apply"),
    (["--expect-digest", "a" * 64, "--apply"], "need --level"),
    (["--restart-unit", "nginx.service"], "need --level"),
    (["--level", "l4-pins", "--restart-unit", "nginx.service"], "for l2-security"),
    (["--level", "l2-security", "--major-allowed", "a.service"], "for l4-pins"),
    (["--level", "remove-path"], "exactly one --remove-path"),
    (["--level", "l2-security", "--remove-path", "/opt/x"], "for the remove-path level"),
    (["--level", "l2-security", "--restart-unit", "nginx"], "bad unit"),
])
def test_run_host_level_refusals(kit: Kit, args: list[str], match: str) -> None:
    proc = kit.run(*args)
    assert proc.returncode == 2 and match in proc.stderr, (proc.stdout, proc.stderr)
    assert kit.calls() == []


def test_run_host_level_never_runs_on_all(kit: Kit) -> None:
    proc = subprocess.run([BASH, str(kit.kit / "run-host.sh"), "all", "--level", "l2-security"], capture_output=True,
                          text=True, env=kit.env, timeout=60, check=False)
    assert proc.returncode == 2 and "never 'all'" in proc.stderr and kit.calls() == []


def test_level_plan_prints_the_digest_and_runs_only_that_level(kit: Kit) -> None:
    proc = kit.run("--level", "l2-security", "--restart-unit", "nginx.service")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    digest = server_apply.plan_sha256(PLAN)
    assert f"PLANDIGEST\tl2-security\t{digest}" in proc.stdout
    assert server_apply.plan_sha256(proc.stdout) == digest           # the executor recomputes the same digest
    assert kit.calls() == [("L1-audit.sh", []), ("L2-os-patch.sh", ["--level", "--restart-unit", "nginx.service"])]


def test_level_apply_refuses_a_changed_plan_before_any_change(kit: Kit) -> None:
    proc = kit.run("--level", "l2-security", "--apply", "--expect-digest", "b" * 64)
    assert proc.returncode == 5 and "DIGEST MISMATCH" in proc.stderr, (proc.stdout, proc.stderr)
    assert all("--apply" not in a for _, a in kit.calls())


def test_level_apply_passes_the_step_digest_and_reaudits(kit: Kit) -> None:
    digest = server_apply.plan_sha256(PLAN)
    proc = kit.run("--level", "l2-security", "--apply", "--expect-digest", digest)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    got = kit.calls()
    assert [s for s, _ in got] == ["L1-audit.sh", "L2-os-patch.sh", "L2-os-patch.sh", "L1-audit.sh"]
    assert got[2][1] == ["--level", "--apply", "--expect-digest", digest]
    assert not any(s in ("L3-node-runtime.sh", "L5-containers.sh") for s, _ in got)


@pytest.mark.parametrize("rc,want", [("6", 6), ("7", 7), ("4", 4), ("9", 3), ("1", 0)])
def test_level_apply_exit_codes(kit: Kit, rc: str, want: int) -> None:
    proc = kit.run("--level", "l2-security", "--apply", "--expect-digest", server_apply.plan_sha256(PLAN),
                   FAKE_APPLY_RC=rc)
    assert proc.returncode == (1 if rc == "1" else want), (proc.stdout, proc.stderr)
    assert [s for s, _ in kit.calls()][-1] == "L1-audit.sh"          # the post-change audit always runs


def test_plan_step_failure_stops_before_any_change(kit: Kit) -> None:
    proc = kit.run("--level", "l2-security", "--apply", "--expect-digest", "a" * 64, FAKE_PLAN_RC="3")
    assert proc.returncode == 3 and all("--apply" not in a for _, a in kit.calls())


APPS = ("web1 | api.service | /srv/api | /srv/api/.venv/bin/python | --as svc | - | idna==3.10\n"
        "web1 | venv.service | /srv/v | /srv/v/.venv/bin/python | --venv-base /usr/bin/python3.11 --distro-pkg python3.11"
        " | http://127.0.0.1:9/h | aiohttp==3.14.4\n"
        "web1 | nopins.service | /srv/n | /usr/bin/python3 | | - |\n"
        "web2 | other.service | /srv/o | /usr/bin/python3 | | - | a==1\n")


def test_l4_pins_and_py_venv_pick_their_entries_and_major_comes_only_from_the_flag(kit: Kit) -> None:
    (kit.conf / "apps.conf").write_text(APPS)
    proc = kit.run("--level", "l4-pins", "--major-allowed", "api.service")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert kit.calls()[1:] == [("L4-python-app.sh", ["--level", "--unit", "api.service", "--dir", "/srv/api",
                                "--python", "/srv/api/.venv/bin/python", "--as", "svc", "--pin", "idna==3.10",
                                "--major-allowed"])]
    for f in kit.calls_dir.iterdir():
        f.unlink()
    proc = kit.run("--level", "py-venv")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert kit.calls()[1:] == [("L6-py-venv.sh", ["--level", "--unit", "venv.service", "--dir", "/srv/v", "--python",
                                "/srv/v/.venv/bin/python", "--health", "http://127.0.0.1:9/h", "--venv-base",
                                "/usr/bin/python3.11", "--distro-pkg", "python3.11", "--pin", "aiohttp==3.14.4"])]


@pytest.mark.parametrize("flag", ["--major-allowed", "--apply", "--git-pull", "--allow-sdist", "--expect-digest"])
def test_apps_conf_flags_cannot_widen_a_level(kit: Kit, flag: str) -> None:
    (kit.conf / "apps.conf").write_text(f"web1 | api.service | /srv/api | /srv/api/.venv/bin/python | {flag} | - | a==1\n")
    proc = kit.run("--level", "l4-pins")
    assert proc.returncode == 3 and "may not contain" in proc.stderr, (proc.stdout, proc.stderr)
    assert [s for s, _ in kit.calls()] == ["L1-audit.sh"]


def test_remove_path_level_calls_l7_with_the_exact_path(kit: Kit) -> None:
    proc = kit.run("--level", "remove-path", "--remove-path", "/root/anaconda3.disabled-20261101")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert kit.calls()[1] == ("L7-remove-path.sh", ["--level", "--path", "/root/anaconda3.disabled-20261101"])


def test_compromised_pre_audit_stops_a_level(kit: Kit) -> None:
    proc = kit.run("--level", "l2-security", "--apply", "--expect-digest", "a" * 64, FAKE_L1_RC="4")
    assert proc.returncode == 4 and len(kit.calls()) == 1


# ---------------------------------------------------------------- one digest rule, one trust-root rule

def _block(text: str, name: str) -> str:
    m = re.search(rf"# BEGIN {name}.*?\n(.*?)# END {name}", text, re.S)
    assert m, name
    return m[1]


def test_shell_copies_of_the_shared_functions_are_identical() -> None:
    texts = {n: (KIT_SRC / n).read_text() for n in ("L2-os-patch.sh", "L4-python-app.sh", "L6-py-venv.sh",
                                                     "L7-remove-path.sh")}
    digests = {_block(t, "plan_digest") for t in texts.values()}
    assert len(digests) == 1
    runhost = (KIT_SRC / "run-host.sh").read_text()
    inline = re.search(r"\n  plan_digest\(\)\{\n(.*?\n)  \}\n", runhost, re.S)
    assert inline and "\n".join(ln[2:] for ln in inline[1].splitlines()) + "\n" in next(iter(digests))
    assert len({_block(texts[n], "health helpers") for n in ("L2-os-patch.sh", "L4-python-app.sh", "L6-py-venv.sh")}) == 1


def test_shell_plan_digest_equals_the_python_one() -> None:
    fn = _block((KIT_SRC / "L7-remove-path.sh").read_text(), "plan_digest")
    sample = "noise\nPLAN\tb\t  x \r\nPLAN\ta\ty\nPLAN\ta\ty\nPLAN\tz\tü\t\t\nPRESTATE\tq\n"
    for text in (sample, "", "no plan\n"):
        proc = subprocess.run([BASH, "-c", f"TAB=$(printf '\\t')\n{fn}\nplan_digest"], input=text, capture_output=True,
                              text=True, check=True)
        assert proc.stdout.strip() == server_apply.plan_sha256(text), text


def test_l7_trust_roots_match_grant_v2() -> None:
    text = (KIT_SRC / "L7-remove-path.sh").read_text()
    roots = re.search(r'^TRUST_ROOTS="([^"]*)"', text, re.M)
    comps = re.search(r'^TRUST_COMPONENTS="([^"]*)"', text, re.M)
    assert roots and tuple(roots[1].split()) == grant_v2.TRUST_ROOTS
    assert comps and tuple(comps[1].split()) == grant_v2.TRUST_COMPONENTS


@pytest.mark.parametrize("path", ["/root", "/root/anaconda3.disabled-20261101", "/".join(("", "home", "u")),
                                  "/".join(("", "home", "u", ".local")), "/".join(("", "home", "u", ".ssh", "x")),
                                  "/etc/x/y", "/usr/local", "/usr/local/lib/python3.9", "/usr/lib/x", "/var/backups",
                                  "/var/backups/agentteams/q", "/opt/a//b", "/opt/a/", "/opt/a b", "/opt/./a"])
def test_l7_trust_rule_matches_python(path: str) -> None:
    text = (KIT_SRC / "L7-remove-path.sh").read_text()
    lib = _block(text, "trust roots") + text[text.index("trust_problem(){"): text.index("owner_mode(){")]
    proc = subprocess.run([BASH, "-c", f"set -f\n{lib}\ntrust_problem \"$1\"", "x", path], capture_output=True,
                          text=True, check=False)
    assert bool(proc.stdout.strip()) == bool(grant_v2.trust_root_problem(path)), (path, proc.stdout)


# ---------------------------------------------------------------- L7 quarantine, run locally

def _mv_fake(bindir: Path) -> None:
    """GNU mv -T semantics on any platform (rename the target itself, never into it)."""
    tool(bindir, "mv", f'#!/bin/bash\nt=false; a=()\nfor x; do case "$x" in -T) t=true;; --) ;; *) a+=("$x");; esac; done\n'
                       f'if $t; then exec "{sys.executable}" -c "import os,sys; os.replace(sys.argv[1], sys.argv[2])" '
                       '"${a[0]}" "${a[1]}"; fi\nexec /bin/mv "$@"\n')


@pytest.fixture
def l7(tmp_path: Path) -> dict:
    tree = (tmp_path / "srv" / "anaconda3.disabled-20261101").resolve()
    (tree / "lib").mkdir(parents=True)
    (tree / "lib" / "f.py").write_text("x = 1\n")
    q = tmp_path.resolve() / "quarantine"
    script = tmp_path / "L7.sh"
    script.write_text((KIT_SRC / "L7-remove-path.sh").read_text().replace(
        'QROOT="/var/backups/agentteams/quarantine"', f'QROOT="{q}"'))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _mv_fake(bindir)
    env = {"PATH": f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin", "LC_ALL": "C"}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([BASH, str(script), *args], capture_output=True, text=True, env=env, timeout=60,
                              check=False)
    return {"tree": tree, "q": q, "run": run, "tmp": tmp_path.resolve()}


def test_l7_plan_then_quarantine_with_archive_and_restore_manifest(l7: dict) -> None:
    tree = str(l7["tree"])
    plan = l7["run"]("--level", "--path", tree)
    assert plan.returncode == 0, (plan.stdout, plan.stderr)
    assert "PLAN ONLY" in plan.stdout and l7["tree"].is_dir()
    digest = server_apply.plan_sha256(plan.stdout)
    bad = l7["run"]("--level", "--path", tree, "--apply", "--expect-digest", "c" * 64)
    assert bad.returncode == 5 and l7["tree"].is_dir() and not l7["q"].exists()
    done = l7["run"]("--level", "--path", tree, "--apply", "--expect-digest", digest)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert not l7["tree"].exists()
    (qdir,) = list(l7["q"].iterdir())
    assert (qdir / "payload" / "lib" / "f.py").read_text() == "x = 1\n"
    manifest = (qdir / "RESTORE.manifest").read_text()
    assert f"original_path={tree}" in manifest and "archive_sha256=" in manifest and "ever deletes it" in manifest
    listing = subprocess.run(["tar", "-tzf", str(qdir / "archive.tar.gz")], capture_output=True, text=True, check=True)
    assert "anaconda3.disabled-20261101/lib/f.py" in listing.stdout
    assert f"QUARANTINED\t{tree}\t{qdir}" in done.stdout


def test_l7_refuses_symlinks_and_symlinked_ancestors(l7: dict) -> None:
    link = l7["tmp"] / "srv" / "link"
    link.symlink_to(l7["tree"])
    res = l7["run"]("--level", "--path", str(link))
    assert res.returncode == 2 and "symlink" in res.stdout
    alias = l7["tmp"] / "alias"
    alias.symlink_to(l7["tmp"] / "srv")
    res = l7["run"]("--level", "--path", str(alias / "anaconda3.disabled-20261101"))
    assert res.returncode == 2 and "realpath must equal the path" in res.stdout


def test_l7_refuses_trust_roots_missing_paths_and_no_level(l7: dict) -> None:
    assert "trust root" in l7["run"]("--level", "--path", "/etc/ssh").stdout
    assert l7["run"]("--level", "--path", str(l7["tmp"] / "nope")).returncode == 2
    assert "only under --level" in l7["run"]("--path", str(l7["tree"])).stdout


# ---------------------------------------------------------------- L2 / L4 / L6 level mode on a fake host

FAKE_COMMON = {
    "systemctl": '#!/bin/bash\necho "$*" >> "$FAKE_LOG"\ncase "$1" in\n  is-active) [ -n "${FAKE_INACTIVE:-}" ] && '
                 '{ [ -z "${FAKE_INACTIVE_IF_LINK:-}" ] || [ -L "$FAKE_INACTIVE_IF_LINK" ]; } && exit 3; exit 0;;\n'
                 '  show) echo 4242;;\nesac\nexit 0\n',
    "ss": '#!/bin/sh\necho "LISTEN 0 128 0.0.0.0:8000 0.0.0.0:* users:((\\"py\\",pid=4242,fd=3))"\n',
    "sleep": "#!/bin/sh\nexit 0\n",
    "hostname": "#!/bin/sh\necho host1\n",
    "getent": '#!/bin/sh\necho "svc:x:1:1::$FAKE_HOME:/bin/sh"\n',
    "runuser": '#!/bin/bash\nwhile [ "$1" != "--" ]; do shift; done; shift; exec "$@"\n',
    "curl": "#!/bin/sh\nexit 0\n",
}


def _host(tmp_path: Path, extra: dict[str, str]) -> dict[str, str]:
    bindir = tmp_path / "hostbin"
    bindir.mkdir(exist_ok=True)
    for name, body in {**FAKE_COMMON, **extra}.items():
        tool(bindir, name, body)
    _mv_fake(bindir)
    (tmp_path / "log").write_text("")
    return {"PATH": f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin", "LC_ALL": "C", "FAKE_LOG": str(tmp_path / "log"),
            "TMPDIR": str(tmp_path),
            "FAKE_HOME": str(tmp_path)}


APT = ('#!/bin/bash\necho "apt-get $*" >> "$FAKE_LOG"\nif [ "$1" = -s ]; then\n'
       "  echo 'Inst libssl3 [3.0.2-1] (3.0.2-2 Ubuntu:22.04/jammy-security [amd64])'\n"
       "  echo 'Inst vim [9.0-1] (9.0-2 Ubuntu:22.04/jammy-updates [amd64])'\n"
       "  echo 'Inst newdep (1.0 Ubuntu:22.04/jammy-security [amd64])'\nfi\nexit 0\n")
NEEDRESTART = "#!/bin/sh\necho 'NEEDRESTART-SVC: nginx.service'\necho 'NEEDRESTART-SVC: other.service'\n"


def _l2(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    host = _host(tmp_path, {"apt-get": APT, "needrestart": NEEDRESTART,
                            "dpkg-query": "#!/bin/sh\necho 'libssl3 3.0.2-1'\n"})
    return subprocess.run([BASH, str(KIT_SRC / "L2-os-patch.sh"), *args], capture_output=True, text=True,
                          env={**host, **env}, timeout=120, check=False)


def test_l2_level_plans_security_upgrades_only_and_installs_exactly_them(tmp_path: Path) -> None:
    plan = _l2(tmp_path, "--level", "--restart-unit", "nginx.service")
    assert plan.returncode == 0, (plan.stdout, plan.stderr)
    assert [ln for ln in plan.stdout.splitlines() if ln.startswith("PLAN")] == ["PLAN\tos\tapt\tlibssl3\t3.0.2-1\t3.0.2-2"]
    assert "PRESTATE\tdpkg\tlibssl3 3.0.2-1" in plan.stdout
    digest = server_apply.plan_sha256(plan.stdout)
    assert _l2(tmp_path, "--level", "--apply", "--expect-digest", "d" * 64).returncode == 5
    assert "install" not in (tmp_path / "log").read_text()
    done = _l2(tmp_path, "--level", "--restart-unit", "nginx.service", "--apply", "--expect-digest", digest)
    assert done.returncode == 0, (done.stdout, done.stderr)
    log = (tmp_path / "log").read_text()
    assert "install --only-upgrade libssl3=3.0.2-2" in log and "vim" not in log.split("-s upgrade")[-1]
    assert "restart nginx.service" in log and "restart other.service" not in log
    assert "NEEDS  other.service restart pending" in done.stdout and "update" not in log   # no list refresh


def test_l2_level_failed_health_is_exit_7_and_never_reboots(tmp_path: Path) -> None:
    plan = _l2(tmp_path, "--level")
    digest = server_apply.plan_sha256(plan.stdout)
    res = _l2(tmp_path, "--level", "--restart-unit", "nginx.service", "--apply", "--expect-digest", digest,
              FAKE_INACTIVE="1")
    assert res.returncode == 7 and "FAIL" in res.stdout, (res.stdout, res.stderr)
    for bad in (["--level", "--reboot"], ["--level", "--all"], ["--level", "--restart-services"]):
        assert _l2(tmp_path, *bad).returncode == 2


def _fake_python(tmp_path: Path, versions: dict[str, str]) -> Path:
    shows = "".join(f'  {k}) echo "Version: {v}";;\n' for k, v in versions.items())
    return tool(tmp_path, "fakepy", f'#!/bin/bash\necho "py $*" >> "$FAKE_LOG"\n'
                f'if [ "$1" = -c ]; then exit 1; fi\nif [ "$1" = -V ]; then echo "Python 3.11.9"; exit 0; fi\n'
                f'if [ "$1 $2" = "-m pip" ]; then case "$3" in\n  show) case "$4" in\n{shows}  esac;;\n'
                f'  freeze) echo "idna==3.7"; echo "aiohttp==3.9.0"; echo "six==1.16.0";;\n  esac; exit 0; fi\n'
                f'if [ "$1 $2" = "-m venv" ]; then mkdir -p "$3/bin"; cp "$0" "$3/bin/python"; '
                f'echo "home = x" > "$3/pyvenv.cfg"; exit 0; fi\nexit 0\n')


def test_l4_level_refuses_an_unallowed_major_jump(tmp_path: Path) -> None:
    host = _host(tmp_path, {})
    app = tmp_path / "app"
    app.mkdir()
    py = _fake_python(tmp_path, {"idna": "2.10"})
    base = [BASH, str(KIT_SRC / "L4-python-app.sh"), "--level", "--unit", "a.service", "--dir", str(app), "--python",
            str(py), "--as", "svc", "--pin", "idna==3.10"]
    res = subprocess.run(base, capture_output=True, text=True, env=host, timeout=60, check=False)
    assert res.returncode == 2 and "crosses a major version" in res.stderr, (res.stdout, res.stderr)
    ok = subprocess.run([*base, "--major-allowed"], capture_output=True, text=True, env=host, timeout=60, check=False)
    assert "PLAN\tpins\ta.service\tidna\t2.10\t3.10" in ok.stdout and "PRESTATE\tpip\ta.service\tidna==3.7" in ok.stdout
    (tmp_path / "log").write_text("")
    mism = subprocess.run([*base, "--major-allowed", "--apply", "--expect-digest", "e" * 64], capture_output=True,
                          text=True, env=host, timeout=60, check=False)
    assert mism.returncode == 5 and "pip install" not in (tmp_path / "log").read_text()
    for bad in ("--git-pull", "--allow-sdist"):
        assert subprocess.run([*base, bad], capture_output=True, text=True, env=host, timeout=60).returncode == 2


def _l6(tmp_path: Path, **env: str) -> tuple[list[str], dict[str, str], Path]:
    host = _host(tmp_path, {})
    app = tmp_path / "app"
    venv = app / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n")
    py = _fake_python(tmp_path, {"aiohttp": "3.9.0"})
    shutil.copy2(py, venv / "bin" / "python")
    args = [BASH, str(KIT_SRC / "L6-py-venv.sh"), "--level", "--unit", "a.service", "--dir", str(app), "--python",
            str(venv / "bin" / "python"), "--as", "svc", "--venv-base", str(py), "--pin", "aiohttp==3.14.4"]
    return args, {**host, **env}, venv


def test_l6_builds_beside_switches_and_keeps_the_old_venv(tmp_path: Path) -> None:
    args, env, venv = _l6(tmp_path)
    plan = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60, check=False)
    assert plan.returncode == 0 and "PLAN\tvenv\ta.service\tpin\taiohttp\t3.9.0\t3.14.4" in plan.stdout, plan.stdout
    digest = server_apply.plan_sha256(plan.stdout)
    done = subprocess.run([*args, "--apply", "--expect-digest", digest], capture_output=True, text=True, env=env,
                          timeout=60, check=False)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert venv.is_symlink() and ".agentteams-" in os.readlink(venv)
    olds = [p for p in venv.parent.iterdir() if ".agentteams-prev-" in p.name]
    assert len(olds) == 1 and (olds[0] / "pyvenv.cfg").is_file()                # the old venv is kept
    req = (Path(os.readlink(venv)) / "agentteams-requirements.txt").read_text().split()
    assert "aiohttp==3.14.4" in req and "aiohttp==3.9.0" not in req and "six==1.16.0" in req


def test_l6_failed_health_switches_back_and_exits_6(tmp_path: Path) -> None:
    args, env, venv = _l6(tmp_path)
    plan = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60, check=False)
    env = {**env, "FAKE_INACTIVE": "1", "FAKE_INACTIVE_IF_LINK": str(venv)}      # unhealthy only on the new venv
    res = subprocess.run([*args, "--apply", "--expect-digest", server_apply.plan_sha256(plan.stdout)],
                         capture_output=True, text=True, env=env, timeout=60, check=False)
    assert res.returncode == 6 and "REVERTED" in res.stdout, (res.stdout, res.stderr)
    assert venv.is_dir() and not venv.is_symlink() and (venv / "pyvenv.cfg").is_file()


def test_l6_runs_only_under_level(tmp_path: Path) -> None:
    args, env, _ = _l6(tmp_path)
    res = subprocess.run([a for a in args if a != "--level"], capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 2 and "only under --level" in res.stdout
