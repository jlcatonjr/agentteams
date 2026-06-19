"""
stale_detector.py — read-only detection of stale agent docs and code/scripts.

Scans a target repository and reports staleness across reliability-tiered signals
(see references/plans/stale-detector-methods.report.md):

- Tier 1 (deterministic, blocking): ``VCS_CONFLICT_MARKER`` (a full ordered git
  merge-conflict triad), ``BROKEN_REF`` (a markdown-link target absent on disk),
  and provenance-gated ``INTEGRITY`` / ``SOURCE_DRIFT``.
- Tier 2 (heuristic, advisory): ``STALE_VS_CODE`` (referenced code committed after
  the doc's last commit), plus inline/anchored broken references.
- Tier 0 (INFO): ``PROVENANCE_ABSENT`` / ``BRIDGE_SOURCE_UNAVAILABLE``.

This module is strictly read-only detection + the preview ``build_remediation_plan``.
``.agentteams-stale-ignore`` (a gitignore-style file at the scan root) suppresses
matching findings — never ``VCS_CONFLICT_MARKER``. The opt-in, ``--yes``-gated revision
phase that actually writes (snapshot/restore, reference repair, bridge re-merge) lives in
the sibling module ``agentteams/stale_remediate.py``.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agentteams import fleet

# ---------------------------------------------------------------------------
# Scan-set configuration
# ---------------------------------------------------------------------------

# Directories pruned during a non-git walk (a git work-tree uses ``git ls-files``,
# which already excludes gitignored trees such as ``tmp/`` and backups).
_PRUNE_DIRS = {
    ".git", "__pycache__", "node_modules", ".agentteams-backups",
    ".agentteams-fleet", "_site", "dist", ".pytest_cache", ".mypy_cache",
}

# Always skipped, even when tracked: these trees are fixtures or archival/ephemeral
# records whose references to past state are expected to "age" and are not actionable
# staleness. ``tmp/`` is the conventional scratch dir (gitignored in some repos, tracked
# in others — skip it either way); ``examples/`` holds fixture trees (``expected/``);
# ``workSummaries/`` are dated point-in-time logs.
_SKIP_PREFIXES = ("examples/", "workSummaries/", "tmp/")

# Generated/live-data paths whose churn is expected; suppressed from all but Tier-1
# conflict-marker findings. Seeded from fleet's churn-suppression denylist.
_VOLATILE_SUFFIXES = tuple(fleet._VOLATILE_SUFFIXES)

_DOC_SUFFIXES = (".md",)
_SCRIPT_SUFFIXES = (".py", ".sh")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Narrow, non-swallowing I/O helpers (CH-24: no broad catches, no pass/continue
# bodies — each returns a sentinel so the caller decides what to skip).
# ---------------------------------------------------------------------------

def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False


def _safe_read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _safe_json(p: Path) -> dict | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class StalenessFinding:
    """One staleness signal at a location, with a confidence tier and action."""

    tier: int  # 1 | 2 | 3 ; 0 == INFO (cannot-verify)
    code: str
    file: str  # repo-relative
    line: int  # 1-based; 0 when not line-anchored
    signal: str
    detail: str
    suggested_action: str
    auto_remediable: bool = False
    # For BROKEN_REF: the anchor-split bare reference path (used by ignore-matching and
    # repair). Structured so callers never re-parse the human-readable ``detail`` string.
    ref_target: str = ""


@dataclass
class StalenessReport:
    root: str
    scanned_files: int = 0
    findings: list[StalenessFinding] = field(default_factory=list)
    git_available: bool = False
    recency_status: str = "ok"  # ok | unavailable:non-git | unavailable:shallow | disabled
    provenance_sources: int = 0
    suppressed_count: int = 0  # findings dropped by .agentteams-stale-ignore

    @property
    def tier1(self) -> list[StalenessFinding]:
        return [f for f in self.findings if f.tier == 1]

    @property
    def tier2(self) -> list[StalenessFinding]:
        return [f for f in self.findings if f.tier == 2]

    @property
    def tier3(self) -> list[StalenessFinding]:
        return [f for f in self.findings if f.tier == 3]

    @property
    def info(self) -> list[StalenessFinding]:
        return [f for f in self.findings if f.tier == 0]

    @property
    def has_blocking(self) -> bool:
        return any(f.tier == 1 for f in self.findings)


# ---------------------------------------------------------------------------
# Fenced-code stripping (preserves 1-based line numbers)
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _strip_fenced_code(text: str) -> str:
    """Blank the body and delimiters of fenced code blocks, preserving line count.

    Each stripped line becomes empty so downstream detectors keep correct 1-based
    line numbers. An unterminated fence blanks to end-of-file.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# T1a — VCS conflict markers
