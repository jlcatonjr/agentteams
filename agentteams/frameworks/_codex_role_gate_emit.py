"""
_codex_role_gate_emit.py — the Codex role gate's generated files (Phase 1b, 2026-10-08).

Under ``write_policy: "orchestrator-only"`` a Codex team gets three more files beside its runner:
the role gate (``.agentteams/bin/codex-role-gate.py``, a pinned copy of ``agentteams/data/codex-role-gate.py``),
the read-only file server Goose uses under the policy (``.agentteams/bin/goose-readfs-mcp.py``), and
``.codex/hooks.json``, which runs the gate before every tool call. Codex tags a spawned agent's calls with
``agent_type``/``agent_id`` (probed live on codex-cli 0.160.1), and the gate limits those to the read-only
tools. The runner (``_codex_sandbox_emit``) checks these files against their pins before launch.

Integrity-pinned: it pins the gate's hash and builds the hook wiring.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = [
    "CODEX_HOOKS_PROJECT_PATH",
    "CODEX_ROLE_GATE_PROJECT_PATH",
    "CODEX_ROLE_GATE_SHA256",
    "HOOK_TIMEOUT_SECONDS",
    "codex_hooks_json",
    "codex_hooks_json_is_ours",
    "codex_role_gate_enabled",
    "role_gate_output_files",
]

#: Under ``write_policy: "orchestrator-only"`` (Phase 1b): the role gate Codex runs as a PreToolUse hook,
#: installed in the control plane (read-only under the launcher, like Goose's read-only server beside it).
CODEX_ROLE_GATE_PROJECT_PATH = ".agentteams/bin/codex-role-gate.py"
#: The project hooks file that wires the gate (inside ``.codex/``, a prompt root the runner protects).
CODEX_HOOKS_PROJECT_PATH = ".codex/hooks.json"
#: sha256 of the shipped gate (``agentteams/data/codex-role-gate.py``). This module is integrity-pinned, so the
#: runner checks the installed copy against it; ``tests/test_codex_role_gate.py`` keeps it current.
CODEX_ROLE_GATE_SHA256 = "8e2a3bea7a3b0022c85db521431699506641859bcc7577360bb82031e9f874c1"
#: The hook's timeout. Codex lets a call through when a hook times out (probed live), so this is generous and
#: the gate is kept fast; it is stated so the bound is visible rather than Codex's unstated default.
HOOK_TIMEOUT_SECONDS = 60
_ROLE_GATE_SOURCE = Path(__file__).resolve().parent.parent / "data" / "codex-role-gate.py"


def codex_role_gate_enabled(manifest: dict[str, Any]) -> bool:
    """Return True when the Codex role gate is emitted: ``write_policy: "orchestrator-only"`` with the runner.

    Args:
        manifest: The team manifest.

    Returns:
        Whether the gate, its hooks file and the read-only server are emitted and the runner enforces them.
    """
    from agentteams import write_policy
    from agentteams.frameworks._codex_sandbox_emit import codex_sandbox_feature_enabled

    return write_policy.enabled(manifest) and codex_sandbox_feature_enabled(manifest)


def codex_hooks_json() -> str:
    """Return ``.codex/hooks.json``: every tool call goes through the role gate, and any failure blocks.

    The command runs through a shell. ``$AGENTTEAMS_PYTHON`` (an absolute interpreter resolved outside the
    launcher, so no writable ``PATH`` entry can substitute one) and ``$AGENTTEAMS_ROOT`` are set by the runner;
    ``|| exit 2`` turns a missing interpreter, file or variable into a block, because Codex lets a call
    through on any other non-zero exit.

    Returns:
        The JSON text.
    """
    command = f'"$AGENTTEAMS_PYTHON" -I -S "$AGENTTEAMS_ROOT/{CODEX_ROLE_GATE_PROJECT_PATH}" || exit 2'
    hook = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [hook]}]}}, indent=2) + "\n"


def codex_hooks_json_is_ours(path: Path) -> bool:
    """Return True when *path* may be (over)written with the generated ``.codex/hooks.json``.

    It may when it is absent, or a regular file whose every hook command runs the role gate (this or an
    earlier agentteams release wrote it). A symlink, unreadable or non-JSON file, or any other hook, is the
    operator's: never overwritten.

    Args:
        path: The project's ``.codex/hooks.json``.

    Returns:
        Whether agentteams owns it.
    """
    if path.is_symlink():
        return False
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        commands = [h.get("command") for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return bool(commands) and all(isinstance(c, str) and f"/{CODEX_ROLE_GATE_PROJECT_PATH}" in c
                                  for c in commands)


def _role_gate_content() -> str:
    """Return the shipped role gate, refusing a copy that doesn't match :data:`CODEX_ROLE_GATE_SHA256`.

    Returns:
        The gate source.

    Raises:
        FileNotFoundError: When the package data is missing.
        ValueError: When it doesn't match its pin.
    """
    try:
        text = _ROLE_GATE_SOURCE.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"the Codex role gate is missing from this agentteams install "
                                f"({_ROLE_GATE_SOURCE}); reinstall agentteams") from exc
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != CODEX_ROLE_GATE_SHA256:
        raise ValueError("the installed Codex role gate does not match its pinned hash (CODEX_ROLE_GATE_SHA256); "
                         "reinstall agentteams before rendering under write_policy orchestrator-only")
    return text



def role_gate_output_files() -> list[tuple[str, str]]:
    """Return the gate, the read-only server and ``.codex/hooks.json``, relative to ``.codex/agents/``.

    Returns:
        ``(rel_path, content)`` pairs.

    Raises:
        FileNotFoundError: When the gate or the server is missing from the install.
        ValueError: When either doesn't match its pinned hash.
    """
    from agentteams.frameworks.goose_docs import _readfs_mcp_content
    from agentteams.frameworks.goose_tool_scoping import READFS_PROTECTED_PATH, READFS_SHA256

    readfs = _readfs_mcp_content()
    if hashlib.sha256(readfs.encode("utf-8")).hexdigest() != READFS_SHA256:
        raise ValueError("the installed read-only file server does not match its pinned hash (READFS_SHA256); "
                         "reinstall agentteams before rendering under write_policy orchestrator-only")
    return [(f"../../{CODEX_ROLE_GATE_PROJECT_PATH}", _role_gate_content()),
            (f"../../{READFS_PROTECTED_PATH}", readfs),
            ("../hooks.json", codex_hooks_json())]
