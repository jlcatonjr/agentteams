#!/usr/bin/env python3
"""build_template_lineage.py — generate the fence-twin lineage corpus from git history.

The corpus (``agentteams/data/template_lineage.json``) is what lets ``agentteams.fence_twins`` drop
an unfenced pre-fencing copy of a section: a twin is deletable only when it equals a section text
some earlier template emitted UNFENCED under the heading its current template now fences. That
makes the corpus deletion authority (Rule 12), so it is never hand-edited: this script derives it
from git objects only, deterministically, and ``tests/test_template_lineage.py`` regenerates it and
requires equality.

What is collected, per current template that carries fences:

* the headings that lead exactly one of its fences (raw, placeholders unresolved);
* for every historical blob of that template reachable from ``HEAD`` and the archived pre-public
  tips in :data:`LINEAGE_TIPS`, the text under each such heading **where it was unfenced** in that
  blob — a fenced occurrence cannot have produced an unfenced twin, so it is never authority;
* each distinct text once, attributed to the earliest commit (committer time, then SHA) that held it.

**History availability.** The pre-public history (2026-04-10 .. 2026-08-31) is not on ``origin``;
it lives in archived local refs. Without those commits the corpus cannot be regenerated and this
script refuses (exit 2) rather than write a smaller corpus that would silently keep less.

Usage::

    python scripts/build_template_lineage.py            # rewrite the corpus
    python scripts/build_template_lineage.py --check    # exit 1 when the shipped corpus differs
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.fence_twins import CORPUS_PATH, fence_leading_headings, unfenced_section  # noqa: E402
from agentteams.git_exec import git_argv, run_git  # noqa: E402

#: Maximal commits of the archived pre-public history (fencing began there). Each must exist locally.
LINEAGE_TIPS: tuple[str, ...] = (
    "2487197409da80ea5901466f48273dd9bdf6e777",  # 2026-08-31 chore(fleet): pre-update snapshot
    "f3e8729a5dea04e14555c197997d761739e9884b",  # 2026-07-17 archive/perf-memory-index-validation-cache
    "f932f15d3bd3f1b52c02b2f9f3387a34d4242d6a",  # 2026-08-27 backup/goose-p1-1
)
#: Template roots over time: ``templates/`` before the package move, ``agentteams/templates/`` after.
_HISTORY_ROOTS = ("templates/", "agentteams/templates/")
_TEMPLATES_DIR = REPO_ROOT / "agentteams" / "templates"
_CORPUS_VERSION = 1


def _git(*args: str) -> str:
    """Run a read-only git command in the repository and return stdout; raise on failure."""
    proc = run_git(REPO_ROOT, *args, hardened=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def missing_tips() -> list[str]:
    """Return the :data:`LINEAGE_TIPS` whose commits are not present in this repository.

    Returns:
        The missing tips; empty when the full history is available.

    Raises:
        Nothing.
    """
    missing = []
    for tip in LINEAGE_TIPS:
        proc = run_git(REPO_ROOT, "cat-file", "-e", f"{tip}^{{commit}}", hardened=True, timeout=30)
        if proc.returncode != 0:
            missing.append(tip)
    return missing


def _current_leads() -> dict[str, list[str]]:
    """``{template rel path: [raw heading, ...]}`` for headings leading exactly one current fence."""
    leads: dict[str, list[str]] = {}
    for path in sorted(_TEMPLATES_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""  # a binary template asset carries no fence
        headings = sorted(h for h, sids in fence_leading_headings(text).items() if len(sids) == 1)
        if headings:
            leads[path.relative_to(_TEMPLATES_DIR).as_posix()] = headings
    return leads


def _template_rel(hist_path: str) -> str | None:
    """Map a historical repository path to a current template rel path (None when not a template)."""
    for root in sorted(_HISTORY_ROOTS, key=len, reverse=True):
        if hist_path.startswith(root):
            return hist_path[len(root):]
    return None


def _commits() -> list[tuple[int, str]]:
    """``(committer time, sha)`` of every commit touching a template root, oldest first."""
    out = _git("log", "--full-history", "--format=%ct %H", *LINEAGE_TIPS, "HEAD", "--",
               *(root.rstrip("/") for root in _HISTORY_ROOTS))
    rows = {(int(t), sha) for t, sha in (line.split() for line in out.splitlines() if line.strip())}
    return sorted(rows)


def _blob_texts(blob_ids: list[str]) -> dict[str, str]:
    """Read blobs in one ``cat-file --batch`` call; undecodable blobs are skipped."""
    raw = subprocess.run(git_argv(REPO_ROOT, "cat-file", "--batch", hardened=True),
                         input="\n".join(blob_ids).encode() + b"\n", capture_output=True, check=True,
                         timeout=300).stdout
    texts: dict[str, str] = {}
    pos = 0
    for blob in blob_ids:
        header_end = raw.index(b"\n", pos)
        _sha, _kind, size = raw[pos:header_end].split()
        body = raw[header_end + 1: header_end + 1 + int(size)]
        pos = header_end + 1 + int(size) + 1
        texts[blob] = body.decode("utf-8", errors="replace")  # binary blobs carry no fence: no entries
    return texts


def build() -> dict:
    """Derive the corpus payload from git history.

    Returns:
        The JSON-serialisable corpus.

    Raises:
        RuntimeError: When a lineage tip is missing or a git command fails.
    """
    missing = missing_tips()
    if missing:
        raise RuntimeError(f"lineage tips not in this repository: {', '.join(missing)}")
    leads = _current_leads()
    first_seen: dict[tuple[str, str], tuple[int, str, str]] = {}  # (rel, blob) -> (ct, sha, hist path)
    for ct, sha in _commits():
        listing = _git("ls-tree", "-r", sha, "--", *(root.rstrip("/") for root in _HISTORY_ROOTS))
        for line in listing.splitlines():
            meta, _, hist_path = line.partition("\t")
            parts = meta.split()
            if len(parts) != 3 or parts[1] != "blob":
                continue
            rel = _template_rel(hist_path)
            if rel is None or rel not in leads:
                continue
            first_seen.setdefault((rel, parts[2]), (ct, sha, hist_path))
    texts = _blob_texts(sorted({blob for _rel, blob in first_seen}))
    table: dict[str, dict[str, dict[str, tuple[int, str, str]]]] = {}
    for (rel, blob), (ct, sha, hist_path) in first_seen.items():
        text = texts.get(blob)
        if text is None:
            continue
        for heading in leads[rel]:
            span = unfenced_section(text, heading, allow_eof=True)
            if isinstance(span, str):
                continue
            section = text[span[0]:span[1]]
            seen = table.setdefault(rel, {}).setdefault(heading, {})
            if section not in seen or (ct, sha) < seen[section][:2]:
                seen[section] = (ct, sha, hist_path)
    templates = {
        rel: {
            heading: [{"commit": sha, "path": hist_path, "text": section}
                      for section, (ct, sha, hist_path) in sorted(by_text.items(), key=lambda kv: (kv[1], kv[0]))]
            for heading, by_text in sorted(headings.items())
        }
        for rel, headings in sorted(table.items())
    }
    return {
        "version": _CORPUS_VERSION,
        "generator": "scripts/build_template_lineage.py",
        "note": ("Generated from git history; never edit by hand. Each text is a section an earlier "
                 "template emitted UNFENCED under a heading its current template fences. Deletion "
                 "authority for agentteams.fence_twins (Rule 12): regenerate, review the diff, re-pin."),
        "lineage_tips": list(LINEAGE_TIPS),
        "templates": templates,
    }


def serialise(payload: dict) -> str:
    """Return the canonical text of *payload* (sorted keys, fixed indent, trailing newline).

    Args:
        payload: A corpus payload from :func:`build`.

    Returns:
        The exact text written to the corpus file.

    Raises:
        Nothing.
    """
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Rewrite the corpus, or with ``--check`` compare it to a fresh build.

    Args:
        argv: Command-line arguments (``sys.argv[1:]`` when None).

    Returns:
        0 on success / match, 1 when ``--check`` finds a difference, 2 when history is missing.

    Raises:
        Nothing.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the shipped corpus is stale")
    args = parser.parse_args(argv)
    try:
        text = serialise(build())
    except RuntimeError as exc:
        print(f"build_template_lineage: {exc}", file=sys.stderr)
        return 2
    if args.check:
        current = CORPUS_PATH.read_text(encoding="utf-8") if CORPUS_PATH.exists() else ""
        if current != text:
            print("template lineage corpus is stale: run scripts/build_template_lineage.py", file=sys.stderr)
            return 1
        print("template lineage corpus is current")
        return 0
    CORPUS_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {CORPUS_PATH.relative_to(REPO_ROOT)} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
