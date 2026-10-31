"""test_vulnscan_doc_conformity.py — the vulnscan reference must keep up with the code.

`docs_src/api-reference/vulnscan.md` is hand-written. An audit on 2026-10-28 found about 25
places where it had drifted: a fix cooldown the code had dropped, a DLP gap the code had closed,
commands missing from the exit-code table, flags missing from the command reference, modules with
no entry at all. `test_api_doc_signatures.py` only compares function headings with signatures, so
none of that failed a test.

These tests compare the reference with the code's own tables, in both directions where the doc has a
table of its own (a documented key, notification, command or module the code no longer has fails too):

* each configuration block's keys and defaults, against that block's table: `daily.DEFAULT_CONFIG`,
  `agent_settings.DEFAULT_AGENT` (with the debrief defaults), `agent_upstreams.DEFAULTS`,
  `subproc_confine.DEFAULTS`, `docker_readiness.DEFAULTS`. Keys are compared per block, because `image` is in two of them;
* every notification, word for word (`notify.MESSAGES`);
* every subcommand, in the command table and in the exit codes;
* every long option and positional choice of every subcommand and sub-subcommand, in a table row or
  paragraph of the command reference that names that command;
* every vulnscan module, under a heading of its own or in the section :data:`_DOCUMENTED_IN` names.

Each check reads only its own section (`escalation` is a ledger-row kind as well as a notification key),
and headings are found outside fenced code only, so a ``# comment`` in a shell example ends nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_settings, agent_upstreams, daily, debrief, docker_readiness, notify, subproc_confine
from agentteams.vulnscan.__main__ import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = (REPO_ROOT / "docs_src/api-reference/vulnscan.md").read_text(encoding="utf-8")
PKG_DIR = REPO_ROOT / "agentteams/vulnscan"

#: Modules documented inside a broader section instead of under a heading of their own: the section's
#: heading. The section's body must name the module in code (`` `coverage` ``, `` `coverage.states(...)` ``
#: or ``agentteams.vulnscan.coverage``), so moving or dropping its text fails.
_DOCUMENTED_IN: dict[str, str] = {
    "agent_driver_base": "### `agentteams.vulnscan.agent_drivers`",
    "agent_drivers_codex": "### `agentteams.vulnscan.agent_drivers`",
    "agent_drivers_copilot": "### `agentteams.vulnscan.agent_drivers`",
    "checkout_freshness": "### `agentteams.vulnscan.daily`",   # split from daily.py, re-exported by it
    "coverage": "## Project registry",
    "registry": "## Project registry",
    "project_cli": "## Project registry",
    "executor_serve": "### `agentteams.vulnscan.executor`",
    "executor_pr": "### `agentteams.vulnscan.executor`",   # split from executor.py: commit and PR text
    "npm_major": "### `agentteams.vulnscan.fix`",          # stage-major's package.json raise (fix.npm_majors)
    "infra_retry": "### `agentteams.vulnscan.daily`",
    "gateway_context": "### `agentteams.vulnscan.gateway`",
    "gateway_dialects": "### `agentteams.vulnscan.gateway`",
    "gateway_dlp": "### `agentteams.vulnscan.gateway`",
    "gateway_peer": "### `agentteams.vulnscan.gateway`",
    "mcp": "## The agents' interface: MCP tools",
    "repo_alias": "## The agents' interface: MCP tools",
    "operator_cli": "## Command reference",
    "team_dir": "### `agentteams.vulnscan.agent_run`",
    "worktree_fixes": "### `agentteams.vulnscan.daily`",
}

#: The configuration blocks, in the order of their tables under Configuration.
_CONFIG_BLOCKS: list[tuple[str, dict[str, object]]] = [
    ("vulnscan.json", dict(daily.DEFAULT_CONFIG)),
    ("agent", {**agent_settings.DEFAULT_AGENT,
               "debrief_budget_usd": debrief.DEFAULT_DEBRIEF_BUDGET_USD,
               "debrief_max_minutes": debrief.DEFAULT_DEBRIEF_MAX_MINUTES,
               "debrief_max_tokens": debrief.DEFAULT_DEBRIEF_MAX_TOKENS}),
    ("agent (upstreams)", dict(agent_upstreams.DEFAULTS)),
    ("confine", dict(subproc_confine.DEFAULTS)),
    ("docker", dict(docker_readiness.DEFAULTS)),
]

#: Defaults the reference states differently from the literal value, on purpose: (block, key) -> cell.
#: `confine.subprocesses` is None in the code ("unset") and means true; the table says so. The
#: `agent` key's default is the block documented by the next table.
_DEFAULT_SPELLED_OUT: dict[tuple[str, str], str] = {
    ("confine", "subprocesses"): "`true` (unset)",
    ("vulnscan.json", "agent"): "see below",
}

_HEADING = re.compile(r"^(#{1,6}) ", re.M)


def _unfenced(text: str) -> str:
    """Return ``text`` with fenced code blanked out, character for character (offsets are kept)."""
    return re.sub(r"^```.*?^```", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.M | re.S)


_DOC_UNFENCED = _unfenced(DOC)


def _section(heading: str, *, stop_level: int | None = None) -> str:
    """Return ``heading``'s text up to the next heading of level ``stop_level`` or higher (default: its own).

    Args:
        heading: The heading line, e.g. ``"## Notifications"``.
        stop_level: End at a heading of this level or higher; ``3`` ends ``## X`` at its first ``###``.

    Returns:
        The heading line and its body. Headings inside fenced code are ignored.
    """
    start = _DOC_UNFENCED.find(f"\n{heading}\n")
    assert start >= 0, f"vulnscan.md has no heading {heading!r}"
    body = start + len(heading) + 2
    stop = stop_level or len(heading) - len(heading.lstrip("#"))
    end = next((m.start() for m in _HEADING.finditer(_DOC_UNFENCED, body) if len(m.group(1)) <= stop), len(DOC))
    return DOC[start + 1:end]


def _tables(text: str) -> list[dict[str, str]]:
    """Return each table in ``text`` as {backticked first cell: second cell}, first row per key."""
    out = []
    for block in re.findall(r"(?:^\|.*\|[ \t]*\n?)+", text, re.M):
        rows: dict[str, str] = {}
        for line in block.splitlines():
            m = re.match(r"^\| `([^`]+)` \| ([^|]*)\|", line)
            if m:
                rows.setdefault(m.group(1), m.group(2).strip())
        out.append(rows)
    return out


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return dict(sub.choices)


def _config_tables() -> list[dict[str, str]]:
    tables = [t for t in _tables(_section("## Configuration: `vulnscan.json`")) if t]
    assert len(tables) == len(_CONFIG_BLOCKS), (
        f"Configuration has {len(tables)} key tables, the test expects {len(_CONFIG_BLOCKS)} "
        f"({', '.join(b for b, _ in _CONFIG_BLOCKS)}): update _CONFIG_BLOCKS with the doc")
    return tables


_DEFAULTS = [(block, key, value) for block, keys in _CONFIG_BLOCKS for key, value in keys.items()]


@pytest.mark.parametrize(("block", "key", "default"), _DEFAULTS, ids=[f"{b}:{k}" for b, k, _ in _DEFAULTS])
def test_every_config_key_is_documented_with_its_default(block: str, key: str, default: object) -> None:
    """A key a config block reads must have a row in that block's table, with the code's default."""
    rows = _config_tables()[[b for b, _ in _CONFIG_BLOCKS].index(block)]
    assert key in rows, f"{block} key {key!r} has no row in its table under Configuration in vulnscan.md"
    cell = rows[key]
    if (block, key) in _DEFAULT_SPELLED_OUT:
        assert cell == _DEFAULT_SPELLED_OUT[block, key], f"{block} key {key!r}: the default cell changed to {cell}"
        return
    m = re.match(r"`([^`]*)`", cell)
    assert m, f"{block} key {key!r}: the default cell {cell!r} does not start with a `value`"
    try:
        documented = json.loads(m.group(1))
    except json.JSONDecodeError:
        pytest.fail(f"{block} key {key!r}: the documented default {m.group(1)!r} is not a JSON value")
    # by value (20 == 20.0), but a bool is never a number (True == 1)
    assert documented == default and isinstance(documented, bool) == isinstance(default, bool), (
        f"{block} key {key!r}: vulnscan.md says the default is {m.group(1)}, the code says {json.dumps(default)}")


def test_no_config_table_documents_a_key_the_code_dropped() -> None:
    """Every row of a config block's table is a key that block still reads."""
    stale = {block: sorted(set(rows) - set(keys))
             for (block, keys), rows in zip(_CONFIG_BLOCKS, _config_tables()) if set(rows) - set(keys)}
    assert not stale, f"vulnscan.md documents config keys the code no longer has: {stale}"


