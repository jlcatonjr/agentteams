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
import sys
from typing import Any

from agentteams.frameworks._prompt_root_protect import (
    PROMPT_ROOT_COMMENT_LINES,
    present_prompt_roots,
    prompt_root_edit_rules,
)


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
    "writes between project subdirectories). `failIfUnavailable: true` makes it FAIL",
    "CLOSED: without it Claude Code silently runs every command UNSANDBOXED when its",
    "sandbox cannot start (measured on Linux without socat: \"Sandbox disabled ... Commands",
    "will run WITHOUT sandboxing\"); with it Claude Code refuses to start instead. Linux",
    "needs bubblewrap (bwrap) AND socat on PATH (scripts/install-sandbox-deps.sh in the",
    "agentteams repo); Ubuntu's kernel.apparmor_restrict_unprivileged_userns=1 makes every",
    "sandboxed command fail closed until the operator relaxes it. Confinement on Linux was",
    "VERIFIED end-to-end on 2026-09-30 (Ubuntu with that restriction on, bubblewrap 0.11.1,",
    "socat 1.8.1.1, Claude Code 2.1.251: write confinement, denyWrite of the switch, denyRead",
    "incl. ~/, the signing-key dir, the .claude read-only bind, failIfUnavailable) ONLY after",
    "installing Claude Code's documented unconfined /etc/apparmor.d/bwrap profile, which",
    "disables Ubuntu's bwrap-userns-restrict host-wide; other distros and Claude Code",
    "versions are untested. A block generated on native Windows omits failIfUnavailable:",
    "Claude Code has no OS enforcement there and the block is advisory only (it would",
    "otherwise refuse to start). To remove it: delete the `sandbox` key.",
    "denyWrite also names the decision-signing verify-key store DIRECTORY",
    "(<agents dir>/references/authorized-verify-keys), so an agent cannot plant its own",
    "public key. Claude Code 2.1.251 on Linux binds every denyWrite entry, this DIRECTORY",
    "included, read-only (its captured bwrap argv, 2026-09-30); other versions are untested.",
    "denyWrite also names the whole `.claude` directory (F-4): without it an agent could",
    "RENAME `.claude` away and plant a replacement tree (settings, hooks, switch) that the",
    "NEXT session reads. Claude Code binds the entry read-only over the project, so `.claude`",
    "cannot be renamed, replaced or written from Bash (you, outside a session, are",
    "unaffected). Status: VERIFIED on Linux with Claude Code 2.1.251 (read-only bind in its",
    "bwrap argv; a sandboxed `mv .claude` fails), under the AppArmor precondition above.",
    "Sibling teams (2026-09-30): when the project held a codex, copilot or goose agentteams team",
    "at generation, denyWrite also names `.codex`, `.github/agents` or `.goose` (only those present:",
    "a missing deny path stops bwrap). Claude Code self-binds each deny path's parent, so `.github`",
    "cannot be renamed while `.github/workflows` stays writable (product-verified, Claude Code",
    "2.1.251, Linux; re-run after upgrades). REMOVE an entry from your .claude/settings.json BEFORE",
    "deleting that team, or every sandboxed command fails (\"Can't mkdir ... Not a directory\").",
]


