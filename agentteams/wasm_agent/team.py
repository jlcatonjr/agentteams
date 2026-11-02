"""team.py — the rendered ``--framework wasm`` team: its on-disk format, its control-plane lock and its loader (P08).

``--framework wasm`` (``agentteams/frameworks/wasm.py``) renders, under the project root:

=====================================  ==========================================================================
``.agentteams-wasm/agents/<slug>.md``  front matter (exactly :data:`FRONT_MATTER_KEYS`) + the agent's prompt body
``.agentteams-wasm/TEAM.md``           team instructions, prepended to every agent's system prompt
``.agentteams-wasm/grants.json``       per-agent ``Grant(mounts, limits)`` (project-relative hosts) + ``read_only_via``
``.agentteams-wasm/runtime.json``      provider preset, tier -> model map (``null`` unless the brief sets one),
                                       budget, prices file, delegation settings (P09), ``host_os_confine``
``.agentteams-wasm/README.md``         how to run it, enforcement status (documentation; locked, never loaded)
``.agentteams/wasm-team.lock.json``    sha256 of every file above and of ``runtime.json``'s prices file (control
                                       plane; written by :func:`write_lock`)
=====================================  ==========================================================================

:func:`load_team` (the ``--run-wasm-agent`` launcher, P09's ``resolve_agent``) refuses any drift: a missing,
edited, extra or symlinked file, a path component that is not a real directory, an unknown front-matter key, a
tool outside the vocabulary or beyond what the agent's grant can serve, a ``delegates`` entry that is not a team
member, a grant mount that is not the canonical one for its agent. The fix for any refusal is to re-render.

The lock binds the team to what agentteams rendered. Relaxations (grant widening, OS off) were classified by the
P15 gate at render time (D7), so the runtime never reads the brief. Every read is nofollow and size-capped.
Rendered content is operator-reviewed configuration; model and guest output stay data (C-4).

Integrity-pinned. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteams.sandbox_state import SCRATCH_REL

#: The rendered team's directory and files (project-relative, POSIX).
TEAM_DIR_REL = ".agentteams-wasm"
AGENTS_REL = f"{TEAM_DIR_REL}/agents"
TEAM_MD_REL = f"{TEAM_DIR_REL}/TEAM.md"
GRANTS_REL = f"{TEAM_DIR_REL}/grants.json"
RUNTIME_REL = f"{TEAM_DIR_REL}/runtime.json"
README_REL = f"{TEAM_DIR_REL}/README.md"
TEAM_FILES_REL: tuple[str, ...] = (TEAM_MD_REL, GRANTS_REL, RUNTIME_REL, README_REL)
#: The control-plane lock (session-write-denied whenever WASM is on, R1).
LOCK_REL = ".agentteams/wasm-team.lock.json"
LOCK_VERSION = 2
FILE_VERSION = 1
#: Exact front-matter keys of an agent file, in order.
FRONT_MATTER_KEYS: tuple[str, ...] = ("name", "description", "tools", "model", "delegates")
#: The one non-agent Markdown file the generator writes into every agents dir (never loaded; see :func:`load_team`).
NON_AGENT_FILES = frozenset({"SETUP-REQUIRED.md"})
#: ``host_os_confine`` values: Stage 0 wraps the host when the OS sandbox is on (``auto``), never when it is off.
HOST_OS_CONFINE = ("auto", "off")
#: Size caps.
MAX_AGENT_BYTES = 256 << 10
MAX_TEAM_MD_BYTES = 1 << 20
MAX_JSON_BYTES = 256 << 10
MAX_LOCK_BYTES = 1 << 20
#: Model tiers (``model_providers.TIERS``; pinned equal by a test).
TIERS = ("primary", "cheap")
#: Provider presets (``model_providers.PROVIDERS``; pinned equal by a test, also the schema enum).
PROVIDER_NAMES = ("anthropic", "openai", "openrouter", "xai", "ollama", "lmstudio", "openai-compatible", "gemini",
                  "stub")
#: The brief's ``wasm_runtime`` keys (``project-description.schema.json``).
BRIEF_RUNTIME_KEYS = ("provider", "base_url", "models", "budget", "prices_file", "delegation")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LIST_RE = re.compile(r"^\[((?:[a-z0-9_-]+(?:, [a-z0-9_-]+)*)?)\]$")
_REL_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_SCRATCH_PREFIX = f"{SCRATCH_REL}/"
#: The operator's run-time base URL (with ``--wasm-base-url``, the only path for a keyed preset; @security S5).
BASE_URL_ENV = "AGENTTEAMS_WASM_BASE_URL"


class TeamError(Exception):
    """The rendered WASM team is missing, drifted or malformed (re-render it with ``--framework wasm``)."""


# --- the agent-file format (rendered by frameworks/wasm.py, parsed here) ----------------------------------------

def render_front_matter(*, name: str, description: str, tools: Iterable[str], model: str,
                        delegates: Iterable[str]) -> str:
    """Return an agent file's front matter (exactly :data:`FRONT_MATTER_KEYS`, one line each).

    Args:
        name: Display name (JSON-quoted, so no value can inject a key).
        description: One-line description (JSON-quoted).
        tools: WASM tool names.
        model: The tier (``primary`` / ``cheap``).
        delegates: Team slugs this agent may delegate to (P09).

    Returns:
        ``---`` ... ``---`` plus a trailing newline.

    Raises:
        ValueError: A tier, tool or slug that is not a plain name.
    """
    if model not in TIERS:
        raise ValueError(f"model tier {model!r} must be one of {TIERS}")
    items = [list(tools), list(delegates)]
    for seq in items:
        if any(not re.fullmatch(r"[a-z0-9_-]+", x) for x in seq):
            raise ValueError(f"front-matter list entries must be plain names: {seq!r}")
    return ("---\n" f"name: {json.dumps(name, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"tools: [{', '.join(items[0])}]\n" f"model: {model}\n" f"delegates: [{', '.join(items[1])}]\n" "---\n")


@dataclass(frozen=True)
class AgentFile:
    """One parsed agent file."""

    slug: str
    name: str
    description: str
    tools: tuple[str, ...]
    model: str
    delegates: tuple[str, ...]
    body: str


def _json_str(value: str, key: str, where: str) -> str:
    try:
        out = json.loads(value)
    except ValueError as exc:
        raise TeamError(f"{where}: {key} must be a JSON string") from exc
    if not isinstance(out, str) or "\n" in out:
        raise TeamError(f"{where}: {key} must be a one-line JSON string")
    return out


def parse_agent_file(slug: str, text: str, *, where: str = "") -> AgentFile:
    """Parse an agent file strictly: exactly :data:`FRONT_MATTER_KEYS`, in order, one line each.

    Args:
        slug: The file's slug.
        text: The file text.
        where: The path for messages.

    Returns:
        The parsed file.

    Raises:
        TeamError: Any deviation from the rendered format.
    """
    where = where or slug
    lines = text.split("\n")
    n = len(FRONT_MATTER_KEYS)
    if len(lines) < n + 2 or lines[0] != "---" or lines[n + 1] != "---":
        raise TeamError(f"{where}: front matter must be exactly {', '.join(FRONT_MATTER_KEYS)} between --- lines")
    values: dict[str, str] = {}
    for key, line in zip(FRONT_MATTER_KEYS, lines[1:n + 1]):
        if not line.startswith(f"{key}: "):
            raise TeamError(f"{where}: expected front-matter key {key!r}, found {line[:40]!r}")
        values[key] = line[len(key) + 2:]
    lists = {}
    for key in ("tools", "delegates"):
        m = _LIST_RE.match(values[key])
        if m is None:
            raise TeamError(f"{where}: {key} must be a flow list of plain names")
        lists[key] = tuple(x for x in m.group(1).split(", ") if x)
        if len(set(lists[key])) != len(lists[key]):
            raise TeamError(f"{where}: {key} lists a name twice")
    if values["model"] not in TIERS:
        raise TeamError(f"{where}: model must be a tier ({', '.join(TIERS)}), not a model id")
    return AgentFile(slug=slug, name=_json_str(values["name"], "name", where),
                     description=_json_str(values["description"], "description", where), tools=lists["tools"],
                     model=values["model"], delegates=lists["delegates"], body="\n".join(lines[n + 2:]))


def dumps(obj: Any) -> str:
    """The one JSON encoding of every rendered team file (byte-stable: sorted keys, two-space indent).

    Args:
        obj: A JSON value.

    Returns:
        The text with a trailing newline.

    Raises:
        TypeError: A non-JSON value.
    """
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


# --- the brief's wasm_runtime block (analyze) and runtime.json -----------------------------------------------------

def _rel_path(value: object, where: str) -> str:
    if not isinstance(value, str) or not _REL_RE.match(value) or any(p in (".", "..") for p in value.split("/")):
        raise ValueError(f"{where} must be a plain project-relative path (no '..', not absolute)")
    return value


def _keyless_local(provider: str | None, url: str) -> str | None:
    """Return why *url* may not serve *provider* without operator say-so, or None when it may.

    It may only when the preset sends no key and is not the stub, and the URL passes P06's shape policy with a
    loopback host: then no key can follow the URL, whoever wrote it.
    """
    from agentteams import model_providers as MP

    if provider is None or provider == "stub" or MP.preset_needs_key(provider):
        return (f"provider {provider or '(unset)'!r} sends an API key (or is the stub), so its base URL comes only "
                f"from the operator at run time ({BASE_URL_ENV} or --wasm-base-url), never from the brief")
    try:
        MP.check_base_url(url)
    except MP.ModelProviderError as exc:
        return exc.message
    if not MP.is_loopback_url(url):
        return "a brief-set base URL must name a loopback host (127.0.0.1, ::1, localhost)"
    return None


def _brief_base_url(provider: str | None, url: object) -> str:
    """Vet the brief's ``wasm_runtime.base_url``: only a keyless local preset on a loopback host (@security S5)."""
    from agentteams import model_providers as MP

    if not isinstance(url, str):
        raise ValueError("wasm_runtime.base_url must be a string")
    why = _keyless_local(provider, url)
    if why is not None:
        raise ValueError(f"wasm_runtime.base_url is refused: {why}. The provider key is sent to this host; a brief "
                         "edit must never choose where it goes.")
    return MP.check_base_url(url)


