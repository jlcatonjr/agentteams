"""L1-audit.sh records for the vfr plans, driven against fakes, and how the engine and report read them.

- A1 (vfr-a): ``DISTROPKG`` (owning package, source package, version, origin) for OS-managed Python paths, and
  ``AUTOUPD`` (unattended-upgrades / dnf-automatic settings, package-list age).
- E1 (vfr-e): ``SCOPE … disabled tree`` for a renamed ``*.disabled-YYYYMMDD`` tree only when nothing references it.
- E2b (vfr-e): ``SCOPE … on-disk-not-served exact`` for node_modules every covering nginx server refuses.
- R4 (vfr-r): ``PYSITE`` and the engine's end-of-life interpreter finding (``requires_python``).

Rule 12: a label never removes or regrades a finding; each label that could hide live code has a test here proving
the live case is not labelled.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine
from agentteams.vulnscan.report import Report
from tests.test_vulnscan_core import Feed, _adv, feed  # noqa: F401  (feed: fixture)
from tests.test_vulnscan_server_kit import BASH, KIT_SRC, Kit, _host, kit  # noqa: F401  (kit: fixture)

pytestmark = pytest.mark.skipif(not (Path(BASH).is_file() or shutil.which(BASH)), reason="bash not available")

TREE = "anaconda3.disabled-20261101"


def _run(hb: Path, root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    tmp = hb.parent / "hosttmp"   # mktemp's directory (the nginx -T dump): never the runner's own
    tmp.mkdir(exist_ok=True)
    return subprocess.run([BASH, str(KIT_SRC / "L1-audit.sh")], capture_output=True, text=True, timeout=120,
                          env={"PATH": str(hb), "AGENTTEAMS_L1_ROOT": str(root), "TMPDIR": str(tmp), **env},
                          check=False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _dist(sp: Path, name: str, version: str) -> None:
    _write(sp / f"{name}-{version}.dist-info" / "METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n")


# ---------------------------------------------------------------- E1: disabled trees

class DisabledHost:
    """A host with one renamed tree holding one site-packages directory; configuration under ``root``."""

    def __init__(self, kit: Kit, extra: dict[str, str] | None = None) -> None:
        self.kit = kit
        self.fs = kit.root / "fs"
        self.tree = self.fs / TREE
        self.sp = self.tree / "lib/python3.12/site-packages"
        _dist(self.sp, "zipp", "3.17.0")
        self.root = kit.root / "hostroot"
        self.root.mkdir(exist_ok=True)
        self.lists = {name: kit.root / f"find-{name}.txt" for name in ("dis", "sp", "pth", "links")}
        self.lists["dis"].write_text(f"{self.tree}\n")
        self.lists["sp"].write_text(f"{self.sp}\n")
        self.lists["pth"].write_text("")
        self.lists["links"].write_text("")
        self.hb = _host(kit, uid=0, extra=extra)

    def env(self) -> dict[str, str]:
        return {"FAKE_FIND_DIS": str(self.lists["dis"]), "FAKE_FIND_SP": str(self.lists["sp"]),
                "FAKE_FIND_PTH": str(self.lists["pth"]), "FAKE_FIND_LINKS": str(self.lists["links"])}

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        return _run(self.hb, self.root, **self.env(), **env)


def test_e1_inert_disabled_tree_is_labelled_and_still_inventoried(kit: Kit) -> None:
    h = DisabledHost(kit)
    out = h.run().stdout
    assert f"SCOPE\t{h.tree}\tdisabled\ttree\tinert since 2026-11-01\n" in out, out
    assert f"INV\tPyPI\tzipp\t3.17.0\t{h.sp}\n" in out          # Rule 12: still inventoried
    assert f"{h.sp}: 1 packages (in disabled tree {h.tree}, inert since 2026-11-01)" in out
    assert f"PYSITE\t{h.sp}\t3.12\n" in out


def _ref_unit(h: DisabledHost) -> None:
    _write(h.root / "etc/systemd/system/app.service", f"[Service]\nExecStart={h.tree}/bin/python app.py\n")


def _ref_pythonpath(h: DisabledHost) -> None:
    _write(h.root / "lib/systemd/system/w.service", f"[Service]\nEnvironment=PYTHONPATH={h.sp}\nExecStart=/usr/bin/x\n")


def _ref_envfile(h: DisabledHost) -> None:
    _write(h.root / "etc/systemd/system/w.service", "[Service]\nEnvironmentFile=-/opt/app/.env\nExecStart=/usr/bin/x\n")
    _write(h.root / "opt/app/.env", f"PYTHONPATH={h.sp}\n")


def _ref_crontab(h: DisabledHost) -> None:
    _write(h.root / "var/spool/cron/crontabs/root", f"0 3 * * * {h.tree}/bin/python /opt/job.py\n")


def _ref_dotfile(h: DisabledHost) -> None:   # by name: ~ and $HOME forms still name the tree
    _write(h.root / "home" / "appuser" / ".bashrc", f'export PATH="$HOME/{TREE}/bin:$PATH"\n')


def _ref_profile(h: DisabledHost) -> None:
    _write(h.root / "etc/profile.d/conda.sh", f". {h.tree}/etc/profile.d/conda.sh\n")


def _ref_pth(h: DisabledHost) -> None:
    pth = _write(h.fs / "opt/venv/lib/python3.12/site-packages/extra.pth", f"{h.sp}\n")
    h.lists["pth"].write_text(f"{pth}\n")


def _ref_symlink(h: DisabledHost) -> None:
    link = h.fs / "usr-bin-python"
    link.symlink_to(h.tree / "bin/python")
    h.lists["links"].write_text(f"{link}\n")


def _ref_process(h: DisabledHost) -> None:
    (h.root / "proc/4242").mkdir(parents=True)
    (h.root / "proc/4242/exe").symlink_to(h.tree / "bin/python3.12")


def _ref_maps(h: DisabledHost) -> None:
    _write(h.root / "proc/77/maps", f"7f00-7f01 r-xp 0 08:01 9 {h.sp}/lxml/etree.so\n")


@pytest.mark.parametrize("make", [_ref_unit, _ref_pythonpath, _ref_envfile, _ref_crontab, _ref_dotfile, _ref_profile,
                                  _ref_pth, _ref_symlink, _ref_process, _ref_maps])
def test_e1_a_referenced_tree_is_never_labelled(kit: Kit, make) -> None:   # noqa: ANN001
    """Rule 12: anything that can still start or load the tree keeps its findings reading as live."""
    h = DisabledHost(kit)
    make(h)
    out = h.run().stdout
    assert "SCOPE\t" not in out, out
    assert f"disabled tree {h.tree}: not labelled" in out
    assert f"INV\tPyPI\tzipp\t3.17.0\t{h.sp}\n" in out


def test_e1_a_tree_named_in_the_nginx_configuration_is_not_labelled(kit: Kit) -> None:
    nginx = (f"#!/bin/sh\n[ \"$1\" = -T ] && printf 'http {{ server {{ location /nb/ {{ alias {kit.root}/fs/{TREE}/s/; "
             "}} }} }}\\n'\nexit 0\n")
    h = DisabledHost(kit, extra={"nginx": nginx})
    out = h.run().stdout
    assert "SCOPE\t" not in out and "referenced by the nginx configuration" in out, out


def test_e1_nginx_dump_failure_fails_closed(kit: Kit) -> None:
    h = DisabledHost(kit, extra={"nginx": "#!/bin/sh\nexit 1\n"})
    out = h.run().stdout
    assert "SCOPE\t" not in out and "unverified: nginx -T failed" in out, out


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a mode-000 file")
def test_e1_an_unreadable_configuration_file_fails_closed(kit: Kit) -> None:
    h = DisabledHost(kit)
    f = _write(h.root / "etc/cron.d/job", "nothing here\n")
    f.chmod(0)
    try:
        out = h.run().stdout
    finally:
        f.chmod(0o644)
    assert "SCOPE\t" not in out and "unverified: a configuration file could not be read" in out, out


def test_e1_history_and_the_tree_itself_do_not_count_as_references(kit: Kit) -> None:
    """Shell history records the rename; it starts and loads nothing. A link inside the tree is the tree's own."""
    h = DisabledHost(kit)
    _write(h.root / "root/.bash_history", f"mv /root/anaconda3 {h.tree}\n")
    inner = h.tree / "bin/python"
    inner.parent.mkdir(parents=True)
    inner.symlink_to(h.tree / "bin/python3.12")
    h.lists["links"].write_text(f"{inner}\n")
    out = h.run().stdout
    assert f"SCOPE\t{h.tree}\tdisabled\ttree\tinert since 2026-11-01\n" in out, out


