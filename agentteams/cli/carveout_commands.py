"""carveout_commands.py — the CLI for the carve-out registry (:mod:`agentteams.carveouts`).

``--sign-carveouts`` (operator only), ``--sync-carveouts``, ``--verify-carveouts`` and ``--check-clearance``, and the
post-build hook that keeps the blocks current after every generate and ``--update``.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

from agentteams.carveouts import (
    _KEY_ID_RE, REGISTRY_REL, CarveoutError, _git, decision_dir,
    load_registry, project, registry_path, signature_problem, signed_fields, signer, validate_registry,
    verify)
from agentteams.carveout_checks import team_dir_problem
from agentteams.cli.clearance_binding import check_file_clearance, clearance_action



def _frozen(root: Path) -> object:
    from agentteams.memory_index_surfaces import resolve_brief

    found = resolve_brief(root)
    if found is None:
        return None
    try:
        return json.loads(found.read_text(encoding="utf-8")).get("frozen_surfaces")
    except (OSError, ValueError):
        return None


def _print_report(report, *, dry_run: bool) -> bool:
    """Print *report*'s outcomes; return True when one was refused or, with *dry_run*, is out of date."""
    failing = False
    for o in report.outcomes:
        change = o.status in ("updated", "added", "removed")
        mark = {"refused": "✗", "skipped": "·", "current": "✓"}.get(o.status, "↻")
        when = " (would be)" if dry_run and change else ""
        print(f"  {mark}  carve-outs: {o.path}: {o.status}{when}{': ' + o.detail if o.detail else ''}")
        failing = failing or o.status == "refused" or (dry_run and change)
    return failing


def after_build(root: Path, *, frozen: object = None, write: bool = True) -> bool:
    """Keep the carve-out blocks current after a generate or ``--update`` (never fails the build).

    Args:
        root: The project root.
        frozen: The brief's ``frozen_surfaces``.
        write: Write; ``False`` only reports drift.

    Returns:
        True when a block is out of date or refused, or the registry is refused.
    """
    try:
        report = project(root, frozen=frozen, write=write)
    except (CarveoutError, ValueError, OSError, UnicodeDecodeError) as exc:
        print(f"  ✗  Carve-outs not rendered: {exc}", file=sys.stderr)
        return True
    quiet = all(o.status in ("current", "skipped") for o in report.outcomes)
    if not quiet:
        registry = load_registry(root)
        if registry is not None:
            print(f"  ·  carve-outs: {signer(root, registry)}")
    return False if quiet else _print_report(report, dry_run=not write)


def run_sync_carveouts(project_dir: str | None, *, dry_run: bool = False) -> int:
    """``--sync-carveouts``: render the registry into every enabled entry file of the project it runs in.

    Args:
        project_dir: The ``--project`` value (must be the working directory: a write into another repository needs
            that repository's own run, C-5).
        dry_run: Report what would change; write nothing.

    Returns:
        0; 1 when the registry or a copy is refused, *project_dir* is another directory, or (with *dry_run*) a copy
        is out of date.
    """
    root = Path(project_dir or ".").resolve()
    if root != Path.cwd().resolve():
        print(f"  ✗  --sync-carveouts writes only the project it runs in; run it from {root}.", file=sys.stderr)
        return 1
    try:
        report = project(root, frozen=_frozen(root), write=not dry_run)
    except (CarveoutError, ValueError) as exc:
        print(f"  ✗  carve-outs not rendered: {exc}", file=sys.stderr)
        return 1
    return 1 if _print_report(report, dry_run=dry_run) else 0


def run_verify_carveouts(project_dir: str | None) -> int:
    """``--verify-carveouts``: report every problem with the registry and its copies. Writes nothing.

    Args:
        project_dir: The ``--project`` value (else the working directory).

    Returns:
        0 when everything is in order; 1 otherwise.
    """
    root = Path(project_dir or ".").resolve()
    result = verify(root, frozen=_frozen(root))
    print(f"  workspace ids: {', '.join(sorted(result.workspace_ids)) or 'none derivable'}")
    try:
        registry = load_registry(root)
    except CarveoutError:
        registry = None
    if registry is not None and isinstance(registry.get("signature"), dict):
        print(f"  {signer(root, registry)}")
    for e in result.active:
        print(f"  ✓  binding: {e['id']} (expires {e['expires']}; lifts {'; '.join(e['relaxes'])})")
    for p in result.problems:
        print(f"  ✗  {p}", file=sys.stderr)
    if result.ok:
        print("  ✓  carve-outs: registry signed, committed and valid; every copy current" if registry_path(root).exists()
              else "  ✓  carve-outs: no registry, and no copy left behind")
    return 0 if result.ok else 1


