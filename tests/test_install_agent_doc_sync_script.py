"""The operator doc-sync unit installer: dry-run by default, refuses root/sudo, unsafe paths, and
any interpreter/package an agent could edit. Only ever exercised in dry-run mode here — --apply
installs a persistent systemd unit and must never run from the test suite."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install-agent-doc-sync.sh"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="bash"),
    pytest.mark.skipif(os.geteuid() == 0, reason="the script refuses root"),
]


def _project(tmp_path: Path, name: str = "proj", dirs: tuple[str, ...] = (".github/agents",
             ".goose/recipes")) -> Path:
    proj = tmp_path / name
    for d in dirs:
        (proj / d).mkdir(parents=True)
    return proj


def _run(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          env={**base, **env})


def test_dry_run_writes_nothing_and_shows_bounded_units(tmp_path):
    proj = _project(tmp_path)
    r = _run(tmp_path, "--project", str(proj), "--python", sys.executable)
    assert r.returncode == 0, r.stderr
    assert "DRY RUN" in r.stdout and not (tmp_path / "cfg").exists()
    real = os.path.realpath(proj)
    for needle in (
        f"PathChanged={real}/.github/agents", f"PathChanged={real}/.goose/recipes",
        f"/usr/bin/env -i PATH=/usr/bin:/bin HOME=%h {sys.executable} -I -m agentteams.cli.app "
        f"--sync-agent-docs --project {real} --apply >>",
        f"WorkingDirectory={real}", "TimeoutStartSec=300", "ExecStartPre=/bin/sleep 5",
        "StartLimitIntervalSec=", "StartLimitBurst=", "TriggerLimitBurst=", "UMask=0077",
        "%h/.cache/agentteams/doc-sync-", "agentteams-doc-sync@",
    ):
        assert needle in r.stdout, needle
    assert "PathChanged=" + real + "/.claude/agents" not in r.stdout  # absent dir: not watched
    service = r.stdout.split(".service:", 1)[1]
    assert "--include-claude" not in service.split("Claude targets are only staged")[0]
    assert "enable-linger" not in SCRIPT.read_text().replace("never enables linger", "")


def test_refuses_sudo(tmp_path):
    proj = _project(tmp_path)
    r = _run(tmp_path, "--project", str(proj), "--apply", SUDO_USER="someone")
    assert r.returncode == 2 and "refusing" in r.stderr
    assert not (tmp_path / "cfg").exists()


@pytest.mark.parametrize("bad", ["a%h", "a b", "a;b", "a$x", "a'b"])
def test_refuses_unsafe_unit_values(tmp_path, bad):
    proj = _project(tmp_path, name=bad)
    r = _run(tmp_path, "--project", str(proj), "--python", sys.executable)
    assert r.returncode == 2 and "unsafe path" in r.stderr


def test_refuses_interpreter_inside_project(tmp_path):
    proj = _project(tmp_path)
    (proj / "bin").mkdir()
    (proj / "bin" / "python3").symlink_to(sys.executable)
    r = _run(tmp_path, "--project", str(proj), "--python", str(proj / "bin" / "python3"))
    assert r.returncode == 2 and "inside the project" in r.stderr, r.stderr


def test_refuses_interpreter_inside_an_allow_write_root(tmp_path):
    proj = _project(tmp_path)
    shared = tmp_path / "shared"
    (shared / "bin").mkdir(parents=True)
    (shared / "bin" / "python3").symlink_to(sys.executable)
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps(
        {"sandbox": {"enabled": True, "filesystem": {"allowWrite": [str(shared)]}}}))
    r = _run(tmp_path, "--project", str(proj), "--python", str(shared / "bin" / "python3"))
    assert r.returncode == 2 and "allowWrite root" in r.stderr, r.stderr


def test_refuses_project_without_agent_dirs(tmp_path):
    proj = _project(tmp_path, dirs=())
    proj.mkdir(exist_ok=True)
    r = _run(tmp_path, "--project", str(proj), "--python", sys.executable)
    assert r.returncode == 2 and "no agent dirs" in r.stderr


def test_refuses_symlinked_agent_dir(tmp_path):
    proj = _project(tmp_path, dirs=(".github/agents",))
    (tmp_path / "elsewhere").mkdir()
    (proj / ".goose").symlink_to(tmp_path / "elsewhere")
    r = _run(tmp_path, "--project", str(proj), "--python", sys.executable)
    assert r.returncode == 2 and "symlink" in r.stderr


def test_requires_project(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 2 and "--project" in r.stderr
