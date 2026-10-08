"""The shared launcher ``--exclude`` helper used by the Goose and Codex runners (follow-up F3, 2026-10-08).

The launcher takes ``--exclude`` literally, so a ``~/`` path must be spelled ``"$HOME"/'rest'``; the Goose runner
used to pass ``'~/x'``, which excluded nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from agentteams.frameworks._goose_sandbox_emit import _build_linux_goose_runner
from agentteams.frameworks._write_roots import runner_exclude_flags

_EXCLUSIVE = {"privilege_profile": "exclusive", "workspace_write_roots": ["."],
              "protected_read_paths": ["~/sibling-team", "/abs/other", "/bad;rm", "~", "~bob/x"]}


def test_helper_spells_home_paths_for_the_shell_and_skips_unsafe_ones() -> None:
    flags, note = runner_exclude_flags(dict(_EXCLUSIVE))
    assert flags == """ --exclude "$HOME"/sibling-team --exclude /abs/other"""
    assert "3 protected_read_path(s) SKIPPED" in note
    assert runner_exclude_flags({"privilege_profile": "confined", "protected_read_paths": ["/x"]}) == ("", "")


def test_goose_runner_expands_home_in_its_exclude(tmp_path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    proj, home = tmp_path / "proj", tmp_path / "home"
    for d in (proj / ".goose", proj / "sandbox", home):
        d.mkdir(parents=True)
    runner = proj / ".goose" / "confined-run.example.sh"
    runner.write_text(_build_linux_goose_runner(dict(_EXCLUSIVE)), encoding="utf-8")
    stub = proj / "sandbox" / "confine-run.sh"                # prints the argv the generated runner passes
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {"HOME": str(home), "USER": "u", "PATH": os.environ.get("PATH", ""), "OPENROUTER_API_KEY": "k",
           "GOOSE_CONFINE_SCRATCH": str(tmp_path / "scratch")}
    out = subprocess.run([bash, str(runner)], env=env, capture_output=True, text=True, check=True)
    argv = out.stdout.splitlines()
    excludes = [argv[i + 1] for i, a in enumerate(argv) if a == "--exclude"]
    assert excludes == [f"{home}/sibling-team", "/abs/other"]          # ~ expanded by the operator's shell
    assert "3 protected_read_path(s) SKIPPED" in out.stderr
