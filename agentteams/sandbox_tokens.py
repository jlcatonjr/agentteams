"""sandbox_tokens.py — the WASM grant-token spelling (``wasm:<agent>:read:<rel>``, ``wasm:<agent>:scratch``,
``native-exec:<agent>``) and the guest read-path grammar (Rule 15, D7).

A leaf module below both layers: :mod:`agentteams.sandbox_state` (the one grant builder) and
:mod:`agentteams.wasm_agent.team` (the ``--framework wasm`` loader) build tokens here without importing the CLI
package, and :mod:`agentteams.cli.sandbox_effects` re-exports them as part of the relaxation vocabulary it shares with
the effect classifier and the render-time gate (so the spelling cannot drift).

Stdlib-only; imports nothing from ``agentteams``.
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def checked_slug(value: str, what: str) -> str:
    """Return ``value`` when it is a slug (``^[a-z0-9][a-z0-9-]*$``).

    Args:
        value: The candidate slug.
        what: What it names, for the message.

    Returns:
        ``value`` unchanged.

    Raises:
        ValueError: ``value`` is not a slug.
    """
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise ValueError(f"{what} {value!r} is not a slug (^[a-z0-9][a-z0-9-]*$)")
    return value


def valid_rel(rel: str) -> str:
    """Return ``rel`` when it is a project-relative guest path, else raise.

    The guest-name segment grammar (P02): segments of ``[A-Za-z0-9._-]+`` joined by ``/``; no ``.`` or ``..``
    segment, no leading or trailing ``/``.

    Args:
        rel: The project-relative path a read grant names.

    Returns:
        ``rel`` unchanged.

    Raises:
        ValueError: ``rel`` is empty, absolute, or has a segment outside the grammar.
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError("a read path must be a non-empty project-relative path")
    for segment in rel.split("/"):
        if segment in {"", ".", ".."} or not _SEGMENT_RE.match(segment):
            raise ValueError(f"read path {rel!r} has a segment outside the guest-name grammar ([A-Za-z0-9._-]+, "
                             "no '.' or '..', no empty segment)")
    return rel


def read_token(agent: str, rel: str) -> str:
    """Return the grant token for a read-only ``/project/<rel>`` mount: ``wasm:<agent>:read:<rel>``.

    Args:
        agent: The agent slug.
        rel: The project-relative path (see :func:`valid_rel`).

    Returns:
        The token.

    Raises:
        ValueError: A bad slug or path.
    """
    return f"wasm:{checked_slug(agent, 'agent')}:read:{valid_rel(rel)}"


def scratch_token(agent: str) -> str:
    """Return the grant token for an agent's private writable scratch dir: ``wasm:<agent>:scratch``.

    Args:
        agent: The agent slug.

    Returns:
        The token.

    Raises:
        ValueError: A bad slug.
    """
    return f"wasm:{checked_slug(agent, 'agent')}:scratch"


def native_exec_token(agent: str) -> str:
    """Return the token for an agent that keeps native execution while WASM is on: ``native-exec:<agent>``.

    Args:
        agent: The agent slug.

    Returns:
        The token.

    Raises:
        ValueError: A bad slug.
    """
    return f"native-exec:{checked_slug(agent, 'agent')}"


__all__ = ["checked_slug", "native_exec_token", "read_token", "scratch_token", "valid_rel"]
