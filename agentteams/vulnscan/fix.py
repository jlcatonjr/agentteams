"""Remediate vulnerable dependency versions in a working tree. Dry run unless ``apply``.

- **requirements*.txt**: each vulnerable ``==`` pin moves to the *smallest* PyPI release that
  (a) OSV reports as clean, (b) was uploaded at least ``cooldown_days`` ago (Rule S-10's
  release cooldown), and (c) is neither a pre-release nor fully yanked. A major-version jump
  is proposed but written only with ``allow_major``. A pin whose current version is
  **malicious** (OSV ``MAL-*``) is never edited: that is an incident, not an upgrade. Line
  endings and every other byte of the file are preserved. A file that pins ``--hash`` values is
  held back (``hash-held``) rather than left with a stale hash, and a symlinked manifest is
  refused, never followed.
- **npm lockfiles**: ``npm audit fix --package-lock-only --ignore-scripts --before=<cutoff>``.
  It touches the lockfile only, runs no install scripts, and resolves only releases older
  than the cooldown. Semver-major fixes are left for a person (no ``--force``). It runs on a
  scratch copy with the public registry and TLS verification fixed, never reading the
  repository's ``.npmrc``. A dry run also lists each vulnerable package of the lockfile from
  the advisories (:func:`plan_npm_lock`); npm, not this module, picks the versions it writes.
- **composer.lock**: guidance only (``composer update <pkg> --no-install --no-scripts --no-plugins``).

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
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.vulnscan import _http, osv
from agentteams.vulnscan.manifests import REQ_NAME, REQ_PIN, discover, parse
from agentteams.vulnscan.versions import is_final, major, version_key

PYPI = "https://pypi.org/pypi"
NPM_REGISTRY = "https://registry.npmjs.org/"    # fixed: a repository .npmrc cannot redirect the unattended fix
NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json")


@dataclass
class PinChange:
    """One proposed or applied requirements edit."""

    path: str
    name: str
    old: str
    new: str = ""
    # fix | major-held | no-clean-release | cooldown-held | hash-held | malicious; npm rows (plan_npm_lock)
    # also workspace-held | npm-unavailable
    action: str = "fix"
    ids: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class FixResult:
    """Everything ``fix_path`` decided or did."""

    changes: list[PinChange] = field(default_factory=list)
    npm_plan: list[PinChange] = field(default_factory=list)        # dry run: per package, from the advisories
    npm_notes: dict[str, str] = field(default_factory=dict)        # lockfile -> what stage-fix does there
    npm: list[tuple[str, str]] = field(default_factory=list)       # (dir, summary)
    composer: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)   # (path, reason)
    feed_errors: list[str] = field(default_factory=list)
    applied: bool = False
    target: str = ""           # the git repository the plan is for (the ``stage-fix`` target), when known
    target_problem: str = ""   # why ``stage-fix`` cannot take ``target`` (e.g. not a git repository)

    def exit_code(self) -> int:
        """Return the shared exit code for a fix run.

        Returns:
            4 if a malicious pin was found, 3 if a feed failed, 1 if anything was held back or
            refused (major jump, cooldown, no clean release, dirty file), else 0.
        """
        rows = self.changes + self.npm_plan
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


def choose_version(name: str, current: str, *, cooldown_days: int = 14,
                   now: _dt.datetime | None = None) -> tuple[str, str]:
    """Pick the smallest clean, cooldown-aged release above ``current``.

    Args:
        name: PyPI project name.
        current: Pinned version.
        cooldown_days: Minimum release age.
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
    return "", f"clean release {young[0]} is younger than the {cooldown_days}-day cooldown (needs an S-10 exception)"


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


