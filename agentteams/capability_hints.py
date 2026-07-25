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
    "  `python -m agentteams.research --help` (install with\n"
    "  `pip install agentteams[research]` if absent), then e.g.\n"
    "  `python -m agentteams.research search \"<query>\"` and\n"
    "  `python -m agentteams.research fetch \"<url>\"`.\n"
)

__all__ = ["RESEARCH_CAPABILITY_BULLET"]
