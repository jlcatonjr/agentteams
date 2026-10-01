"""agent_doc_sync.py — propagate each agent's AGENTTEAMS-LEARNED block across its framework copies.

``agentteams --sync-agent-docs --project P`` keeps the learned-notes block
(:mod:`agentteams.learned_blocks`) of one agent identical across ``.github/agents/<slug>.agent.md``,
``.claude/agents/<slug>.md`` and ``.goose/recipes/<slug>.yaml``. It exists because an agent
running inside a Claude Code sandbox cannot write ``.claude/agents`` (Claude Code itself
read-only binds it), so cross-framework propagation of what agents learn has to run **outside
every agent session** — from an operator-installed systemd user unit
(``scripts/install-agent-doc-sync.sh``) or by hand.

That makes this module an unsandboxed writer of agent files, and every rule below follows:

* **Only the learned block moves.** Front matter, template fences, recipe capability keys,
  settings, build-logs and trust roots are never read for propagation and never written; each
  composed file is proven byte-identical outside the block before it is written.
* **Direction from a three-way baseline.** Per (slug, framework) a digest is kept in
  ``$XDG_STATE_HOME/agentteams/<sha256(realpath P)[:16]>/agent-doc-sync.json`` (default
  ``~/.local/state``) — outside every write root, never in the project. The copy that changed since
  the baseline is the source; two copies that changed differently are a **conflict** (recorded,
  nothing written). A removed block is never propagated (no deletion); an emptied one is, after
  the overwritten text is backed up to the state dir.
* **Default is ``--check``** (report, write nothing). ``--apply`` writes ``.github/agents`` and
  ``.goose/recipes`` targets; ``.claude/agents`` targets are only *staged* (a pending report in the
  state dir) unless the operator adds ``--include-claude``, which prints the diff and writes.
* **Content gates.** Every propagated block is scanned with :func:`agentteams.scan.scan_content`;
  any finding quarantines it (state dir) and nothing is written. Fence or learned marker tokens and
  non-``\\n`` line breaks are refused.
* **Filesystem.** Agent dirs must be real directories (no symlinked component); files must be
  regular, single-link, opened ``O_NOFOLLOW`` relative to a directory fd and stay under the
  project's realpath. Only existing files are written (never created). Writes are temp-file +
  ``rename`` with a digest re-check of the target immediately before the rename, so a concurrent
  agent edit wins; an exclusive lock in the state dir serialises runs.
* **No configuration input.** The sync reads no brief, pin or project config — nothing an agent
  could edit changes what it does beyond the learned text itself.
* **Idempotent.** A run with nothing to do writes nothing, the baseline included.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from agentteams import learned_blocks as lb
from agentteams.scan import scan_content

#: ``(framework id, agents dir relative to the project, file suffix, host format)``.
FRAMEWORK_DIRS: tuple[tuple[str, str, str, str], ...] = (
    ("github", ".github/agents", ".agent.md", lb.MARKDOWN),
    ("claude", ".claude/agents", ".md", lb.MARKDOWN),
    ("goose", ".goose/recipes", ".yaml", lb.RECIPE),
)
#: The framework whose targets are never written without ``--include-claude``.
CLAUDE = "claude"

BASELINE_NAME = "agent-doc-sync.json"
CONFLICTS_NAME = "conflicts.json"
PENDING_NAME = "pending-claude.json"
LOCK_NAME = "agent-doc-sync.lock"
_STATE_VERSION = 1

#: Exit codes: clean (including applied/staged changes), attention needed, fatal.
EXIT_OK, EXIT_ATTENTION, EXIT_FATAL = 0, 1, 2


class SyncError(RuntimeError):
    """A fatal precondition failed (bad project, unsafe state dir, lock held)."""


@dataclass
class _Copy:
    """One framework copy of one agent file, as read."""

    fw: str
    kind: str
    dir_fd: int
    name: str
    raw: bytes
    st: os.stat_result
    text: str
    parsed: lb.ParsedDoc

    @property
    def value(self) -> str | None:
        """Return the block digest, or None when the file has no block."""
        return self.parsed.block.digest if self.parsed.block is not None else None


@dataclass
class SyncReport:
    """What one run did (or, under ``--check``, would do)."""

    applied: bool = False
    lines: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    would_write: list[str] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    skipped_concurrent: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """Return :data:`EXIT_ATTENTION` when anything needs a human, else :data:`EXIT_OK`."""
        if self.conflicts or self.quarantined or self.refused or self.skipped_concurrent:
            return EXIT_ATTENTION
        return EXIT_OK

    def note(self, line: str) -> None:
        """Record one report line.

        Args:
            line: The text (printed by :func:`run_sync_agent_docs`).
        """
        self.lines.append(line)


# ---------------------------------------------------------------------------
# State directory
# ---------------------------------------------------------------------------

def project_hash(project: Path) -> str:
    """Return the 16-hex-char id of a project (sha256 of its realpath).

    Args:
        project: The project root.

    Returns:
        ``sha256(os.path.realpath(project))[:16]``.
    """
    return hashlib.sha256(os.path.realpath(project).encode("utf-8")).hexdigest()[:16]


def state_dir_for(project: Path) -> Path:
    """Return the per-project state dir (honours an absolute ``XDG_STATE_HOME``).

    Args:
        project: The project root.

    Returns:
        ``<XDG_STATE_HOME or ~/.local/state>/agentteams/<project_hash>``. A relative
        ``XDG_STATE_HOME`` is ignored, per the XDG spec.
    """
    base = os.environ.get("XDG_STATE_HOME", "")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "state"
    return root / "agentteams" / project_hash(project)


def _ensure_state_dir(state: Path, project_real: str, create: bool) -> bool:
    """Validate (and optionally create, mode 0700) the state dir; False when absent and not created.

    Raises:
        SyncError: The state dir lies inside the project, or is not a real directory.
    """
    real = os.path.realpath(state)
    if real == project_real or real.startswith(project_real + os.sep):
        raise SyncError(f"state dir {state} lies inside the project; refusing (agents could edit it)")
    if not os.path.lexists(state):
        if not create:
            return False
        os.makedirs(state, mode=0o700, exist_ok=True)
    st = os.lstat(state)
    if not stat.S_ISDIR(st.st_mode):
        raise SyncError(f"state dir {state} is not a real directory (symlink or file); refusing")
    return True


def _load_json(path: Path) -> dict[str, Any]:
    """Load a state JSON object; an absent file is ``{}``.

    Raises:
        SyncError: The file exists but is unreadable, a symlink, or not a JSON object.
    """
    if not os.path.lexists(path):
        return {}
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise SyncError(f"{path} is not a regular file; refusing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SyncError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SyncError(f"{path} is not a JSON object")
    return data


def _write_state(path: Path, data: Any) -> bool:
    """Atomically write a state file (mode 0600) only when its bytes would change.

    Returns:
        True when the file was written.
    """
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if os.path.lexists(path) and stat.S_ISREG(os.lstat(path).st_mode):
        try:
            same = path.read_text(encoding="utf-8") == text
        except (OSError, UnicodeDecodeError):
            same = False
        if same:
            return False
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return True


def _write_state_text(path: Path, text: str) -> None:
    """Write a state text file (quarantine/backup) once; an existing file is left as is."""
    if os.path.lexists(path):
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _acquire_lock(state: Path) -> int:
    """Take the exclusive, non-blocking run lock; return its fd.

    Raises:
        SyncError: Another sync holds the lock, or the platform has no ``fcntl``.
    """
    try:
        import fcntl
    except ImportError as exc:
        raise SyncError("--sync-agent-docs --apply needs fcntl (POSIX)") from exc
    fd = os.open(state / LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise SyncError("another --sync-agent-docs run holds the lock") from exc
    return fd


# ---------------------------------------------------------------------------
# Safe filesystem access
# ---------------------------------------------------------------------------

def _open_agent_dir(project_real: str, rel: str) -> int | None:
    """Open an agent dir as an fd, component by component, refusing any symlink; None when absent.

    Each component is opened ``O_NOFOLLOW | O_DIRECTORY`` relative to its parent's fd, so a
    component swapped for a symlink between the check and the open makes the open fail instead of
    being followed (a path-based ``lstat`` walk followed by one ``open`` is racy).

    Raises:
        SyncError: A component is a symlink or not a directory, or the dir escapes the project.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(project_real, flags)
    path = project_real
    try:
        for part in rel.split("/"):
            path = os.path.join(path, part)
            try:
                st = os.stat(part, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                os.close(fd)
                return None
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise SyncError(f"{rel}: {path} is a symlink or not a directory; refusing")
            try:
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise SyncError(f"{rel}: cannot open {path} without following links: {exc}") from exc
            os.close(fd)
            fd = child
        if os.path.realpath(path) != path:
            raise SyncError(f"{rel} resolves outside the project; refusing")
    except SyncError:
        os.close(fd)
        raise
    return fd


def _read_regular(dir_fd: int, name: str) -> tuple[bytes, os.stat_result]:
    """Read a regular, single-link file relative to *dir_fd* without following symlinks.

    Raises:
        OSError: The entry is not a regular single-link file, or changed while being opened.
    """
    lst = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(lst.st_mode) or lst.st_nlink != 1:
        raise OSError(f"{name} is not a regular single-link file")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino) or not stat.S_ISREG(fst.st_mode):
            raise OSError(f"{name} changed while being opened")
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks), fst


