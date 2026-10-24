"""The selected agent definition (``agent.recipe``): which file a run loads, what the self-test binds of it, and the
vulnscan-owned copy a Copilot or goose run reads instead of the team's file.

@security rc.4 review, Medium 1, and its residuals (vulnscan-rc4-residuals plan R1, R2, R3, R7, R9): a run loads
``agent.recipe`` (default: the driver's), so the self-test runs with the same file (:attr:`.agent_driver_base.Driver.recipe`,
set by :func:`.agent_drivers.driver_for`) and the flags fingerprint binds it (:func:`binding`). What is bound depends
on what the CLI does with the file:

| Framework | How the CLI reads the file | What the CLI is given | Bound in ``flags_sha256`` |
|---|---|---|---|
| copilot | ``--agent``: YAML front matter is **configuration** (``tools``, ``model``, ``mcp-servers`` ...), the body is the prompt | a copy (:func:`copilot_copy`): canonical front matter (``name: vuln-remediation`` and the original ``description``), the body byte for byte; every other key is removed and reported | path **and** sha256 of the copy |
| goose | ``--recipe``: a YAML recipe (its own prompt, extensions and settings) | a copy of the recipe once it passes :func:`goose_recipe_problem` (a top-level key allowlist; ``extensions`` exactly as agentteams renders it) | path **and** sha256 of the copy |
| claude | ``--append-system-prompt-file``: appended to the system prompt verbatim, never parsed | the team's file | path only |
| codex | ``model_instructions_file``: the instructions text, never parsed | the team's file | path only |

claude and codex read the file as prompt text only: its content configures no tool, server or model, so a prompt
edit must not invalidate the self-test (a regenerated team would otherwise churn it daily); the path is bound so the
self-test runs with the selected file. The copy lives in ``<run>/agentdef`` (:data:`AGENTDEF_DIR`), under the
agent's read-only ``/work/run`` mount, and holds nothing else: Copilot's ``--add-dir`` names it instead of the team,
so no other agent file in the team's ``.github/agents`` is in reach (R1). A run writes its copy, then re-checks that
the team's file still renders to it and that the self-test still vouches for it (R9; ``agent_run.run_agent``): an
edit after the copy is written refuses the run, and a later edit no longer changes what the CLI reads. The one
residual is an edit made and reverted between those two re-checks.

The source file is read strictly (R2, :func:`read_source`): a regular file (no symlink, also not through a symlinked
parent: the resolved path must stay under the resolved team), at most :data:`MAX_BYTES`, UTF-8; a BOM is dropped and
CRLF line ends become LF before anything is parsed, and the copy is written from that normalised text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentteams.vulnscan.agent_driver_base import Driver

#: The only front-matter keys a Copilot agent file the CLI reads may carry (everything else is configuration).
COPILOT_KEYS = frozenset({"name", "description"})
#: Frameworks whose agent definition the CLI reads from a vulnscan-owned copy, bound by its sha256.
CONTENT_BOUND = frozenset({"copilot", "goose"})
#: The largest agent definition read (R2).
MAX_BYTES = 256 * 1024
#: ``<run>/agentdef``: the copy's directory, and where the agent container sees it (``/work/run`` is the run dir).
AGENTDEF_DIR = "agentdef"
AGENTDEF_IN_CONTAINER = f"/work/run/{AGENTDEF_DIR}"
#: The copy's name (the slug Copilot's ``--agent`` names: the file stem and the ``name:`` field agree).
COPILOT_NAME = "vuln-remediation"
#: Each content-bound framework's copy, relative to :data:`AGENTDEF_DIR`.
COPY_PATH = {"copilot": f".github/agents/{COPILOT_NAME}.agent.md", "goose": "recipe.yaml"}
#: goose recipe top-level keys a run accepts (``extensions`` too, checked against the rendered block, R7).
GOOSE_KEYS = frozenset({"version", "title", "description", "instructions", "prompt", "parameters", "activities"})
#: The extensions agentteams renders for the vuln-remediation recipe (``goose._scoped_builtin_extensions`` default,
#: plus ``summon`` because the agent hands off to the orchestrator and @security).
GOOSE_EXTENSIONS = ("developer", "summon")
_KEY_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t]+(.*))?")
_RECIPE_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?")
_SINGLE = re.compile(r"'(?:[^']|'')*'")
#: A plain YAML scalar may not start with an indicator (flow, block, anchor, alias, tag, directive ...).
_INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`")


def selected(a: dict[str, Any], d: Driver) -> str:
    """Return the agent definition a run loads, relative to the team: ``agent.recipe``, else the driver's."""
    return str(a.get("recipe") or d.default_recipe).lstrip("/")


