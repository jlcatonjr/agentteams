"""fence_baseline.py — which fence bodies agentteams authored, so an unedited one is never shrink-preserved.

**The defect (ops-team, 2026-11-01).** ``--update --merge`` keeps a fence's on-disk body whenever the new render
would "shrink" it (:func:`agentteams.fences._detect_fence_shrink`; ``--shrink-policy=preserve``, the default).
The guard exists to protect enrichment a person or ``@content-enricher`` added. But it cannot tell enrichment from
an older template's own text: ``vuln-remediation.yaml``'s ``daily_remediation`` fence was byte-identical to the
2026-10-23 template render, yet every later update preserved it because the old body backticked ``failed`` and
``refused`` and the new one discusses them in prose ("lost concrete refs"). The deployed agent ran a nine-day-old
workflow until an operator released it by hand with ``--shrink-allow``.

**Fix.** ``build-log.json`` records, per file and fence, the SHA-256 of the fence block agentteams *authored*
(:data:`BUILD_LOG_KEY`). A merge that would shrink-preserve a fence first checks it: an on-disk block whose digest
equals the recorded one is exactly a past agentteams render, nobody's edit, and takes the template update.

**Authored, not written** (the :mod:`agentteams.front_matter_baseline` lesson). A merge writes a preserved,
hand-edited body back to disk, so "the fence as written" would record a person's edit as agentteams' own after one
run, and the next update would overwrite it. :func:`compute` records a fence only when the written block equals this
run's template render, else carries the previous record forward (the preserved body is still that older render, or
it is not and the digest no longer matches), else records nothing.

**What this exclusion newly permits (Rule 12), and how each case is closed.**

- *A person's edit is overwritten.* Only when its block hashes to a recorded authored digest — full SHA-256 of the
  whole block, markers included, so any edit at all falls back to preserve. Absent data (a pre-fix build log, a
  file or fence never recorded) is unknown and preserves, exactly as before.
- *A forged or stale build log.* Anyone who can write ``build-log.json`` can already write the agent file itself;
  a stale entry only matches a body that really is that old render. Either way the release is never silent: the
  shrink notice still prints (``replaced: unedited since generation``) and the old body goes to the ``.lost``
  sidecar. With no backup dir (``--no-backup``) there would be no sidecar, so emit does not release then.
- *An interop projection.* A projection marker's log authorises no overwrite (cond 13); :func:`load_for` returns
  nothing for it.
- Template-authoritative fences, ``--shrink-allow`` and ``--shrink-policy=halt`` are unchanged.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

#: The build-log key holding ``{rel_path: {section_id: sha256 hex}}``.
BUILD_LOG_KEY = "fence_baseline"

Baseline = dict[str, dict[str, str]]


def digest(block: str) -> str:
    """The digest recorded for one fence block.

    Args:
        block: The full fenced block, BEGIN and END marker lines included.

    Returns:
        The block's full SHA-256 hex digest (UTF-8).

    Raises:
        Nothing.
    """
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def block_digests(content: str) -> dict[str, str]:
    """Digest every fence in *content*.

    Args:
        content: A rendered or on-disk file.

    Returns:
        ``{section_id: digest}``; empty when the file has no fences or they do not parse.

    Raises:
        Nothing.
    """
    from agentteams.fences import _extract_fenced_regions  # local: fences imports this module lazily

    regions = _extract_fenced_regions(content)
    if not isinstance(regions, dict):
        return {}
    return {sid: digest(block) for sid, block in regions.items()}


def load(log: Mapping) -> Baseline:
    """The fence baseline from a parsed ``build-log.json``.

    Args:
        log: The parsed build log (may be empty).

    Returns:
        ``{rel_path: {section_id: digest}}``; ``{}`` (unknown) when absent, malformed, or the log is an interop
        projection marker.

    Raises:
        Nothing.
    """
    from agentteams import drift

    if not isinstance(log, Mapping) or drift.is_interop_marker(log):
        return {}
    raw = log.get(BUILD_LOG_KEY)
    if not isinstance(raw, Mapping):
        return {}
    return {rel: {sid: d for sid, d in sids.items() if isinstance(sid, str) and isinstance(d, str)}
            for rel, sids in raw.items() if isinstance(rel, str) and isinstance(sids, Mapping)}


def load_for(output_dir: Path) -> Baseline:
    """The fence baseline recorded in *output_dir*'s ``references/build-log.json``.

    Args:
        output_dir: The agents directory.

    Returns:
        :func:`load`'s result; ``{}`` when there is no readable log.

    Raises:
        Nothing.
    """
    from agentteams import drift

    try:
        return load(drift.load_build_log(output_dir))
    except (FileNotFoundError, ValueError, OSError, KeyError):
        return {}


def for_run(output_dir: Path, *, merge: bool, dry_run: bool, twin_drop: bool | None,
            backup_path: Path | None) -> Baseline:
    """The baseline an ``emit_all`` run may release unedited fences with, or ``{}`` when it may not.

    A release needs a ``.lost`` sidecar, so a run without a backup dir gets nothing (Rule 12). A dry run has no
    backup dir of its own; it is passed the real run's answer through *twin_drop* (``not --no-backup``), so its
    plan matches the real run.

    Args:
        output_dir: The agents directory.
        merge: Whether this is a ``--merge`` run (nothing to release otherwise).
        dry_run: Whether this is a preview.
        twin_drop: emit's *twin_drop* (None means "when *backup_path* is set").
        backup_path: The run's backup dir, or None.

    Returns:
        :func:`load_for`'s result, or ``{}``.

    Raises:
        Nothing.
    """
    backup_will_exist = bool(twin_drop) if dry_run else (twin_drop is not False and backup_path is not None)
    return load_for(output_dir) if merge and backup_will_exist else {}


def is_unedited(file_baseline: Mapping[str, str] | None, sid: str, block: str) -> bool:
    """True when the on-disk *block* is exactly a fence body agentteams authored.

    Args:
        file_baseline: This file's ``{section_id: digest}`` from :func:`load`, or None.
        sid: The fence's section id.
        block: The on-disk fenced block, markers included.

    Returns:
        Whether the block's digest equals the recorded one. False when nothing is recorded.

    Raises:
        Nothing.
    """
    recorded = (file_baseline or {}).get(sid)
    return bool(recorded) and bool(block) and digest(block) == recorded


def _relative_key(path: Path, output_dir: Path) -> str | None:
    """The build-log key for *path*: POSIX, relative to *output_dir*, never absolute (as front_matter_baseline, OI-21).

    None when no relative form exists (another drive on Windows): nothing is recorded, which fails safe.
    """
    try:
        rel = os.path.relpath(path, output_dir)
    except ValueError:
        return None
    return None if os.path.isabs(rel) else Path(rel).as_posix()


def _file_digests(path: Path) -> dict[str, str]:
    """:func:`block_digests` of the file at *path*; empty when it cannot be read (nothing recorded)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return block_digests(text)