def test_every_notification_is_documented_word_for_word() -> None:
    """Each `notify.MESSAGES` key has a row under Notifications with the exact message, and no other row exists."""
    rows = {k: v for t in _tables(_section("## Notifications")) for k, v in t.items()}
    assert notify.MESSAGES, "notify.MESSAGES is empty: this test would pass vacuously"
    drift = {k: (rows.get(k), v) for k, v in notify.MESSAGES.items() if rows.get(k) != v}
    assert not drift, "notification text differs (doc, code): " + json.dumps(drift, indent=1, ensure_ascii=False)
    extra = set(rows) - set(notify.MESSAGES)
    assert not extra, f"vulnscan.md documents notifications the code does not send: {sorted(extra)}"


def _commands_named(text: str) -> set[str]:
    """Every command name opening a code span in a table row's first cell (`` `grant sign` ``, `` `act OP` ``)."""
    names: set[str] = set()
    for m in re.finditer(r"^\| ([^|]*)\|", text, re.M):
        names |= set(re.findall(r"`([a-z][a-z-]*)(?=[` \[])", m.group(1)))
    return names


_COMMAND_TABLE = _section("## Command reference", stop_level=3)


def test_every_subcommand_is_in_the_command_table() -> None:
    """Each subcommand has a row in the command table, and every command the table names exists."""
    commands = set(_subcommands())
    assert commands, "no subcommands were found: this test would pass vacuously"
    named = _commands_named(_COMMAND_TABLE)
    assert not commands - named, f"subcommands with no row under Command reference: {sorted(commands - named)}"
    assert not named - commands, f"Command reference names commands the CLI does not have: {sorted(named - commands)}"