#: Comment lines appended with every sandbox block: the operator signing-key isolation (F-1) and
#: the ``permissions.deny`` list that covers the built-in tools the sandbox does not.
_SIGNING_KEY_COMMENT_LINES: list[str] = [
    "",
    "Signing-key isolation (F-1) — in EVERY sandboxed profile `denyRead` names the operator",
    "private-key directory ~/.config/agentteams/keys (create it with the operator helper",
    "references/authorized-verify-keys/provision-operator-signing-key.sh in the agentteams",
    "source repository (it is NOT emitted into this project): run it with --team-dir",
    "<agents dir> so the public key lands in the store the gate reads; --migrate moves keys",
    "from the old ~/.config/agentteams/ location). Any legacy",
    "~/.config/agentteams/*.pem found on the GENERATING host is listed by exact path too (the",
    "sandbox takes no globs); regenerate on the host that runs the team. Only the Read of the",
    "key FILE is closed: the environment variables AGENTTEAMS_DECISION_ED25519_KEYFILE and",
    "AGENTTEAMS_*_SIGNING_KEY are INHERITED by sandboxed commands — do not export them into",
    "the shell that launches `claude`. Claude Code's `sandbox.filesystem` binds only Bash",
    "(and its child processes); the built-in Read/Edit/Write tools obey `permissions` instead,",
    "so the `permissions.deny` list below carries: Read(...) rules for the key directory and",
    "the legacy location (Read rules cover Grep/Glob best-effort), and Edit(...) rules —",
    "which cover Edit, Write and MultiEdit (Claude Code matches no NotebookEdit(path) rule) —",
    "for the enforce_decision_signing switch, the verify-key store, the approver and manager",
    "rosters and management-authority.json (team dir), the project-root",
    "references/security-approvers.txt, .claude/settings.json, .claude/settings.local.json",
    "and .claude/hooks/** (PR-D). Without those an agent could Write-tool its own public key",
    "into the store (measured: the Write tool ignores sandbox denyWrite outside .claude/). The",
    "roster, settings and hooks rules are emitted but UNVERIFIED on the product (they are",
    "stricter than Claude Code's own .claude ask, measured under acceptEdits only). The HMAC",
    "grant route through the project-root roster is NOT closed by these rules; it is",
    "addressed separately (Ed25519 grants). RESIDUALS: `permissions.deny` is INERT",
    "until merged like the rest of this example; it is unverified under bypassPermissions;",
    "hooks and MCP servers run UNSANDBOXED; the Claude sandbox arm is verified on Linux only",
    "for Claude Code 2.1.251 on Ubuntu with the documented bwrap AppArmor profile installed",
    "(macOS is UNVERIFIED). A denyRead path that does not exist was tolerated by bwrap on Claude Code 2.1.251",
    "(Linux); older builds are untested.",
    "The list also carries Edit(...) rules for the SIBLING teams' trust-root files (.github/agents,",
    ".codex/agents and .goose/recipes: switch, verify-key store, rosters, build-log.json; plus",
    ".goose/sandbox.sb and .codex/config.toml), emitted whether or not those teams exist. Unless the",
    "brief sets protect_prompt_roots: true, they do NOT cover copilot/codex agent files or",
    ".github/copilot-instructions.md (Edit-tool writable).",
]


#: The operator private-key directory (F-1). Every emitted sandbox (Claude block, goose Seatbelt
#: profile, ``confine-run.sh``) read-denies it whatever the privilege profile. Deliberately NOT
#: ``$XDG_CONFIG_HOME``-relative: a deny path must be static and identical across the emitters.
#: The shell literals in ``confine-run.sh`` and ``provision-operator-signing-key.sh`` are locked to
#: this value by a test. Not the whole ``~/.config/agentteams``: ``goose_config`` reads
#: ``goose-sources.json`` there and silently falls back to built-ins when it cannot.
SIGNING_KEY_DIR = "~/.config/agentteams/keys"
#: The proposal ledger's directory; write-denied to every session under write_policy "orchestrator-only".
LEDGER_DIR_REL = ".agentteams"

#: The pre-F-1 private-key location. Key files there are denied TRANSITIONALLY (@security ruling
#: (b), 2026-09-30); remove once a release has shipped the migration advisory. The glob form is
#: used ONLY in ``permissions.deny`` (gitignore syntax); the sandbox side lists exact paths.
LEGACY_SIGNING_KEY_DIR = "~/.config/agentteams"
LEGACY_SIGNING_KEY_GLOB = f"{LEGACY_SIGNING_KEY_DIR}/*.pem"


def _home_rel_to_abs(path: str) -> str:
    """Return ``path`` with a leading ``~`` expanded and made absolute."""
    return os.path.abspath(os.path.expanduser(path))


def legacy_signing_key_files() -> list[str]:
    """Return the ``~/``-relative paths of ``*.pem`` files in the legacy key location, sorted.

    Host-local by nature: it lists what exists on the GENERATING host (the sandbox side takes
    exact paths only). Unreadable or absent directory → ``[]``.

    Returns:
        E.g. ``["~/.config/agentteams/decision-signing-op-2026.pem"]``.
    """
    legacy = _home_rel_to_abs(LEGACY_SIGNING_KEY_DIR)
    try:
        names = sorted(os.listdir(legacy))
    except OSError:
        return []
    return [
        f"{LEGACY_SIGNING_KEY_DIR}/{n}"
        for n in names
        if n.endswith(".pem") and os.path.isfile(os.path.join(legacy, n))
    ]


def signing_key_deny_read(*, resolve_abspath: bool = False) -> list[str]:
    """Return the sandbox read-deny entries isolating the operator private key.

    Args:
        resolve_abspath: Expand ``~`` to the generating host's absolute home (the P3-3 opt-in).

    Returns:
        :data:`SIGNING_KEY_DIR`, then every legacy key file on this host (exact paths).
    """
    paths = [SIGNING_KEY_DIR, *legacy_signing_key_files()]
    return [_home_rel_to_abs(p) for p in paths] if resolve_abspath else paths