# ---------------------------------------------------------------------------

_CONFLICT_OPEN = re.compile(r"^<<<<<<<(\s|$)")
_CONFLICT_MID = re.compile(r"^=======\s*$")
_CONFLICT_CLOSE = re.compile(r"^>>>>>>>(\s|$)")


def detect_conflict_markers(text: str, rel_path: str) -> list[StalenessFinding]:
    """Find complete, ordered git merge-conflict triads (Tier-1).

    Requires ``<<<<<<<`` then ``=======`` then ``>>>>>>>`` in order with no second
    ``<<<<<<<`` opening in between. A standalone ``=======`` (a Markdown setext
    underline) is ignored. diff3 ``|||||||`` lines between open and mid are harmless.
    Example blocks are excluded by stripping fenced code first.
    """
    findings: list[StalenessFinding] = []
    lines = _strip_fenced_code(text).splitlines()
    state = "OPEN"  # OPEN -> MID -> CLOSE
    open_line = 0
    for i, line in enumerate(lines, start=1):
        if _CONFLICT_OPEN.match(line):
            state, open_line = "MID", i
            continue
        if state == "MID" and _CONFLICT_MID.match(line):
            state = "CLOSE"
            continue
        if state == "CLOSE" and _CONFLICT_CLOSE.match(line):
            findings.append(StalenessFinding(
                tier=1, code="VCS_CONFLICT_MARKER", file=rel_path, line=open_line,
                signal="merge-conflict markers",
                detail="unresolved git conflict triad (<<<<<<< ======= >>>>>>>)",
                suggested_action="resolve the conflict by hand at this location; never auto-resolved",
                auto_remediable=False,
            ))
            state, open_line = "OPEN", 0
    return findings


# ---------------------------------------------------------------------------
# References & broken-reference detection
# ---------------------------------------------------------------------------

@dataclass
class Reference:
    raw: str
    path: str
    anchor: str  # "" when none
    line: int
    kind: str  # "md_link"


_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_GLOB_VAR_CHARS = set("*?{}$~<>")


def _suppress_ref(token: str) -> bool:
    """True when a reference token is not a verifiable repo-relative path."""
    if not token:
        return True
    low = token.lower()
    if low.startswith(("http://", "https://", "mailto:", "//")):
        return True
    if token.startswith("#"):
        return True
    if token.startswith("/"):  # absolute machine path — illustrative, not verifiable
        return True
    if token.endswith("/"):  # directory-only reference
        return True
    if any(c in _GLOB_VAR_CHARS for c in token):
        return True
    return False


def _split_anchor(token: str) -> tuple[str, str]:
    """Split a trailing ``#anchor`` or ``:line`` off a path token."""
    anchor = ""
    if "#" in token:
        token, anchor = token.split("#", 1)
    # Strip a trailing ``:line`` or ``:symbol`` reference (e.g. ``file.py:42`` or
    # ``module.py:func_name``) so the path itself can be resolved; the suffix marks
    # the reference as anchored (Tier-2).
    m = re.search(r":([\w.\-]+)$", token)
    if m:
        anchor = anchor or m.group(1)
        token = token[: m.start()]
    return token, anchor


def extract_references(text: str) -> list[Reference]:
    """Extract verifiable markdown-link references.

    Only markdown links ``[text](path)`` are treated as references — they are
    *intentional navigation*, so a broken one is a real problem. Inline-code path
    mentions are deliberately NOT scanned: in living docs they routinely name
    generated-output paths, illustrative examples, and historical locations that do
    not exist in the source tree, producing false positives that erode trust.
    """
    refs: list[Reference] = []
    for i, line in enumerate(_strip_fenced_code(text).splitlines(), start=1):
        for m in _MD_LINK_RE.finditer(line):
            tok = m.group(1).strip()
            if _suppress_ref(tok):
                continue
            path, anchor = _split_anchor(tok)
            if not path or _suppress_ref(path):
                continue
            refs.append(Reference(raw=tok, path=path, anchor=anchor, line=i, kind="md_link"))
    return refs


