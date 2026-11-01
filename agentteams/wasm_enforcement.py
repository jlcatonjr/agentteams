"""wasm_enforcement.py — per-provider "use run_wasm, not native exec" policy and its honest verdict (WASM plan P13, D2).

When WASM is on, every agent P01 calls :func:`~agentteams.wasm_config.wasm_eligible` (it holds an execute-class
tool) is subject to the brief's ``wasm_sandbox.native_exec`` policy:

* ``replace`` (default): remove the canonical ``execute`` token where this framework actually gives the agent
  ``run_wasm`` (P11 ``wasm_wiring``: ``wired-per-agent`` / ``wired-shared``); where it does not, the agent **keeps**
  native exec (never stripped to nothing) and is listed as a *fallback* in the notice and the record.
* ``deny``: remove ``execute`` wherever the framework has a tool channel, with or without ``run_wasm``.
* ``keep``: no tool change (instructions only).

Never narrowed: the fixed :data:`~agentteams.wasm_config.NATIVE_EXEC_EXEMPT` roles — the orchestrator, recognised by
its *template* (``orchestrator.template.md``), not by a slug a brief could reuse, and the team builder (its file never
reaches the agent branch of the render pipeline) — and every ``native_exec_keep`` entry (D12 default ``navigator``,
``git-operations``; each a gated ``native-exec-keep`` effect, :func:`gate_keep_set`).

The narrowing is applied once, to the canonical file (:func:`apply_canonical`, called by ``cli.render_pipeline``
right after ``write_policy.apply``), so every adapter sees the same list. Because P11 decides ``run_wasm`` from the
rendered tools, the adapters ask :func:`wiring_tools` for the agent's pre-narrowing tools (an in-process record that
JSON input cannot forge).

:func:`enforcement_report` is the verdict, recorded as ``manifest["wasm_enforcement"][<framework>]``
(``team-manifest.schema.json``) and printed as ``[WASM-ENFORCEMENT]`` lines (:func:`notices`). A sub-agent cell keeps
``enforced`` / ``conditional`` only when the OS sandbox is on **and** the METAPLAN R1 control-plane rules are merged
(Claude: ``_sandbox_drift.verify_control_plane_merged`` and ``wasm_mcp.control_plane_advisory``; Goose:
``control_plane_advisory``; Codex: the confined launcher protects and self-probes them at each launch). Otherwise the
cell is ``advisory`` with the reason. The top-level session is always ``advisory``. Integrity-pinned: it removes a
tool from agents (C-3 narrowing) and is the one place enforcement is claimed.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: The manifest key P13 owns (``team-manifest.schema.json`` ``wasm_enforcement``).
KEY = "wasm_enforcement"
#: Transient manifest key: this render's per-agent action record (a :class:`_Actions`, never JSON-loadable).
ACTIONS_KEY = "_wasm_exec_actions"
#: Verdict terms, strongest first.
TERMS: tuple[str, ...] = ("enforced", "conditional", "declared", "advisory")
#: Per-agent actions recorded during the render.
NARROW, KEEP, FALLBACK, EXEMPT = "narrow", "keep", "fallback", "exempt"
#: Frameworks with a per-agent tool channel the canonical narrowing reaches (agents-md has none).
_TOOL_CHANNEL = frozenset({"claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli"})
#: Wiring statuses under which an eligible agent receives ``run_wasm`` (P11).
_WIRED = frozenset({"wired-per-agent", "wired-shared"})
_ORCHESTRATOR_TEMPLATE = "orchestrator.template.md"
_FRONT_RE = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*(?:\n|\Z)", re.DOTALL)
_FLOW_TOOLS_RE = re.compile(r"^tools:[ \t]*\[([^\]\n]*)\][ \t]*$", re.MULTILINE)


class _Actions(dict):
    """This render's ``slug -> action`` record. A private type, so a manifest loaded from JSON can never carry one."""


# --- policy ----------------------------------------------------------------------------------------------------------

def _wasm_on(manifest: Mapping[str, Any]) -> bool:
    from agentteams.wasm_config import wasm_switch

    return wasm_switch(manifest)[0]


def _template_of(manifest: Mapping[str, Any], slug: str) -> str | None:
    """The template path the render uses for ``<slug>.agent.md``, or None."""
    for spec in manifest.get("output_files") or []:
        if isinstance(spec, Mapping) and Path(str(spec.get("path", ""))).name == f"{slug}.agent.md":
            tpl = spec.get("template") or spec.get("fallback_template")
            return str(tpl) if tpl else None
    return None


