"""
Tests for --migrate and --revert-migration CLI commands.

These tests exercise the _run_migrate() and _run_revert_migration() helpers
in build_team.py, using temporary git repositories to avoid touching the
real project working tree.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure repository root is on sys.path for dev-mode imports
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from build_team import _run_migrate, _run_revert_migration, _MIGRATION_TAG


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLES_DIR = _REPO_ROOT / "examples"
_BRIEF = EXAMPLES_DIR / "software-project" / "brief.json"


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _init_git_repo(path: Path) -> None:
    """Initialise a minimal git repository at *path* and make an initial commit."""
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    # Seed with the brief so ingest can load it
    agents_dir = path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    import shutil
    shutil.copy(_BRIEF, agents_dir / "brief.json")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "init"], path)


def _write_pass_security_decision(output_dir: Path, action: str) -> None:
    """Seed a PASS decision row so destructive action gates allow test execution."""
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        f"2026-04-24T00:00:00Z,test,{action}-001,PASS,,verified\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _run_revert_migration
# ---------------------------------------------------------------------------

class TestRevertMigration:
    def test_revert_fails_without_git_repo(self, tmp_path):
        """Non-git directories are rejected with rc=1."""
        rc = _run_revert_migration(tmp_path)
        assert rc == 1

    def test_revert_fails_without_tag(self, tmp_path):
        """A git repo without the snapshot tag returns rc=1."""
        _init_git_repo(tmp_path)
        rc = _run_revert_migration(tmp_path)
        assert rc == 1

    def test_revert_restores_files_and_deletes_tag(self, tmp_path):
        """After a migrate run, revert restores the pre-migration state."""
        _init_git_repo(tmp_path)
        _write_pass_security_decision(tmp_path / ".github" / "agents", "revert-migration")

        # Write a sentinel file and commit it so the snapshot captures it
        sentinel = tmp_path / ".github" / "agents" / "sentinel.agent.md"
        sentinel.write_text("# Sentinel\n", encoding="utf-8")
        _git(["add", "-A"], tmp_path)
        _git(["commit", "-m", "add sentinel"], tmp_path)

        # Manually create the snapshot tag (simulates what --migrate would do)
        _git(["tag", _MIGRATION_TAG], tmp_path)

        # Simulate a post-migrate change: delete the sentinel
        sentinel.unlink()
        _git(["add", "-A"], tmp_path)
        _git(["commit", "-m", "overwrite migration"], tmp_path)

        assert not sentinel.exists(), "sentinel should be gone after simulated migration"

        # Run revert
        rc = _run_revert_migration(tmp_path)
        assert rc == 0

        # Sentinel should be restored
        assert sentinel.exists(), "sentinel should be restored after revert"

        # Tag should be deleted
        rc2, _, _ = _git(["rev-parse", "--verify", _MIGRATION_TAG], tmp_path)
        assert rc2 != 0, "snapshot tag should have been deleted"


# ---------------------------------------------------------------------------
# _run_migrate
# ---------------------------------------------------------------------------

class TestRunMigrate:
    def test_migrate_fails_without_git_repo(self, tmp_path):
        """Non-git directories are rejected with rc=1."""
        rc = _run_migrate(tmp_path, [])
        assert rc == 1

    def test_migrate_fails_if_tag_already_exists(self, tmp_path):
        """If pre-fencing-snapshot tag already exists, migrate returns rc=1."""
        _init_git_repo(tmp_path)
        _git(["tag", _MIGRATION_TAG], tmp_path)
        # Use a dummy argv that would fail before writing — tag conflict detected first
        rc = _run_migrate(tmp_path, ["--description", str(tmp_path / ".github" / "agents" / "brief.json")])
        assert rc == 1

    def test_migrate_creates_snapshot_tag(self, tmp_path, monkeypatch):
        """--migrate creates the pre-fencing-snapshot tag."""
        _init_git_repo(tmp_path)

        # Patch main() so we don't actually run the full pipeline
        import build_team as _bt
        monkeypatch.setattr(_bt, "main", lambda argv: 0)

        brief_path = tmp_path / ".github" / "agents" / "brief.json"
        argv = [
            "--description", str(brief_path),
            "--framework", "copilot-vscode",
            "--project", str(tmp_path),
            "--migrate",
        ]
        rc = _run_migrate(tmp_path, argv)
        assert rc == 0

        rc2, sha, _ = _git(["rev-parse", "--verify", _MIGRATION_TAG], tmp_path)
        assert rc2 == 0, "pre-fencing-snapshot tag should have been created"
        assert sha, "tag should resolve to a commit SHA"

    def test_migrate_tag_survives_failed_overwrite(self, tmp_path, monkeypatch):
        """If the overwrite step fails, the snapshot tag is preserved for rollback."""
        _init_git_repo(tmp_path)

        import build_team as _bt
        monkeypatch.setattr(_bt, "main", lambda argv: 1)  # simulate failure

        brief_path = tmp_path / ".github" / "agents" / "brief.json"
        argv = [
            "--description", str(brief_path),
            "--project", str(tmp_path),
            "--migrate",
        ]
        rc = _run_migrate(tmp_path, argv)
        assert rc == 1  # propagates failure

        # Tag must still exist so the user can inspect and roll back
        rc2, _, _ = _git(["rev-parse", "--verify", _MIGRATION_TAG], tmp_path)
        assert rc2 == 0, "snapshot tag must survive a failed migration"

    def test_migrate_argv_rewrite(self, tmp_path, monkeypatch):
        """--migrate is stripped and --overwrite/--yes are injected into delegated argv."""
        _init_git_repo(tmp_path)

        captured = {}

        import build_team as _bt

        def _capture_main(argv):
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(_bt, "main", _capture_main)

        brief_path = tmp_path / ".github" / "agents" / "brief.json"
        argv = [
            "--description", str(brief_path),
            "--project", str(tmp_path),
            "--migrate",
        ]
        _run_migrate(tmp_path, argv)

        delegated = captured.get("argv", [])
        assert "--migrate" not in delegated
        assert "--overwrite" in delegated
        assert "--yes" in delegated


# ---------------------------------------------------------------------------
# Round-trip: migrate → revert
# ---------------------------------------------------------------------------

class TestMigrateRevertRoundTrip:
    def test_round_trip(self, tmp_path, monkeypatch):
        """migrate creates tag; revert restores state and deletes tag."""
        _init_git_repo(tmp_path)
        _write_pass_security_decision(tmp_path / ".github" / "agents", "revert-migration")

        import build_team as _bt
        monkeypatch.setattr(_bt, "main", lambda argv: 0)

        brief_path = tmp_path / ".github" / "agents" / "brief.json"
        argv = [
            "--description", str(brief_path),
            "--project", str(tmp_path),
            "--migrate",
        ]

        rc_migrate = _run_migrate(tmp_path, argv)
        assert rc_migrate == 0

        # Verify tag exists
        rc_tag, _, _ = _git(["rev-parse", "--verify", _MIGRATION_TAG], tmp_path)
        assert rc_tag == 0

        # Revert
        rc_revert = _run_revert_migration(tmp_path)
        assert rc_revert == 0

        # Tag should be gone
        rc_tag2, _, _ = _git(["rev-parse", "--verify", _MIGRATION_TAG], tmp_path)
        assert rc_tag2 != 0
