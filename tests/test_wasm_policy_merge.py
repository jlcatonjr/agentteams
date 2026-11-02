"""WASM plan P14: switching WASM on for a deployed Claude team with ``--update --merge`` (Risk R3).

The body's WASM section is refreshed by the merge (it sits inside the file's ``content`` fence), but the new
``run_wasm`` tool and ``mcpServers`` entry are capability declarations: the merge prints a notice and keeps the
on-disk front matter (C-3: a widening is never applied silently). The section therefore tells the agent what to do
while ``run_wasm`` is not among its tools.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests._fresh_intel import seed_offline_intel

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))


def _build(root: Path, brief: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    args = ["--description", str(brief), "--framework", "claude", "--project", str(root), "--output", str(out),
            "--no-scan", "--yes", *seed_offline_intel(out, "claude"), *extra]
    return subprocess.run([sys.executable, str(REPO / "build_team.py"), *args], cwd=root, capture_output=True,
                          text=True, timeout=600)


def test_update_merge_adds_the_section_and_only_surfaces_the_tool(tmp_path):
    out = tmp_path / ".claude" / "agents"
    off = tmp_path / "off.json"
    off.write_text(json.dumps(BRIEF), encoding="utf-8")
    first = _build(tmp_path, off, out)
    assert first.returncode == 0, first.stderr[-2000:]
    before = (out / "cleanup.md").read_text(encoding="utf-8")
    assert "WASM Sandbox" not in before
    front = before.split("\n---\n", 1)[0]

    on = tmp_path / "on.json"
    # native_exec_keep [] keeps the update free of a signed native-exec-keep decision in this governed workspace.
    on.write_text(json.dumps({**BRIEF, "wasm_sandbox": {"enabled": True, "native_exec_keep": []}}), encoding="utf-8")
    merged = _build(tmp_path, on, out, "--update", "--merge")
    assert merged.returncode == 0, (merged.stdout + merged.stderr)[-2000:]
    after = (out / "cleanup.md").read_text(encoding="utf-8")
    assert "## WASM Sandbox: Run Code Through `run_wasm`" in after
    assert "If `run_wasm` is not among your tools" in after
    assert after.split("\n---\n", 1)[0] == front  # the operator's front matter is kept
    log = merged.stdout + merged.stderr
    assert "cleanup.md: front matter: 'tools' is a capability declaration" in log
    assert "mcp__agentteams_wasm__run_wasm" in log and "Not applied automatically" in log
    assert "Laundering / sandbox relaxation" in (out / "security.md").read_text(encoding="utf-8")
    assert (out / "references" / "wasm-sandbox.reference.md").is_file()
