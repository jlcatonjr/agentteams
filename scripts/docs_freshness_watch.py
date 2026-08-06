#!/usr/bin/env python3
"""docs_freshness_watch.py — fire the documentation-refresh procedure when docs fall behind code.

The condition
-------------
Let ``docs_age`` be the age of the most recent commit touching the **documentation set**
and ``src_age`` the age of the most recent commit touching the **source set**::

    FIRE  iff  docs_age > 24h  AND  src_age <= 24h

In words: source moved today, documentation did not.

Why this shape and not a "last run" marker
------------------------------------------
The verdict is a **pure function of git history and the wall clock**. This module reads no
persisted state and writes none that participates in the verdict. That is the whole
reliability argument, and it is deliberate:

- A dropped, cancelled, or crashed run has **zero** effect on the next run. There is no
  cursor to corrupt, no marker commit to miss, no cache whose absence changes the answer.
- Re-running is free and idempotent. Running it a hundred times in a row yields one answer.
- A run that fires and whose remediation is never merged will fire again, because the
  docs are still stale. The trigger self-heals by construction rather than by bookkeeping.

The failure mode this avoids is specific and common: a watcher that records "last checked
at T", crashes before updating T, and thereafter either re-alerts forever or — far worse —
records T on the crash path and goes quiet about a condition that is still true. Neither is
reachable here because there is no T.

Advisory, not a gate
--------------------
This **always exits 0** on a successful evaluation, in the same shape and for the same
reason as ``scripts/check_session_obligations.py``: it reports absent evidence, not
violation. It cannot see that a doc was reviewed and correctly judged to need no change.
An instrument that called that a violation would be routed around rather than followed.

``--check`` is the one exception and it is opt-in: it exits 2 when the condition fires, for
callers that want a non-zero signal. Nothing in this repository's CI uses it as a gate.

The sets are derived, never declared
------------------------------------
A hand-written list of documentation files is itself a published artifact that goes stale —
the failure being fixed, one level up (the argument is
``tests/test_published_artifacts_have_checks.py``'s, and it is right). So both sets are
computed from ``git ls-files`` by *what a path is*, with only the exclusions declared. A new
guide added tomorrow is watched tomorrow, with nobody remembering to register it.

Usage::

    python3 scripts/docs_freshness_watch.py              # human-readable verdict, exit 0
    python3 scripts/docs_freshness_watch.py --json       # machine-readable verdict, exit 0
    python3 scripts/docs_freshness_watch.py --check      # exit 2 if the condition fires
    python3 scripts/docs_freshness_watch.py --window 48  # widen the window to 48h
    python3 scripts/docs_freshness_watch.py --ref main   # evaluate a different ref

Procedure: ``references/documentation-refresh.procedure.md``
Origin: the 2026-08-06 documentation staleness audit — see the CHANGELOG entry
"a documentation-refresh procedure, and a trigger that cannot latch itself off".
(The audit report itself lives under ``references/plans/``, which is gitignored by
design: local, non-published working documents. See ``references/filing-conventions.md``.)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The window, in hours. Documentation older than this while source is newer fires.
DEFAULT_WINDOW_HOURS = 24

# ---------------------------------------------------------------------------
# Set derivation — declared exclusions only, never a declared inclusion list
# ---------------------------------------------------------------------------

#: Suffixes that make a tracked file documentation.
_DOC_SUFFIXES = (".md",)

#: Individual tracked paths that are documentation despite not being ``.md``.
_DOC_EXTRA_PATHS = frozenset({
    "agentteams.1",   # the generated man page — a published doc surface
    "mkdocs.yml",     # the site's navigation *is* documentation structure (audit D-5)
})

#: Tracked ``.md`` files that are NOT documentation-freshness evidence.
#:
#: ``CHANGELOG.md`` is the load-bearing entry and the reason this set exists.
#: ``changelog-link.yml`` fails any PR touching ``agentteams/**/*.py`` that does not also
#: change the changelog — so on this repository a changelog edit accompanies nearly every
#: code change. Counting it as documentation would let it act as a freshness alibi for
#: every other doc: ``docs_age`` would track changelog discipline rather than
#: documentation.
#:
#: This is measured, not assumed. Backtesting the condition over 90 days at the real 6h
#: cadence (361 evaluations): **13 firings with CHANGELOG.md in the set, 34 without** —
#: including it suppressed 62% of the signal. Nothing is lost by excluding it, because it
#: is the one doc surface already covered by a failing gate.
_DOC_EXCLUDE_PATHS = frozenset({
    "CHANGELOG.md",
})

#: Suffixes marking point-in-time session artifacts. They are committed once and never
#: maintained forward, so a fresh one says nothing about whether the *maintained* docs
#: track the code — the same argument that excludes ``references/plans/``.
_DOC_EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".conflict-audit.md",
    ".adversarial.md",
    ".report.md",
    ".audit.md",
)

#: Prefixes excluded from the documentation set, each with its reason. A path here is not
#: "not a doc" in general — it is not a doc *whose freshness this watcher can judge*.
_DOC_EXCLUDE_PREFIXES: dict[str, str] = {
    "docs/": "build output, not source (audit D-6)",
    "_site/": "build output",
    "tmp/": "gitignored working artifacts; plan CSVs are evidence, not published docs",
    "workSummaries/": "historical record — backdating it would be falsifying the record",
    "references/plans/": "point-in-time plans and reports; they are not maintained forward",
    ".agentteams-backups/": "backup snapshots",
    "examples/": "fixture trees; their freshness is bound by the example-brief guard",
    "tests/": "test data",
    ".github/agents/": "generated agent files (gitignored except the brief)",
    "agentteams/templates/": "shipped templates — code-adjacent, covered by the template ledger",
    "guides/": "vendored third-party material",
    "memories/": "runtime state",
    "build/": "build output",
    "dist/": "build output",
}

#: Prefixes excluded from the *source* set: churn here does not oblige a doc update.
#: Kept deliberately tight — over-excluding here makes the watcher quiet, which is the
#: failure mode that matters. Anything not excluded and not a doc counts as source, which
#: is why templates (``agentteams/templates/``), example fixtures and test data are absent
#: from this list: they are product artifacts, and changing one can genuinely oblige a doc
#: update even though their own freshness is not what this watcher judges.
_SRC_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "tmp/",
    "docs/",
    "_site/",
    "workSummaries/",
    "references/plans/",   # writing a plan is not a code change owing documentation
    ".agentteams-backups/",
    "build/",
    "dist/",
    "memories/",
    "guides/",
)


def _is_doc(path: str) -> bool:
    """True if ``path`` belongs to the watched documentation set."""
    if path in _DOC_EXTRA_PATHS:
        return True
    if not path.endswith(_DOC_SUFFIXES):
        return False
    if path in _DOC_EXCLUDE_PATHS:
        return False
    if path.endswith(_DOC_EXCLUDE_SUFFIXES):
        return False
    return not any(path.startswith(p) for p in _DOC_EXCLUDE_PREFIXES)


def _is_source(path: str) -> bool:
    """True if ``path`` belongs to the source set whose churn obliges a doc review.

    A path excluded from *both* sets is inert: it neither raises the obligation nor
    discharges it. ``CHANGELOG.md`` is the important case — a changelog-only commit must
    not reset ``docs_age`` (it is not documentation of current behaviour) and must not
    raise ``src_age`` either (it is not a code change owing a doc update).
    """
    if _is_doc(path):
        return False
    if path in _DOC_EXCLUDE_PATHS or path.endswith(_DOC_EXCLUDE_SUFFIXES):
        return False
    if any(path.startswith(p) for p in _SRC_EXCLUDE_PREFIXES):
        return False
    return True


# ---------------------------------------------------------------------------
# Git access
# ---------------------------------------------------------------------------


class GitUnavailable(RuntimeError):
    """Raised when git cannot answer — distinct from 'nothing changed'.

    The distinction is load-bearing. A watcher that maps "git failed" onto "no drift"
    goes quiet exactly when it is least able to justify doing so.
    """


def _git(args: list[str], *, root: Path) -> str:
    """Run a git command, returning stdout.

    Raises:
        GitUnavailable: git is missing, the repo is not a repo, or the command failed.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:            # git not installed
        raise GitUnavailable("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:    # pathological repo / hung filesystem
        raise GitUnavailable(f"git timed out: {' '.join(args)}") from exc
    if proc.returncode != 0:
        raise GitUnavailable(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _tracked_files(root: Path, ref: str) -> list[str]:
    """Every path tracked at ``ref``."""
    return [ln for ln in _git(["ls-tree", "-r", "--name-only", ref], root=root).splitlines() if ln]


#: Pathspecs per ``git log`` invocation. The sets are enumerated explicitly (one predicate,
#: one source of truth) rather than expressed as exclusion pathspecs, which would duplicate
#: the derivation in a second syntax and let the two drift. The cost is an argv that grows
#: with the tree, so calls are chunked: ~1000 paths at ~60 bytes is ~60 KB, comfortably
#: under the smallest ARG_MAX this runs on (~256 KB on macOS).
_PATHSPEC_CHUNK = 1000


def _chunks(paths: list[str], size: int = _PATHSPEC_CHUNK):
    """Yield ``paths`` in argv-safe batches."""
    for i in range(0, len(paths), size):
        yield paths[i : i + size]


def _last_commit_epoch(root: Path, ref: str, paths: list[str]) -> tuple[int | None, str]:
    """Return ``(committer_epoch, short_sha)`` of the newest commit touching any of ``paths``.

    Returns ``(None, "")`` when no commit in history touches them.

    Committer date, not author date: a rebased or cherry-picked commit carries an author
    date that can be arbitrarily old, and "when did this land on the branch" is the question.

    Chunked over ``paths`` and reduced by max, so the answer does not depend on how many
    batches it took.
    """
    best_epoch: int | None = None
    best_sha = ""
    for batch in _chunks(paths):
        line = _git(["log", "-1", "--format=%ct %h", ref, "--", *batch], root=root).strip()
        if not line:
            continue
        epoch_s, _, sha = line.partition(" ")
        epoch = int(epoch_s)
        if best_epoch is None or epoch > best_epoch:
            best_epoch, best_sha = epoch, sha.strip()
    return best_epoch, best_sha


def _now_epoch(root: Path) -> int:
    """Wall clock, as an int epoch. Split out so tests can pin it."""
    import time

    return int(time.time())


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """The evaluation outcome. Serialisable; carries no state between runs."""

    fires: bool
    reason: str
    window_hours: int
    docs_age_hours: float | None = None
    src_age_hours: float | None = None
    docs_last_sha: str = ""
    src_last_sha: str = ""
    doc_count: int = 0
    src_count: int = 0
    recent_source_paths: list[str] = field(default_factory=list)
    error: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "fires": self.fires,
                "reason": self.reason,
                "window_hours": self.window_hours,
                "docs_age_hours": self.docs_age_hours,
                "src_age_hours": self.src_age_hours,
                "docs_last_sha": self.docs_last_sha,
                "src_last_sha": self.src_last_sha,
                "doc_count": self.doc_count,
                "src_count": self.src_count,
                "recent_source_paths": self.recent_source_paths,
                "error": self.error,
            },
            indent=2,
            sort_keys=True,
        )


