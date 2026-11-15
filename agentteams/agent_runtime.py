"""agent_runtime.py — the model and reasoning effort each generated agent runs with, from the brief (agentteams #508).

The brief's optional ``agent_runtime`` block sets four values: the orchestrator's model and effort, and every other
agent's (the "subagents") model and effort. Per-agent overrides go under ``agents.<slug>``::

    "agent_runtime": {
      "orchestrator": {"effort": "medium", "model": {"claude": "claude-fable-5-2"}},
      "subagents":    {"effort": "medium", "model": {"claude": "opus", "copilot": "Claude Opus 4.8 (copilot)"}},
      "agents":       {"security": {"effort": "high"}}
    }

* **Effort** is named once on a neutral scale, :data:`NEUTRAL_EFFORTS` (``low``…``max``). Each level maps to the
  provider's level of the same name: "medium" is each provider's own medium, not the same amount of reasoning
  everywhere. ``effort`` may also be an object ``{"default": "medium", "<provider>": "<that provider's level>"}``,
  so a level only one provider offers (Codex's ``ultra``) stays reachable. A level a provider cannot take is refused
  at render with :class:`AgentRuntimeError`; it is never silently changed. The default is ``medium`` for both roles.
* **Model** is an object keyed by provider (a bare string means Claude). Each provider has its own grammar
  (:data:`_MODEL_GRAMMAR`). The brief never names a provider, base URL or profile: endpoint routing stays in session
  configuration, so a brief cannot send a provider's traffic elsewhere. With no model set, the adapters keep their
  1.2 defaults.
* **The orchestrator** is ``write_policy.ORCHESTRATOR_SLUGS``.

Precedence on a fresh render: ``agents.<slug>`` → the role → the default. On ``--update`` the front-matter merge keeps
an existing hand value unless the brief sets that role or slug explicitly (:func:`explicit_effort`), and reports every
change. ``model`` stays a capability key there (report-only; operator ruling 2026-11-15).

Where a provider has no per-agent surface for a value, :func:`report_lines` says so: nothing is dropped silently at
render. Two things a render cannot see are named in :data:`RUNTIME_CAVEATS`.

Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The neutral effort scale, lowest first.
NEUTRAL_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
#: The default effort for both roles (operator request, agentteams #508).
DEFAULT_EFFORT = "medium"
#: The roles the brief sets.
ROLES: tuple[str, ...] = ("orchestrator", "subagents")

#: Each framework's per-agent effort levels (verified 2026-11-15; ``references`` in the #508 plan). ``None``: the
#: framework has no per-agent effort setting. An empty tuple: any non-empty level is passed through (the provider's
#: levels depend on the model).
PROVIDER_EFFORTS: dict[str, tuple[str, ...] | None] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),       # front matter `effort:`
    "copilot-vscode": (),                                       # `reasoning-effort:` (model-dependent)
    "copilot-cli": None,                                        # no per-agent key; the --effort flag only
    "codex": None,                                              # report-only until a live rehearsal (ruling Q3)
    "goose": None,                                              # no effort setting
    "grok": None,                                               # report-only: keys not in xAI's docs (ruling Q4)
    "agents-md": None,
    "wasm": None,
}
#: Levels Claude Code's ``effortLevel`` setting takes (no ``max``; Claude Code model-config docs, 2026-11-15).
CLAUDE_SESSION_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh")
#: Levels a framework can be given in a per-provider effort object even though it writes none today (Codex's own
#: scale), so a brief written for a later release still validates.
_KNOWN_PROVIDER_LEVELS: dict[str, tuple[str, ...]] = {
    "codex": ("low", "medium", "high", "xhigh", "max", "ultra"),
}

#: Model keys the brief may use, and the frameworks each one covers.
MODEL_PROVIDERS: dict[str, tuple[str, ...]] = {
    "claude": ("claude",),
    "copilot": ("copilot-vscode", "copilot-cli"),
    "copilot-vscode": ("copilot-vscode",),
    "copilot-cli": ("copilot-cli",),
    "codex": ("codex",),
    "goose": ("goose",),
    "grok": ("grok",),
}
_MODEL_GRAMMAR: dict[str, re.Pattern[str]] = {
    # Claude: an alias or model id, optionally `[1m]` (the same rule as _claude_model.is_claude_model_value).
    "claude": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[1m\])?$"),
    # Copilot: a picker display name such as `Claude Opus 4.8 (copilot)`: printable, no quotes, brackets or commas.
    "copilot": re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,99}$"),
    # Codex / Goose / Grok: one token. No `/` (a provider path) and no whitespace.
    "token": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"),
}
#: Frameworks that write a configured model per agent today. Others report it.
MODEL_WRITTEN: frozenset[str] = frozenset({"claude", "copilot-vscode", "copilot-cli"})

#: Governance agents whose effort a per-agent override should not quietly lower (the #508 plan, A7).
GOVERNANCE_SLUGS: frozenset[str] = frozenset({"security", "adversarial", "conflict-auditor", "code-hygiene"})

#: What a render cannot see, stated by ``doctor``.
RUNTIME_CAVEATS: tuple[str, ...] = (
    "Claude Code lowers a level the model does not support to the highest one it does (for example xhigh runs as "
    "high on Opus 4.6); a render cannot see which model a session picks.",
    "CLAUDE_CODE_EFFORT_LEVEL in the caller's environment outranks every file agentteams writes (confine-run.sh "
    "drops it unless --env-allow names it).",
)


class AgentRuntimeError(ValueError):
    """The brief's ``agent_runtime`` block is malformed, or names a level or model a provider cannot take."""