#: Characters a YAML reader may treat as a line break, or that have no place in an agent file: C0 controls but tab
#: and LF, DEL, C1 controls (NEL U+0085 among them) and the Unicode line and paragraph separators (@audit #277).
_HIDDEN_BREAK = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


def normalise(text: str) -> str:
    """Return ``text`` without a leading BOM and with CRLF (and lone CR) line ends as LF (R2)."""
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _scalar_problem(key: str, value: str) -> str:
    """Return why a front-matter value is not a one-line string scalar, or ``""``."""
    if value.startswith('"'):
        try:
            ok = isinstance(json.loads(value), str)
        except ValueError:
            ok = False
        return "" if ok else f"front matter {key!r}: unparseable double-quoted value"
    if value.startswith("'"):
        return "" if _SINGLE.fullmatch(value) else f"front matter {key!r}: unparseable single-quoted value"
    if not value or value.startswith(_INDICATORS) or ": " in value or " #" in value or value.endswith(":"):
        return f"front matter {key!r} must be a one-line string (plain or quoted)"
    return ""


def _scalar(value: str) -> str:
    """Decode a value :func:`_scalar_problem` accepted."""
    if value.startswith('"'):
        return str(json.loads(value))
    return value[1:-1].replace("''", "'") if value.startswith("'") else value


def split_front_matter(text: str) -> tuple[list[str], str, str]:
    """Return ``(front-matter lines, body, problem)`` of a normalised agent file (R2).

    Line 1 must be exactly ``---`` and the block is closed by the next line that is exactly ``---`` (``...`` is no
    closer); a block with nothing but blank and comment lines is refused. ``problem`` is ``""`` when it parses.
    """
    lines = text.split("\n")
    if lines[0] != "---":
        return [], "", "no front matter (line 1 must be exactly ---)"
    end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if end is None:
        return [], "", "unparseable front matter (no closing ---)"
    block = lines[1:end]
    if not any(ln.strip() and not ln.startswith("#") for ln in block):
        return [], "", "empty front matter (a description is required)"
    return block, "\n".join(lines[end + 1:]), ""


def front_matter_problem(text: str) -> str:
    """Return why a Copilot agent file is not one the CLI may read as it is, or ``""``.

    The file the CLI reads is the copy (:func:`copilot_copy`), and the self-test checks it with this
    (``copilot_agent_file_clean``). A strict subset of YAML, so no YAML library is needed and nothing ambiguous
    passes: :func:`split_front_matter`, then ``key: value`` lines at column 0 with one-line string values, blank lines
    and ``#`` comment lines. Anything else (an indented or continuation line, a block or flow value, an anchor, a
    duplicate key) is refused as unparseable, any key other than :data:`COPILOT_KEYS` as configuration, and a missing
    ``description``.
    """
    block, _body, why = split_front_matter(normalise(text))
    if why:
        return why
    seen: set[str] = set()
    for n, line in enumerate(block, start=2):
        if not line.strip() or line.startswith("#"):
            continue
        m = _KEY_LINE.fullmatch(line.rstrip())
        if not m:
            return f"unparseable front matter (line {n}: only 'key: value' lines at column 0 are accepted)"
        key = m.group(1)
        if key not in COPILOT_KEYS:
            return (f"front matter key {key!r} is configuration (Copilot reads tools, model, mcp-servers ... from it); "
                    f"only {', '.join(sorted(COPILOT_KEYS))} are accepted")
        if key in seen:
            return f"front matter key {key!r} appears twice"
        seen.add(key)
        problem = _scalar_problem(key, (m.group(2) or "").strip())
        if problem:
            return problem
    return "" if "description" in seen else "front matter has no 'description'"