def resolve_base_url(runtime: Mapping[str, Any], provider: str, *, operator_url: str | None) -> tuple[str | None,
                                                                                                         str | None]:
    """Return the base URL a run uses for *provider*, and a notice when the rendered one was dropped.

    The operator's URL (``--wasm-base-url``, else :data:`BASE_URL_ENV`) wins and is shape-checked. Otherwise the
    rendered ``runtime.json`` URL is kept only while the keyless local rule holds for the run's effective preset
    (``--wasm-provider`` re-applies it): a keyed preset never inherits a rendered URL, so its key goes to its own
    preset host. With neither, the preset's own URL applies (None).

    Args:
        runtime: The loaded ``runtime.json``.
        provider: The run's effective preset.
        operator_url: The operator's URL, or None.

    Returns:
        ``(base_url or None, notice or None)``.

    Raises:
        TeamError: The operator URL fails P06's policy, or is set for the stub.
    """
    from agentteams import model_providers as MP

    if operator_url is not None:
        if provider == "stub":
            raise TeamError("an operator base URL is not valid for the stub provider")
        try:
            return MP.check_base_url(operator_url), None
        except MP.ModelProviderError as exc:
            raise TeamError(f"operator base URL ({BASE_URL_ENV} / --wasm-base-url): {exc.message}") from exc
    rendered = runtime.get("base_url")
    if rendered is None:
        return None, None
    why = _keyless_local(provider, rendered)
    if why is None:
        return MP.check_base_url(rendered), None
    return None, (f"the rendered wasm_runtime.base_url is not used for provider {provider!r} ({why}); the preset's "
                  f"own URL applies. Set {BASE_URL_ENV} or --wasm-base-url to choose another.")