@dataclass(frozen=True)
class RoleSetting:
    """One role's (or one agent's) settings: an effort per framework and a model per framework.

    Attributes:
        effort: Framework id (or ``"default"``) to effort level.
        model: Framework id to model value.
        effort_explicit: True when the brief set the effort (not the default).
    """

    effort: dict[str, str] = field(default_factory=dict)
    model: dict[str, str] = field(default_factory=dict)
    effort_explicit: bool = False


@dataclass(frozen=True)
class AgentRuntime:
    """The normalised ``agent_runtime`` block.

    Attributes:
        roles: ``"orchestrator"`` and ``"subagents"`` to their :class:`RoleSetting`.
        agents: Agent slug to its override.
    """

    roles: dict[str, RoleSetting]
    agents: dict[str, RoleSetting]


def _effort_map(value: object, where: str) -> tuple[dict[str, str], bool]:
    """Return ``{"default"|framework: level}`` from a string or object effort value, and whether it was set."""
    if value is None:
        return {"default": DEFAULT_EFFORT}, False
    if isinstance(value, str):
        if value not in NEUTRAL_EFFORTS:
            raise AgentRuntimeError(f"{where}.effort {value!r} is not one of {', '.join(NEUTRAL_EFFORTS)} "
                                    "(use an object to name a level only one provider offers)")
        return {"default": value}, True
    if not isinstance(value, dict) or not value:
        raise AgentRuntimeError(f"{where}.effort must be a level or a non-empty object of provider levels")
    out: dict[str, str] = {}
    for key, level in value.items():
        if key != "default" and key not in PROVIDER_EFFORTS:
            raise AgentRuntimeError(f"{where}.effort names unknown provider {key!r}")
        if not isinstance(level, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", level):
            raise AgentRuntimeError(f"{where}.effort.{key} must be a lowercase level name")
        if key == "default" and level not in NEUTRAL_EFFORTS:
            raise AgentRuntimeError(f"{where}.effort.default {level!r} is not one of {', '.join(NEUTRAL_EFFORTS)}")
        out[key] = level
    out.setdefault("default", DEFAULT_EFFORT)
    for key, level in out.items():
        if key != "default":
            check_effort(key, level, where=f"{where}.effort.{key}")
    return out, True


def check_effort(framework: str, level: str, *, where: str = "effort") -> None:
    """Refuse a level the framework cannot take.

    Args:
        framework: A framework id.
        level: The level.
        where: Where the value came from, for the message.

    Returns:
        None.

    Raises:
        AgentRuntimeError: The framework lists its levels and this is not one of them.
    """
    levels = PROVIDER_EFFORTS.get(framework)
    if levels is None:
        levels = _KNOWN_PROVIDER_LEVELS.get(framework)
        if levels is None:
            return
    if levels and level not in levels:
        raise AgentRuntimeError(f"{where}: {framework} takes {', '.join(levels)}, not {level!r}")


def _model_map(value: object, where: str) -> dict[str, str]:
    """Return ``{framework: model}`` from a string (Claude) or a provider-keyed object."""
    if value is None:
        return {}
    if isinstance(value, str):
        value = {"claude": value}
    if not isinstance(value, dict):
        raise AgentRuntimeError(f"{where}.model must be a Claude model string or an object keyed by provider")
    out: dict[str, str] = {}
    for key, model in value.items():
        if key not in MODEL_PROVIDERS:
            raise AgentRuntimeError(f"{where}.model names unknown provider {key!r} "
                                    f"(one of {', '.join(MODEL_PROVIDERS)})")
        grammar = _MODEL_GRAMMAR["claude" if key == "claude" else "copilot" if key.startswith("copilot") else "token"]
        if not isinstance(model, str) or not grammar.fullmatch(model):
            raise AgentRuntimeError(f"{where}.model.{key} {model!r} is not a {key} model name (a bare name: no "
                                    "provider path, URL, quotes or newline)")
        for fw in MODEL_PROVIDERS[key]:
            if key == fw or fw not in out:  # a framework-specific key beats the "copilot" family key
                out[fw] = model
    return out


def _role(value: object, where: str) -> RoleSetting:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"effort", "model"}:
        raise AgentRuntimeError(f"{where} must be an object with only 'effort' and 'model'")
    effort, explicit = _effort_map(value.get("effort"), where)
    return RoleSetting(effort=effort, model=_model_map(value.get("model"), where), effort_explicit=explicit)


