"""test_vulnscan_doc_conformity.py — the vulnscan reference must keep up with the code.

`docs_src/api-reference/vulnscan.md` is hand-written. An audit on 2026-10-28 found about 25
places where it had drifted: a fix cooldown the code had dropped, a DLP gap the code had closed,
commands missing from the exit-code table, flags missing from the command reference, modules with
no entry at all. `test_api_doc_signatures.py` only compares function headings with signatures, so
none of that failed a test.

These tests compare the reference with the code's own tables:

* every configuration key, with its default (`daily.DEFAULT_CONFIG`, `agent_settings.DEFAULT_AGENT`,
  `agent_upstreams.DEFAULTS`, `subproc_confine.DEFAULTS`, the debrief defaults);
* every notification, word for word (`notify.MESSAGES`);
* every subcommand, in the command reference and in the exit codes;
* every long option of every subcommand;
* every vulnscan module, under a heading of its own or in the section :data:`_DOCUMENTED_IN` names.

Each check searches only its own section: `escalation` and `publish-failed` are ledger-row kinds as
well as notification keys, and the first table that mentions a key is not necessarily the right one.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_settings, agent_upstreams, daily, debrief, notify, subproc_confine
from agentteams.vulnscan.__main__ import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = (REPO_ROOT / "docs_src/api-reference/vulnscan.md").read_text(encoding="utf-8")
PKG_DIR = REPO_ROOT / "agentteams/vulnscan"

#: Modules documented inside a broader section instead of under a heading of their own: the section's
#: heading text. The module must be named there, so moving or dropping its text fails.
_DOCUMENTED_IN: dict[str, str] = {
    "agent_driver_base": "### `agentteams.vulnscan.agent_drivers`",
    "agent_drivers_codex": "### `agentteams.vulnscan.agent_drivers`",
    "agent_drivers_copilot": "### `agentteams.vulnscan.agent_drivers`",
    "coverage": "## Project registry",
    "registry": "## Project registry",
    "project_cli": "## Project registry",
    "executor_serve": "### `agentteams.vulnscan.executor`",
    "gateway_context": "### `agentteams.vulnscan.gateway`",
    "gateway_dialects": "### `agentteams.vulnscan.gateway`",
    "gateway_dlp": "### `agentteams.vulnscan.gateway`",
    "gateway_peer": "### `agentteams.vulnscan.gateway`",
    "mcp": "## The agents' interface: MCP tools",
    "repo_alias": "## The agents' interface: MCP tools",
    "operator_cli": "## Command reference",
    "team_dir": "### `agentteams.vulnscan.agent_run`",
}

#: Defaults the reference states differently from the literal value, on purpose: key -> documented cell.
#: `confine.subprocesses` is None in the code ("unset") and means true; the table says so.
_DEFAULT_SPELLED_OUT: dict[str, str] = {"subprocesses": "`true` (unset)"}


def _section(heading: str) -> str:
    """Return the text under ``heading`` up to the next heading of the same or a higher level."""
    start = DOC.find(f"\n{heading}\n")
    assert start >= 0, f"vulnscan.md has no heading {heading!r}"
    level = len(heading) - len(heading.lstrip("#"))
    ends = [m.start() for m in re.finditer(r"^#{1,%d} " % level, DOC[start + 1:], re.M)]
    return DOC[start: start + 1 + ends[1]] if len(ends) > 1 else DOC[start:]


def _table_cells(text: str) -> dict[str, list[str]]:
    """Map the backticked first cell of each table row to its remaining cells."""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^\| `([^`]+)` \|(.*)\|\s*$", line)
        if m:
            rows.setdefault(m.group(1), [c.strip() for c in m.group(2).split(" | ")])
    return rows


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return dict(sub.choices)


_CONFIG = _section("## Configuration: `vulnscan.json`")
_DEFAULTS = [
    *daily.DEFAULT_CONFIG.items(),
    *agent_settings.DEFAULT_AGENT.items(),
    *agent_upstreams.DEFAULTS.items(),
    *subproc_confine.DEFAULTS.items(),
    ("debrief_budget_usd", debrief.DEFAULT_DEBRIEF_BUDGET_USD),
    ("debrief_max_minutes", debrief.DEFAULT_DEBRIEF_MAX_MINUTES),
    ("debrief_max_tokens", debrief.DEFAULT_DEBRIEF_MAX_TOKENS),
]


@pytest.mark.parametrize(("key", "default"), _DEFAULTS, ids=[k for k, _ in _DEFAULTS])
def test_every_config_key_is_documented_with_its_default(key: str, default: object) -> None:
    """A key the config reads must have a row in Configuration whose default is the code's."""
    rows = _table_cells(_CONFIG)
    assert key in rows, f"config key {key!r} has no row under Configuration in vulnscan.md"
    if isinstance(default, dict) and key == "agent":
        return   # the agent block's own table follows
    cell = rows[key][0]
    if key in _DEFAULT_SPELLED_OUT:
        assert cell == _DEFAULT_SPELLED_OUT[key], f"config key {key!r}: the default cell changed to {cell}"
        return
    m = re.match(r"`([^`]*)`", cell)
    assert m, f"config key {key!r}: the default cell {cell!r} does not start with a `value`"
    try:
        documented = json.loads(m.group(1))
    except json.JSONDecodeError:
        pytest.fail(f"config key {key!r}: the documented default {m.group(1)!r} is not a JSON value")
    # by value (20 == 20.0), but a bool is never a number (True == 1)
    assert documented == default and isinstance(documented, bool) == isinstance(default, bool), (
        f"config key {key!r}: vulnscan.md says the default is {m.group(1)}, the code says {json.dumps(default)}")