def normalize_wasm_runtime(raw: object) -> dict[str, Any]:
    """Validate the brief's ``wasm_runtime`` block (fail closed; names the key path).

    Args:
        raw: The brief value.

    Returns:
        ``{"provider", "base_url", "models": {"primary", "cheap"}, "budget", "prices_file", "delegation"}`` with
        ``None`` for anything unset, the budget filled with the ``Budget`` defaults and ``delegation``
        (``default_share``, ``max_tree_runs``; P09) with its defaults, clamped to the hard caps.

    Raises:
        ValueError: Unknown keys, wrong types, an unknown provider, a base URL that is not a keyless local preset's
            loopback URL (a keyed preset's URL comes only from the operator: :data:`BASE_URL_ENV` /
            ``--wasm-base-url``), an invalid budget or delegation block, or a prices file that is not a project-relative path.
    """
    from agentteams.wasm_agent.budget import Budget
    from agentteams.wasm_agent.delegation import normalize_delegation

    if not isinstance(raw, dict):
        raise ValueError("wasm_runtime must be an object")
    extra = sorted(set(raw) - set(BRIEF_RUNTIME_KEYS))
    if extra:
        raise ValueError(f"wasm_runtime: unknown key(s) {extra}; allowed: {', '.join(BRIEF_RUNTIME_KEYS)}")
    provider = raw.get("provider")
    if provider is not None and provider not in PROVIDER_NAMES:
        raise ValueError(f"wasm_runtime.provider must be one of {', '.join(PROVIDER_NAMES)}")
    base_url = raw.get("base_url")
    if base_url is not None:
        base_url = _brief_base_url(provider, base_url)
    models_raw = raw.get("models", {})
    if not isinstance(models_raw, dict) or set(models_raw) - set(TIERS):
        raise ValueError(f"wasm_runtime.models must be an object with only {', '.join(TIERS)}")
    models: dict[str, str | None] = {}
    for tier in TIERS:
        value = models_raw.get(tier)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200
                                  or any(c.isspace() for c in value)):
            raise ValueError(f"wasm_runtime.models.{tier} must be a model id string (no whitespace)")
        models[tier] = value
    try:
        budget = Budget.from_dict(raw.get("budget", {})).to_dict()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"wasm_runtime.budget: {exc}") from exc
    prices = raw.get("prices_file")
    if prices is not None:
        prices = _rel_path(prices, "wasm_runtime.prices_file")
    delegation = normalize_delegation(raw.get("delegation"))
    return {"provider": provider, "base_url": base_url, "models": models, "budget": budget, "prices_file": prices,
            "delegation": delegation}


