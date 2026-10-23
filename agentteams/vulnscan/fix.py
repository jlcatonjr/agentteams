"""Remediate vulnerable dependency versions in a working tree. Dry run unless ``apply``.

- **requirements*.txt**: each vulnerable ``==`` pin moves to the *smallest* PyPI release that
  (a) OSV reports as clean, (b) was uploaded at least ``cooldown_days`` ago (Rule S-10's
  release cooldown), and (c) is neither a pre-release nor fully yanked. A major-version jump
  is proposed but written only with ``allow_major``. A pin whose current version is
  **malicious** (OSV ``MAL-*``) is never edited: that is an incident, not an upgrade. Line
  endings and every other byte of the file are preserved.
- **npm lockfiles**: ``npm audit fix --package-lock-only --ignore-scripts --before=<cutoff>``.
  It touches the lockfile only, runs no install scripts, and resolves only releases older
  than the cooldown. Semver-major fixes are left for a person (no ``--force``).
- **composer.lock**: guidance only (``composer update <pkg> --no-install --no-scripts --no-plugins``).

This module never commits, pushes or opens PRs. Point it at a worktree you control.
``daily`` does that in its ``apply-worktree`` mode. A file with uncommitted changes is
refused unless ``allow_dirty``.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.vulnscan import _http, osv
from agentteams.vulnscan.manifests import REQ_NAME, REQ_PIN, discover
from agentteams.vulnscan.versions import is_final, major, version_key

PYPI = "https://pypi.org/pypi"


@dataclass
class PinChange:
    """One proposed or applied requirements edit."""

    path: str
    name: str
    old: str
    new: str = ""
    action: str = "fix"      # fix | major-held | no-clean-release | cooldown-held | malicious
    ids: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class FixResult:
    """Everything ``fix_path`` decided or did."""

    changes: list[PinChange] = field(default_factory=list)
    npm: list[tuple[str, str]] = field(default_factory=list)       # (dir, summary)
    composer: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)   # (path, reason)
    feed_errors: list[str] = field(default_factory=list)
    applied: bool = False

    def exit_code(self) -> int:
        """Return the shared exit code for a fix run.

        Returns:
            4 if a malicious pin was found, 3 if a feed failed, 1 if anything was held back or
            refused (major jump, cooldown, no clean release, dirty file), else 0.
        """
        if any(c.action == "malicious" for c in self.changes):
            return 4
        if self.feed_errors:
            return 3
        if self.refused or any(c.action != "fix" for c in self.changes):
            return 1
        return 0

    def markdown(self) -> str:
        """Render for people.

        Returns:
            Markdown text.
        """
        out = [f"# Fix plan ({'APPLIED' if self.applied else 'dry run'})", ""]
        if self.changes:
            out += ["| File | Package | Installed | → | Action | Notes |", "|---|---|---|---|---|---|"]
            for c in self.changes:
                out.append(f"| `{c.path}` | {c.name} | {c.old} | {c.new or '—'} | {c.action} | {c.note} |")
            out.append("")
        for d, summary in self.npm:
            out += [f"## npm: `{d}`", "", "```", summary.strip()[-3000:], "```", ""]
        if self.composer:
            out += ["## composer", ""] + [f"- {c}" for c in self.composer] + [""]
        if self.refused:
            out += ["## Refused", ""] + [f"- `{p}`: {r}" for p, r in self.refused] + [""]
        if self.feed_errors:
            out += ["## Not checked (feed unavailable)", ""] + [f"- {e}" for e in self.feed_errors] + [""]
        if len(out) == 2:
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
    if not shutil.which("git"):
        return False
    proc = subprocess.run(["git", "-C", str(path.parent), "status", "--porcelain", "--", path.name],
                          capture_output=True, text=True, check=False)
    return proc.returncode == 0 and bool(proc.stdout.strip())


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
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
    """
    todo = {c.name.lower(): c for c in changes if c.action == "fix" and c.new}
    if not todo:
        return 0
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    n = 0
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = REQ_PIN.match(body)
        if m and m.group(1).lower() in todo and m.group(3) == todo[m.group(1).lower()].old:
            lines[i] = body[:m.start(3)] + todo[m.group(1).lower()].new + body[m.end(3):] + line[len(body):]
            n += 1
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    return n


def npm_fix(directory: Path, *, cooldown_days: int = 14, apply: bool = False) -> str:
    """Run npm's lockfile-only, script-free audit fix in ``directory``.

    Args:
        directory: Folder holding package-lock.json.
        cooldown_days: Releases newer than this are not considered (``--before``).
        apply: Write the lockfile; otherwise ``--dry-run``.

    Returns:
        npm's output, or a note when npm is unavailable.
    """
    npm = shutil.which("npm")
    if not npm:
        return "npm not installed — run `npm audit fix --package-lock-only --ignore-scripts` where it is"
    cutoff = (_dt.date.today() - _dt.timedelta(days=cooldown_days)).isoformat()
    cmd = [npm, "audit", "fix", "--package-lock-only", "--ignore-scripts", f"--before={cutoff}", "--no-fund"]
    if not apply:
        cmd.append("--dry-run")
    try:
        proc = subprocess.run(cmd, cwd=directory, capture_output=True, text=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        return "npm audit fix timed out after 600s"
    return (proc.stdout + proc.stderr) or f"npm exited {proc.returncode}"


def fix_path(root: Path, *, apply: bool = False, allow_major: bool = False, cooldown_days: int = 14,
             include_worktrees: bool = False, allow_dirty: bool = False, npm: bool = True) -> FixResult:
    """Plan (and with ``apply``, make) dependency fixes for every manifest under ``root``.

    Args:
        root: Working tree to change. Point it at a worktree or branch you control.
        apply: Write changes.
        allow_major: Also take major-version jumps.
        cooldown_days: Rule S-10 release cooldown.
        include_worktrees: Descend into linked worktrees too.
        allow_dirty: Edit files that already have uncommitted changes.
        npm: Run the npm step (needs npm on PATH).

    Returns:
        A :class:`FixResult`. Feed failures are recorded under ``refused``.
    """
    root = root.resolve()
    res = FixResult(applied=apply)
    for path in discover(root, include_worktrees=include_worktrees):
        rel = str(path.relative_to(root))
        if REQ_NAME.match(path.name):
            try:
                changes = plan_requirements(path, cooldown_days=cooldown_days, allow_major=allow_major, label=rel)
            except _http.FeedError as exc:
                res.feed_errors.append(f"{rel}: {exc}")
                continue
            res.changes += changes
            if apply and any(c.action == "fix" for c in changes):
                if _dirty(path) and not allow_dirty:
                    res.refused.append((rel, "uncommitted changes — commit/stash first or use a worktree"))
                else:
                    apply_requirements(path, changes)
        elif path.name in ("package-lock.json", "npm-shrinkwrap.json") and npm:
            if apply and _dirty(path) and not allow_dirty:
                res.refused.append((rel, "uncommitted changes — commit/stash first or use a worktree"))
                continue
            res.npm.append((str(path.parent.relative_to(root)) or ".", npm_fix(path.parent, cooldown_days=cooldown_days, apply=apply)))
        elif path.name == "composer.lock":
            res.composer.append(f"`{rel}`: composer update <pkg> --no-install --no-scripts --no-plugins "
                                "(then `composer audit`)")
    return res