def role_exempt(manifest: Mapping[str, Any], slug: str) -> bool:
    """Return whether ``slug`` is one of the fixed exempt roles (orchestrator, team builder), keyed on the role.

    The orchestrator is the agent rendered from ``orchestrator.template.md`` under the slug ``orchestrator``; a
    different agent named ``orchestrator`` (another template) is not exempt. A team builder never reaches the agent
    branch of the render pipeline; a slug starting ``team-builder`` with a builder template is exempt here too.

    Args:
        manifest: The team manifest (``output_files``).
        slug: The agent slug.

    Returns:
        True for exactly the two fixed roles.

    Raises:
        Nothing.
    """
    from agentteams.wasm_config import NATIVE_EXEC_EXEMPT

    tpl = _template_of(manifest, slug)
    if tpl is None:
        return False
    name = Path(tpl).name
    if slug == NATIVE_EXEC_EXEMPT[0]:
        return name == _ORCHESTRATOR_TEMPLATE
    return slug.startswith(NATIVE_EXEC_EXEMPT[1]) and name.startswith(NATIVE_EXEC_EXEMPT[1])


def exec_policy(manifest: Mapping[str, Any], slug: str) -> str:
    """Return the configured native-exec policy for one agent: ``replace``, ``deny`` or ``keep``.

    ``keep`` when WASM is off, for the exempt roles, for ``native_exec_keep`` entries and under ``native_exec:
    keep``. This is the policy, before the per-framework ``replace`` -> keep fallback (:func:`agent_action`).

    Args:
        manifest: The team manifest.
        slug: The agent slug.

    Returns:
        The policy.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record or ``--wasm-sandbox`` value.
    """
    if not _wasm_on(manifest) or role_exempt(manifest, slug):
        return "keep"
    from agentteams.wasm_config import manifest_wasm_config

    cfg = manifest_wasm_config(manifest)
    if slug in cfg.native_exec_keep:
        return "keep"
    return cfg.native_exec


def run_wasm_available(manifest: dict[str, Any], framework: str) -> bool:
    """Return whether an eligible agent of ``framework`` is given ``run_wasm`` (P11 wiring status).

    Args:
        manifest: The team manifest (mutated by ``_wasm_wiring.wiring_row`` on first use).
        framework: The framework id.

    Returns:
        True for ``wired-per-agent`` / ``wired-shared``.

    Raises:
        ValueError: As ``_wasm_wiring.wiring_row``.
    """
    from agentteams.frameworks._wasm_wiring import wiring_row

    row = wiring_row(manifest, framework)
    return row is not None and row.get("status") in _WIRED


def agent_action(manifest: dict[str, Any], slug: str, framework: str, tools: Iterable[str]) -> str | None:
    """Return what happens to one agent's native exec on ``framework``, or None when it is not eligible.

    Args:
        manifest: The team manifest.
        slug: The agent slug.
        framework: The framework id.
        tools: The agent's canonical tools before narrowing.

    Returns:
        ``narrow`` (execute removed), ``keep`` (policy keep / native_exec_keep), ``exempt`` (fixed role),
        ``fallback`` (``replace`` with no ``run_wasm`` here: native exec kept, listed) or None (not eligible, or
        WASM off).

    Raises:
        ValueError: As :func:`exec_policy`.
    """
    from agentteams.wasm_config import wasm_eligible

    if not _wasm_on(manifest) or not wasm_eligible({"slug": slug, "tools": list(tools)}):
        return None
    if role_exempt(manifest, slug):
        return EXEMPT
    policy = exec_policy(manifest, slug)
    if policy == "keep" or framework not in _TOOL_CHANNEL:
        return KEEP if policy == "keep" else FALLBACK
    if policy == "deny" or run_wasm_available(manifest, framework):
        return NARROW
    return FALLBACK


# --- render-time application -----------------------------------------------------------------------------------------

def begin(manifest: dict[str, Any]) -> None:
    """Reset this render's action record (called once at the start of every pipeline render).

    Args:
        manifest: The team manifest (mutated: the transient :data:`ACTIONS_KEY`).

    Raises:
        Nothing.
    """
    manifest.pop(ACTIONS_KEY, None)


