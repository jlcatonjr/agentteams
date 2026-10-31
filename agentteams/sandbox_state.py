"""sandbox_state.py — the effective sandbox state of a render, its build-log record, and the previous state.

METAPLAN §2.1: P15 owns the frozen :class:`SandboxState` (the OS switch, the WASM switch, the WASM languages and
per-agent grants, and the projected model-substrate switch). P00 (OS) and P01 (WASM) add
``effective_sandbox_state(manifest)``; every consumer that reasons about a boundary reads this, never
``privilege_profile`` directly.

This module also holds the one token<->``Mount`` mapping (plan Interface issue 2): a read-only ``/project/<rel>``
mount is ``wasm:<agent>:read:<rel>``, the private ``/scratch`` mount is ``wasm:<agent>:scratch``. Any other mount in an
agent grant (``/src``, ``/lib``, a host path outside the project, a writable ``/project`` mount) is a runtime mount,
never a grant, and is refused (fail closed). Records are project-relative only: no absolute host path is written.

:func:`previous_sandbox_state` fails toward "was on": a malformed record yields :meth:`SandboxState.conservative`,
and on-disk boundary evidence can only turn a switch on (OR, never AND), so a deleted record cannot make an "off"
look unchanged.

``Grant``/``Mount``/``Limits`` come from :mod:`agentteams.wasm_exec` (P02, same wave): annotations only under
``TYPE_CHECKING``, the runtime import is lazy and an ``ImportError`` propagates (never "no grants").
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from agentteams.cli.sandbox_effects import native_exec_token, read_token, scratch_token, valid_rel

if TYPE_CHECKING:
    from agentteams.wasm_exec import Grant, Limits

#: The WASM guest languages, in order. Mirrors ``wasm_guests.LANGUAGES`` (P04), which cannot be imported here before
#: it lands; the wave-2 test battery (P17) pins the two equal.
WASM_LANGUAGES: tuple[str, ...] = ("module", "python", "javascript")

#: Where an agent's private scratch dir lives, relative to the project root (METAPLAN R8).
SCRATCH_REL = ".agentteams/wasm/scratch"

#: The build-log keys this module reads (written by ``build_team._write_run_log``).
BUILD_LOG_STATE_KEY = "sandbox_state"
BUILD_LOG_EXCEPTIONS_KEY = "sandbox_exceptions"

_BUILD_LOG_REL = "references/build-log.json"
_LIMIT_KEYS: tuple[str, ...] = ("fuel", "max_memory_bytes", "timeout_s")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9:._-]{0,127}$")
_SUBSTRATE_VALUES = (None, "on", "off")

#: On-disk OS-boundary evidence per framework (project-relative). ``sandbox/confine-run.sh`` (the neutral launcher)
#: counts for every framework; claude's evidence is the live ``.claude/settings.json`` ``sandbox.enabled``.
_OS_EVIDENCE: dict[str, tuple[str, ...]] = {
    "goose": (".goose/sandbox.sb",),
    "codex": (".codex/confined-run.example.sh",),
    "grok": (".grok/sandbox.toml",),
}
_OS_EVIDENCE_ALL: tuple[str, ...] = ("sandbox/confine-run.sh",)
#: WASM evidence: an installed runtime (P03) or the installed ``agentteams_wasm`` MCP server (P10).
_WASM_EVIDENCE: tuple[str, ...] = (".agentteams/wasm/installed.json", ".agentteams/bin/agentteams-wasm-mcp.py")
#: The pipeline-owned projection of the model-substrate switch (P20).
_SUBSTRATE_FILE = ".agentteams/sandbox-state.json"


def _wasm_exec() -> Any:
    """Import :mod:`agentteams.wasm_exec` lazily (P02). An ImportError propagates: fail closed, never "no grants"."""
    from agentteams import wasm_exec

    return wasm_exec


def _read_regular_text(path: Path) -> str:
    """``signing_switch._read_regular_text`` (lstat regular-file check, then ``O_NOFOLLOW``), imported lazily so the
    framework emitters that will read :class:`SandboxState` (P00) do not form an import cycle through
    ``signing_switch`` -> ``frameworks._sandbox_emit``. Raises ValueError for a symlink, non-regular or unreadable
    file."""
    from agentteams.signing_switch import _read_regular_text as read

    return read(path)


def _root(project_root: Path) -> Path:
    return Path(os.path.realpath(project_root))


def _mount_token(agent: str, mount: Any, root: Path | None) -> str:
    """Return the grant token for one mount of ``agent``'s grant, or raise ValueError (not a grant mount)."""
    guest, mode, host = getattr(mount, "guest", None), getattr(mount, "mode", None), getattr(mount, "host", None)
    if guest == "/scratch" and mode == "rw":
        token = scratch_token(agent)
        expected = None if root is None else root / SCRATCH_REL / agent
    elif isinstance(guest, str) and guest.startswith("/project/") and mode == "ro":
        rel = valid_rel(guest[len("/project/"):])
        token = read_token(agent, rel)
        expected = None if root is None else root / rel
    else:
        raise ValueError(f"agent {agent!r} grant has mount {guest!r} ({mode!r}), which is not a grant mount: only a "
                         "read-only /project/<rel> mount or the rw /scratch mount can be granted")
    if expected is not None and (not isinstance(host, str) or os.path.normpath(host) != str(expected)):
        raise ValueError(f"agent {agent!r} mount {guest!r} has host {host!r}; a grant mount must be "
                         f"{expected} (inside the project, no symlink)")
    return token


