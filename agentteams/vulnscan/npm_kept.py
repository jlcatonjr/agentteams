"""``stage-major`` on a base with kept off-registry entries (vfr-c.C2, vfail F2's rule; ``@security`` 2026-11-02).

``stage-major`` runs ``npm install --package-lock-only --ignore-scripts``, which resolves every ``package.json``
spec. Before C2 it refused any base with an off-registry source (:func:`.manifests.npm_off_registry`), so portal,
whose lockfile installs SheetJS's ``xlsx`` from ``cdn.sheetjs.com``, could never take a major upgrade. C2 relaxes
that for one shape only, and checks the result:

- **Before** (:func:`base_problem`): an off-registry entry is allowed only when it is a top-level
  ``node_modules/<name>`` entry in ``packages``, resolves over ``https://`` with a ``sha512-`` integrity and a plain
  version, is not a link, and ``package.json`` lists ``<name>`` with exactly that URL as its spec. Every other
  non-registry source (git, ``file:``, ``link:``, ``npm:`` alias, a path, ``http://``, a nested or v1-only entry, an
  off-registry entry a registry range points at) is refused as before.
- **During**: npm runs in the tools container with registry-only egress (:func:`.subproc_confine.run_npm`:
  ``registry.npmjs.org:443`` only), lockfile-only and script-free, so npm cannot fetch the CDN tarball: if it
  tried, the install fails and nothing is staged.
- **After** (:func:`after_problem`): the re-resolved lockfile must hold exactly the same off-registry entries,
  each byte-identical in every field (``version``, ``resolved``, ``integrity``, ``dependencies`` and the rest) at the
  same key. A new, changed, moved or removed off-registry entry refuses the whole stage.

``executor.validate_diff`` then applies :func:`.manifests.check_lock_edit` as for every staged lockfile (the same
kept-entry rule), and the PR body lists each kept entry. What this newly permits (Rule 12): an unchanged CDN
entry beside a registry major upgrade; never a new off-registry source, nor a change to one.

Imports only :mod:`.manifests` (and the standard library).
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentteams.vulnscan import manifests

#: The sections of ``package.json`` whose specs ``npm install`` resolves.
_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_PREFIX = "node_modules/"


def _host(url: object) -> str:
    return re.sub(r"^https://([^/]+)/.*$", r"\1", url) if isinstance(url, str) and url.startswith("https://") else ""


def _off_registry(info: dict[str, Any]) -> bool:
    """Return whether a lockfile entry resolves somewhere other than the registry allowlist."""
    url = info.get("resolved")
    return url not in (None, "") and _host(url) not in manifests.REGISTRY_HOSTS


def _kept_shape(info: dict[str, Any]) -> bool:
    """The only off-registry entry C2 tolerates: https, a sha512 integrity, a plain version, not a link."""
    url, version, integrity = info.get("resolved"), info.get("version"), info.get("integrity")
    return (isinstance(url, str) and bool(_host(url)) and info.get("link") is not True
            and isinstance(version, str) and bool(re.fullmatch(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]*", version))
            and isinstance(integrity, str) and integrity.startswith("sha512-"))


def _registry_spec(spec: object) -> bool:
    return isinstance(spec, str) and not re.search(r"[:/]|^(file|link|git|npm|workspace|github|portal)\b",
                                                   spec.strip(), re.I)


def base_problem(rel: str, manifest_text: str, lock_text: str) -> tuple[str, frozenset[str]]:
    """Return why ``stage-major`` may not run ``npm install`` on this base, and the off-registry entries it keeps.

    Args:
        rel: The lockfile's path, for messages.
        manifest_text: The base ``package.json`` text.
        lock_text: The base lockfile text.

    Returns:
        ``(problem, kept)``: ``problem`` is "" when the base is registry-only apart from entries of the one kept
        shape (module docstring); ``kept`` holds those entries' full comparable forms (empty on a problem).
    """
    try:
        manifest = json.loads(manifest_text)
        _root, entries = manifests._walk_lock(rel, lock_text, unique=True)
    except ValueError as exc:
        return f"{rel}: package.json or the lockfile cannot be read ({str(exc)[:120]})", frozenset()
    if not isinstance(manifest, dict):
        return f"{rel}: package.json is not an object", frozenset()
    specs: dict[str, str] = {}
    for section in _SECTIONS:
        deps = manifest.get(section) or {}
        if not isinstance(deps, dict):
            return f"{rel}: package.json {section} is not an object", frozenset()
        for name, spec in deps.items():
            if _registry_spec(spec):
                continue
            if not (isinstance(spec, str) and _host(spec) and _host(spec) not in manifests.REGISTRY_HOSTS):
                return (f"{rel}: package.json {section}.{str(name)[:60]} is not a registry spec "
                        f"({str(spec)[:60]})"), frozenset()
            specs[str(name)] = spec
    kept: set[str] = set()
    urls: dict[str, str] = {}
    for section, name, info in entries:
        if not name:
            continue   # the root package itself
        version = info.get("version")
        if info.get("link") is True or (isinstance(version, str) and not _registry_spec(version)):
            return f"{rel}: {name[:80]} is not a registry package ({str(version or info.get('resolved'))[:80]})", \
                frozenset()
        url = info.get("resolved")
        if url not in (None, "") and not (isinstance(url, str) and url.startswith("https://")):
            return f"{rel}: {name[:80]} resolves outside the registry allowlist ({str(url)[:80]})", frozenset()
        if not _off_registry(info):
            continue
        pkg = name[len(_PREFIX):] if section == "packages" and name.startswith(_PREFIX) else ""
        if not (pkg and _PREFIX not in pkg and _kept_shape(info) and specs.get(pkg) == url):
            return (f"{rel}: {name[:80]} resolves outside the registry allowlist ({str(url)[:80]}) and is not a "
                    "top-level entry package.json names by that exact https URL with a sha512 integrity"), frozenset()
        kept.add(manifests._strict_key(section, name, info))
        urls[pkg] = str(url)
    if missing := sorted(set(specs) - set(urls)):
        return (f"{rel}: package.json names {missing[0][:60]} by URL but the lockfile has no matching entry "
                "(npm install would fetch it)"), frozenset()
    return "", frozenset(kept)


def after_problem(rel: str, kept: frozenset[str], lock_text: str) -> str:
    """Return why the re-resolved lockfile breaks C2's rule, or "" (every off-registry entry kept as it was).

    Args:
        rel: The lockfile's path, for messages.
        kept: :func:`base_problem`'s ``kept``.
        lock_text: The lockfile after ``npm install``.

    Returns:
        The problem: a malformed lockfile, an entry that is new, changed, moved or gone; or "".
    """
    try:
        _root, entries = manifests._walk_lock(rel, lock_text, unique=True)
    except ValueError as exc:
        return f"{rel}: the re-resolved lockfile cannot be read ({str(exc)[:120]})"
    now = {manifests._strict_key(section, name, info): name for section, name, info in entries
           if name and (_off_registry(info) or info.get("link") is True)}
    if added := sorted(name for key, name in now.items() if key not in kept):
        return (f"{rel}: npm install added or changed an off-registry entry ({added[0][:80]}); stage-major keeps "
                "off-registry entries only byte-identical to the base")
    if len(now) != len(kept):
        return f"{rel}: npm install removed an off-registry entry the base had; nothing is staged"
    return ""
