"""
_wasm_team_docs.py — the ``grants.json`` document and the ``README.md`` of a ``--framework wasm`` team (P08 §4.1).

Both are pure functions of the manifest and the effective sandbox state, so a re-render is byte-identical. The
README is documentation for the operator (how to run an agent, which environment variables hold provider keys,
and what is and is not enforced); it is locked with the team but never loaded into a prompt.

Honest reporting (D2): the README states the host OS-confinement setting and its reason, how each read-only mount
is enforced, and that ``inherited`` guest confinement means "inside the host's profile", never a fresh sandbox.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_RUN = "agentteams --run-wasm-agent <slug> --wasm-task \"...\" --project ."


def team_slugs(manifest: dict[str, Any]) -> list[str]:
    """Return the slugs of the agent files this render emits (``output_files`` of type ``agent``), sorted.

    Args:
        manifest: The team manifest.

    Returns:
        The slugs (falls back to ``agent_slug_list`` when the plan is absent, e.g. a direct adapter call).

    Raises:
        Nothing.
    """
    planned = [Path(f["path"]).name for f in manifest.get("output_files") or []
               if isinstance(f, dict) and f.get("type") == "agent" and isinstance(f.get("path"), str)]
    slugs = [p[: -len(".agent.md")] if p.endswith(".agent.md") else Path(p).stem for p in planned]
    return sorted(set(slugs or [s for s in manifest.get("agent_slug_list") or [] if isinstance(s, str)]))


def read_only_via(grant: Any, host_os_confine: str) -> str:
    """How a read-only mount of *grant* is enforced at run time (``none`` when it has none).

    With ``host_os_confine: auto`` the guests run inside the host's OS profile (``inherited``), where the executor
    takes a size-capped per-run snapshot. With ``off`` a fresh nested OS sandbox enforces it where this host has one.

    Args:
        grant: The agent's ``wasm_exec.Grant``.
        host_os_confine: ``auto`` or ``off``.

    Returns:
        ``none`` | ``snapshot`` | ``os``.

    Raises:
        Nothing.
    """
    if not any(m.mode == "ro" for m in grant.mounts):
        return "none"
    if host_os_confine == "auto":
        return "snapshot"
    from agentteams import _sandbox_profiles

    return "os" if _sandbox_profiles.available() else "snapshot"


def grants_document(manifest: dict[str, Any], state: Any, *, host_os_confine: str) -> dict[str, Any]:
    """Return ``grants.json``: one entry per emitted agent, from ``sandbox_state.wasm_grant_for`` only.

    Args:
        manifest: The team manifest (``output_files``, the CLI project root).
        state: The effective sandbox state (WASM on).
        host_os_confine: ``runtime.json``'s value.

    Returns:
        ``{"version": 1, "agents": {slug: entry}}``.

    Raises:
        ValueError: A mount host outside the project root.
    """
    from agentteams.frameworks._write_roots import project_root_of
    from agentteams.sandbox_state import wasm_grant_for
    from agentteams.wasm_agent.team import FILE_VERSION, grant_entry

    root = Path(project_root_of(manifest) or ".")
    agents = {}
    for slug in team_slugs(manifest):
        grant = wasm_grant_for(state, slug)
        agents[slug] = grant_entry(slug, grant, project_root=root, read_only_via=read_only_via(grant, host_os_confine))
    return {"version": FILE_VERSION, "agents": agents}


def _mounts(entry: dict[str, Any]) -> str:
    parts = [f"`{m['guest']}` {m['mode']} (`{m['host']}`)" for m in entry["mounts"]]
    return ", ".join(parts) or "compute only"


def readme_text(manifest: dict[str, Any], state: Any, runtime: dict[str, Any], grants: dict[str, Any]) -> str:
    """Return the team's ``README.md``.

    Args:
        manifest: The team manifest (``project_name``).
        state: The effective sandbox state.
        runtime: ``runtime.json``'s content.
        grants: ``grants.json``'s content.

    Returns:
        Markdown (deterministic).

    Raises:
        Nothing.
    """
    from agentteams import model_providers as MP

    project = manifest.get("project_name") or "this project"
    keys = "\n".join(f"| `{n}` | {', '.join(f'`{v}`' for v in p.key_env) or 'none (keyless)'} |"
                     for n, p in MP.PROVIDERS.items())
    rows = "\n".join(f"| `{slug}` | {_mounts(e)} | {e['limits']['fuel']} / {e['limits']['max_memory_bytes']} / "
                     f"{e['limits']['timeout_s']}s | {e['read_only_via']} |" for slug, e in grants["agents"].items())
    models = ", ".join(f"`{t}` = {('`' + m + '`') if m else 'unset'}" for t, m in runtime["models"].items())
    host = ("`auto`: Stage 0 runs the host inside a fresh OS sandbox where this machine has one (Seatbelt/bwrap); "
            "guests then run inside the host's profile (reported `inherited`, never claimed as a fresh sandbox)"
            if runtime["host_os_confine"] == "auto" else
            "`off`: the OS sandbox switch is off, so the host is NOT OS-confined (WASM limits guest code only)")
    return f"""# WASM agent team — {project}

Generated by agentteams (`--framework wasm`). Every file in `.agentteams-wasm/` is hashed into the control-plane
lock `.agentteams/wasm-team.lock.json`; the launcher refuses a team whose files drifted from it. Edit the brief and
re-render (`--update`) instead of editing these files.

## Run an agent

```
{_RUN}
agentteams --verify-wasm-run <run-id> --project .
agentteams --revoke-wasm-run <run-id> --project . --revoke-reason "..."
```

Install the pinned runtime first (`agentteams --wasm-install all --project .`). The provider is
`{runtime['provider'] or 'unset'}` (override with `--wasm-provider NAME`, a preset name only); models: {models}.
A tier with no model refuses the run: set `wasm_runtime.models.<tier>` in the brief or
`AGENTTEAMS_WASM_MODEL_<TIER>`. No model id is built in.

Provider keys are read only from the launcher's environment (never from these files, a flag, or a guest):

| Provider | Key variable |
|---|---|
{keys}

The key goes to the preset's own base URL unless the operator sets `AGENTTEAMS_WASM_BASE_URL` or `--wasm-base-url`
for the run. A `base_url` in this team's `runtime.json` (from the brief) is used only for a keyless local preset on a
loopback host.

## Enforcement status

- WASM confinement: **on** ({state.wasm_reason}). Each agent step and every `run_code` is a fresh WASM guest with an
  empty environment, no network, fuel/memory/time limits, and only the mounts below.
- Host OS confinement: {host}. OS switch: {'on' if state.os else 'off'} ({state.os_reason}).
- Read-only mounts are always enforced, never mounted writable: `os` = a nested OS profile, `snapshot` = a per-run
  copy (size-capped: a read path larger than the cap refuses the run).
- Delegation (`delegate`) runs a listed sub-agent in its own WASM guest with a grant never wider than its
  delegator's (the intersection of both); `--verify-wasm-run <root> --tree` checks the whole run tree.

| Agent | Mounts | Fuel / memory bytes / timeout | Read-only via |
|---|---|---|---|
{rows}
"""


__all__ = ["grants_document", "read_only_via", "readme_text", "team_slugs"]