def parse(description: dict[str, Any]) -> AgentRuntime:
    """Normalise the brief's ``agent_runtime`` block (absent means every default).

    Args:
        description: The project brief.

    Returns:
        The :class:`AgentRuntime`.

    Raises:
        AgentRuntimeError: The block is malformed or names a value a provider cannot take.
    """
    block = description.get("agent_runtime")
    if block is None:
        block = {}
    if not isinstance(block, dict) or set(block) - {"orchestrator", "subagents", "agents"}:
        raise AgentRuntimeError("agent_runtime must be an object with only 'orchestrator', 'subagents' and 'agents'")
    roles = {role: _role(block.get(role), f"agent_runtime.{role}") for role in ROLES}
    agents_raw = block.get("agents") or {}
    if not isinstance(agents_raw, dict):
        raise AgentRuntimeError("agent_runtime.agents must be an object keyed by agent slug")
    agents: dict[str, RoleSetting] = {}
    for slug, value in agents_raw.items():
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            raise AgentRuntimeError(f"agent_runtime.agents key {slug!r} is not an agent slug")
        where = f"agent_runtime.agents.{slug}"
        if not isinstance(value, dict) or set(value) - {"effort", "model"}:
            raise AgentRuntimeError(f"{where} must be an object with only 'effort' and 'model'")
        effort, explicit = _effort_map(value.get("effort"), where) if "effort" in value else ({}, False)
        agents[slug] = RoleSetting(effort=effort, model=_model_map(value.get("model"), where), effort_explicit=explicit)
    return AgentRuntime(roles=roles, agents=agents)


def to_manifest(runtime: AgentRuntime) -> dict[str, Any]:
    """Return the manifest record for *runtime* (plain JSON).

    Args:
        runtime: The parsed block.

    Returns:
        ``{"roles": {...}, "agents": {...}}`` with ``effort``, ``model`` and ``effort_explicit`` per entry.

    Raises:
        Nothing.
    """
    def one(s: RoleSetting) -> dict[str, Any]:
        return {"effort": dict(s.effort), "model": dict(s.model), "effort_explicit": s.effort_explicit}

    return {"roles": {k: one(v) for k, v in runtime.roles.items()},
            "agents": {k: one(v) for k, v in sorted(runtime.agents.items())}}


