"""
_codex_sandbox_emit.py — the operator-run Codex confinement runner (Phase 1a, 2026-10-08).

Codex's own sandbox cannot nest inside agentteams' framework-neutral launcher
``sandbox/confine-run.sh`` (Codex's sandbox setup fails inside bwrap/Seatbelt), so a confined
Codex team runs Codex with ``--sandbox danger-full-access`` INSIDE the launcher, and the launcher
is the boundary. This module emits ``.codex/confined-run.example.sh``, an INERT example that
wraps the launcher with the settings Codex needs (a writable per-project ``CODEX_HOME`` outside
the repo, host egress for its API). It is the Codex analog of goose's Linux runner
(``_goose_sandbox_emit._build_linux_goose_runner``) and, because the launcher supports both, it
is emitted on Linux AND macOS.

Honest label: Codex ignores a custom agent's ``sandbox_mode`` (spawned agents inherit the
session's sandbox) and every agent command shares the launcher's write access (the project plus
``CODEX_HOME``), so per-role limits on Codex are INSTRUCTION-LEVEL only, except under
``write_policy: "orchestrator-only"`` (Phase 1b, 2026-10-08). There the runner also turns on a
generated PreToolUse role gate (``agentteams/data/codex-role-gate.py``): Codex tags a spawned agent's
calls with ``agent_type``, and the gate limits those to the read-only ``agentteams_readfs`` tools. That
is harness-level (it holds while Codex runs the hook), like Claude's tool grants. Every launch also runs
a self-probe inside the launcher that refuses to start Codex when the boundary doesn't hold. Generation
cannot verify that Codex is actually launched through this script.

Integrity-pinned: it emits a boundary (the runner) and pins the gate's hash.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any

from agentteams.frameworks._codex_role_gate_emit import (
    CODEX_HOOKS_PROJECT_PATH,
    CODEX_ROLE_GATE_PROJECT_PATH,
    CODEX_ROLE_GATE_SHA256,
    codex_hooks_json,
    codex_role_gate_enabled,
    role_gate_output_files,
)

__all__ = [
    "CODEX_RUNNER_PROJECT_PATH",
    "CODEX_RUNNER_REL",
    "codex_sandbox_feature_enabled",
    "codex_sandbox_output_files",
]

#: The runner, relative to the Codex agents dir (``.codex/agents/``), so it lands at
#: ``.codex/confined-run.example.sh``.
CODEX_RUNNER_REL = "../confined-run.example.sh"

#: The project-relative location the runner lands at (listed in
#: ``_sandbox_emit.OPERATOR_EXAMPLE_PATHS`` so agents cannot edit what the operator runs next).
CODEX_RUNNER_PROJECT_PATH = ".codex/confined-run.example.sh"

def codex_sandbox_feature_enabled(manifest: dict[str, Any]) -> bool:
    """Return True iff workspace write-confinement is REQUESTED on this Codex manifest.

    Reads both sources of truth, mirroring ``_goose_sandbox_emit._goose_sandbox_feature_enabled``:
    the ``codex:sandbox`` host-feature token (from ``--target-host-features`` and from the
    ``privilege_profile`` expansion) and the ``privilege_profile`` field itself, so a confined
    manifest also emits on the ``convert``/``render`` paths that do not run the
    profile-to-host_features union. The neutral launcher this runner wraps is gated the same way.

    Args:
        manifest: The team manifest.

    Returns:
        Whether confinement is requested.
    """
    if "codex:sandbox" in (manifest.get("host_features") or []):
        return True
    return manifest.get("privilege_profile") in {"confined", "exclusive"}


#: Runs INSIDE the launcher as ``bash -c "$SELF_PROBE" codex-confined <codex args>`` and then becomes Codex
#: (``exec``), so the probe and Codex run under the same profile. It checks that the boundary the runner asked
#: for actually holds, from the inside, and refuses to start Codex otherwise (exit 3). Whether a key, a legacy
#: key file or ``.agentteams`` exists is read OUTSIDE (``AGENTTEAMS_PROBE_*``), because inside, a path the
#: profile denies can look absent and a probe would pass trivially. ``: >>`` opens for append and writes
#: nothing, so a probe that unexpectedly succeeds changes no file.
_RUNNER_SELF_PROBE = (
    "SELF_PROBE='set -u\n"
    'fail(){ echo "SELF-PROBE FAILED: $1. This launch is NOT confined as expected; refusing to start Codex." >&2; exit 3; }\n'
    'unwritable(){ if ( : >> "$1" ) 2>/dev/null; then fail "$1 is writable"; fi; }\n'
    'if [ "$AGENTTEAMS_PROBE_KEYS" = 1 ] && [ -n "$(ls -A "$HOME/.config/agentteams/keys" 2>/dev/null)" ]; then\n'
    '  fail "the signing-key dir is readable"; fi\n'
    'if [ -n "$AGENTTEAMS_PROBE_PEM" ] && head -c 1 "$AGENTTEAMS_PROBE_PEM" >/dev/null 2>&1; then\n'
    '  fail "a legacy signing-key file is readable"; fi\n'
    'if [ "$AGENTTEAMS_PROBE_AT" = 1 ]; then\n'
    '  d="$AGENTTEAMS_ROOT/.agentteams/.self-probe.$$"\n'
    '  if mkdir "$d" 2>/dev/null; then rmdir "$d"; fail "$AGENTTEAMS_ROOT/.agentteams is writable"; fi; fi\n'
    'if [ "$AGENTTEAMS_PROBE_CODEX" = 1 ]; then\n'
    '  d="$AGENTTEAMS_ROOT/.codex/.self-probe.$$"\n'
    '  if mkdir "$d" 2>/dev/null; then rmdir "$d"; fail "$AGENTTEAMS_ROOT/.codex is writable"; fi; fi\n'
    'unwritable "$CODEX_HOME/config.toml"; unwritable "$CODEX_HOME/hooks.json"\n'
    'if [ "$AGENTTEAMS_PROBE_GATE" = 1 ]; then\n'
    '  unwritable "$AGENTTEAMS_ROOT/.codex/hooks.json"; unwritable "$AGENTTEAMS_ROOT/.agentteams/bin/codex-role-gate.py"; fi\n'
    "exec codex \"$@\"'\n"
)


def _runner_label(gate: bool) -> str:
    """Return the runner header's honest label, which depends on whether the role gate is emitted.

    Args:
        gate: Whether :func:`codex_role_gate_enabled` holds for this team.

    Returns:
        Comment lines.
    """
    if gate:
        return (
            "# PER-ROLE LIMITS (write_policy \"orchestrator-only\"): Codex ignores a custom agent's sandbox_mode,\n"
            "# so this runner turns on the generated role gate instead: .codex/hooks.json runs\n"
            "# .agentteams/bin/codex-role-gate.py before every tool call, and a spawned agent (any call Codex\n"
            "# tags with agent_type) may use only the read-only agentteams_readfs tools. That is HARNESS-LEVEL,\n"
            "# like Claude's tool grants: it holds only while Codex runs the hook, which this script arranges\n"
            "# with --dangerously-bypass-hook-trust (the hook files are pinned and read-only in the launcher).\n"
            "# Residual: Codex lets a call through when a hook TIMES OUT; the gate is kept fast for that reason.\n"
            "# The launcher (OS level) keeps the ledger key and .agentteams out of reach for every role.\n"
            "# agentteams cannot verify that Codex is actually launched through this script.\n"
        )
    return (
        "# HONEST LABEL: per-role limits are INSTRUCTION-LEVEL on Codex. Codex ignores a custom agent's\n"
        "# sandbox_mode (spawned agents inherit the session's sandbox), and every agent command shares the\n"
        "# same write access. A checking command's generated outputs are writable for every agent.\n"
        "# agentteams cannot verify that Codex is actually launched through this script.\n"
    )


def _runner_gate_block() -> str:
    """Return the runner lines that enforce the role gate's preconditions before launch.

    Checks the gate, the read-only server and ``.codex/hooks.json`` against their pins (refusing a missing,
    symlinked or changed copy), then puts the read-only server's entry in ``CODEX_HOME/config.toml``:
    written when the file is empty, required verbatim when the operator has their own config.

    Returns:
        Bash lines.
    """
    from agentteams.frameworks.goose_tool_scoping import READFS_PROTECTED_PATH, READFS_SHA256

    hooks_sha = hashlib.sha256(codex_hooks_json().encode("utf-8")).hexdigest()
    return (
        "# write_policy orchestrator-only: the role gate's files must be exactly what agentteams shipped.\n"
        'sha(){ { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | cut -d" " -f1; }\n'
        'pin(){ if [ ! -f "$1" ] || [ -L "$1" ] || [ "$(sha "$1")" != "$2" ]; then\n'
        '  echo "REFUSING: $1 is missing, a symlink, or not the pinned copy; regenerate the team with agentteams." >&2; exit 2; fi; }\n'
        f'pin "$ROOT/{CODEX_ROLE_GATE_PROJECT_PATH}" {CODEX_ROLE_GATE_SHA256}\n'
        f'pin "$ROOT/{READFS_PROTECTED_PATH}" {READFS_SHA256}\n'
        f'pin "$ROOT/{CODEX_HOOKS_PROJECT_PATH}" {hooks_sha}\n'
        'case "$ROOT" in *[\\"\\\\]*|*[[:cntrl:]]*)\n'
        '  echo "REFUSING: the project path has a quote, backslash or control character; config.toml cannot name it safely." >&2; exit 2 ;; esac\n'
        '# The gate and the server run under an ABSOLUTE interpreter resolved here, outside the launcher, so no\n'
        '# writable PATH entry (a project venv, say) can stand in for python3. Override: AGENTTEAMS_CODEX_PYTHON.\n'
        'PY="${AGENTTEAMS_CODEX_PYTHON:-$(command -v python3 || true)}"\n'
        'case "$PY" in /*) ;; *) echo "REFUSING: no absolute python3 (set AGENTTEAMS_CODEX_PYTHON)." >&2; exit 2 ;; esac\n'
        '# Follow a symlinked interpreter to its target, so a link outside the project into it is caught.\n'
        'n=0; while [ -L "$PY" ] && [ "$n" -lt 40 ]; do\n'
        '  t="$(readlink "$PY")"; case "$t" in /*) PY="$t" ;; *) PY="$(dirname "$PY")/$t" ;; esac; n=$((n + 1)); done\n'
        'PY="$(cd "$(dirname "$PY")" && pwd -P)/$(basename "$PY")"\n'
        'case "$PY/" in "$ROOT"/*|"$CODEX_HOME"/*|*[\\"\\\\]*|*[[:cntrl:]]*)\n'
        '  echo "REFUSING: python3 at $PY is inside a path agents can write (or unquotable); set AGENTTEAMS_CODEX_PYTHON to an interpreter outside the project." >&2; exit 2 ;; esac\n'
        'if grep -qs agentteams_readfs "$ROOT/.codex/config.toml"; then   # any form: table, dotted key, inline\n'
        '  echo "REFUSING: $ROOT/.codex/config.toml mentions agentteams_readfs; only the runner may define that server." >&2; exit 2; fi\n'
        '# Nested Codex config or hooks (subdir/.codex/) would apply to a session started below the root; refuse them.\n'
        '# (|| true: an unreadable dir or head closing the pipe must not kill the runner under pipefail.)\n'
        '# Case-insensitive (APFS resolves .CODEX to .codex); files or symlinks, and a symlinked .codex dir itself.\n'
        'NESTED="$( { find "$ROOT" \\( -path "$ROOT/.git" -o -path "$ROOT/.codex" \\) -prune -o \\\n'
        '  \\( \\( -type f -o -type l \\) \\( -ipath "*/.codex/hooks.json" -o -ipath "*/.codex/config.toml" \\) \\) -print -o \\\n'
        '  \\( -type l -iname .codex \\) -print 2>/dev/null || true; } | head -n 5)"\n'
        'if [ -n "$NESTED" ]; then\n'
        '  echo "REFUSING: nested Codex config or hooks found; under write_policy orchestrator-only only $ROOT/.codex/ may hold them:" >&2\n'
        '  printf \'%s\\n\' "$NESTED" >&2; exit 2; fi\n'
        'READFS_TOML="[mcp_servers.agentteams_readfs]\n'
        'command = \\"$PY\\"\n'
        f'args = [\\"-I\\", \\"-S\\", \\"$ROOT/{READFS_PROTECTED_PATH}\\", \\"--root\\", \\"$ROOT\\"]"\n'
        'if [ ! -s "$CODEX_HOME/config.toml" ]; then printf \'%s\\n\' "$READFS_TOML" > "$CODEX_HOME/config.toml"\n'
        'elif [ "$(grep -xF -A2 -- "[mcp_servers.agentteams_readfs]" "$CODEX_HOME/config.toml")" != "$READFS_TOML" ]; then\n'
        '  echo "REFUSING: $CODEX_HOME/config.toml lacks the read-only server agents need under write_policy" >&2\n'
        '  echo "orchestrator-only (exactly once, as one block). Add these lines to it (outside the launcher), then rerun:" >&2\n'
        '  printf \'%s\\n\' "$READFS_TOML" >&2; exit 2; fi\n'
    )


def _build_codex_runner(manifest: dict[str, Any]) -> str:
    """Build the INERT ``.codex/confined-run.example.sh`` text.

    Paths it computes itself (the project, ``CODEX_HOME``, scratch) come from its own location and
    ``$HOME`` at run time. The only brief-derived values are the ``workspace_write_roots`` (validated, then
    single-quoted by the goose runner's helper) and, for an ``exclusive`` team, the ``protected_read_paths``
    carried as ``--exclude`` (unsafe ones skipped with a visible note), so nothing from the brief can expand
    in the operator's shell.

    Args:
        manifest: The team manifest.

    Returns:
        The bash script text.
    """
    from agentteams.frameworks._goose_sandbox_emit import _runner_root_expr
    from agentteams.frameworks._write_roots import runner_exclude_flags, validate_write_roots

    roots = validate_write_roots(manifest.get("workspace_write_roots") or ["."], manifest)
    writable_flags = " ".join(f"--writable {_runner_root_expr(r)}" for r in roots)
    exclude_flags, skipped_note = runner_exclude_flags(manifest)
    gate = codex_role_gate_enabled(manifest)
    gate_block = _runner_gate_block() if gate else ""
    bypass = " --dangerously-bypass-hook-trust" if gate else ""
    py_setenv = ' --setenv AGENTTEAMS_PYTHON="$PY"' if gate else ""
    return (
        "#!/usr/bin/env bash\n"
        "# .codex/confined-run.example.sh - EXAMPLE: run Codex inside agentteams' launcher (Linux + macOS).\n"
        "# INERT / operator-run: agentteams never runs this or edits your Codex config. Adapt and run it:\n"
        "#   .codex/confined-run.example.sh                 # interactive Codex\n"
        "#   .codex/confined-run.example.sh exec \"...\"      # any codex arguments pass through\n"
        "#\n"
        "# WHY CODEX'S OWN SANDBOX IS OFF: Codex's sandbox cannot nest inside sandbox/confine-run.sh (its\n"
        "# sandbox setup fails inside bwrap/Seatbelt). So Codex runs with --sandbox danger-full-access\n"
        "# INSIDE the launcher, and the launcher is the boundary: writes reach only the project's write roots\n"
        "# and CODEX_HOME, the agentteams control plane, .agentteams and prompt roots are read-only, and\n"
        "# credential dirs (including ~/.config/agentteams/keys) are masked. Run Codex this way or not at\n"
        "# all: outside the launcher, danger-full-access confines nothing.\n"
        "#\n"
        + _runner_label(gate) +
        "#\n"
        "# CODEX_HOME: Codex must write its home (sessions, logs, auth), so it is writable for every agent\n"
        "# command. Default: $HOME/.config/agentteams/codex-home/<project>-<hash>; override with\n"
        "# AGENTTEAMS_CODEX_HOME. It must be strictly under ~/.config/agentteams/codex-home/ or outside\n"
        "# ~/.config/agentteams entirely, and never $HOME, an ancestor of it, inside this project, or an\n"
        "# ancestor of it. Authenticate it once, OUTSIDE the launcher:  CODEX_HOME=<that dir> codex login\n"
        "# Before launch, config.toml, hooks.json, AGENTS.md and AGENTS.override.md (and the rules/ prompts/ skills/ dirs) are\n"
        "# created empty if absent and then made read-only, so a confined agent cannot plant config, hooks\n"
        "# or instructions that Codex would honour on its next run, inside or outside the launcher.\n"
        "# Its auth is readable by every agent command.\n"
        "#\n"
        "# EGRESS: host by default, because Codex must reach its API. Host egress lets a confined process\n"
        "# reach any host, so whatever it can read (the Codex auth included) could leave the box. For a\n"
        "# pinned loopback proxy instead, set CODEX_CONFINE_EGRESS=proxy and add --proxy ADDR:PORT to the\n"
        "# exec line below (confine-run.sh --egress proxy; see the agentteams sandboxing guide).\n"
        "#\n"
        "# RUN AS THE WORKSPACE-OWNING USER, not root (on Linux, bwrap --unshare-user as root loses DAC\n"
        "# over your files).\n"
        "set -euo pipefail\n"
        'HERE="$(cd "$(dirname "$0")" && pwd -P)"; ROOT="$(cd "$HERE/.." && pwd -P)"; REPO_ROOT="$ROOT"\n'
        'LAUNCHER="$ROOT/sandbox/confine-run.sh"\n'
        '[ -x "$LAUNCHER" ] || { echo "neutral launcher missing at $LAUNCHER (is confinement emitted for this team?)" >&2; exit 1; }\n'
        '[ "$(id -u)" -ne 0 ] || echo "WARNING: running as root; run as the workspace user." >&2\n'
        'command -v codex >/dev/null 2>&1 || { echo "codex not found on PATH" >&2; exit 1; }\n'
        'refuse(){ echo "REFUSING CODEX_HOME=$1: $2" >&2; exit 2; }\n'
        'check_home(){   # path home cfg root -> refuse unless an allowed CODEX_HOME placement\n'
        '  case "$1" in /*) ;; *) refuse "$1" "it must be an absolute path" ;; esac\n'
        '  case "$1" in /|*//*) refuse "$1" "it must not be / or contain //" ;; esac\n'
        '  case "/$1/" in */../*|*/./*) refuse "$1" "it must not contain . or .. segments" ;; esac\n'
        '  case "$1/" in\n'
        '    "$3/codex-home/"?*) ;;\n'
        '    "$3"/*) refuse "$1" "inside ~/.config/agentteams it must be under codex-home/ (the keys and operator files stay out of reach)" ;;\n'
        '  esac\n'
        '  case "$2/" in "$1"/*) refuse "$1" "it is \\$HOME or one of its parents" ;; esac\n'
        '  case "$4/" in "$1"/*) refuse "$1" "it is this project or one of its parents" ;; esac\n'
        '  case "$1/" in "$4"/*) refuse "$1" "it is inside this project; keep it outside the repo" ;; esac\n'
        '}\n'
        'HASH="$(printf %s "$ROOT" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-12)"\n'
        'CODEX_HOME="${AGENTTEAMS_CODEX_HOME:-$HOME/.config/agentteams/codex-home/$(basename "$ROOT")-$HASH}"\n'
        'check_home "${CODEX_HOME%/}" "$HOME" "$HOME/.config/agentteams" "$ROOT"   # BEFORE anything is created\n'
        'mkdir -p "$CODEX_HOME"; CODEX_HOME="$(cd "$CODEX_HOME" && pwd -P)"\n'
        'HOME_P="$(cd "$HOME" && pwd -P)"; CFG_P="$HOME_P/.config/agentteams"\n'
        '[ -d "$CFG_P" ] && CFG_P="$(cd "$CFG_P" && pwd -P)"\n'
        'check_home "$CODEX_HOME" "$HOME_P" "$CFG_P" "$ROOT"   # again with symlinks resolved\n'
        '# Config Codex trusts on its next run: create empty if absent, then protect, so it cannot be planted.\n'
        'for d in rules prompts skills plugins; do mkdir -p "$CODEX_HOME/$d"; done\n'
        '[ -e "$CODEX_HOME/config.toml" ] || : > "$CODEX_HOME/config.toml"\n'
        'for f in AGENTS.md AGENTS.override.md; do [ -e "$CODEX_HOME/$f" ] || : > "$CODEX_HOME/$f"; done\n'
        '[ -e "$CODEX_HOME/hooks.json" ] || printf \'{}\\n\' > "$CODEX_HOME/hooks.json"\n'
        + gate_block +
        f'if awk -v gate={int(gate)} \'gate && $0 == "[mcp_servers.agentteams_readfs]" {{ next }}\n'
        '    /^[[:space:]]*(approval_policy|sandbox_mode|notify)[[:space:]]*=/ || /^[[:space:]]*\\[(mcp_servers|sandbox_workspace_write|hooks|plugins)/ || /^[[:space:]]*plugins\\./ { f = 1 }\n'
        '    END { exit !f }\' "$CODEX_HOME/config.toml"; then\n'
        '  echo "WARNING: $CODEX_HOME/config.toml sets security-relevant keys; review it (it is read-only in the sandbox, but Codex honours it)." >&2\n'
        'fi\n'
        'SCRATCH="${CODEX_CONFINE_SCRATCH:-$(mktemp -d "${TMPDIR:-/tmp}/codex-confined.XXXXXX")}"\n'
        'PROTECT=()\n'
        'if [ -e "$ROOT/.agentteams" ]; then PROTECT+=( --protect "$ROOT/.agentteams" ); fi\n'
        'for f in config.toml hooks.json AGENTS.md AGENTS.override.md rules prompts skills plugins; do\n'
        '  PROTECT+=( --protect "$CODEX_HOME/$f" ); done\n'
        'EGRESS="${CODEX_CONFINE_EGRESS:-host}"\n'
        + skipped_note +
        '# Self-probe facts are read OUTSIDE the launcher (inside, a denied path can look absent).\n'
        'PK=0; [ -n "$(ls -A "$HOME/.config/agentteams/keys" 2>/dev/null)" ] && PK=1\n'
        'PA=0; [ -e "$ROOT/.agentteams" ] && PA=1\n'
        'PC=0; [ -d "$ROOT/.codex" ] && PC=1\n'
        'PEM=""; for p in "$HOME"/.config/agentteams/*.pem; do [ -f "$p" ] && { PEM="$p"; break; }; done\n'
        + _RUNNER_SELF_PROBE +
        f'exec "$LAUNCHER" --scratch "$SCRATCH" {writable_flags} --writable "$CODEX_HOME" \\\n'
        f'  ${{PROTECT[@]+"${{PROTECT[@]}}"}} --protect-prompt-roots{exclude_flags} --egress "$EGRESS" \\\n'
        f'  --setenv CODEX_HOME="$CODEX_HOME" --setenv AGENTTEAMS_ROOT="$ROOT" --setenv AGENTTEAMS_CODEX_CONFINED=1 \\\n'
        f'  --setenv AGENTTEAMS_PROBE_KEYS="$PK" --setenv AGENTTEAMS_PROBE_AT="$PA" --setenv AGENTTEAMS_PROBE_GATE={int(gate)} \\\n'
        f'  --setenv AGENTTEAMS_PROBE_PEM="$PEM" --setenv AGENTTEAMS_PROBE_CODEX="$PC"{py_setenv} --env-allow PATH --env-allow HOME \\\n'
        f'  -- bash -c "$SELF_PROBE" codex-confined --sandbox danger-full-access{bypass} "$@"\n'
    )


def codex_sandbox_output_files(
    manifest: dict[str, Any], platform: str | None = None
) -> list[tuple[str, str]]:
    """Return the ``(rel_path, content)`` files for the Codex confinement runner, or ``[]``.

    Emits ``.codex/confined-run.example.sh`` when, and only when, confinement is requested for
    this Codex team (:func:`codex_sandbox_feature_enabled`) AND the build host is Linux or macOS,
    the two platforms where the neutral launcher it wraps is emitted
    (``_linux_sandbox_emit.linux_sandbox_output_files`` / ``macos_sandbox_output_files``).
    Elsewhere there is no launcher to wrap, so nothing is emitted.

    Args:
        manifest: The team manifest.
        platform: Override for the platform string (defaults to live ``sys.platform``); lets
            tests exercise each branch deterministically.

    Under ``write_policy: "orchestrator-only"`` (:func:`codex_role_gate_enabled`) it also emits the role
    gate (``.agentteams/bin/codex-role-gate.py``), the read-only file server Goose uses under the policy
    (``.agentteams/bin/goose-readfs-mcp.py``) and ``.codex/hooks.json``, each checked against its pin.

    Returns:
        The runner (plus the role-gate files under the policy), or ``[]``.

    Raises:
        FileNotFoundError: When the role gate or read-only server is missing from the install.
        ValueError: When either doesn't match its pinned hash.
    """
    if not codex_sandbox_feature_enabled(manifest):
        return []
    plat = sys.platform if platform is None else platform
    if not (plat.startswith("linux") or plat.startswith("darwin")):
        return []
    files = [(CODEX_RUNNER_REL, _build_codex_runner(manifest))]
    if codex_role_gate_enabled(manifest):
        files += role_gate_output_files()
    return files
