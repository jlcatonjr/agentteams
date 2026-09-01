"""_linux_sandbox_emit.py — FRAMEWORK-NEUTRAL Linux OS-confinement emitter (Layer C/D).

Linux "works like Seatbelt": this emits a provider-agnostic ``bwrap`` launcher
(``confine-run.sh``) that wraps ANY agentic process — a ``claude``/``codex``/``copilot`` CLI, a
``goose run``, a python agent, an MCP server — in an OS-enforced boundary. It is the structural
Linux analogue of the macOS Seatbelt path (``_goose_sandbox_emit.py``): macOS confines with
``sandbox-exec -f profile <argv>``; Linux confines with ``confine-run.sh --scratch DIR … -- <argv>``.

Two operator corrections (2026-08-31) define this module's shape, and both are load-bearing:

1. **Linux works like Seatbelt** — emit an OS-confinement launcher, not just an advisory.
2. **No harness preference in agentteams** — the boundary is framework-NEUTRAL. ``bwrap`` wraps any
   process, so Linux enforceability does not depend on which framework is targeted. This emitter is
   therefore NOT goose-gated and does NOT write under ``.goose/``; it emits to a neutral repo-root
   path (``sandbox/confine-run.sh``). Any Goose *preference* lives ONLY in the consumer
   (baseAgent's augmented tier), never here.

The launcher content is shipped byte-for-byte as a template asset
(``templates/universal/sandbox/confine-run.sh``) — the same emission pattern the constitutional
hook uses. It is generic: ``--scratch`` / ``--exclude`` / ``--egress`` are supplied at RUN time, so
no manifest values are baked into the file. The reference implementation and source of truth is the
cross-orchestrator request
``2026-W36-agentteams-linux-agnostic-sandbox.md``; baseAgent keeps its own
``serve/deploy/confine-run.sh`` identical to what this module emits.

Honest enforcement status (kept in step with the launcher's own header):

* **Linux launcher: enforcement-VERIFIED** — baseAgent's deny test
  (``serve/deploy/layerc-escape-tests.sh`` gates [5][6]) proves write-outside-scratch,
  credential/sibling read, and raw egress are all denied for a real process (incl. ``goose``) on a
  live kernel (6/6).
* **macOS Seatbelt** (the sibling path) stays enforcement- and profile-syntax-UNVERIFIED off a mac.
* T6 + host-as-TCB stay bounded, never closed; seccomp/Landlock is a further layer not yet added.

This module is pure and stdlib-only. It must not import any framework adapter (avoids an import
cycle: adapters call INTO here from ``extra_output_files``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: Neutral repo-root-relative launcher path for a 2-deep agents dir (``.claude/agents``,
#: ``.github/agents``, ``.goose/recipes``). 1-deep adapters (codex/agents-md, ``.agents``) pass
#: their own ``rel_path`` (``../sandbox/confine-run.sh``) via ``sandbox_launcher_rel_path``.
LINUX_SANDBOX_LAUNCHER_REL = "../../sandbox/confine-run.sh"

#: The launcher's landing path relative to the generated PROJECT ROOT (for callers/tests that
#: reason about the emitted tree rather than the agents-dir-relative emit path).
LINUX_SANDBOX_LAUNCHER_PROJECT_PATH = "sandbox/confine-run.sh"

#: The shipped asset, relative to ``templates/universal/``.
_LAUNCHER_ASSET_REL = "sandbox/confine-run.sh"


def _sandbox_confinement_requested(manifest: dict[str, Any]) -> bool:
    """Return True iff workspace write-confinement is REQUESTED on this manifest.

    Framework-neutral request test (mirrors the goose/claude enablement checks but keys off no
    single framework's token): a ``confined``/``exclusive`` ``privilege_profile`` OR any
    ``*:sandbox`` host-feature token (``claude:sandbox``, ``goose:sandbox``, …) counts as a
    request. This is the REQUEST gate only — platform enforceability is the separate ``linux``
    guard in :func:`linux_sandbox_output_files`.
    """
    if manifest.get("privilege_profile") in {"confined", "exclusive"}:
        return True
    return any(
        isinstance(t, str) and t.endswith(":sandbox")
        for t in (manifest.get("host_features") or [])
    )


def _read_launcher_asset() -> str:
    """Return the shipped ``confine-run.sh`` launcher text, or ``""`` when the asset is absent.

    Absent rather than raising: a source checkout missing the optional asset degrades to "no
    launcher emitted" instead of breaking generation, exactly as ``_read_template_asset`` does for
    the constitutional hook. The asset's presence is covered by the Linux-emission test.
    """
    asset = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "universal"
        / _LAUNCHER_ASSET_REL
    )
    try:
        return asset.read_text(encoding="utf-8")
    except OSError:
        return ""


def linux_sandbox_output_files(
    manifest: dict[str, Any],
    rel_path: str = LINUX_SANDBOX_LAUNCHER_REL,
    *,
    platform: str | None = None,
) -> list[tuple[str, str]]:
    """Return the ``(rel_path, content)`` files for Linux OS-confinement, or ``[]``.

    Emits the provider-agnostic ``confine-run.sh`` launcher when, and only when, BOTH hold:

    * confinement is REQUESTED for this team (:func:`_sandbox_confinement_requested`), and
    * the current platform is Linux (the emitted ``bwrap`` launcher is a Linux boundary).

    It is framework-neutral by construction: no framework id is consulted, so a confined
    ``codex``/``copilot``/``claude``/``goose`` team all emit the same launcher. Off Linux this
    returns ``[]`` (the macOS Seatbelt path handles darwin; Windows/other have no emittable
    boundary and the CLI has already surfaced the ``privilege_profile_advisory`` there) — never a
    silent, non-enforcing artifact.

    Args:
        manifest: The team manifest. ``privilege_profile`` and ``host_features`` gate emission.
        rel_path: Emit path relative to the framework's agents output directory. Defaults to the
            2-deep repo-root path; 1-deep adapters (codex/agents-md) pass ``../sandbox/…``. The
            target is always repo-root ``sandbox/confine-run.sh`` — deliberately NOT under
            ``.goose/`` (operator correction #2).
        platform: Override for the platform string (defaults to live ``sys.platform``); lets tests
            exercise the linux / off-linux branch deterministically.

    Returns:
        ``[(rel_path, launcher_text)]`` when a Linux boundary is emitted, else ``[]``.
    """
    plat = sys.platform if platform is None else platform
    if not plat.startswith("linux"):
        return []
    if not _sandbox_confinement_requested(manifest):
        return []
    content = _read_launcher_asset()
    if not content:
        return []
    return [(rel_path, content)]


__all__ = [
    "linux_sandbox_output_files",
    "LINUX_SANDBOX_LAUNCHER_REL",
    "LINUX_SANDBOX_LAUNCHER_PROJECT_PATH",
]