@pytest.mark.parametrize("name", ["anaconda3.disabled-20261301", "anaconda3.disabled-2026110", "anaconda3-disabled"])
def test_e1_only_a_dated_disabled_name_is_considered(kit: Kit, name: str) -> None:
    h = DisabledHost(kit)
    other = h.fs / name
    other.mkdir()
    h.lists["dis"].write_text(f"{other}\n")
    out = h.run().stdout
    assert "SCOPE\t" not in out and "no disabled trees" in out, out


def test_e1_an_indicator_inside_a_labelled_tree_stays_a_compromise(kit: Kit) -> None:
    h = DisabledHost(kit)
    bad = _write(h.sp / "evil.pth", "import os\n")
    found = kit.root / "find-ioc.txt"
    found.write_text(f"{bad}\n")
    proc = h.run(FAKE_FIND_IOC=str(found), AGENTTEAMS_IOC_DATA="name evil.pth\n")
    assert proc.returncode == 4, proc.stdout
    assert f"IOC\tindicator file: {bad} (in disabled tree {h.tree}, inert since 2026-11-01)\n" in proc.stdout


# ---------------------------------------------------------------- E1 / E2b: the engine and report

INV = "\n".join([
    f"SCOPE\t/root/{TREE}\tdisabled\ttree\tinert since 2026-11-01",
    "SCOPE\t/var/www/site/app\ton-disk-not-served\texact\tnginx server site: location ~ /node_modules/ { deny all; }",
    f"INV\tPyPI\tzipp\t3.17.0\t/root/{TREE}/lib/python3.12/site-packages",
    f"INV\tPyPI\tzipp\t3.17.0\t/root/{TREE}/pkgs/zipp-3.17.0/lib/python3.12/site-packages",
    f"INV\tPyPI\tFlask\t3.1.0\t/root/{TREE}/lib/python3.12/site-packages",
    "INV\tPyPI\tFlask\t3.1.0\t/opt/hayek-venv/lib/python3.12/site-packages",
    "INV\tnpm\tlodash\t4.17.20\t/var/www/site/app",
    "INV\tnpm\tminimist\t1.2.0\t/var/www/site/app/sub",
])