def from_manifest(manifest: dict[str, Any]) -> AgentRuntime:
    """Rebuild the :class:`AgentRuntime` a manifest carries (every default when it carries none).

    Args:
        manifest: The team manifest.

    Returns:
        The runtime.

    Raises:
        Nothing: a missing or malformed record falls back to the defaults (the manifest was validated upstream).
    """
    rec = manifest.get("agent_runtime")
    if not isinstance(rec, dict):
        return parse({})

    def one(d: object, default_effort: bool) -> RoleSetting:
        d = d if isinstance(d, dict) else {}
        effort = d.get("effort") if isinstance(d.get("effort"), dict) else ({"default": DEFAULT_EFFORT}
                                                                           if default_effort else {})
        model = d.get("model") if isinstance(d.get("model"), dict) else {}
        return RoleSetting(effort=dict(effort), model=dict(model), effort_explicit=bool(d.get("effort_explicit")))

    roles = rec.get("roles") if isinstance(rec.get("roles"), dict) else {}
    agents = rec.get("agents") if isinstance(rec.get("agents"), dict) else {}
    return AgentRuntime(roles={r: one(roles.get(r), True) for r in ROLES},
                        agents={str(k): one(v, False) for k, v in agents.items()})


def role_of(slug: str) -> str:
    """Return ``"orchestrator"`` for the orchestrator slugs, else ``"subagents"``.

    Args:
        slug: An agent slug.

    Returns:
        The role name.

    Raises:
        Nothing.
    """
    from agentteams.write_policy import ORCHESTRATOR_SLUGS

    return "orchestrator" if slug in ORCHESTRATOR_SLUGS else "subagents"


def _pick(effort: dict[str, str], framework: str) -> str | None:
    return effort.get(framework) or effort.get("default")


def effort_for(runtime: AgentRuntime, slug: str, framework: str) -> str | None:
    """Return the effort level to write for *slug* on *framework*, or None when the framework writes none.

    Args:
        runtime: The parsed block.
        slug: The agent slug.
        framework: The framework id.

    Returns:
        The level, or None.

    Raises:
        AgentRuntimeError: The resolved level is one the framework cannot take.
    """
    if PROVIDER_EFFORTS.get(framework) is None:
        return None
    override = runtime.agents.get(slug)
    level = (_pick(override.effort, framework) if override and override.effort else None) \
        or _pick(runtime.roles[role_of(slug)].effort, framework)
    if level is not None:
        check_effort(framework, level, where=f"effort for {slug}")
    return level


def explicit_effort(runtime: AgentRuntime, slug: str) -> bool:
    """Whether the brief set *slug*'s effort itself (its override or its role), not the default.

    Args:
        runtime: The parsed block.
        slug: The agent slug.

    Returns:
        True when an existing hand value should give way on ``--update``.

    Raises:
        Nothing.
    """
    override = runtime.agents.get(slug)
    if override is not None and override.effort_explicit:
        return True
    return runtime.roles[role_of(slug)].effort_explicit


def model_for(runtime: AgentRuntime, slug: str, framework: str) -> str | None:
    """Return the configured model for *slug* on *framework*, or None (the adapter keeps its default).

    Args:
        runtime: The parsed block.
        slug: The agent slug.
        framework: The framework id.

    Returns:
        The model value, or None.

    Raises:
        Nothing.
    """
    override = runtime.agents.get(slug)
    if override is not None and framework in override.model:
        return override.model[framework]
    return runtime.roles[role_of(slug)].model.get(framework)


def session_effort(runtime: AgentRuntime, framework: str) -> str | None:
    """Return the session-level effort to write for *framework* (Claude's ``effortLevel``), or None.

    The orchestrator role's effort is the session's. Claude's ``effortLevel`` takes no ``max``, so ``max`` gives None
    here and :func:`report_lines` says why.

    Args:
        runtime: The parsed block.
        framework: The framework id.

    Returns:
        The level, or None.

    Raises:
        Nothing.
    """
    if framework != "claude":
        return None
    level = _pick(runtime.roles["orchestrator"].effort, "claude")
    return level if level in CLAUDE_SESSION_EFFORTS else None