def test_every_notification_is_documented_word_for_word() -> None:
    """Each `notify.MESSAGES` key must have a row under Notifications with the exact message."""
    rows = _table_cells(_section("## Notifications"))
    assert notify.MESSAGES, "notify.MESSAGES is empty: this test would pass vacuously"
    drift = {k: (rows.get(k, [None])[0], v) for k, v in notify.MESSAGES.items() if rows.get(k, [None])[0] != v}
    assert not drift, "notification text differs (doc, code): " + json.dumps(drift, indent=1, ensure_ascii=False)
    extra = set(rows) - set(notify.MESSAGES)
    assert not extra, f"vulnscan.md documents notifications the code does not send: {sorted(extra)}"


def _commands_named(text: str) -> set[str]:
    """Every backticked command name opening a table row's first cell (``| `grant sign`, `act` |``)."""
    names: set[str] = set()
    for m in re.finditer(r"^\| ([^|]*)\|", text, re.M):
        names |= set(re.findall(r"`([a-z][a-z-]*)", m.group(1)))
    return names


def test_every_subcommand_is_in_the_command_reference() -> None:
    commands = _subcommands()
    assert commands, "no subcommands were found: this test would pass vacuously"
    missing = sorted(set(commands) - _commands_named(_section("## Command reference")))
    assert not missing, f"subcommands with no row under Command reference: {missing}"


def test_every_subcommand_has_documented_exit_codes() -> None:
    """A command is in the exit-code table, or in its "otherwise exit 0" sentence."""
    exits = _section("### Exit codes")
    named = _commands_named(exits)
    zero = re.search(r"^(.*)otherwise exit 0", exits, re.M)
    named |= set(re.findall(r"`([a-z][a-z-]*)`", zero.group(1))) if zero else set()
    missing = sorted(set(_subcommands()) - named)
    assert not missing, f"subcommands with no exit codes under Exit codes: {missing}"


def _long_options() -> list[tuple[str, str]]:
    out = []
    for name, parser in sorted(_subcommands().items()):
        sub = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        parsers = [(name, parser)] + [(f"{name} {n}", p) for a in sub for n, p in a.choices.items()]
        for label, p in parsers:
            out += [(label, opt) for a in p._actions for opt in a.option_strings
                    if opt.startswith("--") and opt != "--help"]
    return sorted(set(out))


_OPTIONS = _long_options()


@pytest.mark.parametrize(("command", "option"), _OPTIONS, ids=[f"{c}:{o}" for c, o in _OPTIONS])
def test_every_long_option_is_documented(command: str, option: str) -> None:
    """Every ``--option`` a subcommand accepts appears in the reference (the walkthrough is not enough)."""
    assert re.search(rf"`[^`\n]*{re.escape(option)}(?![\w-])", DOC), (
        f"`{command}` accepts {option}, which vulnscan.md never mentions")


def _modules() -> list[str]:
    return sorted(p.stem for p in PKG_DIR.glob("*.py") if not p.stem.startswith("_"))


@pytest.mark.parametrize("module", _modules())
def test_every_module_is_documented(module: str) -> None:
    """A module has a `### agentteams.vulnscan.<module>` section, or is named in the one `_DOCUMENTED_IN` gives."""
    if f"\n### `agentteams.vulnscan.{module}`\n" in DOC:
        assert module not in _DOCUMENTED_IN, f"{module} has its own section now: drop it from _DOCUMENTED_IN"
        return
    assert module in _DOCUMENTED_IN, (
        f"agentteams/vulnscan/{module}.py has no section in vulnscan.md: add `### agentteams.vulnscan.{module}`, "
        f"or document it in a broader section and name that section in _DOCUMENTED_IN")
    assert re.search(rf"(?<![\w]){re.escape(module)}(?![\w])", _section(_DOCUMENTED_IN[module])), (
        f"{module} is not named under {_DOCUMENTED_IN[module]!r}, where _DOCUMENTED_IN says it is documented")


def test_documented_in_names_only_real_modules() -> None:
    stale = sorted(set(_DOCUMENTED_IN) - set(_modules()))
    assert not stale, f"_DOCUMENTED_IN names modules that no longer exist: {stale}"
