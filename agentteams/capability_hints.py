"""Operating-guidance text shared by more than one emitter.

Guidance that must reach an agent through *several* paths belongs here rather than in
any one emitter, because the failure this module exists to prevent is guidance living
in one emitter and silently missing from another.

Concretely (2026-07-24): ``agentteams/frameworks/goose.py`` documented the optional
``agentteams.research`` module in the hints it generates, while ``agentteams/bridge.py``
wrote its own entry files for *bridged* Goose teams and omitted it entirely. Measured on
the agentteams repo itself, ``grep -c "agentteams.research" AGENTS.md .goosehints``
returned ``0`` and ``0``, and a live failing turn's 20,258-char system prompt contained
zero occurrences of "research". The agent had a working general web-search capability
installed and no way to know it existed, so it guessed URLs, scraped a homepage, and
spent 54% of its context on navigation HTML without ever retrieving the answer.

The first attempt at a fix hand-copied the blurb into the second emitter and immediately
produced two divergent wordings — recreating the defect class it was meant to close. One
constant cannot drift from itself.
"""
from __future__ import annotations

#: The ONLY sanctioned install source for agentteams. The project is not published on PyPI, so
#: a bare ``pip install agentteams[...]`` installs whoever currently holds that name there
#: (slopsquatting — reported by baseAgent's @security as a HALT, 2026-09-29). Every emitted or
#: operator-facing install instruction is built from this constant;
#: ``tests/test_install_source_guard.py`` fails on any bare PyPI form.
AGENTTEAMS_GIT_SOURCE = "git+https://github.com/jlcatonjr/agentteams.git"


def release_tag(version: str) -> str:
    """Map a package version to this project's release tag (``1.0.0rc7`` -> ``v1.0.0-rc.7``).

    Args:
        version: A PEP 440 version string as in ``agentteams.__version__``.

    Returns:
        The git tag the project publishes for that version.
    """
    import re

    return "v" + re.sub(r"rc(\d+)$", r"-rc.\1", version)


def agentteams_install_command(extra: str) -> str:
    """Return the sanctioned pip command for agentteams + *extra*, pinned to this release's tag.

    Pinned (not HEAD) so an emitted instruction reproduces the version that generated it and
    never silently picks up an unreviewed commit (@security S-10, 2026-09-29).

    Args:
        extra: The optional-dependency extra (e.g. ``research``, ``browser``).

    Returns:
        ``pip install "agentteams[<extra>] @ git+https://github.com/jlcatonjr/agentteams.git@v<tag>"``.

    Raises:
        ValueError: If *extra* is not a plain extra name.
    """
    import re

    from agentteams import __version__

    if not isinstance(extra, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", extra):
        raise ValueError(f"invalid extra name {extra!r}")
    return f'pip install "agentteams[{extra}] @ {AGENTTEAMS_GIT_SOURCE}@{release_tag(__version__)}"'


#: Why an agent should reach for search before fetching, and how to verify the tool is
#: present. Consumed by ``agentteams/frameworks/goose.py`` (adapter-generated hints) and
#: ``agentteams/bridge.py`` (bridged-team entry files). Formatted as a Markdown list item
#: with a trailing newline so either emitter can concatenate it directly.
RESEARCH_CAPABILITY_BULLET = (
    "- **Search before you fetch.** No builtin Goose extension does web *search*\n"
    "  (query in, ranked results out) — `web_scrape` needs a URL you already know, so\n"
    "  guessing one lands you on a homepage and floods context with navigation HTML.\n"
    "  This project may ship `agentteams.research`, which does search, text-extracted\n"
    "  fetch, and (with the `[browser]` extra) JS rendering, through the ordinary\n"
    "  shell — no MCP wiring. Verify first, the same discipline as any CLI tool:\n"
    "  `python -m agentteams.research --help` (if absent, install from the project's git\n"
    f"  source: `{agentteams_install_command('research')}` —\n"
    "  never `pip install agentteams` from PyPI, where the name is not this project's), then e.g.\n"
    "  `python -m agentteams.research search \"<query>\"` and\n"
    "  `python -m agentteams.research fetch \"<url>\"`.\n"
)

__all__ = ["AGENTTEAMS_GIT_SOURCE", "RESEARCH_CAPABILITY_BULLET", "agentteams_install_command", "release_tag"]
