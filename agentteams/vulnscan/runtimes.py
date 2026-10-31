"""Judge installed runtimes and OS releases against live release data.

The products judged are the rows of :data:`PRODUCTS` (language runtimes, servers and
libraries: Node.js, PHP, Python, Ruby, Go, Java, .NET, nginx, OpenSSL, PostgreSQL, MySQL,
MariaDB, Redis, Docker Engine) and :data:`OS_PRODUCTS` (OS releases, by os-release ``ID``
or image name). Three findings are possible for a runtime:

- **end of life**: the release line no longer gets security fixes (HIGH);
- **missing a security release**: a newer release on the same line is flagged as a
  security release (HIGH). Only products with a ``security_feed`` can say this:
  nodejs.org marks these in ``dist/index.json``; php.net tags them ``security``;
- **behind the latest patch**: newer patch releases exist but are not flagged as
  security releases (MODERATE). A product without a feed is judged on end of life and
  latest patch only, and its detail says so.

An OS release (:func:`evaluate_os`) is judged on its release line alone: end of life or not.

Runtimes owned by an OS package manager and installed from the distribution's own archive
(reported as ``pkg:<name>``, optionally ``pkg:<name>:universe``) are not compared with upstream
versions. Distributions backport fixes without changing the upstream number, so those runtimes
are left to OS security updates and reported as such. A package-manager install from any other
origin (``repo:<host>:<name>``, ``repo:unknown:<name>``: nginx.org, PGDG, docker, a PPA, a local
``.deb``) gets no distro backports and is judged like an upstream install.

Data sources: endoflife.date (release lines, EOL dates, latest patch), nodejs.org,
www.php.net. When a source is unreachable, or the release line is unknown to it, the
runtime is reported as *unchecked*, never as fine.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from agentteams.vulnscan import _http
from agentteams.vulnscan.versions import version_key


@dataclass(frozen=True)
class Probe:
    """How to read a product's version on this machine.

    ``argv[0]`` is looked up on ``PATH``; the rest are its arguments. ``regex`` group 1 is the
    version (one to three parts: ``16.4``, ``"21"``, ``1.8.0_412``). ``each`` takes every match
    (``dotnet --list-runtimes`` lists one runtime per line). Output containing ``reject``, or
    lacking ``only_if``, belongs to another product's probe and is skipped (``mysqld`` is MySQL's or
    MariaDB's); output lacking ``require`` is no product vulnscan knows and is reported as not
    judged (LibreSSL's ``openssl``).
    """

    argv: tuple[str, ...]
    stream: str          # "stdout" | "stderr" | "both"
    regex: str
    each: bool = False
    require: str = ""
    only_if: str = ""
    reject: str = ""


@dataclass(frozen=True)
class Product:
    """One row of :data:`PRODUCTS` or :data:`OS_PRODUCTS`.

    ``cycle`` is how many leading version parts name the release line; ``cycle_fn`` replaces it
    where a part count does not fit (Java ``1.8.0_x`` is line ``8``). ``norm`` makes an installed
    version and endoflife.date's ``latest`` comparable (Java ``8u432-b06`` and ``1.8.0_412``).
    ``probes`` is empty for a row only images choose (``java-corretto``).
    """

    eol: str                                   # endoflife.date product slug
    cycle: int                                 # version parts in a release-line name
    security_feed: str | None = None           # "node" | "php" | None
    probes: tuple[Probe, ...] = ()
    label: str = ""                            # name in finding details; the kind when empty
    cycle_fn: Callable[[str], str] | None = field(default=None, compare=False)
    norm: Callable[[str], str] | None = field(default=None, compare=False)


def _java_norm(version: str) -> str:
    """``1.8.0_412`` -> ``8.0.412``; ``8u432-b06`` -> ``8.0.432``; ``21.0.4+7`` -> ``21.0.4``."""
    v = version.strip().split("+", 1)[0]
    m = re.match(r"^1\.(\d+)\.(\d+)_(\d+)", v)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    m = re.match(r"^(\d+)u(\d+)", v)
    if m:
        return f"{m.group(1)}.0.{m.group(2)}"
    return v


def _java_cycle(version: str) -> str:
    return _java_norm(version).split(".")[0]


def _openssl_cycle(version: str) -> str:
    """OpenSSL 3.x lines are ``3.0``, ``3.5``; before 3, the line is three parts (``1.1.1``)."""
    nums = re.findall(r"\d+", version)
    return ".".join(nums[:3] if nums and nums[0] in ("0", "1") else nums[:2])


def _major_from(first: int) -> Callable[[str], str]:
    """A cycle rule: majors from ``first`` name a line alone (``16``); older ones by two parts (``9.6``).

    endoflife.date names PostgreSQL lines this way from 10, .NET from 5 (``8``; ``3.1``) and Docker
    Engine from 27 (``28``; ``26.1``).
    """
    def cycle(version: str) -> str:
        parts = version.split(".")
        return parts[0] if parts[0].isdigit() and int(parts[0]) >= first else ".".join(parts[:2])
    return cycle


_V3 = r"(\d+\.\d+\.\d+)"
_JAVA = Probe(("java", "-version"), "both", r'version "(\d+(?:\.\d+){0,2}(?:_\d+)?)')
_JAVA_LABEL = "Java (judged on Eclipse Temurin's support dates)"

#: Runtime kinds vulnscan judges: kind -> :class:`Product`. A ``RUNTIME`` line of any other kind is
#: reported unscannable, never dropped. Slugs and cycle formats are checked against endoflife.date by the
#: operator's verification script; a mismatch only leaves that product ``unchecked``.
PRODUCTS: dict[str, Product] = {
    "node": Product("nodejs", 1, "node", (Probe(("node", "-v"), "stdout", r"^v?" + _V3),)),
    "php": Product("php", 2, "php", (Probe(("php", "-v"), "stdout", r"PHP " + _V3),)),
    "python": Product("python", 2, None, (Probe(("python3", "--version"), "both", r"Python " + _V3),)),
    "ruby": Product("ruby", 2, None, (Probe(("ruby", "-v"), "stdout", r"ruby " + _V3),)),
    "go": Product("go", 2, None, (Probe(("go", "version"), "stdout", r"go(\d+\.\d+(?:\.\d+)?)"),)),
    "java": Product("eclipse-temurin", 1, None, (_JAVA,), _JAVA_LABEL, _java_cycle, _java_norm),
    "java-corretto": Product("amazon-corretto", 1, None, (), "Java (Amazon Corretto)", _java_cycle, _java_norm),
    "dotnet": Product("dotnet", 2, None, (Probe(("dotnet", "--list-runtimes"), "stdout",
                                                r"^Microsoft\.[A-Za-z.]+ " + _V3, each=True),), ".NET",
                      _major_from(5)),
    "nginx": Product("nginx", 2, None, (Probe(("nginx", "-v"), "both", r"nginx/" + _V3),)),
    "openssl": Product("openssl", 2, None, (Probe(("openssl", "version"), "stdout", r"^OpenSSL (\d+\.\d+\.\d+[a-z]*)",
                                                  require="OpenSSL"),), "OpenSSL", _openssl_cycle),
    "postgresql": Product("postgresql", 1, None, (
        Probe(("postgres", "--version"), "stdout", r"\(PostgreSQL\) (\d+\.\d+(?:\.\d+)?)"),
        Probe(("psql", "--version"), "stdout", r"\(PostgreSQL\) (\d+\.\d+(?:\.\d+)?)")), "PostgreSQL", _major_from(10)),
    "mysql": Product("mysql", 2, None, (Probe(("mysqld", "--version"), "stdout", r"Ver " + _V3, reject="MariaDB"),), "MySQL"),
    "mariadb": Product("mariadb", 2, None, (
        Probe(("mariadbd", "--version"), "stdout", r"Ver " + _V3, require="MariaDB"),
        Probe(("mysqld", "--version"), "stdout", r"Ver " + _V3, only_if="MariaDB")), "MariaDB"),
    "redis": Product("redis", 2, None, (Probe(("redis-server", "--version"), "stdout", r"v=" + _V3),), "Redis"),
    # Server version only: never starts a daemon; no socket access leaves stdout empty (unscannable).
    "docker": Product("docker-engine", 2, None, (Probe(("docker", "version", "--format", "{{.Server.Version}}"),
                                                       "stdout", r"^(\d+\.\d+\.\d+)"),), "Docker Engine",
                      _major_from(27)),
}

#: endoflife.date product per kind (derived from :data:`PRODUCTS`; kept for existing callers).
EOL_PRODUCT: dict[str, str] = {k: p.eol for k, p in PRODUCTS.items()}

#: OS releases, by os-release ``ID`` or image name -> :class:`Product` (no probes, no feed). Judged by
#: :func:`evaluate_os` on the release line alone.
OS_PRODUCTS: dict[str, Product] = {
    "ubuntu": Product("ubuntu", 2, label="Ubuntu"),
    "debian": Product("debian", 1, label="Debian"),
    "alpine": Product("alpine", 2, label="Alpine Linux"),
    "amzn": Product("amazon-linux", 1, label="Amazon Linux"),
    "amazonlinux": Product("amazon-linux", 1, label="Amazon Linux"),
    "rhel": Product("rhel", 1, label="Red Hat Enterprise Linux"),
    "rocky": Product("rocky-linux", 1, label="Rocky Linux"),
    "almalinux": Product("almalinux", 1, label="AlmaLinux"),
    "fedora": Product("fedora", 1, label="Fedora"),
    "centos": Product("centos", 1, label="CentOS"),
}

#: Release codenames (image tags and suffixes such as ``-bookworm``, ``:jammy``) -> version, per OS.
OS_CODENAMES: dict[str, dict[str, str]] = {
    "debian": {"stretch": "9", "buster": "10", "bullseye": "11", "bookworm": "12", "trixie": "13"},
    "ubuntu": {"xenial": "16.04", "bionic": "18.04", "focal": "20.04", "jammy": "22.04", "noble": "24.04"},
}

_NO_FEED = "no upstream security-release feed; judged on end-of-life and latest patch only"
_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class RuntimeFinding:
    """Verdict on one runtime installation."""

    kind: str          # a PRODUCTS key, or an OS_PRODUCTS key for evaluate_os
    version: str
    path: str
    manager: str       # "pkg:<name>", "repo:<origin>:<name>", "brew", "nvm", "tarball", ...
    severity: int      # 0..4 (see osv.SEVERITY_NAME); 0 = fine / informational
    verdict: str       # "ok" | "eol" | "security-release-missing" | "behind-patch" | "distro" | "unchecked"
    detail: str
    upgrade_to: str = ""


def _cycle_with(product: Product, version: str) -> str:
    version = version.strip().lstrip("v")
    if product.cycle_fn is not None:
        return product.cycle_fn(version)
    return ".".join(version.split(".")[:product.cycle])


def cycle_of(kind: str, version: str) -> str:
    """Return the endoflife.date release-line name ``version`` of ``kind`` belongs to.

    Args:
        kind: A :data:`PRODUCTS` or :data:`OS_PRODUCTS` key (``PRODUCTS`` wins on a clash).
        version: An installed version (``"v24.22.0"``, ``"1.8.0_412"``) or an OS ``VERSION_ID``.

    Returns:
        The cycle name, e.g. ``"24"``, ``"8.3"``, ``"8"``, ``"1.1.1"``, ``"22.04"``.

    Raises:
        KeyError: ``kind`` is in neither table.
    """
    product = PRODUCTS.get(kind) or OS_PRODUCTS[kind]
    return _cycle_with(product, version)


def _eol_cycles(product: str) -> list[dict]:
    """Return endoflife.date cycles as [{'cycle','eol','latest'}], handling API v1 and v0 shapes."""
    key = f"eol:{product}"
    if key not in _CACHE:
        try:
            data = _http.fetch_json(f"https://endoflife.date/api/v1/products/{product}")
            rels = ((data or {}).get("result") or {}).get("releases") or []
            cycles = [{"cycle": str(r.get("name")),
                       "eol": r.get("eolFrom") if r.get("isEol") or r.get("eolFrom") else False,
                       "is_eol": bool(r.get("isEol")),
                       "eoes": r.get("eoesFrom"), "is_eoes": r.get("isEoes"),
                       "latest": str(((r.get("latest") or {}).get("name")) or "")} for r in rels]
        except _http.FeedError:
            data = _http.fetch_json(f"https://endoflife.date/api/{product}.json")
            cycles = [{"cycle": str(r.get("cycle")), "eol": r.get("eol"), "is_eol": None,
                       "eoes": r.get("extendedSupport"), "is_eoes": None,
                       "latest": str(r.get("latest") or "")} for r in (data or [])]
        _CACHE[key] = cycles
    return _CACHE[key]  # type: ignore[return-value]


def _is_eol(cycle: dict, today: _dt.date) -> bool:
    if cycle.get("is_eol") is not None:
        return bool(cycle["is_eol"])
    eol = cycle.get("eol")
    if isinstance(eol, bool):
        return eol
    try:
        return _dt.date.fromisoformat(str(eol)) <= today
    except ValueError:
        return False


def _extended_active(cycle: dict, today: _dt.date) -> bool | None:
    """Return whether a cycle's extended support (Ubuntu ESM) still runs; None when the data does not say."""
    if isinstance(cycle.get("is_eoes"), bool):
        return not cycle["is_eoes"]
    eoes = cycle.get("eoes")
    if isinstance(eoes, bool):
        return eoes          # v0 API: true = offered with no end date, false = none
    try:
        return _dt.date.fromisoformat(str(eoes)) > today
    except ValueError:
        return None


def _security_releases(feed: str | None, cycle: str) -> list[str] | None:
    """Versions on ``cycle`` that the ``feed`` (a product's ``security_feed``) flags as security releases.

    Returns None when the flag data cannot be read or has an unexpected shape, or the product has no
    feed, so the caller treats "is the newer patch a security release?" as unknown rather than as "no".
    """
    key = f"sec:{feed}:{cycle}"
    if key in _CACHE:
        return _CACHE[key]  # type: ignore[return-value]
    out: list[str] | None = []
    try:
        if feed == "node":
            data = _http.fetch_json("https://nodejs.org/dist/index.json")
            if not isinstance(data, list):
                out = None
            else:
                out = [str(r.get("version", "")).lstrip("v") for r in data
                       if isinstance(r, dict) and r.get("security") and str(r.get("version", "")).lstrip("v").split(".")[0] == cycle]
        elif feed == "php":
            data = _http.fetch_json(f"https://www.php.net/releases/index.php?json&max=200&version={cycle.split('.')[0]}")
            if not isinstance(data, dict) or not data:
                out = None
            else:
                out = [v for v, info in data.items()
                       if re.match(r"^\d+\.\d+\.\d+$", v) and cycle_of("php", v) == cycle
                       and isinstance(info, dict) and "security" in (info.get("tags") or [])]
        else:
            out = None   # no machine-readable security flag (python.org and every product without a feed)
    except _http.FeedError:
        out = None
    _CACHE[key] = out
    return out


def _release_line(slug: str, cycle: str, today: _dt.date) -> tuple[dict | None, str]:
    """Return (the cycle's endoflife.date record or None, the newest supported line's latest release)."""
    cycles = _eol_cycles(slug)
    info = next((c for c in cycles if c["cycle"] == cycle), None)
    supported = [c for c in cycles if not _is_eol(c, today)]
    newest = max((c["latest"] for c in supported if c["latest"]), key=version_key, default="")
    return info, newest


def evaluate(kind: str, version: str, path: str = "", manager: str = "",
             today: _dt.date | None = None) -> RuntimeFinding:
    """Return the verdict for one runtime installation.

    Args:
        kind: A :data:`PRODUCTS` key (``"node"``, ``"php"``, ``"python"``, ``"java"``, ...).
        version: Installed version, e.g. ``"24.22.0"``, ``"v24.22.0"`` or ``"1.8.0_412"``.
        path: Binary path, for the report.
        manager: How it was installed. ``pkg:<name>`` (the distribution's archive) gives verdict ``distro``;
            ``repo:<origin>:<name>`` (a third-party or unknown origin) and everything else are judged upstream.
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        A :class:`RuntimeFinding`. Feed failures, a release line endoflife.date does not list and a
        kind not in :data:`PRODUCTS` produce verdict ``"unchecked"``.
    """
    version = version.strip().lstrip("v")
    today = today or _dt.date.today()
    if manager.startswith("pkg:"):
        return RuntimeFinding(kind, version, path, manager, 0, "distro",
                              "OS-packaged: fixes arrive as distro security updates (upstream version "
                              "numbers are not comparable) — keep OS security patching current")
    product = PRODUCTS.get(kind)
    if product is None:
        return RuntimeFinding(kind, version, path, manager, 0, "unchecked",
                              f"runtime kind {kind} not judged by this vulnscan version")
    label = product.label or kind
    norm = product.norm or (lambda v: v)
    cmp_version = norm(version)
    cycle = _cycle_with(product, version)
    try:
        info, newest_supported = _release_line(product.eol, cycle, today)
        if info is None:
            return RuntimeFinding(kind, version, path, manager, 0, "unchecked", f"release line {cycle} unknown to endoflife.date")
        if _is_eol(info, today):
            return RuntimeFinding(kind, version, path, manager, 3, "eol",
                                  f"{label} {cycle} is end-of-life — no more security fixes", newest_supported)
        latest = info["latest"]
        note = "" if product.security_feed else f" ({_NO_FEED})"
        sec_list = _security_releases(product.security_feed, cycle)
        newer_sec = [v for v in (sec_list or []) if version_key(v) > version_key(cmp_version)]
        if newer_sec:
            need = latest if latest and version_key(latest) >= max(map(version_key, newer_sec)) else max(newer_sec, key=version_key)
            return RuntimeFinding(kind, version, path, manager, 3, "security-release-missing",
                                  f"missing security release(s) {', '.join(sorted(newer_sec, key=version_key))}", need)
        if latest and version_key(cmp_version) < version_key(norm(latest)):
            if sec_list is None and product.security_feed:
                return RuntimeFinding(kind, version, path, manager, 3, "behind-patch",
                                      f"behind latest {cycle}.x patch; security-release data unavailable, "
                                      "so assume the newer patch is a security release", latest)
            return RuntimeFinding(kind, version, path, manager, 2, "behind-patch",
                                  f"behind latest {cycle}.x patch{note}", latest)
        return RuntimeFinding(kind, version, path, manager, 0, "ok", f"current on supported line {cycle}{note}")
    except _http.FeedError as exc:
        return RuntimeFinding(kind, version, path, manager, 0, "unchecked", f"release data unavailable: {exc}")


def evaluate_os(os_id: str, version_id: str, today: _dt.date | None = None) -> RuntimeFinding:
    """Return the verdict for one OS release: its release line is end-of-life or not.

    ``behind-patch`` does not apply: an OS release is patched by its package manager, not by a newer
    point release.

    Args:
        os_id: An :data:`OS_PRODUCTS` key: the os-release ``ID`` (``"ubuntu"``, ``"amzn"``) or an image
            name (``"amazonlinux"``).
        version_id: The os-release ``VERSION_ID`` (``"22.04"``, ``"12"``, ``"3.18.4"``) or a codename in
            :data:`OS_CODENAMES` (``"bookworm"``).
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        A :class:`RuntimeFinding` (``path`` and ``manager`` empty) with verdict ``"eol"`` (HIGH), ``"ok"``
        or ``"unchecked"`` (unknown OS, unknown release line or feed failure; never reported as fine).
    """
    os_id = os_id.strip().lower()
    version_id = version_id.strip().strip('"')
    today = today or _dt.date.today()
    product = OS_PRODUCTS.get(os_id)
    if product is None:
        return RuntimeFinding(os_id, version_id, "", "", 0, "unchecked", f"OS {os_id} not judged by this vulnscan version")
    version = OS_CODENAMES.get(os_id, {}).get(version_id.lower(), version_id)
    cycle = _cycle_with(product, version)
    try:
        info, newest_supported = _release_line(product.eol, cycle, today)
        if info is None:
            return RuntimeFinding(os_id, version, "", "", 0, "unchecked", f"release line {cycle} unknown to endoflife.date")
        if _is_eol(info, today):
            return RuntimeFinding(os_id, version, "", "", 3, "eol",
                                  f"{product.label} {cycle} is end-of-life — distro security updates have stopped",
                                  newest_supported)
        return RuntimeFinding(os_id, version, "", "", 0, "ok", f"{product.label} {cycle} is a supported release")
    except _http.FeedError as exc:
        return RuntimeFinding(os_id, version, "", "", 0, "unchecked", f"release data unavailable: {exc}")


def os_extended_support(os_id: str, version_id: str, today: _dt.date | None = None) -> bool | None:
    """Return whether an OS release's extended support (Ubuntu ESM) is still running.

    endoflife.date's ``eol`` for Ubuntu is the end of standard support; extended support (``eoesFrom``,
    or ``extendedSupport`` in the v0 API) runs later. :mod:`.engine` uses this to tell "standard support
    ended, ESM still possible" from a release whose ESM has ended too.

    Args:
        os_id: An :data:`OS_PRODUCTS` key (``"ubuntu"``).
        version_id: The os-release ``VERSION_ID`` or a codename in :data:`OS_CODENAMES`.
        today: Date for the comparison (tests); defaults to today.

    Returns:
        True while extended support runs, False once it has ended (or the release has none), None when the
        OS, its release line or the data is unknown, or the feed cannot be read.
    """
    os_id = os_id.strip().lower()
    product = OS_PRODUCTS.get(os_id)
    if product is None:
        return None
    version_id = version_id.strip().strip('"')
    version = OS_CODENAMES.get(os_id, {}).get(version_id.lower(), version_id)
    try:
        info, _newest = _release_line(product.eol, _cycle_with(product, version), today or _dt.date.today())
    except _http.FeedError:
        return None
    return None if info is None else _extended_active(info, today or _dt.date.today())
