"""governance_targets.py — the single source of the governance/trust-ROOT path vocabulary.

Both the effect classifier (``effect_classifier.py``, Workstream D) and the management-directive
denylist (``management_directives.scope_is_allowed``) must agree on *what counts as a trust
root*. Keeping two hand-maintained lists caused exactly the drift the WS-D audit flagged
(Q1): six governance targets the classifier refused were silently accepted on the directive
path. This module is the one place that vocabulary lives, imported by both, so the two paths
can never disagree again.

A *governance / trust root* is a file whose contents decide **who may authorize what** — the
constitution and its mirrors, the approver/manager rosters, the signing switches and markers,
the ledgers, the integrity manifest, the authority-ordering reference, and (WS-B/R1) the
Ed25519 verify-key store. A write that targets one of these widens authority; it is never
ordinary project content, so it belongs on the elevated (asymmetric-signed) path and is
refused as a directive scope.

Stdlib-only; imports nothing from ``agentteams.cli`` (so neither importer can form a cycle).
"""

from __future__ import annotations

import re
import unicodedata

#: Path fragments that name a governance / trust root, matched as a substring of a
#: separator-canonicalized path (see :func:`canon_path`). Chosen so each stem catches its file
#: across spellings (``authorized-verify-keys``, ``authorized_verify_keys``,
#: ``authorizedVerifyKeys``) and locations (root ``CLAUDE.md`` and ``.claude/CLAUDE.md`` both
#: reduce to ``claude.md``). Fail-closed intent: over-matching a benign path only forces the
#: stricter elevated path (never a grant); under-matching a trust root would be fail-open.
GOVERNANCE_TARGET_STEMS: tuple[str, ...] = (
    "verify-key",                  # R1: the Ed25519 verify-key store (the new trust anchor)
    "security-approver",           # the decision-author roster (security-approvers.txt)
    "authorized-manager",          # the management-directive roster
    "enforcement-integrity",       # the integrity manifest
    "agent-privilege",             # the strict-signing switch
    "signing-governed",            # the governed-workspace assertion marker
    "management-directive",        # the directive ledger / machinery
    "security-decision",           # the decision ledger
    "claude-md",                   # the constitution (root CLAUDE.md and .claude/CLAUDE.md; dot-folded)
    "copilot-instructions",        # the constitution mirror (VS Code / Copilot)
    "instruction-authority",       # the authority-ordering reference
    "signing-key",                 # any signing-key material path
    "waiver",                      # a security waiver
)


def canon_path(path: str) -> str:
    """Return a separator-canonical form of ``path`` for governance-root matching.

    Folds the spellings an attacker (or an ordinary tool) might use for the same file into one:
    Unicode is NFKC-normalized (so compatibility/width variants collapse), a camelCase boundary
    becomes a hyphen, and backslashes, underscores, and any whitespace or dot each become a
    hyphen, with runs of hyphens collapsed; the whole string is lowercased. So
    ``authorizedVerifyKeys``, ``authorized_verify_keys``, ``authorized verify keys`` and
    ``references\\Authorized-Verify-Keys`` all reduce to a string containing
    ``authorized-verify-keys`` (matched by the ``verify-key`` stem — the stems below are written
    with hyphens so a dotted filename like ``CLAUDE.md`` folds to ``claude-md`` and still matches).

    Known residual (bounded, documented): this does NOT reverse separator *removal*
    (``authorizedverifykeys``), percent-encoding, or homoglyph substitution (a Cyrillic
    ``сlaude``). Those evade the stem match, but producing an authorizing row at all requires
    signed-write capability (the keyless threat model), so the residual is defense-in-depth, not a
    keyless bypass. See the WS-D re-review R2.

    Args:
        path: A raw target path or scope string.

    Returns:
        The canonicalized string (empty when ``path`` is empty/whitespace).
    """
    text = unicodedata.normalize("NFKC", (path or "")).strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text)  # camelCase → hyphen boundary
    text = text.lower()
    text = re.sub(r"[\\/\s_.%]+", "-", text)  # separators/whitespace/dots/percent → one hyphen
    return text.strip("-")


def target_is_governance_root(target: str) -> bool:
    """Return True iff ``target`` names a governance / trust root.

    Case- and separator-insensitive substring match of :data:`GOVERNANCE_TARGET_STEMS` against
    :func:`canon_path` of ``target``.

    Args:
        target: A write-target path (or scope string) to test.

    Returns:
        Whether the target is a governance/trust root.
    """
    normalized = canon_path(target)
    if not normalized:
        return False
    return any(stem in normalized for stem in GOVERNANCE_TARGET_STEMS)


__all__ = ["GOVERNANCE_TARGET_STEMS", "canon_path", "target_is_governance_root"]