def _feed_for_inv(f: Feed) -> None:
    f.vulns = {("PyPI", "zipp", "3.17.0"): ["Z1"], ("PyPI", "Flask", "3.1.0"): ["F1"],
               ("npm", "lodash", "4.17.20"): ["L1"], ("npm", "minimist", "1.2.0"): ["M1"]}
    f.records = {"Z1": _adv("Z1", "PyPI", "zipp", "0", "3.19.1", "CRITICAL"),
                 "F1": _adv("F1", "PyPI", "Flask", "0", "3.1.1"),
                 "L1": _adv("L1", "npm", "lodash", "0", "4.17.21"),
                 "M1": _adv("M1", "npm", "minimist", "0", "1.2.6")}


def test_e1_e2b_labels_change_no_row_and_no_grade(feed: Feed) -> None:   # noqa: F811
    _feed_for_inv(feed)
    labelled = engine.scan_inventory(INV, "jlc-prod")
    plain = engine.scan_inventory("\n".join(ln for ln in INV.splitlines() if not ln.startswith("SCOPE")), "jlc-prod")
    assert [(f.name, f.severity, f.locations, f.key()) for f in labelled.findings] == \
        [(f.name, f.severity, f.locations, f.key()) for f in plain.findings]
    for fail_on in ("critical", "high", "moderate", "low", "any"):
        assert labelled.exit_code(fail_on) == plain.exit_code(fail_on)
    by = {f.name: f for f in labelled.findings}
    assert set(by["zipp"].location_labels) == set(by["zipp"].locations)
    assert list(by["Flask"].location_labels) == [f"/root/{TREE}/lib/python3.12/site-packages"]   # per location
    assert by["lodash"].location_labels["/var/www/site/app"]["label"] == "on-disk-not-served"
    assert by["minimist"].location_labels == {}          # exact: a nested manifest is not covered


def test_e1_e2b_summary_groups_only_fully_labelled_findings(feed: Feed) -> None:   # noqa: F811
    _feed_for_inv(feed)
    rep = engine.scan_inventory(INV, "jlc-prod")
    md = rep.markdown()
    assert md.count("| zipp (PyPI) |") == 1 and md.count("| Flask (PyPI) |") == 1   # every row stays
    assert "## On disk, not the live system (labelled, still graded)" in md
    assert f"`disabled tree /root/{TREE}, inert since 2026-11-01`: 1 finding(s), worst CRITICAL" in md
    assert "on-disk-not-served (nginx server site: location ~ /node_modules/ { deny all; })`: 1 finding(s)" in md
    assert "`jlc-prod`: 1 finding(s) also at a live location (not grouped): Flask" in md
    back = Report.from_dict(rep.to_dict())
    assert {f.name: f.location_labels for f in back.findings} == {f.name: f.location_labels for f in rep.findings}
    assert back.markdown().split("\n", 3)[3] == md.split("\n", 3)[3]


@pytest.mark.parametrize("path", ["/", "relative/path", ""])
def test_e1_a_scope_path_that_is_not_a_tree_labels_nothing(feed: Feed, path: str) -> None:   # noqa: F811
    _feed_for_inv(feed)
    rep = engine.scan_inventory(f"SCOPE\t{path}\tdisabled\ttree\tx\n" + INV.split("\n", 2)[2], "h")
    assert all(not f.location_labels for f in rep.findings)


def test_e1_an_old_report_without_labels_still_loads() -> None:
    rep = Report.from_dict({"target": "h", "findings": [{"kind": "package", "target": "h", "name": "a"}]})
    assert rep.findings[0].location_labels == {} and "not the live system" not in rep.markdown()


# ---------------------------------------------------------------- E2b: node_modules nginx refuses

def _web(kit: Kit, nginx_conf: str, *, apache: bool = False) -> tuple[subprocess.CompletedProcess[str], Path]:
    app = kit.root / "www/site/app"
    _write(app / "node_modules/.package-lock.json", '{"packages": {"node_modules/lodash": {"version": "4.17.20"}}}')
    lock = kit.root / "find-lock.txt"
    lock.write_text(f"{app}/node_modules/.package-lock.json\n")
    conf = kit.root / "nginx.conf"
    conf.write_text(nginx_conf.replace("WWW", os.path.realpath(kit.root / "www")))
    extra = {"python3": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
             "nginx": f'#!/bin/sh\n[ "$1" = -T ] && cat "{conf}"\nexit 0\n'}
    if apache:
        extra["apache2ctl"] = "#!/bin/sh\nexit 0\n"
    hb = _host(kit, extra=extra)
    root = kit.root / "hostroot"
    root.mkdir(exist_ok=True)
    return _run(hb, root, FAKE_FIND_LOCK=str(lock)), app


