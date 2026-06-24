"""Legacy-file fence-marker injection helper (Plan 4 of W21 --update batch).

Retrofits canonical ``AGENTTEAMS:BEGIN/END`` fence markers around a legacy
markdown file's existing body so it becomes eligible for future merge-mode
``--update`` runs without forcing ``--overwrite``.

Design choices (per the plan):

- D-1: single fence region wrapping the entire body (default, safety-first).
  Heuristic per-section detection is deliberately out of scope for this pass.
- D-2: retrofit fence-id naming convention is documented in
  ``agentteams/templates/PLACEHOLDER-CONVENTIONS.md``. Default id = ``content``
  (matches the fence id ``emit._normalize_generated_content`` uses for the
  default whole-body wrap, so a later ``--update --merge`` against a team that
  emits the same file replaces in-place cleanly instead of duplicating the
  body alongside an orphaned ``legacy_body`` fence — a real bug observed in
  the 2026-05-20 collector-management cross-repo update). If ``content`` is
  already present in the target file the helper appends a numeric suffix.
- D-3: default mode is **sidecar** (writes ``<name>.fenced.md`` alongside the
  source). ``in-place`` mode requires the caller to pass ``confirm_in_place=True``
  (CLI gates this behind ``--yes`` and an ``@security`` review) and creates a
  timestamped ``.agentteams-backups/`` backup before mutating.
- D-4: not auto-invoked by ``--update`` — a separate CLI surface.

Idempotency: if the input file already contains any AGENTTEAMS fence markers,
the helper is a no-op and returns the original path with ``injected=False`` so
callers can detect "nothing to do" without parsing.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentteams.atomicio import _atomic_write_text
from agentteams.emit import _FENCE_BEGIN_RE, _YAML_FM_RE

DEFAULT_RETROFIT_FENCE_ID = "content"
_BACKUP_DIR_NAME = ".agentteams-backups"
_SAFE_FENCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class InjectResult:
    """Outcome of an injection attempt.

    Attributes:
        output_path:    Path of the written file (sidecar or in-place target).
                        ``None`` if the call was a no-op (already fenced).
        injected:       True if marker pair was added; False if no-op (idempotent).
        backup_path:    Set only on a successful in-place injection; ``None``
                        for sidecar mode or no-ops.
        fence_id:       The fence id used (default + numeric suffix if needed).
    """
    output_path: Path | None
    injected: bool
    backup_path: Path | None = None
    fence_id: str = ""


def _unique_fence_id(existing_text: str, base: str = DEFAULT_RETROFIT_FENCE_ID) -> str:
    """Pick a retrofit fence id not already present in *existing_text*."""
    if not _SAFE_FENCE_ID_RE.match(base):
        raise ValueError(
            f"fence-id base must match [a-z][a-z0-9_]*: {base!r}"
        )
    used = {m.group("sid") for m in _FENCE_BEGIN_RE.finditer(existing_text)}
    if base not in used:
        return base
    n = 1
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def _wrap_body(content: str, fence_id: str) -> str:
    """Return *content* with a single ``fence_id`` block wrapping the body.

    YAML front matter, if present, is kept above the BEGIN marker.
    """
    begin = f"<!-- AGENTTEAMS:BEGIN {fence_id} v=1 -->\n"
    end = f"<!-- AGENTTEAMS:END {fence_id} -->\n"
    fm_match = _YAML_FM_RE.match(content)
    if fm_match:
        fm = fm_match.group(1)
        body = content[len(fm):]
        out = fm + begin + body
    else:
        out = begin + content
    if not out.endswith("\n"):
        out += "\n"
    return out + end


def inject_fence_markers(
    path: Path | str,
    *,
    mode: str = "sidecar",
    fence_id: str = DEFAULT_RETROFIT_FENCE_ID,
    confirm_in_place: bool = False,
) -> InjectResult:
    """Inject canonical fence markers around *path*'s existing body.

    Args:
        path:              Markdown file to retrofit. Must exist.
        mode:              ``'sidecar'`` (default) writes ``<name>.fenced.md`` —
                           non-destructive. ``'in-place'`` rewrites *path*.
        fence_id:          Base retrofit id; numeric suffix appended on collision.
        confirm_in_place:  Required True for ``mode='in-place'`` — the CLI
                           surface gates this behind ``--yes`` + ``@security``.

    Returns:
        ``InjectResult``.

    Raises:
        FileNotFoundError: *path* does not exist.
        ValueError:        ``mode='in-place'`` without ``confirm_in_place=True``,
                           or invalid fence_id base.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if mode not in ("sidecar", "in-place"):
        raise ValueError(f"mode must be 'sidecar' or 'in-place': {mode!r}")
    if mode == "in-place" and not confirm_in_place:
        raise ValueError(
            "in-place mode requires confirm_in_place=True (CLI: --yes + "
            "@security clearance)"
        )

    text = p.read_text(encoding="utf-8")

    # Idempotency: if any AGENTTEAMS fence already present, do nothing.
    if _FENCE_BEGIN_RE.search(text):
        return InjectResult(output_path=p, injected=False, fence_id="")

    sid = _unique_fence_id(text, base=fence_id)
    wrapped = _wrap_body(text, sid)

    if mode == "sidecar":
        # Use a derived name to avoid clobbering the original.
        out = p.with_name(p.stem + ".fenced.md") if p.suffix == ".md" else p.with_name(p.name + ".fenced.md")
        _atomic_write_text(out, wrapped)
        return InjectResult(output_path=out, injected=True, fence_id=sid)

    # in-place: backup first, then rewrite.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = p.parent / _BACKUP_DIR_NAME / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_target = backup_dir / p.name
    shutil.copy2(p, backup_target)
    # Atomic rewrite (temp-in-same-dir + os.replace), matching emit.py — a crash
    # mid-write must not truncate the live source file (backup above is the net).
    _atomic_write_text(p, wrapped)
    return InjectResult(
        output_path=p, injected=True, backup_path=backup_target, fence_id=sid,
    )


__all__ = [
    "DEFAULT_RETROFIT_FENCE_ID",
    "InjectResult",
    "inject_fence_markers",
]
