"""Remediate vulnerable dependency versions in a working tree. Dry run unless ``apply``.

- **requirements*.txt**: each vulnerable ``==`` pin moves to the *smallest* PyPI release that
  (a) OSV reports as clean, (b) was uploaded at least ``cooldown_days`` ago (0 by default: Rule S-10's
  remediation carve-out exempts a fix for a scanned vulnerability from the release cooldown and the
  latest-stable rule), and (c) is neither a pre-release nor fully yanked. A major-version jump
  is proposed but written only with ``allow_major``. A pin whose current version is
  **malicious** (OSV ``MAL-*``) is never edited: that is an incident, not an upgrade. Line
  endings and every other byte of the file are preserved. A file that pins ``--hash`` values is
  held back (``hash-held``) rather than left with a stale hash, and a symlinked manifest is
  refused, never followed.
- **npm lockfiles**: ``npm audit fix --package-lock-only --ignore-scripts`` (with ``--before=<cutoff>``
  only for a positive ``cooldown_days``). It touches the lockfile only and runs no install scripts. Semver-major fixes
  are never forced (no ``--force``); with ``allow_major`` (the executor's ``stage-major``) a ``major-held`` package that
  is a direct dependency has its ``package.json`` spec raised to ``^<fixed version>`` and the lockfile re-resolved with
  ``npm install --package-lock-only --ignore-scripts`` first (:mod:`.npm_major`; transitive-only majors and npm
  workspaces stay held). What the audit fix leaves vulnerable inside its range is retried by moving its exact-peer
  group or direct parent (``npm update --no-save``, :func:`.npm_ops.npm_moves`). It runs on a
  scratch copy with the public registry and TLS verification fixed, never reading the
  repository's ``.npmrc``. A dry run also lists each vulnerable package of the lockfile from
  the advisories (:func:`plan_npm_lock`); npm, not this module, picks the versions it writes.
- **composer.lock**: guidance only (``composer update <pkg> --no-install --no-scripts --no-plugins``).
- **Dockerfiles and compose files**: plan only (:mod:`.images`): each base image that is end-of-life, a pinned
  patch tag behind its line, unpinned (``latest``) or deprecated, with a supported tag to move ``FROM`` / ``image:``
  to. Listed in a dry run only; never applied, and ``executor.validate_diff`` refuses a change to these files.
- **Cargo.lock**: plan only (:func:`plan_cargo_lock`): each vulnerable crate with the smallest version the
  advisories call fixed and the command ``cargo update -p <name>@<installed> --precise <version>`` for a person
  to run. It is never applied, and ``stage-fix`` cannot stage it (``executor.validate_diff`` refuses a
  ``Cargo.lock`` change).
- **go.mod, pnpm-lock.yaml, bun.lock, Gemfile.lock, gradle.lockfile, packages.lock.json**: plan only too
  (:func:`plan_lockfile`), with the command each tool takes (:data:`lockfiles.FORMATS`); ``executor.validate_diff``
  refuses a change to any of them.

With ``apply``, every manifest a change touches is re-read against OSV before and after, and
:attr:`FixResult.resolved` counts the advisory occurrences the change removed: npm can move a package within
its range to a release that is still affected, a change that fixes nothing (``stage-fix`` refuses it).

This module never commits, pushes or opens PRs. Point it at a worktree you control.
``daily`` does that in its ``apply-worktree`` mode. A file with uncommitted changes is
refused unless ``allow_dirty`` (checked with the executor's hardened git, so the tree's own git
config runs nothing).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.vulnscan import _http, images, lockfiles, npm_groups, npm_major, npm_ops, open_prs, osv, private_sources, project_policy, subproc_confine
from agentteams.vulnscan.executor_env import ExecError   # leaf module: no import of the executor
from agentteams.vulnscan.manifests import CRATE_LOCKS, REQ_NAME, REQ_PIN, discover, npm_off_registry, parse
from agentteams.vulnscan.npm_ops import has_workspaces as _npm_workspaces, semver_line as _semver_line
from agentteams.vulnscan.versions import is_final, major, version_key

PYPI = "https://pypi.org/pypi"
NPM_REGISTRY = "https://registry.npmjs.org/"    # fixed: a repository .npmrc cannot redirect the unattended fix
NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json")
#: Heads every Cargo.lock section of a fix plan (agents read it through ``get_fix_plans``).
CARGO_NOTE = ("Plan-only: `stage-fix` never edits a `Cargo.lock` (the executor refuses the change), so do not "
              "request `stage-fix` for these rows; report them for a person to run the command shown, then "
              "`cargo build` and the tests. \"→\" is the smallest version the advisories call fixed.")
#: Heads every base-image section of a fix plan.
IMAGE_NOTE = ("Plan-only: `stage-fix` never edits a Dockerfile or compose file (the executor refuses the change), so "
              "do not request `stage-fix` for these rows; report them for a person to change `FROM <ref>` (or "
              "`image:`) to a supported line, e.g. the tag under \"→\", then rebuild and run the tests. The image "
              "contents are not scanned.")
#: What a crates.io crate name or a Cargo.lock version may be before a command is written with it (the lockfile is
#: repository content: a name that could break the command or the table gets no command).
_CRATE_SPEC = re.compile(r"[A-Za-z0-9_.+-]{1,64}")


def lockfile_note(name: str) -> str:
    """Return the note heading a plan-only section of one :data:`lockfiles.FORMATS` lockfile (like :data:`CARGO_NOTE`).

    Args:
        name: The lockfile's name (``go.mod``, ``pnpm-lock.yaml``, ...).

    Returns:
        The note.
    """
    return (f"Plan-only: `stage-fix` never edits a `{name}` (the executor refuses the change), so do not request "
            "`stage-fix` for these rows; report them for a person to run the command shown, then "
            f"{lockfiles.FORMATS[name].then}. \"→\" is the smallest version the advisories call fixed.")


def _plain(text: str) -> str:
    """Return ``text`` for a fix-plan table cell, or ``(unusual)`` when it is not plain crate-name / version text."""
    return text if _CRATE_SPEC.fullmatch(text) else "(unusual)"


@dataclass
class PinChange:
    """One proposed or applied requirements edit."""

    path: str
    name: str
    old: str
    new: str = ""
    # fix | major-held | no-clean-release | cooldown-held | hash-held | malicious | pr-held (an open PR that is
    # not the executor's already updates it, ``skip``); npm rows (plan_npm_lock)
    # also workspace-held | npm-unavailable; Cargo.lock rows (plan_cargo_lock) plan-only | no-clean-release | malicious
    action: str = "fix"
    ids: list[str] = field(default_factory=list)
    note: str = ""
    major: bool = False   # a semver-major upgrade taken under ``allow_major`` (the executor's ``stage-major``)


@dataclass
class FixResult:
    """Everything ``fix_path`` decided or did."""

    changes: list[PinChange] = field(default_factory=list)
    npm_plan: list[PinChange] = field(default_factory=list)        # dry run: per package, from the advisories
    npm_notes: dict[str, str] = field(default_factory=dict)        # lockfile -> what stage-fix does there
    npm: list[tuple[str, str]] = field(default_factory=list)       # (dir, summary)
    composer: list[str] = field(default_factory=list)
    cargo_plan: list[PinChange] = field(default_factory=list)      # Cargo.lock: plan-only, never applied or staged
    lock_plan: list[PinChange] = field(default_factory=list)       # lockfiles.FORMATS files: plan-only, likewise
    image_plan: list[PinChange] = field(default_factory=list)      # base images: plan-only, dry run only
    refused: list[tuple[str, str]] = field(default_factory=list)   # (path, reason)
    feed_errors: list[str] = field(default_factory=list)
    applied: bool = False
    target: str = ""           # the git repository the plan is for (the ``stage-fix`` target), when known
    target_problem: str = ""   # why ``stage-fix`` cannot take ``target`` (e.g. not a git repository)
    # advisory occurrences the applied changes removed; None: not measured (a dry run, or a changed manifest
    # without exact versions)
    resolved: int | None = None

    def exit_code(self) -> int:
        """Return the shared exit code for a fix run.

        Returns:
            4 if a malicious pin was found, 3 if a feed failed, 1 if anything was held back or
            refused (major jump, cooldown, no clean release, dirty file, a plan-only Cargo.lock or base-image
            row), else 0.
        """
        rows = self.changes + self.npm_plan + self.cargo_plan + self.lock_plan + self.image_plan
        if any(c.action == "malicious" for c in rows):
            return 4
        if self.feed_errors:
            return 3
        if self.refused or any(c.action != "fix" for c in rows):
            return 1
        return 0

    @staticmethod
    def _code(text: object) -> str:
        """``text`` as an inline code span that cannot break its line or span (repository paths are untrusted)."""
        return "`" + re.sub(r"[\x00-\x1f\x7f`]", "?", str(text)) + "`"

    def markdown(self) -> str:
        """Render for people.

        Returns:
            Markdown text.
        """
        out = [f"# Fix plan ({'APPLIED' if self.applied else 'dry run'})", ""]
        if self.target and self.target_problem:
            out += [f"Directory {self._code(self.target)}: `stage-fix` cannot take it ({self.target_problem}).", ""]
        elif self.target:
            out += [f"Repository (the `stage-fix` target): {self._code(self.target)}", ""]
        head = len(out)
        if self.changes:
            out += ["| File | Package | Installed | → | Action | Notes |", "|---|---|---|---|---|---|"]
            for c in self.changes:
                out.append(f"| {self._code(c.path)} | {c.name} | {c.old} | {c.new or '—'} | {c.action} | {c.note} |")
            out.append("")
        for lock in dict.fromkeys(c.path for c in self.npm_plan):
            out += [f"## npm: {self._code(lock)}", "", self.npm_notes.get(lock, ""), "",
                    "| Package | Installed | → | Action | Notes |", "|---|---|---|---|---|"]
            out += [f"| {c.name} | {c.old} | {'≥ ' + c.new if c.new else '—'} | {c.action} | {c.note} |"
                    for c in self.npm_plan if c.path == lock]
            out.append("")
        for d, summary in self.npm:
            out += [f"## npm: {self._code(d)}", "", "```", summary.strip()[-3000:], "```", ""]
        if self.composer:
            out += ["## composer", ""] + [f"- {c}" for c in self.composer] + [""]
        for lock in dict.fromkeys(c.path for c in self.cargo_plan):
            out += [f"## crates.io: {self._code(lock)} — plan-only (no staged edit)", "", CARGO_NOTE, "",
                    "| Crate | Installed | → | Action | Command / notes |", "|---|---|---|---|---|"]
            out += [f"| {_plain(c.name)} | {_plain(c.old)} | {c.new or '—'} | {c.action} | {c.note} |"
                    for c in self.cargo_plan if c.path == lock]
            out.append("")
        for lock in dict.fromkeys(c.path for c in self.lock_plan):
            fmt_name = str(lock).replace("\\", "/").rsplit("/", 1)[-1]
            fmt = lockfiles.FORMATS[fmt_name]
            out += [f"## {fmt.section}: {self._code(lock)} — plan-only (no staged edit)", "", lockfile_note(fmt_name),
                    "", "| Package | Installed | → | Action | Command / notes |", "|---|---|---|---|---|"]
            out += [f"| {_cell(c.name, fmt.name)} | {_cell(c.old, fmt.version)} | {_cell(c.new, fmt.version) or '—'} "
                    f"| {c.action} | {c.note} |" for c in self.lock_plan if c.path == lock]
        for df in dict.fromkeys(c.path for c in self.image_plan):
            out += [f"## base images: {self._code(df)} — plan-only (no staged edit)", "", IMAGE_NOTE, "",
                    "| Image | Tag | → | Action | Notes |", "|---|---|---|---|---|"]
            out += [f"| {c.name} | {c.old} | {self._code(c.new) if c.new else '—'} | {c.action} | {c.note} |"
                    for c in self.image_plan if c.path == df]
            out.append("")
        if self.refused:
            out += ["## Refused", ""] + [f"- `{p}`: {r}" for p, r in self.refused] + [""]
        if self.feed_errors:
            out += ["## Not checked (feed unavailable)", ""] + [f"- {e}" for e in self.feed_errors] + [""]
        if len(out) == head:
            out.append("Nothing to change.")
        return "\n".join(out)


def _releases(name: str) -> list[tuple[str, _dt.datetime]]:
    data = _http.fetch_json(f"{PYPI}/{name}/json")
    out = []
    for ver, files in ((data or {}).get("releases") or {}).items():
        if not files or not is_final(ver) or all(f.get("yanked") for f in files):
            continue
        stamps = [f.get("upload_time_iso_8601") for f in files if f.get("upload_time_iso_8601")]
        if stamps:
            out.append((ver, min(_dt.datetime.fromisoformat(s.replace("Z", "+00:00")) for s in stamps)))
    return sorted(out, key=lambda r: version_key(r[0]))


def choose_version(name: str, current: str, *, cooldown_days: int = 0,
                   now: _dt.datetime | None = None) -> tuple[str, str]:
    """Pick the smallest clean, cooldown-aged release above ``current``.

    Args:
        name: PyPI project name.
        current: Pinned version.
        cooldown_days: Minimum release age in days (0: none).
        now: Clock override for tests.

    Returns:
        (version, note). ``version`` is "" when nothing qualifies; the note then says why
        (and names a clean release still inside the cooldown, if any).

    Raises:
        FeedError: PyPI or OSV could not be read.
    """
    now = now or _dt.datetime.now(_dt.UTC)
    cands = [(v, t) for v, t in _releases(name) if version_key(v) > version_key(current)]
    if not cands:
        return "", "no newer final release on PyPI"
    bad = osv.vulnerable_ids(("PyPI", name, v) for v, _ in cands)
    clean = [(v, t) for v, t in cands if ("PyPI", name, v) not in bad]
    if not clean:
        return "", "every newer release still has advisories"
    aged = [(v, t) for v, t in clean if now - t >= _dt.timedelta(days=cooldown_days)]
    if aged:
        return aged[0][0], ""
    young = clean[0]
    return "", f"clean release {young[0]} is younger than the requested {cooldown_days}-day cooldown"


def _dirty(path: Path) -> bool:
    """Return whether ``path`` has uncommitted changes, asking git hardened as the executor runs it (X3).

    The tree may be untrusted and git finds its ``.git`` upwards, so repository config such as
    ``core.fsmonitor`` or hooks must not run: the executor's git command line and environment prevent that.
    """
    if not shutil.which("git"):
        return False
    from agentteams.vulnscan import executor   # lazy: the executor imports this module

    proc = executor.query_git(path.parent, "status", "--porcelain", "--ignore-submodules=all", "--", path.name)
    return proc.returncode == 0 and bool(proc.stdout.strip())


#: git-lfs's standard filter config, exactly as ``git lfs install`` writes it: the one filter driver git may run in a
#: repository the executor touches (plan C2). The executor's git environment sets ``GIT_LFS_SKIP_SMUDGE=1``, so a
#: checkout writes LFS pointer files and nothing is downloaded; the executor never edits LFS-tracked files.
LFS_FILTER = {"filter.lfs.clean": "git-lfs clean -- %f", "filter.lfs.smudge": "git-lfs smudge -- %f",
              "filter.lfs.process": "git-lfs filter-process"}
#: git-lfs settings refused alongside the filter (a POSIX ERE on config key names): extensions run a program on
#: clean/smudge, custom transfer agents (any key, and the URL-scoped ``lfs.<url>.standalonetransferagent`` form) run one
#: on a transfer, and ``lfs.storage`` moves where clean writes objects, possibly outside the repository.
LFS_REFUSED_KEYS = r"^lfs\.(extension|customtransfer)\.|standalonetransferagent$|^lfs\.storage$"
#: Conditional includes (a POSIX ERE on config key names, which git lowercases to ``includeif.``): refused, because
#: their condition is evaluated where git runs, not where :func:`git_config_problem` looked (security follow-up, #311).
CONDITIONAL_INCLUDE = r"^includeif\."


def lfs_available() -> bool:
    """Return whether git-lfs can run where the executor's git runs: the tools container has it (its image installs
    it and ``tools-selftest`` checks it); on the host it must be on ``PATH``."""
    from agentteams.vulnscan import subproc_confine   # lazy: only repo ops ask

    return subproc_confine.active() is not None or shutil.which("git-lfs") is not None


def git_config_problem(directory: Path) -> str:
    """Return why git may not run in ``directory``'s repository, or "" (none, or not a repository).

    The hardened git turns off hooks and ``core.fsmonitor`` by name, but a ``filter.<name>.clean`` /
    ``.process`` / ``.smudge`` driver has an arbitrary name and runs under ``git status`` or a checkout when
    ``.gitattributes`` selects it. Global and system config are off in :func:`executor.query_git`, so any driver
    seen here comes from the repository's own config (including its ``include`` files). The only drivers allowed
    are git-lfs's standard ones, value for value (:data:`LFS_FILTER`), when git-lfs is available
    (:func:`lfs_available`) and no refused git-lfs setting is present (:data:`LFS_REFUSED_KEYS`). A repository git
    refuses to open (e.g. ``safe.directory``) is a problem too: its dirty check would otherwise read as clean.

    A conditional include (``includeIf.*``, :data:`CONDITIONAL_INCLUDE`) is refused outright: its condition
    (``gitdir:``, ``onbranch:``, ``hasconfig:``) is evaluated where git runs, so a driver it adds can be absent here
    and present in the executor's worktree (``gitdir:**/worktrees/**``) or on its branch (``onbranch:vulnscan/**``).
    Plain ``include.path`` resolves to the same file everywhere, so what it adds is checked here like the rest (a
    target outside the repository, e.g. ``~/.gitconfig``, is read as it is at check time; :func:`.executor.stage_fix`
    checks again just before its checkout). A config query git fails (not "no match") is a refusal too.

    Args:
        directory: A directory inside the tree (git finds the repository upwards).

    Returns:
        A refusal reason, or "".
    """
    if not shutil.which("git"):
        return ""
    from agentteams.vulnscan import executor   # lazy: the executor imports this module

    probe = executor.query_git(directory, "rev-parse", "--git-dir")
    if probe.returncode:
        if "not a git repository" in probe.stderr:
            return ""
        return f"git refuses to read this repository ({probe.stderr.strip()[:200]}) — fix it by hand"
    cond = executor.query_git(directory, "config", "--name-only", "--get-regexp", CONDITIONAL_INCLUDE)
    if cond.returncode not in (0, 1):   # 1: no match; anything else (an unreadable config) refuses
        return f"git cannot read this repository's config ({cond.stderr.strip()[:200]}) — fix it by hand"
    if cond.returncode == 0 and cond.stdout.strip():
        return ("the repository's git config has a conditional include (includeIf), which can add settings only "
                "where the executor runs git — fix it by hand")
    proc = executor.query_git(directory, "config", "--get-regexp", r"^filter\..*\.(clean|process|smudge)$")
    if proc.returncode not in (0, 1):
        return f"git cannot read this repository's config ({proc.stderr.strip()[:200]}) — fix it by hand"
    if proc.returncode == 1 or not proc.stdout.strip():
        return ""
    pairs = [(line.split(" ", 1) + [""])[:2] for line in proc.stdout.splitlines() if line.strip()]
    if not all(LFS_FILTER.get(key) == value for key, value in pairs):
        return "the repository's git config defines a filter driver, which git would run — fix it by hand"
    if not lfs_available():
        return "the repository uses git-lfs filters but git-lfs is not installed where the executor runs git"
    refused = executor.query_git(directory, "config", "--get-regexp", LFS_REFUSED_KEYS)
    if refused.returncode not in (0, 1):
        return f"git cannot read this repository's config ({refused.stderr.strip()[:200]}) — fix it by hand"
    if refused.returncode == 0 and refused.stdout.strip():
        return ("the repository's git-lfs config names a program or a storage path (an extension, a custom transfer "
                "agent or lfs.storage) — fix it by hand")
    return ""


def plan_requirements(path: Path, *, cooldown_days: int = 0, allow_major: bool = False,
                      label: str = "", private: tuple[str, ...] = ()) -> list[PinChange]:
    """Return the edits needed for one requirements file.

    Args:
        path: requirements*.txt file.
        cooldown_days: Minimum release age for a replacement (0: none).
        allow_major: Whether a major-version jump counts as ``fix`` (else ``major-held``).
        label: Path shown in the plan (defaults to ``path``).
        private: The project's ``private_packages`` patterns: those pins are never looked up.

    Returns:
        One :class:`PinChange` per vulnerable pin.

    Raises:
        FeedError: OSV or PyPI could not be read.
    """
    pins = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = REQ_PIN.match(line)
        if m and not project_policy.is_private(m.group(1), private, "PyPI"):
            pins.append((m.group(1), m.group(3)))
    hits = osv.vulnerable_ids(("PyPI", n, v) for n, v in pins)
    changes: list[PinChange] = []
    for name, ver in pins:
        ids = hits.get(("PyPI", name, ver))
        if not ids:
            continue
        c = PinChange(label or str(path), name, ver, ids=ids, note=f"{len(ids)} advisories")
        if any(osv.is_malicious(i) for i in ids):
            c.action, c.note = "malicious", "MALICIOUS version installed — isolate, rebuild, rotate credentials; do not just bump"
        elif "--hash" in text:   # X1: a version edit would leave a stale hash; regenerate the file instead
            c.action, c.note = "hash-held", "file pins --hash values: regenerate it (pip-compile --generate-hashes)"
        else:
            new, why = choose_version(name, ver, cooldown_days=cooldown_days)
            if not new:
                c.action = "cooldown-held" if "cooldown" in why else "no-clean-release"
                c.note = why
            else:
                c.new = new
                if major(new) != major(ver) and not allow_major:
                    c.action, c.note = "major-held", f"major jump {ver} → {new}: stage-fix leaves it; stage-major takes it"
                elif major(new) != major(ver):
                    c.major, c.note = True, f"MAJOR upgrade {ver} → {new}; {c.note}"
        changes.append(c)
    return changes


def apply_requirements(path: Path, changes: list[PinChange]) -> int:
    """Rewrite the pins for every ``fix`` change, preserving all other bytes and line endings.

    Args:
        path: requirements file.
        changes: Output of :func:`plan_requirements` for this file.

    Returns:
        Number of lines changed.

    Raises:
        OSError: ``path`` is a symlink (never followed, X2) or cannot be read or written.
    """
    todo = {c.name.lower(): c for c in changes if c.action == "fix" and c.new}
    if not todo:
        return 0
    with open(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    n = 0
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = REQ_PIN.match(body)
        if m and m.group(1).lower() in todo and m.group(3) == todo[m.group(1).lower()].old:
            lines[i] = body[:m.start(3)] + todo[m.group(1).lower()].new + body[m.end(3):] + line[len(body):]
            n += 1
    with open(os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW), "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    return n


def _npm_available() -> bool:
    """Return whether npm is on PATH (``stage-fix`` needs it for a lockfile)."""
    return bool(shutil.which("npm"))


def plan_npm_lock(path: Path, *, cooldown_days: int = 0, label: str = "",
                  private: tuple[str, ...] = ()) -> tuple[list[PinChange], str]:
    """List what ``stage-fix``'s ``npm audit fix`` is there to fix in one lockfile (dry run; npm is not run).

    npm fixes a lockfile as a whole and chooses the versions itself, so a row's ``new`` is the smallest
    version the advisories call fixed, not a version npm is bound to write. Rows mirror
    :func:`plan_requirements`: ``malicious`` is never fixed; ``no-clean-release`` when no advisory names a
    fix; ``major-held`` when the fix leaves the installed version's caret range (npm without ``--force``
    never takes it; ``stage-major`` raises a direct dependency's spec instead, :func:`npm_majors`); dev-only packages are fixed like the rest, as npm
    does. Two holds are npm's own: ``workspace-held`` (``stage-fix`` fails closed on npm workspaces: its
    scratch copy has no member packages) and ``npm-unavailable`` (npm is not on PATH).

    Args:
        path: package-lock.json or npm-shrinkwrap.json.
        cooldown_days: The release cooldown npm is given (``--before``), named in the note (0: none).
        label: Path shown in the plan (defaults to ``path``).
        private: The project's ``private_packages`` patterns: those packages are never looked up.

    Returns:
        (one :class:`PinChange` per vulnerable package version, the note for the lockfile's section).

    Raises:
        FeedError: OSV could not be read.
    """
    label = label or str(path)
    m = parse(path)
    if m.unscannable:
        return [], f"not read: {m.unscannable}"
    scopes: dict[tuple[str, str], str] = {}
    for d in m.deps:
        if project_policy.is_private(d.name, private, "npm"):
            continue
        if scopes.get((d.name, d.version)) != "runtime":
            scopes[(d.name, d.version)] = d.scope
    hits = osv.vulnerable_ids(("npm", n, v) for n, v in scopes)
    workspaces, have_npm = _npm_workspaces(path), _npm_available()
    changes: list[PinChange] = []
    for (name, ver), scope in sorted(scopes.items()):
        ids = hits.get(("npm", name, ver))
        if not ids:
            continue
        dev = "dev only; " if scope == "dev" else ""
        c = PinChange(label, name, ver, ids=ids, note=f"{dev}{len(ids)} advisories")
        changes.append(c)
        if any(osv.is_malicious(i) for i in ids):
            c.action, c.note = "malicious", "MALICIOUS version installed — isolate, rebuild, rotate credentials; do not just bump"
            continue
        c.new, unfixed = osv.minimal_fix(ids, "npm", name, ver)
        c.new = c.new or ""
        if not c.new:
            c.action, c.note = "no-clean-release", f"{dev}no advisory names a fixed version"
        elif _semver_line(c.new) != _semver_line(ver):
            c.action = "major-held"
            c.note = (f"{dev}semver-major {ver} → {c.new}: stage-fix leaves it; stage-major takes it when package.json "
                      "lists it directly")
        elif workspaces:
            c.action, c.note = "workspace-held", f"{dev}npm workspaces: stage-fix fails closed here — fix by hand"
        elif not have_npm:
            c.action, c.note = "npm-unavailable", f"{dev}npm is not on PATH, so stage-fix cannot run it"
        elif unfixed:
            c.note += f" ({unfixed} without a fix)"
    note = ("`stage-fix` runs `npm audit fix --package-lock-only --ignore-scripts` on this lockfile as a whole "
            f"({f'releases at least {cooldown_days} days old only, ' if cooldown_days > 0 else ''}no `--force`). "
            "npm, not this plan, picks the resolved "
            "versions within the ranges `package.json` and the dependent packages allow, so it may stop short of a "
            "row's version or fix fewer rows. \"≥\" is the smallest version the advisories call fixed.")
    if workspaces:
        note += " This lockfile belongs to npm workspaces, which `stage-fix` refuses (fails closed)."
    return changes, note + npm_groups.annotate_plan(path, changes)   # retried moves (and a new plan digest)


def plan_cargo_lock(path: Path, *, label: str = "", private: tuple[str, ...] = ()) -> list[PinChange]:
    """List each vulnerable crate of one ``Cargo.lock`` with the command that fixes it (plan only, never applied).

    A row's ``new`` is the smallest version the advisories call fixed (:func:`osv.minimal_fix`) and its note the
    command ``cargo update -p <name>@<installed> --precise <new>`` (the ``@<installed>`` picks one copy when the
    lockfile holds several versions of the crate). Cargo, not this plan, checks that the version fits the ranges of
    the crates that depend on it. Rows: ``plan-only``; ``malicious`` (never a bump); ``no-clean-release``
    when no advisory names a fix.

    Args:
        path: Cargo.lock.
        label: Path shown in the plan (defaults to ``path``).
        private: The project's ``private_packages`` patterns: those crates are never looked up.

    Returns:
        One :class:`PinChange` per vulnerable crate version (none when the lockfile is unscannable).

    Raises:
        FeedError: OSV could not be read.
    """
    label = label or str(path)
    m = parse(path)
    if m.unscannable:
        return []
    crates = sorted({(d.name, d.version) for d in m.deps
                     if not project_policy.is_private(d.name, private, "crates.io")})
    hits = osv.vulnerable_ids(("crates.io", n, v) for n, v in crates)
    changes: list[PinChange] = []
    for name, ver in crates:
        ids = hits.get(("crates.io", name, ver))
        if not ids:
            continue
        c = PinChange(label, name, ver, action="plan-only", ids=ids)
        changes.append(c)
        if any(osv.is_malicious(i) for i in ids):
            c.action, c.note = "malicious", "MALICIOUS version installed — isolate, rebuild, rotate credentials; do not just bump"
            continue
        fixed, unfixed = osv.minimal_fix(ids, "crates.io", name, ver)
        c.new = fixed or ""
        if not c.new:
            c.action, c.note = "no-clean-release", f"{len(ids)} advisories; no advisory names a fixed version"
            continue
        if not (_CRATE_SPEC.fullmatch(name) and _CRATE_SPEC.fullmatch(ver)):
            c.note = f"{len(ids)} advisories; the crate name or version has an unusual form: fix by hand"
            continue
        c.note = f"`cargo update -p {name}@{ver} --precise {c.new}` ({len(ids)} advisories"
        c.note += f", {unfixed} without a fix)" if unfixed else ")"
    return changes


def _cell(text: str, pattern: re.Pattern[str]) -> str:
    """Return ``text`` for a fix-plan table cell, or ``(unusual)`` when it is not of the format's plain form."""
    return text if not text or pattern.fullmatch(text) else "(unusual)"


def plan_lockfile(path: Path, *, label: str = "", private: tuple[str, ...] = (),
                  hidden: Callable[[str, str], bool] | None = None) -> list[PinChange]:
    """List each vulnerable package of one :data:`lockfiles.FORMATS` lockfile with its command (plan only).

    The rows follow :func:`plan_cargo_lock`: ``plan-only`` with ``new`` the smallest version the advisories call
    fixed and the format's command in the note (``go get <mod>@v<ver>``, ``pnpm update <name>@<ver>``,
    ``bun update <name>@<ver>``, ``bundle update --conservative <name>``, a Gradle build-script change then
    ``./gradlew dependencies --write-locks``, ``dotnet add package <name> --version <ver>``); ``malicious``;
    ``no-clean-release``. A command is written only when the name, the installed and the fixed version are all of
    the format's plain form (the lockfile and the feed are data), else the note says to fix it by hand. Nothing is
    applied, and ``stage-fix`` cannot stage these files.

    Args:
        path: The lockfile (``go.mod`` for Go).
        label: Path shown in the plan (defaults to ``path``).
        private: The project's ``private_packages`` patterns: those packages are never looked up.
        hidden: ``(ecosystem, name) -> bool``: also never looked up when true (the repository's own private
            sources, :mod:`.private_sources`).

    Returns:
        One :class:`PinChange` per vulnerable package version (none when the file yields no versions).

    Raises:
        FeedError: OSV could not be read.
    """
    label = label or str(path)
    fmt = lockfiles.FORMATS[path.name]
    m = parse(path)
    pkgs = sorted({(d.name, d.version) for d in m.deps
                   if not project_policy.is_private(d.name, private, d.ecosystem)
                   and not (hidden and hidden(d.ecosystem, d.name))})
    hits = osv.vulnerable_ids((fmt.ecosystem, n, v) for n, v in pkgs)
    changes: list[PinChange] = []
    for name, ver in pkgs:
        ids = hits.get((fmt.ecosystem, name, ver))
        if not ids:
            continue
        c = PinChange(label, name, ver, action="plan-only", ids=ids)
        changes.append(c)
        if any(osv.is_malicious(i) for i in ids):
            c.action, c.note = "malicious", "MALICIOUS version installed — isolate, rebuild, rotate credentials; do not just bump"
            continue
        fixed, unfixed = osv.minimal_fix(ids, fmt.ecosystem, name, ver)
        c.new = fixed or ""
        if not c.new:
            c.action, c.note = "no-clean-release", f"{len(ids)} advisories; no advisory names a fixed version"
            continue
        if not (fmt.name.fullmatch(name) and fmt.version.fullmatch(ver) and fmt.version.fullmatch(c.new)):
            c.note = f"{len(ids)} advisories; the name or a version has an unusual form: fix by hand"
            continue
        c.note = f"{fmt.command(name, ver, c.new)} ({len(ids)} advisories"
        c.note += f", {unfixed} without a fix)" if unfixed else ")"
    return changes


def npm_fix(directory: Path, *, cooldown_days: int = 0, apply: bool = False, install: bool = False,
            update: tuple[str, ...] = ()) -> str:
    """Run npm's lockfile-only, script-free audit fix (or lockfile-only ``install`` / ``update``) for ``directory``.

    npm runs on a scratch copy holding only ``package.json`` and the lockfile, with its user and
    global config pointed at an empty file, inherited ``npm_config_*`` variables dropped, and the
    registry and TLS verification fixed on the command line. The repository's own ``.npmrc``
    (registry, proxy, ``strict-ssl``, ``ca``/``cafile``) is therefore never read (D2). With
    ``apply`` the changed lockfile is copied back. Inside a confinement scope npm runs in the tools container
    (:func:`subproc_confine.run_npm`): only the scratch copy is mounted (writable) and only the registry is
    reachable; :class:`subproc_confine.Unavailable` propagates (fail closed).

    Args:
        directory: Folder holding package-lock.json.
        cooldown_days: Releases newer than this are not considered (``--before``); 0 sets no cutoff.
        apply: Write the lockfile; otherwise ``--dry-run``.
        install: Run ``npm install --package-lock-only --ignore-scripts`` instead (``stage-major``: re-resolve the
            lockfile after :func:`npm_majors` raised a ``package.json`` spec), with the same cooldown and isolation.
        update: Run ``npm update --no-save <names> --package-lock-only --ignore-scripts`` instead
            (:func:`.npm_ops.npm_moves`: an exact-peer group or a direct parent, within the declared ranges);
            each name must be a plain package name (:data:`npm_groups.NPM_NAME`), so none can read as an option.

    Returns:
        npm's output, or a note when npm is unavailable, a manifest is a symlink or a name is not a package name.

    Raises:
        ValueError: Both ``install`` and ``update`` were given (a caller's mistake).
    """
    confined = subproc_confine.active() is not None
    npm = "npm" if confined else shutil.which("npm")
    verb = ["install", "--no-audit"] if install else ["update", "--no-save", *update] if update else ["audit", "fix"]
    if install and update:
        raise ValueError("npm_fix: install and update are exclusive")
    if update and not all(npm_groups.NPM_NAME.fullmatch(n) for n in update):
        return "npm update not run: a name is not a plain package name"
    if not npm:
        return f"npm not installed — run `npm {verb[0]} ... --package-lock-only --ignore-scripts` where it is"
    names = [n for n in ("package.json", *NPM_LOCKFILES) if (directory / n).is_symlink() or (directory / n).exists()]
    if any((directory / n).is_symlink() for n in names):
        return f"npm {' '.join(verb[:2])} not run: package.json or the lockfile is a symlink"
    cmd = [npm, *verb, "--package-lock-only", "--ignore-scripts", "--no-fund",
           f"--registry={NPM_REGISTRY}", "--strict-ssl=true"]
    if cooldown_days > 0:   # no cutoff at 0: --before=<today> would still exclude today's releases
        cmd.append(f"--before={(_dt.date.today() - _dt.timedelta(days=cooldown_days)).isoformat()}")
    if not apply:
        cmd.append("--dry-run")
    env = {k: v for k, v in os.environ.items() if not k.lower().startswith("npm_config_")}
    with tempfile.TemporaryDirectory(prefix="agentteams-npm-",
                                     dir=subproc_confine.scratch_parent() if confined else None) as tmp_name:
        tmp = Path(tmp_name).resolve()   # confined: mounted at its resolved path
        scratch, user_rc, global_rc = tmp / "pkg", tmp / "user-npmrc", tmp / "global-npmrc"
        scratch.mkdir()
        for empty in (user_rc, global_rc):   # two files: npm refuses one file as both ("double-loading config")
            empty.write_text("", encoding="utf-8")
        for n in names:
            shutil.copyfile(directory / n, scratch / n)
        env.update({"NPM_CONFIG_USERCONFIG": str(user_rc), "NPM_CONFIG_GLOBALCONFIG": str(global_rc)})
        try:
            if confined:   # only the two config paths cross over; nothing of the host environment
                proc = subproc_confine.run_npm(cmd[1:], workdir=scratch, scratch_root=tmp, timeout=600, env={
                    k: env[k] for k in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG")})
            else:
                proc = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, text=True, timeout=600,
                                      check=False)
        except subprocess.TimeoutExpired:
            return f"npm {verb[0]} timed out after 600s"
        if apply:
            for n in names:
                if n in NPM_LOCKFILES and (scratch / n).is_file() and \
                        (scratch / n).read_bytes() != (directory / n).read_bytes():
                    shutil.copyfile(scratch / n, directory / n)
    return (proc.stdout + proc.stderr) or f"npm exited {proc.returncode}"


def _leave_to_prs(rows: list[PinChange], skip: dict[str, list[int]] | None) -> None:
    """Mark each ``fix`` row whose package another open PR already updates as ``pr-held`` ("left to #N", D1)."""
    for c in rows:
        nums = (skip or {}).get(open_prs.normalize(c.name))
        if nums and c.action in ("fix", "major-held"):
            c.action, c.major = "pr-held", False
            c.note = f"left to {', '.join(f'#{n}' for n in nums)} (an open PR already updates it; not duplicated)"


def npm_majors(lockfile: Path, *, label: str, cooldown_days: int = 0, private: tuple[str, ...] = (),
               skip: dict[str, list[int]] | None = None) -> tuple[list[PinChange], str]:
    """Take ``lockfile``'s ``major-held`` packages that ``package.json`` lists directly (``stage-major``).

    Each such package's spec becomes ``^<fixed version>`` in the section that already lists it
    (:func:`npm_major.rewrite_specs`, every other byte kept), then npm re-resolves the lockfile
    (:func:`npm_fix` with ``install``). A package ``package.json`` does not list (transitive only), every
    package of an npm workspace, and every package when ``package.json`` cannot be read, stays ``major-held``
    with a note saying why.

    Fail closed (S1, H1): before anything is written, ``npm install`` is refused unless the tools container is
    active (:func:`subproc_confine.active`; never on the host, never when confinement is opted out) and the base
    ``package.json`` and lockfile name only registry sources (:func:`manifests.npm_off_registry`). After it, a
    reported npm error or an unchanged lockfile refuses the whole stage, so a raised spec is never committed
    beside a stale lockfile.

    Args:
        lockfile: package-lock.json or npm-shrinkwrap.json (``package.json`` beside it is never a symlink here).
        label: The lockfile's path in the plan.
        cooldown_days: Passed to npm (``--before``), as for the audit fix.
        private: The project's ``private_packages`` patterns.
        skip: ``{normalized name: [PR numbers]}`` another open PR already updates: never raised (``pr-held``).

    Returns:
        (the major rows: taken ones as ``fix`` with ``major`` set, held ones as ``major-held``; npm's output or "").

    Raises:
        FeedError: OSV could not be read.
        OSError: ``package.json`` could not be written.
        ExecError: npm install would run unconfined or against a non-registry source, or it failed.
    """
    rows = [c for c in plan_npm_lock(lockfile, cooldown_days=cooldown_days, label=label, private=private)[0]
            if c.action == "major-held"]
    _leave_to_prs(rows, skip)
    if not [c for c in rows if c.action == "major-held"]:
        return rows, ""
    if _npm_workspaces(lockfile):
        for c in rows:
            c.note += "; npm workspaces: stage-major fails closed here"
        return rows, ""
    pkg = lockfile.parent / "package.json"
    try:
        text = pkg.read_text(encoding="utf-8")
        manifest, unread = json.loads(text), ""
    except (OSError, ValueError) as exc:   # missing or malformed: nothing here is rewritten, and the rows say why
        manifest, text, unread = None, "", f"package.json cannot be read ({type(exc).__name__})"
    if manifest is not None and not isinstance(manifest, dict):
        unread = "package.json is not an object"
    if unread:
        for c in rows:
            c.note += f"; {unread}: stays held — fix by hand"
        return rows, ""
    specs: dict[str, tuple[str, str]] = {}
    for c in sorted(rows, key=lambda r: _semver_line(r.new)):   # the highest fixed version of a name wins
        if c.action == "pr-held":
            continue
        section = npm_major.direct_section(manifest, c.name)
        if section:
            specs[c.name] = (section, f"^{c.new.lstrip('vV')}")
    if specs:
        try:
            new_text = npm_major.rewrite_specs(text, specs)
        except ValueError as exc:
            for c in rows:
                c.note += f"; package.json not rewritten ({exc}): fix by hand"
            return rows, ""
        if subproc_confine.active() is None:   # S1: npm install resolves every spec; only ever in the container
            raise ExecError(f"{label}: stage-major refuses npm install outside the tools container (confinement is "
                            "off or opted out)")
        lock_before = lockfile.read_bytes()
        # S1 stays strict: an off-registry base is refused here even though stage-fix's validate_diff keeps an
        # entry identical to the base (manifests._kept_off_registry, e.g. SheetJS's CDN-only xlsx), because npm
        # install re-resolves every package.json spec. Tracked as agentteams-remediation-log:
        # stage-major-offregistry-base; any relaxation needs its own @security review.
        if problem := npm_off_registry(label, text, lock_before.decode("utf-8", "replace")):
            raise ExecError(f"stage-major refuses npm install: {problem}")
        with open(os.open(pkg, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW), "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
    pkg_label = str(Path(label).parent / "package.json")
    for c in rows:
        if c.action == "pr-held":
            continue
        if c.name in specs:
            section, spec = specs[c.name]
            c.path, c.action, c.major = pkg_label, "fix", True
            c.note = f"MAJOR upgrade: package.json {section}.{c.name} → {spec} (lockfile re-resolved by npm install)"
        else:
            c.note += "; transitive only (package.json does not list it): stays held — its parent must move first"
    if not specs:
        return rows, ""
    out = npm_fix(lockfile.parent, cooldown_days=cooldown_days, apply=True, install=True)
    failed = next((ln for ln in out.splitlines() if ln.startswith(("npm error", "npm ERR!"))), "")
    if failed or lockfile.read_bytes() == lock_before:   # H1: never package.json without its re-resolved lockfile
        why = failed or (out.strip().splitlines() or ["no output"])[-1]
        raise ExecError(f"{label}: npm install did not re-resolve the lockfile after package.json was raised "
                        f"({why[:200]}); nothing is staged")
    return rows, out


def advisory_counts(path: Path, private: tuple[str, ...] = ()) -> Counter[tuple[str, str, str]] | None:
    """Count one manifest's (ecosystem, package, advisory) occurrences, one per installed copy.

    Args:
        path: A manifest :func:`parse` reads.
        private: The project's ``private_packages`` patterns: those packages are never looked up.

    Returns:
        The counts, or None when the manifest yields no exact versions.

    Raises:
        FeedError: OSV could not be read.
    """
    m = parse(path)
    if m.unscannable or not m.deps:
        return None
    deps = [d for d in m.deps if not project_policy.is_private(d.name, private, d.ecosystem)]
    hits = osv.vulnerable_ids((d.ecosystem, d.name, d.version) for d in deps)
    return Counter((d.ecosystem, d.name, i) for d in deps for i in hits.get((d.ecosystem, d.name, d.version), ()))


def _add_resolved(res: FixResult, path: Path, old: bytes, rel: str, private: tuple[str, ...]) -> None:
    """Add the advisory occurrences ``path`` lost since it held ``old`` to ``res.resolved``."""
    if res.resolved is None or path.read_bytes() == old:
        return
    try:
        with tempfile.TemporaryDirectory(prefix="agentteams-before-") as tmp:
            before_path = Path(tmp) / path.name   # parse() reads by file name
            before_path.write_bytes(old)
            before = advisory_counts(before_path, private)
        after = advisory_counts(path, private)
    except _http.FeedError as exc:
        res.feed_errors.append(f"{rel}: {exc}")   # stage-fix fails closed on a feed error
        res.resolved = None
        return
    res.resolved = None if before is None or after is None else res.resolved + sum((before - after).values())


def fix_path(root: Path, *, apply: bool = False, allow_major: bool = False, cooldown_days: int = 0,
             include_worktrees: bool = False, allow_dirty: bool = False, npm: bool = True,
             manifests: list[Path] | None = None, gradle_lockfiles: bool = False,
             skip: dict[str, list[int]] | None = None) -> FixResult:
    """Plan (and with ``apply``, make) dependency fixes for every manifest under ``root``.

    Args:
        root: Working tree to change. Point it at a worktree or branch you control.
        apply: Write changes.
        allow_major: Also take major-version jumps: requirements pins, and npm packages ``package.json`` lists
            directly (:func:`npm_majors`).
        cooldown_days: Minimum release age (0, the default: none; Rule S-10's remediation carve-out).
        include_worktrees: Descend into linked worktrees too.
        allow_dirty: Edit files that already have uncommitted changes.
        npm: Run the npm step (needs npm on PATH). A dry run lists each lockfile's vulnerable
            packages (:func:`plan_npm_lock`) either way.
        manifests: Plan only these manifest files under ``root`` (``daily`` passes one repository's,
            without nested repositories); default: everything :func:`discover` finds.
        gradle_lockfiles: Plan ``gradle.lockfile`` files too (off by default: refused with
            :data:`private_sources.GRADLE_OPT_IN`).
        skip: ``{normalized package name: [PR numbers]}`` that another open PR already updates
            (:func:`open_prs.claimed`): those requirements pins and npm majors are left to it (``pr-held``). npm's
            own ``audit fix`` chooses its versions and is not filtered.

    Returns:
        A :class:`FixResult`. Feed failures are recorded under ``feed_errors``; a symlinked
        requirements file, ``package.json`` or npm lockfile is never read or edited and is
        recorded under ``refused`` (X2/M5).
    """
    root = root.resolve()
    res = FixResult(applied=apply, resolved=0 if apply else None)
    found = discover(root, include_worktrees=include_worktrees) if manifests is None else sorted(manifests)
    policies = project_policy.PolicyCache()
    sources = private_sources.PrivateSources(root)
    for path in found:
        rel = str(path.relative_to(root))
        repo = project_policy.repo_dir(path, root)
        pol = policies.get(repo)
        private = pol.private_packages
        if pol.level == "off" or project_policy.is_excluded(path.relative_to(repo).as_posix(), pol.exclude_paths):
            res.refused.append((rel, "left out by the project brief (vulnerability_scanning: level off or exclude_paths)"))
            continue
        if private and path.name in NPM_LOCKFILES and any(project_policy.is_private(d.name, private, d.ecosystem)
                                                          for d in parse(path).deps):
            res.refused.append((rel, ("lists private packages (project brief): npm would send them to the registry "
                                      "— fix this lockfile by hand")))
            continue
        linked = [path] if REQ_NAME.match(path.name) else \
            [path.parent / n for n in ("package.json", *NPM_LOCKFILES)] if path.name in NPM_LOCKFILES else []
        if any(p.is_symlink() for p in linked):
            res.refused.append((rel, "a symlink (the manifest or its package.json) — never followed or edited"))
            continue
        if REQ_NAME.match(path.name):
            try:
                changes = plan_requirements(path, cooldown_days=cooldown_days, allow_major=allow_major, label=rel,
                                            private=private)
            except _http.FeedError as exc:
                res.feed_errors.append(f"{rel}: {exc}")
                continue
            _leave_to_prs(changes, skip)
            res.changes += changes
            if apply and any(c.action == "fix" for c in changes):
                unsafe = git_config_problem(path.parent)
                if unsafe:
                    res.refused.append((rel, unsafe))
                elif _dirty(path) and not allow_dirty:
                    res.refused.append((rel, "uncommitted changes — commit/stash first or use a worktree"))
                else:
                    old = path.read_bytes()
                    apply_requirements(path, changes)
                    _add_resolved(res, path, old, rel, private)
        elif path.name in NPM_LOCKFILES and not apply:
            try:
                rows, res.npm_notes[rel] = plan_npm_lock(path, cooldown_days=cooldown_days, label=rel, private=private)
            except _http.FeedError as exc:
                res.feed_errors.append(f"{rel}: {exc}")
                continue
            res.npm_plan += rows
            if npm:
                res.npm.append((str(path.parent.relative_to(root)) or ".", npm_fix(path.parent, cooldown_days=cooldown_days)))
        elif path.name in NPM_LOCKFILES and npm:
            unsafe = git_config_problem(path.parent) if apply else ""
            if unsafe:
                res.refused.append((rel, unsafe))
                continue
            if apply and _dirty(path) and not allow_dirty:
                res.refused.append((rel, "uncommitted changes — commit/stash first or use a worktree"))
                continue
            old, where = path.read_bytes(), str(path.parent.relative_to(root)) or "."
            if apply and allow_major:   # stage-major: direct semver-major upgrades first, then the audit fix
                try:
                    majors, out = npm_majors(path, label=rel, cooldown_days=cooldown_days, private=private,
                                             skip=skip)
                except _http.FeedError as exc:
                    res.feed_errors.append(f"{rel}: {exc}")
                    continue
                res.changes += majors
                res.npm += [(where, out)] if out else []
            res.npm.append((where, npm_fix(path.parent, cooldown_days=cooldown_days, apply=apply)))
            if apply:   # what the audit fix left inside its range: exact-peer groups and direct parents
                try:
                    res.npm += [(where, o) for o in npm_ops.npm_moves(path, run=npm_fix, counts=advisory_counts,
                                                                      base_lock=old, cooldown_days=cooldown_days,
                                                                      private=private)]
                except _http.FeedError as exc:
                    res.feed_errors.append(f"{rel}: {exc}")
                    continue
            _add_resolved(res, path, old, rel, private)
        elif path.name == "composer.lock":
            res.composer.append(f"`{rel}`: composer update <pkg> --no-install --no-scripts --no-plugins "
                                "(then `composer audit`)")
        elif images.is_container_manifest(path.name) and not apply:   # plan only; nothing here edits these files
            found_images, errors = images.plan(path, rel)
            res.feed_errors += errors
            res.image_plan += [PinChange(rel, f.name, f.version, f.fix, "plan-only", note=f"{f.detail} (line "
                                         + ", ".join(loc.rsplit(":", 1)[-1] for loc in f.locations) + ")")
                               for f in found_images]
        elif path.name in CRATE_LOCKS:   # plan only, with or without apply: nothing here edits a Cargo.lock
            try:
                res.cargo_plan += plan_cargo_lock(path, label=rel, private=private)
            except _http.FeedError as exc:
                res.feed_errors.append(f"{rel}: {exc}")
        elif path.name == "gradle.lockfile" and not gradle_lockfiles:   # no private-repository signal
            res.refused.append((rel, private_sources.GRADLE_OPT_IN))
        elif path.name in lockfiles.FORMATS:   # plan only, with or without apply, like a Cargo.lock
            try:
                res.lock_plan += plan_lockfile(path, label=rel, private=private,
                                               hidden=lambda eco, n, r=repo, f=path.name: sources.is_private(r, f, eco, n))
            except _http.FeedError as exc:
                res.feed_errors.append(f"{rel}: {exc}")
    return res

