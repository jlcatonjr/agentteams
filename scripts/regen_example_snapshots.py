#!/usr/bin/env python3
"""Regenerate the golden snapshots under ``examples/*/expected/``.

``tests/test_integration.py::test_snapshot_comparison`` compares generated output against these
snapshots and runs in CI, so drift *is* caught. What was missing is a supported way to update
them after a legitimate template change: the last three template edits were reconciled by
hand-copying files out of a temp directory, which is slow and easy to get subtly wrong.

Two deliberate safety properties, both from the plan audit for this script:

- **Refuses to run on a dirty tree** outside the paths it is allowed to touch. A regeneration
  that sweeps in unrelated working-tree state would launder an unintended change into the
  goldens, where it becomes the reference everything else is measured against. That is strictly
  worse than copying by hand. Override with ``--allow-dirty`` when you know why.
- **Refreshes only snapshots that already exist.** It never adds a new file to ``expected/``, so
  it cannot silently expand what the snapshot test covers — growing coverage stays a deliberate
  act.

Usage::

    python scripts/regen_example_snapshots.py            # regenerate, refusing a dirty tree
    python scripts/regen_example_snapshots.py --check    # report drift, write nothing
    python scripts/regen_example_snapshots.py --allow-dirty
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _comparable(path: Path) -> bytes:
    """Bytes for snapshot comparison, with per-run live data redacted out of markdown."""
    raw = path.read_bytes()
    if path.suffix != ".md":
        return raw
    from agentteams.fences import redact_live_data
    return redact_live_data(raw.decode("utf-8")).encode("utf-8")
EXAMPLES = REPO_ROOT / "examples"

#: Files whose content is a live network fetch or a per-run timestamp. They differ on every run
#: regardless of template state, so the snapshot test already excludes them and so must this.
LIVE_DATA_FILES = {
    "security-vulnerability-watch.reference.md",
    "framework-watch.reference.md",
}

#: `security.agent.md` used to sit in the set above. It no longer does: only the BODY of its live
#: fences varies per run, and `fences.redact_live_data` blanks exactly those, so the rest of the
#: highest-privilege agent's file is now comparable. The two files remaining are whole-file live
#: payloads with no stable structure worth comparing.

#: Paths this script is allowed to modify. Anything else dirty in the tree blocks the run.
ALLOWED_DIRTY_PREFIXES = ("examples/",)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False
    ).stdout


def _unexpected_dirt() -> list[str]:
    """Return tracked-file changes outside :data:`ALLOWED_DIRTY_PREFIXES`.

    Returns:
        Repo-relative paths that are modified but which this script has no business touching.
    """
    out = []
    for line in _git("status", "--porcelain").splitlines():
        path = line[3:].strip()
        if path and not path.startswith(ALLOWED_DIRTY_PREFIXES):
            out.append(path)
    return out


def _run_pipeline(brief: Path, out_dir: Path) -> None:
    """Generate a team from ``brief`` into ``out_dir`` using the integration test's own path.

    Reusing the test helper rather than shelling out to the CLI keeps the snapshots byte-aligned
    with what the test compares against; a second, subtly different invocation path is exactly
    how goldens drift from the thing they are meant to pin.
    """
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_integration import _run_pipeline as _rp  # type: ignore[import-not-found]

    _rp(brief, out_dir)


def regenerate(example: str, *, check_only: bool) -> list[str]:
    """Refresh (or report on) one example's snapshots.

    Args:
        example: Directory name under ``examples/``.
        check_only: Report differing files without writing.

    Returns:
        Repo-relative paths that changed (or would change).
    """
    brief = EXAMPLES / example / "brief.json"
    expected = EXAMPLES / example / "expected"
    if not brief.is_file() or not expected.is_dir():
        return []

    changed: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        generated = Path(td)
        _run_pipeline(brief, generated)

        for snap in sorted(list(expected.rglob("*.md")) + list(expected.rglob("*.svg"))):
            if snap.name in LIVE_DATA_FILES or "build-log" in snap.name:
                continue
            rel = snap.relative_to(expected)
            match = next(
                (c for c in generated.rglob(rel.name)
                 if c.relative_to(generated).as_posix().endswith(rel.as_posix())),
                None,
            )
            if match is None:
                continue
            if _comparable(match) == _comparable(snap):
                continue
            changed.append(str(snap.relative_to(REPO_ROOT)))
            if not check_only:
                shutil.copy2(match, snap)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report drift and write nothing (exit 1 if any)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="proceed even with unrelated working-tree changes")
    args = parser.parse_args(argv)

    dirt = _unexpected_dirt()
    if dirt and not args.allow_dirty:
        print("Refusing to regenerate: working tree has changes outside examples/.", file=sys.stderr)
        for path in dirt[:20]:
            print(f"  {path}", file=sys.stderr)
        print("\nCommit or stash them first, or pass --allow-dirty if this is deliberate.\n"
              "Regenerating over unrelated changes can bake them into the goldens, where they "
              "become the reference everything else is compared against.", file=sys.stderr)
        return 2

    all_changed: list[str] = []
    for example in sorted(p.name for p in EXAMPLES.iterdir() if (p / "expected").is_dir()):
        changed = regenerate(example, check_only=args.check)
        all_changed.extend(changed)
        print(f"{example}: {len(changed)} snapshot(s) {'differ' if args.check else 'updated'}")

    if all_changed:
        print("\n" + ("Would update:" if args.check else "Updated:"))
        for path in all_changed:
            print(f"  {path}")
        print("\nReview this list before committing — every entry becomes the new reference.")
    return 1 if (args.check and all_changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
