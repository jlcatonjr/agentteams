"""Opt-in API-impact categories: constants and generated files (#451 c)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import _api_impact_categories as cat
from agentteams import api_impact
from agentteams._api_surface import constants

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git")
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
_CATS = "references/api-impact-reviews/CATEGORIES"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], env=_ENV, capture_output=True, check=True)


def _commit(repo: Path, files: dict[str, str], msg: str = "c") -> None:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/conf.py": 'DEFAULT_PATH = "a"\nTIMEOUT: int = 5\n_PRIVATE = 1\nlower = 2\n',
        ".claude/hooks/gate.py": "FAIL_CLOSED = True\n\ndef main():\n    return 0\n",
        "references/api-impact-reviews/.keep": "",
    }, "base")
    return tmp_path


def _edit(repo: Path) -> api_impact.Impact:
    _commit(repo, {"pkg/conf.py": 'DEFAULT_PATH = "b"\nRETRIES = 3\n_PRIVATE = 2\nlower = 3\n',
                   ".claude/hooks/gate.py": "FAIL_CLOSED = False\n\ndef main():\n    return 0\n"}, "edit")
    return api_impact.detect(repo, commit="HEAD")


def test_off_by_default_reports_nothing_new(repo: Path):
    imp = _edit(repo)
    assert imp.symbols_added == imp.symbols_removed == imp.symbols_changed == imp.generated_changed == []
    assert not imp.impactful


def test_constants_count_as_symbols(repo: Path):
    _commit(repo, {_CATS: "# opt in\nconstants\n"}, "opt in")
    imp = _edit(repo)
    assert imp.symbols_added == ["pkg/conf.py::RETRIES"]
    assert imp.symbols_removed == ["pkg/conf.py::TIMEOUT"]
    assert imp.symbols_changed == [".claude/hooks/gate.py::FAIL_CLOSED", "pkg/conf.py::DEFAULT_PATH"]
    assert imp.generated_changed == []


def test_generated_lists_a_body_only_hook_edit(repo: Path):
    _commit(repo, {_CATS: "generated\n"}, "opt in")
    imp = _edit(repo)
    assert imp.generated_changed == [".claude/hooks/gate.py"] and imp.symbols_changed == []


def test_generated_extra_globs(repo: Path):
    _commit(repo, {_CATS: "generated tools/gen/*\n", "tools/gen/out.sh": "a\n"}, "opt in")
    _commit(repo, {"tools/gen/out.sh": "b\n"}, "regen")
    assert api_impact.detect(repo, commit="HEAD").generated_changed == ["tools/gen/out.sh"]


def test_changing_categories_is_itself_impactful(repo: Path):
    _commit(repo, {_CATS: "constants\n"}, "opt in")
    imp = api_impact.detect(repo, commit="HEAD")
    assert imp.review_baseline_changed == [_CATS] and imp.impactful


def test_the_staged_check_reads_categories_from_the_index(repo: Path):
    _commit(repo, {_CATS: "generated\n"}, "opt in")
    (repo / ".claude/hooks/gate.py").write_text("FAIL_CLOSED = False\n")
    _git(repo, "add", "-A")
    assert api_impact.detect(repo).generated_changed == [".claude/hooks/gate.py"]


def test_parse_warns_on_unknown_lines(capsys: pytest.CaptureFixture[str]):
    cats = cat.parse("constants\nconstnats\ngenerated a/*\n")
    assert cats.constants and cats.generated == [*cat.DEFAULT_GENERATED, "a/*"]
    assert "constnats" in capsys.readouterr().err


def test_constants_surface():
    assert constants('A = 1\nB: str\nC, d = 1, 2\nE = F = "x"\n_G = 1\n') == {"A": "1", "B": ": str", "E": "'x'",
                                                                             "F": "'x'"}
    assert constants("def (") is None
    with pytest.raises(TypeError):
        constants(b"A = 1")  # type: ignore[arg-type]


def test_text_report_unchanged_without_categories(repo: Path):
    imp = _edit(repo)
    assert "generated" not in imp.summary()
