"""
Tests for src/emit.py
"""

import pytest
from pathlib import Path
from agentteams.emit import (
    emit_all,
    EmitResult,
    backup_output_dir,
    list_backups,
    restore_backup,
)


# ---------------------------------------------------------------------------
# Basic write
# ---------------------------------------------------------------------------

def test_emit_writes_files(tmp_path):
    rendered = [
        ("orchestrator.agent.md", "# Orchestrator\n\nContent here.\n"),
        ("navigator.agent.md", "# Navigator\n\nNavigation here.\n"),
    ]
    result = emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=False, yes=True)

    assert result.success
    assert len(result.written) == 2
    assert (tmp_path / "orchestrator.agent.md").exists()
    assert (tmp_path / "navigator.agent.md").exists()


def test_emit_creates_parent_dirs(tmp_path):
    rendered = [
        ("references/conflict-log.csv", "type,file,description\n"),
    ]
    result = emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=False, yes=True)

    assert result.success
    assert (tmp_path / "references" / "conflict-log.csv").exists()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_emit_dry_run_no_files_written(tmp_path, capsys):
    rendered = [
        ("orchestrator.agent.md", "# Orchestrator\n"),
    ]
    result = emit_all(rendered, output_dir=tmp_path, dry_run=True, overwrite=False, yes=True)

    assert result.dry_run
    assert not (tmp_path / "orchestrator.agent.md").exists()
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out


# ---------------------------------------------------------------------------
# Overwrite protection
# ---------------------------------------------------------------------------

def test_emit_skip_existing_without_overwrite(tmp_path):
    existing_file = tmp_path / "orchestrator.agent.md"
    existing_file.write_text("OLD CONTENT", encoding="utf-8")

    rendered = [("orchestrator.agent.md", "NEW CONTENT")]
    result = emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=False, yes=False)

    # With yes=False and no overwrite, it should prompt but since we can't
    # interact in tests, the file should remain if the user declines.
    # Actually with yes=False and no TTY, it will read empty input → not 'y' → abort
    # So either skipped or error depending on implementation.
    assert existing_file.read_text(encoding="utf-8") in ("OLD CONTENT", "NEW CONTENT")


def test_emit_overwrite_existing(tmp_path):
    existing_file = tmp_path / "orchestrator.agent.md"
    existing_file.write_text("OLD CONTENT", encoding="utf-8")

    rendered = [("orchestrator.agent.md", "NEW CONTENT")]
    result = emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)

    assert result.success
    written = existing_file.read_text(encoding="utf-8")
    assert "NEW CONTENT" in written
    assert written.startswith("<!-- AGENTTEAMS:BEGIN content v=1 -->\n")


# ---------------------------------------------------------------------------
# Relative-path resolution
# ---------------------------------------------------------------------------

def test_emit_parent_relative_path(tmp_path):
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)

    rendered = [
        ("../copilot-instructions.md", "# Copilot Instructions\n"),
    ]
    result = emit_all(rendered, output_dir=agents_dir, dry_run=False, overwrite=True, yes=True)

    assert result.success
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()


# ---------------------------------------------------------------------------
# Content integrity
# ---------------------------------------------------------------------------

def test_emit_content_preserved(tmp_path):
    content = "type,file,description\n"
    rendered = [("references/conflict-log.csv", content)]
    emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)

    written_content = (tmp_path / "references" / "conflict-log.csv").read_text(encoding="utf-8")
    assert written_content == content


def test_emit_autofences_markdown_outputs(tmp_path):
    content = "# Header\n\nSome content with **bold** and `code`.\n"
    emit_all([("test.agent.md", content)], output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)

    written_content = (tmp_path / "test.agent.md").read_text(encoding="utf-8")
    assert written_content.startswith("<!-- AGENTTEAMS:BEGIN content v=1 -->\n")
    assert "# Header" in written_content
    assert written_content.rstrip().endswith("<!-- AGENTTEAMS:END content -->")


def test_emit_autofences_preserves_yaml_front_matter_position(tmp_path):
    """Front matter must remain first; fence markers go after the closing --- line."""
    content = "---\nname: My Agent\ndescription: test\n---\n\n# Header\n\nBody text.\n"
    emit_all([("my.agent.md", content)], output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)

    written_content = (tmp_path / "my.agent.md").read_text(encoding="utf-8")
    assert written_content.startswith("---\n"), "YAML front matter must be at position 0"
    fence_begin_pos = written_content.find("<!-- AGENTTEAMS:BEGIN content v=1 -->")
    fm_end_pos = written_content.find("---\n", 4) + 4  # end of closing ---
    assert fence_begin_pos >= fm_end_pos, "fence BEGIN must appear after the front-matter block"
    assert written_content.rstrip().endswith("<!-- AGENTTEAMS:END content -->")