def grant_tokens(agent: str, grant: Grant, *, project_root: Path) -> frozenset[str]:
    """Return the relaxation tokens ``agent``'s grant adds over compute-only.

    Args:
        agent: The agent slug.
        grant: The agent's grant (its ``limits`` mint no token: raising them is not a widening).
        project_root: The project root; each mount's host must be the matching path inside it.

    Returns:
        ``wasm:<agent>:read:<rel>`` per read-only ``/project/<rel>`` mount, ``wasm:<agent>:scratch`` for ``/scratch``.

    Raises:
        ValueError: A mount that is not a grant mount, or a host path that does not match its guest name.
    """
    root = _root(project_root)
    return frozenset(_mount_token(agent, m, root) for m in grant.mounts)


def state_tokens(state: SandboxState | None) -> frozenset[str]:
    """Return the union of grant tokens over ``state.wasm_grants`` (empty when ``state`` is None or WASM is off).

    Derived from guest names only (host paths were checked when the state was built or read), so it needs no project
    root. Used by the relaxation gate and the notice.

    Args:
        state: A sandbox state, or None.

    Returns:
        The tokens.

    Raises:
        ValueError: A grant holds a mount that is not a grant mount.
    """
    if state is None or not state.wasm:
        return frozenset()
    return frozenset(_mount_token(a, m, None) for a, g in state.wasm_grants.items() for m in g.mounts)


