"""Sandbox / read-exclusion settings emission for the Claude adapter.

Extracted verbatim from ``claude.py`` (Cluster D / D-2b) so the small, stable
sandbox-emission surface can be integrity-tracked without pinning the
high-churn adapter. This module has ZERO behavior change from the original
inline implementation and depends only on the stdlib — it must not import
``ClaudeAdapter`` or any other ``claude.py`` internal.

``claude.py`` re-exports every public-to-the-project name defined here, so
existing importers (``cli/artifacts.py``, ``tests/…``) continue to resolve
these names from ``agentteams.frameworks.claude``.
"""

from __future__ import annotations

import json
import os
from typing import Any


#: Comment lines appended to the emitted settings example when the sandbox block is
#: injected. Explains that the boundary is inert until merged (same convention as the
#: hook), what it confines, and its platform limits — stated in the file rather than
#: left to be discovered.
_SANDBOX_COMMENT_LINES: list[str] = [
    "",
    "Workspace write-confinement (claude:sandbox) — the `sandbox` block below is part",
    "of this example and, like the hooks block, is INERT until you merge it into your",
    "own .claude/settings.json. Once merged, Claude Code's OS-level sandbox (per its",
    "docs: macOS Seatbelt / Linux + WSL2 bubblewrap) confines file writes to the",
    "allowWrite roots. Writes to the project root via Bash and child processes were",
    "verified denied outside the root under macOS Seatbelt; other platforms/versions",
    "follow Claude Code's own behavior, which this project does not control. Per those",
    "docs, `.claude/` is protected from agent edits even inside allowWrite, and you —",
    "editing outside an agent session — are unaffected. `allowUnsandboxedCommands: false`",
    "closes the escape hatch. allowWrite defaults to [\".\"] — the whole project tree is",
    "the workspace, so this confines writes to WITHIN the project (it does not restrict",
    "writes between project subdirectories). Native Windows has no OS enforcement; there",
    "this block is advisory only. To remove it: delete the `sandbox` key.",
    "denyWrite also names the decision-signing verify-key store DIRECTORY",
    "(<agents dir>/references/authorized-verify-keys), so an agent cannot plant its own",
    "public key. Whether Claude Code honours a DIRECTORY denyWrite entry is UNVERIFIED",
    "until a real-host test passes; test it before relying on it. This closes key",
    "PLANTING only: it does not stop an agent that can read your private signing key.",
]


#: Comment lines appended when the exclusive profile's read-exclusion (denyRead) is
#: injected — states honestly that this is OUTBOUND (seals my team's reads), not the
#: inbound "others can't read my tree" property (which is the operator's filesystem
#: hardening, see the emitted advisory reference).
_READ_EXCLUSION_COMMENT_LINES: list[str] = [
    "",
    "Read-exclusion (privilege_profile: exclusive) — the `denyRead` list above OS-denies",
    "THIS team (and its Bash/child processes) from READING those paths (credentials, and",
    "any sibling workspaces you added via protected_read_paths). `allowRead` re-opens the",
    "write roots so granted paths stay readable. This is OUTBOUND hardening: it stops YOUR",
    "team reading out — it does NOT stop OTHER teams from reading THIS workspace. For that",
    "inbound property, apply the operator OS filesystem hardening printed at generation",
    "and documented under \"Cross-team exclusion\" in the workspace-privilege-scoping docs",
    "(operator-run, not enforced by agentteams).",
    "IMPORTANT — verify these `~/` entries actually deny on YOUR machine. They rely on",
    "Claude Code expanding `~`→$HOME before the OS deny; agentteams cannot confirm that",
    "expansion from its side, and if `~` is NOT expanded, EVERY entry here is a silent",
    "no-op while this config still LOOKS protective. Test it: from inside the sandbox try",
    "to read one entry (e.g. `cat ~/.ssh/id_*`); it MUST be denied. If any read is NOT",
    "denied, replace that entry with an absolute path (e.g. /Users/<you>/.ssh) — but note",
    "an absolute path is host-specific and will not port to another machine. See the docs",
    "\"Verifying enforcement on your machine\".",
]


#: Alternate read-exclusion comment used when `resolve_deny_read_abspath` is set (P3-3):
#: the `denyRead` entries are already `expanduser`-resolved absolute paths, so the `~`
#: silent-no-op risk is gone — but the paths are host-specific and will not port.
_READ_EXCLUSION_ABSPATH_COMMENT_LINES: list[str] = [
    "",
    "Read-exclusion (privilege_profile: exclusive, resolve_deny_read_abspath: true) — the",
    "`denyRead` list above has been resolved to ABSOLUTE paths at generation time, so it",
    "does NOT depend on Claude Code expanding `~`→$HOME before the OS deny (the `~`-relative",
    "form's silent-no-op risk). Trade-off: these paths are HOST-SPECIFIC (they name the",
    "generating machine's home) and will NOT port to another machine — regenerate on the host",
    "that runs the team. This is still OUTBOUND hardening (your team cannot read these paths);",
    "it does not stop OTHER teams reading THIS workspace (operator filesystem hardening). Verify",
    "on your machine: from inside the sandbox try to read one entry; it MUST be denied.",
]


