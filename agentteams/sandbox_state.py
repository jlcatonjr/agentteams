"""sandbox_state.py — the effective sandbox state of a render, its build-log record, and the previous state.

METAPLAN §2.1: P15 owns the frozen :class:`SandboxState` (the OS switch, the WASM switch, the WASM languages and
per-agent grants, and the projected model-substrate switch). P00 adds the OS derivation
(:func:`effective_sandbox_state`, :func:`os_sandbox_requested`, :func:`validate_os_sandbox`); P01 extends it for WASM.
Every consumer that reasons about a boundary reads this, never ``privilege_profile`` directly.

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

from agentteams import _sandbox_profiles as _profiles
from agentteams.sandbox_tokens import native_exec_token, read_token, scratch_token, valid_rel
from agentteams.wasm_config import ON_OFF_VALUES, validate_on_off

if TYPE_CHECKING:
    from agentteams.wasm_exec import Grant, Limits

#: The WASM guest languages, in order. Mirrors ``wasm_guests.LANGUAGES`` (P04, the single source that
#: ``wasm_config`` validates against); ``tests/test_wasm_config.py`` pins the two equal.
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
#: counts for every framework. Claude's evidence is content-checked (:data:`_OS_EVIDENCE_SANDBOX_JSON`): the example
#: is emitted for every Claude team, so only one whose ``sandbox.enabled`` is True is a boundary.
_OS_EVIDENCE: dict[str, tuple[str, ...]] = {
    "goose": (".goose/sandbox.sb",),
    "codex": (".codex/confined-run.example.sh",),
    "grok": (".grok/sandbox.toml",),
}
#: JSON files that are OS-boundary evidence when their ``sandbox.enabled`` is exactly True: the live Claude settings
#: and the previously emitted settings example (wave-2 @security S1: a team that emitted a boundary but never merged
#: it still had one). The example is also looked for beside the team dir (``<agents>/../``), where it is emitted.
_OS_EVIDENCE_SANDBOX_JSON: dict[str, tuple[str, ...]] = {
    "claude": (".claude/settings.json", ".claude/settings.hooks.example.json"),
}
_CLAUDE_EXAMPLE_NAME = "settings.hooks.example.json"
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


def grant_from_tokens(agent: str, tokens: Iterable[str], limits: Limits, *, project_root: Path,
                      check_credentials: bool = True) -> Grant:
    """Build ``agent``'s grant from its tokens (the inverse of :func:`grant_tokens`).

    Mounts are in a canonical order: the read mounts sorted by path, then ``/scratch``. The one grant builder: the
    brief's grants (``wasm_config``) and the runner's jails (``runner_wasm.build_grant``) both come through here.

    Args:
        agent: The agent slug.
        tokens: ``wasm:<agent>:read:<rel>`` / ``wasm:<agent>:scratch`` tokens.
        limits: The grant's limits.
        project_root: The project root the host paths are resolved against.
        check_credentials: Scan each existing read directory for credential-named entries
            (``_sandbox_profiles.refuse_credentials``, bounded; wave-3 @security S3). False only when parsing a
            recorded state (bookkeeping, nothing is mounted); ``wasm_exec.materialize`` scans again before a run.

    Returns:
        The grant.

    Raises:
        ValueError: A token for another agent, an unknown token, a read path that resolves through a symlink
            (its realpath is not the plain path inside the project), or a read directory holding credential-named
            entries or too large to scan.
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
        if check_credentials and host.is_dir():
            try:
                _profiles.refuse_credentials(str(host), f"read path {rel!r}")
            except _profiles.ConfinementError as exc:
                raise ValueError(str(exc)) from exc
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
        wasm_reason: ``"brief"`` | ``"cli"`` | ``"framework"`` (``--framework wasm``, P08) | ``"default-off"`` |
            ``"unknown"``.
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
        if any(v not in ON_OFF_VALUES for v in switches):
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
        return grant_from_tokens(agent, tokens, wx.Limits(**limits), project_root=project_root,
                                 check_credentials=False)
    except (TypeError, ValueError, wx.WasmExecError):   # out-of-bounds limits are malformed too, not a crash
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


def _json_sandbox_enabled(path: Path) -> bool:
    """True when the JSON file at ``path`` has ``sandbox.enabled`` exactly True (booleans only, no echo)."""
    if not os.path.lexists(path):
        return False
    try:
        data = json.loads(_read_regular_text(path))
    except (ValueError, json.JSONDecodeError):
        return False
    sandbox = data.get("sandbox") if isinstance(data, dict) else None
    return isinstance(sandbox, dict) and sandbox.get("enabled") is True