def assert_roots_clear_of_signing_keys(roots: list[str] | None) -> None:
    """Raise if a write root sits at or inside :data:`SIGNING_KEY_DIR`.

    Claude Code resolves overlapping read rules narrower-wins, so a write root (re-opened by
    ``allowRead``, and writable) at or inside the key directory would undo its deny. Only
    ``~``-relative and absolute roots are checked: a relative root is relative to the project,
    which is never inside the key directory.

    Args:
        roots: The workspace write roots (``None`` = the default ``["."]``).

    Raises:
        ValueError: A root resolves at or inside the key directory.
    """
    keys = _home_rel_to_abs(SIGNING_KEY_DIR)
    for root in roots or []:
        if not root or not (root.startswith("~") or os.path.isabs(root)):
            continue
        resolved = _home_rel_to_abs(root)
        if resolved == keys or resolved.startswith(keys + os.sep):
            raise ValueError(
                f"workspace write root {root!r} is at or inside the operator signing-key "
                f"directory {SIGNING_KEY_DIR}; that would re-open the private key to the "
                "sandboxed agent. Remove it from workspace_write_roots (or the grant)."
            )


def signing_keyfile_warnings(keyfile: str) -> list[str]:
    """Return operator warnings about where a private signing key file sits (never refuses).

    The sandboxes read-deny only :data:`SIGNING_KEY_DIR` (plus legacy key files transitionally),
    so a key elsewhere, behind a symlink, or with a mode wider than 600 is a visibility signal
    for the operator (Rule 12), not a hard stop: ``--sign-decision`` still signs.

    Args:
        keyfile: The path named by ``AGENTTEAMS_DECISION_ED25519_KEYFILE``.

    Returns:
        Zero or more warning sentences. A missing file yields a pointer to the migrated copy
        when one exists under :data:`SIGNING_KEY_DIR`, else nothing (the read error reports it).
    """
    keys = os.path.realpath(_home_rel_to_abs(SIGNING_KEY_DIR))
    legacy = os.path.realpath(_home_rel_to_abs(LEGACY_SIGNING_KEY_DIR))
    path = os.path.abspath(os.path.expanduser(keyfile))
    real = os.path.realpath(path)
    if not os.path.exists(path):
        moved = os.path.join(keys, os.path.basename(path))
        if os.path.dirname(real) == legacy and os.path.isfile(moved):
            return [
                f"{keyfile} does not exist, but {moved} does: the key was migrated into "
                f"{SIGNING_KEY_DIR}. Export AGENTTEAMS_DECISION_ED25519_KEYFILE=\"{moved}\"."
            ]
        return []
    out: list[str] = []
    if os.path.islink(path):
        out.append(f"{keyfile} is a symlink (to {real}); sandbox read-denies match the resolved "
                   "path, so keep the key itself in the key directory.")
    if os.path.dirname(real) == legacy:
        out.append(f"{keyfile} is in the pre-F-1 location {LEGACY_SIGNING_KEY_DIR}/. Move it into "
                   f"{SIGNING_KEY_DIR} with provision-operator-signing-key.sh --migrate.")
    elif not real.startswith(keys + os.sep):
        out.append(f"{keyfile} is outside {SIGNING_KEY_DIR}: no emitted sandbox read-denies it, "
                   "so a sandboxed agent may be able to read it.")
    try:
        mode = os.stat(real).st_mode & 0o777
    except OSError:
        mode = 0
    if mode & 0o077:
        out.append(f"{keyfile} has mode {mode:o}; a private key should be 600 (chmod 600).")
    return out


def permission_deny_rules(framework: str = "claude") -> list[str]:
    """Return the Claude Code ``permissions.deny`` rules emitted beside the sandbox block.

    ``sandbox.filesystem`` binds Bash and its children only; the built-in tools obey
    ``permissions``. ``Read(...)`` covers Read/Grep/Glob (best-effort per the docs; there is no
    ``Grep(...)``/``Glob(...)`` form). ``Edit(...)`` covers Edit/Write/MultiEdit; Claude Code 2.1.251
    warns that a ``NotebookEdit(path)`` rule "is not matched by file permission checks", so none is
    emitted. Home paths are ``~/``-anchored; project paths are ``/``-anchored (relative to the
    settings source, i.e. the project root for ``.claude/settings.json``).

    Args:
        framework: The framework whose control-plane paths to protect.

    Returns:
        The rule strings, read rules first.
    """
    rules = [f"Read({SIGNING_KEY_DIR}/**)", f"Read({LEGACY_SIGNING_KEY_GLOB})"]
    store = _verify_key_store_path(framework)
    for path in protected_write_paths(framework):
        if path == _GATE_HOOK_PATH:
            continue  # covered by the broader hooks/** rule below
        rules.append(f"Edit(/{path}/**)" if path == store else f"Edit(/{path})")
    # PR-D: the operator-only rosters (an Edit rule needs no existing path), then the settings
    # files and hooks the next session trusts. Emitted, product-UNVERIFIED.
    rules += [f"Edit(/{p})" for p in (*governed_roster_paths(framework), GRANT_ROSTER_PROJECT_REL)]
    rules += [f"Edit(/{p})" for p in _CLAUDE_SETTINGS_PATHS]
    # Follow-up #2 (2026-09-30): the operator-merged/-run EXAMPLES. An edited settings example or
    # goose runner is what the operator next merges or executes unsandboxed. Product-UNVERIFIED.
    rules += [f"Edit(/{p})" for p in OPERATOR_EXAMPLE_PATHS]
    rules.append(f"Edit(/{_GATE_HOOK_PATH.rsplit('/', 1)[0]}/**)")
    if framework == "claude":
        rules += _sibling_permission_deny_rules()
    return rules