def runtime_record(brief_block: Mapping[str, Any] | None, *, os_on: bool) -> dict[str, Any]:
    """Return ``runtime.json``'s content.

    Args:
        brief_block: :func:`normalize_wasm_runtime` output (``manifest["wasm_runtime"]``), or None.
        os_on: The effective OS sandbox switch (``host_os_confine`` ``auto`` when on, ``off`` when off).

    Returns:
        The record (no key, no secret: provider keys come only from Stage 0's environment).

    Raises:
        ValueError: A malformed block.
    """
    block = normalize_wasm_runtime(dict(brief_block) if brief_block is not None else {})
    return {"version": FILE_VERSION, **block, "host_os_confine": "auto" if os_on else "off"}


def check_runtime(data: object) -> dict[str, Any]:
    """Validate a loaded ``runtime.json`` (the exact :func:`runtime_record` shape).

    Args:
        data: The decoded JSON.

    Returns:
        The record.

    Raises:
        TeamError: Any deviation.
    """
    keys = {"version", *BRIEF_RUNTIME_KEYS, "host_os_confine"}
    if not isinstance(data, dict) or set(data) != keys or data["version"] != FILE_VERSION:
        raise TeamError(f"{RUNTIME_REL}: must be exactly {sorted(keys)} (version {FILE_VERSION})")
    if data["host_os_confine"] not in HOST_OS_CONFINE:
        raise TeamError(f"{RUNTIME_REL}: host_os_confine must be one of {HOST_OS_CONFINE}")
    try:
        block = normalize_wasm_runtime({k: data[k] for k in BRIEF_RUNTIME_KEYS})
    except ValueError as exc:
        raise TeamError(f"{RUNTIME_REL}: {exc}") from exc
    if block != {k: data[k] for k in BRIEF_RUNTIME_KEYS}:
        raise TeamError(f"{RUNTIME_REL}: not in normalised form (re-render)")
    return data


# --- grants.json -------------------------------------------------------------------------------------------------

def grant_entry(slug: str, grant: Any, *, project_root: Path, read_only_via: str) -> dict[str, Any]:
    """Return one agent's ``grants.json`` entry (host paths project-relative).

    Args:
        slug: The agent.
        grant: Its ``wasm_exec.Grant``.
        project_root: The root its absolute mount hosts are under.
        read_only_via: ``os`` / ``snapshot`` / ``none`` (no read-only mount).

    Returns:
        ``{"mounts": [{host, guest, mode}], "limits": {...}, "read_only_via": ...}``.

    Raises:
        ValueError: A mount host outside the project root.
    """
    root = os.path.realpath(str(project_root))
    mounts = []
    for m in grant.mounts:
        rel = os.path.relpath(os.path.abspath(m.host), root)
        if rel == "." or rel.startswith(".."):
            raise ValueError(f"{slug}: mount {m.guest} host is outside the project root")
        mounts.append({"host": Path(rel).as_posix(), "guest": m.guest, "mode": m.mode})
    return {"mounts": mounts, "limits": {"fuel": grant.limits.fuel, "max_memory_bytes": grant.limits.max_memory_bytes,
                                         "timeout_s": grant.limits.timeout_s}, "read_only_via": read_only_via}