def _resolve_ref(ref: Reference, doc_path: Path, root: Path) -> Path | None:
    """Return the resolved path for a reference, or None if it resolves nowhere."""
    candidates = [doc_path.parent / ref.path, root / ref.path]
    for cand in candidates:
        if _safe_exists(cand):
            return cand.resolve()
    return None


def detect_broken_refs(text: str, doc_path: Path, root: Path) -> list[StalenessFinding]:
    """Flag references whose target is absent on disk (Tier-1 md-links / Tier-2 rest)."""
    findings: list[StalenessFinding] = []
    rel = _relpath(doc_path, root)
    for ref in extract_references(text):
        if _resolve_ref(ref, doc_path, root) is not None:
            continue  # resolves; anchors are not validated in v1
        tier1 = ref.kind == "md_link" and not ref.anchor
        findings.append(StalenessFinding(
            tier=1 if tier1 else 2,
            code="BROKEN_REF", file=rel, line=ref.line,
            signal="broken reference",
            detail=f"reference {ref.raw!r} resolves to no file on disk",
            suggested_action="update or remove the reference; if the file moved, point it at the new path",
            auto_remediable=False,
            ref_target=ref.path,
        ))
    return findings


# ---------------------------------------------------------------------------
# T2a — git-recency divergence
# ---------------------------------------------------------------------------

GitRunner = Callable[..., object]


def _git_out(git: GitRunner, root: Path, *args: str) -> str | None:
    """Run git and return stripped stdout, or None on non-zero exit."""
    proc = git(root, *args)
    if getattr(proc, "returncode", 1) != 0:
        return None
    return (getattr(proc, "stdout", "") or "").strip()


def _is_shallow(git: GitRunner, root: Path) -> bool:
    return _git_out(git, root, "rev-parse", "--is-shallow-repository") == "true"


def detect_git_recency(
    root: Path,
    doc_to_refs: dict[str, set[str]],
    *,
    git: GitRunner = fleet._git,
) -> tuple[list[StalenessFinding], str]:
    """Flag docs whose referenced code changed in a commit after the doc's last commit.

    Returns ``(findings, recency_status)``. Self-disables on non-git / shallow repos.
    A finding requires both a commit strictly after the doc's last commit touching the
    code AND a substantive (whitespace-filtered) diff — neutralizing reverts/cherry-picks.
    """
    if not fleet._is_git_repo(root):
        return [], "unavailable:non-git"
    if _is_shallow(git, root):
        return [], "unavailable:shallow"

    findings: list[StalenessFinding] = []
    for doc_rel, code_rels in sorted(doc_to_refs.items()):
        # Skip docs with uncommitted changes (an uncommitted fix would false-positive).
        if _git_out(git, root, "status", "--porcelain", "--", doc_rel):
            continue
        doc_commit = _git_out(git, root, "rev-list", "-1", "HEAD", "--", doc_rel)
        if not doc_commit:
            continue  # untracked / no history
        for code_rel in sorted(code_rels):
            if _git_out(git, root, "status", "--porcelain", "--", code_rel):
                continue
            after = _git_out(git, root, "rev-list", f"{doc_commit}..HEAD", "--", code_rel)
            if not after:
                continue  # code unchanged since the doc's last commit
            diff = _git_out(git, root, "diff", "-w", doc_commit, "HEAD", "--", code_rel)
            if not diff:
                continue  # whitespace-only / reverted to identical content
            findings.append(StalenessFinding(
                tier=2, code="STALE_VS_CODE", file=doc_rel, line=0,
                signal="doc older than referenced code",
                detail=(
                    f"{code_rel} changed after {doc_rel}'s last commit "
                    f"({doc_commit[:8]}); the doc may describe outdated behavior "
                    "(whitespace filtered; comment/format-only changes not excluded)"
                ),
                suggested_action=f"review {doc_rel} against current {code_rel} and update if stale",
                auto_remediable=False,
            ))
    return findings, "ok"


# ---------------------------------------------------------------------------
# Provenance-gated detectors (T1c integrity / T1d bridge drift)
# ---------------------------------------------------------------------------

def _provenance_paths(root: Path, pattern: str, allowed_rel: set[str] | None) -> list[Path]:
    """Discovered provenance files, restricted to the scan-set when one is given.

    ``allowed_rel`` is the relative-path set the aggregator already filtered through
    ``git ls-files`` / prune / ``examples`` skip — applying it here keeps gitignored
    sandboxes (``tmp/``) and fixture trees from flooding provenance findings.
    """
    out: list[Path] = []
    for p in sorted(root.rglob(pattern)):
        if allowed_rel is not None and _relpath(p, root) not in allowed_rel:
            continue
        out.append(p)
    return out