def _sibling_permission_deny_rules() -> list[str]:
    """Return the ``Edit(...)`` rules for the sibling teams' trust-root FILES (2026-09-30).

    Unconditional: an ``Edit`` rule needs no existing path. Scoped to the trust roots (switch,
    verify-key store, rosters, team marker, the goose profile, Codex's config), never a whole
    agents dir: authoring copilot/codex agent files with the Edit tool stays possible.
    """
    rules: list[str] = []
    for key in ("copilot", "codex", "goose"):
        store = _verify_key_store_path(key)
        rules += [f"Edit(/{_AGENT_PRIVILEGE_SWITCH[key]})", f"Edit(/{store}/**)"]
        rules += [f"Edit(/{p})" for p in (*governed_roster_paths(key), team_marker_path(key))]
    rules += ["Edit(/.goose/sandbox.sb)", f"Edit(/{CODEX_CONFIG_REL})"]
    return rules


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
        in which case the block carries only the signing-key ``denyRead`` (the ``confined`` shape).
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
#:   initializing, so the store must exist whenever this deny is emitted: ``base.py`` emits
#:   :data:`VERIFY_KEY_STORE_SENTINEL_REL` for EVERY team framework, unconditionally (2026-09-30;
#:   supersedes the earlier same-branch rule, which left confined goose on Linux without it).
#:   HONEST LIMITS: this closes the key-PLANTING route via Bash only; the built-in Write/Edit
#:   tools ignore ``denyWrite`` and are bound by :func:`permission_deny_rules` instead. The
#:   private key FILE is read-denied separately (:data:`SIGNING_KEY_DIR`, F-1). Claude Code's
#:   handling of a DIRECTORY ``denyWrite`` entry was verified on Linux 2026-09-30 for Claude
#:   Code 2.1.251 only (every denyWrite entry is read-only bound in its captured bwrap argv, and a
#:   sandboxed ``mv .claude`` fails — ``tests/test_os_sandbox_product_enforcement.py``, F-4). The goose Seatbelt arm relies on the existing
#:   ``(subpath …)`` semantics (the directory and all its descendants).
#: - DELIBERATELY NOT denied: the ledgers (``security-decisions.log.csv`` etc.; the in-sandbox gate
#:   rewrites them to consume use counts, and their integrity is signatures + hash chains) and
#:   ``signing-governed.marker`` (creating it only makes the workspace stricter).
#: - SIBLING team dirs (2026-09-30): the table below also maps the copilot and codex team keys.
#:   Their trust roots reach the Claude ``denyWrite`` only as whole dirs, and only when present at
#:   generation (:data:`SIBLING_DENY_DIRS`). ``permission_deny_rules`` covers their files always, and
#:   the launcher and the goose Seatbelt profile list them. The Claude set
#:   (:data:`_PROTECTED_WRITE_PATHS`) is unchanged.
_AGENT_PRIVILEGE_SWITCH: dict[str, str] = {
    "claude": ".claude/agents/references/agent-privilege.json",
    "goose": ".goose/recipes/references/agent-privilege.json",
    # 2026-09-30 (team-dir control plane): the copilot (copilot-vscode + copilot-cli share
    # ``.github/agents``) and codex team dirs hold the same framework-neutral trust roots. They
    # emit no sandbox of their own; the Claude block, the goose Seatbelt profile and the launcher
    # protect them. agents-md (``.agents``, shared with Codex skills) is out of scope.
    "copilot": ".github/agents/references/agent-privilege.json",
    "codex": ".codex/agents/references/agent-privilege.json",
}

#: Every team key whose trust roots are protected, in launcher ``TEAM_DIRS_REL`` order.
ALL_TEAM_FRAMEWORKS: tuple[str, ...] = ("claude", "goose", "copilot", "codex")