def _write_temp(dir_fd: int, tmp: str, data: bytes, mode: int) -> None:
    """Create *tmp* exclusively (``O_NOFOLLOW``) beside the target and write *data* to it."""
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=dir_fd)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def _unchanged(copy: _Copy) -> bool:
    """Return True when the target still holds exactly the bytes (and inode) that were read."""
    try:
        raw, st = _read_regular(copy.dir_fd, copy.name)
    except OSError:
        return False
    return raw == copy.raw and (st.st_dev, st.st_ino) == (copy.st.st_dev, copy.st.st_ino)


def _atomic_replace(copy: _Copy, new_text: str) -> bool:
    """Replace *copy*'s file with *new_text* via temp + rename; False if it changed meanwhile."""
    tmp = f".{copy.name}.agentteams-sync.{secrets.token_hex(6)}.tmp"
    _write_temp(copy.dir_fd, tmp, new_text.encode("utf-8"), stat.S_IMODE(copy.st.st_mode) & 0o777)
    if not _unchanged(copy):
        os.unlink(tmp, dir_fd=copy.dir_fd)
        return False
    os.replace(tmp, copy.name, src_dir_fd=copy.dir_fd, dst_dir_fd=copy.dir_fd)
    os.fsync(copy.dir_fd)
    return True