def detect_integrity(root: Path, allowed_rel: set[str] | None = None) -> list[StalenessFinding]:
    """Reuse drift.verify_output_integrity for every discovered build-log."""
    from agentteams import drift

    findings: list[StalenessFinding] = []
    for build_log in _provenance_paths(root, "references/build-log.json", allowed_rel):
        agents_dir = build_log.parent.parent
        try:
            results = drift.verify_output_integrity(agents_dir)
        except (OSError, ValueError):
            results = []  # unreadable/corrupt build-log — skip this source, don't crash
        for entry in results:
            status = entry.get("status")
            rel = _relpath(Path(entry.get("path", "")), root) if entry.get("path") else entry.get("rel_path", "")
            if status in ("FENCE-BROKEN", "TRUNCATED", "MISSING"):
                findings.append(StalenessFinding(
                    tier=1, code="INTEGRITY", file=rel, line=0,
                    signal=f"generated file {status}",
                    detail=entry.get("note", ""),
                    suggested_action=f"re-run `agentteams --update --merge --output {agents_dir}`",
                    auto_remediable=True,
                ))
            elif status == "MODIFIED":
                findings.append(StalenessFinding(
                    tier=2, code="INTEGRITY", file=rel, line=0,
                    signal="generated file MODIFIED",
                    detail=entry.get("note", ""),
                    suggested_action="review (legitimate USER-EDITABLE edit or drift)",
                    auto_remediable=False,
                ))
    return findings


def detect_bridge_drift(root: Path, allowed_rel: set[str] | None = None) -> list[StalenessFinding]:
    """Check each bridge manifest's source freshness, gated on source presence."""
    from agentteams import bridge

    findings: list[StalenessFinding] = []
    for manifest_path in _provenance_paths(
        root, "references/bridges/*/bridge-manifest.json", allowed_rel
    ):
        manifest = _safe_json(manifest_path)
        if manifest is None:
            continue  # unreadable/corrupt manifest — outside any try block
        source_dir = Path(manifest.get("source_dir", ""))
        rel_manifest = _relpath(manifest_path, root)
        try:
            files = bridge._collect_source_files(source_dir) if source_dir.is_dir() else []
        except OSError:
            files = []  # source unreadable — treat as unavailable below
        if not files:
            findings.append(StalenessFinding(
                tier=0, code="BRIDGE_SOURCE_UNAVAILABLE", file=rel_manifest, line=0,
                signal="bridge source not present",
                detail=f"source_dir {source_dir} is not available on this machine",
                suggested_action=(
                    f"run `agentteams --bridge-from {source_dir} --bridge-check` "
                    "where the source is present to verify bridge freshness"
                ),
                auto_remediable=False,
            ))
            continue
        try:
            rows = bridge._compute_hash_rows(files, source_dir)
            ok, _report = bridge._run_bridge_check(manifest_path=manifest_path, source_hash_rows=rows)
        except (OSError, ValueError, KeyError, TypeError):
            ok = None  # bridge check failed to run — skip (assignment body, not a swallow)
        if ok is None:
            continue
        if not ok:
            findings.append(StalenessFinding(
                tier=1, code="SOURCE_DRIFT", file=rel_manifest, line=0,
                signal="bridge source diverged",
                detail="source agent files differ from the bridge-manifest snapshot",
                suggested_action=f"re-run `agentteams --bridge-from {source_dir} --bridge-merge`",
                auto_remediable=True,
            ))
    return findings


def detect_provenance(root: Path, allowed_rel: set[str] | None = None) -> list[StalenessFinding]:
    """Integrity ∪ bridge drift; a single INFO when no provenance exists anywhere."""
    integrity = detect_integrity(root, allowed_rel)
    bridge_findings = detect_bridge_drift(root, allowed_rel)
    have_build_log = bool(_provenance_paths(root, "references/build-log.json", allowed_rel))
    have_bridge = bool(
        _provenance_paths(root, "references/bridges/*/bridge-manifest.json", allowed_rel)
    )
    if not have_build_log and not have_bridge:
        return [StalenessFinding(
            tier=0, code="PROVENANCE_ABSENT", file=".", line=0,
            signal="no generation provenance",
            detail="no references/build-log.json or bridge-manifest.json found under the scan root",
            suggested_action="generated-artifact integrity cannot be verified on this target",
            auto_remediable=False,
        )]
    return integrity + bridge_findings


