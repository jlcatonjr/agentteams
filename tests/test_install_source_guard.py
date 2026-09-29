"""agentteams is not published on PyPI, so a bare ``pip install agentteams[...]`` installs whoever
holds that name there (slopsquatting — baseAgent handoff item 8, reported as a @security HALT,
2026-09-29). Every emitted or operator-facing install instruction must use the git source
(``agentteams.capability_hints.AGENTTEAMS_GIT_SOURCE``).

CHANGELOG.md is history and is exempt; a line that says "never" is the warning itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.capability_hints import (
    AGENTTEAMS_GIT_SOURCE,
    RESEARCH_CAPABILITY_BULLET,
    agentteams_install_command,
)

_ROOT = Path(__file__).resolve().parents[1]
_GIT = re.escape(AGENTTEAMS_GIT_SOURCE)
# Any installer spelling (pip, pip3, python -m pip, uv pip, pipx), optional flags, then the
# package with or without an extra list — unless it is immediately the sanctioned git source.
_BARE = re.compile(
    r"""(?:\bpip3?|python3?\s+-m\s+pip|\buv\s+pip|\bpipx)\s+install\s+(?:-{1,2}[\w-]+\s+)*"""
    r"""["']?agentteams(?:\[[^\]]*\])?(?!\S*\s*@\s*""" + _GIT + r")"""
)
# A bare extra named as an install target ("install `agentteams[signing]`").
_BARE_EXTRA = re.compile(r"""install\s+[`"']?agentteams\[[^\]]*\][`"']?(?!\s*@)""")
_SCOPES = (
    ("agentteams", ("*.py", "*.md", "*.json", "*.yaml", "*.toml", "*.sh")),
    ("docs_src", ("*.md",)),
    ("examples", ("*.md", "*.json", "*.yaml")),
    ("scripts", ("*.py", "*.sh", "*.md")),
    (".github/workflows", ("*.yml", "*.yaml")),
)


def _is_warning(line: str) -> bool:
    """The PyPI warning itself ("never … PyPI"), not an instruction."""
    low = line.lower()
    return "never" in low and "pypi" in low


def _offenders() -> list[str]:
    files = [_ROOT / "README.md", _ROOT / "SECURITY.md", _ROOT / "agentteams.1"]
    for base, patterns in _SCOPES:
        for pattern in patterns:
            files.extend((_ROOT / base).rglob(pattern))
    hits: list[str] = []
    for path in files:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path == _ROOT / "agentteams" / "capability_hints.py":
            continue  # the single source that BUILDS the sanctioned command (checked below)
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if (_BARE.search(line) or _BARE_EXTRA.search(line)) and not _is_warning(line):
                hits.append(f"{path.relative_to(_ROOT)}:{n}: {line.strip()[:120]}")
    return hits


def test_no_bare_pypi_install_instruction() -> None:
    assert _offenders() == []


def test_regex_catches_the_bare_forms() -> None:
    for bad in ("pip install agentteams[research]", 'pip install "agentteams[browser]"',
                "pip install agentteams", "pip3 install agentteams", "python -m pip install -U agentteams",
                "uv pip install agentteams[research, browser]", "pipx install agentteams",
                'pip install "agentteams[research] @ git+https://evil.example/agentteams.git"'):
        assert _BARE.search(bad), bad
    assert _BARE_EXTRA.search("(install `agentteams[signing]`)")
    assert not _BARE.search(agentteams_install_command("research"))
    assert not _BARE_EXTRA.search(agentteams_install_command("signing"))


def test_emitted_research_hint_uses_git_source() -> None:
    assert AGENTTEAMS_GIT_SOURCE in RESEARCH_CAPABILITY_BULLET
    cmd = agentteams_install_command("research")
    assert cmd.startswith('pip install "agentteams[research] @ git+https://github.com/jlcatonjr/agentteams.git@v')


def test_release_tag_mapping() -> None:
    from agentteams.capability_hints import release_tag

    assert release_tag("1.0.0rc7") == "v1.0.0-rc.7"
    assert release_tag("1.2.0") == "v1.2.0"
