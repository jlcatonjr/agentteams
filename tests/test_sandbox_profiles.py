"""Tests for agentteams/_sandbox_profiles.py — the extracted Seatbelt/bwrap builders (METAPLAN v2 §6 R2).

The byte-identity tests compare today's ``confinement.seatbelt_profile`` / ``bwrap_argv`` against FROZEN copies
of the pre-extraction (v0) bodies, taken verbatim from ``agentteams/confinement.py`` at 43f435f4. They prove the
refactor changed no emitted profile. Do not "update" the frozen copies to match a later change: a deliberate
change to the profile belongs in the confinement tests, with its own review.
"""

from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import confinement as C
from agentteams.frameworks._sandbox_emit import SIGNING_KEY_DIR

# --- frozen v0 bodies (verbatim from confinement.py @ 43f435f4, constants read from confinement) ----------------
_V0_BAD = ('"', "\n", "\r", "\\", "\0")


def _v0_clean(path: str) -> str:
    if any(c in path for c in _V0_BAD) or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise C.ConfinementError(f"path {path!r} contains a character that can't be expressed safely; refused")
    return path


def _v0_real(path: str) -> str:
    return _v0_clean(os.path.realpath(os.path.expanduser(path)))


def _v0_seatbelt_profile(*, root, exec_paths, write_roots, tmp_dir, exec_literals=()):
    real_root = _v0_real(str(root))
    writes = [_v0_real(str(tmp_dir))] + [_v0_real(str(root / w)) for w in write_roots]
    protected = [_v0_real(str(root / p)) for p in C.PROTECTED_REL]
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write*",
        *[f'    (subpath "{w}")' for w in writes],
        *[f'    (literal "{d}")' for d in C._DEV_WRITES],
        ")",
        "(deny file-write*",
        *[f'    (subpath "{p}")' for p in protected],
        f'    (literal "{real_root}")',
        f'    (subpath "{_v0_real(C.KEY_DIR)}")',
        '    (regex #"/\\.[gG][iI][tT](/|$)")',
        ")",
        f'(deny file-read* (subpath "{_v0_real(C.KEY_DIR)}"))',
        "(deny process-exec*)",
        "(allow process-exec*",
        *[f'    (subpath "{_v0_real(e)}")' for e in exec_paths],
        *[f'    (literal "{_v0_real(e)}")' for e in exec_literals],
        ")",
    ]
    return "\n".join(lines) + "\n"


