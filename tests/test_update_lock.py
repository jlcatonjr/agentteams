"""test_update_lock.py — the per-checkout update lock and run record (agentteams/update_lock.py, plan F3)."""

from __future__ import annotations

import argparse
import csv
import errno
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agentteams import gitdirs
from agentteams import update_lock as ul

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF = REPO_ROOT / "examples" / "software-project" / "brief.json"


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A minimal checkout: a project dir with a real ``.git`` directory."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    return root


def _link_worktree(wt: Path, wt_git: Path) -> None:
    """Make *wt* a linked worktree of *wt_git*: a ``.git`` file, HEAD, a back-pointer and a commondir."""
    (wt_git / "HEAD").write_text("ref: refs/heads/wt\n")
    (wt_git / "gitdir").write_text(f"{wt / '.git'}\n")
    (wt_git / "commondir").write_text("../..\n")
    (wt / ".git").write_text(f"gitdir: {wt_git}\n")


def _rows(git_dir: Path) -> list[dict[str, str]]:
    with open(git_dir / ul.RECORD_NAME, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _hold_lock(git_dir: Path) -> subprocess.Popen:
    """Start another process that holds the flock (and a holder record) until its stdin closes."""
    code = textwrap.dedent(f"""
        import fcntl, json, os, sys
        fd = os.open({str(git_dir / ul.LOCK_NAME)!r}, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        with open({str(git_dir / ul.HOLDER_NAME)!r}, "w") as fh:
            json.dump({{"pid": os.getpid(), "started_at": "2026-11-03T02:50:00Z", "invoker": "claude-code",
                       "argv_redacted": ["--self", "--update"]}}, fh)
        print("held", flush=True)
        sys.stdin.read()
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
    return proc


def _release(proc: subprocess.Popen) -> None:
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait(timeout=10)


def _main(out: Path, *extra: str) -> int:
    from agentteams.cli.app import main

    return main(["--description", str(BRIEF), "--output", str(out), "--framework", "claude", "--yes",
                 "--security-offline", *extra])


def test_held_lock_refuses_and_nothing_is_written(checkout: Path) -> None:
    git_dir = checkout / ".git"
    proc = _hold_lock(git_dir)
    try:
        ran = []
        with pytest.raises(ul.UpdateLocked) as info:
            with ul.update_run(checkout, ["--update"], dry_run=False, mode="update", framework="claude"):
                ran.append(True)
        assert not ran
        msg = str(info.value)
        assert f"pid {proc.pid}" in msg and "claude-code" in msg and "--self --update" in msg
        assert not (git_dir / ul.RECORD_NAME).exists()
    finally:
        _release(proc)


def test_held_lock_refuses_the_pipeline_before_any_write(checkout: Path, capsys: pytest.CaptureFixture) -> None:
    """Through the real CLI: a held lock returns 1 with the refusal, and the output dir gains nothing."""
    proc = _hold_lock(checkout / ".git")
    try:
        rc = _main(checkout)
    finally:
        _release(proc)
    assert rc == 1
    assert "[UPDATE-LOCK] refused" in capsys.readouterr().err
    assert sorted(p.name for p in checkout.iterdir()) == [".git"]
    assert not (checkout / ".git" / ul.RECORD_NAME).exists()


def test_pipeline_records_its_exit_code(checkout: Path) -> None:
    """Every return of the locked body passes through set_exit: the row's exit equals main's return."""
    rc = _main(checkout)
    (row,) = _rows(checkout / ".git")
    assert row["exit"] == str(rc) and row["mode"] == "generate" and row["framework"] == "claude"
    assert row["argv"] == "--description --output --framework claude --yes --security-offline"
    assert str(BRIEF) not in row["argv"] and str(checkout) not in row["argv"]


def test_pipeline_dry_run_takes_no_lock(checkout: Path) -> None:
    _main(checkout, "--dry-run")
    assert sorted(p.name for p in (checkout / ".git").iterdir()) == []


def test_lock_released_after_exception(checkout: Path) -> None:
    git_dir = checkout / ".git"
    with pytest.raises(RuntimeError):
        with ul.update_run(checkout, ["--update"], dry_run=False, mode="update"):
            raise RuntimeError("boom")
    assert _rows(git_dir)[-1]["exit"] == "exception"
    assert not (git_dir / ul.HOLDER_NAME).exists()
    with ul.update_run(checkout, ["--update"], dry_run=False, mode="update") as run:
        run.set_exit(0)
    assert [r["exit"] for r in _rows(git_dir)] == ["exception", "0"]


def test_record_redacts_argv_and_records_exit(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    git_dir = checkout / ".git"
    argv = ["--self", "--update", "--merge", "--yes", "--framework", "claude", "--description", "secret-brief.json",
            "--backup-mirror=token-abc-123", "--shrink-policy=additive", "--adopt-only", "a-b,c",
            "--output", "out", "positional", "-f", "Not A Slug"]
    with ul.update_run(checkout, argv, dry_run=False, mode="update", framework="claude") as run:
        holder = json.loads((git_dir / ul.HOLDER_NAME).read_text())
        assert holder["pid"] == os.getpid() and holder["invoker"] == "claude-code"
        assert "secret-brief" not in json.dumps(holder)
        run.set_exit(3)
    (row,) = _rows(git_dir)
    assert row["argv"] == ("--self --update --merge --yes --framework claude --description --backup-mirror "
                           "--shrink-policy=additive --adopt-only a-b,c --output -f")
    assert row["exit"] == "3" and row["mode"] == "update" and row["framework"] == "claude"
    assert row["invoker"] == "claude-code" and row["pid"] == str(os.getpid())
    assert row["started_at"] and row["finished_at"] and row["agentteams_commit"]
    assert oct(os.stat(git_dir / ul.RECORD_NAME).st_mode & 0o777) == "0o600"
    assert oct(os.stat(git_dir / ul.LOCK_NAME).st_mode & 0o777) == "0o600"
    assert not (git_dir / ul.HOLDER_NAME).exists()


def test_dry_run_writes_nothing_and_notices_held_lock(checkout: Path, capsys: pytest.CaptureFixture) -> None:
    git_dir = checkout / ".git"
    with ul.update_run(checkout, ["--dry-run"], dry_run=True):
        pass
    assert list(git_dir.iterdir()) == []
    proc = _hold_lock(git_dir)
    try:
        with ul.update_run(checkout, ["--dry-run"], dry_run=True):
            pass
    finally:
        _release(proc)
    assert "update lock is held" in capsys.readouterr().err
    assert not (git_dir / ul.RECORD_NAME).exists()


def test_symlinked_git_dir_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real-git"
    real.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").symlink_to(real)
    with pytest.raises(ul.UpdateLockError):
        with ul.update_run(root, [], dry_run=False):
            pytest.fail("body must not run")
    assert list(real.iterdir()) == []


def test_symlinked_gitdir_target_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real-git"
    real.mkdir()
    (real / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "link-git").symlink_to(real)
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {tmp_path / 'link-git'}\n")
    with pytest.raises(ul.UpdateLockError):
        with ul.update_run(root, [], dry_run=False):
            pytest.fail("body must not run")
    assert sorted(p.name for p in real.iterdir()) == ["HEAD"]


def test_symlinked_lock_file_is_refused(checkout: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (checkout / ".git" / ul.LOCK_NAME).symlink_to(victim)
    with pytest.raises(ul.UpdateLockError, match="symlink"):
        with ul.update_run(checkout, [], dry_run=False):
            pytest.fail("body must not run")
    assert victim.read_text() == "keep"


def test_symlinked_record_is_not_followed(checkout: Path, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    victim = tmp_path / "victim.csv"
    victim.write_text("keep")
    (checkout / ".git" / ul.RECORD_NAME).symlink_to(victim)
    with ul.update_run(checkout, [], dry_run=False) as run:
        run.set_exit(0)
    assert victim.read_text() == "keep"
    assert "run record not appended" in capsys.readouterr().err


def test_nesting_is_reentrant(checkout: Path) -> None:
    """A --migrate-style in-process re-entry neither refuses itself nor writes a second row."""
    with ul.update_run(checkout, ["--migrate"], dry_run=False, mode="migrate") as outer:
        with ul.update_run(checkout, ["--overwrite"], dry_run=False, mode="overwrite") as inner:
            inner.set_exit(0)
        outer.set_exit(0)
    rows = _rows(checkout / ".git")
    assert [(r["mode"], r["exit"]) for r in rows] == [("migrate", "0")]
    assert ul._depth == 0


def test_worktree_gitdir_file_is_followed(tmp_path: Path) -> None:
    common = tmp_path / "main" / ".git"
    wt_git = common / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    _link_worktree(wt, wt_git)
    assert ul.find_git_dir(wt / "sub" / "not-yet") == wt_git.resolve()
    assert gitdirs.git_dirs(wt) == (wt_git.resolve(), common.resolve())
    with ul.update_run(wt / "sub", ["--update"], dry_run=False, mode="update") as run:
        run.set_exit(0)
    assert (wt_git / ul.RECORD_NAME).exists()
    assert not (common / ul.RECORD_NAME).exists()


def test_relative_gitdir_line(tmp_path: Path) -> None:
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / ".git").write_text("gitdir: ../store\n")
    found = ul.find_git_dir(tmp_path / "proj")
    assert found is not None and found.resolve() == (tmp_path / "store").resolve()


def test_outside_git_runs_unlocked_with_notice(tmp_path: Path, capsys: pytest.CaptureFixture,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ul, "find_git_dir", lambda start: None)
    ran = []
    with ul.update_run(tmp_path, ["--update"], dry_run=False) as run:
        ran.append(True)
        run.set_exit(0)
    assert ran and "not in a git checkout" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_sandbox_denial_runs_unlocked_with_notice(checkout: Path, capsys: pytest.CaptureFixture,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open

    def deny(path, flags, *a, **k):  # noqa: ANN001, ANN202
        if str(path).endswith(ul.LOCK_NAME):
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(ul.os, "open", deny)
    with ul.update_run(checkout, ["--update"], dry_run=False) as run:
        run.set_exit(0)
    assert "running unlocked" in capsys.readouterr().err
    assert not (checkout / ".git" / ul.RECORD_NAME).exists()


def test_no_fcntl_runs_unlocked_with_notice(checkout: Path, capsys: pytest.CaptureFixture,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fcntl", None)  # import fcntl -> ImportError
    with ul.update_run(checkout, ["--update"], dry_run=False) as run:
        run.set_exit(0)
    assert "no fcntl" in capsys.readouterr().err
    assert list((checkout / ".git").iterdir()) == []


def test_lock_exempt_and_mode_classification() -> None:
    ns = argparse.Namespace
    assert ul.is_lock_exempt(ns(dry_run=True, overwrite=True))
    assert ul.is_lock_exempt(ns(dry_run=False, check=True))
    assert ul.is_lock_exempt(ns(dry_run=False, query_index="x"))
    assert not ul.is_lock_exempt(ns(dry_run=False, query_index="x", refresh_index=True))
    assert not ul.is_lock_exempt(ns(dry_run=False, check=True, adopt_orphans=True))
    assert not ul.is_lock_exempt(ns(dry_run=False, list_backups=True, restore_backup="latest"))
    assert not ul.is_lock_exempt(ns(dry_run=False, update=True))
    assert ul.run_mode(ns(update=True), migrate_exemption=True) == "migrate"
    assert ul.run_mode(ns(adopt_only="a")) == "adopt-only"
    assert ul.run_mode(ns(restore_backup="latest")) == "restore-backup"
    assert ul.run_mode(ns(update=True, overwrite=True)) == "update"
    assert ul.run_mode(ns(overwrite=True)) == "overwrite"
    assert ul.run_mode(ns()) == "generate"


def test_detect_invoker() -> None:
    assert ul.detect_invoker({}) == "terminal"
    assert ul.detect_invoker({"CLAUDECODE": "1"}) == "claude-code"
    assert ul.detect_invoker({"GOOSE_TERMINAL": "1"}) == "goose"
    assert ul.detect_invoker({"GOOSE_SESSION_ID": "s"}) == "goose"
    assert ul.detect_invoker({"CLAUDE_CODE_ENTRYPOINT": "cli"}) == "claude-code"
    assert ul.detect_invoker({"CODEX_SANDBOX": "seatbelt"}) == "codex"


def test_agentteams_commit_reads_git_files() -> None:
    commit = ul.agentteams_commit()
    assert commit == "unknown" or (len(commit) >= 40 and all(c in "0123456789abcdef" for c in commit))


# --- review fixes (S1-S5, H2, H3) ---------------------------------------------------------------------------


def test_restore_backup_and_pin_templates_are_locked_even_under_dry_run() -> None:
    """S1: both write under --dry-run (and beside inspection flags), so neither may skip the lock."""
    ns = argparse.Namespace
    assert not ul.is_lock_exempt(ns(dry_run=True, restore_backup="latest"))
    assert not ul.is_lock_exempt(ns(dry_run=True, pin_templates=True))
    assert not ul.is_lock_exempt(ns(dry_run=False, check=True, pin_templates=True))
    assert not ul.is_lock_exempt(ns(dry_run=False, query_index="x", pin_templates=True))
    assert ul.is_lock_exempt(ns(dry_run=True, restore_backup=None, pin_templates=False))
    assert ul.run_mode(ns(pin_templates=True)) == "pin-templates"


def test_dry_run_restore_backup_takes_the_lock(checkout: Path) -> None:
    """S1 through the CLI: --dry-run --restore-backup against a held lock is refused."""
    proc = _hold_lock(checkout / ".git")
    try:
        rc = _main(checkout, "--dry-run", "--restore-backup", "latest")
    finally:
        _release(proc)
    assert rc == 1
    assert sorted(p.name for p in checkout.iterdir()) == [".git"]


def test_gitdir_target_without_head_is_refused(tmp_path: Path) -> None:
    """S2: a .git file naming a directory that is not a git directory is refused."""
    (tmp_path / "victim").mkdir()
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / ".git").write_text(f"gitdir: {tmp_path / 'victim'}\n")
    with pytest.raises(ul.UpdateLockError, match="no HEAD"):
        with ul.update_run(tmp_path / "proj", [], dry_run=False):
            pytest.fail("body must not run")
    assert list((tmp_path / "victim").iterdir()) == []


def test_worktree_back_pointer_must_name_this_git_file(tmp_path: Path) -> None:
    """S2: a .git file cannot borrow another worktree's git directory."""
    wt_git = tmp_path / "main" / ".git" / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    owner = tmp_path / "wt"
    owner.mkdir()
    _link_worktree(owner, wt_git)
    thief = tmp_path / "thief"
    thief.mkdir()
    (thief / ".git").write_text(f"gitdir: {wt_git}\n")
    with pytest.raises(ul.UpdateLockError, match="worktree repair"):
        with ul.update_run(thief, [], dry_run=False):
            pytest.fail("body must not run")
    assert not (wt_git / ul.LOCK_NAME).exists()


def test_symlinked_commondir_is_refused(tmp_path: Path) -> None:
    wt_git = tmp_path / "main" / ".git" / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    _link_worktree(wt, wt_git)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "common-link").symlink_to(tmp_path / "elsewhere")
    (wt_git / "commondir").write_text(f"{tmp_path / 'common-link'}\n")
    with pytest.raises(gitdirs.GitDirError, match="symlink"):
        gitdirs.git_dirs(wt)


def test_oversized_git_file_is_a_refusal_not_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """H2: an oversized .git file raises UpdateLockError (which the CLI prints), never a bare ValueError."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").write_text("gitdir: " + "x" * (gitdirs.MAX_POINTER_BYTES + 10) + "\n")
    with pytest.raises(ul.UpdateLockError):
        with ul.update_run(proj, [], dry_run=False):
            pytest.fail("body must not run")
    assert _main(proj) == 1
    assert "[UPDATE-LOCK] refused" in capsys.readouterr().err


def test_redaction_keeps_only_parser_option_strings() -> None:
    """S4: a token shaped like a flag but unknown to the parser (a key, an abbreviation) is dropped."""
    argv = ["-AKIAABCDEFGHIJKLMNOP", "--update", "--AKIAabcdef=1", "--upd", "--merge", "--x" * 3]
    assert ul.redact_argv(argv) == ["--update", "--merge"]
    assert {"--update", "--framework", "-f", "--shrink-policy", "--adopt-only"} <= ul.known_options()


def test_start_path_is_resolved(checkout: Path, tmp_path: Path) -> None:
    """S5: a symlink to the checkout finds the checkout's git dir (resolved), not a .git beside the link."""
    link = tmp_path / "elsewhere" / "link"
    link.parent.mkdir()
    (tmp_path / "elsewhere" / ".git").mkdir()
    link.symlink_to(checkout)
    assert ul.find_git_dir(link) == (checkout / ".git").resolve()


def test_lock_flag_names_are_real_parser_dests() -> None:
    """H3: every flag name the exemption logic reads is a real dest of the CLI parser."""
    from agentteams.cli.parser import _build_parser

    dests = {action.dest for action in _build_parser()._actions}
    names = set(ul._INSPECTION_FLAGS) | set(ul._WRITING_FLAGS) | set(ul._DRY_RUN_WRITING_FLAGS) | {"dry_run"}
    assert names <= dests, names - dests


def test_commit_reads_do_not_follow_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H1: the commit lookup reads HEAD and refs no-follow; a symlinked ref yields ``unknown``."""
    git_dir = tmp_path / "g"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "planted").write_text("a" * 40 + "\n")
    (git_dir / "refs" / "heads" / "main").symlink_to(tmp_path / "planted")
    monkeypatch.setattr(ul.gitdirs, "git_dirs", lambda start: (git_dir, git_dir))
    assert ul.agentteams_commit() == "unknown"
    (git_dir / "refs" / "heads" / "main").unlink()
    (git_dir / "refs" / "heads" / "main").write_text("b" * 40 + "\n")
    assert ul.agentteams_commit() == "b" * 40
    (git_dir / "HEAD").write_text("ref: refs/../../planted\n")
    assert ul.agentteams_commit() == "unknown"
