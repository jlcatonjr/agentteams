"""wasm_config.py — the brief's ``wasm_sandbox`` block, the ``--wasm-sandbox`` switch, and WASM eligibility (P01).

METAPLAN §2.1 / R9: one validated, fail-closed place to turn WASM confinement on and shape it. The brief block
``wasm_sandbox`` (``enabled``, ``languages``, ``limits``, per-agent ``agents`` grants, ``native_exec``,
``native_exec_keep``, the reserved ``model_substrate``) is normalised by :func:`normalize_wasm_sandbox` into a
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
(3) :func:`wasm_eligible` excludes agents without ``edit``, so read-only agents that hold ``execute`` (``navigator``)
are never offered ``run_wasm`` and keep native shell (METAPLAN §7 accepted limitation).

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

#: The two values of ``--wasm-sandbox``.
WASM_SANDBOX_VALUES: tuple[str, ...] = ("on", "off")

#: ``native_exec`` policies (applied per provider by P13; validated and carried here).
NATIVE_EXEC_VALUES: tuple[str, ...] = ("replace", "deny", "keep")
NATIVE_EXEC_DEFAULT = "replace"

#: Agents exempt by design from native-exec removal (P13); they may not be listed in ``native_exec_keep``.
NATIVE_EXEC_EXEMPT: tuple[str, ...] = ("orchestrator", "team-builder")

_TOP_KEYS = ("enabled", "languages", "limits", "agents", "native_exec", "native_exec_keep", "model_substrate")
_GRANT_KEYS = ("read_paths", "scratch")
_BRIEF_LIMIT_KEYS = ("fuel", "memory_mib", "timeout_s")
_MIB = 1 << 20
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
#: Credential-looking path segments (compared lowercased): never readable by a guest, whatever their directory.
_SECRET_SEGMENT_RE = re.compile(r"^(\.env.*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_.*|\.ssh|\.gnupg|\.aws|\.netrc|"
                                r"\.npmrc|\.pypirc|\.git-credentials)$")
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
        model_substrate: ``None`` | ``"on"`` | ``"off"`` (reserved; P20 owns its behaviour).
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


def default_wasm_config() -> WasmConfig:
    """Return the configuration of an absent block (WASM off, all languages, default limits, no grants).

    Returns:
        The default :class:`WasmConfig`.

    Raises:
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    return WasmConfig(enabled=False, languages=_languages(), limits=_wasm_exec().DEFAULT_LIMITS)


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
    return {"fuel": b["fuel"], "memory_mib": (-(-mem_lo // _MIB), mem_hi // _MIB), "timeout_s": b["timeout_s"]}


def _limits_of(raw: object, path: str) -> Limits:
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
                         max_memory_bytes=raw["memory_mib"] * _MIB if "memory_mib" in raw else d.max_memory_bytes,
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
    secret = next((s for s in rel.split("/") if _SECRET_SEGMENT_RE.match(s.lower())), None)
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
        return ()
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
            ``native_exec_keep`` entries must be among them.
        project_root: The project root for the read-path symlink check, or None to skip it (the effective state
            repeats it with the CLI's real root).

    Returns:
        The :class:`WasmConfig`.

    Raises:
        ValueError: Any malformed, unknown or out-of-bounds value, naming its brief key path.
        ImportError: :mod:`agentteams.wasm_exec` is not importable (fail closed).
    """
    if raw is None:
        return default_wasm_config()
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
        limits=_limits_of(raw.get("limits"), "wasm_sandbox.limits"),
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
        if type(mem) is not int or mem % _MIB:
            raise ValueError("manifest wasm_sandbox.limits.max_memory_bytes must be a whole number of MiB")
        raw["limits"] = {"fuel": limits["fuel"], "memory_mib": mem // _MIB, "timeout_s": limits["timeout_s"]}
    elif limits is not None:
        raise ValueError("manifest wasm_sandbox.limits must be {fuel, max_memory_bytes, timeout_s}")
    cfg = normalize_wasm_sandbox(raw, agent_slugs=agent_slugs)
    if cfg.to_record() != {**cfg.to_record(), **rec}:
        raise ValueError("manifest wasm_sandbox is not in normalised form")
    return cfg


# --- the effective state (WASM half of sandbox_state.effective_sandbox_state) -------------------------------------

def validate_wasm_sandbox_cli(value: object) -> str | None:
    """Return the validated ``--wasm-sandbox`` value.

    Args:
        value: The argv value (or the transient manifest key).

    Returns:
        ``None`` when absent, else ``"on"`` or ``"off"``.

    Raises:
        ValueError: Any other value.
    """
    if value is None:
        return None
    if isinstance(value, str) and value in WASM_SANDBOX_VALUES:
        return value
    raise ValueError(f"--wasm-sandbox must be \"on\" or \"off\" (got {value!r})")


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
        return default_wasm_config()
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
        ``(True|False, "cli" | "brief" | "default-off")``.

    Raises:
        ValueError: A bad ``--wasm-sandbox`` value or a malformed ``wasm_sandbox`` record.
    """
    cli = validate_wasm_sandbox_cli(manifest.get(WASM_SANDBOX_CLI_KEY))
    if cli is not None:
        return cli == "on", "cli"
    if manifest.get("wasm_sandbox") is None:
        return False, "default-off"
    return manifest_wasm_config(manifest).enabled, "brief"


def wasm_switch_set(manifest: Mapping[str, Any]) -> bool:
    """Return whether the brief block or ``--wasm-sandbox`` is present (the gate then engages).

    Args:
        manifest: The team manifest.

    Returns:
        True when either is present.
    """
    return manifest.get("wasm_sandbox") is not None or manifest.get(WASM_SANDBOX_CLI_KEY) is not None


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
            closed), or a read path that resolves through a symlink under the real root.
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
        for p in entry.read_paths:
            check_inside_project(p, root, f"wasm_sandbox.agents.{slug}.read_paths")
        grants[slug] = grant_from_tokens(slug, tokens, cfg.limits, project_root=root)
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

    True iff the agent's tools include the execute class (canonical ``execute``, Claude ``Bash``) and it is not a
    read-only auditor. A read-only auditor is defined by tools, not by a slug list: no ``edit`` scope. So a new auditor
    template is covered without a code change; read-only agents that hold ``execute`` (``navigator``) are not
    eligible and keep native shell (METAPLAN §7 accepted limitation). Pure; no I/O.

    Args:
        agent_meta: ``{"slug": str, "tools": list[str]}`` with canonical scopes (``capability_map.
            CANONICAL_TOOL_SCOPES``); Claude tool names are mapped to canonical ones.

    Returns:
        Whether the agent is eligible. Unknown or missing ``tools`` -> False (fail toward not offering a tool: C-3,
        offering ``run_wasm`` is a grant).

    Raises:
        Nothing.
    """
    if not isinstance(agent_meta, Mapping):
        return False
    canonical = _canonical_tools(agent_meta.get("tools"))
    return canonical is not None and "execute" in canonical and "edit" in canonical


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
    "NATIVE_EXEC_EXEMPT",
    "NATIVE_EXEC_VALUES",
    "WASM_SANDBOX_CLI_KEY",
    "WASM_SANDBOX_VALUES",
    "AgentWasmGrant",
    "WasmConfig",
    "check_inside_project",
    "composition_advisories",
    "config_from_record",
    "default_wasm_config",
    "manifest_wasm_config",
    "normalize_wasm_sandbox",
    "validate_read_path",
    "validate_wasm_sandbox_cli",
    "wasm_eligible",
    "wasm_state_fields",
    "wasm_switch",
    "wasm_switch_set",
]
