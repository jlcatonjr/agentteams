"""
Tests for agentteams/stale_detector.py — conflict markers, broken references,
git-recency, provenance, aggregation/exit codes, and CLI wiring.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams import stale_detector as sd
from agentteams import stale_remediate as sr
from agentteams.cli.app import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")


def _commit(path: Path, msg: str) -> None:
    _git(path, "add", "-A")
    _git(path, "-c", "core.hooksPath=/dev/null", "commit", "-q", "--no-verify", "-m", msg)


class _FakeGit:
    """Delegates to real git but forces the shallow query to a fixed answer."""

    def __init__(self, shallow: bool):
        self._shallow = "true" if shallow else "false"

    def __call__(self, repo, *args):
        if args[:2] == ("rev-parse", "--is-shallow-repository"):
            return subprocess.CompletedProcess(args, 0, stdout=self._shallow + "\n", stderr="")
        return _git(repo, *args)


# ---------------------------------------------------------------------------
# Conflict markers (T1a)
# ---------------------------------------------------------------------------

class TestConflictMarkers:
    def test_detects_full_triad(self):
        text = "ok\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> branch\ndone\n"
        f = sd.detect_conflict_markers(text, "x.md")
        assert len(f) == 1
        assert f[0].code == "VCS_CONFLICT_MARKER"
        assert f[0].tier == 1
        assert f[0].line == 2

    def test_ignores_setext_underline(self):
        text = "Heading\n=======\nbody text\n"
        assert sd.detect_conflict_markers(text, "x.md") == []

    def test_ignores_triad_inside_fenced_block(self):
        text = "```\n<<<<<<< HEAD\na\n=======\nb\n>>>>>>> z\n```\n"
        assert sd.detect_conflict_markers(text, "x.md") == []

    def test_ignores_prose_about_conflicts(self):
        text = "To resolve, delete the <<<<<<< and >>>>>>> lines.\n"
        assert sd.detect_conflict_markers(text, "x.md") == []

    def test_detects_diff3_style_with_pipes(self):
        text = "<<<<<<< HEAD\na\n||||||| base\no\n=======\nb\n>>>>>>> x\n"
        f = sd.detect_conflict_markers(text, "x.md")
        assert len(f) == 1


# ---------------------------------------------------------------------------
# Reference extraction & suppression (T1b inputs)
# ---------------------------------------------------------------------------

class TestReferences:
    def test_extracts_md_link_only(self):
        # Markdown links are references; inline-code mentions are NOT (too noisy).
        text = "See [a](src/a.py) and `lib/b.py`.\n"
        refs = sd.extract_references(text)
        paths = {(r.path, r.kind) for r in refs}
        assert ("src/a.py", "md_link") in paths
        assert not any(r.path == "lib/b.py" for r in refs)

    @pytest.mark.parametrize("token", [
        "https://example.com/x.py",
        "//host/x.py",
        "{PLACEHOLDER}.py",
        "src/<repo>/x.py",
        "src/*.py",
        "/Users/me/abs/x.py",
        ".github/agents/",
        "#section",
    ])
    def test_suppresses_non_paths(self, token):
        text = f"[link]({token})\n"
        assert sd.extract_references(text) == []

    def test_splits_anchor_and_line(self):
        refs = sd.extract_references("[a](src/a.py#L10) [b](src/b.py:42)\n")
        by_path = {r.path: r.anchor for r in refs}
        assert by_path["src/a.py"] == "L10"
        assert by_path["src/b.py"] == "42"

    def test_ignores_refs_in_fenced_code(self):
        text = "```\n[x](missing/zzz.py)\n```\n"
        assert sd.extract_references(text) == []


# ---------------------------------------------------------------------------
# Broken references (T1b)
# ---------------------------------------------------------------------------

class TestBrokenRefs:
    def test_missing_md_link_is_tier1(self, tmp_path):
        doc = tmp_path / "doc.md"
        doc.write_text("[gone](nope.py)\n")
        f = sd.detect_broken_refs(doc.read_text(), doc, tmp_path)
        assert len(f) == 1 and f[0].tier == 1 and f[0].code == "BROKEN_REF"

    def test_resolvable_ref_no_finding(self, tmp_path):
        (tmp_path / "real.py").write_text("x=1\n")
        doc = tmp_path / "doc.md"
        doc.write_text("[ok](real.py)\n")
        assert sd.detect_broken_refs(doc.read_text(), doc, tmp_path) == []

    def test_anchored_md_link_is_tier2(self, tmp_path):
        doc = tmp_path / "doc.md"
        doc.write_text("[a](gone.py#L3)\n")
        f = sd.detect_broken_refs(doc.read_text(), doc, tmp_path)
        assert len(f) == 1 and f[0].tier == 2

    def test_inline_code_path_not_flagged(self, tmp_path):
        # Inline-code paths are intentionally not treated as references.
        doc = tmp_path / "doc.md"
        doc.write_text("the module `missing/lib.py` is gone\n")
        assert sd.detect_broken_refs(doc.read_text(), doc, tmp_path) == []

    def test_resolves_relative_to_doc_dir(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "sib.py").write_text("y=2\n")
        doc = sub / "doc.md"
        doc.write_text("[s](sib.py)\n")
        assert sd.detect_broken_refs(doc.read_text(), doc, tmp_path) == []


# ---------------------------------------------------------------------------
# Git recency (T2a)
# ---------------------------------------------------------------------------

class TestGitRecency:
    def _seed(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("[h](helper.py)\n")
        (tmp_path / "helper.py").write_text('print("v1")\n')
        _commit(tmp_path, "init")

    def test_flags_code_changed_after_doc(self, tmp_path):
        self._seed(tmp_path)
        (tmp_path / "helper.py").write_text('print("v2 substantially different")\n')
        _commit(tmp_path, "change code")
        findings, status = sd.detect_git_recency(tmp_path, {"doc.md": {"helper.py"}})
        assert status == "ok"
        assert any(f.code == "STALE_VS_CODE" and f.tier == 2 for f in findings)

    def test_revert_to_identical_not_flagged(self, tmp_path):
        self._seed(tmp_path)
        (tmp_path / "helper.py").write_text('print("v2")\n')
        _commit(tmp_path, "change")
        (tmp_path / "helper.py").write_text('print("v1")\n')  # reverted to doc-era content
        _commit(tmp_path, "revert")
        findings, _ = sd.detect_git_recency(tmp_path, {"doc.md": {"helper.py"}})
        assert not findings

    def test_shallow_self_disables(self, tmp_path):
        self._seed(tmp_path)
        findings, status = sd.detect_git_recency(
            tmp_path, {"doc.md": {"helper.py"}}, git=_FakeGit(shallow=True)
        )
        assert status == "unavailable:shallow" and findings == []

    def test_non_git_self_disables(self, tmp_path):
        (tmp_path / "doc.md").write_text("x\n")
        findings, status = sd.detect_git_recency(tmp_path, {"doc.md": {"helper.py"}})
        assert status == "unavailable:non-git" and findings == []

    def test_uncommitted_doc_suppressed(self, tmp_path):
        self._seed(tmp_path)
        (tmp_path / "helper.py").write_text('print("v2 different")\n')
        _commit(tmp_path, "change code")
        (tmp_path / "doc.md").write_text("[h](helper.py)\n\nedited but not committed\n")
        findings, _ = sd.detect_git_recency(tmp_path, {"doc.md": {"helper.py"}})
        assert not findings


# ---------------------------------------------------------------------------
# Provenance (T1c/T1d)
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_no_provenance_yields_info(self, tmp_path):
        (tmp_path / "doc.md").write_text("hi\n")
        f = sd.detect_provenance(tmp_path)
        assert len(f) == 1 and f[0].code == "PROVENANCE_ABSENT" and f[0].tier == 0

    def test_missing_recorded_file_is_integrity_tier1(self, tmp_path):
        agents = tmp_path / ".github" / "agents"
        refs = agents / "references"
        refs.mkdir(parents=True)
        # build-log records a file hash for a file that does not exist -> MISSING
        (refs / "build-log.json").write_text(json.dumps({
            "file_hashes": {"orchestrator.agent.md": "deadbeefdeadbeef"},
        }))
        f = sd.detect_integrity(tmp_path)
        assert any(x.code == "INTEGRITY" and x.tier == 1 for x in f)

    def test_bridge_source_absent_yields_info(self, tmp_path):
        pair = tmp_path / "references" / "bridges" / "copilot-vscode-to-claude"
        pair.mkdir(parents=True)
        (pair / "bridge-manifest.json").write_text(json.dumps({
            "source_dir": str(tmp_path / "does-not-exist"),
            "source_hashes": [],
        }))
        f = sd.detect_bridge_drift(tmp_path)
        assert len(f) == 1 and f[0].code == "BRIDGE_SOURCE_UNAVAILABLE" and f[0].tier == 0


# ---------------------------------------------------------------------------
# Aggregation, exit code, scan-set
# ---------------------------------------------------------------------------

class TestScanAndExit:
    def test_clean_tree_exit_zero(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("# clean\nNo refs here.\n")
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        assert not report.has_blocking
        assert sd.exit_code(report) == 0

    def test_conflict_triad_exit_one(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "c.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        assert report.has_blocking and sd.exit_code(report) == 1

    def test_examples_and_gitignored_not_scanned(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("ignored/\n")
        (tmp_path / "keep.md").write_text("# keep\n")
        ex = tmp_path / "examples" / "expected"
        ex.mkdir(parents=True)
        (ex / "bad.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        ig = tmp_path / "ignored"
        ig.mkdir()
        (ig / "also.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        scanned = {f.file for f in report.findings}
        assert not any(s.startswith("examples/") for s in scanned)
        assert not any(s.startswith("ignored/") for s in scanned)
        assert not report.has_blocking  # the only triads were in skipped trees

    def test_end_to_end_recency_via_aggregator(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("[h](helper.py)\n")
        (tmp_path / "helper.py").write_text('print("v1")\n')
        _commit(tmp_path, "init")
        (tmp_path / "helper.py").write_text('print("v2 quite different now")\n')
        _commit(tmp_path, "change")
        report = sd.scan_staleness(tmp_path)
        assert any(f.code == "STALE_VS_CODE" for f in report.findings)

    def test_remediation_plan_rows(self, tmp_path):
        report = sd.StalenessReport(root=str(tmp_path))
        report.findings = [
            sd.StalenessFinding(1, "VCS_CONFLICT_MARKER", "c.md", 1, "s", "d", "a", False),
            sd.StalenessFinding(1, "INTEGRITY", "o.md", 0, "s",
                                "d", "re-run `agentteams --update --merge`", True),
        ]
        plan = sd.build_remediation_plan(report)
        codes = {r["code"]: r for r in plan}
        assert codes["VCS_CONFLICT_MARKER"]["action"] == "manual"
        assert codes["INTEGRITY"]["action"] == "run-safe-writer"
        assert "update --merge" in codes["INTEGRITY"]["command"]


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

class TestCliWiring:
    def test_clean_returns_zero(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("# clean\n")
        _commit(tmp_path, "init")
        assert main(["--stale-check", "--output", str(tmp_path)]) == 0

    def test_blocking_returns_one(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "c.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        _commit(tmp_path, "init")
        assert main(["--stale-check", "--output", str(tmp_path)]) == 1

    def test_remediate_requires_check(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--stale-remediate", "--output", str(tmp_path)])

    def test_mutually_exclusive_with_verify_integrity(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--stale-check", "--verify-integrity", "--output", str(tmp_path)])

    def test_stale_restore_without_snapshot_errors(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("# x\n")
        _commit(tmp_path, "init")
        assert main(["--stale-restore", "--output", str(tmp_path)]) == 1

    def test_no_git_skips_recency(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("[h](helper.py)\n")
        (tmp_path / "helper.py").write_text('print("v1")\n')
        _commit(tmp_path, "init")
        (tmp_path / "helper.py").write_text('print("v2 different")\n')
        _commit(tmp_path, "change")
        report = sd.scan_staleness(tmp_path, include_git=False)
        assert report.recency_status == "disabled"
        assert not any(f.code == "STALE_VS_CODE" for f in report.findings)


# ---------------------------------------------------------------------------
# Revision phase: snapshot / restore safety protocol
# ---------------------------------------------------------------------------

class TestSnapshotRestore:
    def test_snapshot_then_restore_byte_identical(self, tmp_path):
        f = tmp_path / "a.md"
        original = "line1\nline2\n"
        f.write_text(original)
        snap = sr.snapshot_files(tmp_path, ["a.md"], stamp="20260619-000000")
        assert snap is not None and (snap / "manifest.json").is_file()
        # Simulate a buggy revision corrupting the file mid-debug.
        f.write_text("CORRUPTED GARBAGE")
        restored = sr.restore_snapshot(tmp_path, snap)
        assert restored == ["a.md"]
        assert f.read_text() == original  # recovered exactly

    def test_snapshot_empty_when_no_files(self, tmp_path):
        assert sr.snapshot_files(tmp_path, ["missing.md"]) is None

    def test_restore_rejects_corrupt_backup(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("hi\n")
        snap = sr.snapshot_files(tmp_path, ["a.md"], stamp="20260619-000001")
        # Tamper with the backup copy → sha256 mismatch → restore must refuse (fail-safe).
        (snap / "files" / "a.md").write_text("tampered")
        f.write_text("current")
        with pytest.raises(ValueError):
            sr.restore_snapshot(tmp_path, snap)
        assert f.read_text() == "current"  # nothing written on a corrupt snapshot

    def test_latest_snapshot_picks_newest(self, tmp_path):
        (tmp_path / "a.md").write_text("x\n")
        sr.snapshot_files(tmp_path, ["a.md"], stamp="20260619-000000")
        newest = sr.snapshot_files(tmp_path, ["a.md"], stamp="20260619-235959")
        assert sr.latest_snapshot(tmp_path) == newest


# ---------------------------------------------------------------------------
# Revision phase: broken-reference repair + apply_fixes
# ---------------------------------------------------------------------------

class TestRefRepair:
    def _repo_with_moved_file(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "new").mkdir()
        (tmp_path / "new" / "helper.py").write_text("x=1\n")
        # doc links the OLD location (file actually lives in new/)
        (tmp_path / "doc.md").write_text("See [helper](old/helper.py).\n")
        _commit(tmp_path, "init")
        return tmp_path

    def test_dry_run_plans_but_writes_nothing(self, tmp_path):
        self._repo_with_moved_file(tmp_path)
        before = (tmp_path / "doc.md").read_text()
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=False)
        assert any(a.code == "BROKEN_REF" and not a.applied for a in result.actions)
        assert result.snapshot_dir is None
        assert (tmp_path / "doc.md").read_text() == before  # unchanged

    def test_apply_repairs_and_snapshots(self, tmp_path):
        self._repo_with_moved_file(tmp_path)
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=True)
        assert result.snapshot_dir is not None
        assert any(a.action == "repaired-ref" and a.applied for a in result.actions)
        assert "](new/helper.py)" in (tmp_path / "doc.md").read_text()
        # re-scan: the broken ref is gone
        assert not any(f.code == "BROKEN_REF" for f in sd.scan_staleness(tmp_path).findings)

    def test_apply_then_restore_recovers(self, tmp_path):
        self._repo_with_moved_file(tmp_path)
        original = (tmp_path / "doc.md").read_text()
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=True)
        sr.restore_snapshot(tmp_path, Path(result.snapshot_dir))
        assert (tmp_path / "doc.md").read_text() == original

    def test_fenced_file_not_auto_edited(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "new").mkdir()
        (tmp_path / "new" / "helper.py").write_text("x=1\n")
        (tmp_path / "doc.md").write_text(
            "<!-- AGENTTEAMS:BEGIN body v=1 -->\n[helper](old/helper.py)\n"
            "<!-- AGENTTEAMS:END body -->\n"
        )
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=True)
        # broken ref present but the fenced file is routed to manual, not auto-edited
        assert any(a.code == "BROKEN_REF" and a.action == "manual" for a in result.actions)
        assert "old/helper.py" in (tmp_path / "doc.md").read_text()

    def test_no_unambiguous_target_is_manual(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("[gone](does/not/exist.py)\n")
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=True)
        assert any(a.code == "BROKEN_REF" and a.action == "manual" for a in result.actions)


class TestRevisionCli:
    def _repo(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "new").mkdir()
        (tmp_path / "new" / "helper.py").write_text("x=1\n")
        (tmp_path / "doc.md").write_text("[helper](old/helper.py)\n")
        _commit(tmp_path, "init")

    def test_remediate_preview_writes_nothing(self, tmp_path):
        self._repo(tmp_path)
        before = (tmp_path / "doc.md").read_text()
        rc = main(["--stale-check", "--stale-remediate", "--output", str(tmp_path)])
        assert rc == 1  # blocking broken ref still present (preview only)
        assert (tmp_path / "doc.md").read_text() == before

    def test_remediate_yes_applies_then_restore(self, tmp_path):
        self._repo(tmp_path)
        original = (tmp_path / "doc.md").read_text()
        rc = main(["--stale-check", "--stale-remediate", "--yes", "--output", str(tmp_path)])
        assert rc in (0, 3)
        assert "](new/helper.py)" in (tmp_path / "doc.md").read_text()
        # recover via the CLI restore path
        assert main(["--stale-restore", "--output", str(tmp_path)]) == 0
        assert (tmp_path / "doc.md").read_text() == original


# ---------------------------------------------------------------------------
# F1 — .agentteams-stale-ignore suppression
# ---------------------------------------------------------------------------

class TestStaleIgnore:
    def _repo(self, tmp_path, ignore_lines):
        _init_repo(tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "incident.md").write_text("see [x](src/gone.py)\n")
        (tmp_path / "c.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        if ignore_lines is not None:
            (tmp_path / ".agentteams-stale-ignore").write_text(ignore_lines)
        _commit(tmp_path, "init")
        return sd.scan_staleness(tmp_path)

    def test_referrer_file_pattern_suppresses_broken_ref(self, tmp_path):
        r = self._repo(tmp_path, "docs/incident.md\n")
        assert r.suppressed_count == 1
        assert not any(f.code == "BROKEN_REF" for f in r.findings)

    def test_dir_prefix_pattern_suppresses(self, tmp_path):
        r = self._repo(tmp_path, "docs/\n")   # trailing-slash dir prefix (NOT bare fnmatch)
        assert not any(f.code == "BROKEN_REF" for f in r.findings)

    def test_target_pattern_suppresses(self, tmp_path):
        r = self._repo(tmp_path, "src/gone.py\n")  # match the reference target, not the referrer
        assert not any(f.code == "BROKEN_REF" for f in r.findings)

    def test_conflict_marker_never_suppressed(self, tmp_path):
        # A pattern matching c.md must NOT drop its conflict triad.
        r = self._repo(tmp_path, "c.md\ndocs/incident.md\n")
        assert any(f.code == "VCS_CONFLICT_MARKER" for f in r.findings)
        assert not any(f.code == "BROKEN_REF" for f in r.findings)

    def test_comments_and_blanks_ignored(self, tmp_path):
        r = self._repo(tmp_path, "# a comment\n\nsrc/gone.py\n")
        assert not any(f.code == "BROKEN_REF" for f in r.findings)

    def test_no_ignore_file_no_change(self, tmp_path):
        r = self._repo(tmp_path, None)
        assert r.suppressed_count == 0
        assert any(f.code == "BROKEN_REF" for f in r.findings)

    def test_suppressed_broken_ref_flips_exit_and_skips_apply(self, tmp_path):
        # Only the broken ref + a clean tree → suppressing it yields exit 0 and no apply action.
        _init_repo(tmp_path)
        (tmp_path / "doc.md").write_text("[x](gone/missing.py)\n")
        (tmp_path / ".agentteams-stale-ignore").write_text("doc.md\n")
        _commit(tmp_path, "init")
        r = sd.scan_staleness(tmp_path)
        assert sd.exit_code(r) == 0
        result = sr.apply_fixes(r, tmp_path, apply=False)
        assert not any(a.code == "BROKEN_REF" for a in result.actions)


# ---------------------------------------------------------------------------
# F2 — backups-not-gitignored guard
# ---------------------------------------------------------------------------

class TestBackupsGuard:
    def test_warns_when_not_gitignored(self, tmp_path):
        # repo with a relocatable broken ref → apply writes a snapshot; .agentteams-backups
        # is not gitignored → a warning is emitted.
        _init_repo(tmp_path)
        (tmp_path / "new").mkdir()
        (tmp_path / "new" / "helper.py").write_text("x=1\n")
        (tmp_path / "doc.md").write_text("[h](old/helper.py)\n")
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=True)
        assert result.snapshot_dir is not None
        assert any("gitignore" in w for w in result.warnings)

    def test_no_warning_when_gitignored(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text(".agentteams-backups/\n")
        (tmp_path / "new").mkdir()
        (tmp_path / "new" / "helper.py").write_text("x=1\n")
        (tmp_path / "doc.md").write_text("[h](old/helper.py)\n")
        _commit(tmp_path, "init")
        report = sd.scan_staleness(tmp_path)
        result = sr.apply_fixes(report, tmp_path, apply=True)
        assert result.snapshot_dir is not None
        assert result.warnings == []

    def test_backups_ignored_helper_non_git(self, tmp_path):
        # non-git target → n/a → treated as ignored (no warning)
        assert sr._backups_ignored(tmp_path, fleet_git, ".agentteams-backups/x") is True


# fleet._git for the helper test
from agentteams.fleet import _git as fleet_git  # noqa: E402
