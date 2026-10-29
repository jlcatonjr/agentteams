"""Per-host server fix plans from the daily L1 audit (plan D1; no new authority).

Server changes stay a person running the kit's ``run-host.sh --apply``. This module only turns a host's findings into
``<state>/server-plans/<YYYY-MM-DD>/server-fixplan-<host>.md``, so that person sees what each kit level would change and
what it would not:

- **compromise indicators** first: the kit refuses to patch such a host in place (exit 4); nothing else applies;
- **L2 OS security updates** for OS-managed packages (``distro``), with the dry-run and apply commands;
- **L3 Node runtime** for stale runtimes. When the scan found Node missing a security release, the apply command
  carries ``--allow-fresh``: Rule S-10's remediation carve-out, so the kit's 14-day release cooldown does not hold
  the fix;
- **L4 Python apps** per ``apps.conf`` entry of the host whose app directory holds the vulnerable packages, with
  candidate ``--pin`` values (the minimal fixed versions; prefer the versions of the reviewed repository fix);
- **Python packages with no apps.conf entry**: ``run-host.sh`` never updates them; add an entry or fix them by hand;
- **Node app dependencies** (npm trees on the host): fixed in the app's repository and redeployed, never by the kit;
- **host checks** (``kind: host``): the audit's action lines, by hand.

The plans name hosts, app paths and the kit's location from the operator's own ``hosts.conf`` / ``apps.conf`` and
audit, so they are written outside the run directory: the agent container mounts ``<run>`` whole, and its CLI's
file tools could otherwise read them and send them to the model upstream (security review, 2026-10-27).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from agentteams.vulnscan.report import SEVERITY_NAME, Finding, Report

PLAN_GLOB = "server-fixplan-*.md"
_HOST_FILE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class App:
    """One ``apps.conf`` line: ``host | unit | app dir | python | L4 flags | health | pins``."""

    host: str
    unit: str
    app_dir: str
    pins: str

    def holds(self, location: str) -> bool:
        """Return whether a package location lies in this app's directory (``~user/dir`` expanded as on the host).

        Args:
            location: A package directory from the audit.

        Returns:
            True when ``location`` is the app directory or inside it.
        """
        d = self.app_dir.rstrip("/")
        if d.startswith("~"):
            user, _, rest = d[1:].partition("/")
            base = "/root" if user in ("", "root") else "/".join(("", "home", user))
            d = f"{base}/{rest}" if rest else base
        return bool(d) and (location == d or location.startswith(d + "/"))


def parse_apps(text: str) -> list[App]:
    """Parse ``apps.conf`` (``#`` comments and blank lines skipped; lines without 7 ``|`` fields ignored).

    Args:
        text: The file's content.

    Returns:
        One :class:`App` per usable line, in order.
    """
    apps = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 7 and cols[0] and cols[1] and cols[2]:
            apps.append(App(host=cols[0], unit=cols[1], app_dir=cols[2], pins=cols[6]))
    return apps


def _row(f: Finding) -> str:
    fix = f.fix or "no fixed version"
    return f"| {f.name} | {f.version} | {fix} | {SEVERITY_NAME.get(f.severity, '?')} | {len(f.ids)} |"


_TABLE = ["| Package | Installed | Fixed in | Severity | Advisories |", "|---|---|---|---|---|"]


def _node_vulnerable(f: Finding) -> bool:
    """Return whether a runtime finding is a Node.js install the scan found missing a security release.

    The finding's detail starts with its :func:`runtimes.evaluate` verdict. ``behind-patch`` counts only at HIGH,
    which the scan reports when it has no security-release data and assumes the newer patch is one.
    """
    verdict = f.detail.partition(":")[0]
    return "node" in f.name.lower() and (verdict == "security-release-missing"
                                         or (verdict == "behind-patch" and f.severity >= 3))


def markdown(host: str, findings: list[Finding], apps: list[App], *, kit: str = "<server kit>") -> str:
    """Return the fix plan for one host.

    Args:
        host: The ``hosts.conf`` name.
        findings: The host's findings (``target == host``).
        apps: Every ``apps.conf`` entry (only this host's are used).
        kit: How to name the server-kit directory in the commands.

    Returns:
        Markdown.
    """
    mine = [a for a in apps if a.host == host]
    out = [f"# Server fix plan — {host}", "",
           "Nothing here runs by itself: server changes are a person running the kit (vulnscan never applies them).",
           ""]
    iocs = [f for f in findings if f.kind == "ioc" or f.malicious]
    if iocs:
        out += ["## STOP: compromise indicators", "",
                "The kit refuses to patch this host in place (exit 4). Isolate it, investigate, rebuild from a known-good",
                "image and rotate its credentials; do not run the steps below.", ""]
        out += [f"- {f.name} {f.version} {f.detail}".rstrip() for f in iocs] + [""]
    distro = sorted((f for f in findings if f.kind == "package" and f.distro), key=lambda f: (-f.severity, f.name))
    runtimes = [f for f in findings if f.kind == "runtime"]
    py_apps: dict[App, list[Finding]] = defaultdict(list)
    py_orphans: dict[str, list[Finding]] = defaultdict(list)
    npm: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.kind != "package" or f.distro or f.malicious:
            continue
        where = f.locations[0] if f.locations else "(location not recorded)"
        if f.ecosystem == "PyPI":
            app = next((a for a in mine if any(a.holds(loc) for loc in f.locations)), None)
            if app is not None:
                py_apps[app].append(f)
            else:
                py_orphans[where].append(f)
        else:
            npm[where].append(f)
    fresh = " --allow-fresh" if any(_node_vulnerable(f) for f in runtimes) else ""
    out += ["## Commands", "", f"- dry run (changes nothing): `{kit}/run-host.sh {host}{fresh}`",
            f"- apply OS security updates, Node and the apps.conf pins: `{kit}/run-host.sh {host} --apply{fresh}` "
            "(add `--restart-services` / `--reboot` only when you mean it)"]
    if fresh:
        out.append("- `--allow-fresh`: the scan found Node missing a security release, so Rule S-10's remediation "
                   "carve-out lets L3 take a fix release inside the 14-day release cooldown")
    out.append("")
    if distro:
        out += [f"## L2 — OS security updates ({len(distro)} OS-managed package(s))", "",
                "`run-host.sh --apply` installs the distribution's security updates for these.", "", *_TABLE,
                *(_row(f) for f in distro), ""]
    if runtimes:
        out += ["## L3 — runtimes", ""] + [f"- {f.name} {f.version}: {f.detail or 'update it'}" for f in runtimes]
        out.append("")
        if any("node" in f.name.lower() for f in runtimes):
            out += [f"Node: `{kit}/run-host.sh {host} --apply --node-major <N>` moves to a supported major.", ""]
    for app, items in sorted(py_apps.items(), key=lambda kv: kv[0].unit):
        pins = " ".join(f"--pin {f.name}=={f.fix}" for f in sorted(items, key=lambda f: f.name) if f.fix)
        out += [f"## L4 — {app.unit} ({app.app_dir})", "", *_TABLE, *(_row(f) for f in items), ""]
        out.append(f"- apps.conf pins now: `{app.pins or '(empty: the entry is skipped)'}`")
        if pins:
            out.append(f"- candidate pins (minimal fixed versions; prefer your reviewed repository fix): `{pins}`")
        out.append("")
    if py_orphans:
        out += ["## Python packages with no apps.conf entry", "",
                "`run-host.sh` never updates these: add an apps.conf entry for the service that uses them, or fix",
                "them by hand (a package no service uses can be removed).", ""]
        for where, items in sorted(py_orphans.items()):
            out += [f"### `{where}`", "", *_TABLE, *(_row(f) for f in items), ""]
    if npm:
        out += ["## Node app dependencies", "",
                "Fix these in the app's repository (a vulnscan repo fix where it is enrolled) and redeploy; the kit",
                "does not change npm trees.", ""]
        for where, items in sorted(npm.items()):
            out += [f"### `{where}`", "", *_TABLE, *(_row(f) for f in items), ""]
    hosts = [f for f in findings if f.kind == "host"]
    if hosts:
        out += ["## Host checks (by hand)", ""] + [f"- {f.detail or f.name}" for f in hosts] + [""]
    return "\n".join(out)


def write(out_dir: Path, report: Report, hosts: list[str], apps_conf: Path | None, *, kit: str = "<server kit>"
          ) -> list[Path]:
    """Write ``server-fixplan-<host>.md`` for each host with findings; return the files written.

    Plans an earlier run of the same day wrote are removed first, so a host that is clean now has no stale plan.

    Args:
        out_dir: Where the day's plans go (``<state>/server-plans/<YYYY-MM-DD>``; created), never the run directory.
        report: The run's report (server findings have ``target == host``).
        hosts: The configured server host names.
        apps_conf: The operator's ``apps.conf`` (absent or unreadable: no apps are known).
        kit: How to name the server-kit directory in the commands.

    Returns:
        The plan files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(PLAN_GLOB):
        old.unlink()
    try:
        apps = parse_apps(apps_conf.read_text(encoding="utf-8", errors="replace")) if apps_conf else []
    except OSError:
        apps = []
    written = []
    for host in hosts:
        findings = [f for f in report.findings if f.target == host]
        if not findings:
            continue
        path = out_dir / f"server-fixplan-{_HOST_FILE.sub('-', host)}.md"
        path.write_text(markdown(host, findings, apps, kit=kit), encoding="utf-8")
        written.append(path)
    return written