#: Framework id -> team key (the two copilot adapters share one team dir).
TEAM_KEY_BY_FRAMEWORK: dict[str, str] = {
    "claude": "claude", "goose": "goose", "copilot-vscode": "copilot", "copilot-cli": "copilot",
    "codex": "codex",
}

#: Codex's project config (approval and sandbox policy; an upstream claim, unverified here).
#: Protect-if-present everywhere, with no stub. agentteams does write it when ``codex:mcp`` is on
#: (``codex_mcp_emit`` splices MCP tables into an EXISTING file and keeps its other keys), so a
#: planted file can be carried forward: the launcher and ``--check`` warn on its security keys.
CODEX_CONFIG_REL = ".codex/config.toml"
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
    """Project-root-relative control-plane paths an in-sandbox agent must not write, for a team.

    Args:
        framework: A team key of :data:`ALL_TEAM_FRAMEWORKS`.

    Returns:
        The switch path for that team's default agents dir, the gate hook (claude and goose
        only: copilot and codex emit none), then the verify-key store directory.

    Raises:
        KeyError: ``framework`` is not a protected team key.
    """
    if framework in ("claude", "goose"):
        return (_AGENT_PRIVILEGE_SWITCH[framework], _GATE_HOOK_PATH, _verify_key_store_path(framework))
    return (_AGENT_PRIVILEGE_SWITCH[framework], _verify_key_store_path(framework))


def team_agents_dir(framework: str) -> str:
    """Return a team key's project-root-relative default agents dir (e.g. ``.github/agents``)."""
    return _AGENT_PRIVILEGE_SWITCH[framework].rsplit("/", 2)[0]


def team_marker_path(framework: str) -> str:
    """Return a team key's project-root-relative team marker (``<agents dir>/references/build-log.json``)."""
    return f"{team_agents_dir(framework)}/{TEAM_MARKER_REL}"


#: The Claude set (kept under its original name for existing importers).
_PROTECTED_WRITE_PATHS: tuple[str, ...] = protected_write_paths("claude")

#: PR-D: operator-only trust roots in the team ``references/``, by basename. Locked by a test to
#: their readers' constants (``decision_log._DECISION_AUTHORS_FILE``,
#: ``management_directives.AUTHORIZED_MANAGERS_REL``, ``artifacts.MANAGEMENT_AUTHORITY_REL_PATH``;
#: ``frameworks`` must not import ``cli``). Kept OUT of :func:`protected_write_paths` so the Claude
#: ``denyWrite`` is unchanged: they sit under ``.claude``, which it already denies whole, and a
#: per-file entry for a missing roster would stop bwrap initializing.
_GOVERNED_ROSTER_NAMES: tuple[str, ...] = (
    "security-approvers.txt", "authorized-managers.txt", "management-authority.json",
)

#: The project-root approver roster the grant path reads (``grants.held_grants``). Protected where
#: an arm tolerates a missing path (``permissions.deny``, Seatbelt) and protect-if-present in the
#: launcher. NOT a closed route: the HMAC grant route is addressed separately (Ed25519 grants).
GRANT_ROSTER_PROJECT_REL = "references/security-approvers.txt"

#: The agentteams team marker, relative to an agents dir (``drift.load_build_log``). The launcher
#: requires a framework's control-plane entries only when this marker is present.
TEAM_MARKER_REL = "references/build-log.json"

#: Claude Code settings files the NEXT session trusts (hook wiring, permissions).
_CLAUDE_SETTINGS_PATHS: tuple[str, ...] = (".claude/settings.json", ".claude/settings.local.json")

#: Operator-merged / operator-run examples an agent must not edit (follow-up #2, 2026-09-30).
OPERATOR_EXAMPLE_PATHS: tuple[str, ...] = (
    ".claude/settings.hooks.example.json", ".goose/confined-run.example.sh",
)

#: FROZEN comment-only stub texts, written write-if-absent (never overwritten) for a sandboxed team
#: so every launcher/Seatbelt entry exists. A stub reads exactly like an absent file in every reader
#: (default approvers; no manager; no management authority): ``tests/test_prd_trust_roots.py``.
CONTROL_PLANE_STUB_TEXT: dict[str, str] = {
    "security-approvers.txt": (
        "# security-approvers.txt: operator-only approver roster (one author per line).\n"
        "# Comment-only stub written by agentteams so the sandbox can write-protect this path.\n"
        "# While it names nobody the built-in default applies, exactly as if it were absent.\n"
    ),
    "authorized-managers.txt": (
        "# authorized-managers.txt: operator-only manager-team roster (one team id per line).\n"
        "# Comment-only stub written by agentteams so the sandbox can write-protect this path.\n"
        "# While it names nobody every management directive is refused, as if it were absent.\n"
    ),
    "management-authority.json": json.dumps({
        "is_management_repo": False,
        "authorized_managers": [],
        "note": "stub: written by agentteams so the sandbox can write-protect this path; "
                "declares no management authority (same as absent).",
    }, indent=2) + "\n",
}


