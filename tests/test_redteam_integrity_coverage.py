"""test_redteam_integrity_coverage.py — the manifest's membership list is itself pinned.

``agentteams/integrity.py`` hashes the modules the constitution depends on, and its own
docstring is candid that a manifest stored beside the files it protects is a speed bump rather
than a boundary. What nothing covered until now is the *membership list*: no test referenced
``ENFORCEMENT_MODULES`` at all, so an entry could be deleted and every remaining hash would
still verify clean. Removing a module from the list is indistinguishable, in the manifest's
own output, from that module never having mattered.

Two directions, matching the pattern used everywhere else in this audit:

* every listed module exists on disk — a list naming a moved file verifies nothing about it;
* the phase-6 check modules are listed — they are controls, and a silent edit to one turns a
  check that fires into one that cannot.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import integrity

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The red-team modules that must be manifested, with why each is a control rather than a
#: reporter. A dict rather than a set so an entry cannot be added without stating a reason.
_REQUIRED_REDTEAM_MODULES: dict[str, str] = {
    "agentteams/redteam/checks_static.py":
        "holds F-1, F-2 and F-3; editing the scope constants silently narrows what they see",
    "agentteams/redteam/checks_report.py":
        "holds F-4, F-5 and F-6; editing the claim regex silently narrows what F-4 catches",
    "agentteams/redteam/registry.py":
        "holds the evidence normaliser F-5 compares against — widen it far enough and every "
        "probe's evidence digests to the same value, so no outcome change is ever detected",
}


def test_every_manifested_module_exists() -> None:
    """A list naming a moved or deleted file verifies nothing about it."""
    missing = [rel for rel in integrity.ENFORCEMENT_MODULES if not (REPO_ROOT / rel).exists()]
    assert not missing, (
        f"ENFORCEMENT_MODULES names files that are not on disk: {missing}. Either the module "
        f"moved and the list was not updated, or the list is describing a system that no "
        f"longer exists."
    )


def test_the_phase_six_check_modules_are_manifested() -> None:
    """The checks that catch tampering must themselves be covered against tampering."""
    listed = set(integrity.ENFORCEMENT_MODULES)
    absent = {rel: why for rel, why in _REQUIRED_REDTEAM_MODULES.items() if rel not in listed}
    assert not absent, (
        "these red-team control modules are not in ENFORCEMENT_MODULES:\n"
        + "\n".join(f"  {rel} — {why}" for rel, why in absent.items())
    )


def test_integrity_manifests_itself() -> None:
    """Self-inclusion is what makes removing an entry detectable at all."""
    assert "agentteams/integrity.py" in integrity.ENFORCEMENT_MODULES


def test_the_repository_manifest_is_current() -> None:
    """A manifest that no longer matches the modules it names is not a control.

    Regenerate with ``agentteams --write-integrity-manifest`` **after** an intended change,
    never before: regenerating first turns the manifest into a record of whatever is currently
    on disk, which is exactly what an attacker would want it to be.

    That flag was documented in three places and implemented in none until 2026-08-07, when
    probe E4 correctly flagged an intended change and the documented remedy was rejected by
    argparse. ``test_integrity_manifest_cli.py`` now asserts every command this manifest names
    is one the parser accepts.
    """
    manifest = REPO_ROOT / integrity.MANIFEST_REL_PATH
    if not manifest.exists():
        return
    findings = integrity.verify(REPO_ROOT)
    assert not findings, (
        "the enforcement-integrity manifest is stale:\n"
        + "\n".join(f.describe() for f in findings)
    )
