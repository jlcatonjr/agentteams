"""env_hygiene.py — every ``.env`` file is gitignored, and kept out of Docker build contexts.

Backs security Rule S-1's env-file bullets (``templates/universal/security.template.md``) and the
pre-commit env-file guard in :mod:`agentteams.git_hooks`. A rule that only fires on file *content*
catches a secret after it is staged; this checks the precondition, that an env file can't be staged
by accident in the first place.

Findings use :class:`agentteams.scan.ScanFinding`, so :func:`agentteams.scan.verdict_for_findings`
gives the HALT / CONDITIONAL_PASS / PASS verdict:

- ``tracked-env-file`` (high): git tracks an env file. Untracking it (``git rm --cached``) is a manual,
  reviewed step. A deploy may read the file, so this module never untracks.
- ``unignored-env-file`` (high): an env file on disk that ``git add .`` would stage.
- ``dockerignore-env`` (medium): a Dockerfile copies its whole build context (``COPY .``, ``COPY *``,
  the JSON-array form, continuation lines) and neither the root ``.dockerignore`` nor BuildKit's
  ``<Dockerfile>.dockerignore`` excludes every env-file shape (``.env``, ``.env.*``, ``*.env``), so a
  local ``docker build`` bakes them into the image. The build context is assumed to be the repo root.

``python -m agentteams.env_hygiene REPO [REPO ...] [--fix [--execute]]`` prints JSON and exits 1 when
any repository's verdict is HALT. ``--fix`` only *appends* missing ignore lines: always to the root ``.gitignore``, and to the
root ``.dockerignore`` when a Dockerfile is flagged. It is a dry run unless ``--execute`` is given, and it refuses a file with
uncommitted changes, because git is the snapshot. Git runs read-only and hardened (:mod:`agentteams.git_exec`).

Known limit: archives built from a working tree outside Docker (e.g. a Terraform ``archive_file``
``source_dir``) are not checked.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agentteams.git_exec import run_git
from agentteams.scan import HALT, ScanFinding, verdict_for_findings

#: A name component that marks a committed placeholder template, not a real env file.
TEMPLATE_MARKERS = frozenset({"example", "sample", "template"})

#: Lines ``--fix`` makes sure are in the repository-root ``.gitignore``.
GITIGNORE_LINES = (".env", ".env.*", "*.env", "!.env.example", "!.env.sample", "!.env.template",
                   "!*.example.env", "!*.sample.env", "!*.template.env")
#: Lines ``--fix`` makes sure are in a flagged ``.dockerignore``.
DOCKERIGNORE_LINES = (".env", ".env.*", "*.env", "**/.env", "**/.env.*", "**/*.env")

_FIX_HEADER = "# env files are never committed or copied into images (agentteams env_hygiene)"


def is_env_file(name: str) -> bool:
    """Return True when a file *name* (basename) is an env file that must not be committed.

    Matches ``.env``, ``.env.<suffix>`` and ``<name>.env``. Exempts placeholder templates, meaning any
    name with an ``example``/``sample``/``template`` component (``.env.example``, ``dev.example.env``).
    Never matches ``.envrc``, ``environment.yml`` or an ``env/`` directory.

    Args:
        name: A file basename (a path is reduced to its last component).

    Returns:
        Whether the name is an env file.

    Raises:
        Nothing.
    """
    base = PurePosixPath(name).name
    if not (base == ".env" or base.startswith(".env.") or (base.endswith(".env") and len(base) > 4)):
        return False
    return not (set(base.lower().split(".")) & TEMPLATE_MARKERS)


@dataclass
class FixPlan:
    """Ignore lines ``--fix`` would append, per file (repository-relative path → lines)."""
    appends: dict[str, list[str]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)


def _git_lines(repo: Path, *args: str) -> list[str]:
    proc = run_git(repo, *args, hardened=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if p]


def _dockerfiles(tracked: list[str]) -> list[str]:
    return [p for p in tracked
            if (n := PurePosixPath(p).name) == "Dockerfile" or n.startswith("Dockerfile.") or n.endswith(".Dockerfile")]


#: A COPY/ADD source that brings in the whole build context (``*`` matches dotfiles in Docker).
_WHOLE_CONTEXT_SOURCES = frozenset({".", "./", "*", "./*"})


def _copies_whole_context(text: str) -> bool:
    joined = re.sub(r"\\\n", " ", text)  # a trailing backslash continues the instruction
    for line in joined.splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2 or parts[0].upper() not in ("COPY", "ADD"):
            continue
        rest = " ".join(a for a in parts[1].split() if not a.startswith("--"))  # drop --chown= etc.
        srcs = rest.split()[:-1]
        if rest.startswith("["):
            try:
                srcs = json.loads(rest)[:-1]
            except ValueError:  # Docker treats a COPY whose JSON won't parse as the shell form
                srcs = rest.split()[:-1]
        if any(src in _WHOLE_CONTEXT_SOURCES for src in srcs):
            return True
    return False


#: Representative names a ``.dockerignore`` must exclude: one per env-file shape.
_DOCKERIGNORE_PROBES = (".env", ".env.production", "app.env")


def _dockerignore_excludes_env(path: Path) -> bool:
    if not path.is_file():
        return False
    pats = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        pat = raw.strip()
        if not pat or pat.startswith("#") or pat.startswith("!"):
            continue
        pat = pat.lstrip("/")
        pats.append(pat[3:] if pat.startswith("**/") else pat)
    return all(any(fnmatch.fnmatch(probe, pat) for pat in pats) for probe in _DOCKERIGNORE_PROBES)


def audit_repo(repo: Path) -> list[ScanFinding]:
    """Audit one git repository for env-file ignore hygiene.

    Args:
        repo: The repository root.

    Returns:
        The findings (empty when clean).

    Raises:
        RuntimeError: When a git command fails (e.g. *repo* is not a git repository).
    """
    findings: list[ScanFinding] = []
    tracked = _git_lines(repo, "ls-files", "-z")
    for p in tracked:
        if is_env_file(p):
            findings.append(ScanFinding(p, 0, "tracked-env-file", "high",
                                        "env file is tracked by git; untrack it (git rm --cached) after "
                                        "moving anything a deploy reads into the deploy config", p))
    for p in _git_lines(repo, "ls-files", "-z", "--others", "--exclude-standard"):
        if is_env_file(p):
            findings.append(ScanFinding(p, 0, "unignored-env-file", "high",
                                        "env file is not gitignored; `git add .` would commit it", p))
    for df in _dockerfiles(tracked):
        df_path = repo / df
        if not df_path.is_file():
            continue
        if not _copies_whole_context(df_path.read_text(encoding="utf-8", errors="replace")):
            continue
        # The build context is assumed to be the repository root (the usual `docker build .` and
        # CI checkout). BuildKit prefers <Dockerfile>.dockerignore next to the Dockerfile.
        ctx_ignores = (df_path.with_name(df_path.name + ".dockerignore"), repo / ".dockerignore")
        if not any(_dockerignore_excludes_env(p) for p in ctx_ignores):
            findings.append(ScanFinding(df, 0, "dockerignore-env", "medium",
                                        "Dockerfile copies its whole build context but no .dockerignore "
                                        "excludes .env; a local docker build would bake it into the image", df))
    return findings


def _missing(path: Path, wanted: tuple[str, ...]) -> list[str]:
    have = set()
    if path.is_file():
        have = {ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()}
    return [ln for ln in wanted if ln not in have]


def plan_fix(repo: Path, findings: list[ScanFinding]) -> FixPlan:
    """Work out the ignore lines to append. Never untracks a file.

    The root ``.gitignore`` always gets any missing :data:`GITIGNORE_LINES`; a ``.dockerignore`` is
    planned at the repository root (the assumed build context) when a Dockerfile is flagged
    ``dockerignore-env``.

    Args:
        repo: The repository root.
        findings: :func:`audit_repo` output for *repo*.

    Returns:
        The plan: lines to append per file, and files refused because they have uncommitted changes.

    Raises:
        RuntimeError: When a git command fails.
    """
    plan = FixPlan()
    # The root .gitignore always gets the full rule, finding or not: the point is that the next
    # env file someone creates is ignored before anyone can stage it.
    targets: dict[str, tuple[str, ...]] = {".gitignore": GITIGNORE_LINES}
    if any(f.category == "dockerignore-env" for f in findings):
        targets[".dockerignore"] = DOCKERIGNORE_LINES  # the root build context's ignore file
    for rel, wanted in targets.items():
        lines = _missing(repo / rel, wanted)
        if not lines:
            continue
        if run_git(repo, "status", "--porcelain", "--", rel, hardened=True).stdout.strip():
            plan.refused[rel] = "has uncommitted changes; commit or stash them first"
            continue
        plan.appends[rel] = lines
    return plan


def apply_fix(repo: Path, plan: FixPlan) -> FixPlan:
    """Append the planned lines (the ``--execute`` path).

    Args:
        repo: The repository root.
        plan: A :func:`plan_fix` result.

    Returns:
        *plan*, with ``written`` listing the files changed.

    Raises:
        OSError: When a file can't be written.
    """
    for rel, lines in plan.appends.items():
        path = repo / rel
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        sep = "" if not existing or existing.endswith("\n") else "\n"
        block = ("\n" if existing else "") + _FIX_HEADER + "\n" + "\n".join(lines) + "\n"
        path.write_text(existing + sep + block, encoding="utf-8")
        plan.written.append(rel)
    return plan


def main(argv: list[str] | None = None) -> int:
    """``python -m agentteams.env_hygiene REPO [REPO ...] [--fix [--execute]]``.

    Prints ``{"repos": [{"repo", "findings", "verdict", "fix"?}], "verdict"}``. With ``--execute``
    the findings are re-audited after the fix, so the verdict reflects the result.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        1 when any repository's verdict is HALT (an unauditable repository counts as HALT), else 0.

    Raises:
        SystemExit: Exit code 2 on a usage error (``--execute`` without ``--fix``).
    """
    parser = argparse.ArgumentParser(prog="python -m agentteams.env_hygiene")
    parser.add_argument("repos", nargs="+", type=Path, help="git repository roots to audit")
    parser.add_argument("--fix", action="store_true", help="plan appending missing ignore lines (dry run)")
    parser.add_argument("--execute", action="store_true", help="with --fix: write the planned lines")
    args = parser.parse_args(argv)
    if args.execute and not args.fix:
        parser.error("--execute requires --fix")

    out, worst = [], []
    for repo in args.repos:
        entry: dict[str, object] = {"repo": str(repo)}
        try:
            findings = audit_repo(repo)
        except (RuntimeError, OSError) as exc:  # one unreadable repo must not hide the others' results
            err = ScanFinding(str(repo), 0, "audit-error", "high", f"could not audit: {exc}", "")
            worst.append(err)
            entry.update(findings=[err.__dict__], verdict=HALT)
            out.append(entry)
            continue
        if args.fix:
            plan = plan_fix(repo, findings)
            if args.execute:
                apply_fix(repo, plan)
                findings = audit_repo(repo)
            entry["fix"] = {"dry_run": not args.execute, "appends": plan.appends,
                            "refused": plan.refused, "written": plan.written}
        verdict = verdict_for_findings(findings)
        worst.extend(findings)
        entry.update(findings=[f.__dict__ for f in findings], verdict=verdict)
        out.append(entry)
    overall = verdict_for_findings(worst)
    json.dump({"repos": out, "verdict": overall}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if overall == HALT else 0


if __name__ == "__main__":
    raise SystemExit(main())
