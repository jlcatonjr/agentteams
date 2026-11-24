"""The Goose egress check for Ollama (@security SEC-2026-11-23-im-sealed-mail-B/C).

A Goose ``exclusive`` profile denies all network except one sanctioned loopback endpoint
(``goose_egress_proxy``). If that endpoint can reach Ollama, the profile is not network-isolated: Ollama's
``/api/pull`` of a model named ``host/ns/x`` connects to ``host`` (measured 2026-11-23). Until a loopback filter
exists (1.4), the render warns, on stderr and in the emitted ``sandbox.sb``.
"""

from __future__ import annotations

import os

#: Ollama's API port (as in the model_providers "ollama" preset). Its ``/api/pull`` of a model named ``host/ns/x``
#: connects to ``host`` (measured 2026-11-23), so an agent that can reach the API has a channel off the machine.
OLLAMA_DEFAULT_PORT = 11434


def _ollama_ports() -> set[int]:
    """11434, plus the port ``OLLAMA_HOST`` names (``host:port``, ``scheme://host:port/path``, ``[::1]:port``)."""
    netloc = os.environ.get("OLLAMA_HOST", "").strip().split("://", 1)[-1].split("/", 1)[0]
    tail = netloc.rpartition(":")[2]   # a bare "::1" has no port; a bracketed IPv6 host may carry one
    named = tail.isdigit() and (netloc.count(":") == 1 or netloc.startswith("[")) and 0 < int(tail) < 65536
    return {OLLAMA_DEFAULT_PORT, int(tail)} if named else {OLLAMA_DEFAULT_PORT}


def ollama_egress_warning(endpoint: str | None) -> str | None:
    """The render-time warning when an exclusive profile's sanctioned egress can reach Ollama (@security, 2026-11-23).

    Args:
        endpoint: :func:`_seatbelt_egress_rule`'s result (``localhost``, ``localhost:PORT``, ``*``, ``*:PORT``), or None.

    Returns:
        A one-line warning when the endpoint names an Ollama port or no port (every port); otherwise None.

    Raises:
        Nothing.
    """
    host, sep, port = (endpoint or "").partition(":")
    if not endpoint or (sep and not (port.isdigit() and int(port) in _ollama_ports())):   # "011434" counts
        return None
    where = "every host" if host == "*" else "loopback"
    reach = f"Ollama's port {int(port)} on {where}" if sep else f"every port on {where}, Ollama's included"
    return (f"goose_egress_proxy {endpoint!r} re-allows {reach}: Ollama's /api/pull connects to any registry host, so "
            "the exclusive profile is not network-isolated. Put a loopback filter that allows only the inference routes "
            "in front of Ollama and point goose_egress_proxy at it (see goose-privileges.md).")