def grant_from_entry(slug: str, entry: object, *, project_root: Path) -> Any:
    """Rebuild and re-validate an agent's grant from its ``grants.json`` entry.

    Only the canonical mounts are accepted: ``/project/<rel>`` read-only at ``<root>/<rel>`` and ``/scratch``
    read-write at ``<root>/.agentteams/wasm/scratch/<slug>``. They go through the one grant builder
    (``sandbox_state.grant_from_tokens``: symlink checks and, again at every load, the project's credential scan
    ``_sandbox_profiles.refuse_credentials`` over each read directory), and the limits through ``Limits``. A read
    path whose own segments are credential-named (``_sandbox_profiles.credential_name``) is refused too, so a
    re-hashed ``grants.json`` cannot mount ``.ssh`` or a ``.env`` file directly.

    Args:
        slug: The agent.
        entry: The decoded entry.
        project_root: The project root.

    Returns:
        The ``wasm_exec.Grant``.

    Raises:
        TeamError: A malformed entry, a mount the brief could never have granted, or a credential hit.
    """
    from agentteams import _sandbox_profiles as SP
    from agentteams import wasm_exec as W
    from agentteams.sandbox_state import grant_from_tokens
    from agentteams.sandbox_tokens import read_token, scratch_token

    if (not isinstance(entry, dict) or set(entry) != {"mounts", "limits", "read_only_via"}
            or not isinstance(entry["mounts"], list) or entry["read_only_via"] not in ("os", "snapshot", "none")):
        raise TeamError(f"{GRANTS_REL}: {slug}: entry must be {{mounts, limits, read_only_via}}")
    if not isinstance(entry["limits"], dict):
        raise TeamError(f"{GRANTS_REL}: {slug}: limits must be an object")
    tokens = []
    for m in entry["mounts"]:
        if not isinstance(m, dict) or set(m) != {"host", "guest", "mode"} or not all(
                isinstance(m[k], str) for k in m):
            raise TeamError(f"{GRANTS_REL}: {slug}: a mount must be {{host, guest, mode}} strings")
        if m["guest"] == "/scratch" and m["mode"] == "rw" and m["host"] == _SCRATCH_PREFIX + slug:
            tokens.append(scratch_token(slug))
        elif m["guest"] == f"/project/{m['host']}" and m["mode"] == "ro":
            if any(SP.credential_name(part) for part in m["host"].split("/")):
                raise TeamError(f"{GRANTS_REL}: {slug}: read path {m['host']!r} is credential-named; refused")
            try:
                tokens.append(read_token(slug, m["host"]))
            except ValueError as exc:
                raise TeamError(f"{GRANTS_REL}: {slug}: {exc}") from exc
        else:
            raise TeamError(f"{GRANTS_REL}: {slug}: mount {m['guest']!r} is not a canonical grant mount")
    try:
        grant = grant_from_tokens(slug, tokens, W.Limits(**entry["limits"]), project_root=project_root,
                                  check_credentials=True)
    except (TypeError, ValueError, W.WasmExecError) as exc:
        raise TeamError(f"{GRANTS_REL}: {slug}: {exc}") from exc
    if len(grant.mounts) != len(entry["mounts"]):
        raise TeamError(f"{GRANTS_REL}: {slug}: duplicate mounts")
    return grant


# --- custody reads and the lock ---------------------------------------------------------------------------------

def _real_dirs(root: str, rel: str) -> None:
    """Refuse a symlink or non-directory at any component of ``rel``'s parent directories (under *root*)."""
    path = root
    for part in rel.split("/")[:-1]:
        path = os.path.join(path, part)
        try:
            st = os.lstat(path)
        except OSError as exc:
            raise TeamError(f"{rel}: missing ({exc.strerror}); render the team with --framework wasm first") from exc
        if not stat.S_ISDIR(st.st_mode):
            raise TeamError(f"{rel}: {path} is not a real directory (symlink?); refused")


def read_locked(root: str, rel: str, cap: int) -> bytes:
    """Read one team file: real parent directories, no symlink, a regular file, at most *cap* bytes.

    Args:
        root: The project root (real path).
        rel: The project-relative path.
        cap: The size cap.

    Returns:
        The bytes.

    Raises:
        TeamError: Any custody failure.
    """
    from agentteams.atomicio import read_regular_nofollow

    _real_dirs(root, rel)
    try:
        return read_regular_nofollow(Path(root) / rel, cap)
    except (OSError, ValueError) as exc:
        raise TeamError(f"{rel}: unreadable ({exc}); a symlink or non-regular file is refused") from exc


def _cap(rel: str) -> int:
    if rel.startswith(AGENTS_REL + "/"):
        return MAX_AGENT_BYTES
    return MAX_TEAM_MD_BYTES if rel in (TEAM_MD_REL, README_REL) else MAX_JSON_BYTES


def agent_files_on_disk(root: str) -> list[str]:
    """List the agent files (``*.md`` directly in the agents dir, minus :data:`NON_AGENT_FILES`).

    Args:
        root: The project root.

    Returns:
        Their project-relative paths, sorted.

    Raises:
        TeamError: The agents dir is missing or not a real directory.
    """
    _real_dirs(root, AGENTS_REL + "/x")
    try:
        names = sorted(e.name for e in os.scandir(os.path.join(root, AGENTS_REL)))
    except OSError as exc:
        raise TeamError(f"{AGENTS_REL}: unreadable ({exc.strerror})") from exc
    return [f"{AGENTS_REL}/{n}" for n in names if n.endswith(".md") and n not in NON_AGENT_FILES]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prices_rel(runtime_bytes: bytes) -> str | None:
    """The ``prices_file`` a ``runtime.json`` names (None when unset); TeamError when the file is not a JSON object."""
    try:
        runtime = json.loads(runtime_bytes)
    except ValueError as exc:
        raise TeamError(f"{RUNTIME_REL}: not JSON") from exc
    rel = runtime.get("prices_file") if isinstance(runtime, dict) else None
    if rel is not None and (not isinstance(rel, str) or not _REL_RE.match(rel)
                            or any(p in (".", "..") for p in rel.split("/"))):
        raise TeamError(f"{RUNTIME_REL}: prices_file must be a plain project-relative path")
    return rel


