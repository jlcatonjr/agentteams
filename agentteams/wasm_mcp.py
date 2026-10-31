"""wasm_mcp.py — install and launch facts for the ``agentteams_wasm`` MCP server (WASM sandbox plan P10, D8).

The server (``agentteams/data/agentteams-wasm-mcp.py``) gives an agent on any MCP-capable vendor CLI exactly one tool,
``run_wasm``: run a prebuilt core module, Python or JavaScript inside the pinned wasmtime under that agent's WASM grant
(compute-only by default, D5). It needs no runner. MCP carries no caller identity, so **the instance is the identity
and its launch flags are the grant** (:func:`launch_args`): each eligible agent launches its own copy where the host
scopes MCP servers per agent (Claude subagent front matter, Goose recipes); hosts with only a project-wide MCP config
(Codex, Grok, VS Code) get one compute-only ``--agent _session`` copy (:func:`session_launch_args`). P11 emits these
entries; this module only builds them.

The server runs as ``python3 -I -S -B`` and cannot import ``agentteams``, so generation installs, beside it under
``.agentteams/bin/``, a **bundle** of byte-identical copies of the package files it runs (:data:`BUNDLE`): the
executor, pins resolver, OS-profile builders and guest dispatch (METAPLAN R2/R10), the deny probes and the runtime
pins. The server reads each file once, checks its sha256 against the constants it carries (equal to
:data:`BUNDLE_SHA256`; the pins file against ``--pins-sha256``) and executes those same bytes. :func:`install_files`
refuses a packaged file that differs from its pin. Grant integrity rests on R1 (the control plane is session-write-
denied only with the OS sandbox on and R1's rules merged); :func:`control_plane_advisory` says when it is not.

Integrity-pinned: it fixes the server's hash, the bundle's hashes and the flags that carry each agent's grant.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

#: The MCP server name agents see (``mcp__agentteams_wasm__run_wasm``).
SERVER_NAME = "agentteams_wasm"
#: Version of the tool surface and ``run_wasm`` semantics; bump with any change to :data:`TOOLS` or the semantics.
MAP_VERSION = "1"
#: The server's tools.
TOOLS: tuple[str, ...] = ("run_wasm",)
#: The install directory (inside the control plane R1 write-denies to sessions whenever WASM is on).
BIN_DIR = ".agentteams/bin"
#: Where generation installs the server.
PROTECTED_PATH = f"{BIN_DIR}/agentteams-wasm-mcp.py"
#: Python flags: isolated, no ``site``, no bytecode beside the bundle (R2).
PYTHON_FLAGS: tuple[str, ...] = ("-I", "-S", "-B")
#: The shared instance's identity on hosts with only a project-wide MCP config; always compute-only.
SESSION_AGENT = "_session"
#: Goose extension timeout (s): above :data:`MAX_TIMEOUT_S` plus interpreter start and the compile grace (I5).
GOOSE_TIMEOUT = 300
#: Largest grant timeout a launch may carry (I5); the server enforces the same bound.
MAX_TIMEOUT_S = 240
#: sha256 of the shipped server; ``tests/test_wasm_mcp.py`` keeps it current.
SHA256 = "ff2350817310672f58c8336f9934f1f14561ac50922c47159e8f4358b493a1e4"

#: The bundle: ``(installed path, packaged source)``; modules first, in the server's load order.
BUNDLE: tuple[tuple[str, str], ...] = (
    (f"{BIN_DIR}/_sandbox_profiles.py", "_sandbox_profiles.py"),
    (f"{BIN_DIR}/wasm_pins.py", "wasm_pins.py"),
    (f"{BIN_DIR}/wasm_exec.py", "wasm_exec.py"),
    (f"{BIN_DIR}/wasm_guests.py", "wasm_guests.py"),
    (f"{BIN_DIR}/wasm_deny_probe.wasm", "data/wasm_deny_probe.wasm"),
    (f"{BIN_DIR}/wasm_probe_python.py", "data/wasm_probes/probe.py"),
    (f"{BIN_DIR}/wasm_probe_javascript.mjs", "data/wasm_probes/probe.mjs"),
    (f"{BIN_DIR}/wasm_runtime_pins.json", "data/wasm_runtime_pins.json"),
)
#: The bundle's one binary file (the rest are UTF-8 text).
BINARY_FILES: tuple[str, ...] = (f"{BIN_DIR}/wasm_deny_probe.wasm",)
#: The pins file: hashed against ``--pins-sha256`` (``wasm_runtime.PINS_SHA256``), not a constant, so a
#: ``--wasm-pin-update`` needs no server change.
PINS_INSTALLED = f"{BIN_DIR}/wasm_runtime_pins.json"
#: sha256 of every bundle file but the pins (the server carries the same constants; a test keeps all three equal).
BUNDLE_SHA256: Mapping[str, str] = {
    f"{BIN_DIR}/_sandbox_profiles.py": "e576f50fdd82dd9d87c92a644f93cc8dd35ed2fc70e2f2345177749ce5ba7ff6",
    f"{BIN_DIR}/wasm_pins.py": "ffda17a642ac6bf606e23f20030aee2b361a8bbccbfee36f8733af646424b9df",
    f"{BIN_DIR}/wasm_exec.py": "efe3d4982188b600f52593c992e779121e703dbb95f81c3af8e9d90deb250ba6",
    f"{BIN_DIR}/wasm_guests.py": "a3b23dd61bd3ea924f47c37debec44ff7442d2c5af32475d6de18e34c7c7ba5c",
    f"{BIN_DIR}/wasm_deny_probe.wasm": "dbcb3891225e2ce1a9145bd191d7a68afaed687b6572d88c7918baf6e7e7b365",
    f"{BIN_DIR}/wasm_probe_python.py": "1ce444ee1a54d5ebb15db105e4b8376e833e3614199b32353e2488e835745ed1",
    f"{BIN_DIR}/wasm_probe_javascript.mjs": "d5a4288997f9b950a16ec54b8535096475640f9b8bb122efdf81b34186691a58",
}
#: Schema id inside the canonical grant JSON :func:`grant_digest` hashes (the server computes the same).
GRANT_SCHEMA = "agentteams-wasm-mcp-grant/v1"
_LANGUAGES = ("module", "python", "javascript")


# --- packaged content ----------------------------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def server_content() -> str:
    """The shipped server's source.

    Returns:
        The text of ``agentteams/data/agentteams-wasm-mcp.py``.

    Raises:
        OSError: The package data is missing.
    """
    return resources.files("agentteams").joinpath("data", "agentteams-wasm-mcp.py").read_text(encoding="utf-8")


def pins_sha256() -> str:
    """The sha256 the server checks its pins copy against (``--pins-sha256``).

    Returns:
        ``wasm_runtime.PINS_SHA256`` (the packaged ``wasm_runtime_pins.json``).

    Raises:
        Nothing.
    """
    from agentteams import wasm_runtime

    return wasm_runtime.PINS_SHA256


def bundle_content(installed_path: str) -> bytes:
    """The packaged bytes of one bundle file, re-hashed against its pin.

    Args:
        installed_path: A path from :data:`BUNDLE` (e.g. ``.agentteams/bin/wasm_exec.py``).

    Returns:
        The file's bytes, exactly as packaged.

    Raises:
        KeyError: Not a bundle path.
        ValueError: The packaged file does not match :data:`BUNDLE_SHA256` (or, for the pins, ``PINS_SHA256``).
        OSError: The package data is missing.
    """
    source = dict(BUNDLE)[installed_path]
    data = resources.files("agentteams").joinpath(*source.split("/")).read_bytes()
    want = pins_sha256() if installed_path == PINS_INSTALLED else BUNDLE_SHA256[installed_path]
    if _sha(data) != want:
        raise ValueError(f"agentteams/{source} in this agentteams install doesn't match the agentteams_wasm bundle "
                         "pin; reinstall agentteams before rendering")
    return data


def bundle_files() -> list[tuple[str, bytes]]:
    """The server and every bundle file, as ``(project-relative path, bytes)``, each checked against its pin.

    Returns:
        ``[(PROTECTED_PATH, server), *((path, bytes) for path in BUNDLE)]``.

    Raises:
        ValueError: The packaged server or a bundle file does not match its pin (fail closed).
        OSError: The package data is missing.
    """
    server = server_content().encode("utf-8")
    if _sha(server) != SHA256:
        raise ValueError("the agentteams_wasm server in this agentteams install doesn't match its pinned hash; "
                         "reinstall agentteams before rendering")
    return [(PROTECTED_PATH, server), *((p, bundle_content(p)) for p, _ in BUNDLE)]


# --- the grant and the launch --------------------------------------------------------------------------------------

def grant_parts(grant: Any) -> tuple[list[str], bool]:
    """The project-relative read paths and the scratch flag a grant's mounts encode (R9 guest names).

    Args:
        grant: A ``wasm_exec.Grant``.

    Returns:
        ``(sorted read paths, scratch)``: ``/project/<rel>`` (ro) gives ``<rel>``; ``/scratch`` (rw) gives True.

    Raises:
        ValueError: A mount that is not a grant mount.
    """
    reads, scratch = [], False
    for m in grant.mounts:
        if m.guest == "/scratch" and m.mode == "rw":
            scratch = True
        elif m.guest.startswith("/project/") and m.mode == "ro":
            reads.append(m.guest[len("/project/"):])
        else:
            raise ValueError(f"mount {m.guest!r} ({m.mode}) is not a grant mount")
    return sorted(reads), scratch


def grant_digest(grant: Any) -> str:
    """sha256 of the canonical, machine-independent grant JSON (what ``--grant-sha256`` carries).

    Args:
        grant: A ``wasm_exec.Grant``.

    Returns:
        The hex digest over ``{"schema", "reads", "scratch", "limits"}`` (sorted keys, compact separators).

    Raises:
        ValueError: From :func:`grant_parts`.
    """
    reads, scratch = grant_parts(grant)
    lim = grant.limits
    doc = {"schema": GRANT_SCHEMA, "reads": reads, "scratch": scratch,
           "limits": {"fuel": lim.fuel, "max_memory_bytes": lim.max_memory_bytes, "timeout_s": lim.timeout_s}}
    return _sha(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def grant_args(grant: Any) -> list[str]:
    """The grant flags, in canonical order: ``--read`` per path, ``--scratch``, then the three limits.

    Args:
        grant: A ``wasm_exec.Grant``.

    Returns:
        ``["--read", rel, ..., "--scratch"?, "--fuel", n, "--max-memory-bytes", n, "--timeout-s", n]``.

    Raises:
        ValueError: From :func:`grant_parts`, or a timeout above :data:`MAX_TIMEOUT_S`.
    """
    reads, scratch = grant_parts(grant)
    if grant.limits.timeout_s > MAX_TIMEOUT_S:
        raise ValueError(f"a run_wasm grant's timeout_s must be at most {MAX_TIMEOUT_S} (the MCP hosts' tool "
                         f"timeouts); got {grant.limits.timeout_s}")
    out: list[str] = []
    for rel in reads:
        out += ["--read", rel]
    if scratch:
        out.append("--scratch")
    lim = grant.limits
    return out + ["--fuel", str(lim.fuel), "--max-memory-bytes", str(lim.max_memory_bytes),
                  "--timeout-s", str(lim.timeout_s)]


def launch_args(agent: str, grant: Any, languages: Sequence[str], pins_sha: str | None = None) -> list[str]:
    """The canonical argument list for one instance (after the interpreter).

    Args:
        agent: The agent slug bound into the instance, or :data:`SESSION_AGENT` (compute-only only).
        grant: Its ``wasm_exec.Grant`` (``sandbox_state.wasm_grant_for``).
        languages: The team's WASM languages (``SandboxState.wasm_languages``).
        pins_sha: The pins hash; default :func:`pins_sha256`.

    Returns:
        ``[*PYTHON_FLAGS, PROTECTED_PATH, "--agent", agent, "--languages", "a,b", *grant_args(grant),
        "--pins-sha256", hex, "--grant-sha256", hex]``. The server derives the project root from its install
        location, so no machine path is written into the team.

    Raises:
        ValueError: A bad slug, empty or unknown languages, a widened grant for the shared instance, or as
            :func:`grant_args`.
    """
    if agent != SESSION_AGENT and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", agent or ""):
        raise ValueError(f"agent {agent!r} must be an agent slug or {SESSION_AGENT}")
    given = list(languages)
    if not given or len(set(given)) != len(given) or any(x not in _LANGUAGES for x in given):
        raise ValueError(f"languages must be a non-empty, duplicate-free list from {', '.join(_LANGUAGES)}")
    if agent == SESSION_AGENT and grant.mounts:
        raise ValueError(f"the shared {SESSION_AGENT} instance is compute-only; a widened grant needs a host with "
                         "per-agent MCP servers (I3)")
    pins = pins_sha if pins_sha is not None else pins_sha256()
    langs = [x for x in _LANGUAGES if x in given]
    return [*PYTHON_FLAGS, PROTECTED_PATH, "--agent", agent, "--languages", ",".join(langs), *grant_args(grant),
            "--pins-sha256", pins, "--grant-sha256", grant_digest(grant)]


def agent_launch_args(state: Any, agent: str, pins_sha: str | None = None) -> list[str] | None:
    """:func:`launch_args` for *agent* from the effective sandbox state.

    Args:
        state: A ``sandbox_state.SandboxState``.
        agent: The agent slug.
        pins_sha: As :func:`launch_args`.

    Returns:
        The arguments, or None when WASM is off (no server is wired).

    Raises:
        ValueError: As :func:`launch_args`.
    """
    from agentteams.sandbox_state import wasm_grant_for

    grant = wasm_grant_for(state, agent)
    return None if grant is None else launch_args(agent, grant, state.wasm_languages, pins_sha)


def session_launch_args(state: Any, pins_sha: str | None = None) -> list[str] | None:
    """The shared compute-only instance's arguments (project-wide MCP config hosts: Codex, Grok, VS Code).

    Args:
        state: A ``sandbox_state.SandboxState``.
        pins_sha: As :func:`launch_args`.

    Returns:
        The arguments with the team's limits and no mounts, or None when WASM is off.

    Raises:
        ValueError: As :func:`launch_args`.
    """
    from agentteams import wasm_exec

    if not state.wasm:
        return None
    return launch_args(SESSION_AGENT, wasm_exec.COMPUTE_ONLY, state.wasm_languages, pins_sha)


def interpreter() -> str:
    """The absolute system ``python3`` agents launch the server with (shared with ``agentteams_runner``).

    Returns:
        ``runner_mcp.interpreter()``.

    Raises:
        ValueError: No system python3 exists.
    """
    from agentteams import runner_mcp

    return runner_mcp.interpreter()


def tool_names() -> list[str]:
    """Claude's exact tool names: ``["mcp__agentteams_wasm__run_wasm"]``.

    Returns:
        The names.

    Raises:
        Nothing.
    """
    return [f"mcp__{SERVER_NAME}__{t}" for t in TOOLS]


def claude_entry(args: Sequence[str], python: str) -> str:
    """One ``mcpServers:`` list item for a Claude subagent's front matter (P11 composes the key and siblings).

    Args:
        args: From :func:`launch_args` / :func:`agent_launch_args`.
        python: The interpreter (:func:`interpreter`).

    Returns:
        ``"  - agentteams_wasm:\\n      type: stdio\\n      command: ...\\n      args: [...]\\n"``.

    Raises:
        Nothing.
    """
    joined = ", ".join(json.dumps(a) for a in args)
    return (f"  - {SERVER_NAME}:\n      type: stdio\n      command: {json.dumps(python)}\n"
            f"      args: [{joined}]\n")


def goose_extension(args: Sequence[str], python: str) -> dict:
    """The canonical Goose stdio extension (exact ``available_tools``).

    Args:
        args: From :func:`launch_args`.
        python: The interpreter.

    Returns:
        ``{"type": "stdio", "name", "cmd", "args", "env_keys": [], "timeout": GOOSE_TIMEOUT, "available_tools"}``.

    Raises:
        Nothing.
    """
    return {"type": "stdio", "name": SERVER_NAME, "cmd": python, "args": list(args), "env_keys": [],
            "timeout": GOOSE_TIMEOUT, "available_tools": list(TOOLS)}


def table_entry(args: Sequence[str], python: str) -> dict:
    """The ``{command, args}`` entry TOML/JSON MCP configs take (Codex, Grok, VS Code; P11 adds host keys).

    Args:
        args: From :func:`session_launch_args` (those hosts get only the shared compute-only copy).
        python: The interpreter.

    Returns:
        ``{"command": python, "args": [...]}``.

    Raises:
        Nothing.
    """
    return {"command": python, "args": list(args)}


# --- install -------------------------------------------------------------------------------------------------------

def _wasm_on(manifest: Mapping[str, Any]) -> bool:
    from agentteams.wasm_config import wasm_switch

    return wasm_switch(manifest)[0]


def install_files(manifest: dict) -> list[tuple[str, str]]:
    """The text files of the install, relative to an agents dir two levels below the root (as ``runner_mcp``).

    Gated on the WASM switch, not on ``write_policy``: empty when WASM is off, so every other team is unchanged. The
    binary deny probe is :func:`install_binary_files` (the emit phase writes text); :func:`write_bundle` writes all.

    Args:
        manifest: The team manifest.

    Returns:
        ``[("../../" + path, text), ...]`` for the server and every text bundle file.

    Raises:
        ValueError: A packaged file does not match its pin, or a malformed ``wasm_sandbox`` record.
    """
    if not _wasm_on(manifest):
        return []
    return [(f"../../{p}", data.decode("utf-8")) for p, data in bundle_files() if p not in BINARY_FILES]


def install_binary_files(manifest: dict) -> list[tuple[str, bytes]]:
    """The binary files of the install (the deny-probe module), relative like :func:`install_files`.

    Args:
        manifest: The team manifest.

    Returns:
        ``[("../../.agentteams/bin/wasm_deny_probe.wasm", bytes)]`` when WASM is on, else ``[]``.

    Raises:
        ValueError: As :func:`install_files`.
    """
    if not _wasm_on(manifest):
        return []
    return [(f"../../{p}", bundle_content(p)) for p in BINARY_FILES]


def _real_dir(path: Path) -> None:
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError(f"{path} must be a real directory (not a symlink); refusing to install")


def write_bundle(project_root: str | Path, files: Sequence[tuple[str, bytes]] | None = None) -> list[str]:
    """Write the server and bundle byte-exactly into ``<root>/.agentteams/bin/`` (atomic replace, no symlinks).

    Args:
        project_root: The project root.
        files: ``(path, bytes)`` pairs under :data:`BIN_DIR`; default :func:`bundle_files` (tests pass a variant).

    Returns:
        The project-relative paths written.

    Raises:
        ValueError: A pin mismatch, a path outside :data:`BIN_DIR`, or a symlinked ``.agentteams`` / ``bin``.
        OSError: The write failed.
    """
    import secrets

    root = Path(os.path.realpath(project_root))
    cur = root
    for part in BIN_DIR.split("/"):
        cur = cur / part
        if not os.path.lexists(cur):
            os.mkdir(cur, 0o700)
        _real_dir(cur)
    written = []
    for rel, data in (bundle_files() if files is None else files):
        if os.path.dirname(rel) != BIN_DIR or os.path.basename(rel) in ("", ".", ".."):
            raise ValueError(f"{rel} is not a file directly under {BIN_DIR}")
        tmp = cur / f".{os.path.basename(rel)}.{secrets.token_hex(6)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        done = False
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, root / rel)
            done = True
        finally:
            if not done:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        written.append(rel)
    return written


# --- wiring checks ------------------------------------------------------------------------------------------------

def _file_sha(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None
        return _sha(fh.read())


def control_plane_advisory(root: str | Path, framework: str, manifest: dict) -> str | None:
    """Why grant integrity is only advisory here (R1, S2), or None when the control plane is session-protected.

    Protected means: the OS sandbox is on, R1's rules are emitted (``protect_control_plane``), and they are merged
    into the live host config (Claude ``settings.json``; the Goose Seatbelt profile). P13 records the same answer as
    ``wasm_enforcement``.

    Args:
        root: The project root.
        framework: The framework id.
        manifest: The team manifest.

    Returns:
        ``"WASM grant integrity: advisory (control plane is agent-writable: <reason>)"`` or None.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` / ``wasm_wiring`` record.
    """
    from agentteams.frameworks import _control_plane_protect as cpp
    from agentteams.sandbox_state import os_switch_state

    def advisory(reason: str) -> str:
        return f"WASM grant integrity: advisory (control plane is agent-writable: {reason})"

    if not os_switch_state(manifest)[0]:
        return advisory("the OS sandbox is off")
    control = cpp.control_plane_for(manifest)
    if control is None:
        return advisory("the R1 control-plane rules are not emitted")
    protect_ledger = manifest.get("write_policy") == "orchestrator-only"
    root = Path(root)
    if framework == "claude":
        missing: list[str] = []
        try:
            data = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return advisory("live .claude/settings.json is missing or unreadable; merge the emitted settings")
        data = data if isinstance(data, dict) else {}
        sandbox = data.get("sandbox") if isinstance(data.get("sandbox"), dict) else {}
        if sandbox.get("enabled") is not True or sandbox.get("allowUnsandboxedCommands") is True:
            missing.append("sandbox.enabled true without unsandboxed commands")
        deny_write = (sandbox.get("filesystem") or {}).get("denyWrite") or []
        missing += [f"denyWrite {p}" for p in cpp.write_denies(protect_ledger, control) if p not in deny_write]
        deny = (data.get("permissions") or {}).get("deny") or []
        missing += [r for r in cpp.permission_rules(protect_ledger, control) if r not in deny]
        try:
            local = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            local = {}   # absent: nothing overrides the project settings
        except (OSError, ValueError):   # present but unreadable: it might turn the sandbox off (CH-24: say so)
            local = {}
            missing.append("a readable, valid settings.local.json (it is unreadable or not JSON, so it may turn the "
                           "sandbox off)")
        lsb = (local.get("sandbox") or {}) if isinstance(local, dict) else {}
        if lsb.get("enabled") is False or lsb.get("allowUnsandboxedCommands") is True:
            missing.append("settings.local.json must not turn the sandbox off")
        return advisory("live .claude settings lack " + ", ".join(missing)) if missing else None
    if framework == "goose":
        try:
            text = (root / ".goose" / "sandbox.sb").read_text(encoding="utf-8")
        except OSError:
            return advisory(".goose/sandbox.sb is missing; re-render with the OS sandbox on")
        missing = [p for p in cpp.seatbelt_denies(protect_ledger, control)
                   if f'"{p}"' not in text and f'/{p}"' not in text]
        return advisory(".goose/sandbox.sb does not deny writes to " + ", ".join(missing)) if missing else None
    if framework == "grok":
        return advisory("grok's sandbox profile cannot deny a write without denying the read")
    return advisory(f"no verified control-plane write protection for {framework}")


def wiring_problems(root: str | Path, framework: str, manifest: dict, *, home: str | Path | None = None
                    ) -> list[str]:
    """Live-wiring problems for a team with WASM on (``--check-wiring``; P11, P13). Empty when WASM is off.

    * The installed server matches :data:`SHA256` and every bundle file its pin; no ``.pyc``/``__pycache__`` in
      :data:`BIN_DIR`.
    * The runtime is installed and verifies (``wasm_runtime.status``) for wasmtime and each interpreter language.
    * No config scope defines another server named ``agentteams_wasm`` (S7; ``runner_mcp._shadowing``).
    * R1: :func:`control_plane_advisory` (a missing protection is reported, never silently passed).

    Args:
        root: The project root.
        framework: The framework id.
        manifest: The team manifest.
        home: The home directory for user config (tests); default ``~``.

    Returns:
        One message per problem.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record.
    """
    if not _wasm_on(manifest):
        return []
    from agentteams import runner_mcp, wasm_runtime
    from agentteams.wasm_config import manifest_wasm_config

    root = Path(root)
    problems: list[str] = []
    want = {PROTECTED_PATH: SHA256, **BUNDLE_SHA256, PINS_INSTALLED: pins_sha256()}
    for rel, sha in want.items():
        got = _file_sha(root / rel)
        if got is None:
            problems.append(f"{rel} is missing, a symlink or not a regular file; re-render the team")
        elif got != sha and rel == PROTECTED_PATH:   # the server cannot check its own bytes: only this check does
            problems.append(f"{rel} does not match its pinned sha256 (the server cannot detect this itself and "
                            "would still start); re-render")
        elif got != sha:
            problems.append(f"{rel} does not match its pinned sha256 (the server refuses to start); re-render")
    bin_dir = root / BIN_DIR
    if bin_dir.is_dir():
        problems += [f"{BIN_DIR}/{p.name} is bytecode; remove it (the bundle runs only from verified source)"
                     for p in sorted(bin_dir.iterdir()) if p.name == "__pycache__" or p.suffix == ".pyc"]
    status = wasm_runtime.status(root)
    needed = ["wasmtime", *(x for x in manifest_wasm_config(manifest).languages if x != "module")]
    problems += [f"WASM runtime {c}: {status.get(c)}; run `agentteams --wasm-install`" for c in needed
                 if not str(status.get(c, "")).startswith("ok")]
    problems += [f"{p}; remove it so it can't shadow the canonical agentteams_wasm server (S7)"
                 for p in runner_mcp._shadowing(root, home or os.path.expanduser("~"), SERVER_NAME)]
    advisory = control_plane_advisory(root, framework, manifest)
    if advisory:
        problems.append(advisory)
    return problems


__all__ = ["BIN_DIR", "BINARY_FILES", "BUNDLE", "BUNDLE_SHA256", "GOOSE_TIMEOUT", "GRANT_SCHEMA", "MAP_VERSION",
           "MAX_TIMEOUT_S", "PINS_INSTALLED", "PROTECTED_PATH", "PYTHON_FLAGS", "SERVER_NAME", "SESSION_AGENT",
           "SHA256", "TOOLS", "agent_launch_args", "bundle_content", "bundle_files", "claude_entry",
           "control_plane_advisory", "goose_extension", "grant_args", "grant_digest", "grant_parts",
           "install_binary_files", "install_files", "interpreter", "launch_args", "pins_sha256", "server_content",
           "session_launch_args", "table_entry", "tool_names", "wiring_problems", "write_bundle"]
