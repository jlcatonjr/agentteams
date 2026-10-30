"""Python requirements without exact pins: PEP 440 specifier sets as version intervals, and their judgement.

A ``requirements*.txt`` with only range specifiers, or a ``pyproject.toml`` with no lock, has no exact
versions (:mod:`.manifests` marks it *unscannable*). With ``unpinned_ranges`` on, :func:`engine.scan_path`
reads its direct requirements here and checks each **range** against the advisories OSV holds for the
package name (:func:`.osv.package_ids`, :func:`.osv.affected_intervals`), never one guessed version:

| Verdict | Meaning | Finding |
|---|---|---|
| ``whole`` | every version the specifier allows lies inside an affected range | at the advisory's severity |
| ``partial`` | some allowed versions are affected (usually the lower bound admits old releases) | capped at MODERATE |
| ``none`` | no allowed version is affected | none |

The specifier set is minimal PEP 440, built on :func:`.versions.version_key`: ``>=``, ``<=``, ``<``, ``>``,
``!=``, ``~=``, ``==`` and ``==x.*`` / ``!=x.*``, comma-combined, parenthesised or not. Pre-releases are
excluded unless a clause names one. A specifier it cannot read is *not judged* (a coverage note, never
clean). Requirements that are a URL, VCS, ``-e`` or ``===`` are counted as not checked. An environment
marker does not stop the judgement: the requirement is judged for every environment, and the finding says so.

Reading a file also decides whether its names may leave the machine (:attr:`RangeFile.private_index`): a
requirements file with an ``--index-url`` / ``-i`` / ``--extra-index-url`` line, itself or in a file it
includes with ``-r`` / ``-c``, and a ``pyproject.toml`` naming a package source or index
(``[[tool.poetry.source]]``, ``[[tool.uv.index]]``, ``[tool.uv.sources]`` with an ``index``,
``[[tool.pdm.source]]``) are withheld whole. A file over :data:`MAX_BYTES` (64 MiB) is not read.

Nothing here touches the network.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.vulnscan.versions import version_key

#: ``name[extras] spec ; marker`` (the spec may be parenthesised); options after `` --`` are stripped first.
_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*?)\s*(;.*)?$")
_CLAUSE = re.compile(r"^(~=|==|!=|<=|>=|<|>)\s*([0-9][A-Za-z0-9.!+_-]*(?:\.\*)?)$")
_RELEASE = re.compile(r"^(?:(\d+)!)?(\d+(?:\.\d+)*)")
_INDEX_OPT = re.compile(r"^(-i|--index-url|--extra-index-url)(\s|=|$)")
_INCLUDE_OPT = re.compile(r"^(-r|--requirement|-c|--constraint)(?:\s+|=)(\S+)")
_EDITABLE_OPT = re.compile(r"^(-e|--editable)(\s|=)")
_DEV_GROUP = re.compile(r"dev|test|lint|doc|type", re.I)
MAX_INCLUDE_DEPTH = 10
#: Largest requirements file or ``pyproject.toml`` read, as for the lockfile readers (``lockfiles.MAX_BYTES``).
MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Interval:
    """A contiguous set of versions; ``None`` bounds are unbounded."""

    lo: str | None = None
    lo_incl: bool = True
    hi: str | None = None
    hi_incl: bool = False
    fixed: bool = False   # ``hi`` is an advisory's ``fixed`` version (a version to raise the lower bound to)

    def empty(self) -> bool:
        """Return whether no version lies in the interval.

        Returns:
            True when the lower bound is above the upper, or equal with either end open.
        """
        if self.lo is None or self.hi is None:
            return False
        a, b = version_key(self.lo), version_key(self.hi)
        return a > b or (a == b and not (self.lo_incl and self.hi_incl))

    def text(self) -> str:
        """Return the interval as a specifier (``>=1.0, <1.4.2``; ``==1.2`` for one version; ``any`` unbounded).

        Returns:
            The text.
        """
        if self.lo is not None and self.lo == self.hi and self.lo_incl and self.hi_incl:
            return f"=={self.lo}"
        parts = []
        if self.lo is not None:
            parts.append((">=" if self.lo_incl else ">") + self.lo)
        if self.hi is not None:
            parts.append(("<=" if self.hi_incl else "<") + self.hi)
        return ", ".join(parts) or "any"


def _lower_max(a: Interval, b: Interval) -> tuple[str | None, bool]:
    if a.lo is None:
        return b.lo, b.lo_incl
    if b.lo is None:
        return a.lo, a.lo_incl
    ka, kb = version_key(a.lo), version_key(b.lo)
    if ka != kb:
        return (a.lo, a.lo_incl) if ka > kb else (b.lo, b.lo_incl)
    return a.lo, a.lo_incl and b.lo_incl


def _upper_min(a: Interval, b: Interval) -> tuple[str | None, bool, bool]:
    if a.hi is None:
        return b.hi, b.hi_incl, b.fixed
    if b.hi is None:
        return a.hi, a.hi_incl, a.fixed
    ka, kb = version_key(a.hi), version_key(b.hi)
    if ka != kb:
        return (a.hi, a.hi_incl, a.fixed) if ka < kb else (b.hi, b.hi_incl, b.fixed)
    return a.hi, a.hi_incl and b.hi_incl, a.fixed or b.fixed


def intersect(a: Interval, b: Interval) -> Interval:
    """Return the versions in both intervals (possibly :meth:`Interval.empty`).

    Args:
        a: An interval.
        b: Another interval.

    Returns:
        The intersection.
    """
    lo, lo_incl = _lower_max(a, b)
    hi, hi_incl, fixed = _upper_min(a, b)
    return Interval(lo, lo_incl, hi, hi_incl, fixed)


def _lo_at_or_below(outer: Interval, inner: Interval) -> bool:
    if outer.lo is None:
        return True
    if inner.lo is None:
        return False
    ko, ki = version_key(outer.lo), version_key(inner.lo)
    return ko < ki or (ko == ki and (outer.lo_incl or not inner.lo_incl))


def _hi_at_or_above(outer: Interval, inner: Interval) -> bool:
    if outer.hi is None:
        return True
    if inner.hi is None:
        return False
    ko, ki = version_key(outer.hi), version_key(inner.hi)
    return ko > ki or (ko == ki and (outer.hi_incl or not inner.hi_incl))


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Return the union of ``intervals`` as sorted, disjoint intervals (touching ones joined).

    Args:
        intervals: Intervals in any order.

    Returns:
        The union.
    """
    items = sorted((i for i in intervals if not i.empty()),
                   key=lambda i: (i.lo is not None, version_key(i.lo) if i.lo is not None else (), not i.lo_incl))
    out: list[Interval] = []
    for cur in items:
        if out:
            last = out[-1]
            joined = last.hi is None or cur.lo is None or version_key(cur.lo) < version_key(last.hi) or (
                version_key(cur.lo) == version_key(last.hi) and (cur.lo_incl or last.hi_incl))
            if joined:
                hi, hi_incl, fixed = (last.hi, last.hi_incl, last.fixed) if _hi_at_or_above(last, cur) \
                    else (cur.hi, cur.hi_incl, cur.fixed)
                out[-1] = Interval(last.lo, last.lo_incl, hi, hi_incl, fixed)
                continue
        out.append(cur)
    return out