def governed_roster_paths(framework: str) -> tuple[str, ...]:
    """Project-root-relative operator-only rosters/config of ``framework``'s default team dir.

    Args:
        framework: A team key of :data:`ALL_TEAM_FRAMEWORKS`.

    Returns:
        The approver roster, the authorized-manager roster and ``management-authority.json``,
        derived from the switch's ``references/`` dir.

    Raises:
        KeyError: ``framework`` is not a protected team key.
    """
    references_dir = _AGENT_PRIVILEGE_SWITCH[framework].rsplit("/", 1)[0]
    return tuple(f"{references_dir}/{name}" for name in _GOVERNED_ROSTER_NAMES)


def framework_config_dir(framework: str) -> str:
    """Return a team key's project-root-relative top-level config dir (``.claude``, ``.github``, …).

    Derived from the SAME source as :func:`protected_write_paths` (the switch path's first
    segment), so a team written under a non-default ``--output`` gets the same frame, and the
    existing mismatch warning (``cli.generate_helpers._warn_sandbox_deny_path_mismatch``) names it.

    Args:
        framework: A team key of :data:`ALL_TEAM_FRAMEWORKS`. ``.github`` (copilot) is used for
            ancestors only, never as a Claude deny (it holds workflows and CODEOWNERS).

    Returns:
        The config dir, e.g. ``".claude"``.

    Raises:
        KeyError: ``framework`` is not a protected team key.
    """
    return _AGENT_PRIVILEGE_SWITCH[framework].split("/", 1)[0]