def compute(written_abs_paths: Iterable[str], output_dir: Path, template: Mapping[str, Mapping[str, str]],
            previous: Mapping[str, Mapping[str, str]] | None) -> Baseline:
    """Record, per file and fence, the digest of the block agentteams authored.

    Args:
        written_abs_paths: Absolute paths of every file this run wrote, merged or left unchanged.
        output_dir: The agents directory; keys are POSIX paths relative to it (the ``rel_path`` emit uses).
        template: ``{rel_path: {section_id: digest}}`` of this run's template render.
        previous: The previous run's baseline (:func:`load`), or None.

    Returns:
        ``{rel_path: {section_id: digest}}``. A fence is recorded when its on-disk block equals this run's render,
        else when the previous baseline has it; otherwise it is left out (unknown: the next merge preserves it on
        shrink). Files with nothing recorded are omitted.

    Raises:
        Nothing.
    """
    out: Baseline = {}
    for abs_path in written_abs_paths:
        path = Path(abs_path)
        rel = _relative_key(path, output_dir)
        if rel is None or not path.is_file():
            continue
        on_disk = _file_digests(path)
        fresh = template.get(rel, {})
        prior = (previous or {}).get(rel, {})
        kept: dict[str, str] = {}
        for sid, d in on_disk.items():
            if fresh.get(sid) == d:
                kept[sid] = d
            elif sid in prior:
                kept[sid] = prior[sid]
        if kept:
            out[rel] = kept
    return out
