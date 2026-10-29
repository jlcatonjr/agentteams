#!/usr/bin/env python3
"""build_launcher_lineage.py — generate the sandbox launcher's version lineage from git history.

The lineage (``agentteams/data/launcher_lineage.json``) is how ``agentteams.launcher_lineage`` tells a derived copy
of ``sandbox/confine-run.sh`` apart (current / older / modified / missing) without a version stamp in the launcher
(consumers pin its sha256, so its bytes never change for a stamp). It is never hand-edited: this script derives it
from git objects only, deterministically, and ``tests/test_launcher_lineage.py`` regenerates it and requires
equality, and requires its newest version to be the template on disk.

What is collected: one entry per commit in ``HEAD``'s history that changed the template, following renames
(``git log --follow``), oldest first, each with the sha256 of the template's bytes at that commit, the commit, its
committer date (ISO 8601), the path the template had there, the commit subject, and ``security``.

**The ``security`` rule.** An entry is ``security: true`` when either

* its subject matches :data:`SECURITY_SUBJECT` — a ``fix(sandbox…)``, ``fix(launcher…)`` or ``fix(security…)``
  conventional-commit subject; or
* its commit is in :data:`SECURITY_REVIEWED` — commits reviewed by hand at build time because their subject does
  not say so but their change to the template adds or tightens a deny rule (a protected control-plane path, a
  protected team dir or example, the default-deny environment, prompt-root protection).

Every other entry (a feature, a message-only or portability change, a widening such as ``--coord-root``) is
``security: false``. A new template commit whose subject does not match is ``false`` until it is reviewed and,
if it tightens a deny rule, added to :data:`SECURITY_REVIEWED`.

**History availability.** A shallow repository lacks the older versions, and a lineage built from it would call
every older copy ``modified``; the script refuses (exit 2) rather than write it. ``HEAD`` is walked rather than
``origin/main`` so that a branch that changes the template can regenerate the lineage in the same change (its
history contains every released version: released history is ``main``'s, and a branch starts from it).

Usage::

    python scripts/build_launcher_lineage.py            # rewrite the lineage
    python scripts/build_launcher_lineage.py --check    # exit 1 when the shipped lineage differs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.git_exec import run_git  # noqa: E402
from agentteams.launcher_lineage import LINEAGE_PATH  # noqa: E402

#: The template, as a path relative to the repository root.
TEMPLATE_REL = "agentteams/templates/universal/sandbox/confine-run.sh"
#: Subjects that mark a security fix (see the module docstring).
SECURITY_SUBJECT = re.compile(r"^fix\((sandbox|launcher|security)\b[^)]*\)!?:", re.IGNORECASE)
#: Commits reviewed by hand whose template change tightens a deny rule though the subject does not say so.
SECURITY_REVIEWED: dict[str, str] = {
    "38132d0c0553fd855144674c0a37c1dde0dafeea": "default-deny guest environment (no signing key leaks in)",
    "9d409533f9e30c30c48140b4b0c63b013f378cae": "opt-in prompt-root protection",
    "410825708cf0d75201c7ce66ff15d4cdeef9c91b": "protects .codex/confined-run.example.sh and AGENTS.override.md",
    "b8a0de4e6be71b539c1870a4caae84bb02029f5f": "protects the .grok team dir and its trust roots",
    "9c1ee1122b2b0b38067ccf82b9772c62f3c6197d": ".grok is a read-only control plane; planted config warned",
    "323b60a93132654ad1d8f98b333513208f63740f": "reports .grok hooks that agentteams did not write",
    "761d944067c1afd02c7fbcc266cc2bc2be240b13": "protects .grok/confined-run.example.sh",
}
_LINEAGE_VERSION = 1


def _git(*args: str, text: bool = True) -> str | bytes:
    """Run a read-only git command in the repository and return stdout; raise on failure."""
    proc = run_git(REPO_ROOT, *args, hardened=True, timeout=120, text=text)
    if proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
    return proc.stdout


def is_shallow() -> bool:
    """Return whether this repository is a shallow clone (or git cannot tell).

    Returns:
        True when shallow or when ``git rev-parse --is-shallow-repository`` fails.

    Raises:
        Nothing.
    """
    proc = run_git(REPO_ROOT, "rev-parse", "--is-shallow-repository", hardened=True, timeout=30)
    return proc.returncode != 0 or proc.stdout.strip() != "false"


def _security(sha: str, subject: str) -> bool:
    return bool(SECURITY_SUBJECT.match(subject)) or sha in SECURITY_REVIEWED


def build() -> dict:
    """Derive the lineage payload from git history.

    Returns:
        The JSON-serialisable lineage.

    Raises:
        RuntimeError: When the repository is shallow, the template has no history, or a git command fails.
        subprocess.SubprocessError: When a git command times out.
    """
    if is_shallow():
        raise RuntimeError("shallow repository: the lineage needs the full history (git fetch --unshallow)")
    out = _git("log", "--follow", "--name-only", "--format=%x1e%H%x1f%cI%x1f%s", "HEAD", "--", TEMPLATE_REL)
    versions = []
    for record in str(out).split("\x1e")[1:]:
        head, _, names = record.partition("\n")
        sha, date, subject = head.split("\x1f", 2)
        paths = [n for n in names.splitlines() if n.strip()]
        if not paths:
            continue
        path = paths[-1]
        blob = _git("cat-file", "blob", f"{sha}:{path}", text=False)
        versions.append({"commit": sha, "date": date, "path": path, "subject": subject,
                         "sha256": hashlib.sha256(bytes(blob)).hexdigest(), "security": _security(sha, subject)})
    if not versions:
        raise RuntimeError(f"no history for {TEMPLATE_REL}")
    versions.reverse()   # git log is newest first
    return {
        "version": _LINEAGE_VERSION,
        "generator": "scripts/build_launcher_lineage.py",
        "note": ("Generated from git history; never edit by hand. One entry per commit that changed the sandbox "
                 "launcher template, oldest first; the newest is the shipped template. agentteams.launcher_lineage "
                 "reads it to tell a derived launcher copy current / older / modified / missing."),
        "template": TEMPLATE_REL,
        "versions": versions,
    }


def serialise(payload: dict) -> str:
    """Return the canonical text of *payload* (sorted keys, fixed indent, trailing newline).

    Args:
        payload: A lineage payload from :func:`build`.

    Returns:
        The exact text written to the lineage file.

    Raises:
        Nothing.
    """
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Rewrite the lineage, or with ``--check`` compare it to a fresh build.

    Args:
        argv: Command-line arguments (``sys.argv[1:]`` when None).

    Returns:
        0 on success / match, 1 when ``--check`` finds a difference, 2 when the history is unusable.

    Raises:
        Nothing.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the shipped lineage is stale")
    args = parser.parse_args(argv)
    try:
        text = serialise(build())
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(f"build_launcher_lineage: {exc}", file=sys.stderr)
        return 2
    if args.check:
        current = LINEAGE_PATH.read_text(encoding="utf-8") if LINEAGE_PATH.exists() else ""
        if current != text:
            print("launcher lineage is stale: run scripts/build_launcher_lineage.py", file=sys.stderr)
            return 1
        print("launcher lineage is current")
        return 0
    LINEAGE_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {LINEAGE_PATH.relative_to(REPO_ROOT)} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
