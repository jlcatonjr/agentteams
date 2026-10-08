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
``CODEX_HOME``), so per-role limits on Codex are INSTRUCTION-LEVEL only. Generation cannot
verify that Codex is actually launched through this script.
"""

from __future__ import annotations

import shlex
import sys
from typing import Any

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
    from agentteams.frameworks._write_roots import path_char_problem, validate_write_roots

    roots = validate_write_roots(manifest.get("workspace_write_roots") or ["."], manifest)
    writable_flags = " ".join(f"--writable {_runner_root_expr(r)}" for r in roots)
    exclude_flags, skipped_note = "", ""
    if manifest.get("privilege_profile") == "exclusive":
        raw = [p for p in (manifest.get("protected_read_paths") or []) if p]
        safe = [p for p in raw if path_char_problem(p) is None]
        # The launcher takes --exclude literally (no ~ expansion), so a ~/ path is spelled "$HOME"/'rest'.
        exclude_flags = "".join(
            f' --exclude "$HOME"/{shlex.quote(p[2:])}' if p.startswith("~/") else f" --exclude {shlex.quote(p)}"
            for p in safe)
        if len(raw) != len(safe):
            skipped_note = (f'echo "NOTE: {len(raw) - len(safe)} protected_read_path(s) SKIPPED from --exclude '
                            '(unsafe chars); their read-exclusion is NOT enforced - sanitize the brief." >&2\n')
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
        "# HONEST LABEL: per-role limits are INSTRUCTION-LEVEL on Codex. Codex ignores a custom agent's\n"
        "# sandbox_mode (spawned agents inherit the session's sandbox), and every agent command shares the\n"
        "# same write access. Under write_policy \"orchestrator-only\" that means KEY CUSTODY ONLY: the\n"
        "# ledger key and .agentteams stay out of reach, but every role can write the project directly.\n"
        "# A checking command's generated outputs are writable for every agent. agentteams cannot verify\n"
        "# that Codex is actually launched through this script.\n"
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
        'for d in rules prompts skills; do mkdir -p "$CODEX_HOME/$d"; done\n'
        '[ -e "$CODEX_HOME/config.toml" ] || : > "$CODEX_HOME/config.toml"\n'
        'for f in AGENTS.md AGENTS.override.md; do [ -e "$CODEX_HOME/$f" ] || : > "$CODEX_HOME/$f"; done\n'
        '[ -e "$CODEX_HOME/hooks.json" ] || printf \'{}\\n\' > "$CODEX_HOME/hooks.json"\n'
        'if grep -Eq \'^[[:space:]]*(approval_policy|sandbox_mode|notify)[[:space:]]*=|^[[:space:]]*\\[(mcp_servers|sandbox_workspace_write|hooks)\' "$CODEX_HOME/config.toml"; then\n'
        '  echo "WARNING: $CODEX_HOME/config.toml sets security-relevant keys; review it (it is read-only in the sandbox, but Codex honours it)." >&2\n'
        'fi\n'
        'SCRATCH="${CODEX_CONFINE_SCRATCH:-$(mktemp -d "${TMPDIR:-/tmp}/codex-confined.XXXXXX")}"\n'
        'PROTECT=()\n'
        'if [ -e "$ROOT/.agentteams" ]; then PROTECT+=( --protect "$ROOT/.agentteams" ); fi\n'
        'for f in config.toml hooks.json AGENTS.md AGENTS.override.md rules prompts skills; do PROTECT+=( --protect "$CODEX_HOME/$f" ); done\n'
        'EGRESS="${CODEX_CONFINE_EGRESS:-host}"\n'
        + skipped_note +
        f'exec "$LAUNCHER" --scratch "$SCRATCH" {writable_flags} --writable "$CODEX_HOME" \\\n'
        f'  ${{PROTECT[@]+"${{PROTECT[@]}}"}} --protect-prompt-roots{exclude_flags} --egress "$EGRESS" \\\n'
        '  --setenv CODEX_HOME="$CODEX_HOME" --env-allow PATH --env-allow HOME \\\n'
        '  -- codex --sandbox danger-full-access "$@"\n'
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

    Returns:
        A one-item list with the runner, or ``[]``.
    """
    if not codex_sandbox_feature_enabled(manifest):
        return []
    plat = sys.platform if platform is None else platform
    if not (plat.startswith("linux") or plat.startswith("darwin")):
        return []
    return [(CODEX_RUNNER_REL, _build_codex_runner(manifest))]