def _is_pre(version: str) -> bool:
    """Return whether ``version`` is a pre-release or a dev release (PEP 440)."""
    key = version_key(version)
    return key[2] == 0 or "dev" in version.lower()


def _has_final(iv: Interval) -> bool:
    """Return whether the interval holds a final (non-pre-release) version."""
    if iv.empty():
        return False
    if iv.lo is None:
        return True
    m = _RELEASE.match(iv.lo.strip().lower().lstrip("v"))
    if not m:
        return True   # an unusual bound: never conclude "no version" from it
    epoch = f"{m.group(1)}!" if m.group(1) else ""
    base = epoch + m.group(2)
    lo = version_key(iv.lo)
    if not (version_key(base) > lo or (version_key(base) == lo and iv.lo_incl)):
        parts = m.group(2).split(".")
        base = epoch + ".".join(parts + ["0"] * max(0, 6 - len(parts)) + ["1"])   # a final just above base
    if iv.hi is None:
        return True
    kb, kh = version_key(base), version_key(iv.hi)
    return kb < kh or (kb == kh and iv.hi_incl)


def _prefix_interval(version: str) -> Interval:
    """Return the versions ``==version.*`` matches (``1.5.*``: ``>=1.5.dev0, <1.6.dev0``)."""
    head = version[:-2]
    m = _RELEASE.match(head)
    if not m or m.end() != len(head):
        raise ValueError(f"unreadable prefix match: {version}")
    epoch = f"{m.group(1)}!" if m.group(1) else ""
    parts = m.group(2).split(".")
    upper = parts[:-1] + [str(int(parts[-1][:64]) + 1)]
    return Interval(f"{epoch}{m.group(2)}.dev0", True, f"{epoch}{'.'.join(upper)}.dev0", False)


