"""macOS Seatbelt confinement emission for the Goose framework (P1-1).

The Goose analog of ``_sandbox_emit.py`` (the Claude sandbox emitter). Goose does not
confine itself — its ``GOOSE_MODE`` permission modes are in-process approval gates, not an
OS boundary, and it runs with full user privileges (its own ``SECURITY.md`` recommends a
VM/container). On **macOS** Goose can be confined by Apple Seatbelt (``sandbox-exec`` + a
``sandbox.sb`` profile + ``GOOSE_SANDBOX``); there is **no native Linux/Windows Goose OS
sandbox**, so THIS module emits **only on macOS**. That does NOT mean Linux goose is
unconfined: on **Linux** the framework-neutral bwrap launcher ``sandbox/confine-run.sh``
(``_linux_sandbox_emit.py``, emitted for any framework) is the boundary, surfaced via the
non-fatal manual-wire advisory; only **Windows** has no emittable boundary and degrades to
the fatal ``privilege_profile_advisory`` (see ``host_features.is_sandbox_capable`` /
``privilege_profile_advisory``).

Design mirrors the Claude path, honestly and fail-closed:

* Emit a ``sandbox.sb`` Seatbelt profile that ``deny file-write*`` outside the workspace
  write roots (and re-``deny file-write*`` the control-plane files so the agent cannot edit
  its own gate/switch/profile); for ``exclusive`` it additionally ``deny file-read*`` of the
  default protected-read set plus any operator-supplied sibling scratch roots.
* Network posture is EXCLUSIVE-only (operator decision, 2026-W39). The DEFAULT ``confined``
  profile leaves egress OPEN — matching Claude's confined block, so a default-on goose team
  can reach its LLM out of the box; ``confined`` is write-confinement only. ``exclusive``
  emits ``deny network*`` (Seatbelt file-denies do not cover sockets) and re-allows ONE
  sanctioned LOOPBACK endpoint when ``goose_egress_proxy`` is set (Seatbelt ``remote ip``
  accepts only ``localhost``/``*``); absent that, exclusive is fully network-ISOLATED.
* Ship an INERT example (``config.yaml.agentteams.example``) carrying ``GOOSE_SANDBOX`` +
  the profile path — the operator merges it into their live ``~/.config/goose/config.yaml``.
  We NEVER write the operator's live config (mirror the Claude "ship an example, never
  write settings.json" convention).

This module is pure and stdlib-only. It must not import the ``GooseAdapter`` or any other
``goose.py`` internal (``goose.py`` calls INTO here from ``extra_output_files``). It reuses
``_sandbox_emit._DEFAULT_PROTECTED_READ_PATHS`` so the protected-read set stays a single
source of truth shared with the Claude path.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from agentteams.host_features import is_sandbox_capable
from agentteams.frameworks._sandbox_emit import _DEFAULT_PROTECTED_READ_PATHS

#: Output paths (relative to the Goose agents dir ``.goose/recipes/``) the emitter writes.
#: ``../`` lands them in ``.goose/`` alongside ``recipes/`` — inert artifacts, never the
#: operator's live ``~/.config/goose/config.yaml``.
GOOSE_SANDBOX_PROFILE_REL = "../sandbox.sb"
GOOSE_CONFIG_EXAMPLE_REL = "../config.yaml.agentteams.example"

#: The project-relative location the emitted profile lands at (what the config example and
#: the wiring verifier reference).
GOOSE_SANDBOX_PROFILE_PROJECT_PATH = ".goose/sandbox.sb"


def _goose_sandbox_feature_enabled(manifest: dict[str, Any]) -> bool:
    """Return True iff workspace write-confinement is REQUESTED on this goose manifest.

    Reads both sources of truth (mirrors ``_sandbox_emit._sandbox_feature_enabled``): the
    ``goose:sandbox`` host-feature token (set from ``--target-host-features`` and from the
    privilege_profile expansion) and the ``privilege_profile`` field itself (so a confined
    manifest emits even on the ``convert``/``render`` paths that do not run the
    profile→host_features union). This is the REQUEST test — platform enforceability is a
    separate gate applied in :func:`goose_sandbox_output_files`.
    """
    if "goose:sandbox" in (manifest.get("host_features") or []):
        return True
    return manifest.get("privilege_profile") in {"confined", "exclusive"}


def _goose_read_deny_paths(manifest: dict[str, Any]) -> list[str] | None:
    """Return the read-exclusion deny list for an ``exclusive`` goose team, else None.

    Mirrors ``_sandbox_emit._exclusive_read_deny_paths``: the curated credential-path
    defaults plus any operator-supplied ``protected_read_paths`` (sibling agent scratch
    roots / sibling workspaces), de-duplicated. Only ``exclusive`` carries read-exclusion;
    ``confined`` returns None so the emitted profile stays write-confinement only.
    """
    if manifest.get("privilege_profile") != "exclusive":
        return None
    deny: list[str] = list(_DEFAULT_PROTECTED_READ_PATHS)
    for extra in manifest.get("protected_read_paths") or []:
        if extra and extra not in deny:
            deny.append(extra)
    return deny


def _seatbelt_path_expr(path: str) -> str | None:
    """Return a Seatbelt ``(subpath ...)`` s-expression for ``path``, or None if unusable.

    Paths are resolved against launch-time parameters rather than baked to absolute paths,
    so the profile ports between machines while staying exact (no ``~``-expansion silent
    no-op risk — the failure mode the Claude read-exclusion comments warn about):

    * absolute (``/x``)      → ``(subpath "/x")``
    * home-relative (``~/x``)→ ``(subpath (string-append (param "HOME_DIR") "/x"))``
    * workspace-relative (``x``, ``./x``) →
      ``(subpath (string-append (param "WORKSPACE_ROOT") "/x"))``

    ``HOME_DIR`` and ``WORKSPACE_ROOT`` are supplied at launch (``sandbox-exec -D``). A path
    is rejected (returns None) rather than emitted as a porous or malformed rule — fail
    closed. Rejected inputs (audit 2026-W39, hygiene RANK2 / security F5):

    * empty, or containing a double-quote (would break/escape the string literal);
    * any control character — newline / CR / TAB / other C0 — which would split the
      s-expression across lines and could inject an unintended rule;
    * a backslash (Seatbelt string-literal escape ambiguity);
    * the filesystem root ``/`` or ``//`` (``(subpath "/")`` re-allows the WHOLE filesystem,
      fully defeating write-confinement);
    * any ``..`` path segment — Seatbelt ``subpath`` does NOT normalize ``..``, so
      ``WORKSPACE_ROOT/../../etc`` would escape the workspace root.
    """
    if not path or '"' in path or "\\" in path:
        return None
    if any(ord(ch) < 0x20 for ch in path):  # newline/CR/TAB/other control chars
        return None
    # Reject `..` traversal segments (Seatbelt does not normalize subpath).
    if ".." in path.split("/"):
        return None
    if path.startswith("/"):
        # `/` or `//...` collapsing to root would re-allow the entire filesystem.
        if path.rstrip("/") == "":
            return None
        return f'(subpath "{path}")'
    if path.startswith("~/"):
        rest = path[2:]
        return f'(subpath (string-append (param "HOME_DIR") "/{rest}"))'
    rest = path[2:] if path.startswith("./") else path
    if rest in ("", "."):
        return '(subpath (param "WORKSPACE_ROOT"))'
    return f'(subpath (string-append (param "WORKSPACE_ROOT") "/{rest}"))'


def _seatbelt_egress_rule(endpoint: str | None) -> str | None:
    """Validate a sanctioned-egress endpoint for a Seatbelt ``remote ip`` rule.

    Apple Seatbelt's ``(remote ip "...")`` accepts ONLY ``localhost`` or ``*`` as the host;
    an IP or DNS literal makes ``sandbox-exec`` refuse to load the ENTIRE profile (audit
    2026-W39, technical-validator finding — a confined goose team with an IP proxy produced
    an unloadable profile). So a sanctioned egress must be a loopback proxy.

    Args:
        endpoint: The configured ``goose_egress_proxy`` (``host[:port]``), or None.

    Returns:
        The endpoint string when it is a valid loopback/wildcard form
        (``localhost``, ``localhost:PORT``, ``*``, ``*:PORT``); otherwise None (the caller
        keeps deny-all and discloses the rejection). Never returns an unloadable rule.
    """
    if not endpoint or '"' in endpoint or "\\" in endpoint:
        return None
    if any(ord(ch) < 0x20 for ch in endpoint):
        return None
    host, sep, port = endpoint.partition(":")
    if host not in ("localhost", "*"):
        return None
    if sep and not port.isdigit():
        return None
    return endpoint


def _build_seatbelt_profile(
    write_roots: list[str] | None,
    deny_read: list[str] | None = None,
    egress_endpoint: str | None = None,
    deny_network: bool = False,
) -> str:
    """Build the ``sandbox.sb`` Apple-Seatbelt profile text for goose confinement.

    Seatbelt semantics: last matching rule wins. We ``(allow default)``, then ``(deny
    file-write*)`` and re-allow only the workspace roots (writes outside are kernel-denied),
    then re-``(deny file-write*)`` the control-plane files (parity with the Claude
    ``denyWrite`` — a confined agent must not edit its own gate/switch), then — only when
    ``deny_network`` (the ``exclusive`` profile) — ``(deny network*)`` re-allowing a
    sanctioned loopback proxy, and ``(deny file-read*)`` of the protected set.

    Network posture (operator decision 2026-W39): the DEFAULT ``confined`` profile does NOT
    restrict network (``deny_network=False``) — egress stays open, matching Claude's confined
    block, so a default-on goose team can reach its LLM out of the box. Network isolation is
    an ``exclusive``-only property (``deny_network=True``), where the only sanctioned egress
    is a LOOPBACK proxy (Seatbelt ``remote ip`` accepts only ``localhost``/``*``).

    Args:
        write_roots: Workspace roots the agent may write to (default ``["."]`` → the whole
            project tree, resolved via the ``WORKSPACE_ROOT`` launch parameter).
        deny_read: Read-exclusion paths (``exclusive`` only); None/empty emits no read deny.
        egress_endpoint: A single loopback ``host:port`` the network deny re-allows (the
            sanctioned egress proxy); only meaningful when ``deny_network``. A non-loopback
            value is rejected (kept deny-all) rather than emitted as an unloadable rule.
        deny_network: When True (``exclusive``), deny all network except a sanctioned
            loopback proxy. When False (``confined``, the default), leave egress open and
            disclose that in the profile.

    Returns:
        The profile text (ends with a trailing newline).

    Raises:
        ValueError: a non-empty ``write_roots`` or ``deny_read`` entry cannot be expressed
            as a safe Seatbelt rule (fail closed, never silently drop it).
    """
    from agentteams.frameworks._sandbox_emit import _PROTECTED_WRITE_PATHS
    roots = list(write_roots) if write_roots else ["."]
    # Fail CLOSED on any unrepresentable root rather than silently dropping it: a dropped
    # write root would leave the agent unable to write where the operator intended (and a
    # root like "/" or "../x" that _seatbelt_path_expr now rejects must surface as an error,
    # not vanish). (audit 2026-W39, security F5.)
    rejected_roots = [r for r in roots if _seatbelt_path_expr(r) is None]
    if rejected_roots:
        raise ValueError(
            "goose sandbox write_roots contains path(s) that cannot be expressed as a safe "
            f"Seatbelt rule (root '/', '..' traversal, quote, backslash, or control char): "
            f"{rejected_roots!r}. Fix the workspace_write_roots in the brief."
        )
    write_exprs = [e for e in (_seatbelt_path_expr(r) for r in roots) if e]
    if not write_exprs:
        # Never emit a profile with NO writable root: that would deny every write including
        # the workspace and break the harness while LOOKING confined. Fall back to the
        # workspace root explicitly (fail safe toward the documented default, not open).
        write_exprs = ['(subpath (param "WORKSPACE_ROOT"))']

    lines: list[str] = [
        "(version 1)",
        "",
        ";; ===================================================================",
        ";; agentteams-emitted Goose confinement profile (P1-1). INERT until you",
        ";; wire it in (see config.yaml.agentteams.example). Enforced by the macOS",
        ";; kernel via Apple Seatbelt (sandbox-exec) ONLY — there is NO Linux/Windows",
        ";; equivalent. sandbox-exec / Seatbelt is Apple-DEPRECATED; for untrusted code",
        ";; the portable primary is a container / WASI at the consumer layer. VERIFY on",
        ";; YOUR machine and Goose build (see `agentteams ... --check-wiring`); this file",
        ";; does not by itself prove your Goose build honors it.",
        ";;",
        ";; Ground-truth launch (ALWAYS enforces on macOS, independent of GOOSE_SANDBOX",
        ";; build variance):",
        ";;   sandbox-exec -D WORKSPACE_ROOT=\"$PWD\" -D HOME_DIR=\"$HOME\" \\",
        ";;                -f .goose/sandbox.sb goose run --recipe <recipe> ...",
        ";; ===================================================================",
        "",
        "(allow default)",
        "",
        ";; --- Workspace write-confinement (deny writes outside the workspace roots) ---",
        "(deny file-write*)",
    ]
    lines.append("(allow file-write*")
    for expr in write_exprs:
        lines.append(f"    {expr}")
    lines.append(")")
    lines += [
        ";; Ephemeral runtime scratch Goose needs to function (NOT protected zones):",
        "(allow file-write*",
        '    (subpath "/private/tmp")',
        '    (subpath "/private/var/folders")',
        '    (literal "/dev/null")',
        '    (literal "/dev/stdout")',
        '    (literal "/dev/stderr"))',
    ]
    # Control-plane deny-write (parity with the Claude denyWrite / _PROTECTED_WRITE_PATHS —
    # audit 2026-W39 hygiene RANK3): a confined agent must not edit its own enforcement
    # switch, gate hook, or its OWN Seatbelt profile. Emitted AFTER the workspace allow so
    # last-match-wins denies these even though they sit inside the writable workspace.
    control_plane = [*_PROTECTED_WRITE_PATHS, ".goose/sandbox.sb"]
    cp_exprs = [e for e in (_seatbelt_path_expr(p) for p in control_plane) if e]
    lines += [
        ";; --- Control-plane protection (agent may not rewrite its own enforcement) ---",
        "(deny file-write*",
        *[f"    {e}" for e in cp_exprs],
        ")",
        "",
        ";; --- Network egress ---",
    ]
    if deny_network:
        lines += [
            ";; privilege_profile: exclusive — network ISOLATED. Seatbelt FILE denies do NOT",
            ";; restrict sockets, so deny all network explicitly (never silently open).",
            "(deny network*)",
        ]
        egress_expr = _seatbelt_egress_rule(egress_endpoint)
        if egress_expr is not None:
            lines += [
                ";; [egress-proxy] Re-allow ONLY the one sanctioned LOOPBACK endpoint. Seatbelt's",
                ";; `remote ip` accepts only `localhost`/`*` (an IP/DNS literal makes sandbox-exec",
                ";; REFUSE TO LOAD THE WHOLE PROFILE — audit 2026-W39, TV finding), so a sanctioned",
                ";; egress must be a LOOPBACK proxy (e.g. the goose route-proxy on localhost:PORT).",
                ";; HONEST RESIDUAL: this endpoint is bidirectional — agentteams cannot close",
                ";; exfiltration THROUGH it; bound it with the proxy's own content/rate controls.",
                f'(allow network* (remote ip "{egress_expr}"))',
            ]
        else:
            lines += [
                ";; No sanctioned egress proxy (goose_egress_proxy unset, or a non-loopback value",
                ";; Seatbelt cannot express) — fully NETWORK-ISOLATED. NOTE: a goose agent needs",
                ";; egress to reach its LLM; an exclusive goose team therefore requires a LOOPBACK",
                ";; proxy. Set goose_egress_proxy to `localhost:PORT` (or `*`) and regenerate, or",
                ";; add:  (allow network* (remote ip \"localhost:PORT\"))",
            ]
    else:
        lines += [
            ";; privilege_profile: confined — network is NOT restricted (egress OPEN), matching",
            ";; the Claude confined block, so a default-on goose team can reach its LLM out of",
            ";; the box. This is write-confinement only. HONEST RESIDUAL: a confined agent can",
            ";; still make arbitrary network connections (exfiltration is NOT closed here). For",
            ";; network isolation, use privilege_profile: exclusive (adds deny network* + a",
            ";; loopback egress-proxy allow) and read-exclusion.",
        ]

    if deny_read:
        # Fail CLOSED: an unrepresentable read-exclusion path must NOT be silently dropped
        # while the profile still advertises exclusive read-exclusion (audit 2026-W39,
        # security F5). Surface it as an error so the operator fixes protected_read_paths.
        rejected_reads = [p for p in deny_read if _seatbelt_path_expr(p) is None]
        if rejected_reads:
            raise ValueError(
                "goose sandbox protected_read_paths contains path(s) that cannot be expressed "
                f"as a safe Seatbelt read-deny (quote, backslash, control char, '..', or bare "
                f"'/'): {rejected_reads!r}. Fix protected_read_paths in the brief."
            )
        read_exprs = [e for e in (_seatbelt_path_expr(p) for p in deny_read) if e]
        if read_exprs:
            lines += [
                "",
                ";; --- Read-exclusion (privilege_profile: exclusive) ---",
                ";; OUTBOUND hardening: THIS team (and its subprocesses) cannot READ these",
                ";; paths (credentials + sibling agent scratch roots). It does NOT stop OTHER",
                ";; teams reading THIS workspace (that inbound property is operator OS",
                ";; filesystem hardening, which agentteams advises but does not enforce). It",
                ";; denies FILES, not env vars: a secret already exported into the agent's",
                ";; environment is not covered by a filesystem read-deny.",
                "(deny file-read*",
            ]
            for expr in read_exprs:
                lines.append(f"    {expr}")
            lines.append(")")

    lines.append("")
    return "\n".join(lines) + "\n"


def _build_config_example(
    write_roots: list[str] | None,
    exclusive: bool,
    egress_endpoint: str | None = None,
) -> str:
    """Build the INERT ``config.yaml.agentteams.example`` (Goose GOOSE_SANDBOX wiring).

    This is a SHIPPED EXAMPLE, never the operator's live config: merge the ``GOOSE_SANDBOX``
    setting into your own ``~/.config/goose/config.yaml`` (agentteams never writes it, so
    your customizations are never clobbered). It documents the two enforcement paths and the
    Goose-build-variance / Apple-deprecation caveats honestly.
    """
    roots = list(write_roots) if write_roots else ["."]
    # Network posture is EXCLUSIVE-only (C1, 2026-W39): confined leaves egress OPEN.
    if not exclusive:
        proxy_note = (
            "#   Network: OPEN (confined = write-confinement only; egress is NOT restricted,\n"
            "#   matching Claude confined, so goose can reach its LLM). Use privilege_profile\n"
            "#   exclusive for network isolation.\n"
        )
    elif egress_endpoint:
        proxy_note = (
            f"#   Network: ISOLATED (exclusive); sanctioned loopback egress proxy: {egress_endpoint}\n"
        )
    else:
        proxy_note = (
            "#   Network: ISOLATED (exclusive) — deny-all; no egress proxy configured. Set a\n"
            "#   loopback goose_egress_proxy (localhost:PORT) so goose can reach its LLM.\n"
        )
    profile = "confined + exclusive (read-exclusion + network isolation)" if exclusive else "confined"
    return (
        "# ===================================================================\n"
        "# agentteams-emitted Goose confinement example (P1-1) — INERT / EXAMPLE ONLY.\n"
        "#\n"
        "# DO NOT point Goose at this file directly and do NOT let it clobber your live\n"
        "# ~/.config/goose/config.yaml. Copy the GOOSE_SANDBOX line below into your OWN\n"
        "# config, or export it in the environment. agentteams never writes your live\n"
        "# config (ship-an-example, never-clobber convention).\n"
        "#\n"
        f"# Profile requested: {profile}\n"
        f"# Workspace write roots: {roots}\n"
        f"{proxy_note}"
        "#\n"
        "# ENFORCEMENT (macOS only — Goose has no native OS sandbox on Linux/Windows):\n"
        "#\n"
        "#  A) Ground-truth (ALWAYS enforces on macOS, independent of your Goose build):\n"
        '#       sandbox-exec -D WORKSPACE_ROOT=\"$PWD\" -D HOME_DIR=\"$HOME\" \\\n'
        "#                    -f .goose/sandbox.sb goose run --recipe <recipe> ...\n"
        "#\n"
        "#  B) Goose-native (convenience; semantics VARY by Goose build/version — Desktop\n"
        "#     vs CLI differ, and older builds may ignore it). Set:\n"
        "#       GOOSE_SANDBOX=1\n"
        "#     and point your Goose build at the emitted profile (its exact key differs by\n"
        "#     build — consult `goose --version` and the Goose docs). Because this is\n"
        "#     build-variant, agentteams does NOT claim path B enforces anything until you\n"
        "#     confirm it with `--check-wiring` plus the manual test below. Prefer path A\n"
        "#     when in doubt (fail closed, do not assume Desktop behavior).\n"
        "#\n"
        "# VERIFY (must actually deny — a config that LOOKS protective but no-ops is the\n"
        "# failure this feature exists to prevent):\n"
        "#   1) run `agentteams generate ... --check-wiring` (checks GOOSE_SANDBOX live +\n"
        "#      the profile exists + write roots match).\n"
        "#   2) from inside the sandbox, a write outside the workspace MUST be denied"
        + (
            ", and a\n#      raw non-proxied network egress MUST be denied (exclusive). If either succeeds,\n"
            "#      the boundary is NOT in effect on your build — use path A.\n"
            if exclusive else
            ". (Egress\n#      is OPEN under confined — do NOT expect network to be denied.) If the write\n"
            "#      succeeds, the boundary is NOT in effect on your build — use path A.\n"
        )
        + "#\n"
        "# CAVEAT: sandbox-exec / Seatbelt is Apple-deprecated (App Sandbox preferred); the\n"
        "# portable primary for untrusted code remains a container / WASI at the consumer\n"
        "# layer. This profile is the best OS boundary agentteams can emit for Goose today.\n"
        "# ===================================================================\n"
        "\n"
        "# --- inert example snippet (merge into your live config / environment) ---\n"
        "GOOSE_SANDBOX: 1\n"
        "# agentteams_sandbox_profile: <ABSOLUTE_PATH_TO_PROJECT>/.goose/sandbox.sb\n"
    )


def _detect_goose_sandbox_support() -> tuple[bool, str]:
    """Best-effort detection of whether OS-enforced goose confinement is possible HERE.

    Used by the wiring verifier (not the emitter, which only ships an inert example). Checks
    the platform and the presence of ``sandbox-exec`` — the ground-truth enforcement path
    that does not depend on Goose-build ``GOOSE_SANDBOX`` semantics. Goose's own
    ``GOOSE_SANDBOX`` support varies by build, so we DO NOT claim goose-native enforcement we
    cannot confirm; the verifier reports what it can actually establish.

    Returns:
        ``(supported, detail)`` — ``supported`` is True only on macOS with a usable
        ``sandbox-exec``. ``detail`` names the Goose build when discoverable.
    """
    if not is_sandbox_capable("goose"):
        return False, "not macOS — Goose has no native OS sandbox on this platform"
    if shutil.which("sandbox-exec") is None:
        return False, "macOS, but sandbox-exec was not found on PATH"
    build = "unknown Goose build"
    goose_bin = shutil.which("goose")
    if goose_bin is not None:
        try:
            out = subprocess.run(
                [goose_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ver = (out.stdout or out.stderr or "").strip().splitlines()
            build = ver[0].strip() if ver else "Goose present (version unreported)"
        except (OSError, subprocess.SubprocessError) as exc:
            # Do NOT swallow: record the reason so the verifier reports it honestly rather
            # than claiming a build it could not read.
            build = f"Goose present but `--version` failed: {exc}"
    else:
        build = "goose not found on PATH (profile still enforceable via sandbox-exec path A)"
    return True, f"macOS + sandbox-exec available; {build}"


def goose_sandbox_output_files(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the (rel_path, content) files for goose confinement, or [] when not applicable.

    Emits the macOS Seatbelt profile ONLY, and only when confinement is REQUESTED
    (:func:`_goose_sandbox_feature_enabled`) AND the platform is macOS (the explicit ``darwin``
    guard below). Off macOS this returns ``[]`` — but that is NOT "no boundary": on **Linux** the
    boundary is the framework-neutral bwrap launcher emitted by ``base.extra_output_files`` /
    ``_linux_sandbox_emit`` (surfaced via the non-fatal manual-wire advisory); only **Windows** has
    no emittable boundary and degrades to the fatal ``privilege_profile_advisory``. This function
    never ships a Seatbelt profile that cannot run — never a silent, non-enforcing artifact.
    """
    if not _goose_sandbox_feature_enabled(manifest):
        return []
    # Explicit darwin guard: the macOS Seatbelt path emits ONLY on macOS. Since Linux is now
    # framework-neutrally sandbox-capable (is_sandbox_capable(_, "linux") -> True), guarding on
    # is_sandbox_capable("goose") alone is no longer sufficient to keep Seatbelt macOS-only — on a
    # Linux host the neutral bwrap launcher (_linux_sandbox_emit) is the boundary, never Seatbelt.
    if sys.platform != "darwin":
        return []
    if not is_sandbox_capable("goose"):
        return []
    write_roots = manifest.get("workspace_write_roots") or ["."]
    deny_read = _goose_read_deny_paths(manifest)
    egress_endpoint = manifest.get("goose_egress_proxy") or None
    # Network isolation is an EXCLUSIVE-only property (operator decision 2026-W39): the
    # default confined profile leaves egress open so a goose team can reach its LLM.
    deny_network = manifest.get("privilege_profile") == "exclusive"
    profile_text = _build_seatbelt_profile(
        write_roots, deny_read, egress_endpoint, deny_network=deny_network
    )
    config_text = _build_config_example(
        write_roots, exclusive=deny_read is not None, egress_endpoint=egress_endpoint
    )
    return [
        (GOOSE_SANDBOX_PROFILE_REL, profile_text),
        (GOOSE_CONFIG_EXAMPLE_REL, config_text),
    ]