def grant_from_tokens(agent: str, tokens: Iterable[str], limits: Limits, *, project_root: Path) -> Grant:
    """Build ``agent``'s grant from its tokens (the inverse of :func:`grant_tokens`).

    Mounts are in a canonical order: the read mounts sorted by path, then ``/scratch``.

    Args:
        agent: The agent slug.
        tokens: ``wasm:<agent>:read:<rel>`` / ``wasm:<agent>:scratch`` tokens.
        limits: The grant's limits.
        project_root: The project root the host paths are resolved against.

    Returns:
        The grant.

    Raises:
        ValueError: A token for another agent, an unknown token, or a read path that resolves through a symlink
            (its realpath is not the plain path inside the project).
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    wx = _wasm_exec()
    root = _root(project_root)
    reads: set[str] = set()
    scratch = False
    for token in tokens:
        if token == scratch_token(agent):
            scratch = True
            continue
        prefix = f"wasm:{agent}:read:"
        if not isinstance(token, str) or not token.startswith(prefix):
            raise ValueError(f"token {token!r} is not a WASM grant token for agent {agent!r}")
        reads.add(valid_rel(token[len(prefix):]))
    mounts = []
    for rel in sorted(reads):
        host = root / rel
        if os.path.realpath(host) != str(host):
            raise ValueError(f"read path {rel!r} resolves through a symlink; a grant must name the real path")
        mounts.append(wx.Mount(host=str(host), guest=f"/project/{rel}", mode="ro"))
    if scratch:
        mounts.append(wx.Mount(host=str(root / SCRATCH_REL / agent), guest="/scratch", mode="rw"))
    return wx.Grant(mounts=tuple(mounts), limits=limits)


@dataclass(frozen=True)
class SandboxState:
    """The effective sandbox state of one render (METAPLAN §2.1; field names and order are the contract).

    Attributes:
        os: Whether the OS boundary is emitted.
        os_reason: ``"brief"`` | ``"cli"`` | ``"profile:<p>"`` | ``"host-feature:<tok>"`` | ``"unknown"``.
        wasm: Whether WASM confinement is on.
        wasm_reason: ``"brief"`` | ``"cli"`` | ``"default-off"`` | ``"unknown"``.
        wasm_languages: A subset of :data:`WASM_LANGUAGES`, in that order; ``()`` when WASM is off.
        wasm_grants: Agent slug -> grant, only for agents whose grant is not compute-only; ``{}`` when off.
        model_substrate: ``None`` | ``"on"`` | ``"off"`` (P20).
    """

    os: bool
    os_reason: str
    wasm: bool
    wasm_reason: str
    wasm_languages: tuple[str, ...]
    wasm_grants: Mapping[str, Grant] = field(default_factory=dict)
    model_substrate: str | None = None

    def __post_init__(self) -> None:
        # Frozen means the grants mapping is read-only too.
        object.__setattr__(self, "wasm_grants", MappingProxyType(dict(self.wasm_grants)))

    @classmethod
    def conservative(cls) -> SandboxState:
        """Return the "was on" state used when a previous record is malformed: every "off" now is a relaxation.

        Returns:
            OS and WASM on (reasons ``"unknown"``), no grants, the model substrate on.
        """
        return cls(os=True, os_reason="unknown", wasm=True, wasm_reason="unknown", wasm_languages=(),
                   wasm_grants={}, model_substrate="on")

    def to_record(self, *, project_root: Path) -> dict[str, object]:
        """Return the build-log / team-manifest record (project-relative only).

        Args:
            project_root: The project root the grants' host paths are checked against.

        Returns:
            ``{"os","os_reason","wasm","wasm_reason","wasm_languages","wasm_grants","model_substrate"}``, where each
            grant is ``{"tokens": [sorted], "limits": {"fuel","max_memory_bytes","timeout_s"}}``.

        Raises:
            ValueError: A grant mount that is not a grant mount, or whose host is not the matching project path.
        """
        grants = {
            agent: {"tokens": sorted(grant_tokens(agent, grant, project_root=project_root)),
                    "limits": {k: getattr(grant.limits, k) for k in _LIMIT_KEYS}}
            for agent, grant in sorted(self.wasm_grants.items())
        }
        return {"os": "on" if self.os else "off", "os_reason": self.os_reason,
                "wasm": "on" if self.wasm else "off", "wasm_reason": self.wasm_reason,
                "wasm_languages": list(self.wasm_languages), "wasm_grants": grants,
                "model_substrate": self.model_substrate}

    @classmethod
    def from_record(cls, rec: object, *, project_root: Path) -> SandboxState | None:
        """Parse a record written by :meth:`to_record`.

        Args:
            rec: The decoded record.
            project_root: The project root the grants' host paths are built under.

        Returns:
            The state, or None when the record is malformed in any way.

        Raises:
            ImportError: The record carries grants and :mod:`agentteams.wasm_exec` is not importable (fail closed).
        """
        if not isinstance(rec, dict) or set(rec) != {"os", "os_reason", "wasm", "wasm_reason", "wasm_languages",
                                                     "wasm_grants", "model_substrate"}:
            return None
        switches = rec["os"], rec["wasm"]
        if any(v not in ("on", "off") for v in switches):
            return None
        if not all(isinstance(rec[k], str) and _REASON_RE.match(rec[k]) for k in ("os_reason", "wasm_reason")):
            return None
        langs = rec["wasm_languages"]
        if (not isinstance(langs, list) or len(set(langs)) != len(langs)
                or [x for x in WASM_LANGUAGES if x in langs] != langs):
            return None
        if rec["model_substrate"] not in _SUBSTRATE_VALUES or not isinstance(rec["wasm_grants"], dict):
            return None
        grants: dict[str, Any] = {}
        for agent, entry in rec["wasm_grants"].items():
            parsed = _grant_from_record(agent, entry, project_root)
            if parsed is None:
                return None
            grants[agent] = parsed
        return cls(os=rec["os"] == "on", os_reason=rec["os_reason"], wasm=rec["wasm"] == "on",
                   wasm_reason=rec["wasm_reason"], wasm_languages=tuple(langs), wasm_grants=grants,
                   model_substrate=rec["model_substrate"])


def _grant_from_record(agent: object, entry: object, project_root: Path) -> Any | None:
    """Return one recorded grant, or None when malformed. An ImportError (no ``wasm_exec``) propagates."""
    if not isinstance(agent, str) or not _SLUG_RE.match(agent):
        return None
    if not isinstance(entry, dict) or set(entry) != {"tokens", "limits"}:
        return None
    tokens, limits = entry["tokens"], entry["limits"]
    if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
        return None
    if (not isinstance(limits, dict) or set(limits) != set(_LIMIT_KEYS)
            or not all(type(limits[k]) is int and limits[k] > 0 for k in _LIMIT_KEYS)):
        return None
    wx = _wasm_exec()
    try:
        return grant_from_tokens(agent, tokens, wx.Limits(**limits), project_root=project_root)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SandboxExceptions:
    """Relaxation-gate inputs that are not part of :class:`SandboxState`.

    Attributes:
        native_exec_keep: Agents that keep native execution while WASM is on (P13 computes it; P01 schema).
        wasm_programs: Agents whose runner ``confined_programs`` entry is ``sandbox: wasm`` (P12).
        os_programs: Agents whose runner ``confined_programs`` entry is ``sandbox: os`` (P12). With the exception
            registry it tells a still-switched agent (``wasm-to-os-switch``) from one that has left the runner.
    """

    native_exec_keep: frozenset[str] = frozenset()
    wasm_programs: frozenset[str] = frozenset()
    os_programs: frozenset[str] = frozenset()

    def to_record(self) -> dict[str, list[str]]:
        """Return the build-log record (sorted lists).

        Returns:
            ``{"native_exec_keep": [...], "wasm_programs": [...], "os_programs": [...]}``.
        """
        return {"native_exec_keep": sorted(self.native_exec_keep), "wasm_programs": sorted(self.wasm_programs),
                "os_programs": sorted(self.os_programs)}

    @classmethod
    def from_record(cls, rec: object) -> SandboxExceptions | None:
        """Parse a record written by :meth:`to_record`.

        Args:
            rec: The decoded record (``os_programs`` may be absent).

        Returns:
            The value, or None when malformed.
        """
        keys = ("native_exec_keep", "wasm_programs", "os_programs")
        if not isinstance(rec, dict) or not set(keys[:2]) <= set(rec) <= set(keys):
            return None
        sets: list[frozenset[str]] = []
        for key in keys:
            value = rec.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and _SLUG_RE.match(v) for v in value):
                return None
            sets.append(frozenset(value))
        return cls(*sets)


def _read_build_log(team_dir: Path) -> dict | None:
    """Return the decoded build-log, None when absent, or ``{}`` when present but unusable (symlink, bad JSON)."""
    path = Path(team_dir) / _BUILD_LOG_REL
    if not os.path.lexists(path):
        return None
    try:
        data = json.loads(_read_regular_text(path))
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _claude_live_sandbox_enabled(root: Path) -> bool:
    """True when the live ``.claude/settings.json`` has ``sandbox.enabled`` exactly True (booleans only, no echo)."""
    try:
        data = json.loads(_read_regular_text(root / ".claude" / "settings.json"))
    except (ValueError, json.JSONDecodeError):
        return False
    sandbox = data.get("sandbox") if isinstance(data, dict) else None
    return isinstance(sandbox, dict) and sandbox.get("enabled") is True


def _substrate_evidence(root: Path) -> bool:
    """True when the P20 projection says the substrate switch is on; a present but unreadable file counts as on."""
    path = root / _SUBSTRATE_FILE
    if not os.path.lexists(path):
        return False
    try:
        data = json.loads(_read_regular_text(path))
    except (ValueError, json.JSONDecodeError):
        return True
    return not isinstance(data, dict) or data.get("model_substrate_wasm") != "off"


def previous_sandbox_state(team_dir: Path, project_root: Path, framework_id: str) -> SandboxState | None:
    """Return the sandbox state the previous render left, failing toward "was on".

    1. The build-log record (``references/build-log.json`` key ``sandbox_state``), read ``O_NOFOLLOW`` after an lstat
       regular-file check. A present but malformed record, or a build-log that is a symlink or not valid JSON, yields
       :meth:`SandboxState.conservative`.
    2. Evidence, OR-ed in (never AND): ``os`` is on when this framework's boundary files exist; ``wasm`` when a runtime
       or the ``agentteams_wasm`` server is installed; ``model_substrate`` is ``"on"`` when the P20 projection says so.
       Grants come from the record only.
    3. No record and no evidence: None (a new team, or one never confined).

    Args:
        team_dir: The team directory (holds ``references/build-log.json``).
        project_root: The project root (evidence files).
        framework_id: The framework being rendered (selects its OS evidence).

    Returns:
        The previous state, or None.

    Raises:
        ImportError: The record carries grants and :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    root = _root(project_root)
    log = _read_build_log(team_dir)
    record: SandboxState | None = None
    if log is not None and (log == {} or BUILD_LOG_STATE_KEY in log):
        record = SandboxState.from_record(log.get(BUILD_LOG_STATE_KEY), project_root=root)
        if record is None:
            record = SandboxState.conservative()
    os_paths = (*_OS_EVIDENCE.get(framework_id, ()), *_OS_EVIDENCE_ALL)
    os_evidence = any(os.path.lexists(root / p) for p in os_paths) or (
        framework_id == "claude" and _claude_live_sandbox_enabled(root))
    wasm_evidence = any(os.path.lexists(root / p) for p in _WASM_EVIDENCE)
    substrate_evidence = _substrate_evidence(root)
    if record is None and not (os_evidence or wasm_evidence or substrate_evidence):
        return None
    if record is None:
        record = SandboxState(os=False, os_reason="unknown", wasm=False, wasm_reason="unknown", wasm_languages=(),
                              wasm_grants={}, model_substrate=None)
    return SandboxState(
        os=record.os or os_evidence, os_reason=record.os_reason,
        wasm=record.wasm or wasm_evidence, wasm_reason=record.wasm_reason,
        wasm_languages=record.wasm_languages, wasm_grants=record.wasm_grants,
        model_substrate="on" if substrate_evidence else record.model_substrate,
    )