def control_plane_ancestors(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return every proper ancestor dir of ``paths`` below the project root, top-down, de-duplicated.

    F-4: an in-sandbox agent that may write the project root can RENAME an ancestor of a
    write-denied control-plane path (``mv .claude .claude.old``) and plant a replacement tree read
    by the next session. These are the directories whose rename must be refused.

    Args:
        paths: Project-root-relative control-plane paths (no leading ``/`` or ``./``).

    Returns:
        E.g. ``(".claude", ".claude/agents", ".claude/agents/references", ".claude/hooks")``.
    """
    out: list[str] = []
    for path in paths:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            anc = "/".join(parts[:i])
            if anc not in out:
                out.append(anc)
    return tuple(sorted(out))


#: Sibling team key -> the directory the Claude block write-denies when that team is present.
#: ``.codex`` and ``.goose`` whole (no renameable ancestor below the project root); ``.github/agents``
#: only, never ``.github`` (workflows, CODEOWNERS). Claude Code 2.1.251 self-binds each deny path's
#: PARENT read-write (captured argv), so ``.github`` becomes a mount point and cannot be renamed
#: while ``.github/workflows`` stays writable (product itest, RUN_CLAUDE_SANDBOX_ITEST=1).
SIBLING_DENY_DIRS: dict[str, str] = {"codex": ".codex", "copilot": ".github/agents", "goose": ".goose"}

#: Transient manifest key carrying the COMPUTED sibling deny dirs (``cli.generate_helpers``). Never
#: persisted (the ``_`` prefix keeps it out of the manifest fingerprint; the build log records named
#: keys only). Honoured only as a ``tuple`` (a JSON brief or manifest can only yield a list, so an
#: input-supplied value is ignored) and only for :data:`SIBLING_DENY_DIRS` values.
SIBLING_DENY_DIRS_KEY = "_sibling_deny_dirs"


def _real_dir_chain(project_root: str, rel: str) -> bool:
    """True iff every component of ``rel`` under ``project_root`` is a real dir (never a symlink)."""
    path = project_root
    for part in rel.split("/"):
        path = os.path.join(path, part)
        if os.path.islink(path) or not os.path.isdir(path):
            return False
    return True


def present_sibling_deny_dirs(project_root: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return the sibling deny dirs whose agentteams team is present under ``project_root``, sorted.

    A team is present when its default agents dir and ``references/`` are real directories (no
    component a symlink) holding a regular-file, non-symlink team marker. A symlinked ``.github``,
    ``.codex``, agents dir or marker is rejected: a deny through a symlink would protect the
    target, not the path the next session reads.

    Args:
        project_root: The project root (where the merged ``.claude/settings.json`` applies).

    Returns:
        E.g. ``(".codex", ".github/agents")``.
    """
    root = os.fspath(project_root)
    out: list[str] = []
    for key, deny in SIBLING_DENY_DIRS.items():
        if not _real_dir_chain(root, f"{team_agents_dir(key)}/references"):
            continue
        marker = os.path.join(root, team_marker_path(key))
        if os.path.isfile(marker) and not os.path.islink(marker):
            out.append(deny)
    return tuple(sorted(out))


def sibling_deny_dirs(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Return the computed sibling deny dirs carried by ``manifest`` (:data:`SIBLING_DENY_DIRS_KEY`).

    Args:
        manifest: The team manifest.

    Returns:
        The allowed, de-duplicated dirs in sorted order; ``()`` when absent or not computed.
    """
    value = manifest.get(SIBLING_DENY_DIRS_KEY)
    if not isinstance(value, tuple):
        return ()
    allowed = set(SIBLING_DENY_DIRS.values())
    return tuple(sorted({v for v in value if isinstance(v, str) and v in allowed}))


def _sandbox_fails_closed_on(platform: str | None = None) -> bool:
    """Return True iff the emitted block should carry ``failIfUnavailable: true`` here.

    Claude Code OS-enforces its sandbox on macOS (Seatbelt) and Linux/WSL2 (bubblewrap +
    socat). There, an operator who asked for confinement must get a refusal to start rather
    than a silent unsandboxed session, so the block fails closed. On native Windows (and any
    other platform) Claude Code has no OS sandbox, the block is advisory only, and
    ``failIfUnavailable`` would stop Claude Code starting at all. The predicate mirrors the
    ``privilege-profile-claude-native-windows-advisory`` branch in ``host_features``; it is
    re-stated here because this module depends only on the stdlib.

    Args:
        platform: Override for ``sys.platform`` (tests). ``None`` reads the live value: the
            generating host stands in for the target, as it does for every other platform
            decision in the emitters.

    Returns:
        Whether the sandbox block should fail closed.
    """
    plat = sys.platform if platform is None else platform
    return plat.startswith("linux") or plat == "darwin"


def _build_sandbox_block(
    write_roots: list[str] | None,
    deny_read: list[str] | None = None,
    *,
    platform: str | None = None,
    resolve_abspath: bool = False,
    sibling_deny_dirs: tuple[str, ...] | list[str] = (),
    project_root: str | None = None,
    protect_prompt_roots: bool = False,
    protect_ledger: bool = False,
) -> dict[str, Any]:
    """Build the Claude Code ``sandbox`` settings block for workspace confinement.

    Three read properties are independent: the signing-key ``denyRead`` is emitted in EVERY
    block (F-1); the profile read-exclusion ``deny_read`` is appended only when given
    (``exclusive``); ``allowRead`` (re-opening the write roots) only accompanies ``deny_read``.
    So a ``confined`` block carries the key deny and no ``allowRead``.

    Args:
        write_roots: Directories (relative to the merged ``settings.json`` at the
            project root) the agent may write to. Defaults to ``["."]`` — the whole
            generated project tree is the workspace.
        deny_read: Profile read-exclusion paths (P3a, ``exclusive``). ``None``/empty emits no
            profile read-exclusion and no ``allowRead``.
        platform: Override for ``sys.platform`` (tests); see :func:`_sandbox_fails_closed_on`.
        resolve_abspath: Emit the signing-key entries as absolute paths (P3-3 opt-in).
        sibling_deny_dirs: Sibling team dirs PRESENT at generation
            (:func:`present_sibling_deny_dirs`), appended to ``denyWrite``. Never an absent one:
            a missing deny path stops bwrap.
        project_root: The absolute project root (``_write_roots.PROJECT_ROOT_KEY``), for the
            project-ancestor ban and the prompt-root presence check; ``None`` skips both.
        protect_prompt_roots: Append the PRESENT prompt roots (:func:`present_prompt_roots`) to
            ``denyWrite`` (follow-up #8 phase 2, opt-in).

    Returns:
        The ``sandbox`` settings object: OS-level enforcement on, writes confined to
        ``write_roots``, the unsandboxed-command escape hatch closed, the operator signing key
        read-denied, and — when ``deny_read`` is given — reads of those paths denied while
        ``write_roots`` are re-opened for read via ``allowRead`` (so a P2-granted write target
        inside a denied region stays readable). On macOS/Linux it also sets
        ``failIfUnavailable: true``, so Claude Code refuses to start when its sandbox cannot
        initialize instead of silently running every command unsandboxed. A native-Windows
        block omits it (advisory only there).

    Raises:
        ValueError: A write root matches a hard ban (``_write_roots.validate_write_roots``:
            the signing-key directory, home, the project control plane, metacharacters, …).
    """
    from agentteams.frameworks._write_roots import validate_write_roots

    roots = list(write_roots) if write_roots else ["."]
    assert_roots_clear_of_signing_keys(roots)
    validate_write_roots(roots, project_root=project_root)
    filesystem: dict[str, Any] = {"allowWrite": roots}
    # D-3: deny the in-sandbox agent write access to the control-plane files it would otherwise
    # be able to edit (the switch is inside the write root). denyWrite wins over allowWrite.
    # F-4 (2026-09-30): the whole framework config dir too. Claude Code turns a denyWrite entry
    # into a --ro-bind AFTER the rw root bind (captured argv, 2.1.251), so `.claude` becomes a
    # read-only mount point: renaming it (the ancestor-rename route to a planted tree) gives
    # EBUSY and a planted `.claude/settings.local.json` is refused. A deny only removes
    # capability, whatever the write roots; permissions.deny is unaffected (built-in tools).
    filesystem["denyWrite"] = [*_PROTECTED_WRITE_PATHS, framework_config_dir("claude"),
                               *sibling_deny_dirs]
    if protect_prompt_roots:
        filesystem["denyWrite"] += [p for p in present_prompt_roots(project_root)
                                    if p not in filesystem["denyWrite"]]
    if protect_ledger and LEDGER_DIR_REL not in filesystem["denyWrite"]:
        # write_policy "orchestrator-only" (P4a): only the out-of-session runner writes the ledger.
        filesystem["denyWrite"].append(LEDGER_DIR_REL)
    denied = list(deny_read or [])
    for path in signing_key_deny_read(resolve_abspath=resolve_abspath):
        if path not in denied:
            denied.append(path)
    filesystem["denyRead"] = denied
    if deny_read:
        filesystem["allowRead"] = roots
    block: dict[str, Any] = {
        "enabled": True,
        "filesystem": filesystem,
        "allowUnsandboxedCommands": False,
    }
    if _sandbox_fails_closed_on(platform):
        block["failIfUnavailable"] = True
    return block


def _inject_sandbox_block(
    example_text: str,
    write_roots: list[str] | None,
    deny_read: list[str] | None = None,
    *,
    deny_read_resolved_abspath: bool = False,
    sibling_deny_dirs: tuple[str, ...] = (),
    project_root: str | None = None,
    protect_prompt_roots: bool = False,
    protect_ledger: bool = False,
) -> str:
    """Return the settings example JSON with a ``sandbox`` block merged in.

    Parses the shipped hooks example, adds the ``sandbox`` block, the ``permissions.deny``
    rules that bind the built-in tools (:func:`permission_deny_rules`) and explanatory
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
        deny_read: Optional read-exclusion paths (P3a, ``exclusive`` profile); adds them to
            ``denyRead`` with ``allowRead`` and the exclusive comment when present. The
            signing-key ``denyRead``, the ``permissions.deny`` rules and their comment are
            emitted whether or not it is given.
        deny_read_resolved_abspath: The P3-3 opt-in; also resolves the signing-key entries.
        sibling_deny_dirs: Present sibling team dirs for ``denyWrite`` (see
            :func:`_build_sandbox_block`).
        project_root: Forwarded to :func:`_build_sandbox_block` (write-root validation).
        protect_prompt_roots: Add the prompt-root ``Edit`` rules, ``denyWrite`` entries and
            comment (follow-up #8 phase 2, opt-in; off leaves the output unchanged).

    Returns:
        The settings example JSON text with the sandbox block merged in.

    Raises:
        ValueError: If ``example_text`` is not parseable as a JSON object — a corrupted
            shipped asset that must not be masked when a sandbox was requested — or if a
            write root matches a hard ban.
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
    data["sandbox"] = _build_sandbox_block(
        write_roots, deny_read, resolve_abspath=deny_read_resolved_abspath,
        sibling_deny_dirs=sibling_deny_dirs, project_root=project_root,
        protect_prompt_roots=protect_prompt_roots, protect_ledger=protect_ledger,
    )
    # Same branch as the sandbox block, never one without the other (R11): the built-in tools
    # are bound by permissions, not by the sandbox.
    permissions = data.setdefault("permissions", {})
    deny = permissions.setdefault("deny", [])
    rules = permission_deny_rules("claude")
    if protect_prompt_roots:
        rules += prompt_root_edit_rules(project_root)
    for rule in rules:
        if rule not in deny:
            deny.append(rule)
    comment = data.get("_comment")
    if isinstance(comment, list):
        extra = list(_SANDBOX_COMMENT_LINES) + _SIGNING_KEY_COMMENT_LINES
        if deny_read:
            extra += (
                _READ_EXCLUSION_ABSPATH_COMMENT_LINES
                if deny_read_resolved_abspath
                else _READ_EXCLUSION_COMMENT_LINES
            )
        if protect_prompt_roots:
            extra += PROMPT_ROOT_COMMENT_LINES
        data["_comment"] = comment + extra
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
