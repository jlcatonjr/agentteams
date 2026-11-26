"""One security policy, at the repository root, and it is the one GitHub shows.

GitHub looks for a policy in ``.github/``, then the root, then ``docs/``, so a second copy in ``.github/`` hides the
root policy that every other document links to. These tests keep exactly one policy file, keep the impossible
"private issue" fallback out of it, keep it under CODEOWNERS review, and keep tracked docs from linking to a
policy path that does not exist.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SHADOWING_DIRS = (".github", "docs")
_LINK_TO_SHADOW = re.compile(r"(?:\]\(|github\.com/\S*?/)\S*?(?:\.github|docs)/SECURITY\.md", re.I)
_TEXT_SUFFIXES = {".md", ".yml", ".yaml", ".html", ".txt", ".toml", ".json"}


def _shadowing_policies(root: Path) -> list[str]:
    """Policy files GitHub would show instead of the root one."""
    return [f"{d}/{p.name}" for d in _SHADOWING_DIRS if (root / d).is_dir()
            for p in (root / d).iterdir() if p.name.lower() == "security.md"]


def _policy_problems(root: Path) -> list[str]:
    """What is wrong with the root policy's reporting guidance."""
    path = root / "SECURITY.md"
    if not path.is_file():
        return ["no root SECURITY.md"]
    text = path.read_text(encoding="utf-8")
    problems = []
    if "security/advisories/new" not in text:
        problems.append("does not name the private reporting link")
    if "private coordination issue" in text.lower():
        problems.append("offers a private issue, which GitHub does not have")
    if "public issue that says only that you have a security report" not in text:
        problems.append("has no fallback for a reporter without the Security tab")
    return problems


def _codeowners_covers_root_policy(root: Path) -> bool:
    lines = (root / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    owners = {line.split()[0]: line for line in lines if line.strip() and not line.lstrip().startswith("#")}
    return "/SECURITY.md" in owners and "/.github/SECURITY.md" not in owners


def _tracked_text_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
        paths = [root / p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in root.rglob("*") if ".git" not in p.parts]
    return [p for p in paths if p.suffix.lower() in _TEXT_SUFFIXES and p.is_file()]


def _links_to_shadow(root: Path) -> list[str]:
    hits = []
    for path in _tracked_text_files(root):
        if _LINK_TO_SHADOW.search(path.read_text(encoding="utf-8", errors="replace")):
            hits.append(str(path.relative_to(root)))
    return hits


def _old_layout(tmp_path: Path) -> Path:
    """The two-policy layout these tests replace: the negative control for each check."""
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "SECURITY.md").write_text(
        "If private advisory flow is unavailable, open a private coordination issue with maintainers.\n")
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @owner\n/.github/SECURITY.md @owner\n")
    (tmp_path / "SECURITY.md").write_text("Report at security/advisories/new.\n")
    (tmp_path / "guide.md").write_text("See [the policy](.github/SECURITY.md).\n")
    return tmp_path


def test_only_the_root_security_policy_exists(tmp_path: Path) -> None:
    assert _shadowing_policies(REPO) == []
    assert (REPO / "SECURITY.md").is_file()
    assert _shadowing_policies(_old_layout(tmp_path)) == [".github/SECURITY.md"]


def test_the_root_policy_has_no_private_issue_fallback(tmp_path: Path) -> None:
    assert _policy_problems(REPO) == []
    old = _old_layout(tmp_path)
    assert _policy_problems(old) == ["has no fallback for a reporter without the Security tab"]
    (old / "SECURITY.md").write_text("security/advisories/new\nOpen a private coordination issue.\n")
    assert "offers a private issue, which GitHub does not have" in _policy_problems(old)


def test_codeowners_protects_the_root_policy(tmp_path: Path) -> None:
    assert _codeowners_covers_root_policy(REPO)
    assert not _codeowners_covers_root_policy(_old_layout(tmp_path))


def test_no_tracked_doc_links_to_a_shadowing_policy(tmp_path: Path) -> None:
    assert _links_to_shadow(REPO) == []
    assert _links_to_shadow(_old_layout(tmp_path)) == ["guide.md"]
