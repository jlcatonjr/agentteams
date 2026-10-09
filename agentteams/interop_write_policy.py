"""``write_policy`` for ``--interop-from --description BRIEF`` (M6: adopted bespoke agents).

An interop import builds a stub manifest from the source team. Without the brief's ``write_policy`` and
``mcp_grants`` in it, a granted agent rendered through interop got no ``agentteams_runner`` block or tools,
while the orchestrator's queue refused it: an agent that could write by no route at all. This module carries
those fields from the brief, under the same checks native generation applies (:func:`write_policy.resolve`),
narrows each imported non-orchestrator agent's tools, gives it its section once, and installs the pinned
server where native generation would.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentteams import write_policy as _wp

#: Frameworks interop may render under the switch: the two whose session sandbox the runner relies on and whose
#: adapters emit the agentteams_runner block. Codex holds the switch only via its launcher, which interop
#: doesn't emit, so it is refused rather than rendered outside the guarantee.
SUPPORTED: dict[str, tuple[str, str]] = {"claude": (".claude", "agents"), "goose": (".goose", "recipes")}


def manifest_fields(description: dict[str, Any], framework: str) -> dict[str, Any]:
    """The manifest fields an interop import takes from the brief: those native generation would set.

    Args:
        description: The project brief (``ingest.load``).
        framework: The interop target framework.

    Returns:
        ``{}`` when the switch is off for this framework; otherwise ``write_policy`` (and ``mcp_grants`` when
        set), plus ``goose_tool_scoping: "grant"`` for goose, as ``analyze.build_manifest`` sets it.

    Raises:
        ValueError: Any refusal of :func:`write_policy.resolve`; the switch on a framework other than claude or
            goose; malformed ``mcp_grants`` (the runner's own validator, fail closed).
    """
    from agentteams.proposal_policy import ProposalError, _mcp_agents

    policy = _wp.resolve(description, framework)
    fields = _wp.manifest_fields(description, policy)
    if not fields:
        return {}
    if framework not in SUPPORTED:
        raise ValueError(f'--interop-from --description: write_policy "orchestrator-only" is supported for '
                         f"{' and '.join(SUPPORTED)} imports only (not {framework})")
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
        ValueError: The server's install location is outside the project, or ``preserve_existing`` under the
            switch (every agent must be re-rendered).
    """
    manifest.update(fields or {})
    install_server(manifest, framework, target_dir, dry_run=True)
    if _wp.enabled(manifest) and preserve_existing:
        raise ValueError("write_policy orchestrator-only: preserve_existing is not supported (re-render every agent)")


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
    """Whether an imported agent is narrowed under the switch (every agent but the orchestrator).

    ``bridge-orchestrator`` is a writer only as Goose's bridge entry recipe; the Claude audit exempts
    ``orchestrator`` alone, so a Claude import of that slug would be a second, unchecked writer and is refused.

    Args:
        slug: The agent's slug.
        manifest: The interop manifest.
        framework: The interop target framework.

    Returns:
        True when the switch is on and the agent isn't this framework's orchestrator.

    Raises:
        ValueError: A ``bridge-orchestrator`` imported to a framework other than goose under the switch.
    """
    if not _wp.enabled(manifest):
        return False
    if slug in _wp.ORCHESTRATOR_SLUGS - {"orchestrator"} and framework != "goose":
        raise ValueError(f"{slug}: a writer only as Goose's bridge entry recipe; under write_policy "
                         f"orchestrator-only a {framework} import of it would be a second writer")
    return slug not in _wp.ORCHESTRATOR_SLUGS


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
    if not files:
        return []
    canonical = SUPPORTED[framework]
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
