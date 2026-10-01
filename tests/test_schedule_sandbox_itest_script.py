"""#6: the operator timer script is dry-run by default, refuses root/sudo, and carries no secrets."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "schedule-sandbox-itest.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="bash")


def _run(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "claude").write_text("#!/bin/sh\necho 2.1.251\n")
    (bindir / "claude").chmod(0o755)
    base = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    base.pop("SUDO_USER", None)
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          env={**base, **env})


@pytest.mark.skipif(os.geteuid() == 0, reason="the script refuses root")
def test_dry_run_writes_nothing_and_shows_bounded_units(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "DRY RUN" in r.stdout and not (tmp_path / "cfg").exists()
    for needle in ("TimeoutStartSec=1800", "Persistent=false", "RandomizedDelaySec", "UMask=0077",
                   "%h/.cache/agentteams/sandbox-itest.log", "Cost:"):
        assert needle in r.stdout, needle
    assert "API_KEY" not in r.stdout and "EnvironmentFile" not in r.stdout
    assert "enable-linger" not in SCRIPT.read_text().replace("never enables linger", "")


def test_refuses_sudo(tmp_path):
    r = _run(tmp_path, "--apply", SUDO_USER="someone")
    assert r.returncode == 2 and "refusing" in r.stderr
    assert not (tmp_path / "cfg").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="the script refuses root")
@pytest.mark.parametrize("bad", ["a%h", "a b", "a;b", "a$x"])
def test_refuses_unsafe_unit_values(tmp_path, bad):
    repo = tmp_path / bad
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_os_sandbox_product_enforcement.py").write_text("")
    r = _run(tmp_path, "--repo", str(repo))
    assert r.returncode == 2 and "unsafe path" in r.stderr
