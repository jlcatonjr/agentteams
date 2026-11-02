"""_codex_wasm_gate.py — the Codex role gate in WASM mode (WASM plan P13).

Without ``write_policy: "orchestrator-only"`` but with WASM on and Codex confinement requested (``codex:sandbox``),
a Codex team gets the same pinned role gate (``.agentteams/bin/codex-role-gate.py``) and ``.codex/hooks.json`` the
write policy uses, but the runner starts it in WASM mode (``AGENTTEAMS_CODEX_WASM=1``): a spawned agent is denied
the shell and steered to ``run_wasm`` (the shared compute-only ``agentteams_wasm`` copy in ``.codex/config.toml``),
except the agents in ``AGENTTEAMS_CODEX_NATIVE_KEEP`` (the exempt orchestrator and the operator's
``native_exec_keep``). The read-only file server is not installed: nothing else is narrowed.

Not emitted under ``native_exec: keep`` (nothing to deny) or under the write policy (its own gate already denies
every spawned agent everything but ``agentteams_readfs``). Holds only while Codex is started by
``.codex/confined-run.example.sh``; unknown shell tool names pass and Codex lets a call through when a hook times
out (both stated in ``wasm_enforcement``). Integrity-pinned: it decides when the gate's WASM mode is emitted.
"""

from __future__ import annotations

import re
import sys
from typing import Any

__all__ = [
    "codex_wasm_gate_emitted",
    "codex_wasm_gate_enabled",
    "runner_env_flags",
    "wasm_gate_output_files",
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def codex_wasm_gate_enabled(manifest: dict[str, Any]) -> bool:
    """Return whether the runner starts the role gate in WASM mode for this Codex team.

    Args:
        manifest: The team manifest.

    Returns:
        WASM on, ``codex:sandbox`` requested, no ``write_policy``, and ``native_exec`` not ``keep``.

    Raises:
        ValueError: A malformed ``wasm_sandbox`` record or an invalid ``os_sandbox`` value.
    """
    from agentteams import write_policy
    from agentteams.frameworks._codex_sandbox_emit import codex_sandbox_feature_enabled
    from agentteams.wasm_config import manifest_wasm_config, wasm_switch

    if not wasm_switch(manifest)[0] or write_policy.enabled(manifest):
        return False
    if manifest_wasm_config(manifest).native_exec == "keep":
        return False
    return codex_sandbox_feature_enabled(manifest)


def codex_wasm_gate_emitted(manifest: dict[str, Any], platform: str | None = None) -> bool:
    """Return whether this render emits the WASM-mode gate (Codex target, a host with the neutral launcher).

    Args:
        manifest: The team manifest.
        platform: ``sys.platform`` override (tests).

    Returns:
        :func:`codex_wasm_gate_enabled` on a Codex render on Linux or macOS.

    Raises:
        ValueError: As :func:`codex_wasm_gate_enabled`.
    """
    plat = sys.platform if platform is None else platform
    if manifest.get("framework") not in (None, "codex") or not (plat.startswith("linux") or plat.startswith("darwin")):
        return False
    return codex_wasm_gate_enabled(manifest)


def runner_env_flags(manifest: dict[str, Any]) -> str:
    """Return the launcher ``--setenv`` flags that select WASM mode and carry the keep list.

    Each name is a validated agentteams slug, so the single-quoted value cannot expand.

    Args:
        manifest: The team manifest.

    Returns:
        `` --setenv AGENTTEAMS_CODEX_WASM=1 --setenv AGENTTEAMS_CODEX_NATIVE_KEEP='a,b'``.

    Raises:
        ValueError: A keep entry outside the slug grammar (never expected: the brief is validated).
    """
    from agentteams.wasm_enforcement import codex_gate_keep

    keep = codex_gate_keep(manifest)
    bad = [k for k in keep if not _SLUG_RE.fullmatch(k)]
    if bad:
        raise ValueError(f"native_exec_keep names a non-slug agent: {bad!r}")
    return f" --setenv AGENTTEAMS_CODEX_WASM=1 --setenv AGENTTEAMS_CODEX_NATIVE_KEEP='{','.join(keep)}'"


def wasm_gate_output_files() -> list[tuple[str, str]]:
    """Return the gate and ``.codex/hooks.json`` (no read-only server), relative to ``.codex/agents/``.

    Returns:
        ``(rel_path, content)`` pairs.

    Raises:
        FileNotFoundError: The gate is missing from the install.
        ValueError: The gate does not match its pin.
    """
    from agentteams.frameworks import _codex_role_gate_emit as rg

    return [(f"../../{rg.CODEX_ROLE_GATE_PROJECT_PATH}", rg.role_gate_content()),
            ("../hooks.json", rg.codex_hooks_json())]