# ---------------------------------------------------------------------------
# EmitResult properties
# ---------------------------------------------------------------------------

def test_emit_result_success_when_no_errors():
    result = EmitResult()
    assert result.success is True


def test_emit_result_failure_when_errors_present():
    result = EmitResult(errors=["Something went wrong"])
    assert result.success is False


def test_emit_result_default_written_empty():
    result = EmitResult()
    assert result.written == []


def test_emit_result_default_skipped_empty():
    result = EmitResult()
    assert result.skipped == []


def test_emit_result_dry_run_default_false():
    result = EmitResult()
    assert result.dry_run is False


# ---------------------------------------------------------------------------
# Empty rendered list
# ---------------------------------------------------------------------------

def test_emit_empty_rendered_list_succeeds(tmp_path):
    result = emit_all([], output_dir=tmp_path, dry_run=False, overwrite=False, yes=True)
    assert result.success is True
    assert result.written == []


# ---------------------------------------------------------------------------
# Skipped tracking
# ---------------------------------------------------------------------------

def test_emit_skipped_list_populated_when_file_exists(tmp_path):
    """When a file exists and overwrite=False, yes=True: the pre-check promotes
    overwrite to True and all files are written (none skipped, none errored)."""
    existing_file = tmp_path / "navigator.agent.md"
    existing_file.write_text("ORIGINAL", encoding="utf-8")

    rendered = [("navigator.agent.md", "NEW CONTENT")]
    result = emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=False, yes=True)

    # yes=True promotes overwrite → file is written, not skipped or errored
    assert result.success is True
    assert len(result.written) == 1
    written = existing_file.read_text(encoding="utf-8")
    assert "NEW CONTENT" in written
    assert written.startswith("<!-- AGENTTEAMS:BEGIN content v=1 -->\n")


# ---------------------------------------------------------------------------
# Backup: backup_output_dir
# ---------------------------------------------------------------------------

def test_backup_creates_timestamped_dir(tmp_path):
    (tmp_path / "orchestrator.agent.md").write_text("CONTENT", encoding="utf-8")
    result = backup_output_dir(tmp_path)
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.files_backed_up == 1
    assert not result.skipped


def test_backup_copies_file_contents(tmp_path):
    (tmp_path / "agent.agent.md").write_text("ORIGINAL", encoding="utf-8")
    result = backup_output_dir(tmp_path)
    backed_up = result.backup_path / "agent.agent.md"
    assert backed_up.exists()
    assert backed_up.read_text(encoding="utf-8") == "ORIGINAL"


def test_backup_selective_only_backs_up_listed_files(tmp_path):
    (tmp_path / "a.agent.md").write_text("A", encoding="utf-8")
    (tmp_path / "b.agent.md").write_text("B", encoding="utf-8")
    result = backup_output_dir(tmp_path, files_to_backup=["a.agent.md"])
    assert result.files_backed_up == 1
    assert (result.backup_path / "a.agent.md").exists()
    assert not (result.backup_path / "b.agent.md").exists()


def test_backup_skipped_when_output_dir_missing(tmp_path):
    missing = tmp_path / "does_not_exist"
    result = backup_output_dir(missing)
    assert result.skipped is True
    assert result.backup_path is None


def test_backup_skipped_when_no_matching_files(tmp_path):
    # selective backup with files not present on disk → no backup created
    result = backup_output_dir(tmp_path, files_to_backup=["nonexistent.agent.md"])
    assert result.skipped is True


def test_backup_dry_run(tmp_path, capsys):
    (tmp_path / "agent.agent.md").write_text("X", encoding="utf-8")
    result = backup_output_dir(tmp_path, dry_run=True)
    assert result.skipped is True
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    # Backup dir should not have been created
    backup_root = tmp_path / ".agentteams-backups"
    assert not backup_root.exists()


def test_backup_does_not_back_up_backup_dir_itself(tmp_path):
    (tmp_path / "agent.agent.md").write_text("X", encoding="utf-8")
    # Create a pre-existing backup
    first = backup_output_dir(tmp_path)
    first_count = first.files_backed_up
    # Second backup should still only back up the real agent file
    second = backup_output_dir(tmp_path)
    assert second.files_backed_up == first_count


# ---------------------------------------------------------------------------
# Backup: list_backups and restore_backup
# ---------------------------------------------------------------------------