def _collect(project_real: str, report: SyncReport) -> tuple[dict[str, dict[str, _Copy]], list[int]]:
    """Read every framework copy; return ``{slug: {fw: copy}}`` and the dir fds to close."""
    by_slug: dict[str, dict[str, _Copy]] = {}
    fds: list[int] = []
    for fw, rel, suffix, kind in FRAMEWORK_DIRS:
        try:
            dir_fd = _open_agent_dir(project_real, rel)
        except SyncError as exc:
            report.refused.append(f"{rel}: {exc}")
            report.note(f"  REFUSED {exc}")
            continue
        if dir_fd is None:
            continue
        fds.append(dir_fd)
        for name in sorted(os.listdir(dir_fd)):
            if not name.endswith(suffix) or name.startswith(".") or len(name) <= len(suffix):
                continue
            label = f"{rel}/{name}"
            if os.path.realpath(os.path.join(project_real, rel, name)) != os.path.join(
                    project_real, rel, name):
                report.refused.append(label)
                report.note(f"  REFUSED {label}: symlink or path outside the project")
                continue
            try:
                raw, st = _read_regular(dir_fd, name)
                text = raw.decode("utf-8")
                parsed = lb.parse(text, kind)
            except (OSError, UnicodeDecodeError, lb.LearnedBlockError) as exc:
                report.refused.append(label)
                report.note(f"  REFUSED {label}: {exc}")
                continue
            slug = name[: -len(suffix)]
            by_slug.setdefault(slug, {})[fw] = _Copy(fw, kind, dir_fd, name, raw, st, text, parsed)
    return by_slug, fds


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def _label(copy: _Copy) -> str:
    """Return the project-relative path of a copy."""
    rel = next(r for fw, r, _s, _k in FRAMEWORK_DIRS if fw == copy.fw)
    return f"{rel}/{copy.name}"


def _diff(old: str, new: str, label: str) -> str:
    """Return a unified diff of two block contents."""
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{label} (learned block)", tofile=f"{label} (proposed)"))


def _gate(slug: str, content: str) -> list[str]:
    """Return the content-gate findings for a block about to be propagated."""
    problems = lb.content_problems(content)
    for finding in scan_content(content, filename=f"<learned-block:{slug}>"):
        problems.append(f"scan {finding.severity} {finding.category} (line {finding.line}): "
                        f"{finding.message}")
    return problems


@dataclass
class _Run:
    """Mutable state of one run."""

    report: SyncReport
    apply: bool
    include_claude: bool
    state: Path
    baseline: dict[str, Any]
    conflicts: dict[str, Any]
    pending: dict[str, Any]