DENY = """# configuration file /etc/nginx/nginx.conf:
http { include /etc/nginx/sites-enabled/*; }
# configuration file /etc/nginx/sites-enabled/site:
server {
  server_name site.example.com;
  root WWW/site;
  location / { try_files $uri $uri/ =404; }
  location ^~ /app/node_modules/ { deny all; }
}
"""


def test_e2b_node_modules_every_server_refuses_is_labelled(kit: Kit) -> None:
    proc, app = _web(kit, DENY)
    assert f"\t4.17.20\t{app}\n" in proc.stdout
    want = (f"SCOPE\t{app}\ton-disk-not-served\texact\tnginx server site.example.com: "
            "location ^~ /app/node_modules/ { deny all; }\n")
    assert want in proc.stdout, (proc.stdout, proc.stderr)


@pytest.mark.parametrize("conf", [
    DENY.replace("location / {", "location ~ \\.js$ { expires 1d; }\n  location / {"),   # ^~ beats every regex
    DENY.replace("location ^~ /app/node_modules/", "location /app/node_modules/"),          # no regex anywhere
])
def test_e2b_other_refusals_that_win_are_labelled(kit: Kit, conf: str) -> None:
    proc, app = _web(kit, conf)
    assert f"SCOPE\t{app}\ton-disk-not-served\texact\t" in proc.stdout, proc.stdout


REGEX_DENY = DENY.replace("location ^~ /app/node_modules/", "location ~ /node_modules/")


@pytest.mark.parametrize(("conf", "why"), [
    (DENY.replace("location ^~ /app/node_modules/ { deny all; }", ""), "is served"),
    (DENY.replace("{ deny all; }", "{ allow 10.0.0.0/8; deny all; }"), "is served"),
    # security review 2: a regex refusal is never trusted (regex order, captures), even when it wins today
    (REGEX_DENY, "a regex location exists; a ^~ or = refusal is needed"),
    # an earlier regex location that serves .js wins over a later regex deny: refused as a regex refusal first
    (REGEX_DENY.replace("location / {", "location ~ \\.js$ { expires 1d; }\n  location / {"),
     "a regex location exists"),
    # a longer ^~ prefix that serves beats the refusal
    (DENY.replace("location / {", "location ^~ /app/node_modules/x/ { expires 1d; }\n  location / {"), "is served"),
    # security review 2: an include in a server, or a location outside every server (an included snippet)
    (DENY.replace("root WWW/site;", "root WWW/site;\n  include snippets/static.conf;"), "an include in a server"),
    (DENY + "# configuration file /etc/nginx/snippets/static.conf:\nlocation /app/ { expires 1d; }\n",
     "a location outside any server"),
    (DENY + "server { server_name other; root WWW; }\n", "is served"),          # a second server serves it
    (DENY.replace("root WWW/site;", "root site;"), "relative"),
    (DENY.replace("location / {", "location /x/ { alias WWW/site/app/; }\n  location / {"), "location-level alias"),
    (DENY.replace("deny all;", "deny all; }"), "unreadable"),
    ("http { server { root /elsewhere; location ^~ /node_modules/ { deny all; } } }\n", "under no nginx root"),
])
def test_e2b_node_modules_that_may_be_served_is_never_labelled(kit: Kit, conf: str, why: str) -> None:
    """Rule 12: the label needs proof that nothing serves the files; every doubt leaves it off."""
    proc, app = _web(kit, conf)
    assert "on-disk-not-served" not in proc.stdout, proc.stdout
    assert f"\t4.17.20\t{app}\n" in proc.stdout      # the packages are inventoried either way
    assert why in proc.stdout, proc.stdout


def test_e2b_apache_present_fails_closed(kit: Kit) -> None:
    proc, _app = _web(kit, DENY, apache=True)
    assert "on-disk-not-served" not in proc.stdout and "Apache present" in proc.stdout


# ---------------------------------------------------------------- A1: distro package source and auto-updates

FAKE_DPKG_A1 = r"""#!/bin/bash
[ "$1" = -S ] || exit 1
case "$(basename "$2")" in
  PyJWT-2.7.0.egg-info) echo "python3-jwt: $2";; *) exit 1;;
esac
"""
FAKE_DPKG_QUERY = r"""#!/bin/bash
case "$*" in
  *python3-jwt*) printf 'pyjwt\t2.7.0-1ubuntu0.1\tpyjwt\n';;
esac
"""
FAKE_APT_CACHE_A1 = r"""#!/bin/bash
echo "$6:"; echo "  Installed: 1"; echo "  Version table:"
printf ' *** 2.7.0-1ubuntu0.1 500\n        500 http://security.ubuntu.com/ubuntu noble-security/main amd64 Packages\n'
"""
FAKE_APT_CONFIG = r"""#!/bin/bash
[ "$1" = dump ] || exit 1
cat <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Acquire::http::Proxy "http://user:hunter2@proxy:3128";
Unattended-Upgrade::Allowed-Origins "";
Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}";
Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-security";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
"""