def write_lock(project_root: str | Path) -> Path:
    """Hash the rendered team into the control-plane lock (after a successful emit).

    Args:
        project_root: The project root.

    Returns:
        The lock path.

    Raises:
        TeamError: A team file (or ``runtime.json``'s prices file) is missing or fails a custody check, or an agent
            file name is not a slug.
        OSError: The lock cannot be written.
    """
    from agentteams.atomicio import write_new_atomic
    from agentteams.wasm_agent.guest_source import GUEST_SHA256
    from agentteams.wasm_agent.tools import TOOL_SPEC_SHA256
    from agentteams.wasm_agent.transcript import JournalError, ensure_dir

    root = os.path.realpath(str(project_root))
    rels = agent_files_on_disk(root)
    slugs = [Path(r).stem for r in rels]
    bad = [s for s in slugs if not _SLUG_RE.match(s)]
    if bad:
        raise TeamError(f"{AGENTS_REL}: agent file names must be slugs: {bad}")
    data = {rel: read_locked(root, rel, _cap(rel)) for rel in [*rels, *TEAM_FILES_REL]}
    prices_rel = _prices_rel(data[RUNTIME_REL])
    prices = None if prices_rel is None else {"path": prices_rel,
                                              "sha256": _sha(read_locked(root, prices_rel, MAX_JSON_BYTES))}
    lock = {"version": LOCK_VERSION, "agents": slugs, "files": {rel: _sha(b) for rel, b in data.items()},
            "prices": prices, "guest_sha256": GUEST_SHA256, "tool_spec_sha256": TOOL_SPEC_SHA256}
    try:
        lock_dir = ensure_dir(root, (".agentteams",))
    except JournalError as exc:
        raise TeamError(str(exc)) from exc
    return write_new_atomic(Path(lock_dir), Path(LOCK_REL).name, dumps(lock).encode("utf-8"), mode=0o644)


def _verify_prices(root: str, locked: Mapping[str, Any] | None, out: dict[str, bytes]) -> list[str]:
    """Check ``runtime.json``'s prices file against the lock (C3); add its bytes to *out*; return the drift."""
    try:
        rel = _prices_rel(out[RUNTIME_REL]) if RUNTIME_REL in out else None
    except TeamError as exc:
        return [str(exc)]
    if rel is None:
        return [] if locked is None else [f"{RUNTIME_REL} (names no prices file, the lock does)"]
    if locked is None or locked["path"] != rel:
        return [f"{rel} (the prices file is not the locked one)"]
    try:
        data = read_locked(root, rel, MAX_JSON_BYTES)
    except TeamError as exc:
        return [f"{rel} ({str(exc).split(': ', 1)[-1][:80]})"]
    if _sha(data) != locked["sha256"]:
        return [f"{rel} (prices changed)"]
    out[rel] = data
    return []


def verify_lock(project_root: str | Path) -> dict[str, bytes]:
    """Re-hash the team against its lock and return every locked file's bytes.

    Args:
        project_root: The project root.

    Returns:
        ``{rel: bytes}`` for every locked file (the prices file, when ``runtime.json`` names one, included).

    Raises:
        TeamError: No lock, a malformed lock, or any drift (listed by path).
    """
    from agentteams.wasm_agent.guest_source import GUEST_SHA256
    from agentteams.wasm_agent.tools import TOOL_SPEC_SHA256

    root = os.path.realpath(str(project_root))
    try:
        lock = json.loads(read_locked(root, LOCK_REL, MAX_LOCK_BYTES))
    except TeamError as exc:
        raise TeamError(f"no usable team lock ({exc}); render the team with --framework wasm first") from exc
    except ValueError as exc:
        raise TeamError(f"{LOCK_REL}: not JSON") from exc
    if (not isinstance(lock, dict)
            or set(lock) != {"version", "agents", "files", "prices", "guest_sha256", "tool_spec_sha256"}
            or lock["version"] != LOCK_VERSION or not isinstance(lock["files"], dict)
            or not isinstance(lock["agents"], list) or not (lock["prices"] is None or (
                isinstance(lock["prices"], dict) and set(lock["prices"]) == {"path", "sha256"}))):
        raise TeamError(f"{LOCK_REL}: malformed lock; re-render the team")
    if lock["guest_sha256"] != GUEST_SHA256 or lock["tool_spec_sha256"] != TOOL_SPEC_SHA256:
        raise TeamError("the team was rendered for a different agentteams guest/tool version; re-render (--update)")
    expected = {f"{AGENTS_REL}/{s}.md" for s in lock["agents"] if isinstance(s, str)} | set(TEAM_FILES_REL)
    if set(lock["files"]) != expected:
        raise TeamError(f"{LOCK_REL}: file list does not match its agent list; re-render the team")
    drift: list[str] = []
    on_disk = set(agent_files_on_disk(root))
    drift += [f"{rel} (not in the lock)" for rel in sorted(on_disk - expected)]
    out: dict[str, bytes] = {}
    for rel, digest in sorted(lock["files"].items()):
        try:
            data = read_locked(root, rel, _cap(rel))
        except TeamError as exc:
            drift.append(f"{rel} ({str(exc).split(': ', 1)[-1][:80]})")
            continue
        if _sha(data) != digest:
            drift.append(f"{rel} (changed)")
        out[rel] = data
    drift += _verify_prices(root, lock["prices"], out)
    if drift:
        raise TeamError("the WASM team drifted from its lock: " + "; ".join(drift)
                        + ". Re-render it (--framework wasm --update) and review the change.")
    return out