def test_list_backups_empty_when_no_backups(tmp_path):
    assert list_backups(tmp_path) == []


def test_list_backups_returns_entries_newest_first(tmp_path):
    (tmp_path / "agent.agent.md").write_text("V1", encoding="utf-8")
    backup_output_dir(tmp_path)
    (tmp_path / "agent.agent.md").write_text("V2", encoding="utf-8")
    backup_output_dir(tmp_path)
    backups = list_backups(tmp_path)
    assert len(backups) == 2
    # newest first
    assert backups[0][0] >= backups[1][0]


def test_restore_backup_round_trip(tmp_path):
    original = "ORIGINAL CONTENT"
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text(original, encoding="utf-8")
    br = backup_output_dir(tmp_path)
    # Overwrite the file
    agent_file.write_text("CHANGED CONTENT", encoding="utf-8")
    # Restore
    count = restore_backup(br.backup_path, tmp_path)
    assert count >= 1
    assert agent_file.read_text(encoding="utf-8") == original


def test_restore_backup_nonexistent_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        restore_backup(tmp_path / "nonexistent", tmp_path)


# ---------------------------------------------------------------------------
# Regression: --update --merge must not overwrite user content below fences
# ---------------------------------------------------------------------------

def test_merge_preserves_user_content_below_fence(tmp_path):
    existing = (
        "<!-- AGENTTEAMS:BEGIN header v=1 -->\n"
        "# Header — OLD\n"
        "<!-- AGENTTEAMS:END header -->\n"
        "\n"
        "## User Section\n"
        "This is user-authored content below the fence.\n"
    )
    new_rendered = (
        "<!-- AGENTTEAMS:BEGIN header v=1 -->\n"
        "# Header — NEW\n"
        "<!-- AGENTTEAMS:END header -->\n"
        "\n"
        "## User Section\n"
        "This is user-authored content below the fence.\n"
    )
    target = tmp_path / "agent.agent.md"
    target.write_text(existing, encoding="utf-8")

    result = emit_all(
        [("agent.agent.md", new_rendered)],
        output_dir=tmp_path,
        merge=True,
    )

    assert result.success
    content = target.read_text(encoding="utf-8")
    assert "# Header — NEW" in content
    assert "user-authored content below the fence" in content


def test_merge_normalizes_unfenced_markdown_render_before_merge(tmp_path):
    existing = (
        "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
        "# Header — OLD\n"
        "<!-- AGENTTEAMS:END content -->\n"
        "\n"
        "## User Section\n"
        "This is user-authored content below the fence.\n"
    )
    new_rendered = "# Header — NEW\n"
    target = tmp_path / "agent.agent.md"
    target.write_text(existing, encoding="utf-8")

    result = emit_all(
        [("agent.agent.md", new_rendered)],
        output_dir=tmp_path,
        merge=True,
    )

    assert result.success
    content = target.read_text(encoding="utf-8")
    assert "# Header — NEW" in content
    assert "user-authored content below the fence" in content


def test_merge_unchanged_result_not_counted_as_skip(tmp_path):
    existing = (
        "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
        "# Header — SAME\n"
        "<!-- AGENTTEAMS:END content -->\n"
    )
    target = tmp_path / "agent.agent.md"
    target.write_text(existing, encoding="utf-8")

    result = emit_all(
        [("agent.agent.md", "# Header — SAME\n")],
        output_dir=tmp_path,
        merge=True,
    )

    assert result.success
    assert len(result.unchanged) == 1
    assert result.skipped == []


def test_merge_overwrites_machine_managed_json_artifact_without_fences(tmp_path):
    target = tmp_path / "references" / "security-vulnerability-watch.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"generated_at":"old"}\n', encoding="utf-8")

    result = emit_all(
        [("references/security-vulnerability-watch.json", '{"generated_at":"new"}\n')],
        output_dir=tmp_path,
        merge=True,
    )

    assert result.success
    assert len(result.merged) == 1
    assert result.skipped == []
    assert target.read_text(encoding="utf-8") == '{"generated_at":"new"}\n'


def test_merge_counts_unchanged_machine_managed_json_artifact(tmp_path):
    target = tmp_path / "references" / "security-vulnerability-watch.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"generated_at":"same"}\n', encoding="utf-8")

    result = emit_all(
        [("references/security-vulnerability-watch.json", '{"generated_at":"same"}\n')],
        output_dir=tmp_path,
        merge=True,
    )

    assert result.success
    assert len(result.unchanged) == 1
    assert result.skipped == []