# ---------------------------------------------------------------------------
# File-set & aggregator
# ---------------------------------------------------------------------------

def _relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return str(path)


def _skip_rel(rel: str) -> bool:
    if any(rel == s or rel.startswith(s) for s in _SKIP_PREFIXES):
        return True
    parts = set(Path(rel).parts)
    return bool(parts & _PRUNE_DIRS)


def _iter_scan_files(root: Path, git: GitRunner = fleet._git) -> list[Path]:
    """Tracked files via ``git ls-files`` in a work-tree; else a pruned walk."""
    files: list[Path] = []
    if fleet._is_git_repo(root):
        out = _git_out(git, root, "ls-files")
        for rel in (out or "").splitlines():
            rel = rel.strip()
            if not rel or _skip_rel(rel):
                continue
            p = root / rel
            if p.is_file():
                files.append(p)
        return files
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = _relpath(p, root)
        if _skip_rel(rel):
            continue
        files.append(p)
    return files


def _is_volatile(rel: str) -> bool:
    return any(rel.endswith(s) or rel == s for s in _VOLATILE_SUFFIXES)


# ---------------------------------------------------------------------------
# .agentteams-stale-ignore — maintainer-controlled finding suppression
# ---------------------------------------------------------------------------

_STALE_IGNORE_FILE = ".agentteams-stale-ignore"


def _load_stale_ignore(root: Path) -> list[str]:
    """Read ``<root>/.agentteams-stale-ignore`` (gitignore-style); missing → []."""
    text = _safe_read_text(root / _STALE_IGNORE_FILE)
    if text is None:
        return []
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _match_ignore(rel_path: str, pattern: str) -> bool:
    """Match a POSIX relative path against one ignore pattern.

    Not bare fnmatch (fnmatch != gitignore). Supports: exact path; **dir-prefix**
    (``dir`` or ``dir/`` matches the directory and everything under it); and ``*`` globs.
    """
    pat = pattern.rstrip("/")
    if not pat:
        return False
    if rel_path == pat or rel_path.startswith(pat + "/"):
        return True
    return fnmatch.fnmatch(rel_path, pat)


def _is_ignored(finding: StalenessFinding, patterns: list[str]) -> bool:
    """True when a finding should be suppressed. VCS_CONFLICT_MARKER is NEVER
    suppressible — a complete conflict triad is always broken."""
    if finding.code == "VCS_CONFLICT_MARKER":
        return False
    targets = [finding.file]
    if finding.code == "BROKEN_REF" and finding.ref_target:
        targets.append(finding.ref_target)
    return any(_match_ignore(t, p) for t in targets for p in patterns)