def _sandbox_json_evidence(root: Path, team_dir: Path, framework_id: str) -> bool:
    """True when any of ``framework_id``'s content-checked evidence files enables a sandbox."""
    paths = [root / p for p in _OS_EVIDENCE_SANDBOX_JSON.get(framework_id, ())]
    beside_team = Path(os.path.realpath(team_dir)).parent
    if framework_id == "claude" and beside_team.is_relative_to(root):  # never read outside the project
        paths.append(beside_team / _CLAUDE_EXAMPLE_NAME)
    return any(_json_sandbox_enabled(p) for p in dict.fromkeys(paths))


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
    2. Evidence, OR-ed in (never AND): ``os`` is on when this framework's boundary files exist (for claude: the live
       settings or the emitted ``settings.hooks.example.json`` with ``sandbox.enabled`` True); ``wasm`` when a runtime
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
    os_evidence = any(os.path.lexists(root / p) for p in os_paths) or _sandbox_json_evidence(
        root, Path(team_dir), framework_id)
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


#: The two values of the dedicated OS-sandbox switch (brief ``os_sandbox``, argv ``--os-sandbox``; P00).
OS_SANDBOX_VALUES: tuple[str, ...] = ON_OFF_VALUES

#: Transient manifest key carrying ``--os-sandbox`` (operator argv). Set only by ``cli/generate.py`` and the
#: ``--check-wiring`` path, never read from input: :func:`effective_sandbox_state` honours it only as one of
#: :data:`OS_SANDBOX_VALUES`. The team manifest is never persisted whole, and the build-log records only
#: ``sandbox_state``, so it never reaches disk.
OS_SANDBOX_CLI_KEY = "_os_sandbox_cli"

#: Transient manifest key carrying the ``*:sandbox`` tokens the operator passed explicitly on
#: ``--target-host-features`` (set by ``cli.artifacts.resolve_host_features_and_advise``; never from input).
EXPLICIT_SANDBOX_TOKENS_KEY = "_explicit_sandbox_tokens"

_CONFINING_PROFILES = frozenset({"confined", "exclusive"})


def validate_os_sandbox(value: object) -> str | None:
    """Return the validated ``os_sandbox`` value.

    Fails closed on a typo (CC-6 analogue of ``validate_privilege_profile``): ``"OFF"``, ``true`` or ``1`` look like
    a choice while meaning none, so they are refused rather than ignored.

    Args:
        value: The raw brief / argv value.

    Returns:
        ``None`` for an absent value, else ``"on"`` or ``"off"``.

    Raises:
        ValueError: Any other value.
    """
    return validate_on_off(value, "os_sandbox")


def _sandbox_tokens(manifest: dict[str, Any]) -> list[str]:
    return [t for t in (manifest.get("host_features") or []) if isinstance(t, str) and t.endswith(":sandbox")]


def legacy_os_requested(manifest: dict[str, Any]) -> bool:
    """Return today's (pre-``os_sandbox``) framework-neutral request test, ignoring the switch.

    A confined/exclusive ``privilege_profile`` or any ``*:sandbox`` host-feature token. Kept for the one consumer that
    must never see fewer boundaries than before the switch existed: the control-plane stubs a sibling team's launcher
    needs (``control_plane_io.stubs_enabled``, METAPLAN R9).

    Args:
        manifest: The team manifest.

    Returns:
        Whether the profile or a token requests a boundary.

    Raises:
        Nothing.
    """
    return manifest.get("privilege_profile") in _CONFINING_PROFILES or bool(_sandbox_tokens(manifest))