def _subtract(iv: Interval, hole: Interval) -> list[Interval]:
    """Return ``iv`` without ``hole``: up to two intervals."""
    if intersect(iv, hole).empty():
        return [iv]
    out = []
    if hole.lo is not None:
        out.append(intersect(iv, Interval(None, True, hole.lo, not hole.lo_incl)))
    if hole.hi is not None:
        out.append(intersect(iv, Interval(hole.hi, not hole.hi_incl, None, False)))
    return [i for i in out if not i.empty()]


@dataclass(frozen=True)
class SpecifierSet:
    """A parsed PEP 440 specifier set: the allowed versions as a union of intervals."""

    text: str                          # normalised: clauses joined with ","; "" allows every version
    intervals: tuple[Interval, ...]    # allowed versions (an empty tuple allows none)
    prereleases: bool = False          # a clause names a pre-release, so pre-releases are allowed

    def contains(self, version: str) -> bool:
        """Return whether ``version`` is allowed.

        Args:
            version: A version string.

        Returns:
            True when it lies in an allowed interval (and is not a pre-release, unless they are allowed).
        """
        if _is_pre(version) and not self.prereleases:
            return False
        point = Interval(version, True, version, True)
        return any(not intersect(i, point).empty() for i in self.intervals)


def parse_specifier(text: str) -> SpecifierSet:
    """Parse a specifier set such as ``>=1.0,<2,!=1.5.*`` (whitespace and one pair of parentheses allowed).

    Args:
        text: The specifier part of a requirement (``""`` allows every version).

    Returns:
        The :class:`SpecifierSet`.

    Raises:
        ValueError: An operator or version this module does not read (``===`` included: it is not judged).
    """
    body = text.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    clauses = [c.strip() for c in body.split(",")] if body else []
    allowed = [Interval()]
    pre = False
    norm = []
    for clause in clauses:
        m = _CLAUSE.match(clause)
        if not m:
            raise ValueError(f"unreadable specifier: {clause[:40]!r}")
        op, ver = m.groups()
        norm.append(op + ver)
        star = ver.endswith(".*")
        if star and op not in ("==", "!="):
            raise ValueError(f"a wildcard needs == or !=: {clause[:40]!r}")
        pre = pre or (not star and _is_pre(ver))
        if op == "!=":
            hole = _prefix_interval(ver) if star else Interval(ver, True, ver, True)
            allowed = [piece for iv in allowed for piece in _subtract(iv, hole)]
            continue
        if op == "~=":
            parts = _RELEASE.match(ver)
            release = parts.group(2).split(".") if parts else []
            if len(release) < 2:
                raise ValueError(f"~= needs at least two release segments: {clause[:40]!r}")
            epoch = f"{parts.group(1)}!" if parts and parts.group(1) else ""
            bounds = [Interval(ver, True, None, False), _prefix_interval(epoch + ".".join(release[:-1]) + ".*")]
        elif op == "==":
            bounds = [_prefix_interval(ver) if star else Interval(ver, True, ver, True)]
        else:
            bounds = [{">=": Interval(ver, True, None, False), ">": Interval(ver, False, None, False),
                       "<=": Interval(None, True, ver, True), "<": Interval(None, True, ver, False)}[op]]
        for bound in bounds:
            allowed = [i for i in (intersect(iv, bound) for iv in allowed) if not i.empty()]
    return SpecifierSet(",".join(norm), tuple(merge(allowed)), pre)


@dataclass(frozen=True)
class Judgement:
    """How one specifier set meets one advisory's affected ranges."""

    verdict: str                                  # "whole" | "partial" | "none"
    overlap: tuple[Interval, ...] = ()            # allowed versions that are affected
    raise_to: str = ""                            # partial: lower bound that clears this advisory ("" = none known)


