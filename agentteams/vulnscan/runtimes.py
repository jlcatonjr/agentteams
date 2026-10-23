"""Judge installed language runtimes (Node.js, PHP, Python) against live release data.

Three findings are possible:

- **end of life**: the release line no longer gets security fixes (HIGH);
- **missing a security release**: a newer release on the same line is flagged as a
  security release (HIGH). nodejs.org marks these in ``dist/index.json``; php.net tags
  them ``security``;
- **behind the latest patch**: newer patch releases exist but are not flagged as
  security releases (MODERATE).

Runtimes owned by an OS package manager (dpkg/rpm, reported as ``pkg:<name>``) are not
compared with upstream versions. Distributions backport fixes without changing the
upstream number, so those runtimes are left to OS security updates and reported as such.

Data sources: endoflife.date (release lines, EOL dates, latest patch), nodejs.org,
www.php.net. When a source is unreachable the runtime is reported as *unchecked*,
never as fine.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

from agentteams.vulnscan import _http
from agentteams.vulnscan.versions import version_key

EOL_PRODUCT = {"node": "nodejs", "php": "php", "python": "python"}
_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class RuntimeFinding:
    """Verdict on one runtime installation."""

    kind: str          # "node" | "php" | "python"
    version: str
    path: str
    manager: str       # "pkg:<name>", "brew", "nvm", "tarball", ...
    severity: int      # 0..4 (see osv.SEVERITY_NAME); 0 = fine / informational
    verdict: str       # "ok" | "eol" | "security-release-missing" | "behind-patch" | "distro" | "unchecked"
    detail: str
    upgrade_to: str = ""


def _cycle_of(kind: str, version: str) -> str:
    parts = version.lstrip("v").split(".")
    return parts[0] if kind == "node" else ".".join(parts[:2])


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
                       "latest": str(((r.get("latest") or {}).get("name")) or "")} for r in rels]
        except _http.FeedError:
            data = _http.fetch_json(f"https://endoflife.date/api/{product}.json")
            cycles = [{"cycle": str(r.get("cycle")), "eol": r.get("eol"), "is_eol": None,
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


def _security_releases(kind: str, cycle: str) -> list[str] | None:
    """Versions on ``cycle`` that upstream flags as security releases.

    Returns None when the flag data cannot be read or has an unexpected shape, so the caller
    treats "is the newer patch a security release?" as unknown rather than as "no".
    """
    key = f"sec:{kind}:{cycle}"
    if key in _CACHE:
        return _CACHE[key]  # type: ignore[return-value]
    out: list[str] | None = []
    try:
        if kind == "node":
            data = _http.fetch_json("https://nodejs.org/dist/index.json")
            if not isinstance(data, list):
                out = None
            else:
                out = [str(r.get("version", "")).lstrip("v") for r in data
                       if isinstance(r, dict) and r.get("security") and str(r.get("version", "")).lstrip("v").split(".")[0] == cycle]
        elif kind == "php":
            data = _http.fetch_json(f"https://www.php.net/releases/index.php?json&max=200&version={cycle.split('.')[0]}")
            if not isinstance(data, dict) or not data:
                out = None
            else:
                out = [v for v, info in data.items()
                       if re.match(r"^\d+\.\d+\.\d+$", v) and _cycle_of("php", v) == cycle
                       and isinstance(info, dict) and "security" in (info.get("tags") or [])]
        else:
            out = None   # python.org publishes no machine-readable security flag
    except _http.FeedError:
        out = None
    _CACHE[key] = out
    return out


def evaluate(kind: str, version: str, path: str = "", manager: str = "",
             today: _dt.date | None = None) -> RuntimeFinding:
    """Return the verdict for one runtime installation.

    Args:
        kind: ``"node"``, ``"php"`` or ``"python"``.
        version: Installed version, e.g. ``"24.22.0"`` or ``"v24.22.0"``.
        path: Binary path, for the report.
        manager: How it was installed (``pkg:<name>`` means the OS package manager).
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        A :class:`RuntimeFinding`. Feed failures produce verdict ``"unchecked"``.
    """
    version = version.strip().lstrip("v")
    today = today or _dt.date.today()
    if manager.startswith("pkg:"):
        return RuntimeFinding(kind, version, path, manager, 0, "distro",
                              "OS-packaged: fixes arrive as distro security updates (upstream version "
                              "numbers are not comparable) — keep OS security patching current")
    cycle = _cycle_of(kind, version)
    try:
        cycles = _eol_cycles(EOL_PRODUCT[kind])
        info = next((c for c in cycles if c["cycle"] == cycle), None)
        if info is None:
            return RuntimeFinding(kind, version, path, manager, 0, "unchecked", f"release line {cycle} unknown to endoflife.date")
        supported = [c for c in cycles if not _is_eol(c, today)]
        newest_supported = max((c["latest"] for c in supported if c["latest"]), key=version_key, default="")
        if _is_eol(info, today):
            return RuntimeFinding(kind, version, path, manager, 3, "eol",
                                  f"{kind} {cycle} is end-of-life — no more security fixes", newest_supported)
        latest = info["latest"]
        sec_list = _security_releases(kind, cycle)
        newer_sec = [v for v in (sec_list or []) if version_key(v) > version_key(version)]
        if newer_sec:
            need = latest if latest and version_key(latest) >= max(map(version_key, newer_sec)) else max(newer_sec, key=version_key)
            return RuntimeFinding(kind, version, path, manager, 3, "security-release-missing",
                                  f"missing security release(s) {', '.join(sorted(newer_sec, key=version_key))}", need)
        if latest and version_key(version) < version_key(latest):
            if sec_list is None and kind in ("node", "php"):
                return RuntimeFinding(kind, version, path, manager, 3, "behind-patch",
                                      f"behind latest {cycle}.x patch; security-release data unavailable, "
                                      "so assume the newer patch is a security release", latest)
            return RuntimeFinding(kind, version, path, manager, 2, "behind-patch",
                                  f"behind latest {cycle}.x patch", latest)
        return RuntimeFinding(kind, version, path, manager, 0, "ok", f"current on supported line {cycle}")
    except _http.FeedError as exc:
        return RuntimeFinding(kind, version, path, manager, 0, "unchecked", f"release data unavailable: {exc}")
