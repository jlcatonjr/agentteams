"""wasm_config.py — the brief's ``wasm_sandbox`` block, the ``--wasm-sandbox`` switch, and WASM eligibility (P01).

METAPLAN §2.1 / R9: one validated, fail-closed place to turn WASM confinement on and shape it. The brief block
``wasm_sandbox`` (``enabled``, ``languages``, ``limits``, per-agent ``agents`` grants, ``native_exec``,
``native_exec_keep``, the projected ``model_substrate``) is normalised by :func:`normalize_wasm_sandbox` into a
:class:`WasmConfig`; ``analyze.build_manifest`` carries :meth:`WasmConfig.to_record` as ``manifest["wasm_sandbox"]``
only when the brief has the block (every other manifest stays byte-identical). ``--wasm-sandbox on|off`` is the
transient :data:`WASM_SANDBOX_CLI_KEY` and beats the brief's ``enabled``.

:func:`wasm_state_fields` is the WASM half of :func:`agentteams.sandbox_state.effective_sandbox_state`: the switch,
its reason, the languages and the per-agent :class:`~agentteams.wasm_exec.Grant` values built through the one
token<->``Mount`` mapping in :mod:`agentteams.sandbox_state` (``grant_from_tokens``). Brief ``read_paths`` become
read-only ``/project/<rel>`` mounts at ``<project>/<rel>``; ``scratch: true`` becomes the rw ``/scratch`` mount at
``<project>/.agentteams/wasm/scratch/<agent>/`` (R8: agentteams picks the location, never the brief). Network has no
key at all, so no value can widen it (D5).

Fail-closed rules (every refusal is a :class:`ValueError` naming the brief key path, e.g.
``wasm_sandbox.agents.analyst.read_paths[1]``): unknown keys, wrong types (``bool`` is not an integer here), values
outside the enums, limits outside ``wasm_exec.LIMIT_BOUNDS`` (never clamped), unknown agent slugs, empty grant entries,
and any read path that is absolute, ``~``-relative, has a ``..``/``.``/empty segment or a character outside the
guest-name grammar, is at, inside or above the project control plane, names credential material, is a governance /
trust root, or resolves through a symlink.

Rule-12 carve-outs (audit targets): (1) grants are carried while WASM is off but are inert (no mount, no token) — the
moment WASM turns on the gate sees them as a widening over compute-only; (2) block limits differing from the defaults
give every agent a grant entry, but limits mint no token (raising them within ``LIMIT_BOUNDS`` is not a widening);
(3) the default :data:`NATIVE_EXEC_KEEP_DEFAULT` (``navigator``, ``git-operations``: they need git) keeps native exec
for two eligible agents (D12) — each entry is a ``native-exec-keep`` effect through the P15 gate (governed: the
operator's signed decision once; ungoverned: notice + record), never a silent keep.

Credential names come from the one shared list in :mod:`agentteams._sandbox_profiles` (``credential_name``), and a
granted read directory is scanned for them (bounded) when the grant is built (wave-3 @security S1/S3).

Integrity-pinned (it decides what a guest may read and write).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    from agentteams.wasm_exec import Grant, Limits

#: Transient manifest key carrying ``--wasm-sandbox`` (operator argv). Set only by ``cli/generate.py`` (through
#: ``cli.wasm_sandbox_switch``) and the ``--check-wiring`` path, never read from input: honoured only as one of
#: :data:`WASM_SANDBOX_VALUES`. The team manifest is never persisted whole, so it never reaches disk.
WASM_SANDBOX_CLI_KEY = "_wasm_sandbox_cli"

#: The two values of every sandbox on/off switch (``os_sandbox`` / ``--os-sandbox``, ``--wasm-sandbox``,
#: ``--wasm-substrate`` / ``wasm_sandbox.model_substrate``); the one constant :func:`validate_on_off` checks.
ON_OFF_VALUES: tuple[str, ...] = ("on", "off")
#: The two values of ``--wasm-sandbox`` and of ``--wasm-substrate`` / ``wasm_sandbox.model_substrate``.
WASM_SANDBOX_VALUES: tuple[str, ...] = ON_OFF_VALUES

#: Transient manifest key carrying ``--wasm-substrate`` (operator argv; P20 D3). Set only by ``cli/generate.py`` and
#: the ``--check-wiring`` path, never read from input; validated by :func:`validate_wasm_substrate_cli`.
WASM_SUBSTRATE_CLI_KEY = "_wasm_substrate_cli"

#: Transient manifest key carrying the value of a pipeline-owned ``.agentteams/sandbox-state.json`` left in place
#: when neither the brief nor argv sets the substrate switch (P20 D4: the floor is kept, never silently dropped).
#: Set only by ``cli.wasm_sandbox_switch.apply_wasm_switch`` from disk, never from input.
WASM_SUBSTRATE_KEPT_KEY = "_wasm_substrate_kept"

#: Transient manifest key set only by ``cli/wasm_sandbox_switch.apply_wasm_switch`` when the target is
#: ``--framework wasm`` and neither the brief nor ``--wasm-sandbox`` names the switch (P08 §4.6): the framework choice
#: is the opt-in (D6), reason ``"framework"``. Honoured only as the boolean ``True``; never read from input.
WASM_FRAMEWORK_KEY = "_wasm_sandbox_framework"

#: ``native_exec`` policies (applied per provider by P13; validated and carried here).
NATIVE_EXEC_VALUES: tuple[str, ...] = ("replace", "deny", "keep")
NATIVE_EXEC_DEFAULT = "replace"

#: Agents exempt by design from native-exec removal (P13); they may not be listed in ``native_exec_keep``.
NATIVE_EXEC_EXEMPT: tuple[str, ...] = ("orchestrator", "team-builder")

#: The ``native_exec_keep`` used when the brief block omits the key (operator decision D12): these agents need git,
#: so they keep native exec while WASM is on. Only slugs in the team are kept. An explicit brief list replaces it
#: (list these too to extend it; ``[]`` keeps none). Each kept agent is a ``native-exec-keep`` gate effect.
NATIVE_EXEC_KEEP_DEFAULT: tuple[str, ...] = ("git-operations", "navigator")

_TOP_KEYS = ("enabled", "languages", "limits", "agents", "native_exec", "native_exec_keep", "model_substrate")
_GRANT_KEYS = ("read_paths", "scratch")
_BRIEF_LIMIT_KEYS = ("fuel", "memory_mib", "timeout_s")
#: Bytes per MiB: the brief's ``memory_mib`` unit (shared with ``runner_wasm``'s jail limits).
MIB = 1 << 20
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SUBSTRATE_VALUES = (None, "on", "off")


def _wasm_exec() -> Any:
    """Import :mod:`agentteams.wasm_exec` lazily (stdlib-only). An ImportError propagates: fail closed."""
    from agentteams import wasm_exec

    return wasm_exec


def _languages() -> tuple[str, ...]:
    """Return ``wasm_guests.LANGUAGES`` (P04, the single source)."""
    from agentteams.wasm_guests import LANGUAGES

    return tuple(LANGUAGES)


@dataclass(frozen=True)
class AgentWasmGrant:
    """One agent's brief-level widening beyond compute-only.

    Attributes:
        read_paths: Normalised project-relative paths (POSIX, no trailing ``/``), de-duplicated and sorted.
        scratch: Whether the agent gets its private writable ``/scratch``.
    """

    read_paths: tuple[str, ...] = ()
    scratch: bool = False


@dataclass(frozen=True)
class WasmConfig:
    """The normalised ``wasm_sandbox`` block.

    Attributes:
        enabled: The brief's switch (``--wasm-sandbox`` overrides it in the effective state).
        languages: A non-empty subset of ``wasm_guests.LANGUAGES``, in that order.
        limits: The per-run :class:`~agentteams.wasm_exec.Limits` every agent gets.
        agents: Agent slug -> :class:`AgentWasmGrant` (sorted by slug; read-only mapping).
        native_exec: One of :data:`NATIVE_EXEC_VALUES`.
        native_exec_keep: Agents keeping native execution while WASM is on (sorted; default empty).
        model_substrate: ``None`` | ``"on"`` | ``"off"``: the projected model-substrate switch (P20,
            :func:`substrate_switch`); independent of ``enabled``.
    """

    enabled: bool
    languages: tuple[str, ...]
    limits: Limits
    agents: Mapping[str, AgentWasmGrant] = field(default_factory=dict)
    native_exec: str = NATIVE_EXEC_DEFAULT
    native_exec_keep: tuple[str, ...] = ()
    model_substrate: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents", MappingProxyType(dict(sorted(dict(self.agents).items()))))

    def to_record(self) -> dict[str, object]:
        """Return the team-manifest record (``team-manifest.schema.json`` ``wasm_sandbox``).

        Returns:
            ``{"enabled","languages","limits":{"fuel","max_memory_bytes","timeout_s"},
            "agents":{slug:{"read_paths","scratch"}},"native_exec","native_exec_keep","model_substrate"}``.
        """
        return {
            "enabled": self.enabled,
            "languages": list(self.languages),
            "limits": {"fuel": self.limits.fuel, "max_memory_bytes": self.limits.max_memory_bytes,
                       "timeout_s": self.limits.timeout_s},
            "agents": {slug: {"read_paths": list(g.read_paths), "scratch": g.scratch}
                       for slug, g in self.agents.items()},
            "native_exec": self.native_exec,
            "native_exec_keep": list(self.native_exec_keep),
            "model_substrate": self.model_substrate,
        }


def default_keep(agent_slugs: Iterable[str]) -> tuple[str, ...]:
    """Return :data:`NATIVE_EXEC_KEEP_DEFAULT` restricted to the team's agents (D12).

    Args:
        agent_slugs: The team's agent slugs.

    Returns:
        The sorted default keep entries that are agents of this team.

    Raises:
        Nothing.
    """
    slugs = frozenset(agent_slugs)
    return tuple(sorted(s for s in NATIVE_EXEC_KEEP_DEFAULT if s in slugs))


def default_wasm_config(agent_slugs: Iterable[str] = ()) -> WasmConfig:
    """Return the configuration of an absent block (WASM off, all languages, default limits, no grants).

    Args:
        agent_slugs: The team's agent slugs; the default ``native_exec_keep`` (D12) names those among them.

    Returns:
        The default :class:`WasmConfig`.

    Raises:
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    return WasmConfig(enabled=False, languages=_languages(), limits=_wasm_exec().DEFAULT_LIMITS,
                      native_exec_keep=default_keep(agent_slugs))


