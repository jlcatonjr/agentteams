"""toolchain_group_commands.py — the CLI verbs for the first-party toolchain group.

``--sign-toolchain-group FILE`` (operator-only), ``--verify-toolchain-group`` and ``--verify-toolchain-pin LOCKFILE``
(both read-only). The logic lives in :mod:`agentteams.cli.toolchain_group`; this module only resolves the team dir,
project root, governance dir and team id from the CLI arguments.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentteams.cli import toolchain_group as tg
from agentteams.cli.commands_output import _resolve_output_dir


def _read_brief(args: argparse.Namespace, root: Path) -> tuple[dict | None, str | None]:
    """``(brief, problem)``: the brief (``--description``, else the project's own brief as fleet discovery finds it,
    :func:`agentteams.memory_index_surfaces.resolve_brief`), or None when there is none; *problem* says why a brief
    that was named or found could not be used."""
    brief = getattr(args, "description", None)
    if not brief:
        from agentteams.memory_index_surfaces import brief_candidate, resolve_brief

        found = resolve_brief(root)
        if found is None and (candidate := brief_candidate(root)) is not None:
            found = candidate  # found but unusable: parsed below, so the problem is reported, never skipped
        brief = str(found) if found else None
    if not brief:
        return None, None
    try:
        description = json.loads(Path(brief).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"the brief {brief} cannot be read ({exc})"
    if not isinstance(description, dict):
        return None, f"the brief {brief} is not a JSON object"
    return description, None


def _brief(args: argparse.Namespace, root: Path) -> dict | None:
    """The brief for the team-id lookup: ``--description``, else :func:`resolve_brief` (strict: a brief that is
    unreadable, a symlink, outside the project or names another project is not used), else None."""
    brief = getattr(args, "description", None)
    if not brief:
        from agentteams.memory_index_surfaces import resolve_brief

        found = resolve_brief(root)
        brief = str(found) if found else None
    if not brief:
        return None
    try:
        description = json.loads(Path(brief).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return description if isinstance(description, dict) else None


def _team_id(args: argparse.Namespace, root: Path) -> str | None:
    """``--team-id``, else the brief's ``team_id``, else its slugified ``project_name`` (as ``analyze`` derives it)."""
    if getattr(args, "team_id", None):
        return str(args.team_id).strip()
    description = _brief(args, root)
    if description is None:
        return None
    from agentteams.analyze import _slugify

    return description.get("team_id") or _slugify(str(description.get("project_name") or "")).lstrip("-") or None


def _resolve(args: argparse.Namespace, ungoverned: str | None = None) -> tuple[Path, Path, Path, tuple[Path, ...]] | None:
    """Return ``(team_dir, project_root, governance_dir, halt_dirs)``, or None with a message (*ungoverned* replaces
    the toolchain-group wording of the not-governed one)."""
    from agentteams.cli.decision_log import _enforce_decision_signing
    from agentteams.cli.s10_exempt_gate import _project_root_of, _surface_dirs, governance_team_dir

    team_dir = _resolve_output_dir(args)
    root = _project_root_of(team_dir)
    if root is None:
        print(f"Error: {team_dir} is not a team dir in a default layout (e.g. --output .github/agents).",
              file=sys.stderr)
        return None
    try:
        governance = governance_team_dir(team_dir)
        governed = _enforce_decision_signing(governance)
    except RuntimeError as exc:
        print(f"Error: the signing switch cannot be read: {exc}", file=sys.stderr)
        return None
    if not governed:
        print(f"Error: {governance} is not governed (decision signing off), so "
              + (ungoverned or "a render never applies a toolchain group here; the brief's s10_cooldown_exempt_repos "
                 "applies instead."), file=sys.stderr)
        return None
    try:
        return team_dir, root, governance, (team_dir, *_surface_dirs(root))
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None


def _member_filter(governance: Path, halt_dirs: tuple[Path, ...]):
    """The render gate's per-member HALT filter, bound to these dirs (C-2)."""
    from agentteams.cli.s10_exempt_gate import drop_halted_members

    return lambda repos: drop_halted_members(repos, (governance, *halt_dirs))


