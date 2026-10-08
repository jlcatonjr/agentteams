"""Opt-in prompt-root protection for the Claude sandbox block (follow-up #8 phase 2, P4b).

A *prompt root* is a file or directory another harness reads as instructions (Copilot, Codex,
goose, Claude Code itself). Phase 1 (``agentteams/prompt_roots.py``) only DETECTS a change since the
last build. Phase 2 PREVENTS it from a Claude-sandboxed agent when the brief sets
``protect_prompt_roots: true`` (default off: it also blocks in-sandbox authoring of those agents,
including an in-sandbox ``agentteams --update``). The @security scope decision (2026-09-30) fixes
the set below; ``.github/workflows`` is deliberately excluded, and ``.agentteams/`` is protected
only when present at generation.

Two arms consume it (``_sandbox_emit.py``): unconditional ``permissions.deny`` ``Edit(...)`` rules
(the built-in tools) and present-only ``sandbox.filesystem.denyWrite`` entries (Bash; a missing
deny path stops bwrap initializing). The launcher's ``PROMPT_ROOTS_REL`` (``confine-run.sh
--protect-prompt-roots``) is locked to the same set by ``tests/test_protect_prompt_roots.py``.
``cli/write_root_policy.py`` uses :data:`ALL_PROMPT_ROOT_EDIT_RULES` for the RELAXATION notice.
Stdlib only; integrity-pinned (``agentteams/integrity.py`` ``ENFORCEMENT_MODULES``).
"""

from __future__ import annotations

import os
from typing import Any

#: Single-file prompt roots (project-relative).
PROMPT_ROOT_FILES: tuple[str, ...] = (
    ".github/copilot-instructions.md", "AGENTS.md", "AGENTS.override.md", ".goosehints", "CLAUDE.md", "CLAUDE.local.md",
    ".mcp.json",
)
#: Directory prompt roots (project-relative), protected whole.
PROMPT_ROOT_DIRS: tuple[str, ...] = (
    ".github/instructions", ".github/prompts", ".github/agents", ".codex", ".goose/recipes",
)
#: Protected in every arm only when present at generation (the @security scope decision).
PROMPT_ROOT_PRESENT_ONLY_DIRS: tuple[str, ...] = (".agentteams",)

#: Comment lines appended to the settings example when the brief opts in.
PROMPT_ROOT_COMMENT_LINES: list[str] = [
    "",
    "Prompt-root protection (protect_prompt_roots: true, follow-up #8) — permissions.deny also",
    "carries Edit(...) rules for the files other harnesses read as instructions:",
    ".github/copilot-instructions.md, .github/instructions/**, .github/prompts/**,",
    ".github/agents/**, AGENTS.md, .goosehints, .codex/**, .goose/recipes/**, CLAUDE.md,",
    "CLAUDE.local.md, .mcp.json, and .agentteams/** when present. denyWrite names only those",
    "that EXISTED at generation (a missing deny path stops bwrap): regenerate after creating",
    "one. .github/workflows is NOT covered. This also stops Claude-sandboxed agents (and an",
    "in-sandbox agentteams --update) authoring Copilot, Codex or goose agents and instructions:",
    "regenerate from outside the sandbox. Product-UNVERIFIED (mechanism-verified with bwrap).",
]


def prompt_roots_enabled(manifest: dict[str, Any] | None) -> bool:
    """Return True iff the manifest opts in to prompt-root protection.

    Args:
        manifest: The team manifest (``None`` allowed).

    Returns:
        True only for a JSON ``true`` ``protect_prompt_roots`` (a truthy string does not count).
    """
    return (manifest or {}).get("protect_prompt_roots") is True


def _present(project_root: str | None, rel: str, is_dir: bool) -> bool:
    """True iff ``rel`` exists under ``project_root`` with the right type and no symlink on its path."""
    if not project_root:
        return False
    path = project_root
    parts = rel.split("/")
    for part in parts[:-1]:
        path = os.path.join(path, part)
        if os.path.islink(path) or not os.path.isdir(path):
            return False
    path = os.path.join(path, parts[-1])
    if os.path.islink(path):
        return False  # a deny through a symlink would protect the target, not the path read
    return os.path.isdir(path) if is_dir else os.path.isfile(path)


def present_prompt_roots(project_root: str | None) -> tuple[str, ...]:
    """Return the prompt roots present under ``project_root`` (for ``denyWrite``), in table order.

    Args:
        project_root: The absolute project root; ``None`` (a library call) yields ``()``.

    Returns:
        Existing, non-symlinked roots only: a missing ``denyWrite`` path stops bwrap initializing.
    """
    out = [f for f in PROMPT_ROOT_FILES if _present(project_root, f, False)]
    out += [d for d in (*PROMPT_ROOT_DIRS, *PROMPT_ROOT_PRESENT_ONLY_DIRS) if _present(project_root, d, True)]
    return tuple(out)


def prompt_root_edit_rules(project_root: str | None = None) -> list[str]:
    """Return the ``permissions.deny`` ``Edit(...)`` rules for the prompt roots.

    Args:
        project_root: The absolute project root (decides the present-only roots).

    Returns:
        ``Edit(/<file>)`` per file root and ``Edit(/<dir>/**)`` per directory root; a
        present-only root only when it exists.
    """
    rules = [f"Edit(/{p})" for p in PROMPT_ROOT_FILES] + [f"Edit(/{d}/**)" for d in PROMPT_ROOT_DIRS]
    rules += [f"Edit(/{d}/**)" for d in PROMPT_ROOT_PRESENT_ONLY_DIRS if _present(project_root, d, True)]
    return rules


#: Every rule :func:`prompt_root_edit_rules` can emit (relaxation detection).
ALL_PROMPT_ROOT_EDIT_RULES: frozenset[str] = frozenset(
    [f"Edit(/{p})" for p in PROMPT_ROOT_FILES]
    + [f"Edit(/{d}/**)" for d in (*PROMPT_ROOT_DIRS, *PROMPT_ROOT_PRESENT_ONLY_DIRS)]
)
