"""`agentteams/templates/` is the only template root the pipeline may read.

A stale top-level `/templates/` existed alongside the canonical `agentteams/templates/` — an
earlier revision (9 constitutional rules against the canonical 28, Workflows 1–8) with zero
readers anywhere in the pipeline. It was gitignored and untracked, so it never reached a clone,
but on the machine that had it a contributor could edit it for an hour and observe no effect on
anything.

Deleting the stray fixes one machine. This test is the part that lasts: it pins that every
`TEMPLATES_DIR` in the codebase resolves under `agentteams/templates`, so a second root cannot
quietly become load-bearing later.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = _REPO_ROOT / "agentteams" / "templates"

_SKIP_DIRS = {".git", ".venv", ".venv-ci", "build", "dist", "_site", "node_modules",
              "__pycache__", ".agentteams-backups", "docs", "tmp"}

_TEMPLATES_DIR_ASSIGN = re.compile(r"^\s*TEMPLATES_DIR\s*=\s*(.+)$", re.MULTILINE)

#: The hazardous shape: binding TEMPLATES_DIR to the *repo root* + "templates", i.e. the stray
#: directory. Checked by pattern rather than by "does the text mention agentteams", because the
#: canonical root is legitimately written several ways —
#: ``_SCRIPT_DIR / "agentteams" / "templates"``, ``REPO_ROOT / "agentteams/templates"``, and
#: ``Path(__file__).resolve().parents[1] / "templates"`` from inside ``agentteams/cli/`` all
#: resolve to the same place. Flagging those would make the guard noise, and a noisy guard gets
#: deleted.
_REPO_ROOT_NAMES = ("REPO_ROOT", "_REPO_ROOT", "_SCRIPT_DIR", "PROJECT_ROOT")
_STRAY_ASSIGN = re.compile(
    r"(?:" + "|".join(_REPO_ROOT_NAMES) + r")\s*/\s*['\"]templates['\"]\s*$"
)


def _python_sources() -> list[Path]:
    return [
        p for p in _REPO_ROOT.rglob("*.py")
        if not _SKIP_DIRS & set(p.relative_to(_REPO_ROOT).parts)
    ]


def test_the_canonical_template_root_exists_and_is_populated():
    assert _CANONICAL.is_dir()
    assert len(list(_CANONICAL.rglob("*.template.md"))) >= 40


def test_no_second_template_root_at_the_repo_top_level():
    """The stray directory must not come back.

    It is gitignored, so nothing would flag its reappearance — and its whole hazard was being
    invisible: edits to it are silently inert.
    """
    stray = _REPO_ROOT / "templates"
    assert not stray.exists(), (
        "a top-level templates/ directory has reappeared. The canonical root is "
        "agentteams/templates/; a second copy is unread by the pipeline, so edits to it are "
        "silently lost. Delete it, or if it is genuinely needed, wire it in and update this test."
    )


def test_no_module_binds_templates_dir_to_the_repo_root():
    """The durable guard: nothing may bind TEMPLATES_DIR to the top-level stray."""
    offenders = []
    for path in _python_sources():
        for match in _TEMPLATES_DIR_ASSIGN.finditer(path.read_text(encoding="utf-8")):
            expr = match.group(1).strip()
            if _STRAY_ASSIGN.search(expr):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}: TEMPLATES_DIR = {expr}")
    assert not offenders, (
        "TEMPLATES_DIR is bound to the repo-root templates/ rather than agentteams/templates/:\n  "
        + "\n  ".join(offenders)
    )


def test_the_stray_pattern_is_actually_detectable():
    """A guard that cannot fire is decoration — prove the predicate matches the real hazard."""
    assert _STRAY_ASSIGN.search('REPO_ROOT / "templates"')
    assert _STRAY_ASSIGN.search("_SCRIPT_DIR / 'templates'")
    # ...and does not fire on the several legitimate spellings of the canonical root.
    for benign in (
        '_SCRIPT_DIR / "agentteams" / "templates"',
        'REPO_ROOT / "agentteams/templates"',
        'Path(__file__).resolve().parents[1] / "templates"',
    ):
        assert not _STRAY_ASSIGN.search(benign), benign


def test_templates_dir_resolves_to_the_canonical_root_at_runtime():
    """Static text is not proof; resolve the real values the pipeline uses."""
    import build_team
    from agentteams.cli import generate

    for name, value in (("build_team", build_team.TEMPLATES_DIR),
                        ("cli.generate", generate.TEMPLATES_DIR)):
        assert Path(value).resolve() == _CANONICAL.resolve(), f"{name} -> {value}"


@pytest.mark.parametrize("doc", ["FENCE-CONVENTIONS.md", "AUTHORING-GUIDE.md"])
def test_template_authoring_docs_live_with_the_templates_they_describe(doc):
    """Both docs also existed in the stray copy; the canonical pair is the one that must exist."""
    assert (_CANONICAL / doc).is_file()