def run_toolchain_group(args: argparse.Namespace) -> int:
    """Dispatch the toolchain-group verb named by *args*.

    Args:
        args: Parsed CLI namespace.

    Returns:
        0 signed / verified; 1 on any failure (fail-closed), including an ungoverned workspace, where a render never
        applies the group. ``--verify-toolchain-pin`` runs in every tier and exits 0 or 1 only (#555).
    """
    if getattr(args, "sign_toolchain_group", None):
        return tg.sign_group_file(Path(args.sign_toolchain_group), _resolve_output_dir(args),
                                  confirm_sha256=getattr(args, "confirm_review_sha256", None),
                                  allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))
    if getattr(args, "verify_toolchain_pin", None):
        return _verify_pin_command(args)
    resolved = _resolve(args)
    if resolved is None:
        return 1
    _team_dir, root, governance, halt_dirs = resolved
    try:
        present = tg.group_file_present(root)
    except tg.ToolchainGroupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not present:
        print(f"No {tg.GROUP_FILE_REL} in {root}: no toolchain group (the cooldown applies to every release).")
        return 1
    team_id = _team_id(args, root)
    if not team_id:
        print("Error: name this workspace's team id with --team-id (or --description <brief>).", file=sys.stderr)
        return 1
    return tg.verify_group_command(root, governance, team_id, halt_dirs, member_filter=_member_filter(governance,
                                                                                                      halt_dirs))


def _pin_sources(args: argparse.Namespace, team_dir: Path, root: Path, pins: dict[str, str],
                 own: str | None) -> tuple[set[str], set[str], list[str]] | None:
    """``(repos S-9 checks, those named explicitly, problems)``; None after a printed refusal.

    @security ruling on #555 (2026-11-20): sources only ever add repos, never remove one. They are pins owned by this
    workspace's GitHub owner, the brief's ``s10_cooldown_exempt_repos``, ``--pin-repos``, and the verified group
    when the workspace is governed and has a group file.
    """
    # _enforce_decision_signing stays private: decision_log.py is at the CH-07 margin (974 lines), and _resolve imports it
    # the same way.
    from agentteams.cli.decision_log import _enforce_decision_signing
    from agentteams.cli.s10_exempt_gate import governance_team_dir, surface_dirs

    repos: set[str] = set()
    named: set[str] = set()  # named by the brief, --pin-repos or the group: never excluded as the own repo
    problems: list[str] = []
    locked = {r.lower() for r in pins.values()}
    if own:
        owner = own.split("/", 1)[0].lower()
        repos |= {r.lower() for r in pins.values() if r.split("/", 1)[0].lower() == owner}
    brief, problem = _read_brief(args, root)
    if problem:  # a brief that names repos to check must never silently add nothing (@security, #555)
        print(f"Error: {problem}, so its s10_cooldown_exempt_repos cannot be read (fail-closed).", file=sys.stderr)
        return None
    hint = (brief or {}).get("s10_cooldown_exempt_repos")
    if hint is not None:
        if not isinstance(hint, list) or not all(isinstance(r, str) and r.strip() for r in hint):
            print("Error: the brief's s10_cooldown_exempt_repos must be a list of owner/repo strings (fail-closed).",
                  file=sys.stderr)
            return None
        named |= {r.strip().lower() for r in hint}
    for repo in filter(None, (r.strip() for r in (getattr(args, "pin_repos", None) or "").split(","))):
        named.add(repo.lower())
        if repo.lower() not in locked:
            problems.append(f"--pin-repos names {repo}, which {args.verify_toolchain_pin} does not pin")
    try:
        present = tg.group_file_present(root)
        if present:
            governance = governance_team_dir(team_dir)
            governed = _enforce_decision_signing(governance)
            halt_dirs = (team_dir, *surface_dirs(root)) if governed else ()
    except RuntimeError as exc:  # ToolchainGroupError included: an unreadable group file, team dir or switch
        print(f"Error: {exc} (fail-closed)", file=sys.stderr)
        return None
    if present:
        if governed:
            team_id = _team_id(args, root)
            if not team_id:
                print("Error: name this workspace's team id with --team-id (or --description <brief>).",
                      file=sys.stderr)
                return None
            result = tg.load_group(root, governance, team_id=team_id, halt_dirs=halt_dirs, consume=False)
            if result.problem:
                tg.report(result)
                return None
            named |= {r.lower() for r in result.repos}
    return repos | named, named, problems


