"""Rules a repository supplies to the script census: families and repeated fragments.

Steps three and four of a script-consolidation review (``references/script-consolidation.reference.md``),
reached through ``python -m agentteams.script_census``:

- ``classify`` reads an inventory on stdin and adds ``family``: the first ``family_rules`` entry that
  matches the script's path, its text, or both. A script no rule matches is ``unclassified``.
- ``fragments`` reads an inventory on stdin and writes ``path<TAB>fragment``, one row for each
  ``fragment_rules`` pattern found in a script's text.

No rule ships with the tool: what counts as a family or a repeated fragment is the repository's
judgment, written in a JSON file (:data:`CONFIG_NAME`) that is checked against a bundled schema.

The patterns are regular expressions the repository wrote, and Python's ``re`` has no time limit, so
a pattern such as ``(a+)+$`` could run without end. All matching therefore runs in one worker process
that the caller stops after a wall-clock limit; the refusal names the rule and the script it was on.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentteams.api_impact import AGENT_DIRS
from agentteams.handoff_payloads import worker_mp_context
from agentteams.script_census import MAX_BYTES, CensusError, read_regular, read_row

#: File name of the configuration, looked for under ``references/``.
CONFIG_NAME: str = "script-census.json"
#: Seconds allowed for all pattern matching of one command.
MATCH_TIMEOUT: float = 60.0
#: The ``family`` of a script no rule matches.
UNCLASSIFIED: str = "unclassified"
#: The columns ``fragments`` writes.
FRAGMENT_COLUMNS: tuple[str, ...] = ("path", "fragment")
#: A configuration file larger than this is refused.
MAX_CONFIG_BYTES: int = 1_000_000
_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "script-census-config.schema.json"


def find_config(repo: Path | str = ".") -> Path | None:
    """Where the configuration is, in the order ``api_impact.ledger_dir`` looks for its ledger.

    Args:
        repo: The directory to look in: the repository root.

    Returns:
        ``references/script-census.json`` at the root when it exists, else the same name under the
        first agent directory's ``references/`` that has it, else ``None``.

    Raises:
        Nothing.
    """
    root = Path(repo)
    for base in ("", *AGENT_DIRS):
        candidate = root / base / "references" / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | str | None = None, *, repo: Path | str = ".") -> dict[str, Any]:
    """Read the configuration and check it against the bundled schema.

    Args:
        path: The file, or ``None`` to use :func:`find_config`.
        repo: The directory :func:`find_config` looks in when *path* is ``None`` (default: the
            current directory).

    Returns:
        The configuration. Every pattern in it compiles.

    Raises:
        CensusError: No file is found; it is a symbolic link, not a regular file, or larger than
            :data:`MAX_CONFIG_BYTES`; it is not JSON; it does not match the schema; a pattern does
            not compile; or ``jsonschema`` is not installed.
    """
    found = Path(path) if path is not None else find_config(repo)
    if found is None:
        raise CensusError(f"no configuration: pass --config FILE or create references/{CONFIG_NAME}")
    raw = read_regular(str(found), MAX_CONFIG_BYTES)  # no link followed, no FIFO or device, bounded
    if raw is None:
        raise CensusError(f"{found} is not a readable regular file of at most {MAX_CONFIG_BYTES} bytes "
                          "(a symbolic link is not followed)")
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CensusError(f"{found} cannot be read as JSON: {type(exc).__name__}: {exc}"[:300]) from exc
    try:
        import jsonschema
    except ImportError as exc:  # a declared dependency; absent only in a stripped install
        raise CensusError("jsonschema is not installed; the configuration cannot be checked") from exc
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(config), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(top level)"
        raise CensusError(f"{found} does not match the schema at {where}: {first.message}")
    for kind, keys in (("family_rules", ("path", "content")), ("fragment_rules", ("pattern",))):
        for number, rule in enumerate(config.get(kind, []), start=1):
            for key in keys:
                if key in rule:
                    try:
                        re.compile(rule[key])
                    except re.error as exc:
                        raise CensusError(f"{found}: {kind} entry {number}, {key} is not a regular expression: {exc}") from exc
    return config


def _match_worker(rows: list[dict[str, str]], rules: list[dict[str, str]], first_only: bool, max_bytes: int,
                  conn: Any) -> None:
    """Run in the worker process: match every rule against every row and send what was found.

    Messages: ``("at", row number, rule name)`` before each rule is tried, so the caller can name
    what was running when it stopped the worker; ``("row", row number, [names], readable)`` after
    each row; ``("done",)`` at the end; ``("error", text)`` for a row that is refused.
    """
    try:
        compiled = [(rule["name"],
                     re.compile(rule["path"]) if "path" in rule else None,
                     re.compile(rule["content"], re.MULTILINE) if "content" in rule else None) for rule in rules]
        for number, row in enumerate(rows):
            data = read_row(row, max_bytes)
            text = data.decode("utf-8", errors="replace") if data is not None else None
            names: list[str] = []
            for name, path_re, content_re in compiled:
                conn.send(("at", number, name))
                if path_re is not None and not path_re.search(row["path"]):
                    continue
                if content_re is not None and (text is None or not content_re.search(text)):
                    continue
                names.append(name)
                if first_only:
                    break
            conn.send(("row", number, names, text is not None))
        conn.send(("done",))
    except CensusError as exc:
        conn.send(("error", str(exc)))
    finally:
        conn.close()


def _match(rows: list[dict[str, str]], rules: list[dict[str, str]], *, first_only: bool, max_bytes: int,
           timeout: float) -> tuple[list[list[str]], int]:
    """The rule names matching each row, and how many rows could not be read; stops the worker at *timeout*."""
    if max_bytes < 0:
        raise CensusError("max_bytes must not be negative")
    if not math.isfinite(timeout) or timeout <= 0:
        raise CensusError("timeout must be a number of seconds greater than zero")
    ctx = worker_mp_context()
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_match_worker, args=(rows, rules, first_only, max_bytes, sender))
    with warnings.catch_warnings():
        # The worker takes no inherited lock and only reads files and matches text; see
        # handoff_payloads.worker_mp_context for why fork is the start method.
        warnings.filterwarnings("ignore", message=r".*fork.*may lead to deadlocks in the child.*",
                                category=DeprecationWarning)
        proc.start()
    sender.close()
    found: list[list[str]] = [[] for _ in rows]
    unreadable = 0
    at: tuple[int, str] | None = None
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not receiver.poll(remaining):
                where = f" while rule {at[1]!r} was matching {rows[at[0]]['path']!r}" if at else ""
                raise CensusError(f"pattern matching exceeded {timeout:g}s{where}; simplify that pattern")
            try:
                message = receiver.recv()
            except EOFError as exc:
                raise CensusError(f"the matching worker stopped without a result (exit {proc.exitcode})") from exc
            if message[0] == "at":
                at = (message[1], message[2])
            elif message[0] == "row":
                found[message[1]] = message[2]
                unreadable += 0 if message[3] else 1
            elif message[0] == "error":
                raise CensusError(message[1])
            else:
                return found, unreadable
    finally:
        if proc.is_alive():
            proc.terminate()
        proc.join(1.0)
        if proc.is_alive():
            proc.kill()
            proc.join(1.0)
        receiver.close()


def classify(rows: Iterable[dict[str, str]], config: dict[str, Any], *, max_bytes: int = MAX_BYTES,
             timeout: float = MATCH_TIMEOUT) -> tuple[list[dict[str, str]], int]:
    """Add ``family`` to inventory rows: the first rule that matches wins.

    A rule with ``path`` matches when that expression is found in the row's ``path``; a rule with
    ``content`` when it is found in the script's text; a rule with both when both are. A script that
    can no longer be read can match only a rule without ``content``.

    The rows are text, so they are not trusted, and ``root`` is whatever the row says: a row can name
    any regular file the caller can read. What the result reveals about it is which rules matched.

    Args:
        rows: Inventory rows (at least ``path`` and ``root``).
        config: A configuration from :func:`load_config`.
        max_bytes: Files larger than this are not read. Use the value the inventory was made with.
        timeout: Seconds allowed for all matching.

    Returns:
        ``(rows, unreadable)``: the rows in order, each with ``family`` set to a rule's family or
        :data:`UNCLASSIFIED`, and the number of scripts that could not be read.

    Raises:
        CensusError: A row is not an inventory row, *max_bytes* is negative, *timeout* is not a
            finite number above zero, or matching did not finish in *timeout* seconds.
    """
    listed = [dict(row) for row in rows]
    rules = [{"name": rule["family"], **{k: rule[k] for k in ("path", "content") if k in rule}}
             for rule in config.get("family_rules", [])]
    found, unreadable = _match(listed, rules, first_only=True, max_bytes=max_bytes, timeout=timeout)
    return [{**row, "family": names[0] if names else UNCLASSIFIED} for row, names in zip(listed, found)], unreadable


def fragments(rows: Iterable[dict[str, str]], config: dict[str, Any], *, max_bytes: int = MAX_BYTES,
              timeout: float = MATCH_TIMEOUT) -> tuple[list[dict[str, str]], int]:
    """List which fragment patterns are found in which scripts.

    Args:
        rows: Inventory rows (at least ``path`` and ``root``).
        config: A configuration from :func:`load_config`.
        max_bytes: Files larger than this are not read. Use the value the inventory was made with.
        timeout: Seconds allowed for all matching.

    Returns:
        ``(found, unreadable)``. ``found`` has one dict per script and matching rule, keyed by
        :data:`FRAGMENT_COLUMNS`, in inventory order and then rule order; ``path`` is the root and
        the path joined with ``/`` so that it is unique across roots. A rule is reported once per
        script however often it matches. ``unreadable`` counts scripts that could not be read.

    Raises:
        CensusError: As :func:`classify`.
    """
    listed = [dict(row) for row in rows]
    rules = [{"name": rule["fragment"], "content": rule["pattern"]} for rule in config.get("fragment_rules", [])]
    found, unreadable = _match(listed, rules, first_only=False, max_bytes=max_bytes, timeout=timeout)
    out: list[dict[str, str]] = []
    for row, names in zip(listed, found):
        where = f"{row['root'].replace(os.sep, '/').rstrip('/')}/{row['path']}"
        out.extend({"path": where, "fragment": name} for name in names)
    return out, unreadable


__all__ = ["CONFIG_NAME", "FRAGMENT_COLUMNS", "MATCH_TIMEOUT", "MAX_CONFIG_BYTES", "UNCLASSIFIED", "classify",
           "find_config", "fragments", "load_config"]