@dataclass(frozen=True)
class AgentCopy:
    """The vulnscan-owned copy of an agent definition: its bytes, where it goes and the keys removed from it."""

    framework: str
    source: str
    data: bytes
    dropped: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        """The copy's sha256 (the fingerprint binds it; the self-test record carries it)."""
        return hashlib.sha256(self.data).hexdigest()

    @property
    def path(self) -> str:
        """The copy's path relative to :data:`AGENTDEF_DIR`."""
        return COPY_PATH[self.framework]

    def bound(self) -> dict[str, str]:
        """Return :func:`binding`'s value for this copy (a run re-checks that the copy is the one fingerprinted)."""
        return {"path": self.source, "sha256": self.sha256}

    def record(self) -> dict[str, Any]:
        """Return the run record's entry: the source path, the copy's sha256 and the removed keys (no content)."""
        return {"source": self.source, "copy": f"{AGENTDEF_DIR}/{self.path}", "sha256": self.sha256,
                "removed_keys": list(self.dropped)}


def copilot_copy(text: str, source: str = "") -> AgentCopy:
    """Return the copy Copilot reads: ``name: vuln-remediation``, the original ``description`` (JSON-quoted), the body.

    The source's front matter is read only for its ``description``; every other key is removed (``name`` is replaced,
    ``tools``, ``model``, ``handoffs`` ... are reported in :attr:`AgentCopy.dropped`). Each removed key only narrows
    what a run gets (vulnscan's flags and ``COPILOT_MODEL`` take precedence), so a removed key no longer refuses the run.
    Column-0 lines must be ``key:`` lines, comments or block-sequence items (``- ...``); indented lines continue the
    key above.

    Raises:
        ValueError: No front matter, an unterminated or empty block, an unparseable line, a duplicate key, or a
            missing, multi-line or non-string ``description``.
    """
    block, body, why = split_front_matter(normalise(text))
    if why:
        raise ValueError(why)
    keys: list[str] = []
    values: dict[str, str] = {}
    continued: set[str] = set()
    for n, line in enumerate(block, start=2):
        if not line.strip() or line.startswith("#"):
            continue
        if line[0] in " \t" or line == "-" or line.startswith("- "):
            if not keys:
                raise ValueError(f"unparseable front matter (line {n}: a continuation line before any key)")
            continued.add(keys[-1])
            continue
        m = _KEY_LINE.fullmatch(line.rstrip())
        if not m:
            raise ValueError(f"unparseable front matter (line {n}: only 'key: value' lines at column 0 are accepted)")
        if m.group(1) in values:
            raise ValueError(f"front matter key {m.group(1)!r} appears twice")
        keys.append(m.group(1))
        values[m.group(1)] = (m.group(2) or "").strip()
    if "description" not in values:
        raise ValueError("front matter has no 'description'")
    problem = _scalar_problem("description", values["description"])
    if problem or "description" in continued:
        raise ValueError(problem or "front matter 'description' must be a one-line string (plain or quoted)")
    head = f"---\nname: {COPILOT_NAME}\ndescription: {json.dumps(_scalar(values['description']))}\n---\n"
    dropped = tuple(k for k in keys if k not in COPILOT_KEYS)
    return AgentCopy("copilot", source, (head + body).encode("utf-8"), dropped)


@lru_cache(maxsize=1)
def rendered_goose_extensions() -> tuple[str, ...]:
    """Return the ``extensions`` block agentteams renders for the vuln-remediation recipe, normalised."""
    from agentteams.frameworks.goose_recipe_emit import _emit_recipe   # lazy: vulnscan imports no renderer otherwise

    recipe = _emit_recipe(title="t", description="d", instructions="i", extensions=list(GOOSE_EXTENSIONS))
    return _recipe_keys(recipe)[1]["extensions"]