def scan_staleness(
    root: Path,
    *,
    include_git: bool = True,
    git: GitRunner = fleet._git,
) -> StalenessReport:
    """Run all detectors over ``root`` and return an aggregated read-only report."""
    root = Path(root)
    report = StalenessReport(root=str(root), git_available=fleet._is_git_repo(root))

    files = _iter_scan_files(root, git)
    report.scanned_files = len(files)
    allowed_rel = {_relpath(p, root) for p in files}

    doc_to_refs: dict[str, set[str]] = {}
    for path in files:
        rel = _relpath(path, root)
        suffix = path.suffix.lower()
        is_doc = suffix in _DOC_SUFFIXES
        is_script = suffix in _SCRIPT_SUFFIXES
        if not (is_doc or is_script):
            continue
        text = _safe_read_text(path)
        if text is None:
            continue

        # Tier-1 conflict markers on docs AND scripts (not volatile-suppressed).
        report.findings.extend(detect_conflict_markers(text, rel))

        if is_doc and not _is_volatile(rel):
            report.findings.extend(detect_broken_refs(text, path, root))
            # Build doc->code map for recency (only existing code targets).
            code_targets: set[str] = set()
            for ref in extract_references(text):
                resolved = _resolve_ref(ref, path, root)
                if resolved is None:
                    continue
                if resolved.suffix.lower() in (_SCRIPT_SUFFIXES + _DOC_SUFFIXES):
                    code_targets.add(_relpath(resolved, root))
            code_targets.discard(rel)
            if code_targets:
                doc_to_refs[rel] = code_targets

    if include_git:
        recency, status = detect_git_recency(root, doc_to_refs, git=git)
        report.findings.extend(recency)
        report.recency_status = status
    else:
        report.recency_status = "disabled"

    provenance = detect_provenance(root, allowed_rel)
    report.findings.extend(provenance)
    report.provenance_sources = (
        len(_provenance_paths(root, "references/build-log.json", allowed_rel))
        + len(_provenance_paths(root, "references/bridges/*/bridge-manifest.json", allowed_rel))
    )

    # Suppress findings matched by .agentteams-stale-ignore (maintainer intent) BEFORE
    # dedupe/exit-code/remediation so they drop out of every downstream consumer. A
    # conflict triad is never suppressible (see _is_ignored).
    ignore_patterns = _load_stale_ignore(root)
    if ignore_patterns:
        kept = [f for f in report.findings if not _is_ignored(f, ignore_patterns)]
        report.suppressed_count = len(report.findings) - len(kept)
        report.findings = kept

    # Dedupe: the same physical file can be recorded in more than one build-log
    # (e.g. .vscode/tasks.json emitted by both the github and goose agents dirs),
    # which would otherwise report one finding per provenance source.
    seen: set[tuple] = set()
    deduped: list[StalenessFinding] = []
    for f in report.findings:
        key = (f.tier, f.code, f.file, f.line, f.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    deduped.sort(key=lambda f: (f.tier if f.tier else 99, f.file, f.line))
    report.findings = deduped
    return report


# ---------------------------------------------------------------------------
# Reporting, exit code, remediation
# ---------------------------------------------------------------------------

def exit_code(report: StalenessReport) -> int:
    """0 clean; 1 any Tier-1 (blocking). (3 reserved for unresolved remediation.)"""
    return 1 if report.has_blocking else 0


def print_staleness_report(report: StalenessReport) -> None:
    import sys

    print(f"Stale-check: scanned {report.scanned_files} file(s) under {report.root}")
    suppressed = f" | suppressed: {report.suppressed_count}" if report.suppressed_count else ""
    print(
        f"  git: {'yes' if report.git_available else 'no'} | "
        f"recency: {report.recency_status} | "
        f"provenance sources: {report.provenance_sources}{suppressed}"
    )
    if not report.findings:
        print("  ✓  No staleness detected.")
        return

    for f in report.tier1:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  [TIER-1 {f.code}] {loc} — {f.detail}", file=sys.stderr)
    for f in report.tier2:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  [tier-2 {f.code}] {loc} — {f.detail}")
    for f in report.tier3:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  [tier-3 {f.code}] {loc} — {f.detail}")
    for f in report.info:
        print(f"  [info  {f.code}] {f.file} — {f.detail}")

    n1, n2 = len(report.tier1), len(report.tier2)
    print(
        f"\n{n1} blocking (Tier-1), {n2} advisory (Tier-2), "
        f"{len(report.tier3)} Tier-3, {len(report.info)} info."
    )
    if report.has_blocking:
        print("Blocking staleness found — see Tier-1 findings above.", file=sys.stderr)


def build_remediation_plan(report: StalenessReport) -> list[dict]:
    """Per actionable finding, a suggested action (no file writes in v1)."""
    plan: list[dict] = []
    for f in report.findings:
        if f.code == "VCS_CONFLICT_MARKER":
            plan.append({
                "code": f.code, "file": f.file,
                "action": "manual", "command": None,
                "manual_reason": "merge-conflict markers are never auto-resolved; resolve by hand",
            })
        elif f.code in ("INTEGRITY", "SOURCE_DRIFT") and f.auto_remediable:
            plan.append({
                "code": f.code, "file": f.file,
                "action": "run-safe-writer", "command": f.suggested_action,
                "manual_reason": None,
            })
        elif f.code == "BROKEN_REF":
            plan.append({
                "code": f.code, "file": f.file,
                "action": "manual", "command": None,
                "manual_reason": "update the reference; rename auto-fix is not applied in v1",
            })
    return plan


def print_remediation_plan(plan: list[dict]) -> None:
    if not plan:
        print("\nRemediation: nothing actionable.")
        return
    print(f"\nRemediation plan ({len(plan)} item(s)) — suggested only, no files edited:")
    for row in plan:
        if row["action"] == "run-safe-writer":
            print(f"  [{row['code']}] {row['file']}: {row['command']}")
        else:
            print(f"  [{row['code']}] {row['file']}: MANUAL — {row['manual_reason']}")
    print("  (re-run with --stale-remediate --yes to apply the safe revisions)")

