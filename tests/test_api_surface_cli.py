"""``api_impact`` sees the whole command-line surface (#451 a): positional arguments and their ``choices``, option
``choices``, and options read straight from ``sys.argv`` (a heuristic)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import api_impact
from agentteams._api_surface import cli_label, cli_term, surface


def test_a_positional_argument_and_its_choices_are_surface():
    s = surface('import argparse\nap = argparse.ArgumentParser()\nap.add_argument("command", choices=["plan", "copy"])\n')
    assert s["cli-pos:command"] == "choices=['copy', 'plan']"


def test_option_choices_are_in_the_value_and_plain_options_stay_empty():
    s = surface('ap.add_argument("--mode", "-m", choices=("a", "b"))\nap.add_argument("--json", action="store_true")\n')
    assert s["cli:--mode"] == s["cli:-m"] == "choices=['a', 'b']" and s["cli:--json"] == ""


def test_a_literal_sys_argv_test_is_a_heuristic_option_unless_declared():
    s = surface('import sys\nwrite = "--write" in sys.argv[1:]\nquiet = "-q" not in sys.argv\n'
                'ap.add_argument("--json")\nj = "--json" in sys.argv\nx = "--x" in other\n')
    assert "cli-argv:--write" in s and "cli-argv:-q" in s
    assert "cli-argv:--json" not in s and "cli-argv:--x" not in s


def test_labels_and_terms():
    assert cli_label("cli:--x") == "--x"
    assert cli_label("cli-pos:command") == "command (positional)"
    assert cli_label("cli-argv:--w") == "--w (sys.argv, heuristic)"
    assert cli_label("cli-cmd:sub") is None and cli_label("main") is None
    assert cli_term("command (positional)") == "command" and cli_term("--x") == "--x"


_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], env=_ENV, capture_output=True, check=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git")
@pytest.mark.parametrize("before,after,field,entry", [
    ('ap.add_argument("command", choices=["plan", "copy", "purge"])\n',
     'ap.add_argument("command", choices=["plan", "copy"])\n', "cli_changed", "command (positional)"),
    ('ap.add_argument("command", choices=["plan"])\n',
     'ap.add_argument("command", choices=["plan", "purge"])\n', "cli_changed", "command (positional)"),
    ("x = 1\n", 'import sys\nwrite = "--write" in sys.argv[1:]\n', "cli_added", "--write (sys.argv, heuristic)"),
    ('ap.add_argument("target")\n', "x = 1\n", "cli_removed", "target (positional)"),
])
def test_detect_reports_command_line_changes(tmp_path: Path, before: str, after: str, field: str, entry: str):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "tool.py").write_text(before)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "a")
    (tmp_path / "tool.py").write_text(after)
    _git(tmp_path, "add", "-A")
    imp = api_impact.detect(tmp_path)
    assert getattr(imp, field) == [entry]
    assert not imp.symbols_changed and not imp.symbols_added