def judge(spec: SpecifierSet, affected: Iterable[Interval]) -> Judgement:
    """Intersect the allowed versions with an advisory's affected intervals.

    Args:
        spec: The requirement's specifier set.
        affected: The advisory's affected intervals (:func:`.osv.affected_intervals`).

    Returns:
        ``whole`` when every allowed version is affected, ``partial`` when some are, ``none`` otherwise. A
        partial judgement's ``raise_to`` is the highest ``fixed`` version among the overlapping ranges, when
        every one of them ends in a ``fixed`` event.
    """
    aff = [a for a in affected if not a.empty()]
    usable = [a for a in spec.intervals if spec.prereleases or _has_final(a)]
    pairs = [(a, intersect(a, b), b) for a in usable for b in aff]
    hits = [(o, b) for _a, o, b in pairs if not o.empty() and (spec.prereleases or _has_final(o))]
    if not hits:
        return Judgement("none")
    overlap = tuple(merge(o for o, _b in hits))
    union = merge(aff)
    if all(any(_lo_at_or_below(u, a) and _hi_at_or_above(u, a) for u in union) for a in usable):
        return Judgement("whole", overlap)
    ends = [b for _o, b in hits]
    raise_to = ""
    if all(b.fixed and b.hi is not None for b in ends):
        raise_to = max((b.hi for b in ends if b.hi is not None), key=version_key)
    return Judgement("partial", overlap, raise_to)


@dataclass(frozen=True)
class Requirement:
    """One direct requirement whose range can be judged."""

    name: str
    spec: SpecifierSet
    scope: str = "runtime"   # "runtime" | "dev"
    marker: bool = False     # it had an environment marker (judged for every environment)


@dataclass
class RangeFile:
    """What :func:`read_requirements` / :func:`read_pyproject` found in one manifest."""

    path: Path
    reqs: list[Requirement] = field(default_factory=list)
    unchecked: int = 0          # URL, VCS, editable or === requirements: not checked
    unreadable: int = 0         # specifiers this module cannot read: not judged
    private_index: bool = False  # it names a package index or source: nothing in it leaves the machine


def parse_requirement(line: str, scope: str = "runtime") -> Requirement | str | None:
    """Parse one requirement (a requirements-file line or a PEP 621 dependency string).

    Args:
        line: The requirement, comment already removed.
        scope: ``runtime`` or ``dev``.

    Returns:
        A :class:`Requirement`; ``"unchecked"`` for a URL, VCS, editable or ``===`` requirement; ``"unreadable"``
        for one whose specifier cannot be read; None for an empty line or an option.
    """
    text = line.strip()
    if not text:
        return None
    if _EDITABLE_OPT.match(text) or text.startswith(("git+", "hg+", "svn+", "bzr+", "http:", "https:", "file:")):
        return "unchecked"
    if text.startswith("-"):
        return None
    text = re.split(r"\s--?[A-Za-z]", " " + text, maxsplit=1)[0].strip() if " -" in text else text
    m = _REQ.match(text)
    if not m:
        return "unreadable"
    name, _extras, spec, marker = m.groups()
    if spec.startswith("@") or "===" in spec:
        return "unchecked"
    try:
        parsed = parse_specifier(spec)
    except ValueError:
        return "unreadable"
    return Requirement(name, parsed, scope, bool(marker))


def _logical_lines(text: str) -> list[str]:
    """Return the file's lines with ``\\`` continuations joined and `` #`` comments removed."""
    out, buf = [], ""
    for raw in text.splitlines():
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
            continue
        full = buf + raw
        buf = ""
        line = full.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            out.append(line)
    if buf.strip():
        out.append(buf.split(" #", 1)[0].strip())
    return out


def _read_text(path: Path, errors: str = "replace") -> str:
    """Return a file's text, refusing one over :data:`MAX_BYTES` before reading it.

    Raises:
        OSError: It cannot be read.
        ValueError: It is larger than :data:`MAX_BYTES`.
    """
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"larger than {MAX_BYTES} bytes: not read")
    return path.read_text(encoding="utf-8", errors=errors)