def evaluate(
    *,
    root: Path = ROOT,
    ref: str = "HEAD",
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now_epoch: int | None = None,
) -> Verdict:
    """Evaluate the trigger condition against ``ref``.

    Args:
        root: Repository root.
        ref: Git ref to evaluate. Production callers pass the default branch.
        window_hours: The freshness window. Documentation older than this while source
            is newer fires the procedure.
        now_epoch: Wall clock override, for tests. Defaults to the real clock.

    Returns:
        A :class:`Verdict`. On git failure the verdict does not fire and carries
        ``error`` — the caller is expected to surface that as "watcher broken", never
        to read it as "docs are fine".

    Raises:
        Never. Every failure is folded into the returned verdict, because a watcher that
        raises on an unusual repo is a watcher that stops watching.
    """
    now = _now_epoch(root) if now_epoch is None else now_epoch
    window_s = window_hours * 3600

    try:
        tracked = _tracked_files(root, ref)
    except GitUnavailable as exc:
        return Verdict(
            fires=False,
            reason="indeterminate — git unavailable",
            window_hours=window_hours,
            error=str(exc),
        )

    docs = sorted(p for p in tracked if _is_doc(p))
    srcs = sorted(p for p in tracked if _is_source(p))

    if not docs or not srcs:
        return Verdict(
            fires=False,
            reason=(
                "indeterminate — the tree yielded "
                f"{len(docs)} doc path(s) and {len(srcs)} source path(s); "
                "a set that collapses to zero means the derivation regressed, not that "
                "nothing changed"
            ),
            window_hours=window_hours,
            doc_count=len(docs),
            src_count=len(srcs),
            error="empty derived set",
        )

    try:
        docs_epoch, docs_sha = _last_commit_epoch(root, ref, docs)
        src_epoch, src_sha = _last_commit_epoch(root, ref, srcs)
    except GitUnavailable as exc:
        return Verdict(
            fires=False,
            reason="indeterminate — git unavailable",
            window_hours=window_hours,
            doc_count=len(docs),
            src_count=len(srcs),
            error=str(exc),
        )

    docs_age = None if docs_epoch is None else (now - docs_epoch) / 3600
    src_age = None if src_epoch is None else (now - src_epoch) / 3600

    common = dict(
        window_hours=window_hours,
        docs_age_hours=None if docs_age is None else round(docs_age, 2),
        src_age_hours=None if src_age is None else round(src_age, 2),
        docs_last_sha=docs_sha,
        src_last_sha=src_sha,
        doc_count=len(docs),
        src_count=len(srcs),
    )

    if src_epoch is None or (now - src_epoch) > window_s:
        return Verdict(
            fires=False,
            reason=f"source is also quiet (no source commit within {window_hours}h)",
            **common,
        )

    if docs_epoch is not None and (now - docs_epoch) <= window_s:
        return Verdict(
            fires=False,
            reason=f"documentation moved within {window_hours}h",
            **common,
        )

    return Verdict(
        fires=True,
        reason=(
            f"source moved within {window_hours}h "
            f"(most recent: {src_sha}) while documentation did not"
        ),
        recent_source_paths=_recent_source_paths(root, ref, now, window_s, srcs),
        **common,
    )