def run_sign_carveouts(project_dir: str | None, key_id: str | None, *, confirm_sha256: str | None = None,
                       allow_checkout: bool = False) -> int:
    """``--sign-carveouts --key-id ID``: the operator signs the registry with the operator Ed25519 key.

    Same order as ``--sign-decision``: the registry is validated, then the preflight (key env, key file, the signing
    code's integrity, install location), the review digest and its confirmation; only then is the key read. The
    signature is verified against ``<decision_log>/references/authorized-verify-keys/<key_id>.pub.pem`` before the
    registry is rewritten. The operator then commits the registry.

    Args:
        project_dir: The ``--project`` value (must be the working directory).
        key_id: The verify key's file stem.
        confirm_sha256: ``--confirm-review-sha256`` (no terminal).
        allow_checkout: ``--allow-checkout-signing``.

    Returns:
        0 when signed and written; 1 otherwise.
    """
    from agentteams.cli import operator_signing as ops
    from agentteams.cli.signed_ledger import ed25519_sign

    root = Path(project_dir or ".").resolve()
    if root != Path.cwd().resolve():
        print(f"Error: --sign-carveouts signs only the project it runs in; run it from {root}.", file=sys.stderr)
        return 1
    if not key_id or not _KEY_ID_RE.match(key_id):
        print("Error: --sign-carveouts needs --key-id <verify-key file stem>.", file=sys.stderr)
        return 1
    try:
        registry = load_registry(root)
    except CarveoutError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if registry is None:
        print(f"Error: there is no {REGISTRY_REL} to sign.", file=sys.stderr)
        return 1
    problems = validate_registry(registry, today=_dt.date.today())
    if problems:
        print("Error: the registry is invalid; nothing was signed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    team_dir = decision_dir(root, registry)
    problem = team_dir_problem(root, registry)
    if problem:
        print(f"Error: {problem.detail}", file=sys.stderr)
        return 1
    committed = _git(root, "show", f"HEAD:{REGISTRY_REL}")
    try:
        last = json.loads(committed.stdout).get("sequence", 0) if committed.returncode == 0 else 0
    except ValueError:
        last = 0
    if isinstance(last, int) and registry["sequence"] <= last:
        print(f"Error: sequence must be higher than the committed one ({last}); raise it, then sign.", file=sys.stderr)
        return 1
    body = {k: v for k, v in registry.items() if k != "signature"}
    preflight = ops._signing_preflight(ops._project_roots(team_dir), allow_checkout)
    if preflight is None:
        return 1
    keyfile, _overridden = preflight
    print(f"  Signing {REGISTRY_REL} ({len(registry['carveouts'])} entr{'y' if len(registry['carveouts']) == 1 else 'ies'}):")
    for e in registry["carveouts"]:
        print(f"    {e['id']} [{e['status']}] {', '.join(e['workspaces'])} lifts {'; '.join(e['relaxes'])} "
              f"until {e['expires']}")
    print(f"  verify key      : {team_dir.name}/references/authorized-verify-keys/{key_id}.pub.pem")
    if not ops._confirm(ops.review_digest("carveouts", body, team_dir, key_id), confirm_sha256):
        return 1
    private_pem = ops._read_operator_private_key(keyfile)
    if private_pem is None:
        return 1
    try:
        value = ed25519_sign(private_pem, signed_fields(body, key_id))
    except RuntimeError as exc:
        print(f"Error: cannot sign: {exc}", file=sys.stderr)
        return 1
    signed = {**body, "signature": {"scheme": "ed25519", "key_id": key_id, "value": value}}
    problem = signature_problem(root, signed)
    if problem:
        print(f"Error: refusing to write: {problem.detail}", file=sys.stderr)
        return 1
    registry_path(root).write_text(json.dumps(signed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  ✓  signed {REGISTRY_REL} with key {key_id}. Commit it, then run agentteams --sync-carveouts.")
    return 0


def run_check_clearance(scope: str, clearance_file: str | None, output: str | None) -> int:
    """``--check-clearance SCOPE --clearance-file PATH``: is there a verified PASS bound to PATH's sha256?

    Args:
        scope: The clearance scope (an action id).
        clearance_file: The file the clearance must be bound to.
        output: The team directory holding ``references/security-decisions.log.csv`` (default: the working
            directory).

    Returns:
        0 when cleared; 1 when not; 2 when the arguments are missing.
    """
    if not clearance_file:
        print("  ✗  --check-clearance needs --clearance-file PATH", file=sys.stderr)
        return 2
    path = Path(clearance_file)
    ok, reason = check_file_clearance(Path(output or ".").resolve(), scope, path)
    if ok:
        print(f"  ✓  cleared: {clearance_action(scope, path)} (now use exactly these bytes)")
        return 0
    print(f"  ✗  not cleared: {reason}", file=sys.stderr)
    return 1