def report_lines(runtime: AgentRuntime, framework: str, slugs: list[str] | None = None) -> list[str]:
    """Return one line per value this framework does not write, and per governance-role lowering.

    Args:
        runtime: The parsed block.
        framework: The framework id being rendered.
        slugs: The team's agent slugs (for the governance check); None skips it.

    Returns:
        Human-readable lines (empty when every value is written).

    Raises:
        Nothing.
    """
    lines: list[str] = []
    if PROVIDER_EFFORTS.get(framework) is None:
        why = {
            "copilot-cli": "Copilot CLI agent files take no effort key; pass --effort when starting a session",
            "codex": "not written yet: Codex's model_reasoning_effort waits for a live codex-cli rehearsal "
                     "(operator ruling 2026-11-15)",
            "goose": "Goose has no effort setting",
            "grok": "not written: Grok's effort key is not in xAI's docs (operator ruling 2026-11-15)",
        }.get(framework, f"{framework} has no effort setting")
        levels = sorted({_pick(runtime.roles[r].effort, framework) or "" for r in ROLES} - {""})
        lines.append(f"agent runtime: effort ({'/'.join(levels)}) is not written for {framework}: {why}.")
    if framework == "claude" and _pick(runtime.roles["orchestrator"].effort, "claude") == "max":
        lines.append("agent runtime: orchestrator effort max is written to the orchestrator agent file only; Claude "
                     "Code's effortLevel setting takes no max, so a main session not started as that agent runs at "
                     "its own level (start it with --effort max or /effort max).")
    configured = {fw for s in [*runtime.roles.values(), *runtime.agents.values()] for fw in s.model}
    if framework in configured and framework not in MODEL_WRITTEN:
        why = {"goose": "Goose takes its model from session config (--goose-model); a recipe model would need a "
                        "provider from the brief",
               "codex": "Codex's model key waits for a live codex-cli rehearsal (operator ruling 2026-11-15)",
               "grok": "Grok's model key is not in xAI's docs (operator ruling 2026-11-15)"}.get(framework, "")
        lines.append(f"agent runtime: the configured {framework} model is not written: {why}.")
    if slugs and PROVIDER_EFFORTS.get(framework) is not None:
        for slug in sorted(GOVERNANCE_SLUGS & set(slugs)):
            override = runtime.agents.get(slug)
            if override is None:
                continue
            role_level = _pick(runtime.roles["subagents"].effort, framework)
            level = _pick(override.effort, framework) if override.effort else role_level
            if (level in NEUTRAL_EFFORTS and role_level in NEUTRAL_EFFORTS
                    and NEUTRAL_EFFORTS.index(level) < NEUTRAL_EFFORTS.index(role_level)):
                lines.append(f"agent runtime: WARNING: @{slug} (governance) is lowered to effort {level} below the "
                             f"subagent level {role_level}.")
            if framework in override.model:
                lines.append(f"agent runtime: WARNING: @{slug} (governance) has its own model "
                             f"{override.model[framework]!r}.")
    return lines


def effective_table(runtime: AgentRuntime, frameworks: list[str]) -> list[tuple[str, str, str, str]]:
    """Return ``(framework, role, model, effort)`` rows for ``doctor`` (``-`` where nothing is written).

    Args:
        runtime: The parsed block.
        frameworks: Framework ids.

    Returns:
        One row per framework and role.

    Raises:
        Nothing.
    """
    from agentteams.frameworks._claude_model import CLAUDE_AGENT_MODEL_DEFAULT, CLAUDE_ORCHESTRATOR_MODEL_DEFAULT

    rows: list[tuple[str, str, str, str]] = []
    for fw in frameworks:
        for role, probe in (("orchestrator", "orchestrator"), ("subagents", "__subagent__")):
            model = model_for(runtime, probe, fw) if fw in MODEL_WRITTEN else None
            if model is None and fw == "claude":
                model = CLAUDE_ORCHESTRATOR_MODEL_DEFAULT if role == "orchestrator" else CLAUDE_AGENT_MODEL_DEFAULT
            elif model is None and fw in MODEL_WRITTEN:
                model = "(the agent file's own)"
            try:
                effort = effort_for(runtime, probe, fw)
            except AgentRuntimeError as exc:
                effort = f"refused ({exc})"
            rows.append((fw, role, model or "-", effort or "-"))
    return rows


__all__ = [
    "AgentRuntime", "AgentRuntimeError", "CLAUDE_SESSION_EFFORTS", "DEFAULT_EFFORT", "GOVERNANCE_SLUGS",
    "MODEL_PROVIDERS", "MODEL_WRITTEN", "NEUTRAL_EFFORTS", "PROVIDER_EFFORTS", "ROLES", "RUNTIME_CAVEATS",
    "RoleSetting", "check_effort", "effective_table", "effort_for", "explicit_effort", "from_manifest",
    "model_for", "parse", "report_lines", "role_of", "session_effort", "to_manifest",
]
