"""github_management.py — the constitution's "GitHub management" section, from the brief's ``github_management``.

Every generated team's instruction file carries a fenced ``github_management_rule`` section. Its body is the
``{GITHUB_MANAGEMENT_RULE}`` placeholder this module fills:

* ``github_management`` true (the default, operator request 2026-11-14): routine GitHub work through the ``gh`` and
  ``git`` CLIs is authorized, with the work allocated by role. Nothing is widened: no agent's ``tools:`` line changes
  (``@git-operations`` and the orchestrator already have ``execute``), no MCP server is activated (the ``github-read`` /
  ``github-write`` catalogue entries stay opt-in), and the constitutional gate's asks stay. The section says so, so an
  agent never reads it as consent to a gate prompt.
* ``github_management`` false: a one-line notice that every GitHub write goes to the operator first.

The section is the same text in every team that sets the flag the same way; it names no repository (the
constraint-relaxing-authorizations rule: ship the machinery, never a specific exception).

Stdlib only.
"""

from __future__ import annotations

from typing import Any

#: The placeholder that carries the section body.
PLACEHOLDER = "GITHUB_MANAGEMENT_RULE"

_ON = """GitHub management through the `gh` and `git` CLIs is authorized for this team by default (the brief's
`github_management`, default on). Routine GitHub work for this project's own repository needs no per-action
question.

- **Who does what.** `@git-operations` runs git and the `gh` writes: branches, pushes of work branches, pull
  requests, issue comments, labels and closing, re-running CI, and checking CI and Pages runs. `@pr-manager` owns
  the pull-request lifecycle and never merges; `@pr-notifier` proposes the notices. The orchestrator coordinates and
  may run read-only `gh` queries itself (`gh pr view`, `gh pr checks`, `gh run list`, `gh issue view`). Every other
  agent routes GitHub work through these roles. When the operator has activated the `github-read` / `github-write`
  MCP servers, the same split applies (`github-write` has no merge).
- **This project's repository only.** A write aimed at another repository (`-R`, another remote, a fork) is a
  cross-repository action and needs the operator.
- **Ask when it looks risky, and always for these.** An agent asks the operator before any GitHub action it judges
  risky or questionable. It always asks, whatever its judgment, for: anything C-5 lists (force-push, branch, tag or
  remote deletion, history rewrite, bulk or cross-repository changes); anything on the default branch, a `release/*`
  line or a tag; signing; moving a cross-repository pin.
- **The gate still decides.** This section never answers or pre-approves a constitutional-gate prompt and is not
  operator consent. The gate still asks on a direct `gh pr merge`, on `gh` publication commands and on the C-5
  classes; answer nothing on the operator's behalf, and never route around a prompt.
- **Merging.** A pull-request merge goes through the gate's prompt. The only merge without that prompt is
  `agentteams --merge-pr` under the integration-branch merge authorization below, run by `@git-operations`.
- **Tokens.** Use the credentials `gh` already holds. Never print, paste or log a token."""

_OFF = """GitHub management by agents is not authorized by default in this team (the brief sets `github_management`
to false): every GitHub write (a push, a pull request, an issue or a CI action) goes to the operator first.
Read-only queries need no question."""


def enabled(description: dict[str, Any]) -> bool:
    """Whether the brief authorizes GitHub management (default True).

    Args:
        description: The project brief.

    Returns:
        False only when the brief sets ``github_management`` to ``false``.

    Raises:
        ValueError: ``github_management`` is set to something other than a boolean (a string ``"false"`` would
            otherwise read as on).
    """
    value = description.get("github_management", True)
    if not isinstance(value, bool):
        raise ValueError(f"github_management must be true or false, not {value!r}")
    return value


def section_body(description: dict[str, Any]) -> str:
    """Return the ``{GITHUB_MANAGEMENT_RULE}`` text for this brief.

    Args:
        description: The project brief.

    Returns:
        The authorized-by-default rules, or the off notice.

    Raises:
        ValueError: As :func:`enabled`.
    """
    return _ON if enabled(description) else _OFF


def placeholders(description: dict[str, Any]) -> dict[str, str]:
    """Return this module's placeholder map for *description*.

    Args:
        description: The project brief.

    Returns:
        ``{PLACEHOLDER: section_body(description)}``.

    Raises:
        ValueError: As :func:`enabled`.
    """
    return {PLACEHOLDER: section_body(description)}


__all__ = ["PLACEHOLDER", "enabled", "placeholders", "section_body"]
