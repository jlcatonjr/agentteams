"""_linux_sandbox_emit.py — FRAMEWORK-NEUTRAL OS-confinement launcher emitter (Layer C/D).

Emits the provider-agnostic ``confine-run.sh`` launcher that wraps ANY agentic process — a
``claude``/``codex``/``copilot`` CLI, a ``goose run``, a python agent, an MCP server — in an
OS-enforced boundary, plus (on macOS) its deny-test control and two inert Tier-B examples. The name
is historical: the module began as the Linux emitter, and :func:`linux_sandbox_output_files` is
still the Linux path (``bwrap``), but as of 2026-W36 :func:`macos_sandbox_output_files` emits the
SAME launcher's macOS (``sandbox-exec``) branch + sidecars on darwin. The launcher itself is the one
cross-platform artifact: Linux confines with ``confine-run.sh --scratch DIR … -- <argv>`` (bwrap),
macOS with the same script's ``build_macos`` branch (``sandbox-exec -f profile <argv>``). The
goose-specific macOS Seatbelt config path is separate (``_goose_sandbox_emit.py``).

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

import posixpath
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

#: macOS sidecar assets shipped BESIDE the launcher on darwin, relative to ``templates/universal/``.
#: The deny test (``mac-escape-tests.sh``) is the gate that must pass UNNESTED before any macOS
#: boundary is called "confined" (wiring-verified != enforcement-verified). The two examples are
#: INERT Tier-B references (operator-provisioned uid + PF anchor) — never auto-run, never elevate.
#: Emitted ONLY on darwin (see :func:`macos_sandbox_output_files`).
_MACOS_DENYTEST_ASSET_REL = "sandbox/mac-escape-tests.sh"
_MACOS_TIER_B_EXAMPLE_ASSETS = (
    "sandbox/dedicated-uid-provisioning.example.sh",
    "sandbox/pf-per-tenant-anchor.example.conf",
)


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


def _read_sandbox_asset(asset_rel: str) -> str:
    """Return a shipped ``templates/universal/<asset_rel>`` file's text, or ``""`` when absent.

    Absent rather than raising: a source checkout missing an optional asset degrades to "not
    emitted" instead of breaking generation, exactly as ``_read_template_asset`` does for the
    constitutional hook. Shared by the Linux launcher emit and the macOS launcher+sidecar emit so
    both resolve and load assets IDENTICALLY — one loader, no gating/loader drift between platforms.

    Args:
        asset_rel: Asset path relative to ``templates/universal/`` (e.g. ``sandbox/confine-run.sh``).

    Returns:
        The asset's UTF-8 text, or ``""`` if it cannot be read.
    """
    asset = Path(__file__).resolve().parents[1] / "templates" / "universal" / asset_rel
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
    content = _read_sandbox_asset(_LAUNCHER_ASSET_REL)
    if not content:
        return []
    return [(rel_path, content)]


def macos_sandbox_output_files(
    manifest: dict[str, Any],
    rel_path: str = LINUX_SANDBOX_LAUNCHER_REL,
    *,
    platform: str | None = None,
) -> list[tuple[str, str]]:
    """Return the ``(rel_path, content)`` files for macOS OS-confinement, or ``[]``.

    Darwin mirror of :func:`linux_sandbox_output_files`, gated on the SAME framework-neutral
    confinement-request predicate (:func:`_sandbox_confinement_requested`). When confinement is
    requested AND the current platform is macOS, emit:

    * the SAME provider-agnostic launcher ``sandbox/confine-run.sh`` — its ``build_macos`` branch
      (sandbox-exec + a generated Seatbelt profile, RLIMIT_CPU/NPROC caps, loopback-proxy DNS
      contract, setuid denylist) is the real boundary on darwin;
    * ``sandbox/mac-escape-tests.sh`` — the on-host deny test that must pass, UNNESTED and with its
      positive controls, before any macOS boundary may be called "confined"
      (wiring-verified != enforcement-verified); and
    * two INERT Tier-B examples (``dedicated-uid-provisioning.example.sh``,
      ``pf-per-tenant-anchor.example.conf``) — reference-only, never auto-run, never elevate.

    Like Linux, the launcher is NOT auto-applied: the operator must wrap the agent invocation with
    it. That "nothing is confined until you wire it, and unverified until the deny test passes"
    state is surfaced by :func:`agentteams.host_features.privilege_profile_advisory` (its darwin
    manual-wire branch), never silently. Off darwin this returns ``[]`` (Linux is handled by
    :func:`linux_sandbox_output_files`; Windows/other have no emittable boundary).

    Args:
        manifest: The team manifest. ``privilege_profile`` and ``host_features`` gate emission.
        rel_path: Launcher emit path relative to the framework's agents output directory; the
            sidecars land in that same directory. Defaults to the 2-deep repo-root path; 1-deep
            adapters pass ``../sandbox/…``.
        platform: Override for the platform string (defaults to live ``sys.platform``); lets tests
            exercise the darwin / off-darwin branch deterministically.

    Returns:
        ``[(launcher_path, text), (denytest_path, text), (example_path, text)…]`` on macOS with a
        requested boundary and readable assets, else ``[]``. A missing sidecar asset is skipped
        (still emits the launcher); a missing launcher asset yields ``[]``.
    """
    plat = sys.platform if platform is None else platform
    if not plat.startswith("darwin"):
        return []
    if not _sandbox_confinement_requested(manifest):
        return []
    launcher = _read_sandbox_asset(_LAUNCHER_ASSET_REL)
    if not launcher:
        return []
    files: list[tuple[str, str]] = [(rel_path, launcher)]
    sidecar_dir = posixpath.dirname(rel_path)
    for asset_rel in (_MACOS_DENYTEST_ASSET_REL, *_MACOS_TIER_B_EXAMPLE_ASSETS):
        text = _read_sandbox_asset(asset_rel)
        if not text:
            continue
        files.append((posixpath.join(sidecar_dir, posixpath.basename(asset_rel)), text))
    return files


__all__ = [
    "linux_sandbox_output_files",
    "macos_sandbox_output_files",
    "LINUX_SANDBOX_LAUNCHER_REL",
    "LINUX_SANDBOX_LAUNCHER_PROJECT_PATH",
]