def test_a1_distro_packages_carry_owner_source_version_and_origin(kit: Kit) -> None:
    root = kit.root / "hostroot"
    sp = root / "usr/lib/python3/dist-packages"
    _write(sp / "PyJWT-2.7.0.egg-info" / "PKG-INFO", "Name: PyJWT\nVersion: 2.7.0\n\n")
    _dist(sp, "certifi", "2026.6.17")                       # status-only / unowned (vk-api, vfr-a A2)
    other = kit.root / "opt/venv/lib/python3.12/site-packages"
    _dist(other, "requests", "2.32.5")
    found = kit.root / "find-sp.txt"
    found.write_text(f"{sp}\n{other}\n")
    hb = _host(kit, extra={"dpkg": FAKE_DPKG_A1, "dpkg-query": FAKE_DPKG_QUERY, "apt-cache": FAKE_APT_CACHE_A1})
    out = _run(hb, root, FAKE_FIND_SP=str(found)).stdout
    assert f"DISTROPKG\t{sp}\tPyJWT\t2.7.0\tpython3-jwt\tpyjwt\t2.7.0-1ubuntu0.1\tpkg:python3-jwt\n" in out, out
    assert f"DISTROPKG\t{sp}\tcertifi\t2026.6.17\t-\t-\t-\tunowned\n" in out
    assert f"INV\tPyPI\tPyJWT\t2.7.0\t{sp}\n" in out and f"INV\tPyPI\trequests\t2.32.5\t{other}\n" in out
    assert out.count("DISTROPKG\t") == 2                  # a venv is not OS-managed


def test_a1_rpm_source_package_is_the_srpm_name(kit: Kit) -> None:
    root = kit.root / "hostroot"
    sp = root / "usr/lib/python3.9/site-packages"
    _dist(sp, "urllib3", "1.25.10")
    found = kit.root / "find-sp.txt"
    found.write_text(f"{sp}\n")
    rpm = r"""#!/bin/bash
case "$1" in
  -qf) echo "python3-urllib3-1.25.10-5.amzn2023.0.3.noarch";;
  -q) case "$*" in *SOURCERPM*) printf 'python-urllib3-1.25.10-5.amzn2023.0.3.src.rpm\t1.25.10-5.amzn2023.0.3\n';;
                   *VENDOR*) printf 'Amazon Linux';; esac;;
esac
"""
    hb = _host(kit, extra={"rpm": rpm})
    out = _run(hb, root, FAKE_FIND_SP=str(found)).stdout
    line = next(ln for ln in out.splitlines() if ln.startswith("DISTROPKG\t"))
    assert line.split("\t")[3:8] == ["1.25.10", "python3-urllib3-1.25.10-5.amzn2023.0.3.noarch", "python-urllib3",
                                     "1.25.10-5.amzn2023.0.3",
                                     "repo:unknown:python3-urllib3-1.25.10-5.amzn2023.0.3.noarch"]
    assert f"PYSITE\t{sp}\t3.9\n" in out


def test_a1_apt_auto_update_settings_and_list_age_never_print_other_config(kit: Kit) -> None:
    hb = _host(kit, extra={"apt-get": "#!/bin/sh\nexit 0\n", "apt-config": FAKE_APT_CONFIG,
                           "dpkg": "#!/bin/sh\nexit 1\n"})
    out = _run(hb, kit.root / "hostroot").stdout
    for line in ("AUTOUPD\tapt\tAPT::Periodic::Update-Package-Lists\t1",
                 "AUTOUPD\tapt\tAPT::Periodic::Unattended-Upgrade\t1",
                 "AUTOUPD\tapt\tUnattended-Upgrade::Allowed-Origins\t${distro_id}:${distro_codename}-security",
                 "AUTOUPD\tapt\tUnattended-Upgrade::Automatic-Reboot\tfalse",
                 "AUTOUPD\tapt\tlists-age-hours\tunknown"):
        assert line + "\n" in out, (line, out)
    assert "hunter2" not in out and "Proxy" not in out


def test_a1_dnf_automatic_settings_timers_and_metadata_age(kit: Kit) -> None:
    root = kit.root / "hostroot"
    _write(root / "etc/dnf/automatic.conf", "[commands]\nupgrade_type = security\napply_updates = yes\n"
                                            "[emitters]\nemit_via = stdio\n[email]\nemail_password = s3cr3t\n")
    repomd = _write(root / "var/cache/dnf/amazonlinux-abc/repodata/repomd.xml", "<repomd/>")
    old = repomd.stat().st_mtime - 30 * 3600
    os.utime(repomd, (old, old))
    systemctl = '#!/bin/sh\n[ "$1" = is-enabled ] && [ "$2" = dnf-automatic-install.timer ]\n'
    hb = _host(kit, extra={"dnf": "#!/bin/sh\nexit 0\n", "systemctl": systemctl})
    (hb / "stat").symlink_to(shutil.which("stat", path="/usr/bin:/bin"))
    out = _run(hb, root).stdout
    for line in ("AUTOUPD\tdnf\tupgrade_type\tsecurity", "AUTOUPD\tdnf\tapply_updates\tyes",
                 "AUTOUPD\tdnf\ttimer\tdnf-automatic-install.timer enabled"):
        assert line + "\n" in out, (line, out)
    age = next(ln for ln in out.splitlines() if ln.startswith("AUTOUPD\tdnf\tmetadata-age-hours\t"))
    assert age.split("\t")[3] in ("29", "30", "31"), age
    assert "s3cr3t" not in out and "emit_via" not in out


