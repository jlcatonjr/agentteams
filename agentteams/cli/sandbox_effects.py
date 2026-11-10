"""sandbox_effects.py — the six sandbox-relaxation effect kinds and their token spelling (Rule 15, D7).

Turning a sandbox off, widening what a WASM guest may reach, or turning off the per-call MCP approval prompt is a
constraint-relaxing effect: in a governed
workspace it needs the operator's Ed25519-signed decision row, never a self-declared flag. This small module is the
one vocabulary the effect classifier, the management-directive denylist and the render-time gate
(:mod:`agentteams.cli.sandbox_relaxation_gate`) share, so they cannot drift (modelled on
:mod:`agentteams.cli.frozen_surfaces`).

The kinds:

* ``sandbox-os-off`` — the OS boundary the profile (or an earlier render) would emit is suppressed;
* ``sandbox-wasm-off`` — WASM confinement that was on is turned off (also the projected model-substrate switch);
* ``wasm-grant-widen`` — a per-agent WASM grant gains read paths or a scratch dir beyond compute-only;
* ``native-exec-keep`` — a WASM-eligible agent keeps native execution while WASM is on;
* ``wasm-to-os-switch`` — a runner ``confined_programs`` entry moves from ``sandbox: wasm`` back to ``os``;
* ``mcp-approval-auto`` — the shared ``agentteams_wasm`` MCP table stops prompting per tool call (the opt-in
  ``codex:wasm-auto-approve`` host feature sets its ``default_tools_approval_mode`` to ``auto``; WP-C2).

Gate action ids are ``<kind>:<qualifier>`` (a framework id, ``model-substrate``, or an agent slug); the
``mcp-approval-auto`` id is ``mcp-approval-auto:codex``. Grant tokens are
``wasm:<agent>:read:<rel>``, ``wasm:<agent>:scratch`` and ``native-exec:<agent>``.

Stdlib-only; imports only the leaf :mod:`agentteams.sandbox_tokens` (the token spelling, re-exported here), so
every importer stays cycle-free.
"""

from __future__ import annotations

import re

from agentteams.sandbox_tokens import checked_slug as _slug
from agentteams.sandbox_tokens import native_exec_token, read_token, scratch_token, valid_rel

SANDBOX_OS_OFF = "sandbox-os-off"
SANDBOX_WASM_OFF = "sandbox-wasm-off"
WASM_GRANT_WIDEN = "wasm-grant-widen"
NATIVE_EXEC_KEEP = "native-exec-keep"
WASM_TO_OS_SWITCH = "wasm-to-os-switch"
MCP_APPROVAL_AUTO = "mcp-approval-auto"

#: The six relaxation kinds, in a fixed order (schema enum, gate action families).
SANDBOX_RELAXATION_KINDS: tuple[str, ...] = (
    SANDBOX_OS_OFF, SANDBOX_WASM_OFF, WASM_GRANT_WIDEN, NATIVE_EXEC_KEEP, WASM_TO_OS_SWITCH, MCP_APPROVAL_AUTO)

#: The qualifier of the projected model-substrate switch (P20): ``sandbox-wasm-off:model-substrate``.
MODEL_SUBSTRATE_QUALIFIER = "model-substrate"

_STEMS: tuple[str, ...] = (
    *SANDBOX_RELAXATION_KINDS,
    "sandbox-off", "unsandbox", "disable-sandbox", "no-sandbox", "os-sandbox-off", "wasm-sandbox-off",
    "keep-native-exec", "native-exec", "wasm-to-os",
    # mcp-approval-auto synonyms (the codex:wasm-auto-approve host feature): word-start prefixes, so every
    # inflection (auto-approve/-approval/-approving, autoapprove) matches. Not "tools-approval-mode": the
    # setting's name alone also appears in text about the "prompt" default.
    "auto-approv", "autoapprov",
)

#: Word-start stems that name a sandbox relaxation in free text (scope, action, decision, task_scope), in the hyphen,
#: underscore and space spellings. The directive denylist matches them raw (lowercased, word start), so each spelling
#: is listed; :func:`names_sandbox_relaxation` also folds the text first.
SANDBOX_RELAXATION_TOKENS: tuple[str, ...] = tuple(dict.fromkeys(
    variant for stem in _STEMS for variant in (stem, stem.replace("-", "_"), stem.replace("-", " "))))

_RELAXATION_RE = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(t) for t in _STEMS) + ")")
_FOLD_RE = re.compile(r"[\s_]+")


def _fold(text: str) -> str:
    """Lowercase ``text`` and fold underscores and whitespace runs to one hyphen."""
    return _FOLD_RE.sub("-", (text or "").lower())


def names_sandbox_relaxation(text: str) -> bool:
    """Return True when ``text`` names a sandbox relaxation (any spelling, at a word start).

    Args:
        text: A scope, action, decision or task-scope text.

    Returns:
        Whether the text names one of the relaxation kinds or a synonym (``unsandboxed`` matches, ``xunsandbox``
        does not).
    """
    return _RELAXATION_RE.search(_fold(text)) is not None


def action_id(kind: str, qualifier: str) -> str:
    """Return the gate action id ``<kind>:<qualifier>``.

    Args:
        kind: One of :data:`SANDBOX_RELAXATION_KINDS`.
        qualifier: A framework id, :data:`MODEL_SUBSTRATE_QUALIFIER`, or an agent slug.

    Returns:
        The action id.

    Raises:
        ValueError: ``kind`` is unknown or ``qualifier`` is not a slug.
    """
    if kind not in SANDBOX_RELAXATION_KINDS:
        raise ValueError(f"unknown sandbox relaxation kind {kind!r}")
    return f"{kind}:{_slug(qualifier, 'qualifier')}"


def relaxation_kind_of(action: str) -> str | None:
    """Return the relaxation kind an action id belongs to, or None.

    Args:
        action: A gate action id such as ``sandbox-os-off:claude`` (a bare kind is accepted too).

    Returns:
        The kind, or None when the id is not a sandbox relaxation.
    """
    head = (action or "").strip().lower().split(":", 1)[0]
    return head if head in SANDBOX_RELAXATION_KINDS else None


def is_relaxation_token(token: str) -> bool:
    """Return True when a capability token is a sandbox-relaxation grant (``wasm:`` or ``native-exec:``).

    Args:
        token: An ``effect_grants`` entry.

    Returns:
        Whether it names a WASM grant widening or a native-exec keep.
    """
    return (token or "").strip().lower().startswith(("wasm:", "native-exec:"))


__all__ = [
    "MCP_APPROVAL_AUTO",
    "MODEL_SUBSTRATE_QUALIFIER",
    "NATIVE_EXEC_KEEP",
    "SANDBOX_OS_OFF",
    "SANDBOX_RELAXATION_KINDS",
    "SANDBOX_RELAXATION_TOKENS",
    "SANDBOX_WASM_OFF",
    "WASM_GRANT_WIDEN",
    "WASM_TO_OS_SWITCH",
    "action_id",
    "is_relaxation_token",
    "names_sandbox_relaxation",
    "native_exec_token",
    "read_token",
    "relaxation_kind_of",
    "scratch_token",
    "valid_rel",
]
