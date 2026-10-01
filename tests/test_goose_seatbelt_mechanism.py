"""#5 (2026-09-30): the emitted goose Seatbelt profile, run under the real ``sandbox-exec``.

Opt-in (``RUN_GOOSE_SEATBELT_PROBES=1``) on darwin with ``/usr/bin/sandbox-exec``: the CI
``macos-seatbelt`` job sets it (advisory first). The workspace and the "outside" target live
under ``$HOME``, never under a tmp dir the profile allows (``/private/tmp``, ``/private/var/folders``),
so the inside-write negative control and the outside-write deny can both genuinely fail. Params
are passed as goose's documented launch line does, with realpaths (Seatbelt matches resolved
paths; ``/var`` is a symlink to ``/private/var``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_probes = pytest.mark.skipif(
    not (os.environ.get("RUN_GOOSE_SEATBELT_PROBES") == "1" and sys.platform == "darwin"
         and Path(SANDBOX_EXEC).exists()),
    reason="opt-in macOS Seatbelt probes (RUN_GOOSE_SEATBELT_PROBES=1 on darwin)",
)

_SWITCH = ".goose/recipes/references/agent-privilege.json"
_STORE = ".goose/recipes/references/authorized-verify-keys"


@pytest.fixture
def ws():
    base = Path(os.path.realpath(Path.home())) / f".agentteams-seatbelt-probe-{os.getpid()}"
    shutil.rmtree(base, ignore_errors=True)
    work, outside = base / "work", base / "outside"
    for rel in (_SWITCH, ".claude/agents/references/agent-privilege.json", ".claude/settings.json",
                ".codex/config.toml"):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text("ORIG\n")
    (work / _STORE).mkdir(parents=True, exist_ok=True)
    (work / _STORE / "README.md").write_text("ORIG\n")
    outside.mkdir(parents=True)
    profile = base / "sandbox.sb"
    profile.write_text(_build_seatbelt_profile(None), encoding="utf-8")
    yield work, outside, profile
    shutil.rmtree(base, ignore_errors=True)


def _run(ws_tuple, cmd: str) -> subprocess.CompletedProcess[str]:
    work, _, profile = ws_tuple
    return subprocess.run(
        [SANDBOX_EXEC, "-f", str(profile), "-D", f"WORKSPACE_ROOT={work}",
         "-D", f"HOME_DIR={os.path.realpath(Path.home())}", "/bin/sh", "-c", cmd],
        cwd=str(work), capture_output=True, text=True, timeout=60,
    )


@_probes
def test_profile_parses_and_runs(ws):
    r = _run(ws, "/usr/bin/true")
    assert r.returncode == 0, f"profile did not apply: {r.stderr}"


@_probes
def test_negative_control_inside_write_succeeds(ws):
    _run(ws, "echo x > inside.txt")
    assert (ws[0] / "inside.txt").exists(), "write inside WORKSPACE_ROOT was denied (control failed)"


@_probes
def test_write_outside_the_workspace_is_denied(ws):
    _run(ws, f"echo x > '{ws[1]}/escaped.txt'")
    assert not (ws[1] / "escaped.txt").exists()


@_probes
@pytest.mark.parametrize("rel", [_SWITCH, ".claude/agents/references/agent-privilege.json",
                                 ".claude/settings.json", ".codex/config.toml"])
def test_control_plane_files_are_write_denied(ws, rel):
    _run(ws, f"echo TAMPERED > '{rel}'")
    assert (ws[0] / rel).read_text() == "ORIG\n"


@_probes
def test_verify_key_store_cannot_be_planted(ws):
    _run(ws, f"echo k > '{_STORE}/evil.pub.pem'")
    assert not (ws[0] / _STORE / "evil.pub.pem").exists()


@_probes
@pytest.mark.parametrize("src", [".goose", ".goose/recipes", ".goose/recipes/references", ".codex",
                                 ".claude", ".claude/agents"])
def test_control_plane_ancestors_cannot_be_renamed(ws, src):
    _run(ws, f"mv '{src}' '{src}.moved'")
    assert (ws[0] / src).is_dir() and not (ws[0] / f"{src}.moved").exists(), src


# --- always-on (not opt-in): the macOS gate must target a file the emitter ships ------------------

def test_mac_escape_tests_wrap_names_the_emitted_launcher():
    import re

    from agentteams.frameworks import _linux_sandbox_emit as lse

    text = (Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "universal"
            / lse._MACOS_DENYTEST_ASSET_REL).read_text(encoding="utf-8")
    wrap = re.search(r'^WRAP="\$HERE/([^"]+)"', text, re.M).group(1)
    # mac-escape-tests.sh is emitted beside the launcher (same sandbox/ dir)
    assert lse._MACOS_DENYTEST_ASSET_REL.rsplit("/", 1)[0] == lse._LAUNCHER_ASSET_REL.rsplit("/", 1)[0]
    assert wrap == lse._LAUNCHER_ASSET_REL.rsplit("/", 1)[1]