def _canonical_tools(content: str) -> tuple[re.Match[str] | None, list[str] | None]:
    """The front matter's one-line ``tools: [...]`` match and its tokens (None when absent)."""
    fm = _FRONT_RE.match(content)
    if fm is None:
        return None, None
    m = _FLOW_TOOLS_RE.search(fm.group(1))
    if m is None:
        if re.search(r"^tools\s*:", fm.group(1), re.MULTILINE):
            raise ValueError("agent file's `tools:` is not a one-line flow list; cannot apply the WASM native-exec "
                             "policy safely (fail closed)")
        return None, None
    tokens = [t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()]
    return m, tokens


def apply_canonical(content: str, slug: str, manifest: dict[str, Any], framework: str) -> str:
    """Apply the native-exec policy to one rendered canonical agent file (identity while WASM is off).

    Removes only the ``execute`` token; ``retrieval`` (the scoped research CLI on Claude) is kept (Rule-12
    carve-out 2). Records the agent's action for :func:`wiring_tools` and the report.

    Args:
        content: The canonical agent file (after ``write_policy.apply``).
        slug: The agent slug.
        manifest: The team manifest (mutated: :data:`ACTIONS_KEY`).
        framework: The framework being rendered.

    Returns:
        The content, narrowed when the action is ``narrow``.

    Raises:
        ValueError: A ``tools:`` key that is not a one-line flow list while WASM is on, or a malformed WASM record.
    """
    if not _wasm_on(manifest):
        return content
    m, tokens = _canonical_tools(content)
    if m is None or tokens is None:
        return content
    action = agent_action(manifest, slug, framework, tokens)
    if action is None:
        return content
    record = manifest.get(ACTIONS_KEY)
    if not isinstance(record, _Actions):
        record = manifest[ACTIONS_KEY] = _Actions()
    record[slug] = action
    if action != NARROW:
        return content
    kept = [t for t in tokens if t.lower() != "execute"]
    fm = _FRONT_RE.match(content)
    assert fm is not None  # _canonical_tools matched it
    start = fm.start(1)
    line = "tools: [" + ", ".join(f"'{t}'" for t in kept) + "]"
    return content[:start + m.start()] + line + content[start + m.end():]


