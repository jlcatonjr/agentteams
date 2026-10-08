"""The neutral launcher protects the control plane on macOS too (F-4 parity with Linux, 2026-10-07)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
TEXT = LAUNCHER.read_text()


def _macos_branch() -> str:
    start = TEXT.index("build_macos() {")
    return TEXT[start:TEXT.index("\n}\n", start)]


def test_the_macos_profile_writes_control_plane_denies_after_the_allows():
    body = _macos_branch()
    assert "control_plane_collect" in body
    deny_sub = body.index('(deny file-write* (subpath \\"$cp\\"))')
    deny_lit = body.index('(deny file-write* (literal \\"$cp\\"))')
    last_allow = body.rindex("(allow file-write*")
    assert last_allow < deny_sub and last_allow < deny_lit     # SBPL: the last matching rule wins
    assert 'reject_sbpl_meta "$cp"' in body                     # every interpolated path is SBPL-checked


def test_no_mapfile_and_no_gnu_only_realpath_in_shared_or_macos_code():
    def code(s: str) -> str:   # drop comment lines and trailing comments
        return "\n".join(line.split(" #", 1)[0] for line in s.splitlines() if not line.lstrip().startswith("#"))
    collect = code(TEXT[TEXT.index("control_plane_collect() {"):TEXT.index("control_plane_binds() {")])
    assert "mapfile" not in collect and "mapfile" not in code(_macos_branch())
    assert "realpath -e" not in collect                          # roots go through phys_dir


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="no /bin/bash")
def test_the_launcher_parses_under_the_system_bash():
    # macOS's /bin/bash is 3.2: the macOS branch must stay within it.
    assert subprocess.run(["/bin/bash", "-n", str(LAUNCHER)], capture_output=True).returncode == 0


def _team(root: Path) -> Path:
    refs = root / ".claude" / "agents" / "references"
    (refs / "authorized-verify-keys").mkdir(parents=True)
    (refs / "agent-privilege.json").write_text('{"enforce_decision_signing": true}')
    (refs / "build-log.json").write_text("{}")
    for f in ("security-approvers.txt", "authorized-managers.txt", "management-authority.json"):
        (refs / f).write_text("x")
    (root / ".claude" / "agents" / "o.md").write_text("---\nname: o\n---\n")
    (root / ".claude" / "hooks").mkdir()
    (root / ".claude" / "hooks" / "constitutional-gate.py").write_text("#")
    return refs


def _sandbox_usable() -> bool:
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        return False
    probe = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                           capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _sandbox_usable(), reason="macOS sandbox-exec not usable here (non-macOS, or nested)")
def test_live_macos_control_plane_is_write_protected(tmp_path):
    home_dir = Path(os.path.expanduser("~")) / f".agentteams-cp-probe-{os.getpid()}"   # outside /private/tmp
    try:
        proj, scr = home_dir / "proj", home_dir / "scr"
        scr.mkdir(parents=True)
        refs = _team(proj)
        script = (f'echo ok > "{proj}/baseline" && echo BASE;'
                  f'(echo pwn > "{refs}/agent-privilege.json") 2>/dev/null && echo SWITCH;'
                  f'(mv "{refs}" "{proj}/.claude/agents/moved") 2>/dev/null && echo RENAME;'
                  f'(rm "{refs}/build-log.json") 2>/dev/null && echo MARKER')
        out = subprocess.run(["/bin/bash", str(LAUNCHER), "--scratch", str(scr), "--writable", str(proj), "--",
                              "/bin/sh", "-c", script], capture_output=True, text=True, timeout=60).stdout
        assert "BASE" in out                                   # positive control: the root is writable
        assert "SWITCH" not in out and "RENAME" not in out and "MARKER" not in out
        assert "true" in (refs / "agent-privilege.json").read_text()
    finally:
        shutil.rmtree(home_dir, ignore_errors=True)



def _run(args: list[str], script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/bash", str(LAUNCHER), *args, "--", "/bin/sh", "-c", script],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(not _sandbox_usable(), reason="macOS sandbox-exec not usable here (non-macOS, or nested)")
def test_a_root_under_private_tmp_cannot_be_renamed_away():
    import tempfile
    base = Path(tempfile.mkdtemp(dir="/private/tmp"))
    try:
        proj, scr = base / "proj", base / "scr"
        scr.mkdir()
        refs = _team(proj)
        out = _run(["--scratch", str(scr), "--writable", str(proj)],
                   f'echo ok > "{proj}/b" && echo BASE; (mv "{proj}" "{base}/moved") 2>/dev/null && echo RENAMED').stdout
        assert "BASE" in out and "RENAMED" not in out
        assert "true" in (refs / "agent-privilege.json").read_text()
    finally:
        shutil.rmtree(base, ignore_errors=True)


_LAUNCHER_BUILDS = (sys.platform == "darwin" and shutil.which("sandbox-exec") is not None) or (
    sys.platform.startswith("linux") and shutil.which("bwrap") is not None)


@pytest.mark.skipif(not _LAUNCHER_BUILDS, reason="no sandbox backend here (the launcher refuses before the check)")
def test_a_protected_file_with_a_second_hard_link_is_refused(tmp_path):
    proj, scr = tmp_path / "proj", tmp_path / "scr"
    scr.mkdir()
    refs = _team(proj)
    os.link(refs / "agent-privilege.json", proj / "alias")
    out = _run(["--scratch", str(scr), "--writable", str(proj), "--check"], "true")
    assert out.returncode == 2 and "hard link" in out.stderr   # the file check or the .claude sweep, whichever runs first


@pytest.mark.skipif(not _LAUNCHER_BUILDS, reason="no sandbox backend here (the launcher refuses before the check)")
def test_a_protect_symlink_with_a_trailing_slash_is_refused(tmp_path):
    proj, scr = tmp_path / "proj", tmp_path / "scr"
    scr.mkdir()
    _team(proj)
    (tmp_path / "real").mkdir()
    (proj / "link").symlink_to(tmp_path / "real")
    out = _run(["--scratch", str(scr), "--writable", str(proj), "--protect", f"{proj}/link/", "--check"], "true")
    assert out.returncode == 2 and "symlink" in out.stderr



@pytest.mark.skipif(not _LAUNCHER_BUILDS, reason="no sandbox backend here (the launcher refuses before the check)")
def test_a_hard_linked_file_inside_a_protected_directory_is_refused(tmp_path):
    proj, scr = tmp_path / "proj", tmp_path / "scr"
    scr.mkdir()
    refs = _team(proj)
    key = refs / "authorized-verify-keys" / "op.pub.pem"
    key.write_text("k")
    os.link(key, proj / "key-alias")
    out = _run(["--scratch", str(scr), "--writable", str(proj), "--check"], "true")
    assert out.returncode == 2 and "more than one hard link" in out.stderr