def git_config_problem(directory: Path) -> str:
    """Return why git may not run in ``directory``'s repository, or "" (none, or not a repository).

    The hardened git turns off hooks and ``core.fsmonitor`` by name, but a ``filter.<name>.clean`` /
    ``.process`` / ``.smudge`` driver has an arbitrary name and runs under ``git status`` or a checkout when
    ``.gitattributes`` selects it. Global and system config are off in :func:`executor.query_git`, so any driver
    seen here comes from the repository's own config (including its ``include`` files). A repository git
    refuses to open (e.g. ``safe.directory``) is a problem too: its dirty check would otherwise read as clean.

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
    proc = executor.query_git(directory, "config", "--get-regexp", r"^filter\..*\.(clean|process|smudge)$")
    if proc.returncode == 0 and proc.stdout.strip():
        return "the repository's git config defines a filter driver, which git would run — fix it by hand"
    return ""


def plan_requirements(path: Path, *, cooldown_days: int = 14, allow_major: bool = False,
                      label: str = "") -> list[PinChange]:
    """Return the edits needed for one requirements file.

    Args:
        path: requirements*.txt file.
        cooldown_days: Minimum release age for a replacement.
        allow_major: Whether a major-version jump counts as ``fix`` (else ``major-held``).
        label: Path shown in the plan (defaults to ``path``).

    Returns:
        One :class:`PinChange` per vulnerable pin.

    Raises:
        FeedError: OSV or PyPI could not be read.
    """
    pins = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = REQ_PIN.match(line)
        if m:
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
                    c.action, c.note = "major-held", f"major jump {ver} → {new}: review the changelog, then allow_major"
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


def _semver_line(version: str) -> tuple[int, ...]:
    """Return what a caret range holds fixed: the major, or ``0.minor``, or ``0.0.patch``."""
    core = version.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)[:3]]
    nums += [0] * (3 - len(nums))
    return (nums[0],) if nums[0] else (0, nums[1]) if nums[1] else (0, 0, nums[2])


def _npm_workspaces(lockfile: Path) -> bool:
    """Return whether the lockfile's root package, or the ``package.json`` beside it, declares workspaces."""
    for path, root_of in ((lockfile, lambda d: d["packages"].get("") if isinstance(d.get("packages"), dict) else None),
                          (lockfile.parent / "package.json", lambda d: d)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # missing or malformed: npm reports that, not the plan
            data = None
        root = root_of(data) if isinstance(data, dict) else None
        if isinstance(root, dict) and root.get("workspaces"):
            return True
    return False


def plan_npm_lock(path: Path, *, cooldown_days: int = 14, label: str = "") -> tuple[list[PinChange], str]:
    """List what ``stage-fix``'s ``npm audit fix`` is there to fix in one lockfile (dry run; npm is not run).

    npm fixes a lockfile as a whole and chooses the versions itself, so a row's ``new`` is the smallest
    version the advisories call fixed, not a version npm is bound to write. Rows mirror
    :func:`plan_requirements`: ``malicious`` is never fixed; ``no-clean-release`` when no advisory names a
    fix; ``major-held`` when the fix leaves the installed version's caret range (npm without ``--force``
    never takes it, so ``allow_major`` does not apply); dev-only packages are fixed like the rest, as npm
    does. Two holds are npm's own: ``workspace-held`` (``stage-fix`` fails closed on npm workspaces: its
    scratch copy has no member packages) and ``npm-unavailable`` (npm is not on PATH).

    Args:
        path: package-lock.json or npm-shrinkwrap.json.
        cooldown_days: The release cooldown npm is given (``--before``), named in the note.
        label: Path shown in the plan (defaults to ``path``).

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
            c.note = f"{dev}semver-major {ver} → {c.new}: npm audit fix (no --force) leaves it; change package.json by hand"
        elif workspaces:
            c.action, c.note = "workspace-held", f"{dev}npm workspaces: stage-fix fails closed here — fix by hand"
        elif not have_npm:
            c.action, c.note = "npm-unavailable", f"{dev}npm is not on PATH, so stage-fix cannot run it"
        elif unfixed:
            c.note += f" ({unfixed} without a fix)"
    note = ("`stage-fix` runs `npm audit fix --package-lock-only --ignore-scripts` on this lockfile as a whole "
            f"(releases at least {cooldown_days} days old only, no `--force`). npm, not this plan, picks the resolved "
            "versions within the ranges `package.json` and the dependent packages allow, so it may stop short of a "
            "row's version or fix fewer rows. \"≥\" is the smallest version the advisories call fixed.")
    if workspaces:
        note += " This lockfile belongs to npm workspaces, which `stage-fix` refuses (fails closed)."
    return changes, note


def npm_fix(directory: Path, *, cooldown_days: int = 14, apply: bool = False) -> str:
    """Run npm's lockfile-only, script-free audit fix for ``directory``.

    npm runs on a scratch copy holding only ``package.json`` and the lockfile, with its user and
    global config pointed at an empty file, inherited ``npm_config_*`` variables dropped, and the
    registry and TLS verification fixed on the command line. The repository's own ``.npmrc``
    (registry, proxy, ``strict-ssl``, ``ca``/``cafile``) is therefore never read (D2). With
    ``apply`` the changed lockfile is copied back.

    Args:
        directory: Folder holding package-lock.json.
        cooldown_days: Releases newer than this are not considered (``--before``).
        apply: Write the lockfile; otherwise ``--dry-run``.

    Returns:
        npm's output, or a note when npm is unavailable or a manifest is a symlink.
    """
    npm = shutil.which("npm")
    if not npm:
        return "npm not installed — run `npm audit fix --package-lock-only --ignore-scripts` where it is"
    names = [n for n in ("package.json", *NPM_LOCKFILES) if (directory / n).is_symlink() or (directory / n).exists()]
    if any((directory / n).is_symlink() for n in names):
        return "npm audit fix not run: package.json or the lockfile is a symlink"
    cutoff = (_dt.date.today() - _dt.timedelta(days=cooldown_days)).isoformat()
    cmd = [npm, "audit", "fix", "--package-lock-only", "--ignore-scripts", f"--before={cutoff}", "--no-fund",
           f"--registry={NPM_REGISTRY}", "--strict-ssl=true"]
    if not apply:
        cmd.append("--dry-run")
    env = {k: v for k, v in os.environ.items() if not k.lower().startswith("npm_config_")}
    with tempfile.TemporaryDirectory(prefix="agentteams-npm-") as tmp:
        scratch, user_rc, global_rc = Path(tmp) / "pkg", Path(tmp) / "user-npmrc", Path(tmp) / "global-npmrc"
        scratch.mkdir()
        for empty in (user_rc, global_rc):   # two files: npm refuses one file as both ("double-loading config")
            empty.write_text("", encoding="utf-8")
        for n in names:
            shutil.copyfile(directory / n, scratch / n)
        env.update({"NPM_CONFIG_USERCONFIG": str(user_rc), "NPM_CONFIG_GLOBALCONFIG": str(global_rc)})
        try:
            proc = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, text=True, timeout=600,
                                  check=False)
        except subprocess.TimeoutExpired:
            return "npm audit fix timed out after 600s"
        if apply:
            for n in names:
                if n in NPM_LOCKFILES and (scratch / n).is_file() and \
                        (scratch / n).read_bytes() != (directory / n).read_bytes():
                    shutil.copyfile(scratch / n, directory / n)
    return (proc.stdout + proc.stderr) or f"npm exited {proc.returncode}"


def fix_path(root: Path, *, apply: bool = False, allow_major: bool = False, cooldown_days: int = 14,
             include_worktrees: bool = False, allow_dirty: bool = False, npm: bool = True,
             manifests: list[Path] | None = None) -> FixResult:
    """Plan (and with ``apply``, make) dependency fixes for every manifest under ``root``.

    Args:
        root: Working tree to change. Point it at a worktree or branch you control.
        apply: Write changes.
        allow_major: Also take major-version jumps.
        cooldown_days: Rule S-10 release cooldown.
        include_worktrees: Descend into linked worktrees too.
        allow_dirty: Edit files that already have uncommitted changes.
        npm: Run the npm step (needs npm on PATH). A dry run lists each lockfile's vulnerable
            packages (:func:`plan_npm_lock`) either way.
        manifests: Plan only these manifest files under ``root`` (``daily`` passes one repository's,
            without nested repositories); default: everything :func:`discover` finds.

    Returns:
        A :class:`FixResult`. Feed failures are recorded under ``feed_errors``; a symlinked
        requirements file, ``package.json`` or npm lockfile is never read or edited and is
        recorded under ``refused`` (X2/M5).
    """
    root = root.resolve()
    res = FixResult(applied=apply)
    found = discover(root, include_worktrees=include_worktrees) if manifests is None else sorted(manifests)
    for path in found:
        rel = str(path.relative_to(root))
        linked = [path] if REQ_NAME.match(path.name) else \
            [path.parent / n for n in ("package.json", *NPM_LOCKFILES)] if path.name in NPM_LOCKFILES else []
        if any(p.is_symlink() for p in linked):
            res.refused.append((rel, "a symlink (the manifest or its package.json) — never followed or edited"))
            continue
        if REQ_NAME.match(path.name):
            try:
                changes = plan_requirements(path, cooldown_days=cooldown_days, allow_major=allow_major, label=rel)
            except _http.FeedError as exc:
                res.feed_errors.append(f"{rel}: {exc}")
                continue
            res.changes += changes
            if apply and any(c.action == "fix" for c in changes):
                unsafe = git_config_problem(path.parent)
                if unsafe:
                    res.refused.append((rel, unsafe))
                elif _dirty(path) and not allow_dirty:
                    res.refused.append((rel, "uncommitted changes — commit/stash first or use a worktree"))
                else:
                    apply_requirements(path, changes)
        elif path.name in NPM_LOCKFILES and not apply:
            try:
                rows, res.npm_notes[rel] = plan_npm_lock(path, cooldown_days=cooldown_days, label=rel)
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
            res.npm.append((str(path.parent.relative_to(root)) or ".", npm_fix(path.parent, cooldown_days=cooldown_days, apply=apply)))
        elif path.name == "composer.lock":
            res.composer.append(f"`{rel}`: composer update <pkg> --no-install --no-scripts --no-plugins "
                                "(then `composer audit`)")
    return res

