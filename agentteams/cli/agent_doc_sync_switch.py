"""agent_doc_sync_switch.py — CLI flags + dispatch for ``--sync-agent-docs``.

Own-module pattern (``sync_switch.py``, ``goose_switch.py``): ``cli/parser.py`` gains one
registrar call and ``cli/app.py`` one dispatch. The mode is standalone and is dispatched before
anything reads a brief, pin or project config — the sync must not take direction from any file an
agent can edit (see :mod:`agentteams.agent_doc_sync`).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def add_agent_doc_sync_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``--sync-agent-docs``, ``--apply``, ``--include-claude`` and ``--restore-removed``.

    Args:
        parser: The main CLI argument parser to extend in place.

    Returns:
        None. The parser gains an "agent-doc sync" group.
    """
    group = parser.add_argument_group("agent-doc sync (learned blocks)")
    group.add_argument(
        "--sync-agent-docs",
        action="store_true",
        dest="sync_agent_docs",
        default=False,
        help=(
            "Propagate each agent's <!-- AGENTTEAMS-LEARNED --> block between its copies in "
            ".github/agents, .claude/agents, .goose/recipes and .codex/agents of --project. Report-only unless "
            "--apply. Never touches front matter, template fences or recipe keys; reads no brief."
        ),
    )
    group.add_argument(
        "--apply",
        action="store_true",
        dest="sync_apply",
        default=False,
        help="With --sync-agent-docs: write .github/agents, .goose/recipes and .codex/agents targets "
             "(.claude/agents targets are staged for review unless --include-claude). With "
             "--branch-cleanup / --branch-post-merge: execute instead of a dry run.",
    )
    group.add_argument(
        "--include-claude",
        action="store_true",
        dest="sync_include_claude",
        default=False,
        help="With --sync-agent-docs --apply: also write .claude/agents targets after showing "
             "each diff and asking y/N. Refused unless stdin/stdout are a terminal and CLAUDECODE "
             "is unset (operator review; the unattended unit never passes this).",
    )
    group.add_argument(
        "--restore-removed",
        action="store_true",
        dest="sync_restore_removed",
        default=False,
        help="With --sync-agent-docs --apply: re-insert the agreed learned block into copies an "
             "agent removed it from (they are otherwise excluded, with a warning every run).",
    )


def validate_agent_doc_sync_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse incoherent ``--sync-agent-docs`` flag combinations (argparse ``error`` exits 2).

    Args:
        parser: The main parser (for ``parser.error``).
        args: The parsed namespace.

    Returns:
        None when the combination is valid.
    """
    on = bool(getattr(args, "sync_agent_docs", False))
    branch_execute = bool(getattr(args, "branch_cleanup", None)
                          or getattr(args, "branch_post_merge", None))
    if getattr(args, "sync_apply", False) and not (on or branch_execute):
        parser.error("--apply requires --sync-agent-docs, --branch-cleanup or --branch-post-merge")
    if getattr(args, "sync_include_claude", False) and not (on and args.sync_apply):
        parser.error("--include-claude requires --sync-agent-docs --apply")
    if getattr(args, "sync_restore_removed", False) and not (on and args.sync_apply):
        parser.error("--restore-removed requires --sync-agent-docs --apply")
    if not on:
        return
    if not getattr(args, "project", None):
        parser.error("--sync-agent-docs requires --project <dir>")
    clashes = [flag for flag, set_ in (
        ("--description", getattr(args, "description", None) is not None),
        ("--self", bool(getattr(args, "self_update", False))),
        ("--update", bool(getattr(args, "update", False))),
        ("--output", bool(getattr(args, "output", None))),
        ("--fleet", getattr(args, "fleet", None) is not None),
    ) if set_]
    if clashes:
        parser.error(f"--sync-agent-docs is standalone; it cannot be combined with {', '.join(clashes)}")


def run_agent_doc_sync_cli(args: argparse.Namespace) -> int:
    """Run the sync for a validated namespace.

    Args:
        args: Parsed CLI namespace with ``sync_agent_docs`` set.

    Returns:
        The process exit code from :func:`agentteams.agent_doc_sync.run_sync_agent_docs`.
    """
    from agentteams.agent_doc_sync import run_sync_agent_docs

    return run_sync_agent_docs(Path(args.project), apply=bool(args.sync_apply),
                               include_claude=bool(args.sync_include_claude),
                               restore_removed=bool(args.sync_restore_removed))