def _warn_on_exemption_halts(repos: set[str], team_dir: Path, root: Path) -> bool:
    """Print a ``::warning`` for each checked repo whose cooldown exemption is HALTed; never drops a pin (#555).

    Returns False after a printed refusal: a team dir or decisions log that cannot be read could hide a HALT, and
    :func:`exemption_halt` reports that the same way as a HALT (3.14, fail-closed).
    """
    from agentteams.cli.decision_log import _read_decision_rows  # raises when the log is there but unreadable
    from agentteams.cli.s10_exempt_gate import exemption_halt, governance_team_dir, surface_dirs

    try:
        dirs = tuple(dict.fromkeys((governance_team_dir(team_dir), team_dir, *surface_dirs(root))))
        for halt_dir in dirs:
            _read_decision_rows(halt_dir)
    except RuntimeError as exc:
        print(f"Error: {exc}, so its HALTs cannot be read (fail-closed).", file=sys.stderr)
        return False
    for repo in sorted(repos):
        exc = exemption_halt(repo, dirs)
        if exc is not None:
            print(f"::warning title=S-10 cooldown exemption revoked::{repo}: {exc}. Its pin is still checked "
                  "here; the 14-day cooldown applies, and exit 0 does not clear it.", file=sys.stderr)
    return True


def _verify_pin_command(args: argparse.Namespace) -> int:
    """``--verify-toolchain-pin LOCKFILE``: Rule S-9's signed-tag check, in every tier (#555).

    Exit 0 only when at least one pin was checked and every checked pin verifies; 1 otherwise. There is no exit 2.
    """
    from agentteams.cli.s10_exempt_gate import project_root_of

    team_dir = _resolve_output_dir(args)
    root = project_root_of(team_dir)
    if root is None:
        print(f"Error: {team_dir} is not a team dir in a default layout (e.g. --output .github/agents).",
              file=sys.stderr)
        return 1
    if not getattr(args, "allowed_signers", None):
        print("Error: --verify-toolchain-pin needs --allowed-signers <file naming the release key>.", file=sys.stderr)
        return 1
    lockfile = Path(args.verify_toolchain_pin)
    try:
        pins = tg.locked_pins(lockfile)
    except (OSError, UnicodeDecodeError, tg.ToolchainLockError) as exc:
        print(f"Error: cannot read {lockfile}: {exc}", file=sys.stderr)
        return 1
    own = tg.workspace_repo(root)
    collected = _pin_sources(args, team_dir, root, pins, own)
    if collected is None:
        return 1
    repos, named, problems = collected
    # Only an owner-derived pin is excluded as the own repo: origin is not signed, so a consumer whose origin claims
    # to be a named repository must still verify that pin (IM-da review of #598).
    if own and own.lower() in repos and own.lower() not in named:
        repos.discard(own.lower())
        for name in (n for n, r in pins.items() if r.lower() == own.lower()):
            print(f"  -  {name}={own}: this workspace's own repository, not checked.")
    if not _warn_on_exemption_halts(repos, team_dir, root):
        return 1
    checked: list[str] = []
    try:
        problems += tg.verify_pins(lockfile, sorted(repos), Path(args.allowed_signers), checked=checked)
    except (OSError, UnicodeDecodeError, tg.ToolchainLockError) as exc:
        print(f"Error: cannot read {lockfile}: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"::error title=Toolchain pin not verified::{problem}", file=sys.stderr)
    if problems:
        return 1
    if not checked:
        print(f"Error: {lockfile} pins no first-party repository (this owner's, the brief's s10_cooldown_exempt_repos, "
              "--pin-repos or the toolchain group), so nothing was verified (fail-closed).", file=sys.stderr)
        return 1
    print(f"{len(checked)} pin(s) in {lockfile} verify against the release key: {', '.join(checked)}. "
          "This checks signed tags, not release age: it never clears the cooldown.")
    return 0


__all__ = ["run_toolchain_group"]
