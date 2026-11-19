"""``agentteams --term-check``: the term-index gate over a team's fenced, generated text (issue #548; beta).

A team opts in by adding a register at ``<agents dir>/terminology/terms.json`` (schema
``agentteams-term-register``; see :mod:`agentteams.term_index`). The gate reads **only text inside
``AGENTTEAMS:BEGIN``/``END`` fences**, so it never flags a user-editable section (Project-Specific Rules
or Notes, carried user regions) and never asks anyone to rewrite governed text. It writes nothing.

The check is live: the register, its scope and discouraged forms. A committed index is not compared
(researchteam's committed-index workflow stays with :func:`agentteams.term_index.check_project`). A document
whose fences are not well formed is not scanned at all and is reported as a ``FENCE`` defect.

CEILING (do not soften): a clean run establishes WELL-FORMED, never
CONSISTENT IN MEANING. Matching finds forms; it cannot tell whether a term is used in its defined sense.

Exit codes: 0 no defect, advisory mode, or nothing checked | 1 at least one defect | 2 usage or I/O error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentteams import term_index as ti
from agentteams.unfenced import _FENCE_BEGIN_RE, _FENCE_END_RE


def fence_scan(text: str) -> tuple[str, str | None]:
    """Keep only the text inside well-formed ``AGENTTEAMS`` fences, as the fence engine reads them.

    Markers follow the fence engine's grammar (``<!-- AGENTTEAMS:BEGIN <sid> v=N -->`` …
    ``<!-- AGENTTEAMS:END <sid> -->``). Everything outside a fence, and the marker lines themselves, become
    spaces of the same length, so offsets and line breaks are kept and a blanked line is a paragraph break.
    When the fences are not well formed (an unclosed, nested or mismatched fence, or an END without a
    BEGIN) **nothing** is kept: a broken marker must never widen the scan into user-editable text.

    Args:
        text: A rendered agent file or template, with ``\\n`` line breaks.

    Returns:
        ``(kept, problem)``: the filtered text, and ``None`` or a description of the fence defect.
    """
    out, open_sid, problem = [], None, None
    for n, line in enumerate(text.split("\n"), 1):
        begin, end = _FENCE_BEGIN_RE.search(line), _FENCE_END_RE.search(line)
        if begin and problem is None:
            if open_sid is not None:
                problem = f"line {n}: fence '{begin.group('sid')}' opens inside '{open_sid}'"
            open_sid = begin.group("sid")
        elif end and problem is None:
            if end.group("sid") != open_sid:
                problem = f"line {n}: END '{end.group('sid')}' does not close " + (
                    f"'{open_sid}'" if open_sid else "an open fence")
            open_sid = None
        else:
            out.append(line if open_sid is not None else " " * len(line))
            continue
        out.append(" " * len(line))
    if problem is None and open_sid is not None:
        problem = f"fence '{open_sid}' has no END marker"
    return ("\n".join(" " * len(line) for line in text.split("\n")) if problem else "\n".join(out)), problem


def fenced_only(text: str) -> str:
    """The region filter the gate scans with: :func:`fence_scan`'s kept text.

    Args:
        text: A rendered agent file or template.

    Returns:
        The kept text (all blank when the fences are not well formed).
    """
    return fence_scan(text)[0]


#: How agentteams reads a team's register: fenced text only, no reference-list sections, no source check.
#: The generator differs from the module default because the fence filter changes what is indexed.
TEAM_CONFIG = ti.TermIndexConfig(
    generator="agentteams-term-index/1+fenced",
    region_filter=fenced_only,
    rebuild_hint="--term-check checks live; it reads no committed index",
    glossary_hint="Check it: agentteams --term-check <agents dir>.",
)


def _fence_findings(root: Path) -> tuple[list, list]:
    """Documents whose fences are not well formed; each was scanned as empty.

    The finding is a blocking ``FENCE`` defect only in a file that also holds a complete
    ``AGENTTEAMS:BEGIN``/``END`` pair, that is, a generated file whose fences broke. In a file without one
    (a hand-written document quoting a lone marker, say) it is an advisory review: the gate never demands
    an edit to text agentteams does not generate.
    """
    a = ti.load_register(root, TEAM_CONFIG)
    if a.status != ti.OK:
        return [], []
    defects, reviews = [], []
    for rel in ti.documents(root, a.register, TEAM_CONFIG):
        raw = ti.read_document(root / rel)
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n") if raw else ""
        problem = fence_scan(text)[1]
        if problem:
            ends = {m.group("sid") for m in _FENCE_END_RE.finditer(text)}
            generated = any(m.group("sid") in ends for m in _FENCE_BEGIN_RE.finditer(text))
            (defects if generated else reviews).append(("FENCE", f"{ti.shown(rel)}: {problem}; the file was not scanned"))
    return defects, reviews


def run_term_check(args: argparse.Namespace) -> int:
    """Run the gate for ``args.term_check`` (a team's agents directory; ``""`` means the working directory).

    The check is always live: a committed term index is not compared (``indexed=False``), and generated
    index files are reported, never trusted.

    Args:
        args: Parsed CLI arguments: ``term_check`` and ``term_check_advisory``.

    Returns:
        The process exit code: 0 no defect, advisory mode, or nothing checked; 1 a defect; 2 an I/O error.
    """
    named = bool(args.term_check)
    root = Path(args.term_check or ".").resolve()
    advisory = bool(getattr(args, "term_check_advisory", False))
    if not root.is_dir():
        print(f"ERROR: --term-check: not a directory: {ti.shown(str(args.term_check))}", file=sys.stderr)
        return 2
    try:
        checked, defects, reviews = ti.check_project(root, TEAM_CONFIG, required=named, indexed=False)
        fence_defects, fence_reviews = _fence_findings(root)
        defects, reviews = fence_defects + defects, fence_reviews + reviews
    except (OSError, ValueError) as exc:  # the I/O boundary: an unreadable file is an environment error
        # The type only: an OSError's text carries an absolute path, and this goes to the hook log (#551 review).
        print(f"ERROR: the term check could not run ({type(exc).__name__}); run it by hand for the detail",
              file=sys.stderr)
        return 2
    tdir = root / TEAM_CONFIG.dir_name
    if checked and any((tdir / n).exists() for n in ("term-index.json", "index", "glossary.md")):
        reviews.append(("INDEX", "committed generated term files (term-index.json, index/, glossary.md) are not "
                                 "compared by --term-check (live check); they may be stale"))
    print("Term-index check (fenced, generated text only; beta)")
    print("NOTE: a clean run establishes WELL-FORMED, not that terms are used in their defined sense.")
    for code, msg in defects:
        print(f"  DEFECT[{code}]".ljust(18) + msg)
    for code, msg in reviews:
        print(f"  NEEDS-REVIEW[{code}]".ljust(23) + msg)
    print(f"---\nDEFECT: {len(defects)}  |  NEEDS-REVIEW: {len(reviews)}")
    if defects:
        if advisory:
            print("ADVISORY MODE (--term-check-advisory): defects found but not blocking (exit 0).")
            return 0
        print("GATE FAILED: see docs_src/api-reference/term-index.md.")
        return 1
    if not checked:
        print(f"NOTHING CHECKED: no term register at {TEAM_CONFIG.dir_name}/terms.json. This is not a pass.")
        return 0
    print("PASS (no DEFECT). NEEDS-REVIEW items are advisory.")
    return 0