def _exclusive_read_deny_paths(manifest: dict[str, Any]) -> list[str] | None:
    """Return the read-exclusion deny list for the manifest, or None when not exclusive.

    Only the ``exclusive`` privilege profile carries read-exclusion (P3a). The list is
    the curated credential-path defaults plus any operator-supplied
    ``protected_read_paths`` (e.g. sibling-workspace roots), de-duplicated.

    When the manifest opts in via ``resolve_deny_read_abspath`` (P3-3), each
    ``~/``-relative entry is resolved to an ``expanduser``'d absolute path at emit
    time. This removes the dependency on Claude Code expanding ``~``→``$HOME`` before
    the OS deny — an unverified assumption that, if false, silently no-ops every
    default deny entry. The cost is portability: an absolute path is host-specific
    (it names the builder's home), so the default keeps the portable ``~/`` form.

    Args:
        manifest: The team manifest.

    Returns:
        The deny-read paths for an exclusive team, or ``None`` for any other profile —
        which keeps the emitted sandbox block byte-identical to the ``confined`` shape.
    """
    if manifest.get("privilege_profile") != "exclusive":
        return None
    deny: list[str] = list(_DEFAULT_PROTECTED_READ_PATHS)
    for extra in manifest.get("protected_read_paths") or []:
        if extra and extra not in deny:
            deny.append(extra)
    if manifest.get("resolve_deny_read_abspath"):
        deny = [os.path.abspath(os.path.expanduser(p)) for p in deny]
    return deny


def _sandbox_feature_enabled(manifest: dict[str, Any]) -> bool:
    """Return True iff workspace write-confinement is requested on this manifest.

    Reads BOTH sources of truth so the emitter is self-sufficient on every code path,
    not only the CLI generate path:

    * ``"claude:sandbox" in host_features`` — the token, set from --target-host-features
      and from privilege_profile expansion (the established gate convention, ``bridge.py``).
    * ``privilege_profile in {confined, exclusive}`` — the source field itself. This
      matters because ``extra_output_files`` is also invoked from ``convert.py`` and
      ``render_pipeline.py``, which do NOT run the profile→host_features union that
      ``cli/generate.py`` does; without this a confined manifest would silently emit no
      sandbox there.

    Args:
        manifest: The team manifest.

    Returns:
        Whether the sandbox settings block should be emitted.
    """
    if "claude:sandbox" in (manifest.get("host_features") or []):
        return True
    return manifest.get("privilege_profile") in {"confined", "exclusive"}


#: Default read-exclusion paths for the `exclusive` profile (P3a). High-value secret
#: stores an agent has no business reading, chosen to NOT break common authenticated
#: toolchains: SSH keys, cloud provider creds. Denied OS-level so even a Bash subprocess
#: cannot read them. `~/` is Claude Code's documented sandbox home-dir prefix. Registry
#: auth files (~/.npmrc, ~/.pypirc, ~/.netrc, ~/.docker/config.json) are DELIBERATELY
#: excluded from the default — denying them breaks authenticated npm/pip/git/docker
#: against private registries; operators who do not use those add them via
#: `protected_read_paths`. The same test excludes `~/.config/gh` (the GitHub CLI token,
#: read by `gh` on every call — and this framework ships @pr-manager/@pr-notifier/
#: @pr-reminder agents that routinely shell out to `gh`) and `~/.netrc` (git/curl auth):
#: both are routine agent dev-work identities, so denying them by default breaks the
#: toolchain — operators who want them add them via `protected_read_paths`. `~/.azure`
#: IS in the default: like `~/.aws`/`~/.config/gcloud` it is a cloud-provider credential
#: an agent rarely acts as during its build work. NOTE: this is OUTBOUND read hardening
#: (my team cannot read these FILES) — it does not stop other teams reading MY tree, and
#: it denies FILES, not environment variables (a secret already exported into the agent's
#: env — e.g. a signing key — is not covered by a filesystem denyRead).
_DEFAULT_PROTECTED_READ_PATHS: tuple[str, ...] = (
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.config/gcloud", "~/.azure",
)