def test_a1_distro_records_without_findings_add_nothing() -> None:   # A2: test_vulnscan_distro_verdict.py
    rep = engine.scan_inventory("DISTROPKG\t/usr/lib/python3/dist-packages\tPyJWT\t2.7.0\tpython3-jwt\tpyjwt\t2.7.0-1"
                                "\tpkg:python3-jwt\nAUTOUPD\tapt\tlists-age-hours\t3\n", "h")
    assert rep.findings == [] and rep.feed_errors == [] and rep.checked == 0


# ---------------------------------------------------------------- R4: end-of-life interpreter, held packages

LOCAL = "/srv/app/.local/lib/python3.9/site-packages"
EOL = "https://endoflife.date/api/v1/products/python"


def _bastion(f: Feed, monkeypatch: pytest.MonkeyPatch, *, eol: bool = True) -> str:
    monkeypatch.setattr(engine, "_REQUIRES_PYTHON", {})
    f.vulns = {("PyPI", "aiohttp", "3.13.5"): ["A"], ("PyPI", "multidict", "6.7.1"): ["M"],
               ("PyPI", "idna", "3.6"): ["I"]}
    f.records = {"A": _adv("A", "PyPI", "aiohttp", "0", "3.14.4"),
                 "M": _adv("M", "PyPI", "multidict", "0", "6.9.1", "MODERATE"),
                 "I": _adv("I", "PyPI", "idna", "0", "3.7")}
    f.urls[EOL] = {"result": {"releases": [
        {"name": "3.9", "isEol": eol, "eolFrom": "2025-10-31", "latest": {"name": "3.9.25"}},
        {"name": "3.13", "isEol": False, "latest": {"name": "3.13.9"}}]}}
    f.urls["https://pypi.org/pypi/aiohttp/3.14.4/json"] = {"info": {"requires_python": ">=3.10"}}
    f.urls["https://pypi.org/pypi/multidict/6.9.1/json"] = {"info": {"requires_python": ">=3.10"}}
    f.urls["https://pypi.org/pypi/idna/3.7/json"] = {"info": {"requires_python": ">=3.5"}}
    pkgs = (("aiohttp", "3.13.5"), ("multidict", "6.7.1"), ("idna", "3.6"))
    return "\n".join([f"INV\tPyPI\t{n}\t{v}\t{LOCAL}" for n, v in pkgs] + [f"PYSITE\t{LOCAL}\t3.9"])