def actions(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Return this render's ``slug -> action`` record (empty when none, or when the key is not pipeline-made).

    Args:
        manifest: The team manifest.

    Returns:
        A copy of the record.

    Raises:
        Nothing.
    """
    record = manifest.get(ACTIONS_KEY)
    return dict(record) if isinstance(record, _Actions) else {}


def wiring_tools(manifest: Mapping[str, Any], slug: str, tools: Iterable[str]) -> list[str]:
    """Return the tools P11's eligibility check should see: ``execute`` restored for an agent narrowed this render.

    The narrowing replaces native exec *by* ``run_wasm``, so the agent stays eligible for its copy. Only the
    in-process :class:`_Actions` record counts; a manifest key loaded from JSON is ignored (cannot widen).

    Args:
        manifest: The team manifest.
        slug: The agent slug.
        tools: The tools as rendered (canonical or Claude names).

    Returns:
        The tools, plus ``execute`` when this render narrowed the agent.

    Raises:
        Nothing.
    """
    out = list(tools)
    if actions(manifest).get(slug) == NARROW and "execute" not in out:
        out.append("execute")
    return out


# --- governance input -------------------------------------------------------------------------------------------------

def _template_tools(manifest: Mapping[str, Any], slug: str) -> list[str] | None:
    """The canonical tools the template for ``slug`` declares (None: unknown)."""
    from agentteams.cli.render_pipeline import TEMPLATES_DIR

    tpl = _template_of(manifest, slug)
    if tpl is None:
        return None
    root = Path(TEMPLATES_DIR).resolve()
    path = (root / tpl).resolve()
    if not path.is_relative_to(root):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        _m, tokens = _canonical_tools(text)
    except ValueError:
        return None
    return tokens


def planned_actions(manifest: dict[str, Any], framework: str) -> dict[str, str]:
    """Return the actions a render of ``framework`` would record, from the template tools (``--check-wiring``).

    Under ``write_policy: orchestrator-only`` only the orchestrator keeps ``execute``, as at render.

    Args:
        manifest: The team manifest (``output_files``, ``agent_slug_list``).
        framework: The framework id.

    Returns:
        ``slug -> action`` for the eligible agents whose template tools are readable.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record.
    """
    from agentteams import write_policy

    out: dict[str, str] = {}
    for slug in manifest.get("agent_slug_list") or []:
        if not isinstance(slug, str):
            continue
        if write_policy.enabled(manifest) and slug not in write_policy.ORCHESTRATOR_SLUGS:
            continue
        tools = _template_tools(manifest, slug)
        action = agent_action(manifest, slug, framework, tools) if tools is not None else None
        if action is not None:
            out[slug] = action
    return out


def gate_keep_set(manifest: dict[str, Any]) -> frozenset[str]:
    """Return the agents whose kept native exec is a ``native-exec-keep`` gate effect (P15).

    ``native_exec_keep`` entries always; under ``native_exec: keep`` also every other non-exempt agent that may be
    eligible (an agent whose template tools cannot be read counts: over-inclusion only asks for more signatures).
    Under ``write_policy: orchestrator-only`` nothing but the orchestrator keeps ``execute``, so only the explicit
    entries count. The ``replace`` fallback (no ``run_wasm`` on the host) is not an operator relaxation and is not
    included.

    Args:
        manifest: The team manifest.

    Returns:
        The slugs (empty while WASM is off).

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record.
    """
    if not _wasm_on(manifest):
        return frozenset()
    from agentteams import write_policy
    from agentteams.wasm_config import manifest_wasm_config, wasm_eligible

    cfg = manifest_wasm_config(manifest)
    keep = set(cfg.native_exec_keep)
    if cfg.native_exec == "keep" and not write_policy.enabled(manifest):
        for slug in manifest.get("agent_slug_list") or []:
            if not isinstance(slug, str) or role_exempt(manifest, slug):
                continue
            tools = _template_tools(manifest, slug)
            if tools is None or wasm_eligible({"slug": slug, "tools": tools}):
                keep.add(slug)
    return frozenset(keep)


def codex_gate_keep(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Return the agent names the Codex WASM-mode gate lets keep the shell: exempt roles plus ``native_exec_keep``.

    Args:
        manifest: The team manifest.

    Returns:
        Sorted slugs present in the team.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record.
    """
    from agentteams.wasm_config import manifest_wasm_config

    slugs = [s for s in manifest.get("agent_slug_list") or [] if isinstance(s, str)]
    keep = {s for s in slugs if role_exempt(manifest, s)} | set(manifest_wasm_config(manifest).native_exec_keep)
    return tuple(sorted(keep))


# --- verdict ---------------------------------------------------------------------------------------------------------

#: framework -> (base term for narrowed sub-agents, mechanism, evidence).
_MECHANISM: Mapping[str, tuple[str, str, str]] = {
    "claude": ("enforced", "Bash removed from subagent tools (Claude Code enforces subagent tools); run_wasm wired "
               "per subagent", "in-repo"),
    "goose": ("enforced", "developer shell absent from the recipe's available_tools (enforced at dispatch on a direct "
              "`goose run --recipe`); run_wasm wired per recipe", "spike"),
    "codex": ("declared", "execute removed from the role declaration; Codex ignores role tool limits", "in-repo"),
    "grok": ("declared", "Bash absent from tools: (deny by absence; no live negative probe yet)", "unverified"),
    "copilot-vscode": ("enforced", "execute removed from agent tools (Copilot tools classification)", "in-repo"),
    "copilot-cli": ("enforced", "execute removed from agent tools (Copilot tools classification)", "in-repo"),
    "agents-md": ("advisory", "AGENTS.md has no tool channel; instructions only", "in-repo"),
}


def _project_root(manifest: Mapping[str, Any], fallback: Path | None) -> Path | None:
    from agentteams.frameworks._write_roots import project_root_of

    root = project_root_of(dict(manifest))
    return Path(root) if root is not None else fallback


def _control_plane(manifest: dict[str, Any], framework: str, root: Path | None) -> tuple[bool, str]:
    """``(merged, reason)`` for the R1 precondition on ``framework`` (never raises: unreadable is not merged)."""
    from agentteams.frameworks._control_plane_protect import control_plane_for

    if control_plane_for(manifest) is None:
        return False, "control-plane rules not emitted"
    if framework == "codex":
        from agentteams.frameworks._codex_wasm_gate import codex_wasm_gate_emitted

        if codex_wasm_gate_emitted(manifest):
            return True, ("protected by .codex/confined-run.example.sh (--protect + self-probe) at each launch; "
                          "not verifiable at render")
        return False, "no confined launcher with the role gate for this team"
    if root is None:
        return False, "control-plane rules not merged: the project root is unknown"
    from agentteams import wasm_mcp

    try:
        advisory = wasm_mcp.control_plane_advisory(root, framework, manifest)
        missing: list[str] = []
        if framework == "claude":
            from agentteams.frameworks._control_plane_protect import wasm_protected_config_paths
            from agentteams.frameworks._sandbox_drift import verify_control_plane_merged
            from agentteams.frameworks._wasm_wiring import launching_config_paths

            extra = tuple(dict.fromkeys([*wasm_protected_config_paths(manifest), *launching_config_paths(root)]))
            merged, missing = verify_control_plane_merged(root, extra)
            if not merged and not missing:
                missing = ["unverified"]
    except (OSError, ValueError) as exc:
        return False, f"control-plane rules not merged: check failed ({type(exc).__name__})"
    if missing:
        more = f" (+{len(missing) - 3} more; see --check-wiring)" if len(missing) > 3 else ""
        return False, "control-plane rules not merged: " + ", ".join(missing[:3]) + more
    if advisory:
        return False, f"control-plane rules not merged: {advisory}"
    return True, ""


def enforcement_report(manifest: dict[str, Any], framework: str, *, project_root: Path | None = None,
                       acts: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Return the ``wasm_enforcement`` row for ``framework``, or None while WASM is off.

    Args:
        manifest: The team manifest (this render's action record; ``wasm_wiring``).
        framework: The framework id.
        project_root: Where the team was emitted (used when the manifest carries no CLI-set root).
        acts: The per-agent actions; default this render's record (:func:`actions`); ``--check-wiring`` passes
            :func:`planned_actions`.

    Returns:
        ``{subagents, top_level, delegates?, mechanism, evidence, reason, os_sandbox, control_plane_merged, exempt,
        kept_native, fallback, changed_agents}``.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record.
    """
    if not _wasm_on(manifest):
        return None
    from agentteams.sandbox_state import os_switch_state

    acts = actions(manifest) if acts is None else dict(acts)
    changed = sorted(s for s, a in acts.items() if a == NARROW)
    kept = sorted(s for s, a in acts.items() if a == KEEP)
    fallback = sorted(s for s, a in acts.items() if a == FALLBACK)
    exempt = sorted({s for s, a in acts.items() if a == EXEMPT} | {
        s for s in manifest.get("agent_slug_list") or [] if isinstance(s, str) and role_exempt(manifest, s)})
    os_on = os_switch_state(manifest)[0]
    base, mechanism, evidence = _MECHANISM.get(framework, ("advisory", f"no enforcement channel for {framework}",
                                                           "unverified"))
    if framework == "goose":
        from agentteams.frameworks.goose_tool_scoping import grant_mode

        if not grant_mode(manifest):
            base, mechanism = "advisory", ("legacy goose_tool_scoping loads the whole developer extension (no "
                                           "per-recipe allowlist); set goose_tool_scoping: grant")
    if framework == "codex":
        from agentteams.frameworks._codex_wasm_gate import codex_wasm_gate_emitted

        if codex_wasm_gate_emitted(manifest):
            base, mechanism = "conditional", ("PreToolUse role gate denies the probed shell tool (Bash) to every "
                                              "spawned agent not in the keep list, only when Codex is started by "
                                              ".codex/confined-run.example.sh; unknown tool names pass; hooks fail "
                                              "open on timeout")
        elif manifest.get("write_policy") == "orchestrator-only":
            from agentteams.frameworks._codex_role_gate_emit import codex_role_gate_enabled

            if codex_role_gate_enabled(manifest):
                base, mechanism = "conditional", ("write_policy role gate: spawned agents get only agentteams_readfs "
                                                  "(no shell), under .codex/confined-run.example.sh")
    merged, cp_reason = (False, "os sandbox off")
    if os_on:
        merged, cp_reason = _control_plane(manifest, framework, _project_root(manifest, project_root))
    reason = ""
    term = base
    if not changed and not (framework == "codex" and base == "conditional"):
        term, reason = "advisory", ("no agent's native exec removed on this host" +
                                    (f" (fallback: no run_wasm here for {', '.join(fallback)})" if fallback else ""))
    elif term in ("enforced", "conditional") and not os_on:
        term, reason = "advisory", "os sandbox off"
    elif term in ("enforced", "conditional") and not merged:
        term, reason = "advisory", cp_reason
    elif term == "declared":
        reason = "written, not enforced by the host"
    elif framework == "codex" and term == "conditional":
        reason = cp_reason
    row: dict[str, Any] = {
        "subagents": term, "top_level": "advisory", "mechanism": mechanism, "evidence": evidence, "reason": reason,
        "os_sandbox": bool(os_on), "control_plane_merged": bool(merged), "exempt": exempt, "kept_native": kept,
        "fallback": fallback, "changed_agents": changed,
    }
    if framework == "goose":
        row["delegates"] = "advisory"
    return row


def record(manifest: dict[str, Any], framework: str, *, project_root: Path | None = None) -> dict[str, Any] | None:
    """Compute and store ``manifest["wasm_enforcement"][framework]`` (nothing while WASM is off).

    Args:
        manifest: The team manifest (mutated).
        framework: The framework id.
        project_root: As :func:`enforcement_report`.

    Returns:
        The row, or None.

    Raises:
        ValueError: As :func:`enforcement_report`.
    """
    row = enforcement_report(manifest, framework, project_root=project_root)
    if row is not None:
        rec = manifest.get(KEY)
        if not isinstance(rec, dict):
            rec = manifest[KEY] = {}
        rec[framework] = row
    return row


def notices(framework: str, row: Mapping[str, Any] | None) -> list[str]:
    """Return the ``[WASM-ENFORCEMENT]`` lines for one framework row (empty for None).

    Args:
        framework: The framework id.
        row: :func:`enforcement_report`'s row.

    Returns:
        The framework line, the exemption / exception line, and an OS-off note when it applies.

    Raises:
        Nothing.
    """
    if row is None:
        return []
    term = str(row["subagents"]).upper()
    why = f" ({row['reason']})" if row.get("reason") else ""
    n = len(row["changed_agents"])
    lines = [f"[WASM-ENFORCEMENT] {framework}: subagents {term}{why}; native exec removed from {n} agent"
             f"{'' if n == 1 else 's'}; mechanism: {row['mechanism']}; top-level session ADVISORY"]
    if "delegates" in row:
        lines[0] += ("; delegates ADVISORY (a Goose delegate inherits the orchestrator's extensions, keeps native "
                     "exec and cannot add run_wasm: route WASM work to a direct `goose run --recipe`)")
    if term == "ADVISORY":
        lines[0] = lines[0].replace("subagents ADVISORY", "ADVISORY ONLY: subagents ADVISORY", 1)
    lines.append(f"[WASM-ENFORCEMENT] exempt (fixed): {', '.join(row['exempt']) or '<none>'}; operator exceptions "
                 f"(native-exec-keep): {', '.join(row['kept_native']) or '<none>'}; fallback (no run_wasm, native "
                 f"exec kept): {', '.join(row['fallback']) or '<none>'}")
    if not row["os_sandbox"]:
        lines.append("[WASM-ENFORCEMENT] OS sandbox off: a kept native shell is unconfined, and the agent files "
                     "carrying these limits are agent-writable")
    return lines


def report(manifest: dict[str, Any], framework: str, *, project_root: Path | None = None) -> None:
    """Record the row and print its notices (stderr); also appends an ``advisories`` entry when advisory.

    Args:
        manifest: The team manifest (mutated: ``wasm_enforcement``, ``advisories``).
        framework: The framework id.
        project_root: As :func:`enforcement_report`.

    Raises:
        ValueError: As :func:`enforcement_report`.
    """
    row = record(manifest, framework, project_root=project_root)
    for line in notices(framework, row):
        print(f"  {line}", file=sys.stderr)
    if row is not None and row["subagents"] == "advisory":
        manifest.setdefault("advisories", []).append(
            {"code": "wasm-enforcement-advisory",
             "message": f"WASM native-exec policy on {framework} is advisory only ({row['reason']})"})


__all__ = [
    "ACTIONS_KEY",
    "EXEMPT",
    "FALLBACK",
    "KEEP",
    "KEY",
    "NARROW",
    "TERMS",
    "actions",
    "agent_action",
    "apply_canonical",
    "begin",
    "codex_gate_keep",
    "enforcement_report",
    "exec_policy",
    "gate_keep_set",
    "notices",
    "planned_actions",
    "record",
    "report",
    "role_exempt",
    "run_wasm_available",
    "wiring_tools",
]
