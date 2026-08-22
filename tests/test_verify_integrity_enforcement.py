"""test_verify_integrity_enforcement.py — --verify-integrity must heed the enforcement manifest.

Closes the 2026-08-13 remediation-log gap: the enforcement-integrity manifest
(``references/enforcement-integrity.json``) had no CLI verification path — the only
check lived inside the red-team battery, so the man page's "review the diff" guidance
had no command to run. ``_run_verify_integrity`` now re-checks the manifest whenever
one exists at the resolved root and fails on any mismatch.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil

from agentteams import integrity
from agentteams.cli.commands import _run_verify_integrity

REPO = pathlib.Path(__file__).resolve().parents[1]


def _args(root: pathlib.Path) -> argparse.Namespace:
    ns = argparse.Namespace(output=str(root), project=None, description=None)
    setattr(ns, "self", False)  # "self" collides with Namespace.__init__'s own parameter
    return ns


def _scratch_root_with_manifest(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal root carrying real enforcement modules + a freshly-written manifest."""
    root = tmp_path / "root"
    for rel in integrity.compute_digests(REPO):
        src = REPO / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    integrity.write_manifest(root)
    return root


def test_matching_manifest_passes(tmp_path) -> None:
    root = _scratch_root_with_manifest(tmp_path)
    assert _run_verify_integrity(_args(root)) == 0


def test_enforcement_module_mismatch_fails(tmp_path, capsys) -> None:
    """Tampering with a covered module after the manifest was recorded must exit 1."""
    root = _scratch_root_with_manifest(tmp_path)
    victim = root / next(iter(integrity.compute_digests(root)))
    victim.write_text(victim.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    assert _run_verify_integrity(_args(root)) == 1
    err = capsys.readouterr().err
    assert "ENFORCEMENT" in err and "MISMATCH" in capsys.readouterr().out + err


def test_absent_manifest_keeps_legacy_behavior(tmp_path) -> None:
    """No manifest, no build-log baseline: report cannot-verify, exit 0 (unchanged)."""
    root = tmp_path / "bare"
    root.mkdir()
    assert _run_verify_integrity(_args(root)) == 0


def test_source_tree_enforcement_integrity_is_currently_consistent() -> None:
    """R5/D5: the shipped agentteams source must be manifest-consistent.

    This is the invariant the `--check` integrity gate enforces (fail-closed at CI): every
    ENFORCEMENT_MODULES entry is present in references/enforcement-integrity.json and its
    digest matches. A red result here means an enforcement module was edited without
    regenerating the manifest (`--write-integrity-manifest`) — the exact D5 trap.
    """
    from agentteams.cli.generate import _verify_enforcement_integrity

    findings = _verify_enforcement_integrity()
    assert findings == [], (
        "enforcement-integrity drift: "
        + "; ".join(f.describe() for f in findings)
        + " — regenerate with `agentteams --write-integrity-manifest`."
    )
