"""``write_policy`` for ``--interop-from --description BRIEF`` (M6: adopted bespoke agents).

An interop import builds a stub manifest from the source team. Without the brief's ``write_policy`` and
``mcp_grants`` in it, a granted agent rendered through interop got no ``agentteams_runner`` block or tools,
while the orchestrator's queue refused it: an agent that could write by no route at all. This module carries
those fields from the brief, under the same checks native generation applies (:func:`write_policy.resolve`),
narrows each imported non-orchestrator agent's tools, gives it its section once, and installs the pinned
server where native generation would.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentteams import write_policy as _wp

#: Frameworks interop may render under the switch, with their canonical agents directory. Claude and Goose carry
#: the switch in the agent files themselves. Codex holds it only through its launcher and role gate, which interop
#: doesn't emit, so a Codex import is admitted only into a native Codex team already rendered under the switch
#: whose launcher, hooks file and pinned role gate are present (:func:`require_native_codex_gate`); the import then
#: relies on them, renders every imported agent read-only, and never replaces a native agent or ``AGENTS.md``.
SUPPORTED: dict[str, tuple[str, str]] = {"claude": (".claude", "agents"), "goose": (".goose", "recipes"),
                                         "codex": (".codex", "agents")}


def manifest_fields(description: dict[str, Any], framework: str) -> dict[str, Any]:
    """The manifest fields an interop import takes from the brief: those native generation would set.

    Args:
        description: The project brief (``ingest.load``).
        framework: The interop target framework.

    Returns:
        ``{}`` when the switch is off for this framework; otherwise ``write_policy`` (and ``mcp_grants`` when
        set), plus ``goose_tool_scoping: "grant"`` for goose, as ``analyze.build_manifest`` sets it, and
        ``privilege_profile`` for codex (it turns on the role-gate reading instructions, as natively). On codex
        ``mcp_grants`` are kept, as native generation keeps them, so a source rendered with them still matches its
        write-policy section; they are not honoured there (:func:`codex_import_notices`).

    Raises:
        ValueError: Any refusal of :func:`write_policy.resolve`; the switch on a framework not in
            :data:`SUPPORTED`; malformed ``mcp_grants`` (the runner's own validator, fail closed).
    """
    from agentteams.proposal_policy import ProposalError, _mcp_agents

    policy = _wp.resolve(description, framework)
    fields = _wp.manifest_fields(description, policy)
    if not fields:
        return {}
    if framework not in SUPPORTED:
        raise ValueError(f'--interop-from --description: write_policy "orchestrator-only" is supported for '
                         f"{', '.join(SUPPORTED)} imports only (not {framework})")
    if framework == "codex":
        fields["privilege_profile"] = description.get("privilege_profile")
    try:
        _mcp_agents(fields.get("mcp_grants"))
    except ProposalError as exc:
        raise ValueError(str(exc)) from exc
    if framework == "goose":
        fields["goose_tool_scoping"] = "grant"
    return fields


def prepare(manifest: dict[str, Any], fields: dict[str, Any] | None, framework: str, target_dir: Path, *,
            preserve_existing: bool) -> None:
    """Merge the brief's fields into the interop manifest and run the checks due before any agent is written.

    Args:
        manifest: The interop manifest (mutated: the fields are set last, so no captured extension overrides them).
        fields: :func:`manifest_fields` of the target brief, or None.
        framework: The interop target framework.
        target_dir: The agents directory being written.
        preserve_existing: Pinned-sync mode, which would skip an unchanged agent with no check.

    Returns:
        None.

    Raises:
        ValueError: The server's install location is outside the project, ``preserve_existing`` under the
            switch (every agent must be re-rendered), or a codex target without its native gate
            (:func:`require_native_codex_gate`).
    """
    manifest.update(fields or {})
    if _wp.enabled(manifest):
        manifest["write_policy_target_framework"] = framework  # the UNENFORCED-on-goose banner keys on it
    if _wp.enabled(manifest) and framework == "codex":
        manifest[_NATIVE_SLUGS_KEY] = require_native_codex_gate(target_dir)
    install_server(manifest, framework, target_dir, dry_run=True)
    if _wp.enabled(manifest) and preserve_existing:
        raise ValueError("write_policy orchestrator-only: preserve_existing is not supported (re-render every agent)")


#: Manifest key holding the native Codex team's agent slugs, which a codex import under the switch never replaces.
_NATIVE_SLUGS_KEY = "interop_native_codex_slugs"


def require_native_codex_gate(target_dir: Path) -> list[str]:
    """Refuse a codex import under the switch unless the native Codex team and its enforcement are in place.

    Codex ignores a spawned agent's ``sandbox_mode`` and tool list; under the switch what keeps an imported agent
    read-only is agentteams' launcher (``.codex/confined-run.example.sh``) and the role gate it runs as a
    PreToolUse hook, which denies every spawned agent's writes by its runtime ``agent_type``, whatever its file
    says. Interop emits neither, so it requires the native render to have done so.

    Args:
        target_dir: The target agents directory; it must be the project's ``.codex/agents``.

    Returns:
        The native team's agent slugs (from its build-log), which the import must not replace.

    Raises:
        ValueError: Naming every missing precondition: not the canonical ``.codex/agents``; no build-log recording
            ``write_policy: "orchestrator-only"``; the role gate absent, a symlink, or not matching
            ``CODEX_ROLE_GATE_SHA256``; ``.codex/hooks.json`` absent or not agentteams'; the launcher absent.
    """
    import hashlib

    from agentteams.frameworks._codex_role_gate_emit import (
        CODEX_HOOKS_PROJECT_PATH,
        CODEX_ROLE_GATE_PROJECT_PATH,
        CODEX_ROLE_GATE_SHA256,
        codex_hooks_json_is_ours,
    )
    from agentteams.frameworks._codex_sandbox_emit import CODEX_RUNNER_PROJECT_PATH

    target = Path(target_dir).resolve()
    if target.parts[-2:] != SUPPORTED["codex"]:
        raise ValueError(f"codex import under write_policy \"orchestrator-only\": the target must be the project's "
                         f".codex/agents (not {target_dir})")
    project = target.parent.parent
    problems: list[str] = []
    try:
        data: Any = json.loads((target / "references" / "build-log.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None  # missing or unreadable: no native switch team is proven
    if not isinstance(data, dict) or data.get("write_policy") != "orchestrator-only":
        problems.append("no native Codex team rendered under the switch (.codex/agents/references/build-log.json "
                        "must record write_policy \"orchestrator-only\")")
    gate = project / CODEX_ROLE_GATE_PROJECT_PATH
    if gate.is_symlink() or not gate.is_file() or (
            hashlib.sha256(gate.read_bytes()).hexdigest() != CODEX_ROLE_GATE_SHA256):
        problems.append(f"{CODEX_ROLE_GATE_PROJECT_PATH} is missing or does not match its pinned hash")
    hooks = project / CODEX_HOOKS_PROJECT_PATH
    if not hooks.is_file() or not codex_hooks_json_is_ours(hooks):  # is_ours alone accepts an absent file
        problems.append(f"{CODEX_HOOKS_PROJECT_PATH} is missing or not agentteams' role-gate wiring")
    if not (project / CODEX_RUNNER_PROJECT_PATH).is_file():
        problems.append(f"{CODEX_RUNNER_PROJECT_PATH} (the launcher) is missing")
    if problems:
        raise ValueError("codex import under write_policy \"orchestrator-only\" relies on the native Codex team's "
                         "launcher and role gate: " + "; ".join(problems) + ". Render the Codex team natively "
                         "first: agentteams --description <brief> --framework codex")
    slugs = {str(s) for s in data.get("agent_slug_list") or []}
    if not slugs:
        raise ValueError("codex import under write_policy \"orchestrator-only\": the native build-log lists no "
                         "agent_slug_list, so the import can't tell native agents from bespoke ones; re-render the "
                         "Codex team natively first")
    generated = {Path(rel).stem for rel in data.get("file_hashes") or {} if str(rel).endswith(".toml")}
    return sorted(slugs | generated | {"orchestrator"})


def native_agent(slug: str, dest: Path, manifest: dict[str, Any]) -> list[str]:
    """The error for an imported agent that would replace a native Codex agent, under the switch.

    Args:
        slug: The imported agent's slug.
        dest: Its destination file.
        manifest: The interop manifest.

    Returns:
        One error when *slug* matches a native agent (its build-log roster or generated ``.toml``, the
        orchestrator always, compared case-insensitively), else none.

    Raises:
        Nothing.
    """
    if _NATIVE_SLUGS_KEY not in manifest:
        return []
    if slug.casefold() not in {s.casefold() for s in manifest[_NATIVE_SLUGS_KEY]}:  # case-insensitive filesystems
        return []
    return [f"{dest}: {slug} belongs to the native Codex team; a codex import under write_policy "
            "\"orchestrator-only\" adds agents beside it and never replaces one (rename the bespoke agent)"]


def keeps_native_instructions(framework: str, manifest: dict[str, Any], inst_dest: Path) -> bool:
    """Whether a codex import under the switch must not write ``AGENTS.md`` (the native team's, the writer's prompt).

    Args:
        framework: The interop target framework.
        manifest: The interop manifest.
        inst_dest: The instructions file the import would write.

    Returns:
        True for a codex import under the switch, whether or not the file exists (a missing one is the native
        render's to restore, never the import's to fill with the source's instructions).

    Raises:
        Nothing.
    """
    return framework == "codex" and _wp.enabled(manifest)


def codex_import_notices(manifest: dict[str, Any], imported: list[str]) -> list[str]:
    """What a codex import under the switch leaves to the operator.

    Args:
        manifest: The interop manifest.
        imported: The imported agents' slugs.

    Returns:
        Notices: grants not honoured on codex, and the imported agents' routing and later native ``--update``;
        empty when this isn't a codex import under the switch or nothing was imported.

    Raises:
        Nothing.
    """
    if _NATIVE_SLUGS_KEY not in manifest or not imported:
        return []
    notices = [f"codex: imported {', '.join(sorted(imported))} read-only (the role gate denies every spawned agent's "
               "writes). AGENTS.md is the native team's and was not changed: route to them in its editable section. "
               "A later native --update lists them as orphans; keep them with --adopt-orphans."]
    granted = sorted(set(manifest.get("mcp_grants") or {}) & set(imported))
    if granted:
        notices.append(f"codex: mcp_grants for {', '.join(granted)} are not honoured on codex (the role gate allows "
                       "spawned agents only the read-only agentteams_readfs tools); they hold on claude.")
    return notices


def left_in_place(dest: Path, manifest: dict[str, Any]) -> list[str]:
    """The error for an existing agent file the import didn't replace, under the switch (it may hold full tools).

    Args:
        dest: The agent file left in place.
        manifest: The interop manifest.

    Returns:
        One error under the switch, else none.

    Raises:
        Nothing.
    """
    if not _wp.enabled(manifest):
        return []
    return [f"{dest}: exists and was not replaced; under write_policy orchestrator-only every imported agent must "
            "be re-rendered (pass --overwrite)"]


def check_rendered(slug: str, name: str, rendered: str, framework: str, manifest: dict[str, Any]) -> None:
    """Refuse a restricted agent whose rendered file fails ``AR_WRITE_POLICY`` (see :func:`audit_problems`).

    Args:
        slug: The agent's slug.
        name: Its file name in the target directory.
        rendered: The adapter's output.
        framework: The interop target framework.
        manifest: The interop manifest.

    Returns:
        None.

    Raises:
        ValueError: The file fails the audit; nothing is written.
    """
    problems = audit_problems(name, rendered, framework, manifest)
    if problems:
        raise ValueError(f"{slug}: the imported agent fails the write-policy audit: {'; '.join(problems)}")


def restricted(slug: str, manifest: dict[str, Any], framework: str) -> bool:
    """Whether an imported agent is narrowed under the switch (every agent but the framework's writer).

    ``bridge-orchestrator`` is a writer only as Goose's bridge entry recipe; elsewhere (the Claude audit exempts
    ``orchestrator`` alone) it is narrowed and audited like any agent, e.g. the read-only one a Copilot bridge
    emits.

    Args:
        slug: The agent's slug.
        manifest: The interop manifest.
        framework: The interop target framework.

    Returns:
        True when the switch is on and the agent isn't this framework's writer.

    Raises:
        Nothing.
    """
    if not _wp.enabled(manifest):
        return False
    return slug not in (_wp.ORCHESTRATOR_SLUGS if framework == "goose" else {"orchestrator"})


def withheld(key: str, value: Any) -> bool:
    """Whether a restricted agent's captured front-matter key must not be restored on import.

    The same keys ``AR_WRITE_POLICY`` errors on: every capability key but ``tools``/``model``/``disallowedTools``/
    ``agents`` (``mcpServers``, ``hooks``, ``skills``, ``memory``, ``allowed-tools``, ``capabilities``, ...) and
    a ``permissionMode`` that relaxes checks.

    Args:
        key: The front-matter key.
        value: Its captured value.

    Returns:
        True when restoring it would widen the agent past the switch.

    Raises:
        Nothing.
    """
    from agentteams.audit_agent_contract import _SAFE_PERMISSION_MODES, _WRITE_POLICY_BAD_KEYS

    if key == "permissionMode":
        return str(value).strip().strip("'\"") not in _SAFE_PERMISSION_MODES
    return key in _WRITE_POLICY_BAD_KEYS


def audit_problems(name: str, rendered: str, framework: str, manifest: dict[str, Any]) -> list[str]:
    """``AR_WRITE_POLICY`` errors for one rendered restricted agent (the native audit, applied per file).

    Args:
        name: The agent's file name in the target directory.
        rendered: The adapter's output for it.
        framework: The interop target framework.
        manifest: The interop manifest.

    Returns:
        The error descriptions; empty when the file passes.

    Raises:
        Nothing.
    """
    from agentteams.audit_agent_contract import _check_write_policy

    ext = "." + name.split(".", 1)[1] if "." in name else ""
    findings = _check_write_policy({name: rendered}, agent_ext=ext, framework=framework, enabled=True,
                                   mcp_grants=manifest.get("mcp_grants") or {})
    return [f.description for f in findings if f.severity == "error"]


def install_server(manifest: dict[str, Any], framework: str, target_dir: Path, *, dry_run: bool) -> list[str]:
    """Install the pinned agentteams_runner server for a granted import, as native generation does.

    Args:
        manifest: The interop manifest.
        framework: The interop target framework.
        target_dir: The agents directory being written.
        dry_run: Report the path without writing.

    Returns:
        The installed path (empty when no agent is granted).

    Raises:
        ValueError: ``target_dir`` isn't the project's canonical agents directory (the server installs two
            levels above it, where the runner checks it), or the packaged server fails its pin.
    """
    from agentteams import runner_mcp
    from agentteams.atomicio import _atomic_write_text

    files = runner_mcp.install_files(manifest)
    canonical = SUPPORTED.get(framework)
    if not manifest.get("mcp_grants") and Path(target_dir).resolve().parts[-2:] != canonical:
        files = []  # only the status tree's .gitignore, which belongs beside the canonical agents dir; the runner
        # creates the tree itself at start
    if not files:
        return []
    if Path(target_dir).resolve().parts[-2:] != canonical:
        raise ValueError(f"mcp_grants need the agents directory to be the project's {'/'.join(canonical)}; this "
                         "--output would install the agentteams_runner server outside the project")
    written = []
    for rel, content in files:
        dest = (Path(target_dir) / rel).resolve()
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(dest, content)
        written.append(str(dest))
    return written


def target_under_switch(agents_dir: Path) -> bool:
    """Whether the team in *agents_dir* was generated under ``write_policy: "orchestrator-only"``.

    Read from its ``references/build-log.json``, which native generation writes with a ``write_policy`` field only
    under the switch. A missing build-log reads as off: a bridge-only project with no native team has no record,
    which is a documented ceiling (pass ``--description`` there). A build-log that exists but can't be read or
    isn't a JSON object reads as **on** (fail closed).

    Args:
        agents_dir: The target agents directory (``.claude/agents``, ``.goose/recipes``, ...).

    Returns:
        True when the build-log records the switch, or exists but is unreadable or malformed.

    Raises:
        Nothing.
    """
    log = Path(agents_dir) / "references" / "build-log.json"
    if not log.exists() and not log.is_symlink():
        return False
    try:
        data = json.loads(log.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True  # present but unreadable: fail closed (a truncated log must not unblock convert/bridge)
    return not isinstance(data, dict) or data.get("write_policy") == "orchestrator-only"


def refuse_outside_interop(mode: str, framework: str, agents_dir: Path,
                           description: dict[str, Any] | None = None) -> None:
    """Refuse ``--convert-from`` / bridge subagent stubs for a team under the switch; interop carries it instead.

    Neither path narrows tools, adds the write-policy section or wires the runner, so under the switch they would
    write agents that can write. ``--interop-from --description`` does all of that and is audited per agent.

    Args:
        mode: The refusing mode, for the message (``"--convert-from"``, ``"--bridge-from subagent stubs"``).
        framework: The target framework.
        agents_dir: The target agents directory (its build-log is checked).
        description: The ``--description`` brief, when given.

    Returns:
        None.

    Raises:
        ValueError: The target's build-log records the switch, or the brief turns it on for *framework* (including
            :func:`write_policy.resolve`'s own refusals).
    """
    on = target_under_switch(agents_dir)
    if not on and description is not None:
        on = bool(_wp.manifest_fields(description, _wp.resolve(description, framework)))
    if on:
        fix = (f"Use: agentteams --interop-from <source agents dir> --framework {framework} --description <brief> "
               "--overwrite" + (" (into a native Codex team rendered under the switch)" if framework == "codex" else "")
               if framework in SUPPORTED else
               f"Regenerate the {framework} team natively: agentteams --description <brief> --framework {framework}")
        raise ValueError(f"{mode}: this team runs write_policy \"orchestrator-only\", which {mode} doesn't apply "
                         f"(it would write agents that can write). {fix}")


def refuse_bridge_stubs(source_framework: str, framework: str, output_root: Path, host_features: list[str],
                        description: dict[str, Any] | None = None) -> None:
    """Refuse bridge subagent stubs (Claude or Goose) for a team under the switch, before the bridge writes.

    The stubs copy the source agents' tools (``bridge_subagents._tools_to_allowed``; Goose stubs ship
    ``developer``), so under the switch they would be writers. Other bridge artifacts are unaffected.

    Args:
        source_framework: The bridge's source framework.
        framework: The bridge target framework.
        output_root: The bridge output root (the project).
        host_features: The bridge host-feature tokens.
        description: The ``--description`` brief, when given.

    Returns:
        None.

    Raises:
        ValueError: Stubs are requested and the target team is under the switch (:func:`refuse_outside_interop`).
    """
    feature = ("bridge:copilot-vscode-to-claude:subagents" if framework == "claude"
               else f"bridge:{source_framework}-to-{framework}:subagents")
    if framework in SUPPORTED and feature in host_features:
        refuse_outside_interop("--bridge-from subagent stubs", framework,
                               Path(output_root).joinpath(*SUPPORTED[framework]), description)