# --- validation helpers ------------------------------------------------------------------------------------------

def _require_dict(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object (got {type(value).__name__})")
    return value


def _refuse_unknown(value: dict, allowed: Iterable[str], path: str) -> None:
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        raise ValueError(f"{path} has unknown key(s) {unknown}; allowed: {', '.join(allowed)}")


def _languages_of(raw: object, path: str) -> tuple[str, ...]:
    supported = _languages()
    if raw is None:
        return supported
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} must be a non-empty list of {', '.join(supported)}")
    for i, lang in enumerate(raw):
        if not isinstance(lang, str) or lang not in supported:
            raise ValueError(f"{path}[{i}] = {lang!r} is not a supported WASM language ({', '.join(supported)})")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{path} lists a language twice")
    return tuple(lang for lang in supported if lang in raw)


def _brief_limit_bounds() -> dict[str, tuple[int, int]]:
    """Return the brief's limit bounds: ``LIMIT_BOUNDS`` in whole MiB / seconds, rounded inward."""
    b = _wasm_exec().LIMIT_BOUNDS
    mem_lo, mem_hi = b["max_memory_bytes"]
    return {"fuel": b["fuel"], "memory_mib": (-(-mem_lo // MIB), mem_hi // MIB), "timeout_s": b["timeout_s"]}


def limits_of(raw: object, path: str) -> Limits:
    """Return the :class:`~agentteams.wasm_exec.Limits` of a brief-shaped ``limits`` object (MiB -> bytes).

    The one ``{fuel, memory_mib, timeout_s}`` -> ``Limits`` conversion (``runner_wasm``'s jail limits use it too).

    Args:
        raw: ``{"fuel"?, "memory_mib"?, "timeout_s"?}`` (integers), or None for the defaults.
        path: The key path for messages.

    Returns:
        The limits; absent keys take ``wasm_exec.DEFAULT_LIMITS``.

    Raises:
        ValueError: An unknown key, a non-integer, or a value outside the brief bounds (never clamped).
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    wx = _wasm_exec()
    if raw is None:
        return wx.DEFAULT_LIMITS
    raw = _require_dict(raw, path)
    _refuse_unknown(raw, _BRIEF_LIMIT_KEYS, path)
    bounds = _brief_limit_bounds()
    for key, value in raw.items():
        lo, hi = bounds[key]
        if type(value) is not int:  # bool is an int subclass: refused, as are floats and strings
            raise ValueError(f"{path}.{key} must be an integer (got {value!r})")
        if not lo <= value <= hi:
            raise ValueError(f"{path}.{key} = {value} is outside [{lo}, {hi}]; out-of-range limits are refused, "
                             "never clamped")
    d = wx.DEFAULT_LIMITS
    try:
        return wx.Limits(fuel=raw.get("fuel", d.fuel),
                         max_memory_bytes=raw["memory_mib"] * MIB if "memory_mib" in raw else d.max_memory_bytes,
                         timeout_s=raw.get("timeout_s", d.timeout_s))
    except wx.WasmExecError as exc:  # defence in depth: the executor's own bounds
        raise ValueError(f"{path}: {exc}") from exc


def _slug_in_team(value: object, path: str, agent_slugs: frozenset[str]) -> str:
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise ValueError(f"{path} = {value!r} is not an agent slug (^[a-z0-9][a-z0-9-]*$)")
    if value not in agent_slugs:
        raise ValueError(f"{path} names {value!r}, which is not an agent in this team")
    return value


def _ancestor_of_control_plane(rel: str) -> str | None:
    """Return a control-plane entry ``rel`` is an ancestor of (e.g. ``.github`` holds ``.github/agents``)."""
    from agentteams.frameworks._write_roots import PROJECT_PROTECTED

    low = rel.lower() + "/"
    return next((p for p in PROJECT_PROTECTED if p.lower().startswith(low)), None)


def validate_read_path(raw: object, path: str, *, project_root: Path | None) -> str:
    """Return the normalised form of one brief ``read_paths`` entry, or raise.

    Args:
        raw: The brief value.
        path: The brief key path for messages (``wasm_sandbox.agents.<slug>.read_paths[<i>]``).
        project_root: The project root for the symlink check, or None to skip it (repeated with the real root in
            the effective state).

    Returns:
        The path, POSIX, without a trailing ``/``.

    Raises:
        ValueError: A refused path (see the module docstring).
    """
    from agentteams.cli.governance_targets import target_is_governance_root
    from agentteams.cli.sandbox_effects import valid_rel
    from agentteams.frameworks._write_roots import control_plane_of, path_char_problem

    if not isinstance(raw, str):
        raise ValueError(f"{path} must be a string (got {raw!r})")
    problem = path_char_problem(raw)
    if problem:
        raise ValueError(f"{path} = {raw!r} is refused: {problem}")
    if raw.startswith("/") or raw.startswith("~") or os.path.isabs(raw):
        raise ValueError(f"{path} = {raw!r} must be project-relative (no absolute or ~ path)")
    rel = raw.rstrip("/")
    if rel in ("", "."):
        raise ValueError(f"{path} = {raw!r} names the whole project; list the directories the agent needs")
    if ".." in rel.split("/"):
        raise ValueError(f"{path} = {raw!r} has a '..' segment (a read path stays inside the project)")
    try:
        valid_rel(rel)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    entry = control_plane_of(rel, platform="darwin") or _ancestor_of_control_plane(rel)  # case-folded: over-refuse
    if entry is not None:
        raise ValueError(f"{path} = {raw!r} reaches the project control plane ({entry}); refused")
    from agentteams._sandbox_profiles import credential_name

    secret = next((s for s in rel.split("/") if credential_name(s)), None)
    if secret is not None:
        raise ValueError(f"{path} = {raw!r} names credential material ({secret!r}); refused")
    if target_is_governance_root(rel):
        raise ValueError(f"{path} = {raw!r} is a governance / trust root; refused")
    if project_root is not None:
        check_inside_project(rel, project_root, path)
    return rel


def check_inside_project(rel: str, project_root: Path, path: str) -> None:
    """Refuse ``rel`` when ``<project_root>/<rel>`` resolves through a symlink (in or out of the project).

    Args:
        rel: A normalised project-relative path.
        project_root: The project root.
        path: The brief key path for the message.

    Raises:
        ValueError: The realpath differs from the plain path inside the real root.
    """
    root = Path(os.path.realpath(project_root))
    plain = root / rel
    if os.path.realpath(plain) != str(plain):
        raise ValueError(f"{path} = {rel!r} resolves through a symlink (to {os.path.realpath(plain)}); a read path "
                         "must name the real path inside the project")


def _agents_of(raw: object, path: str, agent_slugs: frozenset[str],
               project_root: Path | None) -> dict[str, AgentWasmGrant]:
    if raw is None:
        return {}
    raw = _require_dict(raw, path)
    out: dict[str, AgentWasmGrant] = {}
    for slug, entry in raw.items():
        where = f"{path}.{slug}"
        _slug_in_team(slug, f"{path} key", agent_slugs)
        entry = _require_dict(entry, where)
        _refuse_unknown(entry, _GRANT_KEYS, where)
        reads = entry.get("read_paths", [])
        if not isinstance(reads, list):
            raise ValueError(f"{where}.read_paths must be a list of project-relative paths")
        scratch = entry.get("scratch", False)
        if not isinstance(scratch, bool):
            raise ValueError(f"{where}.scratch must be true or false (got {scratch!r})")
        norm = sorted({validate_read_path(p, f"{where}.read_paths[{i}]", project_root=project_root)
                       for i, p in enumerate(reads)})
        if not norm and not scratch:
            raise ValueError(f"{where} is an empty grant (no read_paths, no scratch): remove the entry")
        out[slug] = AgentWasmGrant(read_paths=tuple(norm), scratch=scratch)
    return out


def _keep_of(raw: object, path: str, agent_slugs: frozenset[str]) -> tuple[str, ...]:
    if raw is None:
        return default_keep(agent_slugs)
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a list of agent slugs")
    for i, slug in enumerate(raw):
        _slug_in_team(slug, f"{path}[{i}]", agent_slugs | frozenset(NATIVE_EXEC_EXEMPT))
        if slug in NATIVE_EXEC_EXEMPT:
            raise ValueError(f"{path}[{i}] = {slug!r} is exempt by design; remove the entry")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{path} lists an agent twice")
    return tuple(sorted(raw))


def normalize_wasm_sandbox(raw: object, *, agent_slugs: Iterable[str],
                           project_root: Path | None = None) -> WasmConfig:
    """Validate and normalise the brief's ``wasm_sandbox`` block (fail-closed).

    Args:
        raw: The brief value; ``None`` means the block is absent (:func:`default_wasm_config`).
        agent_slugs: The team's agent slugs (the manifest's ``agent_slug_list``); ``agents`` keys and
            ``native_exec_keep`` entries must be among them. An absent ``native_exec_keep`` takes
            :func:`default_keep` of them (D12).
        project_root: The project root for the read-path symlink check, or None to skip it (the effective state
            repeats it with the CLI's real root).

    Returns:
        The :class:`WasmConfig`.

    Raises:
        ValueError: Any malformed, unknown or out-of-bounds value, naming its brief key path.
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    if raw is None:
        return default_wasm_config(agent_slugs)
    raw = _require_dict(raw, "wasm_sandbox")
    _refuse_unknown(raw, _TOP_KEYS, "wasm_sandbox")
    slugs = frozenset(agent_slugs)
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"wasm_sandbox.enabled must be true or false (got {enabled!r})")
    native_exec = raw.get("native_exec", NATIVE_EXEC_DEFAULT)
    if not isinstance(native_exec, str) or native_exec not in NATIVE_EXEC_VALUES:
        raise ValueError(f"wasm_sandbox.native_exec must be one of {', '.join(NATIVE_EXEC_VALUES)} "
                         f"(got {native_exec!r})")
    substrate = raw.get("model_substrate")
    if substrate not in _SUBSTRATE_VALUES or isinstance(substrate, bool):
        raise ValueError(f"wasm_sandbox.model_substrate must be \"on\" or \"off\" (got {substrate!r})")
    return WasmConfig(
        enabled=enabled,
        languages=_languages_of(raw.get("languages"), "wasm_sandbox.languages"),
        limits=limits_of(raw.get("limits"), "wasm_sandbox.limits"),
        agents=_agents_of(raw.get("agents"), "wasm_sandbox.agents", slugs, project_root),
        native_exec=native_exec,
        native_exec_keep=_keep_of(raw.get("native_exec_keep"), "wasm_sandbox.native_exec_keep", slugs),
        model_substrate=substrate,
    )


def config_from_record(rec: object, *, agent_slugs: Iterable[str]) -> WasmConfig:
    """Re-validate a :meth:`WasmConfig.to_record` value (the manifest's ``wasm_sandbox``).

    The manifest is in-memory pipeline data, but a caller could hand in anything, so the record goes back through
    :func:`normalize_wasm_sandbox` and must round-trip exactly.

    Args:
        rec: The record.
        agent_slugs: The team's agent slugs.

    Returns:
        The configuration.

    Raises:
        ValueError: A malformed record.
    """
    rec = _require_dict(rec, "manifest wasm_sandbox")
    _refuse_unknown(rec, _TOP_KEYS, "manifest wasm_sandbox")
    limits = rec.get("limits")
    raw: dict[str, Any] = {k: v for k, v in rec.items() if k != "limits" and v is not None}
    if isinstance(limits, dict) and set(limits) == {"fuel", "max_memory_bytes", "timeout_s"}:
        mem = limits["max_memory_bytes"]
        if type(mem) is not int or mem % MIB:
            raise ValueError("manifest wasm_sandbox.limits.max_memory_bytes must be a whole number of MiB")
        raw["limits"] = {"fuel": limits["fuel"], "memory_mib": mem // MIB, "timeout_s": limits["timeout_s"]}
    elif limits is not None:
        raise ValueError("manifest wasm_sandbox.limits must be {fuel, max_memory_bytes, timeout_s}")
    cfg = normalize_wasm_sandbox(raw, agent_slugs=agent_slugs)
    if cfg.to_record() != {**cfg.to_record(), **rec}:
        raise ValueError("manifest wasm_sandbox is not in normalised form")
    return cfg


# --- the effective state (WASM half of sandbox_state.effective_sandbox_state) -------------------------------------

def validate_on_off(value: object, label: str) -> str | None:
    """Return a validated sandbox on/off switch value (fail closed on anything else).

    The one validator behind ``--wasm-sandbox``, ``--wasm-substrate`` and ``os_sandbox``
    (``sandbox_state.validate_os_sandbox``): ``"OFF"``, ``true`` or ``1`` look like a choice while meaning none, so
    they are refused rather than ignored.

    Args:
        value: The raw brief / argv value (or the transient manifest key).
        label: The switch's name in the error message (for example ``"--wasm-sandbox"``).

    Returns:
        ``None`` when absent, else ``"on"`` or ``"off"``.

    Raises:
        ValueError: Any other value.
    """
    if value is None:
        return None
    if isinstance(value, str) and value in ON_OFF_VALUES:
        return value
    raise ValueError(f"{label} must be \"on\" or \"off\" (got {value!r})")


def validate_wasm_sandbox_cli(value: object) -> str | None:
    """Return the validated ``--wasm-sandbox`` value (:func:`validate_on_off`).

    Args:
        value: The argv value (or the transient manifest key).

    Returns:
        ``None`` when absent, else ``"on"`` or ``"off"``.

    Raises:
        ValueError: Any other value.
    """
    return validate_on_off(value, "--wasm-sandbox")


def validate_wasm_substrate_cli(value: object) -> str | None:
    """Return the validated ``--wasm-substrate`` value (:func:`validate_on_off`).

    Args:
        value: The argv value (or the transient manifest key).

    Returns:
        ``None`` when absent, else ``"on"`` or ``"off"``.

    Raises:
        ValueError: Any other value.
    """
    return validate_on_off(value, "--wasm-substrate")


def substrate_switch(manifest: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(model_substrate, source)``: ``--wasm-substrate`` > ``wasm_sandbox.model_substrate`` > a kept file.

    The model-substrate switch (P20 D1/D1a) is independent of ``wasm_sandbox.enabled``: it projects a floor to a
    consumer that runs model code (baseAgent's battery) and never changes how agent code runs here.

    Args:
        manifest: The team manifest (the transient :data:`WASM_SUBSTRATE_CLI_KEY` /
            :data:`WASM_SUBSTRATE_KEPT_KEY` and ``wasm_sandbox``).

    Returns:
        ``(None, None)`` when nothing sets it, else ``("on"|"off", "cli"|"brief"|"kept")``. Only ``"cli"`` and
        ``"brief"`` are projected; ``"kept"`` holds the floor a previous render left for the gate.

    Raises:
        ValueError: A bad ``--wasm-substrate`` value, a malformed ``wasm_sandbox`` record, or a kept value other
            than on/off.
    """
    cli = validate_wasm_substrate_cli(manifest.get(WASM_SUBSTRATE_CLI_KEY))
    if cli is not None:
        return cli, "cli"
    brief = manifest_wasm_config(manifest).model_substrate if manifest.get("wasm_sandbox") is not None else None
    if brief is not None:
        return brief, "brief"
    kept = manifest.get(WASM_SUBSTRATE_KEPT_KEY)
    if kept is None:
        return None, None
    return validate_on_off(kept, "the kept model-substrate value"), "kept"


def _slugs_of(manifest: Mapping[str, Any]) -> list[str]:
    slugs = manifest.get("agent_slug_list") or []
    return [s for s in slugs if isinstance(s, str)]


def manifest_wasm_config(manifest: Mapping[str, Any]) -> WasmConfig:
    """Return the manifest's validated WASM configuration (defaults when the block is absent).

    Args:
        manifest: The team manifest.

    Returns:
        The configuration.

    Raises:
        ValueError: A malformed ``manifest["wasm_sandbox"]``.
    """
    rec = manifest.get("wasm_sandbox")
    if rec is None:
        return default_wasm_config(_slugs_of(manifest))
    slugs = set(_slugs_of(manifest))
    if not slugs and isinstance(rec, dict):
        # A manifest without a roster (a direct library call): analyze already checked the slugs against it.
        named = [*(rec.get("agents") or {}), *(rec.get("native_exec_keep") or [])]
        slugs = {s for s in named if isinstance(s, str)}
    return config_from_record(rec, agent_slugs=slugs)


def wasm_switch(manifest: Mapping[str, Any]) -> tuple[bool, str]:
    """Return ``(wasm, wasm_reason)``: ``--wasm-sandbox`` > the brief's ``enabled`` > off.

    Cheap (no grant building, no filesystem): what :func:`agentteams.frameworks._control_plane_protect.
    protect_control_plane` reads, so R1 holds even where no project root is known.

    Args:
        manifest: The team manifest.

    Returns:
        ``(True|False, "cli" | "brief" | "framework" | "default-off")``; ``"framework"`` only when neither names
        the switch and :data:`WASM_FRAMEWORK_KEY` is ``True`` (``--framework wasm``, P08).

    Raises:
        ValueError: A bad ``--wasm-sandbox`` value or a malformed ``wasm_sandbox`` record.
    """
    cli = validate_wasm_sandbox_cli(manifest.get(WASM_SANDBOX_CLI_KEY))
    if cli is not None:
        return cli == "on", "cli"
    if manifest.get("wasm_sandbox") is None:
        return (True, "framework") if manifest.get(WASM_FRAMEWORK_KEY) is True else (False, "default-off")
    return manifest_wasm_config(manifest).enabled, "brief"


def wasm_switch_set(manifest: Mapping[str, Any]) -> bool:
    """Return whether the brief block, ``--wasm-sandbox`` or ``--framework wasm`` sets the switch (the gate engages).

    Args:
        manifest: The team manifest.

    Returns:
        True when any of them is present.
    """
    return (manifest.get("wasm_sandbox") is not None or manifest.get(WASM_SANDBOX_CLI_KEY) is not None
            or manifest.get(WASM_FRAMEWORK_KEY) is True)


def _project_root(manifest: Mapping[str, Any]) -> Path | None:
    from agentteams.frameworks._write_roots import project_root_of

    root = project_root_of(dict(manifest))
    return Path(root) if root is not None else None


def wasm_state_fields(manifest: Mapping[str, Any]
                      ) -> tuple[bool, str, tuple[str, ...], dict[str, Grant]]:
    """Return the WASM half of the effective sandbox state (METAPLAN §2.1, plan D3).

    Off: ``(False, reason, (), {})`` — grants are inert (Rule-12 carve-out 1). On: the languages and, for every
    agent whose grant differs from compute-only, its :class:`~agentteams.wasm_exec.Grant` built by
    ``sandbox_state.grant_from_tokens``. When the block limits differ from ``DEFAULT_LIMITS`` every agent in
    ``agent_slug_list`` gets an entry (possibly mount-less) so the limits apply to all.

    Args:
        manifest: The team manifest (``wasm_sandbox``, the transient CLI key, ``agent_slug_list`` and the CLI-set
            project root ``_project_root``).

    Returns:
        ``(wasm, wasm_reason, wasm_languages, wasm_grants)``.

    Raises:
        ValueError: A malformed block or CLI value, a mount needed while the project root is unknown (fail
            closed), or a read path that resolves through a symlink under the real root or holds credential-named
            entries (or is too large to scan).
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    on, reason = wasm_switch(manifest)
    if not on:
        return False, reason, (), {}
    from agentteams.cli.sandbox_effects import read_token, scratch_token
    from agentteams.sandbox_state import grant_from_tokens

    wx = _wasm_exec()
    cfg = manifest_wasm_config(manifest)
    root = _project_root(manifest)
    grants: dict[str, Grant] = {}
    slugs = list(cfg.agents)
    if cfg.limits != wx.DEFAULT_LIMITS:
        slugs = list(dict.fromkeys([*_slugs_of(manifest), *slugs]))
    for slug in slugs:
        entry = cfg.agents.get(slug, AgentWasmGrant())
        tokens = [read_token(slug, p) for p in entry.read_paths] + ([scratch_token(slug)] if entry.scratch else [])
        if not tokens:
            grants[slug] = wx.Grant(mounts=(), limits=cfg.limits)
            continue
        if root is None:
            raise ValueError(f"wasm_sandbox.agents.{slug} needs host mounts but the project root is unknown; "
                             "run through the CLI (fail closed)")
        # grant_from_tokens is the one symlink + credential check (wave-3 H3/S3); this only adds the key path.
        try:
            grants[slug] = grant_from_tokens(slug, tokens, cfg.limits, project_root=root)
        except ValueError as exc:
            raise ValueError(f"wasm_sandbox.agents.{slug}.read_paths: {exc}") from exc
    return True, reason, cfg.languages, grants


# --- eligibility (R9) ---------------------------------------------------------------------------------------------

def _canonical_tools(tools: object) -> frozenset[str] | None:
    """Return the canonical scopes of ``tools`` (canonical tokens or Claude tool names), None when unusable."""
    from agentteams.capability_map import CANONICAL_TOOL_SCOPES, claude_allowed_tools_to_canonical

    if not isinstance(tools, (list, tuple)) or not all(isinstance(t, str) for t in tools):
        return None
    canonical = {t for t in tools if t in CANONICAL_TOOL_SCOPES}
    canonical |= set(claude_allowed_tools_to_canonical([t for t in tools if t not in CANONICAL_TOOL_SCOPES]))
    return frozenset(canonical)


def wasm_eligible(agent_meta: Mapping[str, object]) -> bool:
    """Return whether an agent is offered ``run_wasm`` (and narrowed by P13) while WASM is on (R9).

    True iff the agent's tools include the execute class (canonical ``execute``, Claude ``Bash``), whatever else it
    holds (operator decision D12: the earlier ``edit`` requirement is dropped). Defined by tools, not by a slug list,
    so a new template is covered without a code change. C-3: for a shell holder, moving to compute-only ``run_wasm``
    (P13 removes native exec) is a *narrowing* of its declared grant, not a widening, so read-only agents that hold
    ``execute`` (``navigator``) are eligible too; the agents that need native exec keep it only through
    ``native_exec_keep`` (default :data:`NATIVE_EXEC_KEEP_DEFAULT`, each a gated ``native-exec-keep`` effect) or the
    fixed :data:`NATIVE_EXEC_EXEMPT`. Pure; no I/O.

    Args:
        agent_meta: ``{"slug": str, "tools": list[str]}`` with canonical scopes (``capability_map.
            CANONICAL_TOOL_SCOPES``); Claude tool names are mapped to canonical ones.

    Returns:
        Whether the agent is eligible. Unknown or missing ``tools`` -> False (fail toward not offering a tool: for
        an agent without a shell, offering ``run_wasm`` would be a grant).

    Raises:
        Nothing.
    """
    if not isinstance(agent_meta, Mapping):
        return False
    canonical = _canonical_tools(agent_meta.get("tools"))
    return canonical is not None and "execute" in canonical


# --- composition notices (D6) -------------------------------------------------------------------------------------

def composition_advisories(state: Any, framework_id: str, *, platform: str | None = None,
                           mechanism: bool | None = None) -> list[dict[str, str]]:
    """Return the D6 composition advisories for a WASM-on state (never an enforcement claim; P13 owns those).

    Args:
        state: The effective :class:`~agentteams.sandbox_state.SandboxState`.
        framework_id: The target framework.
        platform: ``sys.platform`` override (tests).
        mechanism: The live OS-mechanism probe result (``host_features.os_sandbox_mechanism_available``); probed
            when None and WASM is on.

    Returns:
        ``wasm-without-os-sandbox`` when the OS sandbox is off; ``wasm-only-host`` when this host has no OS sandbox
        for the framework (native Windows, ``is_sandbox_capable`` False, or the mechanism probe False; D6: WASM
        runs alone). Empty when WASM is off.

    Raises:
        Nothing.
    """
    if not getattr(state, "wasm", False):
        return []
    from agentteams import host_features

    out: list[dict[str, str]] = []
    if not state.os:
        out.append({"code": "wasm-without-os-sandbox", "message": (
            f"WASM confinement is on but the OS sandbox is off ({state.os_reason}): the host process (the vendor CLI "
            "or the agentteams runtime) is not OS-confined, and the control plane is not session-write-denied. WASM "
            "limits guest code only.")})
    plat = sys.platform if platform is None else platform
    # Native Windows has no OS sandbox under any framework (D6), even where agentteams still emits a Claude block.
    capable = not plat.startswith("win") and host_features.is_sandbox_capable(framework_id, plat)
    if capable and mechanism is None and platform is None:
        mechanism = host_features.os_sandbox_mechanism_available()
    if not capable or mechanism is False:
        out.append({"code": "wasm-only-host", "message": "No OS sandbox on this host; WASM runs alone (D6)."})
    return out


__all__ = [
    "NATIVE_EXEC_DEFAULT",
    "MIB",
    "NATIVE_EXEC_EXEMPT",
    "NATIVE_EXEC_KEEP_DEFAULT",
    "NATIVE_EXEC_VALUES",
    "ON_OFF_VALUES",
    "WASM_FRAMEWORK_KEY",
    "WASM_SANDBOX_CLI_KEY",
    "WASM_SANDBOX_VALUES",
    "WASM_SUBSTRATE_CLI_KEY",
    "WASM_SUBSTRATE_KEPT_KEY",
    "AgentWasmGrant",
    "WasmConfig",
    "check_inside_project",
    "composition_advisories",
    "config_from_record",
    "default_keep",
    "default_wasm_config",
    "limits_of",
    "manifest_wasm_config",
    "normalize_wasm_sandbox",
    "substrate_switch",
    "validate_on_off",
    "validate_read_path",
    "validate_wasm_sandbox_cli",
    "validate_wasm_substrate_cli",
    "wasm_eligible",
    "wasm_state_fields",
    "wasm_switch",
    "wasm_switch_set",
]
