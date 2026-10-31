"""``stage-major`` for npm: raise a direct dependency's ``package.json`` spec to the fixed major line.

``npm audit fix`` without ``--force`` never leaves a dependency's caret range, so a fix that needs a semver-major
upgrade stays ``major-held`` under ``stage-fix``. Under ``stage-major`` (a grant must name it explicitly, Rule 15)
:func:`agentteams.vulnscan.fix.fix_path` takes such a package when it is a **direct** dependency: its spec in
``package.json`` (``dependencies`` or ``devDependencies``, wherever it already is) becomes ``^<fixed version>`` and
npm re-resolves the lockfile with ``npm install --package-lock-only --ignore-scripts``. A package that is only a
transitive dependency stays held (its parent must move first, a person's change), and npm workspaces stay refused.

This module holds the pure text side: a formatting-preserving spec rewrite (:func:`rewrite_specs`) and the
executor's check of the result (:func:`check_package_json_edit`, called by ``executor.validate_diff``). It never
runs npm and imports nothing from the rest of the package.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: The sections a direct dependency is rewritten in (an optional or peer dependency is left for a person).
DEP_SECTIONS = ("dependencies", "devDependencies")
#: A ``package.json`` path ``executor.validate_diff`` accepts, under ``stage-major`` only.
PACKAGE_JSON = re.compile(r"(^|/)package\.json$")
_SPEC = re.compile(r"^\^(\d+)\.(\d+)\.(\d+)$")   # the only spec stage-major writes
_COLON = re.compile(r"\s*:\s*")


def _string_end(text: str, i: int) -> int:
    """Return the index just past the JSON string starting at ``text[i]`` (a ``"``)."""
    k = i + 1
    while k < len(text):
        if text[k] == "\\":
            k += 2
            continue
        if text[k] == '"':
            return k + 1
        k += 1
    raise ValueError("unterminated string")


def _value_end(text: str, i: int) -> int:
    """Return the index just past the JSON object or string starting at ``text[i]``."""
    if text[i] == '"':
        return _string_end(text, i)
    if text[i] != "{":
        raise ValueError("not an object or a string")
    depth, k = 0, i
    while k < len(text):
        ch = text[k]
        if ch == '"':
            k = _string_end(text, k)
            continue
        depth += 1 if ch in "{[" else -1 if ch in "}]" else 0
        k += 1
        if depth == 0:
            return k
    raise ValueError("unterminated object")


def _member(text: str, lo: int, key: str) -> tuple[int, int]:
    """Return the span of member ``key``'s value in the object starting at ``text[lo]``.

    Raises:
        ValueError: No such member, or its value is neither an object nor a string.
    """
    hi, depth, k = _value_end(text, lo), 0, lo
    while k < hi:
        ch = text[k]
        if ch == '"':
            end = _string_end(text, k)
            colon = _COLON.match(text, end)
            if depth == 1 and colon and json.loads(text[k:end]) == key:
                return colon.end(), _value_end(text, colon.end())
            k = end
            continue
        depth += 1 if ch in "{[" else -1 if ch in "}]" else 0
        k += 1
    raise ValueError(f"{key!r} not found")


def direct_section(manifest: dict[str, Any], name: str) -> str:
    """Return the :data:`DEP_SECTIONS` section of ``manifest`` that lists ``name`` with a registry spec, or "".

    A spec that is a URL, a path, a git or an ``npm:`` alias is never rewritten ("").

    Args:
        manifest: Parsed ``package.json``.
        name: npm package name.

    Returns:
        The section name, or "".

    Raises:
        TypeError: ``manifest`` is not a dict or ``name`` not a string.
    """
    if not isinstance(manifest, dict) or not isinstance(name, str):
        raise TypeError("direct_section takes a dict manifest and a string name")
    for section in DEP_SECTIONS:
        deps = manifest.get(section)
        spec = deps.get(name) if isinstance(deps, dict) else None
        if isinstance(spec, str):
            return section if re.search(r"\d", spec) and not re.search(r"[:/]", spec) else ""
    return ""


def rewrite_specs(text: str, specs: dict[str, tuple[str, str]]) -> str:
    """Rewrite dependency specs in ``package.json`` text, leaving every other byte as it was.

    Args:
        text: ``package.json`` text.
        specs: ``{name: (section, new spec)}``.

    Returns:
        The new text.

    Raises:
        TypeError: ``text`` is not a string or ``specs`` not a ``{str: (str, str)}`` dict.
        ValueError: The text is not a JSON object, a member is not where :func:`direct_section` put it, a new spec is
            not ``^X.Y.Z``, or the result does not parse to exactly the expected manifest.
    """
    if not isinstance(text, str) or not isinstance(specs, dict) or not all(
            isinstance(k, str) and isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, str) for x in v)
            for k, v in specs.items()):
        raise TypeError("rewrite_specs takes a string and a {name: (section, spec)} dict of strings")
    for name, (section, spec) in specs.items():
        if section not in DEP_SECTIONS or not _SPEC.match(spec):
            raise ValueError(f"{name}: only a {'/'.join(DEP_SECTIONS)} spec of the form ^X.Y.Z is written")
    expected = json.loads(text)
    if not isinstance(expected, dict):
        raise ValueError("package.json is not an object")
    start = text.index("{")
    for name, (section, spec) in specs.items():
        lo, _ = _member(text, start, section)
        v_lo, v_hi = _member(text, lo, name)
        text = text[:v_lo] + json.dumps(spec) + text[v_hi:]
        expected[section][name] = spec
    if json.loads(text) != expected:
        raise ValueError("package.json rewrite did not hold (duplicate keys?)")
    return text