def _recipe_keys(text: str) -> tuple[str, dict[str, tuple[str, ...]]]:
    """Return ``(problem, {key: normalised value lines})`` for a recipe's top level.

    Column-0 lines must be ``key:`` lines (plain keys, no quoting, flow, anchor, merge key, directive or document
    marker) or ``#`` comments; indented and blank lines continue the key above. A value is its key line's text plus
    the continuation lines, right-stripped, without blank and comment lines.
    """
    keys: dict[str, list[str]] = {}
    current: list[str] | None = None
    for n, line in enumerate(text.split("\n"), start=1):
        if line.startswith("#"):
            continue
        if not line.strip() or line[0] in " \t":
            if current is None and line.strip():
                return f"line {n}: an indented line before any key", {}
            if current is not None and line.strip() and not line.strip().startswith("#"):
                current.append(line.rstrip())
            continue
        m = _RECIPE_KEY.fullmatch(line.rstrip())
        if not m:
            return f"line {n}: only plain 'key:' lines and comments may start at column 0", {}
        if m.group(1) in keys:
            return f"key {m.group(1)!r} appears twice", {}
        current = keys[m.group(1)] = [m.group(2) or ""]
    return "", {k: tuple(v) for k, v in keys.items()}


def goose_recipe_problem(text: str) -> str:
    """Return why a goose recipe may not run, or ``""`` (R7, rc.4 core).

    Top-level keys must be in :data:`GOOSE_KEYS` (``settings``, ``sub_recipes``, ``retry``, ``response`` ... are
    refused), and ``extensions`` must be present and equal, line for line after normalisation, to the block agentteams
    renders for the vuln-remediation recipe (:func:`rendered_goose_extensions`); the vulnscan MCP server is added by
    the command (``--with-extension``), never by the recipe.
    """
    why, keys = _recipe_keys(normalise(text))
    if why:
        return f"unparseable recipe ({why})"
    extra = sorted(set(keys) - GOOSE_KEYS - {"extensions"})
    if extra:
        return (f"recipe key(s) {', '.join(map(repr, extra))} are not accepted (only "
                f"{', '.join(sorted(GOOSE_KEYS | {'extensions'}))})")
    if keys.get("extensions") != rendered_goose_extensions():
        return ("the recipe's extensions differ from the ones agentteams renders for it "
                f"({', '.join(GOOSE_EXTENSIONS)}); regenerate the team or restore the block")
    return ""


def _team_file(a: dict[str, Any], d: Driver) -> Path | None:
    team = str(a.get("team_dir") or "").strip()
    return Path(team).expanduser() / selected(a, d) if team else None