#: D-3 (2026-08-26): control-plane files that live *inside* the write root but an in-sandbox
#: agent must not edit — else it could disable its own boundary. Emitted as ``denyWrite``, which
#: takes precedence over ``allowWrite`` (deny-over-allow; the Seatbelt mechanism is empirically
#: verified in ``tests/test_os_sandbox_enforcement.py``). Exact paths only — globs are unsupported.
#: - the ``enforce_decision_signing`` switch is emitted into the TEAM's ``references/``
#:   (``artifacts.AGENT_PRIVILEGE_REL_PATH``, relative to the agents dir). These deny paths are
#:   relative to the PROJECT root (the merged ``settings.json`` / the Seatbelt ``WORKSPACE_ROOT``),
#:   so the switch is ``<agents dir>/references/agent-privilege.json``. It was emitted as a bare
#:   ``references/agent-privilege.json`` — a project-root path nothing writes — until 2026-09-30
#:   (found auditing researchteam's rc7 refresh): the real switch was unprotected, and on Linux a
#:   deny path that does not exist stops bwrap initializing the sandbox at all. The existing test
#:   compared the privilege path in the agents-dir frame and the hook in the project-root frame.
#:   Claude Code's ``.claude/`` auto-protection claim is itself unverified (open item B-7 / P1-5),
#:   so the claude switch is listed explicitly even though it sits under ``.claude/``.
#: - the gate hook is added belt-and-suspenders for the same reason.
#: - the decision-signing Ed25519 verify-key store (``decision_log._VERIFY_KEY_STORE_REL``, a
#:   DIRECTORY of operator-provisioned ``<key-id>.pub.pem``) sits beside the switch in the team's
#:   ``references/``. Unprotected, an agent could plant its own public key and self-sign a
#:   relaxing authorization (2026-09-30). Its path is DERIVED from the switch path (one
#:   per-framework agents-dir mapping, no second copy). A missing deny path stops bwrap
#:   initializing, so the store must exist whenever this deny is emitted: the adapters emit
#:   :data:`VERIFY_KEY_STORE_SENTINEL_REL` from the SAME branch that emits the deny.
#:   HONEST LIMITS: this closes the key-PLANTING route only. Self-signing stays possible while
#:   the operator's private key is readable in the sandbox (remediation F-1, open). Claude Code's
#:   handling of a DIRECTORY ``denyWrite`` entry is UNVERIFIED pending a real-host test (only
#:   directory ``denyRead`` is verified). The goose Seatbelt arm relies on the existing
#:   ``(subpath …)`` semantics (the directory and all its descendants).
#: - DELIBERATELY NOT denied: the ledgers (``security-decisions.log.csv`` etc.; the in-sandbox gate
#:   rewrites them to consume use counts, and their integrity is signatures + hash chains) and
#:   ``signing-governed.marker`` (creating it only makes the workspace stricter).
_AGENT_PRIVILEGE_SWITCH: dict[str, str] = {
    "claude": ".claude/agents/references/agent-privilege.json",
    "goose": ".goose/recipes/references/agent-privilege.json",
}
_GATE_HOOK_PATH = ".claude/hooks/constitutional-gate.py"

#: The verify-key store's name inside the team ``references/``. ``decision_log`` reads
#: ``references/authorized-verify-keys``; a test locks the two together (``frameworks`` must not
#: import ``cli``).
_VERIFY_KEY_STORE_NAME = "authorized-verify-keys"

#: Agents-dir-relative path of the tool-owned sentinel that makes the verify-key store exist.
#: Never a ``*.pem`` name, so ``decision_log._load_verify_key`` can never select it.
VERIFY_KEY_STORE_SENTINEL_REL = f"references/{_VERIFY_KEY_STORE_NAME}/README.md"

#: FROZEN sentinel text. Do not edit: once a team is confined the store (and so this file) is
#: write-denied, and an in-sandbox ``--update`` that tried to rewrite changed text would fail on a
#: read-only path. ``tests/test_workspace_privilege_scoping.py`` pins its digest.
VERIFY_KEY_STORE_SENTINEL_TEXT = (
    "# authorized-verify-keys\n"
    "\n"
    "This directory holds the operator-provisioned Ed25519 public verify keys for\n"
    "decision signing, one `<key-id>.pub.pem` file per key. The operator provisions\n"
    "keys OUTSIDE any agent sandbox.\n"
    "\n"
    "When the team is sandboxed (confined/exclusive), this directory is write-denied\n"
    "to in-sandbox agents so an agent cannot plant its own key. agentteams emits only\n"
    "this README (so the denied path always exists) and never writes, rewrites or\n"
    "deletes any `*.pub.pem` file here.\n"
)


