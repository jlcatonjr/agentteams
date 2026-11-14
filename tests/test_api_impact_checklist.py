"""The audit checklist prefers module-qualified evidence (#451 b)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import _api_impact_checklist as ck
from agentteams import api_impact

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git")
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
_USERS = ("pkg/uses_from.py", "pkg/uses_import.py", "pkg/uses_plain.py")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], env=_ENV, capture_output=True, check=True)


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/tool.py": "def main(argv=None):\n    return 0\n",
        "pkg/other.py": "def main():\n    pass\n",                           # an unrelated main
        "pkg/uses_import.py": "from pkg import tool\n\ntool.main([])\n",
        "pkg/uses_from.py": "from pkg.tool import (\n    main,\n)\nmain()\n",
        "pkg/uses_plain.py": "import pkg.tool\n\nprint(pkg.tool.main)\n",
        "docs_src/cli.md": "Run `main` to start.\n",                         # bare word only
        "references/runs/2026-11-11.csv": "main,delivered\n",                # a record that quotes the name
        "references/api-impact-reviews/CHECKLIST_EXCLUDES": "# record paths\nreferences/runs/*\n",
    })
    _commit(tmp_path, "base")
    return tmp_path


def _resign_main(repo: Path) -> dict[str, list[str]]:
    _write(repo, {"pkg/tool.py": "def main(argv=None, *, strict=False):\n    return 0\n"})
    _commit(repo, "re-sign main")
    _, commits = api_impact.status(repo, "HEAD~1..HEAD")
    return commits[-1].checklist


def _drop_users(repo: Path) -> None:
    for f in _USERS:
        (repo / f).unlink()
    _commit(repo, "drop users")


def test_a_common_name_lists_only_the_modules_users(repo: Path):
    assert _resign_main(repo) == {"main": list(_USERS)}


def test_no_qualified_evidence_falls_back_to_the_bare_word_and_says_so(repo: Path):
    _drop_users(repo)
    assert _resign_main(repo) == {"main" + ck.FALLBACK: ["docs_src/cli.md", "pkg/other.py"]}


def test_without_project_excludes_records_are_listed(repo: Path):
    (repo / "references/api-impact-reviews/CHECKLIST_EXCLUDES").write_text("")
    _commit(repo, "no excludes")
    _drop_users(repo)
    assert "references/runs/2026-11-11.csv" in _resign_main(repo)["main" + ck.FALLBACK]


def test_a_removed_module_lists_its_importers(repo: Path):
    (repo / "pkg/tool.py").unlink()
    _commit(repo, "remove tool")
    _, commits = api_impact.status(repo, "HEAD~1..HEAD")
    assert commits[-1].checklist["tool"] == list(_USERS)


@pytest.mark.parametrize("text,module,expect", [
    ("import pkg.tool\n", "pkg.tool", True),
    ("import os, pkg.tool as t\n", "pkg.tool", True),
    ("from pkg.tool import x\n", "pkg.tool", True),
    ("from pkg import (\n    a,\n    tool,\n)\n", "pkg.tool", True),
    ("import pkg.toolbox\n", "pkg.tool", False),
    ("from pkg import other\ntool = 1\n", "pkg.tool", False),
])
def test_imports(text: str, module: str, expect: bool):
    assert ck._imports(text, module) is expect


def test_dotted():
    assert ck.dotted("pkg/mod.py") == "pkg.mod" and ck.dotted("pkg/__init__.py") == "pkg"


def test_dotted_drops_a_src_root():
    assert ck.dotted("src/pkg/mod.py") == "pkg.mod" and ck.dotted("src.py") == "src"


def test_excludes_are_read_at_the_tip_not_the_working_tree(repo: Path):
    _drop_users(repo)
    (repo / "references/api-impact-reviews/CHECKLIST_EXCLUDES").write_text("")   # uncommitted: not in force
    _write(repo, {"pkg/tool.py": "def main(argv=None, *, strict=False):\n    return 0\n"})
    _git(repo, "add", "pkg/tool.py")
    _git(repo, "commit", "-q", "--no-verify", "-m", "re-sign main")
    _, commits = api_impact.status(repo, "HEAD~1..HEAD")
    assert "references/runs/2026-11-11.csv" not in commits[-1].checklist["main" + ck.FALLBACK]


def test_non_utf8_excludes_warn_and_are_ignored(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "references/api-impact-reviews/CHECKLIST_EXCLUDES").write_bytes(b"references/runs/*\n\xff\n")
    _commit(repo, "bad excludes")
    assert ck.read_excludes(repo, "HEAD", "references/api-impact-reviews") == []
    assert "not UTF-8" in capsys.readouterr().err


def test_qualified_hits_that_are_all_skipped_do_not_fall_back(repo: Path):
    for f in _USERS:
        (repo / f).unlink()
    _write(repo, {"tests/test_tool.py": "from pkg.tool import main\n"})
    _commit(repo, "only a test uses main")
    assert _resign_main(repo) == {"main": []}


def test_a_term_that_is_a_symbol_and_a_module_gets_both(repo: Path):
    _write(repo, {"pkg/tool.py": "def main(argv=None):\n    return 0\ndef tool():\n    pass\n",
                  "pkg/calls_tool.py": "from pkg.tool import tool\n"})
    _commit(repo, "add tool()")
    entries = {"tool": {"pkg/tool.py", "pkg/gone/tool.py"}}
    out = ck.build(repo, entries, symbols={"tool": ["pkg.tool"]}, modules={"tool": ["pkg.gone.tool"]},
                   skip=lambda _h: False, excludes=[], cap=50)
    assert "pkg/calls_tool.py" in out["tool"] and "pkg/uses_import.py" in out["tool"]


def test_relative_imports_count(repo: Path):
    for f in _USERS:
        (repo / f).unlink()
    _write(repo, {"pkg/rel_from.py": "from .tool import main\nmain()\n",
                  "pkg/sub/__init__.py": "",
                  "pkg/sub/rel_up.py": "from .. import tool\ntool.main()\n"})
    _commit(repo, "relative users")
    assert _resign_main(repo) == {"main": ["pkg/rel_from.py", "pkg/sub/rel_up.py"]}


@pytest.mark.parametrize("text,path,expect", [
    ("from .tool import x\n", "pkg/a.py", "from pkg.tool import x\n"),
    ("from . import tool\n", "pkg/__init__.py", "from pkg import tool\n"),
    ("from ..x import y\n", "src/pkg/sub/a.py", "from pkg.x import y\n"),
    ("from ...x import y\n", "pkg/a.py", "from ...x import y\n"),
])
def test_absolutize(text: str, path: str, expect: str):
    assert ck._absolutize(text, path) == expect


def test_type_guards():
    with pytest.raises(TypeError):
        ck.dotted(Path("pkg/mod.py"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ck.importers("repo", "pkg")  # type: ignore[arg-type]
