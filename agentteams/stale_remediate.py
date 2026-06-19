"""
stale_remediate.py — the opt-in, ``--yes``-gated revision phase for stale findings.

Split from ``stale_detector.py`` (CH-07: keep modules under the line ceiling; detection
vs. write-phase are a clean seam). Takes a sha256-verified safety snapshot of every file
it will touch into ``.agentteams-backups/stale-fix-<ts>/`` (recoverable via
``restore_snapshot`` / ``--stale-restore``) BEFORE writing, then performs only safe,
deterministic revisions — broken-reference relocation repair (never inside a fenced /
USER-EDITABLE region) and bridge re-merge for ``SOURCE_DRIFT`` (the canonical fence-aware
writer). ``INTEGRITY`` is routed (the ``--update --merge`` command is printed, not
auto-run). Conflict markers are never auto-resolved.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agentteams import fleet
from agentteams.stale_detector import (
    GitRunner,
    StalenessReport,
    _iter_scan_files,
    _relpath,
    _safe_json,
    _safe_read_text,
)

_SNAPSHOT_PREFIX = "stale-fix-"

# A file carrying any managed/user-editable fence is never auto-edited by the
# reference repairer (route to manual) — only the canonical writers touch fences.
_FENCE_MARKER_RE = re.compile(r"AGENTTEAMS(-BRIDGE)?:(BEGIN|END)|USER[- ]?EDITABLE", re.I)


@dataclass
class FixAction:
    code: str
    file: str
    action: str  # repaired-ref | bridge-merge | routed | manual | failed
    applied: bool
    detail: str


@dataclass
class FixResult:
    snapshot_dir: str | None
    actions: list[FixAction] = field(default_factory=list)
    applied: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def n_applied(self) -> int:
        return sum(1 for a in self.actions if a.applied)

    @property
    def n_unresolved(self) -> int:
        # Blocking findings left for manual handling (the exit-3 condition).
        return sum(1 for a in self.actions if a.action in ("manual", "failed", "routed"))


# --- safety snapshot / restore --------------------------------------------

def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def snapshot_files(root: Path, rel_paths: list[str], *, stamp: str | None = None) -> Path | None:
    """Copy each existing file into ``.agentteams-backups/stale-fix-<ts>/files/<rel>``
    with a sha256 manifest. Returns the snapshot dir, or None if nothing to snapshot."""
    rels = sorted({r for r in rel_paths if (root / r).is_file()})
    if not rels:
        return None
    snap = root / ".agentteams-backups" / f"{_SNAPSHOT_PREFIX}{stamp or _utc_stamp()}"
    files_dir = snap / "files"
    entries: list[dict[str, str]] = []
    for rel in rels:
        data = (root / rel).read_bytes()
        dst = files_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        entries.append({"rel": rel, "sha256": hashlib.sha256(data).hexdigest()})
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "manifest.json").write_text(
        json.dumps({"created_utc": _utc_stamp(), "files": entries}, indent=2),
        encoding="utf-8",
    )
    return snap


def _backups_ignored(root: Path, git: GitRunner, sample_rel: str) -> bool:
    """True when ``.agentteams-backups/`` is gitignored (or the repo is non-git, where the
    warning is n/a). Checks an EXISTING path (the just-written snapshot), because a
    trailing-slash gitignore pattern does not match a non-existent bare directory."""
    if not fleet._is_git_repo(root):
        return True  # n/a — no warning for non-git targets
    return git(root, "check-ignore", "-q", sample_rel).returncode == 0


def latest_snapshot(root: Path) -> Path | None:
    base = root / ".agentteams-backups"
    if not base.is_dir():
        return None
    snaps = sorted(p for p in base.glob(f"{_SNAPSHOT_PREFIX}*") if (p / "manifest.json").is_file())
    return snaps[-1] if snaps else None


def restore_snapshot(root: Path, snap_dir: Path) -> list[str]:
    """Restore every file recorded in a snapshot back to ``root``, verifying the
    backup's own sha256 first. Returns the restored relative paths. Raises ValueError
    if the manifest is unreadable or a backup file is corrupt (fail-safe: write nothing)."""
    manifest = _safe_json(snap_dir / "manifest.json")
    if manifest is None:
        raise ValueError(f"unreadable snapshot manifest: {snap_dir / 'manifest.json'}")
    planned: list[tuple[str, bytes]] = []
    for entry in manifest.get("files", []):
        rel = entry["rel"]
        backup_file = snap_dir / "files" / rel
        if not backup_file.is_file():
            raise ValueError(f"snapshot missing backup for {rel}")
        data = backup_file.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise ValueError(f"snapshot backup corrupt for {rel} (sha256 mismatch)")
        planned.append((rel, data))
    # All verified — now write (so a corrupt backup never leaves a half-restore).
    restored: list[str] = []
    for rel, data in planned:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        restored.append(rel)
    return restored


# --- broken-reference repair ----------------------------------------------

def _relocate_target(ref_path: str, tracked: list[str]) -> str | None:
    """If the reference's basename matches exactly one tracked file, return that
    relative path (a deterministic 'file moved' repair); else None."""
    base = ref_path.rsplit("/", 1)[-1]
    if not base or "." not in base:
        return None
    matches = [t for t in tracked if t == base or t.endswith("/" + base)]
    return matches[0] if len(matches) == 1 else None


def _plan_ref_repairs(report: StalenessReport, root: Path, tracked: list[str]) -> list[dict]:
    """For each BROKEN_REF, compute a deterministic relocation rewrite if one exists
    and the host file carries no fences. Returns plan rows (no writes)."""
    plans: list[dict] = []
    for f in report.findings:
        if f.code != "BROKEN_REF" or not f.ref_target:
            continue
        ref_path = f.ref_target
        target = _relocate_target(ref_path, tracked)
        if target is None:
            continue
        doc = root / f.file
        text = _safe_read_text(doc)
        if text is None or _FENCE_MARKER_RE.search(text):
            continue  # never auto-edit a fenced/managed file
        # New href relative to the doc's directory.
        new_href = _rel_href(f.file, target)
        if new_href is None or new_href == ref_path:
            continue
        plans.append({"file": f.file, "old": ref_path, "ref_path": ref_path,
                      "new_href": new_href, "line": f.line})
    return plans


def _rel_href(doc_rel: str, target_rel: str) -> str | None:
    """Relative href from a doc to a target (both repo-relative POSIX), or None."""
    try:
        rel = os.path.relpath(Path(target_rel), Path(doc_rel).parent)
    except ValueError:
        return None  # e.g. cross-drive on Windows — not repairable
    return Path(rel).as_posix()


def _apply_ref_repair(root: Path, plan: dict) -> bool:
    """Rewrite the broken link to the relocated target. Returns True on a real change."""
    doc = root / plan["file"]
    text = _safe_read_text(doc)
    if text is None:
        return False
    # Replace the href inside the markdown link target only (…](old)).
    needle = f"]({plan['old']})"
    repl = f"]({plan['new_href']})"
    if needle not in text:
        return False
    doc.write_text(text.replace(needle, repl), encoding="utf-8")
    return True


# --- orchestration ---------------------------------------------------------

def _bridge_target_rels(root: Path) -> list[str]:
    """The bounded set of files a --bridge-merge may touch, for snapshotting."""
    rels: list[str] = []
    for pattern in (".claude/**/*", "CLAUDE.md", "AGENTS.md", "references/bridges/**/*"):
        for p in root.glob(pattern):
            if p.is_file():
                rels.append(_relpath(p, root))
    return rels


def _run_bridge_merge(root: Path, manifest_path: Path) -> bool:
    """Invoke the canonical fence-aware --bridge-merge writer for one manifest.
    Returns True on success (exit 0)."""
    manifest = _safe_json(manifest_path)
    if manifest is None:
        return False
    source_dir = Path(manifest.get("source_dir", ""))
    if not source_dir.is_dir():
        return False
    from agentteams.cli.commands import _run_bridge
    rc = _run_bridge(
        source_dir=source_dir,
        source_framework=manifest.get("source_framework"),
        target_framework=manifest.get("target_framework", "claude"),
        output=root,
        dry_run=False,
        overwrite=False,
        check_only=False,
        merge_only=True,
    )
    return rc == 0


def apply_fixes(
    report: StalenessReport,
    root: Path,
    *,
    apply: bool,
    git: GitRunner = fleet._git,
) -> FixResult:
    """Plan (and, when ``apply``, perform) safe revisions for the report's findings.

    When ``apply`` is True a verified safety snapshot of every file to be touched is
    written first, so any bad revision is recoverable via ``restore_snapshot``.
    """
    root = Path(root)
    tracked = [_relpath(p, root) for p in _iter_scan_files(root, git)]
    ref_plans = _plan_ref_repairs(report, root, tracked)
    source_drift = [f for f in report.findings if f.code == "SOURCE_DRIFT"]

    result = FixResult(snapshot_dir=None, applied=apply)

    if apply:
        touch = [p["file"] for p in ref_plans]
        if source_drift:
            touch += _bridge_target_rels(root)
        snap = snapshot_files(root, touch)
        result.snapshot_dir = str(snap) if snap else None
        if snap is not None and not _backups_ignored(root, git, _relpath(snap, root)):
            result.warnings.append(
                ".agentteams-backups/ is not gitignored here — the safety snapshot will "
                "show as untracked files. Add '.agentteams-backups/' to .gitignore."
            )

    # 1) Broken-reference repairs (deterministic relocation).
    for plan in ref_plans:
        if apply:
            ok = _apply_ref_repair(root, plan)
            result.actions.append(FixAction(
                "BROKEN_REF", plan["file"], "repaired-ref" if ok else "failed", ok,
                f"{plan['ref_path']} -> {plan['new_href']}",
            ))
        else:
            result.actions.append(FixAction(
                "BROKEN_REF", plan["file"], "repaired-ref", False,
                f"would relocate {plan['ref_path']} -> {plan['new_href']}",
            ))

    # 2) SOURCE_DRIFT -> canonical --bridge-merge writer.
    for f in source_drift:
        manifest_path = root / f.file
        if apply:
            ok = _run_bridge_merge(root, manifest_path)
            result.actions.append(FixAction(
                "SOURCE_DRIFT", f.file, "bridge-merge" if ok else "failed", ok,
                "re-merged bridge" if ok else "bridge-merge failed (snapshot preserved)",
            ))
        else:
            result.actions.append(FixAction(
                "SOURCE_DRIFT", f.file, "bridge-merge", False, "would run --bridge-merge",
            ))

    # 3) INTEGRITY -> routed (printed command, not auto-run). 4) others -> manual.
    for f in report.findings:
        if f.code == "INTEGRITY" and f.tier == 1:
            result.actions.append(FixAction(
                "INTEGRITY", f.file, "routed", False, f.suggested_action,
            ))
        elif f.code == "VCS_CONFLICT_MARKER":
            result.actions.append(FixAction(
                "VCS_CONFLICT_MARKER", f.file, "manual", False,
                "never auto-resolved; fix by hand",
            ))
        elif f.code == "BROKEN_REF" and not any(p["file"] == f.file and p["line"] == f.line for p in ref_plans):
            result.actions.append(FixAction(
                "BROKEN_REF", f.file, "manual", False,
                "no unambiguous relocated target; update by hand",
            ))
    return result


def print_fix_result(result: FixResult) -> None:
    import sys

    verb = "Applied" if result.applied else "Would apply"
    for w in result.warnings:
        print(f"  ⚠  {w}", file=sys.stderr)
    if result.snapshot_dir:
        print(f"\nSafety snapshot: {result.snapshot_dir}")
        print("  (recover with: agentteams --stale-restore  — restores the latest snapshot)")
    print(f"\nRevision plan ({len(result.actions)} item(s)) — {verb.lower()}:")
    for a in result.actions:
        mark = "✓" if a.applied else ("→" if a.action in ("routed", "manual", "bridge-merge", "repaired-ref") else "✗")
        stream = sys.stderr if a.action == "failed" else sys.stdout
        print(f"  {mark} [{a.code}] {a.file}: {a.action} — {a.detail}", file=stream)
    if result.applied:
        print(f"\n{result.n_applied} revision(s) applied; {result.n_unresolved} need manual/routed handling.")