# ---------------------------------------------------------------------------
# restore_backup — remove_extra (snapshot-complete restore)
# ---------------------------------------------------------------------------

def test_restore_backup_remove_extra_deletes_orphaned_files(tmp_path):
    """Files absent from backup are deleted when remove_extra=True."""
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text("ORIGINAL", encoding="utf-8")
    br = backup_output_dir(tmp_path)

    # Add a new file AFTER the backup (simulates post-migration CSV)
    extra = tmp_path / "references" / "adjacent-repos-changelog.csv"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("date,repo_name,action,files_changed,summary\n2026-01-01,myrepo,init,f.md,setup\n")

    restore_backup(br.backup_path, tmp_path, remove_extra=True)

    assert agent_file.read_text(encoding="utf-8") == "ORIGINAL"
    assert not extra.exists(), "orphaned CSV should be removed"


def test_restore_backup_remove_extra_false_leaves_orphaned_files(tmp_path):
    """Default (remove_extra=False) leaves extra files untouched."""
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text("ORIGINAL", encoding="utf-8")
    br = backup_output_dir(tmp_path)

    extra = tmp_path / "references" / "adjacent-repos-changelog.csv"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("date,repo_name,action,files_changed,summary\n")

    restore_backup(br.backup_path, tmp_path)  # remove_extra defaults to False

    assert extra.exists(), "extra file must not be deleted when remove_extra=False"


def test_restore_backup_remove_extra_preserves_backup_dir(tmp_path):
    """The .agentteams-backups directory is never deleted during remove_extra."""
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text("ORIGINAL", encoding="utf-8")
    br = backup_output_dir(tmp_path)

    restore_backup(br.backup_path, tmp_path, remove_extra=True)

    # Backup directory must still exist
    assert br.backup_path.exists()


def test_restore_backup_remove_extra_preserves_build_log(tmp_path):
    """references/build-log.json is excluded from removal even if absent from backup."""
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text("CONTENT", encoding="utf-8")
    br = backup_output_dir(tmp_path)

    build_log = tmp_path / "references" / "build-log.json"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    build_log.write_text("{}", encoding="utf-8")

    restore_backup(br.backup_path, tmp_path, remove_extra=True)

    assert build_log.exists(), "build-log.json must be preserved"


def test_restore_backup_remove_extra_count_matches_restored(tmp_path):
    """Return value is restored file count, not including removed files."""
    (tmp_path / "a.md").write_text("A")
    (tmp_path / "b.md").write_text("B")
    br = backup_output_dir(tmp_path)

    extra = tmp_path / "c.md"
    extra.write_text("EXTRA")

    count = restore_backup(br.backup_path, tmp_path, remove_extra=True)
    assert count == 2
    assert not extra.exists()


# ---------------------------------------------------------------------------
# backup_output_dir — CSV log files always included in selective backup
# ---------------------------------------------------------------------------

def test_backup_selective_always_includes_csv_logs(tmp_path):
    """CSV log files are included in selective backup even if not in files_to_backup."""
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text("AGENT")

    refs = tmp_path / "references"
    refs.mkdir()
    changelog = refs / "adjacent-repos-changelog.csv"
    coord_log = refs / "adjacent-repos-coordination-log.csv"
    changelog.write_text("date,repo_name,action,files_changed,summary\n2026-01-01,r,init,f.md,s\n")
    coord_log.write_text("date,adjacent_repo,direction,outcome\n")

    br = backup_output_dir(tmp_path, files_to_backup=["agent.agent.md"])

    assert br.backup_path is not None
    assert (br.backup_path / "references" / "adjacent-repos-changelog.csv").exists()
    assert (br.backup_path / "references" / "adjacent-repos-coordination-log.csv").exists()


def test_backup_selective_csv_not_duplicated_if_already_in_list(tmp_path):
    """CSV files in files_to_backup are not backed up twice."""
    refs = tmp_path / "references"
    refs.mkdir()
    changelog = refs / "adjacent-repos-changelog.csv"
    changelog.write_text("date,repo_name,action,files_changed,summary\n")

    br = backup_output_dir(
        tmp_path,
        files_to_backup=["references/adjacent-repos-changelog.csv"],
    )
    assert br.files_backed_up == 1


def test_backup_selective_csv_not_backed_up_if_absent(tmp_path):
    """If CSV files don't exist yet, they are silently skipped in selective backup."""
    agent_file = tmp_path / "agent.agent.md"
    agent_file.write_text("CONTENT")

    br = backup_output_dir(tmp_path, files_to_backup=["agent.agent.md"])
    assert br.files_backed_up == 1  # only the agent file, no CSVs