# --- the loaded team ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Team:
    """A lock-verified WASM team."""

    project_root: Path
    agents: Mapping[str, AgentFile]
    team_md: str
    grants: Mapping[str, Any]
    grant_entries: Mapping[str, Mapping[str, Any]]
    runtime: Mapping[str, Any]
    agent_bytes: Mapping[str, bytes]
    prices_bytes: bytes | None = None

    def model_for(self, slug: str, provider: str, environ: Mapping[str, str] | None = None) -> str:
        """Resolve *slug*'s model: ``AGENTTEAMS_WASM_MODEL_<TIER>`` > ``runtime.json`` > refuse (no default ids).

        Args:
            slug: The agent.
            provider: The provider preset (``stub`` needs no model and gets the label ``stub``).
            environ: The environment (default ``os.environ``).

        Returns:
            The model id.

        Raises:
            TeamError: No model configured for the agent's tier.
        """
        from agentteams import model_providers as MP

        tier = self.agents[slug].model
        env = os.environ if environ is None else environ
        if provider == "stub" and not (env.get(MP.TIER_ENV_PREFIX + tier.upper()) or self.runtime["models"][tier]):
            return "stub"
        try:
            return MP.model_for_tier(tier, self.runtime["models"], environ=env)
        except MP.ModelProviderError as exc:
            raise TeamError(f"no model configured for tier {tier!r}: set wasm_runtime.models.{tier} in the brief or "
                            f"{MP.TIER_ENV_PREFIX}{tier.upper()}") from exc

    def agent_spec(self, slug: str, *, provider: str | None = None, environ: Mapping[str, str] | None = None) -> Any:
        """Build the host's ``AgentSpec`` for *slug*.

        Args:
            slug: The agent.
            provider: A preset overriding ``runtime.json`` (``--wasm-provider``), or None.
            environ: The environment for the tier override (default ``os.environ``).

        Returns:
            The ``wasm_agent.host.AgentSpec``.

        Raises:
            TeamError: Unknown agent, no provider, an unknown provider, or no model for the tier.
        """
        from agentteams.wasm_agent.budget import Budget
        from agentteams.wasm_agent.host import AgentSpec

        if slug not in self.agents:
            raise TeamError(f"no agent {slug!r} in the WASM team; agents: {', '.join(sorted(self.agents))}")
        name = provider or self.runtime["provider"]
        if name is None:
            raise TeamError("no model provider configured: set wasm_runtime.provider in the brief or pass "
                            "--wasm-provider NAME")
        if name not in PROVIDER_NAMES:
            raise TeamError(f"unknown provider {name!r}; known: {', '.join(PROVIDER_NAMES)}")
        agent = self.agents[slug]
        grant = self.grants[slug]
        digest = hashlib.sha256(self.agent_bytes[slug] + b"\0" + self.team_md.encode("utf-8") + b"\0"
                                + dumps(self.grant_entries[slug]).encode("utf-8")).hexdigest()
        prompt = f"{self.team_md.rstrip()}\n\n{agent.body.strip()}\n\n{runtime_section(agent, grant)}"
        return AgentSpec(slug=slug, system_prompt=prompt, tools=frozenset(agent.tools),
                         delegates=frozenset(agent.delegates), grant=grant, provider=name,
                         model=self.model_for(slug, name, environ),
                         max_tokens=Budget.from_dict(self.runtime["budget"]).max_output_tokens_per_call,
                         spec_sha256=digest)

    def prices(self) -> dict[str, dict[str, float]] | None:
        """The run's price table: the lock-verified bytes of ``runtime.json``'s ``prices_file`` (C3), parsed.

        Returns:
            The ``gateway_dialects`` table, or None when no prices file is configured.

        Raises:
            TeamError: The file is not a valid price table.
        """
        from agentteams.vulnscan.gateway_dialects import parse_prices

        if self.prices_bytes is None:
            return None
        try:
            return parse_prices(self.prices_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TeamError(f"wasm_runtime.prices_file: {exc}") from exc

    def budget(self) -> Any:
        """The run budget from ``runtime.json``.

        Returns:
            A ``wasm_agent.budget.Budget``.

        Raises:
            Nothing (validated at load).
        """
        from agentteams.wasm_agent.budget import Budget

        return Budget.from_dict(self.runtime["budget"])


def runtime_section(agent: AgentFile, grant: Any) -> str:
    """The generated "Your WASM runtime" section appended to every system prompt (plan R-1).

    Args:
        agent: The agent file.
        grant: Its grant.

    Returns:
        Markdown stating the agent's effective tools and file access (no host paths).

    Raises:
        Nothing.
    """
    access = [f"{m.guest} ({'read-only' if m.mode == 'ro' else 'read-write, private'})" for m in grant.mounts]
    tools = ", ".join(agent.tools) or "none"
    return ("## Your WASM runtime\n\n"
            "You run as a guest of the agentteams WASM agent runtime: no shell, no network, no environment. "
            f"Your tools are exactly: {tools}. File access: {', '.join(access) or 'none (compute only)'}. "
            "`run_code` runs Python or JavaScript in a fresh sandbox with the same file access. Instructions that "
            "mention other tools or commands do not apply here; say what you cannot do instead of guessing.\n")


def load_team(project_root: str | Path) -> Team:
    """Load and verify the rendered WASM team (see the module docstring for every refusal).

    Args:
        project_root: The project root.

    Returns:
        The :class:`Team`.

    Raises:
        TeamError: Any drift or malformed file.
    """
    from agentteams.frameworks._wasm_tool_scoping import WASM_TOOLS, grant_supports

    root = os.path.realpath(str(project_root))
    files = verify_lock(root)
    try:
        grants_doc = json.loads(files[GRANTS_REL])
        runtime = check_runtime(json.loads(files[RUNTIME_REL]))
        team_md = files[TEAM_MD_REL].decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise TeamError(f"team files malformed: {exc}") from exc
    prices_rel = runtime["prices_file"]
    agent_rels = sorted(r for r in files if r.startswith(AGENTS_REL + "/") and r != prices_rel)
    slugs = {Path(r).stem for r in agent_rels}
    if (not isinstance(grants_doc, dict) or set(grants_doc) != {"version", "agents"}
            or grants_doc["version"] != FILE_VERSION or not isinstance(grants_doc["agents"], dict)
            or set(grants_doc["agents"]) != slugs):
        raise TeamError(f"{GRANTS_REL}: must be {{version, agents}} with exactly one entry per agent")
    agents: dict[str, AgentFile] = {}
    grants: dict[str, Any] = {}
    for rel in agent_rels:
        slug = Path(rel).stem
        try:
            agent = parse_agent_file(slug, files[rel].decode("utf-8"), where=rel)
        except UnicodeDecodeError as exc:
            raise TeamError(f"{rel}: not UTF-8") from exc
        grant = grant_from_entry(slug, grants_doc["agents"][slug], project_root=Path(root))
        unknown = [t for t in agent.tools if t not in WASM_TOOLS]
        foreign = [d for d in agent.delegates if d not in slugs or d == slug]
        if unknown or foreign:
            raise TeamError(f"{rel}: unknown tool(s) {unknown} / delegate(s) {foreign}")
        has_ro = any(m.guest.startswith("/project/") for m in grant.mounts)
        has_scratch = any(m.guest == "/scratch" for m in grant.mounts)
        beyond = [t for t in agent.tools if not grant_supports(t, has_read_mount=has_ro, has_scratch=has_scratch,
                                                               has_delegates=bool(agent.delegates))]
        if beyond:
            raise TeamError(f"{rel}: tool(s) {beyond} exceed what its grant can serve; re-render the team")
        agents[slug], grants[slug] = agent, grant
    return Team(project_root=Path(root), agents=agents, team_md=team_md, grants=grants,
                grant_entries=dict(grants_doc["agents"]), runtime=runtime,
                agent_bytes={Path(r).stem: files[r] for r in agent_rels},
                prices_bytes=files.get(prices_rel) if prices_rel is not None else None)


__all__ = [
    "AGENTS_REL", "BASE_URL_ENV", "FRONT_MATTER_KEYS", "GRANTS_REL", "HOST_OS_CONFINE", "LOCK_REL", "NON_AGENT_FILES",
    "PROVIDER_NAMES", "README_REL", "RUNTIME_REL", "TEAM_DIR_REL", "TEAM_FILES_REL", "TEAM_MD_REL", "TIERS",
    "AgentFile", "Team", "TeamError", "agent_files_on_disk", "check_runtime", "dumps", "grant_entry",
    "grant_from_entry", "load_team", "normalize_wasm_runtime", "parse_agent_file", "read_locked",
    "render_front_matter", "resolve_base_url", "runtime_record", "runtime_section", "verify_lock", "write_lock",
]