def _names_index(path: Path, repo: Path, seen: set[str], depth: int = 0) -> bool:
    """Return whether ``path``, or a file it includes with ``-r`` / ``-c``, has an index option line.

    An include that cannot be read, leaves ``repo`` or goes deeper than :data:`MAX_INCLUDE_DEPTH` counts as
    naming an index: an include that cannot be checked never lets names leave the machine.
    """
    real = os.path.realpath(path)
    if real in seen:
        return False
    seen.add(real)
    try:
        text = _read_text(path)
    except (OSError, ValueError):   # unreadable or too large: it cannot be checked, so it counts as naming one
        return True
    root = os.path.realpath(repo)
    for line in _logical_lines(text):
        if _INDEX_OPT.match(line):
            return True
        inc = _INCLUDE_OPT.match(line)
        if inc:
            target = (path.parent / inc.group(2)).resolve()
            if depth >= MAX_INCLUDE_DEPTH or not (str(target) == root or str(target).startswith(root + os.sep)):
                return True
            if _names_index(target, repo, seen, depth + 1):
                return True
    return False


def read_requirements(path: Path, repo: Path) -> RangeFile:
    """Read the direct requirements of a requirements file that has no exact pins.

    Args:
        path: The requirements file.
        repo: Its repository: ``-r`` / ``-c`` includes are followed only inside it.

    Returns:
        The :class:`RangeFile`; a file named like ``dev`` or ``test`` gives ``dev`` requirements. When
        ``private_index`` is set, ``reqs`` is empty (nothing of it is sent).

    Raises:
        OSError: The file cannot be read.
        ValueError: It is larger than :data:`MAX_BYTES`.
    """
    rf = RangeFile(path)
    text = _read_text(path)
    if _names_index(path, repo, set()):
        rf.private_index = True
        return rf
    low = path.name.lower()
    scope = "dev" if "dev" in low or "test" in low else "runtime"
    for line in _logical_lines(text):
        got = parse_requirement(line, scope)
        if isinstance(got, Requirement):
            rf.reqs.append(got)
        elif got == "unchecked":
            rf.unchecked += 1
        elif got == "unreadable":
            rf.unreadable += 1
    return rf


def _pyproject_names_index(data: dict) -> bool:
    """Return whether a ``pyproject.toml`` names a package source or index (its names then stay local)."""
    tool = data.get("tool")
    tool = tool if isinstance(tool, dict) else {}
    poetry, uv, pdm = (tool.get(k) if isinstance(tool.get(k), dict) else {} for k in ("poetry", "uv", "pdm"))
    if poetry.get("source") or uv.get("index") or pdm.get("source"):
        return True
    sources = uv.get("sources")
    if isinstance(sources, dict):
        for spec in sources.values():
            for item in spec if isinstance(spec, list) else [spec]:
                if isinstance(item, dict) and "index" in item:
                    return True
    return False


def read_pyproject(path: Path, skip: Iterable[str] = ()) -> RangeFile:
    """Read the PEP 621 dependencies of a ``pyproject.toml`` that has no lock.

    Args:
        path: The ``pyproject.toml``.
        skip: Names (any spelling) already scanned as exact pins, by the file itself or by the requirements
            files alongside: they are not judged again.

    Returns:
        The :class:`RangeFile`: ``[project].dependencies`` are runtime, ``optional-dependencies`` groups named
        like dev, test, lint, doc or type are dev. When ``private_index`` is set, ``reqs`` is empty.

    Raises:
        OSError: The file cannot be read.
        tomllib.TOMLDecodeError: It is not TOML.
        ValueError: It is larger than :data:`MAX_BYTES`.
    """
    rf = RangeFile(path)
    data = tomllib.loads(_read_text(path, errors="strict"))
    if _pyproject_names_index(data):
        rf.private_index = True
        return rf
    done = {re.sub(r"[-_.]+", "-", n).lower() for n in skip}
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    deps = project.get("dependencies") or []
    specs = [(s, "runtime") for s in deps] if isinstance(deps, list) else [(deps, "runtime")]
    groups = project.get("optional-dependencies")
    for group, items in (groups.items() if isinstance(groups, dict) else []):
        scope = "dev" if _DEV_GROUP.search(str(group)) else "runtime"
        specs += [(s, scope) for s in items] if isinstance(items, list) else [(items, scope)] if items else []
    for spec, scope in specs:
        got = parse_requirement(spec, scope) if isinstance(spec, str) else "unreadable"
        if isinstance(got, Requirement):
            if re.sub(r"[-_.]+", "-", got.name).lower() not in done:
                rf.reqs.append(got)
        elif got == "unchecked":
            rf.unchecked += 1
        elif got == "unreadable":
            rf.unreadable += 1
    return rf