def _recent_source_paths(
    root: Path, ref: str, now: int, window_s: int, srcs: list[str]
) -> list[str]:
    """Best-effort: which **source** paths changed inside the window.

    Used only to make the alert actionable — it names what moved so a reader can judge
    which docs it implicates. A failure here degrades the message and nothing else, so it
    swallows :class:`GitUnavailable` deliberately: the verdict is already decided and must
    not be revised by a cosmetic query.
    """
    since = now - window_s
    found: set[str] = set()
    for batch in _chunks(srcs):
        try:
            out = _git(
                ["log", f"--since=@{since}", "--name-only", "--format=", ref, "--", *batch],
                root=root,
            )
        except GitUnavailable:
            return sorted(found)[:60]
        found.update(ln for ln in out.splitlines() if ln.strip())
    return sorted(found)[:60]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_markdown(v: Verdict) -> str:
    """Render a verdict as the body of an alert issue or a job summary."""
    lines: list[str] = []
    if v.error:
        lines += [
            "## Documentation freshness — INDETERMINATE",
            "",
            f"The watcher could not evaluate the condition: `{v.error}`",
            "",
            "**This is not a pass.** Treat it as the watcher being broken, not as the "
            "documentation being fresh. See "
            "`references/documentation-refresh.procedure.md`.",
        ]
        return "\n".join(lines) + "\n"

    head = "FIRED" if v.fires else "clear"
    lines += [
        f"## Documentation freshness — {head}",
        "",
        f"- window: **{v.window_hours}h**",
        f"- documentation age: **{_fmt_age(v.docs_age_hours)}** (last: `{v.docs_last_sha or 'n/a'}`)",
        f"- source age: **{_fmt_age(v.src_age_hours)}** (last: `{v.src_last_sha or 'n/a'}`)",
        f"- sets: {v.doc_count} doc path(s), {v.src_count} source path(s)",
        "",
        f"**Verdict:** {v.reason}",
    ]
    if v.fires:
        lines += [
            "",
            "### Source paths that moved inside the window",
            "",
        ]
        if v.recent_source_paths:
            lines += ["```", *v.recent_source_paths, "```"]
        else:
            lines += ["_(unavailable — the verdict stands regardless)_"]
        lines += [
            "",
            "### What to do",
            "",
            "Run the documentation-refresh procedure: "
            "`references/documentation-refresh.procedure.md`.",
            "",
            "Stage 1 is mechanical and scripted. Stage 2 is authored and routes to "
            "`@technical-validator` → `@agent-updater`.",
            "",
            "This alert is **advisory**. If the source changes genuinely needed no "
            "documentation update, say so and close it — the watcher will re-evaluate "
            "and reopen only if the condition is still true tomorrow.",
        ]
    return "\n".join(lines) + "\n"


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Returns:
        ``0`` always, except under ``--check``, which returns ``2`` when the condition
        fires. Never returns non-zero for an indeterminate evaluation — an unreachable
        git is a *watcher* fault and is reported through the ``error`` field, not through
        an exit code that a caller would read as "docs are stale".
    """
    ap = argparse.ArgumentParser(
        prog="docs_freshness_watch",
        description="Fire the documentation-refresh procedure when docs fall behind code.",
    )
    ap.add_argument("--ref", default="HEAD", help="Git ref to evaluate (default: HEAD)")
    ap.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW_HOURS,
        metavar="HOURS",
        help=f"Freshness window in hours (default: {DEFAULT_WINDOW_HOURS})",
    )
    ap.add_argument("--json", action="store_true", help="Emit the verdict as JSON")
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit 2 when the condition fires (opt-in; nothing in CI gates on this)",
    )
    ap.add_argument(
        "--out",
        metavar="PATH",
        help="Also write the markdown body to PATH (parent dirs created)",
    )
    args = ap.parse_args(argv)

    v = evaluate(root=ROOT, ref=args.ref, window_hours=args.window)

    body = render_markdown(v)
    sys.stdout.write(v.to_json() + "\n" if args.json else body)

    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(body, encoding="utf-8")
        except OSError as exc:
            # A failed side-channel write must not change the verdict or the exit code.
            print(f"[warn] could not write {out}: {exc}", file=sys.stderr)

    if args.check and v.fires:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
