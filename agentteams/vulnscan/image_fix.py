"""``stage-image``: move an end-of-life official runtime image's Dockerfile ``FROM`` tag to the newest supported line.

A constraint-relaxing op of its own (Rule 15): only a grant that names ``stage-image`` covers it, and no grant that
lists ``stage-fix`` or ``stage-major`` implies it. The agent names only the repository; this module decides every
edit from the scan's own data (:func:`.images.refs`, :data:`.images.IMAGE_PRODUCTS` and the endoflife.date release
data :func:`.runtimes.evaluate` reads).

:func:`plan_file` stages a reference only when **all** of these hold:

- it is a ``FROM`` of a Dockerfile (``Dockerfile*``, ``*.Dockerfile``, ``Containerfile``; never a compose file),
  written on one physical line, with no variable and no ``ARG`` default;
- it names a Docker Hub official image in :data:`ELIGIBLE` (language runtimes whose newest supported line is the
  right target: ``python``, ``ruby``, ``golang``, ``php``). Databases (an on-disk format change), ``node`` and
  ``eclipse-temurin`` (the newest line may be a non-LTS one) and OS images are left to a person;
- it has a version tag (never ``:latest`` or no tag: report-only), no ``@sha256:`` digest (never changed, never
  added), and a variant suffix of letters-only tokens that names no OS release (``-slim``, ``-alpine``, ``-fpm``;
  not ``-bookworm`` or ``-alpine3.19``, which the newer line may not publish);
- its release line is end-of-life. The new tag is the newest supported line with the **same** suffix, and the same
  pinning granularity: a line tag stays a line tag (``3.9-slim`` -> ``3.15-slim``), a full-patch tag becomes the
  newest line's latest release (``3.9.18-slim`` -> ``3.15.0-slim``).

Every other image finding is held with its reason (``image-held``). :func:`check_from_edit` is the executor's
offline, byte-level check of a staged change: only those tag edits, upward by a release line.

Imports :mod:`.fix` (which imports this module) only inside :func:`fix_result` and :func:`plan_rows`.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agentteams.vulnscan import images, manifests, project_policy, runtimes
from agentteams.vulnscan.versions import version_key

if TYPE_CHECKING:
    from agentteams.vulnscan.fix import FixResult, PinChange
    from agentteams.vulnscan.report import Finding

#: Official image -> :data:`runtimes.PRODUCTS` kind that ``stage-image`` may move to a new release line.
ELIGIBLE: dict[str, str] = {"python": "python", "ruby": "ruby", "golang": "go", "php": "php"}
#: A variant suffix ``stage-image`` keeps: letters-only ``-token`` runs (never a versioned OS such as ``alpine3.19``).
_VARIANT = re.compile(r"(?:-[a-z]+)*")
_NUMERIC = re.compile(r"\d+(?:\.\d+)*")
#: One physical ``FROM`` line: (prefix incl. options, the image reference, the rest).
_FROM_LINE = re.compile(r"^(\s*FROM\s+(?:--\S+\s+)*)(\S+)(.*?)(\r?\n)?$", re.IGNORECASE | re.DOTALL)
HELD = "image-held"


@dataclass(frozen=True)
class Bump:
    """One ``FROM`` tag change ``stage-image`` makes."""

    path: str      # scan-root relative
    line: int      # 1-based
    image: str     # the official image name (``python``)
    old_tag: str
    new_tag: str


@dataclass
class ImagePlan:
    """What :func:`plan_tree` decided for a working tree."""

    bumps: list[Bump] = field(default_factory=list)
    held: list[tuple[str, int, str, str, str]] = field(default_factory=list)   # (path, line, image, tag, reason)
    feed_errors: list[str] = field(default_factory=list)


def is_dockerfile(name: str) -> bool:
    """Return whether a file name is a Dockerfile or Containerfile (a container manifest that is not compose YAML).

    Args:
        name: File name (a path's last part is used).

    Returns:
        True for ``Dockerfile*``, ``*.Dockerfile`` and ``Containerfile``.
    """
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    return images.is_container_manifest(base) and not re.search(r"\.ya?ml$", base, re.IGNORECASE)


def _shape_problem(ref: images.ImageRef) -> str:
    """Why ``ref`` is outside what ``stage-image`` may change (judged on the reference alone), or ""."""
    if ref.source != "FROM":
        return "compose image: stage-image edits Dockerfile FROM lines only"
    if ref.official not in ELIGIBLE:
        return "not an official runtime image stage-image moves (python, ruby, golang, php)"
    if ref.tag in ("", "latest"):
        return "unpinned tag: report-only (stage-image never picks a line or a digest for :latest)"
    if ref.digest:
        return "pinned by digest: stage-image never changes a digest-pinned reference"
    if ref.arg_default:
        return "set by an ARG default: change the ARG by hand"
    if images.tag_os(ref.tag):
        return "the tag names an OS release: the new line may not be published for it"
    version, suffix = images.split_tag(ref.tag)
    if not version or ref.tag.startswith("v") or not _VARIANT.fullmatch(suffix):
        return "tag variant not handled by stage-image"
    product = runtimes.PRODUCTS[ELIGIBLE[ref.official]]
    if product.cycle_fn is not None or len(version.split(".")) < product.cycle:
        return "floating tag: release line not determinable"
    return ""


def new_tag(ref: images.ImageRef, today: _dt.date | None = None) -> tuple[str, str]:
    """Return the tag ``stage-image`` moves ``ref`` to.

    Args:
        ref: A reference :func:`.images.refs` read.
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        ``(tag, "")`` to stage; ``("", reason)`` to hold; ``("", "")`` when there is nothing to move (the line is
        supported, or the reference is not an end-of-life runtime line at all).

    Raises:
        LookupError: The release data is unavailable (the message says why); nothing is guessed.
    """
    if ref.source != "FROM" or ref.official not in ELIGIBLE:
        return "", ""   # not a runtime line stage-image moves: the fix plan's plan-only row stands
    version, suffix = images.split_tag(ref.tag)
    if ref.tag in ("", "latest") or not version:
        return "", _shape_problem(ref) or "tag without a version"
    kind = ELIGIBLE[ref.official]
    v = runtimes.evaluate(kind, version, manager="image", today=today)
    if v.verdict == "unchecked":
        if v.detail.startswith("release data unavailable"):
            raise LookupError(f"{kind} {version}: {v.detail}")
        return "", ""
    if v.verdict != "eol":
        return "", ""
    problem = _shape_problem(ref)
    if problem:
        return "", problem
    if not v.upgrade_to or not _NUMERIC.fullmatch(v.upgrade_to):
        return "", "no supported release line known"
    parts = len(version.split("."))
    line = runtimes.cycle_of(kind, v.upgrade_to)
    target = line if parts <= len(line.split(".")) else v.upgrade_to
    if len(target.split(".")) != parts or version_key(target) <= version_key(version):
        return "", "no newer supported tag of the same form"
    return target + suffix, ""


def plan_file(path: Path, rel: str, today: _dt.date | None = None) -> ImagePlan:
    """Plan the ``FROM`` tag changes of one Dockerfile.

    Args:
        path: The Dockerfile.
        rel: Its path for the plan (scan-root relative).
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        An :class:`ImagePlan`: bumps, held references (with the reason, the image shown only by
        :meth:`.images.ImageRef.display`), and feed errors. This function does not raise for bad input.
    """
    out = ImagePlan()
    if not is_dockerfile(path.name):
        return out
    found, _notes = images.refs(path)
    try:
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return out
    for ref in found:
        try:
            tag, why = new_tag(ref, today)
        except LookupError as exc:
            out.feed_errors.append(f"{rel}:{ref.line}: {exc}")
            continue
        if not tag and why:
            out.held.append((rel, ref.line, ref.display(), ref.tag or "(no tag)", why))
            continue
        if not tag:
            continue
        m = _FROM_LINE.match(lines[ref.line - 1]) if 0 < ref.line <= len(lines) else None
        parsed = images.parse_ref(m.group(2), ref.line, "FROM") if m else None
        if m is None or parsed is None or lines[ref.line - 1].rstrip().endswith("\\") or \
                (parsed.registry, parsed.repository, parsed.tag, parsed.digest) != \
                (ref.registry, ref.repository, ref.tag, ref.digest):
            out.held.append((rel, ref.line, ref.display(), ref.tag, "FROM spans several lines: change it by hand"))
            continue
        out.bumps.append(Bump(rel, ref.line, str(ref.official), ref.tag, tag))
    return out


def plan_tree(root: Path, today: _dt.date | None = None) -> ImagePlan:
    """Plan every Dockerfile under a working tree (``manifests.discover``; the brief's ``exclude_paths`` honoured).

    Args:
        root: The executor's worktree (a repository root).
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        The combined :class:`ImagePlan`. A brief with ``level: off`` plans nothing.
    """
    root = root.resolve()
    out = ImagePlan()
    pol = project_policy.read_policy(root)
    if pol.level == "off":
        return out
    for path in manifests.discover(root):
        rel = path.relative_to(root).as_posix()
        if not is_dockerfile(path.name) or path.is_symlink() or \
                (pol.exclude_paths and project_policy.is_excluded(rel, pol.exclude_paths)):
            continue
        one = plan_file(path, rel, today)
        out.bumps += one.bumps
        out.held += one.held
        out.feed_errors += one.feed_errors
    return out


def apply(root: Path, bumps: list[Bump]) -> None:
    """Write the planned tag changes (only the reference's tag on each ``FROM`` line; every other byte kept).

    Args:
        root: The working tree the bumps' paths are relative to.
        bumps: :attr:`ImagePlan.bumps`.

    Raises:
        ValueError: A bump no longer matches its line (the file changed since it was planned).
        OSError: A file could not be read or written.
    """
    by_file: dict[str, list[Bump]] = {}
    for b in bumps:
        by_file.setdefault(b.path, []).append(b)
    for rel, group in by_file.items():
        path = root / rel
        if path.is_symlink():
            raise ValueError(f"{rel}: a symlink: never edited")
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
        for b in group:
            m = _FROM_LINE.match(lines[b.line - 1]) if 0 < b.line <= len(lines) else None
            if m is None or not m.group(2).endswith(":" + b.old_tag):
                raise ValueError(f"{rel}:{b.line}: FROM line changed since it was planned")
            token = m.group(2)[: -len(b.old_tag)] + b.new_tag
            lines[b.line - 1] = m.group(1) + token + m.group(3) + (m.group(4) or "")
        path.write_bytes("".join(lines).encode("utf-8"))


def check_from_edit(rel: str, old: bytes, new: bytes) -> None:
    """Check, offline and byte for byte, that a staged Dockerfile change is only ``stage-image``'s tag moves.

    Every differing line must be a one-line ``FROM`` whose image reference differs only in its tag. The image must
    be an :data:`ELIGIBLE` official image with no digest and no variable, the variant suffix must be unchanged,
    letters-only and OS-free, and the version must have the same number of parts and a higher release line.
    Everything else on the line, and every other line (count and endings included), must be unchanged.

    Args:
        rel: The changed path.
        old: Its bytes at the base commit (``b""`` when new: refused).
        new: Its staged bytes.

    Raises:
        ValueError: Any other change.
    """
    if not is_dockerfile(rel):
        raise ValueError(f"refusing change outside Dockerfile FROM tags: {rel}")
    if not old:
        raise ValueError(f"refusing {rel}: a new file")
    try:
        before = old.decode("utf-8").splitlines(keepends=True)
        after = new.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise ValueError(f"refusing {rel}: not UTF-8 text") from exc
    if len(before) != len(after):
        raise ValueError(f"refusing {rel}: lines added or removed")
    changed = 0
    for n, (a, b) in enumerate(zip(before, after), 1):
        if a == b:
            continue
        changed += 1
        ma, mb = _FROM_LINE.match(a), _FROM_LINE.match(b)
        if not ma or not mb or (ma.group(1), ma.group(3), ma.group(4)) != (mb.group(1), mb.group(3), mb.group(4)) \
                or a.rstrip().endswith("\\"):
            raise ValueError(f"refusing {rel}:{n}: only a FROM line's image tag may change")
        _check_ref_move(rel, n, ma.group(2), mb.group(2))
    if not changed:
        raise ValueError(f"refusing {rel}: no FROM tag changed")


def _check_ref_move(rel: str, n: int, old_text: str, new_text: str) -> None:
    if "$" in old_text or "$" in new_text:
        raise ValueError(f"refusing {rel}:{n}: an image reference with a variable")
    if "@" in old_text or "@" in new_text:
        raise ValueError(f"refusing {rel}:{n}: a digest-pinned reference (never changed, never added)")
    o, w = images.parse_ref(old_text, n, "FROM"), images.parse_ref(new_text, n, "FROM")
    if o is None or w is None or (o.registry, o.repository) != (w.registry, w.repository) or \
            old_text.rsplit(":", 1)[0] != new_text.rsplit(":", 1)[0]:
        raise ValueError(f"refusing {rel}:{n}: the image itself changed (registry or repository)")
    if o.official not in ELIGIBLE:
        raise ValueError(f"refusing {rel}:{n}: not an official runtime image stage-image moves")
    (ov, osuf), (nv, nsuf) = images.split_tag(o.tag), images.split_tag(w.tag)
    if not ov or not nv or o.tag.startswith("v") or w.tag.startswith("v") or osuf != nsuf or \
            not _VARIANT.fullmatch(osuf) or images.tag_os(o.tag) or images.tag_os(w.tag):
        raise ValueError(f"refusing {rel}:{n}: the tag variant changed or is not handled ({o.tag} -> {w.tag})")
    kind = ELIGIBLE[o.official]
    if len(ov.split(".")) != len(nv.split(".")) or \
            version_key(runtimes.cycle_of(kind, nv)) <= version_key(runtimes.cycle_of(kind, ov)):
        raise ValueError(f"refusing {rel}:{n}: not an upgrade to a newer release line ({o.tag} -> {w.tag})")


def fix_result(wt: Path, claims: dict[str, list[int]]) -> FixResult:
    """Make ``stage-image``'s change in the executor's worktree and describe it as a :class:`.fix.FixResult`.

    Rows: ``fix`` (made, ``major`` set), ``pr-held`` (another open PR already updates that image, D1) and
    :data:`HELD`. Nothing is written when a feed failed.

    Args:
        wt: The executor's worktree.
        claims: ``{normalized name: [PR numbers]}`` other open PRs update (:func:`.open_prs.staging_claims`).

    Returns:
        The result; ``resolved`` is the number of tags moved.

    Raises:
        ValueError: A planned line changed before it was written (:func:`apply`).
        OSError: A Dockerfile could not be read or written.
    """
    from agentteams.vulnscan import fix, open_prs  # lazy: fix imports this module

    plan = plan_tree(wt)
    changes: list[PinChange] = []
    take: list[Bump] = []
    for b in plan.bumps:
        held = open_prs.normalize(b.image) in claims
        changes.append(fix.PinChange(b.path, b.image, b.old_tag, b.new_tag, "pr-held" if held else "fix",
                                     note=f"FROM line {b.line}", major=not held))
        take += [] if held else [b]
    changes += [fix.PinChange(p, name, tag, "", HELD, note=f"line {line}: {why}") for p, line, name, tag, why in plan.held]
    if not plan.feed_errors:
        apply(wt, take)
    return fix.FixResult(changes=changes, applied=True, resolved=len(take), feed_errors=list(plan.feed_errors))


def plan_rows(path: Path, rel: str, found: list[Finding], errors: list[str]) -> list[PinChange]:
    """Return a dry-run fix plan's rows for one container manifest's image findings (:func:`.images.plan`).

    Args:
        path: The container manifest.
        rel: Its path for the plan.
        found: Its image findings.
        errors: Its feed errors (any: every row stays ``plan-only``).

    Returns:
        ``stage-image`` rows where :func:`plan_file` would move the tag (its target under "→"), else ``plan-only``
        rows, noting why ``stage-image`` holds one when it does.
    """
    from agentteams.vulnscan.fix import PinChange  # lazy: fix imports this module

    staged = plan_file(path, rel) if found and not errors else ImagePlan()
    moves = {b.line: b for b in staged.bumps}
    held = {line: why for _, line, _, _, why in staged.held}
    rows = []
    for f in found:
        lines = [int(n) for loc in f.locations if (n := loc.rsplit(":", 1)[-1]).isdigit()]
        note = f"{f.detail} (line " + ", ".join(map(str, lines)) + ")"
        bump = next((moves[n] for n in lines if n in moves), None)
        if bump is not None and f.severity == 3 and not f.detail.startswith("base OS") and \
                str(f.fix).startswith(f"{bump.image}:"):
            rows.append(PinChange(rel, f.name, f.version, f"{bump.image}:{bump.new_tag}", "stage-image", note=note))
            continue
        why = next((held[n] for n in lines if n in held), "")
        rows.append(PinChange(rel, f.name, f.version, f.fix, "plan-only",
                              note=note + (f"; not stage-image: {why}" if why else "")))
    return rows