def _verify_key_store_path(framework: str) -> str:
    """Return ``framework``'s project-root-relative verify-key store dir, derived from its switch."""
    references_dir = _AGENT_PRIVILEGE_SWITCH[framework].rsplit("/", 1)[0]
    return f"{references_dir}/{_VERIFY_KEY_STORE_NAME}"


def protected_write_paths(framework: str) -> tuple[str, ...]:
    """Project-root-relative control-plane paths an in-sandbox agent of ``framework`` must not write.

    Args:
        framework: ``"claude"`` or ``"goose"`` (the frameworks with an emitted sandbox).

    Returns:
        The switch path for that framework's default agents dir, the gate hook, then the
        verify-key store directory (see the comment above for its unverified Claude arm).

    Raises:
        KeyError: ``framework`` emits no sandbox.
    """
    return (_AGENT_PRIVILEGE_SWITCH[framework], _GATE_HOOK_PATH, _verify_key_store_path(framework))


#: The Claude set (kept under its original name for existing importers).
_PROTECTED_WRITE_PATHS: tuple[str, ...] = protected_write_paths("claude")


def _build_sandbox_block(
    write_roots: list[str] | None, deny_read: list[str] | None = None
) -> dict[str, Any]:
    """Build the Claude Code ``sandbox`` settings block for workspace confinement.

    Args:
        write_roots: Directories (relative to the merged ``settings.json`` at the
            project root) the agent may write to. Defaults to ``["."]`` — the whole
            generated project tree is the workspace.
        deny_read: Paths the agent (and its subprocesses) may not READ (P3a read
            exclusion, ``exclusive`` profile). ``None``/empty emits no read restriction,
            leaving the block byte-identical to the ``confined`` shape.

    Returns:
        The ``sandbox`` settings object: OS-level enforcement on, writes confined to
        ``write_roots``, the unsandboxed-command escape hatch closed, and — when
        ``deny_read`` is given — reads of those paths denied while ``write_roots`` are
        re-opened for read via ``allowRead`` (so a P2-granted write target inside a
        denied region stays readable; read-modify-write keeps working).
    """
    roots = list(write_roots) if write_roots else ["."]
    filesystem: dict[str, Any] = {"allowWrite": roots}
    # D-3: deny the in-sandbox agent write access to the control-plane files it would otherwise
    # be able to edit (the switch is inside the write root). denyWrite wins over allowWrite.
    filesystem["denyWrite"] = list(_PROTECTED_WRITE_PATHS)
    if deny_read:
        filesystem["denyRead"] = list(deny_read)
        filesystem["allowRead"] = roots
    return {
        "enabled": True,
        "filesystem": filesystem,
        "allowUnsandboxedCommands": False,
    }


def _inject_sandbox_block(
    example_text: str,
    write_roots: list[str] | None,
    deny_read: list[str] | None = None,
    *,
    deny_read_resolved_abspath: bool = False,
) -> str:
    """Return the settings example JSON with a ``sandbox`` block merged in.

    Parses the shipped hooks example, adds the ``sandbox`` block and explanatory
    ``_comment`` lines, and re-serializes.

    Fails LOUD, not open: this is called only when confinement was *requested*, and the
    input is an agentteams-controlled, test-covered asset
    (``tests/.../test_emitted_settings_example_is_valid_json``). A parse failure is
    therefore agentteams' own bug — silently shipping a hooks-only example would hand
    the operator an unconfined team while they believe they asked for confinement, the
    worst outcome for a security feature. Raising surfaces the corruption instead.

    Args:
        example_text: The verbatim ``settings.hooks.example.json`` template text.
        write_roots: Optional override of the confined write roots.
        deny_read: Optional read-exclusion paths (P3a, ``exclusive`` profile); adds a
            ``denyRead`` restriction and an explanatory comment when present.

    Returns:
        The settings example JSON text with the sandbox block merged in.

    Raises:
        ValueError: If ``example_text`` is not parseable as a JSON object — a corrupted
            shipped asset that must not be masked when a sandbox was requested.
    """
    try:
        data = json.loads(example_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "settings.hooks.example.json is not valid JSON; cannot inject the requested "
            f"sandbox confinement block: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            "settings.hooks.example.json did not parse to a JSON object; cannot inject "
            "the requested sandbox confinement block."
        )
    data["sandbox"] = _build_sandbox_block(write_roots, deny_read)
    comment = data.get("_comment")
    if isinstance(comment, list):
        extra = list(_SANDBOX_COMMENT_LINES)
        if deny_read:
            extra += (
                _READ_EXCLUSION_ABSPATH_COMMENT_LINES
                if deny_read_resolved_abspath
                else _READ_EXCLUSION_COMMENT_LINES
            )
        data["_comment"] = comment + extra
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