def _os_switch(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(os, os_reason)`` by the P00 precedence (first match wins). Raises ValueError on a bad value or the
    argv conflict."""
    cli = validate_os_sandbox(manifest.get(OS_SANDBOX_CLI_KEY))
    explicit = [t for t in (manifest.get(EXPLICIT_SANDBOX_TOKENS_KEY) or ())
                if isinstance(t, str) and t.endswith(":sandbox")]
    if cli == "off" and explicit:
        raise ValueError(f"--os-sandbox off conflicts with the explicit --target-host-features {explicit[0]}; "
                         "drop one of them")
    if cli is not None:
        return cli == "on", "cli"
    if explicit:  # operator argv beats the (agent-writable) brief
        return True, f"host-feature:{explicit[0]}"
    brief = validate_os_sandbox(manifest.get("os_sandbox"))
    if brief is not None:
        return brief == "on", "brief"
    profile = manifest.get("privilege_profile")
    if profile in _CONFINING_PROFILES:
        return True, f"profile:{profile}"
    tokens = _sandbox_tokens(manifest)
    if tokens:
        return True, f"host-feature:{tokens[0]}"
    return False, f"profile:{profile}" if isinstance(profile, str) and _SLUG_RE.match(profile) else "profile:none"


def os_switch_state(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(os, os_reason)`` alone: the OS half of :func:`effective_sandbox_state`, without the WASM half.

    For consumers that ask only about the OS boundary (the emitter twins, the gate-hook flip): it builds no WASM
    grant, so it needs no project root.

    Args:
        manifest: The team manifest.

    Returns:
        ``(os, os_reason)`` by the P00 precedence.

    Raises:
        ValueError: As :func:`effective_sandbox_state` (OS values only).
    """
    return _os_switch(manifest)


def effective_sandbox_state(manifest: dict[str, Any]) -> SandboxState:
    """Return the effective sandbox state of ``manifest`` (METAPLAN §2.1; P00 derives the OS half, P01 the WASM half).

    OS precedence, first match wins: ``--os-sandbox`` (:data:`OS_SANDBOX_CLI_KEY`) -> an explicit argv
    ``*:sandbox`` token (:data:`EXPLICIT_SANDBOX_TOKENS_KEY`) -> the brief's ``os_sandbox`` -> a confined/exclusive
    ``privilege_profile`` -> any ``*:sandbox`` host-feature token -> off. With no switch set this is exactly the
    pre-P00 framework-neutral request test (:func:`legacy_os_requested`), so existing renders are unchanged.

    WASM (P01, :func:`agentteams.wasm_config.wasm_state_fields`): ``--wasm-sandbox`` > the brief's
    ``wasm_sandbox.enabled`` > off. Off: no languages, no grants (an inert grant is not a relaxation). On: the
    block's languages and a ``Grant(mounts, limits)`` per agent whose grant is not compute-only, host paths under
    the CLI-set project root.

    Model substrate (P20 D1a, :func:`agentteams.wasm_config.substrate_switch`): ``--wasm-substrate`` > the brief's
    ``wasm_sandbox.model_substrate`` > the value of a pipeline-owned projection the CLI kept in place > None.
    Independent of the WASM switch: neither one changes the other.

    Args:
        manifest: The team manifest (``privilege_profile``, ``host_features``, ``os_sandbox``, ``wasm_sandbox``,
            ``agent_slug_list`` and the transient CLI keys).

    Returns:
        The state.

    Raises:
        ValueError: An ``os_sandbox`` value other than on/off, ``--os-sandbox off`` together with an explicit
            ``--target-host-features <fw>:sandbox``, a malformed ``wasm_sandbox`` / ``--wasm-sandbox`` /
            ``--wasm-substrate`` value, or a WASM mount needed while the project root is unknown (fail closed).
        ImportError: WASM is on and :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    from agentteams.wasm_config import substrate_switch, wasm_state_fields

    os_on, os_reason = _os_switch(manifest)
    wasm, wasm_reason, languages, grants = wasm_state_fields(manifest)
    return SandboxState(os=os_on, os_reason=os_reason, wasm=wasm, wasm_reason=wasm_reason, wasm_languages=languages,
                        wasm_grants=grants, model_substrate=substrate_switch(manifest)[0])


def wasm_grant_for(state: SandboxState, agent: str) -> Grant | None:
    """Return the grant ``agent``'s WASM guests run under, or None when WASM is off (METAPLAN R9, plan D8).

    Reads only :class:`SandboxState`, never the brief or the manifest. ``None`` tells the caller not to run a guest;
    there is no exception path.

    Args:
        state: The effective sandbox state.
        agent: The agent slug.

    Returns:
        None iff ``state.wasm`` is false; else the agent's grant, or ``wasm_exec.COMPUTE_ONLY`` (the same object)
        when it has none.

    Raises:
        ImportError: WASM is on and :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    if not state.wasm:
        return None
    grant = state.wasm_grants.get(agent)
    return grant if grant is not None else _wasm_exec().COMPUTE_ONLY


def wasm_eligible(agent_meta: Mapping[str, object]) -> bool:
    """Return whether an agent is offered ``run_wasm`` while WASM is on (R9; see :func:`agentteams.wasm_config.
    wasm_eligible`, the single predicate P11/P13/P14 read).

    Args:
        agent_meta: ``{"slug": str, "tools": list[str]}``.

    Returns:
        True iff the tools hold ``execute`` (D12); False for anything else, including missing tools.

    Raises:
        Nothing.
    """
    from agentteams.wasm_config import wasm_eligible as eligible

    return eligible(agent_meta)


def os_sandbox_requested(manifest: dict[str, Any], token: str | None = None) -> bool:
    """Return whether an OS boundary is emitted for ``manifest`` (the one helper every emitter twin delegates to).

    Args:
        manifest: The team manifest.
        token: The calling emitter's own token (``"claude:sandbox"``, ``"goose:sandbox"``, ...), or None for the
            framework-neutral question (the launcher, write-root widening).

    Returns:
        False when the effective OS state is off. When on: True for ``token=None``, when ``token`` is in
        ``host_features``, or when the switch or the profile asks (reason ``cli`` / ``brief`` / ``profile:*``),
        which keeps the convert/render paths (no profile->token union) emitting for a confined manifest. A
        boundary that is on only because of ANOTHER framework's token is not this framework's.

    Raises:
        ValueError: As :func:`effective_sandbox_state`.
    """
    os_on, os_reason = _os_switch(manifest)  # the OS half only: no WASM grant (and so no project root) needed
    if not os_on:
        return False
    if token is None or token in (manifest.get("host_features") or []):
        return True
    return os_reason in ("cli", "brief") or os_reason.startswith("profile:")


def os_switch_set(manifest: dict[str, Any]) -> bool:
    """Return whether the dedicated OS switch is set at all (brief ``os_sandbox`` or ``--os-sandbox``).

    Args:
        manifest: The team manifest.

    Returns:
        True when either is present.

    Raises:
        Nothing.
    """
    return manifest.get("os_sandbox") is not None or manifest.get(OS_SANDBOX_CLI_KEY) is not None


def verifier_sandbox_state(manifest: dict[str, Any] | None, team_dir: Path,
                           project_root: Path) -> SandboxState | None:
    """Return the sandbox state a read-only drift check compares live config against (P00 D8).

    The manifest's effective state when a manifest is given (the brief, plus ``--os-sandbox``), else the
    ``sandbox_state`` the last render recorded in the build-log. Evidence files are NOT OR-ed in here: a drift check
    must report what the brief asks for, not what disk suggests.

    Args:
        manifest: The team manifest, or None.
        team_dir: The team directory (holds ``references/build-log.json``).
        project_root: The project root.

    Returns:
        The state, or None when neither source says anything (no manifest, no usable record).

    Raises:
        ValueError: An invalid ``os_sandbox`` value in ``manifest``, or a malformed ``wasm_sandbox`` block.
        ImportError: The recorded state carries WASM grants and :mod:`agentteams.wasm_exec` is not importable
            (fail closed: a drift check never reports "no state" because the executor is missing; wave-3 H6).
    """
    if manifest is not None:
        from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY

        if not isinstance(manifest.get(PROJECT_ROOT_KEY), Path):  # the CLI's root, so WASM grants can be built
            manifest = {**manifest, PROJECT_ROOT_KEY: Path(project_root).resolve()}
        return effective_sandbox_state(manifest)
    log = _read_build_log(team_dir)
    if not log:
        return None
    return SandboxState.from_record(log.get(BUILD_LOG_STATE_KEY), project_root=_root(project_root))


def recorded_model_substrate(team_dir: Path) -> str | None:
    """Return the ``model_substrate`` the last render recorded in the build-log, without rebuilding any grant (P20).

    Reads only that one field, so a missing :mod:`agentteams.wasm_exec` or a malformed grant elsewhere in the record
    can never hide a recorded ``"on"`` floor from ``--verify-integrity`` (``sandbox_projection.verify_projection``).

    Args:
        team_dir: The team directory (holds ``references/build-log.json``).

    Returns:
        ``"on"`` / ``"off"``, or None when the build-log is absent or unusable or records no such value.

    Raises:
        Nothing.
    """
    record = (_read_build_log(team_dir) or {}).get(BUILD_LOG_STATE_KEY)
    value = record.get("model_substrate") if isinstance(record, dict) else None
    return value if isinstance(value, str) and value in ON_OFF_VALUES else None


def has_recorded_state(team_dir: Path) -> bool:
    """Return whether the previous render recorded a ``sandbox_state`` (or left an unusable build-log).

    Once a team has used a sandbox switch, every later render keeps going through the relaxation gate, so removing
    the switch from the brief cannot step around it.

    Args:
        team_dir: The team directory.

    Returns:
        True when the build-log carries the key, or exists but cannot be read (fail toward governed).
    """
    log = _read_build_log(team_dir)
    return log is not None and (log == {} or BUILD_LOG_STATE_KEY in log)


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
    "EXPLICIT_SANDBOX_TOKENS_KEY",
    "OS_SANDBOX_CLI_KEY",
    "OS_SANDBOX_VALUES",
    "SCRATCH_REL",
    "WASM_LANGUAGES",
    "SandboxExceptions",
    "SandboxState",
    "effective_sandbox_state",
    "grant_from_tokens",
    "grant_tokens",
    "has_recorded_state",
    "keep_tokens",
    "legacy_os_requested",
    "os_sandbox_requested",
    "os_switch_set",
    "os_switch_state",
    "previous_sandbox_exceptions",
    "previous_sandbox_state",
    "recorded_model_substrate",
    "state_tokens",
    "validate_os_sandbox",
    "verifier_sandbox_state",
    "wasm_eligible",
    "wasm_grant_for",
]
