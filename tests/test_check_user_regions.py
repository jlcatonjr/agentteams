"""``--check`` treats an operator's own notes as operator text, not drift (baseAgent 2026-11-02).

When the manifest fingerprint moved (the live intel moves it daily), ``--check`` compared each file with a fresh
render, so any text in ``## Project-Specific Notes`` failed it. The comparison now carries the disk file's user
regions into the render first, as ``--update`` keeps them. An edit inside a fence still fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from tests._fresh_intel import seed_offline_intel

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"


def _run(project: Path, out: Path, brief: Path, *extra: str, seed: bool = True) -> subprocess.CompletedProcess:
    intel = seed_offline_intel(out, "copilot-vscode") if seed else ["--security-offline"]
    return subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(brief), "--project",
                           str(project), "--framework", "copilot-vscode", "--output", str(out), "--no-scan", "--yes",
                           *extra, *intel], cwd=project, capture_output=True, text=True, timeout=300)


def test_operator_notes_are_not_drift_but_a_fence_edit_is(tmp_path: Path):
    brief = tmp_path / "brief.json"
    brief.write_text(BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / ".github" / "agents"
    assert _run(tmp_path, out, brief).returncode == 0
    assert _run(tmp_path, out, brief, "--update", "--merge", "--no-backup").returncode == 0
    nav = out / "navigator.agent.md"
    nav.write_text(nav.read_text(encoding="utf-8").replace("## Project-Specific Notes\n",
                                                           "## Project-Specific Notes\n\n- operator note\n", 1),
                   encoding="utf-8")
    check = _run(tmp_path, out, brief, "--check", seed=False)
    assert check.returncode == 0, check.stdout[-1500:] + check.stderr[-1500:]
    nav.write_text(nav.read_text(encoding="utf-8").replace("<!-- AGENTTEAMS:END", "tampered\n<!-- AGENTTEAMS:END", 1),
                   encoding="utf-8")
    check = _run(tmp_path, out, brief, "--check", seed=False)
    assert check.returncode == 1 and "navigator.agent.md" in re.findall(r"^\s+~ (\S+)", check.stdout, re.M)