#: Truthy YAML/env values for GOOSE_SANDBOX in a live Goose config.
_GOOSE_SANDBOX_TRUTHY = frozenset({"1", "true", "yes", "on"})
#: Matches ``GOOSE_SANDBOX: 1`` / ``GOOSE_SANDBOX = true`` / ``export GOOSE_SANDBOX=on``.
_GOOSE_SANDBOX_LINE = re.compile(
    r"(?im)^\s*(?:export\s+)?GOOSE_SANDBOX\s*[:=]\s*[\"']?([A-Za-z0-9]+)[\"']?\s*$"
)


def _live_goose_sandbox_enabled(config_path: Path) -> bool | None:
    """Return whether GOOSE_SANDBOX is enabled in the operator's LIVE Goose config.

    Output-only, secret-safe: reads the live config but NEVER echoes its content (it may
    hold API keys) — the caller reports a boolean, mirroring the Claude verifier's
    discipline. Returns None when the file is absent or unreadable (cannot confirm).
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _GOOSE_SANDBOX_LINE.search(text)
    if match is None:
        return False
    return match.group(1).strip().lower() in _GOOSE_SANDBOX_TRUTHY


def verify_goose_sandbox_wiring(
    project_root: Path,
    manifest: dict[str, Any] | None = None,
    *,
    live_config_path: Path | None = None,
) -> tuple[bool, list[str]]:
    """Verify an emitted goose Seatbelt boundary is actually wired live (goose analog of P1-3).

    Parallels ``claude.verify_sandbox_wiring``: the emitted ``.goose/sandbox.sb`` +
    ``config.yaml.agentteams.example`` are INERT until the operator enables ``GOOSE_SANDBOX``
    in their live ``~/.config/goose/config.yaml`` (or launches via ``sandbox-exec``). This
    read-only, output-only check closes the silent "looks confined, enforces nothing" gap.
    It never echoes live-config content (secret-safe) and never claims enforcement it cannot
    establish (Goose-build variance / non-macOS).

    Returns ``(ok, messages)``:

    * **Linux** — verifies the framework-neutral bwrap launcher ``sandbox/confine-run.sh``, NOT
      Seatbelt: ``ok=True`` with an ``ENFORCEABLE`` notice when the launcher is present (it still
      must be WRAPPED around the invocation to take effect), or ``ok=False`` with a regenerate
      warning when a confined team is missing it.
    * **Windows / other** — exit-NEUTRAL (``ok=True``) with an explicit "NOT ENFORCEABLE HERE"
      notice (no emittable boundary there); never a misleading clean pass. Points at outside-in
      confinement.
    * **macOS, confinement requested but the boundary is missing/unmerged** — ``ok=False``.
    * **macOS, wired correctly** — ``ok=True`` with an OK line plus the honest build-variance
      reminder to run the manual deny test.
    * **no confinement requested** — ``ok=True`` (nothing to verify).
    """
    requested = manifest is not None and _goose_sandbox_feature_enabled(manifest)
    plat = sys.platform

    # Linux: enforcement is the FRAMEWORK-NEUTRAL bwrap launcher (repo-root
    # ``sandbox/confine-run.sh``, emitted by ``_linux_sandbox_emit``), NOT Seatbelt. Verify the
    # launcher, never ``.goose/sandbox.sb`` (which is macOS-only). Linux is now enforceable, so
    # this is no longer the exit-neutral "NOT ENFORCEABLE HERE" case.
    if plat.startswith("linux"):
        if not requested:
            return True, [
                f"no goose confinement was requested for this team ({plat}) — nothing to verify.",
            ]
        launcher = project_root / "sandbox" / "confine-run.sh"
        if launcher.is_file():
            return True, [
                f"ENFORCEABLE ({plat}) via the framework-neutral bwrap launcher emitted at "
                "sandbox/confine-run.sh (read-only root + rootless netns + NoNewPrivs + "
                "credential read-exclusion). It must WRAP the goose invocation — "
                "`sandbox/confine-run.sh --scratch DIR … -- goose run …` — to take effect; the "
                "launcher itself is inert until used. Run its deny test to confirm on this "
                "kernel. T6 + host-as-TCB remain bounded, never closed.",
            ]
        return False, [
            f"WARNING: confinement was requested for this goose team on {plat} but the "
            "framework-neutral launcher sandbox/confine-run.sh was not emitted — regenerate "
            "the project.",
        ]

    # Windows / other: no emittable OS boundary — exit-neutral, honest, never a clean pass.
    if plat != "darwin":
        if requested:
            return True, [
                f"NOT ENFORCEABLE HERE ({plat}): a confined/exclusive goose team was "
                "requested, but there is NO emittable OS boundary on this platform and "
                "agentteams emitted none. This is NOT a clean pass — nothing is enforced. "
                "Confine from OUTSIDE the process: a container plus seccomp-bpf + Landlock "
                "and egress filtering.",
            ]
        return True, [
            f"no OS-enforced goose confinement is possible on this platform ({plat}); no "
            "confinement was requested — nothing to verify.",
        ]

    profile = project_root / ".goose" / "sandbox.sb"
    if not profile.is_file():
        if requested:
            return False, [
                "WARNING: confinement was requested for this goose team but no "
                ".goose/sandbox.sb profile was emitted — regenerate the project.",
            ]
        return True, [
            "no goose confinement was requested for this team (cooperative) — nothing to verify."
        ]

    try:
        profile_text = profile.read_text(encoding="utf-8")
    except OSError:
        return False, [
            f"WARNING: {profile} exists but is unreadable — cannot confirm the boundary.",
        ]

    msgs: list[str] = []
    ok = True

    # (d) escape-hatch / porousness: the required denies MUST be present. Network isolation is
    # an EXCLUSIVE-only property (C1, 2026-W39) — confined leaves egress OPEN, so requiring
    # `(deny network*)` for a confined team would wrongly report a healthy profile as broken.
    # Determine exclusive from the manifest, else infer from the read-exclusion marker.
    is_exclusive = (
        (manifest or {}).get("privilege_profile") == "exclusive"
        if manifest is not None else "(deny file-read*" in profile_text
    )
    required = [("(deny file-write*)", "workspace write-confinement")]
    if is_exclusive:
        required.append(
            ("(deny network*)", "network isolation (exclusive; Seatbelt file-denies do not cover sockets)")
        )
    for token, why in required:
        if token not in profile_text:
            ok = False
            msgs.append(f"WARNING: emitted sandbox.sb is missing `{token}` — {why} is NOT in force.")

    # (c) write roots match the manifest expectation.
    if manifest is not None:
        expected_roots = manifest.get("workspace_write_roots") or ["."]
        for root in expected_roots:
            expr = _seatbelt_path_expr(root)
            if expr and expr not in profile_text:
                ok = False
                msgs.append(
                    "WARNING: the emitted profile's write roots differ from the manifest "
                    f"expectation (missing write-allow for {root!r}) — regenerate."
                )

    # (a) GOOSE_SANDBOX enabled in the operator's LIVE config (the unmerged-boundary case).
    cfg = live_config_path or (Path.home() / ".config" / "goose" / "config.yaml")
    live = _live_goose_sandbox_enabled(cfg)
    if live is None:
        ok = False
        msgs.append(
            f"WARNING: unmerged boundary — {cfg} is absent/unreadable, so GOOSE_SANDBOX is "
            "not enabled live. Merge the emitted config.yaml.agentteams.example (or launch "
            "goose under `sandbox-exec -f .goose/sandbox.sb`)."
        )
    elif live is False:
        ok = False
        msgs.append(
            "WARNING: unmerged boundary — GOOSE_SANDBOX is not enabled in your live goose "
            "config. Merge the emitted example, or use the sandbox-exec launch path."
        )

    # Build detection: report what we can actually establish; never claim goose-native
    # enforcement we cannot confirm (build variance).
    supported, detail = _detect_goose_sandbox_support()
    msgs.append(f"Goose build detection: {detail}")
    if not supported:
        ok = False
        msgs.append(
            "WARNING: OS enforcement could not be confirmed on this host (see above) — do "
            "NOT treat the emitted profile as active until the manual deny test passes."
        )

    if ok:
        denies = "write + network denies" if is_exclusive else "write-confinement (network open — confined)"
        manual = (
            "a write outside the workspace and a raw egress MUST both be denied"
            if is_exclusive else "a write outside the workspace MUST be denied (egress is open under confined)"
        )
        msgs.append(
            f"OK: .goose/sandbox.sb is present with {denies}, its write roots match, and "
            f"GOOSE_SANDBOX is enabled live. NOTE (build variance): this confirms STATIC wiring "
            f"only — run the manual test ({manual}) to confirm your Goose build honors it."
        )
    return ok, msgs