def test_every_subcommand_has_documented_exit_codes() -> None:
    """A command is in the exit-code table, or in its "otherwise exit 0" paragraph; no other command is."""
    exits = _section("### Exit codes")
    named = _commands_named(exits)
    zero = next((p for p in re.split(r"\n\s*\n", exits) if "otherwise exit 0" in p), "")
    # "every `list`" names an action of several commands, not a command
    named |= set(re.findall(r"(?<!every )`([a-z][a-z-]*)`", zero.split("otherwise exit 0")[0]))
    commands = set(_subcommands())
    assert not commands - named, f"subcommands with no exit codes under Exit codes: {sorted(commands - named)}"
    assert not named - commands, f"Exit codes names commands the CLI does not have: {sorted(named - commands)}"


def _units(text: str) -> list[str]:
    """Split the command reference into table rows and prose paragraphs."""
    return [u for u in re.split(r"\n\s*\n|\n(?=\|)", text) if u.strip()]


def _arguments() -> list[tuple[str, str, str]]:
    """Every ``(command, label, argument)``: long options, and the choices of positionals, at every level."""
    out = []

    def walk(command: str, label: str, parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    walk(command, f"{label} {name}", child)
            elif action.option_strings:
                out.extend((command, label, o) for o in action.option_strings if o.startswith("--") and o != "--help")
            elif action.choices:
                out.extend((command, label, str(c)) for c in action.choices)

    for name, parser in sorted(_subcommands().items()):
        walk(name, name, parser)
    return sorted(set(out))


_ARGUMENTS = _arguments()
_COMMAND_UNITS = _units(_section("## Command reference"))


@pytest.mark.parametrize(("command", "label", "argument"), _ARGUMENTS,
                         ids=[f"{lbl}:{arg}" for _, lbl, arg in _ARGUMENTS])
def test_every_argument_is_documented_with_its_command(command: str, label: str, argument: str) -> None:
    """An option or positional choice appears, in code, in a command-reference row or paragraph naming its command.

    Args:
        command: The top-level subcommand (``grant``).
        label: The command path the argument belongs to (``grant sign``), for the message.
        argument: ``--option`` or a positional choice.
    """
    names_command = re.compile(rf"`{re.escape(command)}(?=[` \[])")
    in_code = re.compile(rf"`[^`\n]*(?<![\w-]){re.escape(argument)}(?![\w-])[^`\n]*`")
    assert any(names_command.search(u) and in_code.search(u) for u in _COMMAND_UNITS), (
        f"`{label}` accepts {argument}, which no Command reference row or paragraph about `{command}` mentions")


def _modules() -> list[str]:
    return sorted(p.stem for p in PKG_DIR.glob("*.py") if not p.stem.startswith("_"))


@pytest.mark.parametrize("module", _modules())
def test_every_module_is_documented(module: str) -> None:
    """A module has a `### agentteams.vulnscan.<module>` section, or is named in code in the one `_DOCUMENTED_IN` gives.

    Args:
        module: A vulnscan module's name.
    """
    if f"\n### `agentteams.vulnscan.{module}`\n" in _DOC_UNFENCED:
        assert module not in _DOCUMENTED_IN, f"{module} has its own section now: drop it from _DOCUMENTED_IN"
        return
    assert module in _DOCUMENTED_IN, (
        f"agentteams/vulnscan/{module}.py has no section in vulnscan.md: add `### agentteams.vulnscan.{module}`, "
        f"or document it in a broader section and name that section in _DOCUMENTED_IN")
    body = _section(_DOCUMENTED_IN[module]).split("\n", 1)[1]   # the heading's own words do not count
    named = (re.search(rf"`{re.escape(module)}[`.(]", body)
             or re.search(rf"`[^`\n]*agentteams\.vulnscan\.{re.escape(module)}(?![\w])[^`\n]*`", body))
    assert named, (
        f"{module} is not named (in code) under {_DOCUMENTED_IN[module]!r}, where _DOCUMENTED_IN says it is documented")


def test_no_module_section_outlives_its_module() -> None:
    """Every `### agentteams.vulnscan.<module>` heading, and every `_DOCUMENTED_IN` entry, names an existing module."""
    modules = set(_modules())
    headings = set(re.findall(r"^### `agentteams\.vulnscan\.([a-z_]+)`$", _DOC_UNFENCED, re.M))
    assert headings, "no module headings were found: this test would pass vacuously"
    assert not headings - modules, f"vulnscan.md has sections for modules that no longer exist: {sorted(headings - modules)}"
    assert not set(_DOCUMENTED_IN) - modules, (
        f"_DOCUMENTED_IN names modules that no longer exist: {sorted(set(_DOCUMENTED_IN) - modules)}")
