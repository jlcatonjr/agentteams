"""``stage-image``: move an end-of-life official runtime image's Dockerfile ``FROM`` tag to a supported line.

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
- its release line is end-of-life. The new tag (:func:`.images.upgrade_tag`, which the fix plan's "→" uses too) is
  the newest supported line first released at least :data:`.images.LINE_MIN_AGE_DAYS` days ago (S-10: leaving an
  end-of-life line does not need the newest one), with the **same** suffix and the same pinning granularity: a line
  tag stays a line tag (``3.9-slim`` -> ``3.14-slim``), a full-patch tag becomes that line's latest release;
- the Dockerfile has no byte-order mark, no heredoc (``<<``) and no ``escape`` / ``syntax`` parser directive
  (:func:`hazard`): its lines are split on ``"\n"`` only, as Docker splits them, and anything that could make
  Docker read a line differently is left to a person.

Every other reference with an image finding is held with its reason (``image-held``), a manifest that cannot be read
gets a note (never a silent empty plan), and :func:`check_from_edit` is the executor's offline, byte-level check
of a staged change: only those tag edits, upward by a release line.

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
_FROM_LINE = re.compile(r"^([ \t]*FROM[ \t]+(?:--[^ \t\r\n]+[ \t]+)*)([^ \t\r\n]+)(.*?)(\r?\n)?$",
                        re.IGNORECASE | re.DOTALL)
#: A parser directive that changes how the rest of the file parses (``# escape=``, ``# syntax=``).
_DIRECTIVE = re.compile(rb"(?im)^[ \t]*#[ \t]*(?:escape|syntax)[ \t]*=")
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
    notes: list[str] = field(default_factory=list)   # a manifest not planned at all, and why (never silent)


def is_dockerfile(name: str) -> bool:
    """Return whether a file name is a Dockerfile or Containerfile (a container manifest that is not compose YAML).

    Args:
        name: File name (a path's last part is used).

    Returns:
        True for ``Dockerfile*``, ``*.Dockerfile`` and ``Containerfile``.
    """
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    return images.is_container_manifest(base) and not re.search(r"\.ya?ml$", base, re.IGNORECASE)


def _split_lines(text: str) -> list[str]:
    """Split on "\n" only, as Docker's parser does, keeping each line's ending (``\r\n`` stays on its line)."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def hazard(data: bytes) -> str:
    """Return why a Dockerfile's text is outside what ``stage-image`` parses the way Docker does, or "".

    Args:
        data: The file's bytes.

    Returns:
        A reason for a byte-order mark, a heredoc (``<<``), or an ``escape`` / ``syntax`` parser directive (a
        different escape character, or another frontend, changes how every line parses); "" otherwise.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return "a byte-order mark: change it by hand"
    if b"<<" in data:
        return "a heredoc (<<): change it by hand"
    if _DIRECTIVE.search(data):
        return "an escape or syntax parser directive: change it by hand"
    return ""


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
    """Return the tag ``stage-image`` moves ``ref`` to (:func:`.images.upgrade_tag`, the fix plan's "→" too).

    Args:
        ref: A reference :func:`.images.refs` read.
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        ``(tag, "")`` to stage; ``("", reason)`` to hold a reference that has a finding; ``("", "")`` when there is
        nothing to report (no finding at all).

    Raises:
        LookupError: The release data is unavailable (the message says why); nothing is guessed.
    """
    judged = images.judge(ref, "", "", today)
    if judged.feed_errors:
        raise LookupError("; ".join(judged.feed_errors))
    if not judged.findings:
        return "", ""
    problem = _shape_problem(ref)
    if problem:
        return "", problem
    version, suffix = images.split_tag(ref.tag)
    kind = ELIGIBLE[str(ref.official)]
    if runtimes.evaluate(kind, version, manager="image", today=today).verdict != "eol":
        return "", "not an end-of-life runtime line (stage-image moves only those)"
    target = images.upgrade_tag(kind, version, suffix, today)
    tv = images.split_tag(target)[0]
    if not target or not _NUMERIC.fullmatch(tv) or len(tv.split(".")) != len(version.split(".")) or \
            version_key(tv) <= version_key(version):
        return "", f"no supported release line at least {images.LINE_MIN_AGE_DAYS} days old"
    return target, ""


def plan_file(path: Path, rel: str, today: _dt.date | None = None) -> ImagePlan:
    """Plan the ``FROM`` tag changes of one container manifest (only a Dockerfile's are ever bumped).

    Args:
        path: The Dockerfile or compose file.
        rel: Its path for the plan (scan-root relative).
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        An :class:`ImagePlan`: bumps, held references with a finding (with the reason; the image shown only by
        :meth:`.images.ImageRef.display`), feed errors, and a note when the file cannot be read. This function does
        not raise for bad input.
    """
    out = ImagePlan()
    try:
        data = path.read_bytes()
        lines = _split_lines(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        out.notes.append(f"{rel}: not read ({type(exc).__name__}): base images not planned")
        return out
    found, _notes = images.refs(path)
    risk = hazard(data) if is_dockerfile(path.name) else ""
    for ref in found:
        try:
            tag, why = new_tag(ref, today)
        except LookupError as exc:
            out.feed_errors.append(f"{rel}:{ref.line}: {exc}")
            continue
        m = _FROM_LINE.match(lines[ref.line - 1]) if tag and 0 < ref.line <= len(lines) else None
        parsed = images.parse_ref(m.group(2), ref.line, "FROM") if m else None
        if tag and risk:
            why = risk
        elif tag and (parsed is None or lines[ref.line - 1].rstrip().endswith("\\") or
                      (parsed.registry, parsed.repository, parsed.tag, parsed.digest) !=
                      (ref.registry, ref.repository, ref.tag, ref.digest)):
            why = "FROM spans several lines: change it by hand"
        if why:
            out.held.append((rel, ref.line, ref.display(), ref.tag or "(no tag)", why))
        elif tag:
            out.bumps.append(Bump(rel, ref.line, str(ref.official), ref.tag, tag))
    return out


def plan_tree(root: Path, today: _dt.date | None = None) -> ImagePlan:
    """Plan every container manifest under a working tree (``manifests.discover``; ``exclude_paths`` honoured).

    Args:
        root: The executor's worktree (a repository root).
        today: Date for EOL comparison (tests); defaults to today.

    Returns:
        The combined :class:`ImagePlan` (bumps only ever in Dockerfiles). A brief with ``level: off`` plans nothing.
    """
    root = root.resolve()
    out = ImagePlan()
    pol = project_policy.read_policy(root)
    if pol.level == "off":
        return out
    for path in manifests.discover(root):
        rel = path.relative_to(root).as_posix()
        if not images.is_container_manifest(path.name) or \
                (pol.exclude_paths and project_policy.is_excluded(rel, pol.exclude_paths)):
            continue
        if path.is_symlink():
            out.notes.append(f"{rel}: a symlink: never read or edited")
            continue
        one = plan_file(path, rel, today)
        out.bumps += one.bumps
        out.held += one.held
        out.feed_errors += one.feed_errors
        out.notes += one.notes
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
        data = path.read_bytes()
        if hazard(data):
            raise ValueError(f"{rel}: {hazard(data)}")
        lines = _split_lines(data.decode("utf-8"))
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
        before = _split_lines(old.decode("utf-8"))
        after = _split_lines(new.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"refusing {rel}: not UTF-8 text") from exc
    if risk := hazard(old) or hazard(new):
        raise ValueError(f"refusing {rel}: {risk}")
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
    return fix.FixResult(changes=changes, applied=True, resolved=len(take), feed_errors=list(plan.feed_errors),
                         refused=[(n.split(": ", 1)[0], n.split(": ", 1)[-1]) for n in plan.notes])


def futile_detail(changes: list[PinChange]) -> str:
    """Return ``stage-image``'s refusal when it moved no tag (held per op by :mod:`.retry_guard`).

    Args:
        changes: The :func:`fix_result` rows.

    Returns:
        :data:`.retry_guard.IMAGE_FUTILE_DETAIL` with the count of :data:`HELD` rows.
    """
    from agentteams.vulnscan import (
        retry_guard,  # lazy: keep this module's load-time imports small
    )

    held = sum(1 for c in changes if c.action == HELD)
    return f"{retry_guard.IMAGE_FUTILE_DETAIL} ({held} base image reference(s) held for a person; see the fix plan)"


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
    unread = "; ".join(staged.notes)
    rows = []
    for f in found:
        lines = [int(n) for loc in f.locations if (n := loc.rsplit(":", 1)[-1]).isdigit()]
        note = f"{f.detail} (line " + ", ".join(map(str, lines)) + ")"
        bump = next((moves[n] for n in lines if n in moves), None)
        if bump is not None and f.severity == 3 and not f.detail.startswith("base OS") and \
                str(f.fix).startswith(f"{bump.image}:"):
            rows.append(PinChange(rel, f.name, f.version, f"{bump.image}:{bump.new_tag}", "stage-image", note=note))
            continue
        why = next((held[n] for n in lines if n in held), unread)
        rows.append(PinChange(rel, f.name, f.version, f.fix, "plan-only",
                              note=note + (f"; not stage-image: {why}" if why else "")))
    return rows