def test_r4_eol_interpreter_with_held_packages_is_its_own_finding(
        feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:   # noqa: F811
    rep = engine.scan_inventory(_bastion(feed, monkeypatch), "vk-bastion")
    held = [f for f in rep.findings if f.kind == "runtime"]
    assert len(held) == 1, rep.findings
    f = held[0]
    assert (f.name, f.version, f.severity, f.fix, f.locations) == ("python", "3.9", 3, "3.13.9", [LOCAL])
    assert "2 package(s) here are held below their fix by requires_python" in f.detail
    assert "aiohttp 3.13.5 → 3.14.4 (requires_python >=3.10)" in f.detail and "idna" not in f.detail
    assert len([x for x in rep.findings if x.kind == "package"]) == 3     # the package rows stay
    assert rep.feed_errors == []


def test_r4_supported_interpreter_gives_no_finding(feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:   # noqa: F811
    rep = engine.scan_inventory(_bastion(feed, monkeypatch, eol=False), "vk-bastion")
    assert not [f for f in rep.findings if f.kind == "runtime"]


def test_r4_unread_requires_python_is_a_feed_error_never_not_held(
        feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:   # noqa: F811
    inv = _bastion(feed, monkeypatch)
    del feed.urls["https://pypi.org/pypi/multidict/6.9.1/json"]
    rep = engine.scan_inventory(inv, "vk-bastion")
    assert any("requires_python of the fix release unread for 1 package(s)" in e and "multidict" in e
               for e in rep.feed_errors), rep.feed_errors
    held = [f for f in rep.findings if f.kind == "runtime"]
    assert len(held) == 1 and "1 package(s) here are held" in held[0].detail


def test_r4_os_managed_paths_are_left_to_the_distro(feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:   # noqa: F811
    inv = _bastion(feed, monkeypatch).replace(LOCAL, "/usr/lib/python3.9/site-packages")
    rep = engine.scan_inventory(inv, "vk-bastion")
    assert not [f for f in rep.findings if f.kind == "runtime"]


@pytest.mark.parametrize(("name", "version"), [("../etc", "1.0"), ("a b", "1"), ("ok", "1.0/../../x")])
def test_r4_requires_python_refuses_names_that_are_not_pypi_identifiers(name: str, version: str) -> None:
    with pytest.raises(_http.FeedError):
        engine.requires_python(name, version)


# ---------------------------------------------------------------- review fixes (security 1, 3, 4, 5; hygiene a, b)

@pytest.mark.parametrize("value", ['"/opt/app/env"', "/etc/app/%i.env", "-/etc/default", "-etc/default/app",
                                   "--/etc/default/app", "/opt/missing.env", "/a.env /b.env"])
def test_e1_an_environment_file_it_cannot_resolve_leaves_the_tree_unverified(kit: Kit, value: str) -> None:
    """Security 1: a unit environment file the check cannot read as one existing file is never assumed clean."""
    h = DisabledHost(kit)
    _write(h.root / "etc/default/app", "X=1\n")
    _write(h.root / "etc/systemd/system/w.service", f"[Service]\nEnvironmentFile={value}\nExecStart=/usr/bin/x\n")
    out = h.run().stdout
    assert "SCOPE\t" not in out and "unverified: EnvironmentFile=" in out, out


def _ref_preload(h: DisabledHost) -> None:
    _write(h.root / "etc/ld.so.preload", f"{h.tree}/lib/libhook.so\n")


def _ref_mountinfo(h: DisabledHost) -> None:
    _write(h.root / "proc/91/mountinfo", f"36 25 8:1 {h.tree} /srv/app rw,relatime - ext4 /dev/sda1 rw\n")


def _ref_pm2(h: DisabledHost) -> None:
    _write(h.root / "root/.pm2/dump.pm2", f'[{{"pm_exec_path": "{h.tree}/bin/python"}}]\n')


@pytest.mark.parametrize("where", ["etc/php/8.3/fpm/pool.d/www.conf", "etc/pam.d/sshd", "etc/default/app",
                                   "etc/logrotate.d/app", "var/spool/at/a0001", "var/spool/cron/atjobs/a0002",
                                   "run/systemd/generator.late/app.service", "usr/local/lib/systemd/system/a.service"])
def test_e1_more_reference_sources_block_the_label(kit: Kit, where: str) -> None:
    """Security 3: each further place a tree can be started or loaded from."""
    h = DisabledHost(kit)
    _write(h.root / where, f"x {h.tree}/bin/python\n")
    out = h.run().stdout
    assert "SCOPE\t" not in out and f"referenced by {h.root / where}" in out, out


@pytest.mark.parametrize("make", [_ref_preload, _ref_mountinfo, _ref_pm2])
def test_e1_preload_bind_mounts_and_pm2_block_the_label(kit: Kit, make) -> None:   # noqa: ANN001
    h = DisabledHost(kit)
    make(h)
    out = h.run().stdout
    assert "SCOPE\t" not in out and f"disabled tree {h.tree}: not labelled" in out, out


@pytest.mark.parametrize(("ps_rc", "mounts", "want"), [
    (0, "/var/lib/docker/volumes/x /srv/TREE/lib ", "mounted into a docker container"),
    (1, "", "unverified: docker ps failed"),
])
def test_e1_container_mounts_are_checked_and_unreadable_fails_closed(kit: Kit, ps_rc: int, mounts: str,
                                                                     want: str) -> None:
    docker = (f'#!/bin/sh\ncase "$1" in\n  ps) echo abc123; exit {ps_rc};;\n'
              f'  inspect) printf "%s" "{mounts.replace("TREE", TREE)}";;\nesac\n')
    h = DisabledHost(kit, extra={"docker": docker})
    _write(h.root / "var/run/docker.sock", "")   # a daemon socket exists: its failure is unverified
    out = h.run().stdout
    assert "SCOPE\t" not in out and want in out, out


def test_e1_a_container_without_the_tree_does_not_block(kit: Kit) -> None:
    docker = '#!/bin/sh\ncase "$1" in\n  ps) echo abc123;;\n  inspect) printf "/srv/data ";;\nesac\n'
    h = DisabledHost(kit, extra={"docker": docker})
    _write(h.root / "var/run/docker.sock", "")
    assert f"SCOPE\t{h.tree}\tdisabled\t" in h.run().stdout


@pytest.mark.parametrize("name", [".env", ".npmrc", ".pypirc", ".vault-token", ".s3cfg", ".boto"])
def test_e1_a_secret_store_dotfile_blocks_only_when_it_names_the_tree(kit: Kit, name: str) -> None:
    """Security ruling Q2: matched with grep -lF (path only, never its content)."""
    h = DisabledHost(kit)
    f = _write(h.root / "root" / name, "token=s3cr3t-value\n")
    proc = h.run()
    assert f"SCOPE\t{h.tree}\tdisabled\t" in proc.stdout, proc.stdout
    f.write_text(f"token=s3cr3t-value\nprefix={h.tree}\n")
    proc = h.run()
    assert "SCOPE\t" not in proc.stdout and f"referenced by {f}" in proc.stdout, proc.stdout
    assert "s3cr3t" not in proc.stdout + proc.stderr


def test_e1_an_optional_environment_file(kit: Kit) -> None:
    """Security ruling Q1: "-" missing loads nothing; "-" present is read like any other environment file."""
    h = DisabledHost(kit)
    _write(h.root / "etc/systemd/system/w.service", "[Service]\nEnvironmentFile=-/etc/default/app\nExecStart=/x\n")
    assert f"SCOPE\t{h.tree}\tdisabled\t" in h.run().stdout            # missing: systemd ignores it
    _write(h.root / "etc/default/app", "X=1\n")
    assert f"SCOPE\t{h.tree}\tdisabled\t" in h.run().stdout            # present, does not name the tree
    env = _write(h.root / "opt/app.env", f"PYTHONPATH={h.sp}\n")
    _write(h.root / "etc/systemd/system/w.service", "[Service]\nEnvironmentFile=-/opt/app.env\nExecStart=/x\n")
    out = h.run().stdout
    assert "SCOPE\t" not in out and f"referenced by {env}" in out, out


def test_e1_a_docker_client_without_a_daemon_contributes_nothing(kit: Kit) -> None:
    h = DisabledHost(kit, extra={"docker": "#!/bin/sh\nexit 1\n"})
    assert f"SCOPE\t{h.tree}\tdisabled\t" in h.run().stdout


def test_e1_a_failing_podman_stays_fail_closed(kit: Kit) -> None:
    h = DisabledHost(kit, extra={"podman": "#!/bin/sh\nexit 1\n"})
    out = h.run().stdout
    assert "SCOPE\t" not in out and "unverified: podman ps failed" in out, out


def test_nginx_dump_runs_once_and_its_temp_file_is_removed(kit: Kit) -> None:
    """Hygiene b / security 5: one `nginx -T`, into a temp file the EXIT trap removes; the exit code is kept."""
    calls = kit.root / "nginx-calls"
    nginx = f'#!/bin/sh\n[ "$1" = -T ] && {{ echo x >> "{calls}"; echo "http {{ }}"; }}\nexit 0\n'
    h = DisabledHost(kit, extra={"nginx": nginx})
    tmp = kit.root / "tmpdir"
    tmp.mkdir()
    proc = h.run(TMPDIR=str(tmp))
    assert calls.read_text().count("x") == 1, proc.stdout
    assert list(tmp.iterdir()) == []
    assert proc.returncode == 1 and "RESULT action-needed" in proc.stdout
    assert f"SCOPE\t{h.tree}\tdisabled\t" in proc.stdout
    header = (KIT_SRC / "L1-audit.sh").read_text().split("\n\n", 1)[0]
    assert "error-log" in header and "mktemp" in header


def test_a1_dnf_values_never_carry_a_tab(kit: Kit) -> None:
    root = kit.root / "hostroot"
    _write(root / "etc/dnf/automatic.conf", "[commands]\nupgrade_type = security\textra\n")
    hb = _host(kit, extra={"dnf": "#!/bin/sh\nexit 0\n"})
    out = _run(hb, root).stdout
    assert "AUTOUPD\tdnf\tupgrade_type\tsecurity extra\n" in out, out


def _scope_module() -> dict:
    """The E2b parser embedded in L1-audit.sh, loaded without running its main (hygiene a)."""
    text = (KIT_SRC / "L1-audit.sh").read_text()
    start = text.index('python3 -I - "$locs" "$NGINX_DUMP" <<\'PY\'\n')
    body = text[text.index("\n", start) + 1:]
    ns: dict = {"__name__": "nginx_scope"}
    exec(compile(body[:body.index("\nPY\n")], "L1-audit.sh:nginx_scope", "exec"), ns)   # noqa: S102
    return ns


def _locs(conf: str) -> list:
    ns = _scope_module()
    srv = next(sub for a, sub in ns["parse"](conf) if a[:1] == ["server"])
    return [(*ns["location"](d), sub) for d, sub in srv if d[:1] == ["location"] and sub is not None]


@pytest.mark.parametrize(("conf", "uri", "want"), [
    ("server { location = /a { x; } location /a { y; } }", "/a", "="),                  # exact first
    ("server { location ^~ /a/ { x; } location ~ /a/ { y; } }", "/a/b", "^~"),          # ^~ stops regexes
    ("server { location /a/ { x; } location ~ /a/ { y; } }", "/a/b", "~"),               # a plain prefix does not
    ("server { location ~ \\.js$ { x; } location ~ /a/ { y; } }", "/a/b.js", "~"),       # first regex in order
    ("server { location / { x; } location /a/b/ { y; } }", "/a/b/c", ""),                # longest prefix
    ("server { location ~* /A/ { x; } }", "/a/b", "~*"),                                 # ~* ignores case
    ("server { location /z/ { x; } }", "/a/b", None),                                    # nothing matches
])
def test_e2b_select_follows_nginx_location_order(conf: str, uri: str, want: str | None) -> None:
    sel = _scope_module()["select"](_locs(conf), uri)
    assert (sel[0] if sel else None) == want
    if conf.startswith("server { location ~ \\.js$"):
        assert sel[1] == "\\.js$"
    if "/a/b/ {" in conf:
        assert sel[1] == "/a/b/"


@pytest.mark.parametrize(("body", "refused"), [
    ("deny all;", True), ("internal;", True), ("return 404;", True), ("return 403;", True),
    ("return 301 /x;", False), ("allow 10.0.0.0/8; deny all;", False), ("if ($x) { return 404; } deny all;", False),
    ("rewrite ^ /x; deny all;", False), ("location /n/ { deny all; } deny all;", False), ("expires 1d;", False),
])
def test_e2b_refuses_only_an_unconditional_refusal(body: str, refused: bool) -> None:
    ns = _scope_module()
    (mod, arg, sub), = _locs(f"server {{ location ^~ /a/ {{ {body} }} }}")
    assert bool(ns["refuses"]((mod, arg, sub))) is refused
    assert ns["refuses"](None) == ""