def read_source(a: dict[str, Any], d: Driver) -> str:
    """Read the selected agent definition from ``agent.team_dir`` strictly (R2) and return it normalised.

    Raises:
        ValueError: ``agent.team_dir`` is unset, or the file is absent, unreadable, not a regular file (a symlink
            too), outside the resolved team (a symlinked parent), larger than :data:`MAX_BYTES` or not UTF-8.
    """
    path, rel = _team_file(a, d), selected(a, d)
    if path is None:
        raise ValueError(f"agent.team_dir is not set (the {d.name} agent file is read from it)")
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"the {d.name} agent file {rel} is a symlink (a regular file is required)")
        if not stat.S_ISREG(mode):   # checked before opening: a FIFO would block the open
            raise ValueError(f"the {d.name} agent file {rel} is not a regular file")
        team = path.parents[len(Path(rel).parts) - 1].resolve(strict=True)
        if not path.resolve(strict=True).is_relative_to(team):
            raise ValueError(f"the {d.name} agent file {rel} resolves outside the team (a symlinked directory)")
        # a symlink swapped in after the checks fails here; O_NONBLOCK: a FIFO swapped in does not block the open
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"the {d.name} agent file {rel} is not a regular file")
            if st.st_size > MAX_BYTES:
                raise ValueError(f"the {d.name} agent file {rel} is larger than {MAX_BYTES} bytes")
            data = os.read(fd, MAX_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_BYTES:
            raise ValueError(f"the {d.name} agent file {rel} is larger than {MAX_BYTES} bytes")
        text = normalise(data.decode("utf-8"))
        bad = _HIDDEN_BREAK.search(text)
        if bad:   # a YAML reader breaks lines where this parser does not: a key could hide from the allowlist
            line = text.count("\n", 0, bad.start()) + 1
            raise ValueError(f"the {d.name} agent file {rel} has a control character or line separator "
                             f"(U+{ord(bad.group()):04X}) on line {line}; only LF and tab are allowed")
        return text
    except (OSError, UnicodeDecodeError) as exc:   # absent, a symlink (ELOOP), unreadable, not UTF-8: fail closed
        raise ValueError(f"the {d.name} agent file {rel} cannot be read ({type(exc).__name__})") from exc


def make_copy(a: dict[str, Any], d: Driver) -> AgentCopy | None:
    """Return the copy the CLI reads (None for claude and codex, which read the team's file as prompt text).

    Raises:
        ValueError: As :func:`read_source`, or the file is refused (:func:`copilot_copy`, :func:`goose_recipe_problem`).
    """
    if d.name not in CONTENT_BOUND:
        return None
    rel, text = selected(a, d), read_source(a, d)
    if d.name == "copilot":
        try:
            return copilot_copy(text, rel)
        except ValueError as exc:
            raise ValueError(f"the copilot agent file {rel} is refused: {exc}") from exc
    why = goose_recipe_problem(text)
    if why:
        raise ValueError(f"the goose recipe {rel} is refused: {why}")
    return AgentCopy("goose", rel, text.encode("utf-8"))


def problem(a: dict[str, Any], d: Driver) -> str:
    """Return why the selected agent definition may not run, or ``""`` (copilot and goose: :func:`make_copy`)."""
    try:
        make_copy(a, d)
    except ValueError as exc:
        return str(exc)
    return ""


def binding(a: dict[str, Any], d: Driver) -> str | dict[str, str]:
    """Return what ``flags_sha256`` binds of the selected agent definition (see the module table).

    The relative path for claude and codex (unchanged from the earlier ``default_recipe`` value when the default is
    selected, so their fingerprints do not move on this account); copilot and goose add the sha256 of the copy the
    CLI reads (``"refused"`` when there is none).
    """
    rel = selected(a, d)
    if d.name not in CONTENT_BOUND:
        return rel
    try:
        copy = make_copy(a, d)
    except ValueError:
        return {"path": rel, "sha256": "refused"}
    return {"path": rel, "sha256": copy.sha256 if copy else "refused"}


def in_container(a: dict[str, Any], d: Driver) -> str:
    """Return the absolute in-container path of the file the CLI is given: the copy, or the team's file."""
    if d.name in CONTENT_BOUND:
        return f"{AGENTDEF_IN_CONTAINER}/{COPY_PATH[d.name]}"
    from agentteams.vulnscan.agent_driver_base import TEAM_IN_CONTAINER

    return f"{TEAM_IN_CONTAINER}/{selected(a, d)}"


def write_copy(a: dict[str, Any], d: Driver, run_dir: Path) -> AgentCopy | None:
    """Write the copy into a fresh ``<run_dir>/agentdef`` (the file mode 0444, nothing else in the directory).

    Returns:
        The copy, or None (claude, codex: nothing is written).

    Raises:
        ValueError: As :func:`make_copy`.
        OSError: The directory cannot be replaced or the file written.
    """
    copy = make_copy(a, d)
    if copy is None:
        return None
    root = run_dir / AGENTDEF_DIR
    if root.is_symlink() or root.is_file():
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)
    target = root / copy.path
    target.parent.mkdir(parents=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, "wb") as fh:
        fh.write(copy.data)
    os.chmod(target, 0o444)   # whatever the umask
    return copy


def notice(copy: AgentCopy | None) -> str:
    """Return the operator's run-start line about the keys removed from the copy, or ``""``."""
    if copy is None or not copy.dropped:
        return ""
    return (f"agent definition {copy.source}: removed front-matter key(s) {', '.join(copy.dropped)} from the copy "
            f"the CLI reads (vulnscan's flags and settings apply instead)")