def _sync_slug(run: _Run, slug: str, copies: dict[str, _Copy]) -> None:
    """Three-way sync of one agent's learned block across its framework copies."""
    rep = run.report
    base = run.baseline.get(slug) if isinstance(run.baseline.get(slug), dict) else {}
    base_copies = base.get("copies") if isinstance(base.get("copies"), dict) else {}
    agreed = base.get("agreed") if isinstance(base.get("agreed"), str) else None
    removed = sorted(fw for fw, c in copies.items() if c.value is None and base_copies.get(fw))
    for fw in removed:
        rep.note(f"  note {_label(copies[fw])}: learned block removed; removal is never propagated")
    changed = {fw: c.value for fw, c in copies.items()
               if c.value is not None and c.value != base_copies.get(fw)}
    run.conflicts.pop(slug, None)
    if len(set(changed.values())) > 1:
        run.conflicts[slug] = {fw: d for fw, d in sorted(changed.items())}
        rep.conflicts.append(slug)
        rep.note(f"  CONFLICT {slug}: learned block changed differently in "
                 f"{', '.join(sorted(changed))}; nothing written (reconcile by hand)")
        return
    new_agreed = next(iter(changed.values())) if changed else agreed
    source = next((copies[fw] for fw in sorted(copies) if copies[fw].value == new_agreed), None)
    if new_agreed is None or source is None or source.parsed.block is None:
        return
    content = source.parsed.block.content
    targets = [copies[fw] for fw in sorted(copies)
               if copies[fw].value != new_agreed and fw not in removed]
    run.pending.pop(slug, None)
    written: set[str] = set()
    if targets:
        problems = _gate(slug, content)
        if problems:
            rep.quarantined.append(slug)
            rep.note(f"  QUARANTINED {_label(source)}: {'; '.join(problems)}")
            if run.apply:
                _write_state_text(run.state / "quarantine" / f"{slug}.{source.fw}.{new_agreed[:16]}.txt",
                                  f"# {_label(source)}\n# " + "\n# ".join(problems) + "\n" + content)
            return
    for tgt in targets:
        if not _propagate(run, slug, source, tgt, content):
            continue
        written.add(tgt.fw)
    if not run.apply:
        return
    # Written copies now carry the agreed digest; a removed block keeps its old baseline (so the
    # removal is never mistaken for "never had one" and re-inserted); every other present copy
    # records what it holds now (a staged/refused/skipped target is unchanged, so this equals its
    # old baseline); a copy absent this run (missing or refused file) keeps its old baseline.
    entry_copies: dict[str, str] = {
        fw: d for fw, d in base_copies.items() if fw not in copies and isinstance(d, str)
    }
    for fw, c in copies.items():
        if fw in written:
            entry_copies[fw] = new_agreed
        elif fw in removed:
            entry_copies[fw] = base_copies[fw]
        elif c.value is not None:
            entry_copies[fw] = c.value
    run.baseline[slug] = {"agreed": new_agreed, "copies": dict(sorted(entry_copies.items()))}


def _propagate(run: _Run, slug: str, source: _Copy, tgt: _Copy, content: str) -> bool:
    """Compose, verify and (when allowed) write one target; True only when it was written."""
    rep = run.report
    label = _label(tgt)
    new_text = lb.compose(tgt.text, tgt.parsed, content)
    problems = lb.verify_composed(tgt.text, new_text, tgt.kind, content)
    if problems:
        rep.refused.append(label)
        rep.note(f"  REFUSED {label}: {'; '.join(problems)}")
        return False
    old = tgt.parsed.block.content if tgt.parsed.block is not None else ""
    if tgt.fw == CLAUDE and not run.include_claude:
        run.pending[slug] = {"source": _label(source), "target": label,
                             "digest": lb.content_digest(content),
                             "diff": _diff(old, content, label)}
        rep.staged.append(label)
        verb = "STAGED" if run.apply else "would stage"
        rep.note(f"  {verb} {label} <- {_label(source)} (Claude targets need --apply --include-claude)")
        return False
    if not run.apply:
        rep.would_write.append(label)
        rep.note(f"  would write {label} <- {_label(source)}")
        return False
    if tgt.fw == CLAUDE:
        rep.note(_diff(old, content, label).rstrip("\n"))
    if not content and tgt.parsed.block is not None and old:
        _write_state_text(run.state / "backups" / f"{slug}.{tgt.fw}.{lb.content_digest(old)[:16]}.txt",
                          old)
    if not _atomic_replace(tgt, new_text):
        rep.skipped_concurrent.append(label)
        rep.note(f"  SKIPPED {label}: changed while syncing; the concurrent edit wins")
        return False
    rep.written.append(label)
    rep.note(f"  wrote {label} <- {_label(source)}")
    return True