def _numbers(spec: str) -> tuple[int, ...]:
    nums = [int(n) for n in re.findall(r"\d+", spec)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def check_package_json_edit(rel: str, old: bytes, new: bytes) -> None:
    """Refuse a ``package.json`` edit that is anything but raising direct dependency specs to ``^X.Y.Z``.

    Every top-level member other than :data:`DEP_SECTIONS` must be unchanged, and so must each section's set of
    names. A changed spec must have been a plain registry range (no URL, path, git or alias) and become
    ``^X.Y.Z`` above it. A new ``package.json`` is refused.

    Args:
        rel: Path for messages.
        old: File bytes at the base commit (``b""`` for a new file).
        new: File bytes now.

    Raises:
        ValueError: The edit is anything else.
    """
    try:
        a, b = json.loads(old.decode("utf-8")), json.loads(new.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{rel}: package.json is new or not valid JSON: {exc}") from exc
    if not (isinstance(a, dict) and isinstance(b, dict)):
        raise ValueError(f"{rel}: package.json is not an object")
    if {k: v for k, v in a.items() if k not in DEP_SECTIONS} != {k: v for k, v in b.items() if k not in DEP_SECTIONS}:
        raise ValueError(f"{rel}: a package.json member other than {'/'.join(DEP_SECTIONS)} changed")
    for section in DEP_SECTIONS:
        x, y = a.get(section), b.get(section)
        if x == y:
            continue
        if not (isinstance(x, dict) and isinstance(y, dict)) or set(x) != set(y):
            raise ValueError(f"{rel}: {section}: a dependency was added or removed")
        for name in x:
            if x[name] == y[name]:
                continue
            was, now = x[name], y[name]
            m = _SPEC.match(now) if isinstance(now, str) else None
            if not (isinstance(was, str) and m and direct_section({section: {name: was}}, name)) or \
                    tuple(map(int, m.groups())) <= _numbers(was):
                raise ValueError(f"{rel}: {section}.{name}: {str(was)[:40]} -> {str(now)[:40]} is not a raise "
                                 "to ^X.Y.Z")


def changed_manifest(wt: Path, lock_rel: str, changed: list[str]) -> dict[str, Any] | None:
    """Return the parsed ``package.json`` beside ``lock_rel`` when this change edits it, else None.

    ``executor.validate_diff`` passes it to :func:`agentteams.vulnscan.manifests.check_lock_edit`: a lockfile's root
    dependencies may change only to mirror a ``package.json`` the same change edits (and that
    :func:`check_package_json_edit` passes).

    Args:
        wt: Worktree.
        lock_rel: The lockfile's path in the worktree.
        changed: Every changed path.

    Returns:
        The manifest, or None.
    """
    rel = f"{lock_rel.rsplit('/', 1)[0]}/package.json" if "/" in lock_rel else "package.json"
    if rel not in changed:
        return None
    try:
        data = json.loads((wt / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):   # unreadable: no root change is allowed
        return None
    return data if isinstance(data, dict) else None