def _v0_bwrap_argv(*, root, write_roots, tmp_dir, cwd):
    argv = [shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--bind", _v0_real(str(tmp_dir)), _v0_real(str(tmp_dir))]
    for w in write_roots:
        target = _v0_real(str(root / w))
        argv += ["--bind", target, target]
    for p in C.PROTECTED_REL:
        target = root / p
        if target.exists():
            argv += ["--ro-bind", _v0_real(str(target)), _v0_real(str(target))]
    keys = Path(os.path.expanduser(C.KEY_DIR))
    if keys.exists():
        argv += ["--tmpfs", _v0_real(str(keys))]
    argv += ["--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session",
             "--chdir", _v0_real(str(cwd))]
    return argv
# -----------------------------------------------------------------------------------------------------------------


def _project(tmp_path: Path, *, protected: bool) -> Path:
    root = tmp_path / "proj"
    (root / "build").mkdir(parents=True)
    (root / "out" / "deep").mkdir(parents=True)
    if protected:
        for p in (".agentteams", ".git", ".claude"):
            (root / p).mkdir()
    return root


_CASES = [
    dict(exec_paths=[], write_roots=[], exec_literals=()),
    dict(exec_paths=["/usr/bin", "/bin/sh"], write_roots=["build"], exec_literals=()),
    dict(exec_paths=["/usr/bin/git"], write_roots=["build", "out/deep"],
         exec_literals=["/Library/Developer/CommandLineTools/usr/bin/git"]),
]


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("protected", [False, True])
def test_seatbelt_profile_is_byte_identical_to_v0(tmp_path: Path, case: dict, protected: bool) -> None:
    root = _project(tmp_path, protected=protected)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    got = C.seatbelt_profile(root=root, tmp_dir=tmp, **case)
    assert got == _v0_seatbelt_profile(root=root, tmp_dir=tmp, **case)


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("protected", [False, True])
def test_bwrap_argv_is_byte_identical_to_v0(tmp_path: Path, case: dict, protected: bool) -> None:
    root = _project(tmp_path, protected=protected)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    kw = dict(root=root, write_roots=case["write_roots"], tmp_dir=tmp, cwd=root / "build")
    assert C.bwrap_argv(**kw) == _v0_bwrap_argv(**kw)


def test_wrap_output_unchanged_for_bwrap(tmp_path: Path) -> None:
    root = _project(tmp_path, protected=True)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    got = C.wrap(["/bin/echo", "x"], sandbox="bwrap", root=root, cwd=root, exec_paths=[], write_roots=["build"],
                 tmp_dir=tmp)
    assert got == [*_v0_bwrap_argv(root=root, write_roots=["build"], tmp_dir=tmp, cwd=root), "--", "/bin/echo", "x"]


def test_confinement_reexports_are_the_same_objects() -> None:
    assert C.ConfinementError is SP.ConfinementError
    assert C.kind is SP.kind and C.available is SP.available
    assert C._clean is SP._clean and C._real is SP._real
    assert C._BAD_PATH_CHARS == _V0_BAD == SP.BAD_PATH_CHARS
    assert C.KEY_DIR == SIGNING_KEY_DIR


def test_bad_path_chars_still_refused(tmp_path: Path) -> None:
    with pytest.raises(C.ConfinementError):
        C.seatbelt_profile(root=tmp_path, exec_paths=['/bin/x"y'], write_roots=[], tmp_dir=tmp_path)


def test_no_intra_package_imports() -> None:
    src = Path(SP.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and not (node.module or "").startswith("agentteams"), node.module
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("agentteams") for a in node.names)


# --- the WASM-only parameters (Rule-12 audit targets) --------------------------------------------------------------

def _sb(root: Path, tmp: Path, **kw: object) -> str:
    return SP.seatbelt_profile(root=root, exec_paths=[], write_roots=[], tmp_dir=tmp, protected_rel=C.PROTECTED_REL,
                               key_dir=C.KEY_DIR, dev_writes=C._DEV_WRITES, **kw)


def test_new_parameters_absent_by_default(tmp_path: Path) -> None:
    prof = _sb(tmp_path, tmp_path)
    assert "network" not in prof and prof.count("(allow file-write*") == 1
    assert "--unshare-net" not in SP.bwrap_argv(root=tmp_path, write_roots=[], tmp_dir=tmp_path, cwd=tmp_path,
                                                protected_rel=C.PROTECTED_REL, key_dir=C.KEY_DIR)


def test_deny_network_and_reads(tmp_path: Path) -> None:
    creds = tmp_path / "creds"
    creds.mkdir()
    netrc = tmp_path / "netrc"
    netrc.write_text("x")
    prof = _sb(tmp_path, tmp_path, deny_network=True, deny_reads=[str(creds), str(tmp_path / "absent")])
    assert prof.endswith("(deny network*)\n")
    assert f'(subpath "{os.path.realpath(creds)}")' in prof and "absent" in prof  # Seatbelt denies absent paths too
    argv = SP.bwrap_argv(root=tmp_path, write_roots=[], tmp_dir=tmp_path, cwd=tmp_path, protected_rel=(),
                         key_dir=str(tmp_path / "nokeys"), deny_network=True,
                         deny_reads=[str(creds), str(netrc), str(tmp_path / "absent")])
    assert "--unshare-net" in argv
    assert ["--tmpfs", os.path.realpath(creds)] == argv[argv.index("--tmpfs"):argv.index("--tmpfs") + 2]
    assert ["--ro-bind", "/dev/null", os.path.realpath(netrc)] == argv[argv.index("/dev/null") - 1:
                                                                      argv.index("/dev/null") + 2]
    assert not any("absent" in a for a in argv)


@pytest.mark.parametrize("rel", [".agentteams/wasm/scratch/navigator",
                                 ".agentteams/wasm/exec/0123456789abcdef/scratch"])
def test_late_allow_accepts_only_scratch(tmp_path: Path, rel: str) -> None:
    (tmp_path / rel).mkdir(parents=True)
    prof = _sb(tmp_path, tmp_path, late_write_allows=[str(tmp_path / rel)])
    deny_at = prof.index('(subpath "' + os.path.realpath(tmp_path / ".agentteams") + '")')
    allow_at = prof.index('(subpath "' + os.path.realpath(tmp_path / rel) + '")')
    assert allow_at > deny_at  # SBPL: last match wins, so the allow must follow the protected deny
    argv = SP.bwrap_argv(root=tmp_path, write_roots=[], tmp_dir=tmp_path, cwd=tmp_path,
                         protected_rel=C.PROTECTED_REL, key_dir=C.KEY_DIR, late_binds=[str(tmp_path / rel)])
    real = os.path.realpath(tmp_path / rel)
    assert argv.index(real) > argv.index(os.path.realpath(tmp_path / ".agentteams"))


@pytest.mark.parametrize("rel", [".agentteams", ".agentteams/wasm", ".agentteams/wasm/scratch",
                                 ".agentteams/wasm/exec/0123456789abcdef", ".agentteams/wasm/exec/0123456789abcdef/bin",
                                 ".agentteams/wasm/scratch/Bad_Name", ".agentteams/wasm/scratch/a/b", ".git",
                                 "src", ".agentteams/wasm/scratch/../../ledger"])
def test_late_allow_refuses_anything_else(tmp_path: Path, rel: str) -> None:
    with pytest.raises(SP.ConfinementError):
        _sb(tmp_path, tmp_path, late_write_allows=[str(tmp_path / rel)])
    with pytest.raises(SP.ConfinementError):
        SP.bwrap_argv(root=tmp_path, write_roots=[], tmp_dir=tmp_path, cwd=tmp_path, protected_rel=(),
                      key_dir=C.KEY_DIR, late_binds=[str(tmp_path / rel)])


def test_late_allow_through_symlink_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".agentteams" / "wasm" / "scratch").mkdir(parents=True)
    (tmp_path / ".agentteams" / "wasm" / "scratch" / "evil").symlink_to(tmp_path / ".git")
    with pytest.raises(SP.ConfinementError):
        _sb(tmp_path, tmp_path, late_write_allows=[str(tmp_path / ".agentteams/wasm/scratch/evil")])


def test_late_allow_keeps_nested_git_denied(tmp_path: Path) -> None:
    rel = ".agentteams/wasm/scratch/a"
    (tmp_path / rel).mkdir(parents=True)
    prof = _sb(tmp_path, tmp_path, late_write_allows=[str(tmp_path / rel)])
    assert prof.rindex("(regex") > prof.index('(subpath "' + os.path.realpath(tmp_path / rel) + '")')
