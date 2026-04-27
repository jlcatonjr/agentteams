"""
man.py — Generate a POSIX groff man-page from the agentteams argparse parser.

The generated man-page source is committed to the repository root as
``agentteams.1`` and installed to ``share/man/man1/`` on pip install.

Usage (regenerate the committed man-page)::

    python -m agentteams.man > agentteams.1

Preview locally::

    man ./agentteams.1
"""

from __future__ import annotations

import argparse
import textwrap
from datetime import date


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_man_page(parser: argparse.ArgumentParser) -> str:
    """Generate a groff man-page source document from an argparse parser.

    Produces sections: NAME, SYNOPSIS, DESCRIPTION, OPTIONS, EXIT STATUS,
    and EXAMPLES.  Derives all content from the parser's prog, description,
    and registered arguments — no duplication required.

    Args:
        parser: Configured argparse.ArgumentParser instance.

    Returns:
        Complete groff man-page source as a plain string (write to ``<name>.N``).
    """
    prog = parser.prog
    description = parser.description or ""
    today = date.today().strftime("%B %Y")

    lines: list[str] = []

    # .TH — title header
    lines.append(f'.TH {prog.upper()} 1 "{today}" "{prog}" "User Commands"')

    # NAME
    lines.append(".SH NAME")
    lines.append(f"{prog} \\- {description}")

    # SYNOPSIS
    lines.append(".SH SYNOPSIS")
    synopsis = _build_synopsis(parser)
    lines.append(f".B {synopsis}")

    # DESCRIPTION
    lines.append(".SH DESCRIPTION")
    desc_text = _get_epilog_description(parser)
    for para in desc_text:
        lines.append(".PP")
        lines.append(_groff_escape(para))

    # OPTIONS
    lines.append(".SH OPTIONS")
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        lines.extend(_format_option(action))

    # EXIT STATUS
    lines.append(".SH EXIT STATUS")
    lines.append(".TP")
    lines.append(".B 0")
    lines.append("Success.")
    lines.append(".TP")
    lines.append(".B 1")
    lines.append(
        "Error: validation failure, file not found, "
        "drift detected (with \\fB--check\\fR), "
        "or security issues (with \\fB--scan-security\\fR)."
    )

    # EXAMPLES
    lines.append(".SH EXAMPLES")
    for example in _get_examples():
        lines.append(".PP")
        lines.append(_groff_escape(example["desc"]) + ":")
        lines.append(".PP")
        lines.append(".RS 4")
        lines.append(".nf")
        lines.append(_groff_escape(example["cmd"]))
        lines.append(".fi")
        lines.append(".RE")

    # SEE ALSO
    lines.append(".SH SEE ALSO")
    lines.append(
        "Full documentation: "
        ".UR https://jlcatonjr.github.io/agentteams/\n"
        ".UE\n"
        ".PP\n"
        "Source repository: "
        ".UR https://github.com/jlcatonjr/agentteams\n"
        ".UE"
    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_synopsis(parser: argparse.ArgumentParser) -> str:
    """Build a concise synopsis line from the parser's optionals."""
    prog = parser.prog
    # Collect short forms of every option
    parts = [prog]
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._VersionAction):
            parts.append("[--version]")
            continue
        if isinstance(action, argparse._StoreTrueAction):
            opt = action.option_strings[0] if action.option_strings else ""
            parts.append(f"[{opt}]")
        elif action.option_strings:
            opt = action.option_strings[0]
            metavar = action.metavar or action.dest.upper()
            if action.required:
                parts.append(f"{opt} {metavar}")
            else:
                parts.append(f"[{opt} {metavar}]")
    return " ".join(parts)


def _format_option(action: argparse.Action) -> list[str]:
    """Format a single argparse action as groff .TP block."""
    lines: list[str] = []
    if not action.option_strings:
        return lines

    # Build the option header (e.g.  --description PATH, -d PATH)
    opts = ", ".join(action.option_strings)
    if action.metavar:
        opts += f" {action.metavar}"
    elif hasattr(action, "choices") and action.choices:
        opts += f" {{{','.join(str(c) for c in action.choices)}}}"

    lines.append(".TP")
    lines.append(f".B {_groff_escape(opts)}")
    if action.help:
        help_text = action.help.replace("%(prog)s", action.option_strings[0])
        # Avoid double-default: only append if the default isn't already in the help text
        default = action.default
        default_str = str(default) if default not in (None, False, argparse.SUPPRESS) else ""
        if default_str and default_str not in help_text:
            help_text += f" (default: {default_str})"
        # Escape literal brace tokens so groff doesn't interpret them
        help_text = help_text.replace("{", "\\(lC").replace("}", "\\(rC")
        lines.append(_groff_escape(help_text))
    return lines


def _get_epilog_description(parser: argparse.ArgumentParser) -> list[str]:
    """Extract paragraph descriptions from the module's module-level docstring."""
    description = parser.description or ""
    epilog = parser.epilog or ""

    # Use the module docstring from build_team for the fuller description
    # Fall back to brief description + fixed paragraph
    paras = [
        description,
        (
            "Given a project description file (.json or .md), agentteams analyzes "
            "the project goal, selects the right 4-tier agent team, renders all "
            "agent files from templates, and writes them to the target project's "
            ".github/agents/ directory."
        ),
        (
            "The generated team includes an orchestrator, ten governance agents, "
            "domain agents appropriate for the deliverable type, and one workstream "
            "expert per project component."
        ),
    ]
    return [p for p in paras if p]


def _get_examples() -> list[dict[str, str]]:
    """Return a fixed list of usage examples."""
    return [
        {
            "desc": "Generate a VS Code Copilot agent team",
            "cmd": "agentteams --description brief.json --project /path/to/project",
        },
        {
            "desc": "Generate for Copilot CLI without writing (dry run)",
            "cmd": "agentteams --description brief.json --framework copilot-cli --dry-run",
        },
        {
            "desc": "Re-render drifted files after a template update",
            "cmd": "agentteams --description brief.json --update",
        },
        {
            "desc": "Check for drift in CI (exits 1 if stale)",
            "cmd": "agentteams --description brief.json --check",
        },
        {
            "desc": "Scan generated files for security issues",
            "cmd": "agentteams --description brief.json --scan-security",
        },
        {
            "desc": "Run post-generation audit with AI review",
            "cmd": "agentteams --description brief.json --post-audit",
        },
    ]


def _groff_escape(text: str) -> str:
    """Escape characters that have special meaning in groff."""
    # Escape backslash first, then dash (groff treats - as minus in .B context)
    text = text.replace("\\", "\\\\")
    text = text.replace("-", "\\-")
    return text


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    # Import here to avoid circular at module load time
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from build_team import _build_parser  # type: ignore[import]
    sys.stdout.write(generate_man_page(_build_parser()))