def sync_agent_docs(project: Path, *, apply: bool = False,
                    include_claude: bool = False) -> SyncReport:
    """Run one learned-block sync over a project's agent copies.

    Args:
        project: The project root (holds ``.github/agents``, ``.claude/agents``, ``.goose/recipes``).
        apply: Write targets (default: report only, write nothing — not even state).
        include_claude: With *apply*, also write ``.claude/agents`` targets (otherwise staged).

    Returns:
        The run report.

    Raises:
        SyncError: The project is not a directory, the state dir is unsafe, ``include_claude`` was
            given without ``apply``, or another run holds the lock.
    """
    if include_claude and not apply:
        raise SyncError("--include-claude requires --apply")
    project_real = os.path.realpath(project)
    if not os.path.isdir(project_real):
        raise SyncError(f"project {project} is not a directory")
    state = state_dir_for(Path(project_real))
    have_state = _ensure_state_dir(state, project_real, create=apply)
    lock_fd = _acquire_lock(state) if apply else None
    report = SyncReport(applied=apply)
    fds: list[int] = []
    try:
        doc = _load_json(state / BASELINE_NAME) if have_state else {}
        slugs = doc.get("slugs") if isinstance(doc.get("slugs"), dict) else {}
        conflicts = _load_json(state / CONFLICTS_NAME) if have_state else {}
        pending = _load_json(state / PENDING_NAME) if have_state else {}
        run = _Run(report, apply, include_claude, state, dict(slugs), dict(conflicts), dict(pending))
        by_slug, fds = _collect(project_real, report)
        for slug in sorted(by_slug):
            if len(by_slug[slug]) > 1:
                _sync_slug(run, slug, by_slug[slug])
        for slug in [s for s in run.pending if s not in by_slug or CLAUDE not in by_slug[s]]:
            run.pending.pop(slug)
        if apply:
            _write_state(state / BASELINE_NAME, {"version": _STATE_VERSION, "project": project_real,
                                                 "slugs": dict(sorted(run.baseline.items()))})
            _persist_optional(state / CONFLICTS_NAME, run.conflicts)
            _persist_optional(state / PENDING_NAME, run.pending)
        for slug, item in sorted(run.pending.items()):
            if isinstance(item, dict) and str(item.get("target")) not in report.staged:
                report.note(f"  pending {item.get('target')} (staged earlier; --apply --include-claude)")
    finally:
        for fd in fds:
            os.close(fd)
        if lock_fd is not None:
            os.close(lock_fd)
    return report


def _persist_optional(path: Path, data: dict[str, Any]) -> None:
    """Write a conflicts/pending file when non-empty; remove it once it becomes empty."""
    if data:
        _write_state(path, dict(sorted(data.items())))
    elif os.path.lexists(path) and stat.S_ISREG(os.lstat(path).st_mode):
        os.unlink(path)


def run_sync_agent_docs(project: Path, *, apply: bool = False, include_claude: bool = False,
                        out: TextIO | None = None) -> int:
    """CLI wrapper: run :func:`sync_agent_docs`, print the report, return the exit code.

    Args:
        project: The project root.
        apply: Write targets (``--apply``).
        include_claude: Also write ``.claude/agents`` targets (``--include-claude``).
        out: Stream for the report (default ``sys.stdout``).

    Returns:
        :data:`EXIT_OK`, :data:`EXIT_ATTENTION` (conflict, quarantine, refusal or concurrent-edit
        skip), or :data:`EXIT_FATAL` (a :class:`SyncError`).
    """
    import sys

    stream = out if out is not None else sys.stdout
    mode = "apply" if apply else "check"
    if apply and include_claude:
        mode = "apply, include-claude"
    try:
        report = sync_agent_docs(project, apply=apply, include_claude=include_claude)
    except SyncError as exc:
        print(f"Agent-doc sync ({mode}): FATAL: {exc}", file=stream)
        return EXIT_FATAL
    print(f"Agent-doc sync ({mode}): {os.path.realpath(project)}", file=stream)
    for line in report.lines:
        print(line, file=stream)
    if not report.lines:
        print("  in sync: nothing to do", file=stream)
    print(f"  summary: {len(report.written)} written, {len(report.would_write)} would write, "
          f"{len(report.staged)} staged for Claude, {len(report.conflicts)} conflict(s), "
          f"{len(report.quarantined)} quarantined, {len(report.refused)} refused, "
          f"{len(report.skipped_concurrent)} skipped (concurrent edit)", file=stream)
    return report.exit_code