def previous_sandbox_exceptions(team_dir: Path) -> SandboxExceptions | None:
    """Return the gate inputs the previous render recorded (build-log key ``sandbox_exceptions``).

    A malformed record (or an unusable build-log) yields empty sets, so every current keep entry counts as new.

    Args:
        team_dir: The team directory.

    Returns:
        The previous value, or None when nothing was recorded.
    """
    log = _read_build_log(team_dir)
    if log is None or (log and BUILD_LOG_EXCEPTIONS_KEY not in log):
        return None
    return SandboxExceptions.from_record(log.get(BUILD_LOG_EXCEPTIONS_KEY)) or SandboxExceptions()


def keep_tokens(state: SandboxState | None, exceptions: SandboxExceptions | None) -> frozenset[str]:
    """Return ``native-exec:<agent>`` per agent keeping native exec, or empty when WASM is off or unknown.

    Args:
        state: The sandbox state, or None.
        exceptions: The matching gate inputs, or None.

    Returns:
        The tokens.

    Raises:
        ValueError: A bad agent slug.
    """
    if state is None or not state.wasm or exceptions is None:
        return frozenset()
    return frozenset(native_exec_token(a) for a in exceptions.native_exec_keep)


__all__ = [
    "BUILD_LOG_EXCEPTIONS_KEY",
    "BUILD_LOG_STATE_KEY",
    "SCRATCH_REL",
    "WASM_LANGUAGES",
    "SandboxExceptions",
    "SandboxState",
    "grant_from_tokens",
    "grant_tokens",
    "keep_tokens",
    "previous_sandbox_exceptions",
    "previous_sandbox_state",
    "state_tokens",
]
