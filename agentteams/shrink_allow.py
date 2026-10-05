"""shrink_allow.py — reviewed, content-bound, per-section overrides of the shrink guard.

``--shrink-policy preserve`` (the default) keeps an on-disk fence body whenever the new render
would "lose concrete refs". It cannot tell a reference the *template itself* retired from a
project's own enrichment, so a retirement pins the old body forever (2026-10-04: the dead
``references/git-procedures.md`` citation, retired upstream in 703b1e2, held researchteam's whole
``git-operations`` body — and every later template update — in place; 20 sections were pinned
there and 49 in musicmaker).

**The review.** ``AGENTTEAMS_SHRINK_REPORT=<path>`` makes a merge run write, for every section it
pinned, the old and new bodies, each lost token and that token's provenance in agentteams' own
template history (``retired`` — in a past template, gone now; ``current``; ``never`` — never in a
template, i.e. enrichment). It prints the exact override entry for each section.

**The override.** ``<rel path>:<fence id>@<digest>`` — the digest is the first 12 hex of the
sha256 of the *reviewed* on-disk body. It releases that section only while the on-disk body still
hashes to the digest, so an entry left in an exported variable, a nested run or a later CI job
cannot release anything that was not reviewed. Sources: repeated ``--shrink-allow`` flags and
``AGENTTEAMS_SHRINK_ALLOW`` (``;``-separated; for wrappers such as ``researchteam update``).

**Recovery.** An overridden section takes the replace path, so its old body goes to a
``.lost.<fence>.md`` sidecar in the run's backup dir. ``emit_all`` refuses every override when
there is no backup dir (``--no-backup``), rather than release a section it cannot recover.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

#: Environment variable carrying ``;``-separated override entries.
SHRINK_ALLOW_ENV = "AGENTTEAMS_SHRINK_ALLOW"
#: Environment variable naming the review report to write (``.json``; a ``.md`` twin is written).
SHRINK_REPORT_ENV = "AGENTTEAMS_SHRINK_REPORT"

_ENTRY_RE = re.compile(r"^(?P<path>[^:\s][^:]*?):(?P<sid>[a-z][a-z0-9_]*)@(?P<digest>[0-9a-f]{12})$")
_TEMPLATES_REL = "agentteams/templates"


class ShrinkAllowError(ValueError):
    """Raised when an override entry is not ``<rel path>:<fence id>@<12-hex digest>``."""


def body_digest(block: str) -> str:
    """The 12-hex digest an override binds to: sha256 of the section's stripped body.

    Args:
        block: A fenced region as ``fences`` stores it (markers included) or a bare body.

    Returns:
        The first 12 hex characters of the digest.
    """
    from agentteams.fences import _fence_body

    return hashlib.sha256(_fence_body(block).strip().encode("utf-8")).hexdigest()[:12]


def key(rel_path: str, sid: str, block: str) -> str:
    """The override entry for one section as it is on disk now.

    Args:
        rel_path: The file path relative to the output dir, as merge notices print it.
        sid: The fence id.
        block: The section's current on-disk region.

    Returns:
        ``<rel path>:<sid>@<digest>``.
    """
    return f"{rel_path}:{sid}@{body_digest(block)}"


def parse_entries(entries: Iterable[str]) -> frozenset[str]:
    """Validate and normalize override entries.

    Args:
        entries: Raw entries (blanks ignored).

    Returns:
        The normalized set (``./`` prefixes stripped).

    Raises:
        ShrinkAllowError: An entry is malformed, lacks its digest, is absolute, or uses ``..``.
    """
    out: set[str] = set()
    for raw in entries:
        entry = raw.strip()
        if not entry:
            continue
        m = _ENTRY_RE.match(entry)
        if not m:
            raise ShrinkAllowError(
                f"--shrink-allow {entry!r}: expected <rel path>:<fence id>@<12-hex digest of the "
                "reviewed body>, as printed by the AGENTTEAMS_SHRINK_REPORT review"
            )
        path = m.group("path").removeprefix("./")
        if path.startswith("/") or ".." in path.split("/"):
            raise ShrinkAllowError(f"--shrink-allow {entry!r}: the path must stay inside the output dir")
        out.add(f"{path}:{m.group('sid')}@{m.group('digest')}")
    return frozenset(out)


def resolve(flag_entries: Iterable[str] | None) -> frozenset[str]:
    """Combine ``--shrink-allow`` flags with ``AGENTTEAMS_SHRINK_ALLOW``.

    Args:
        flag_entries: The parsed flag values (may be None).

    Returns:
        The validated override set (empty when neither source is set).

    Raises:
        ShrinkAllowError: Any entry is malformed.
    """
    env = os.environ.get(SHRINK_ALLOW_ENV, "")
    return parse_entries([*(flag_entries or []), *env.split(";")])


def announce(allowed: frozenset[str], *, has_backup: bool, dry_run: bool) -> frozenset[str]:
    """Print the active overrides and return the set this run may actually use.

    An override needs a backup dir for its ``.lost`` sidecar; without one (and outside a dry run)
    every override is refused with a warning, and the sections stay pinned.

    Args:
        allowed: The resolved override set.
        has_backup: Whether this run writes a backup dir.
        dry_run: Whether this is a dry run (previews are always allowed).

    Returns:
        ``allowed``, or an empty set when overrides are refused.
    """
    if not allowed:
        return allowed
    for entry in sorted(allowed):
        print(f"  ⚠  shrink override active: {entry}", file=sys.stderr)
    if not has_backup and not dry_run:
        print("  WARNING: --shrink-allow refused: this run has no backup dir (--no-backup), so a "
              "released section's old body could not be recovered; the sections stay pinned",
              file=sys.stderr)
        return frozenset()
    return allowed


def warn_unused(allowed: frozenset[str], used: set[str]) -> None:
    """Warn about overrides that released nothing (a typo, or the body changed since review).

    Args:
        allowed: The override set the run used.
        used: Entries that actually released a section.
    """
    for entry in sorted(allowed - used):
        print(f"  WARNING: --shrink-allow {entry}: released nothing (no pinned section with that "
              "path, fence and reviewed-body digest)", file=sys.stderr)


def track(used: set[str], report: "Report", rel_path: str, existing: str | None,
          merge_result: object) -> None:
    """Record one file's merge outcome: overrides that fired, and sections still pinned.

    Args:
        used: Accumulates the entries that released a section.
        report: Collects pinned sections for the review report.
        rel_path: File path relative to the output dir.
        existing: The on-disk text before the merge.
        merge_result: The file's :class:`agentteams.fences.MergeResult`.
    """
    from agentteams.fences import _extract_fenced_regions

    overridden = getattr(merge_result, "shrink_overridden", [])
    if overridden and existing:
        regions = _extract_fenced_regions(existing)
        if isinstance(regions, dict):
            used.update(key(rel_path, sid, regions.get(sid, "")) for sid in overridden)
    for sid, (old_block, new_block) in getattr(merge_result, "shrink_pinned", {}).items():
        report.add(rel_path, sid, old_block, new_block)


# ---------------------------------------------------------------------------
# Review report
# ---------------------------------------------------------------------------


def _agentteams_checkout() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    return root if (root / ".git").exists() and (root / _TEMPLATES_REL).is_dir() else None


def token_provenance(token: str, checkout: Path | None) -> str:
    """Classify a lost token against agentteams' template history.

    Args:
        token: A path or backtick identifier the new render no longer contains.
        checkout: The agentteams git checkout, or None when not running from one.

    Returns:
        ``retired`` (in a past template, absent now), ``current`` (still in a template — the loss
        is local), ``never`` (never in any template: project enrichment), or ``unknown``.
    """
    if checkout is None:
        return "unknown"
    git = ["git", "-C", str(checkout)]
    now = subprocess.run([*git, "grep", "-q", "-F", "-e", token, "HEAD", "--", _TEMPLATES_REL],
                         capture_output=True, check=False)
    if now.returncode == 0:
        return "current"
    past = subprocess.run([*git, "log", "--all", "--format=%h", "-S", token, "--", _TEMPLATES_REL],
                          capture_output=True, text=True, check=False)
    if past.returncode != 0:
        return "unknown"
    return "retired" if past.stdout.strip() else "never"


class Report:
    """Collect the sections a run pinned and write the review report if requested."""

    def __init__(self) -> None:
        """Start an empty report."""
        self.items: list[dict[str, object]] = []

    def add(self, rel_path: str, sid: str, old_block: str, new_block: str) -> None:
        """Record one pinned section.

        Args:
            rel_path: File path relative to the output dir.
            sid: Fence id.
            old_block: The on-disk region that was kept.
            new_block: The template region that was suppressed.
        """
        from agentteams.fences import _BACKTICK_IDENT_RE, _PATH_RE, _fence_body

        old, new = _fence_body(old_block), _fence_body(new_block)
        lost = (set(_PATH_RE.findall(old)) | set(_BACKTICK_IDENT_RE.findall(old))) - (
            set(_PATH_RE.findall(new)) | set(_BACKTICK_IDENT_RE.findall(new)))
        self.items.append({"file": rel_path, "fence": sid, "entry": key(rel_path, sid, old_block),
                           "lost": sorted(lost), "old_bytes": len(old), "new_bytes": len(new),
                           "old": old, "new": new})

    def write(self) -> Path | None:
        """Write ``$AGENTTEAMS_SHRINK_REPORT`` (+ a ``.md`` twin) when set and non-empty.

        Returns:
            The JSON path written, or None.
        """
        target = os.environ.get(SHRINK_REPORT_ENV, "").strip()
        if not target or not self.items:
            return None
        checkout = _agentteams_checkout()
        lines = ["# Pinned sections (--shrink-policy preserve)", ""]
        for item in self.items:
            prov = {t: token_provenance(t, checkout) for t in item["lost"]}  # type: ignore[union-attr]
            item["provenance"] = prov
            item["all_retired"] = bool(prov) and all(v == "retired" for v in prov.values())
            lines += [f"## {item['file']} — `{item['fence']}`", "",
                      f"- entry: `{item['entry']}`",
                      f"- bytes {item['old_bytes']} -> {item['new_bytes']}; every lost token "
                      f"retired: **{'yes' if item['all_retired'] else 'no'}**", ""]
            lines += [f"  - `{t}`: {v}" for t, v in prov.items()] + [""]
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.items, indent=2) + "\n", encoding="utf-8")
        path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  ✓  shrink review report: {path} ({len(self.items)} pinned section(s))")
        return path


__all__ = [
    "SHRINK_ALLOW_ENV", "SHRINK_REPORT_ENV", "Report", "ShrinkAllowError", "announce",
    "body_digest", "key", "parse_entries", "resolve", "token_provenance", "track", "warn_unused",
]
