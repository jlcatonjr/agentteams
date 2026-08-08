"""test_integrity_manifest_cli.py — the manifest's own instructions must be executable.

`references/enforcement-integrity.json` carries a `note` telling operators how to regenerate it.
That note named `agentteams --write-integrity-manifest` from the day it was written, and the flag
did not exist: argparse answered *"unrecognized arguments"*. The only working path was importing
the library.

It surfaced the way these things do — by doing the right thing. Probe E4 flagged an intended
change to an enforcement module, exactly as designed, and the documented recovery path was
unavailable. That is worse than a stale docstring: a control whose recovery path is broken
teaches operators to route around the control, and the next person to hit E4 under time pressure
edits the manifest by hand or deletes the probe.

The note is read from the **generated payload**, never from a copy, so the document and the
interface cannot drift apart again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import integrity
from agentteams.cli.parser import _build_parser as build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Long-option tokens appearing in prose, e.g. ``agentteams --write-integrity-manifest``.
_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]+")


def _generated_note(tmp_path: Path) -> str:
    """Return the ``note`` from a freshly generated manifest.

    Generated rather than read from disk: a test that reads the committed file would pass on a
    stale copy, which is the failure mode being guarded.
    """
    (tmp_path / "references").mkdir(parents=True, exist_ok=True)
    for module in integrity.ENFORCEMENT_MODULES:
        target = tmp_path / module
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder", encoding="utf-8")
    path = integrity.write_manifest(tmp_path)
    return json.loads(path.read_text(encoding="utf-8"))["note"]


def test_every_flag_the_manifest_names_is_accepted_by_the_parser(tmp_path: Path) -> None:
    """The regression this file exists for."""
    note = _generated_note(tmp_path)
    flags = sorted(set(_FLAG_RE.findall(note)))
    assert flags, "the manifest note names no command — the instruction went missing"

    # Registered option strings, NOT parse_args. argparse does prefix matching, so
    # parse_args(["--write-integrity-manifest"]) succeeds against a parser that only defines
    # `--write-integrity-manifest-DISABLED`. Mutation-testing this file caught exactly that:
    # renaming the real flag left all four tests green. A guard that survives deletion of the
    # thing it guards is the always-passing verification this suite exists to prevent.
    registered = {opt for action in build_parser()._actions for opt in action.option_strings}
    for flag in flags:
        assert flag in registered, (
            f"the integrity manifest instructs operators to run {flag!r}, and the parser does "
            f"not define it (nearest: "
            f"{sorted(o for o in registered if o.startswith(flag[:12])) or 'none'}). A control "
            f"whose documented recovery path does not exist teaches operators to route around "
            f"the control."
        )


def test_the_flag_actually_rewrites_the_manifest(tmp_path: Path) -> None:
    """Accepting the flag is not the same as it doing anything.

    Without this, a no-op flag would satisfy the test above and leave the recovery path just as
    broken — a verification that always passes.
    """
    from agentteams.cli.commands import _run_write_integrity_manifest

    for module in integrity.ENFORCEMENT_MODULES:
        target = tmp_path / module
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder", encoding="utf-8")

    manifest_path = tmp_path / integrity.MANIFEST_REL_PATH
    assert not manifest_path.exists()

    class _Args:
        output = str(tmp_path)
        project = None

    assert _run_write_integrity_manifest(_Args()) == 0
    assert manifest_path.exists(), "the flag was accepted and wrote nothing"
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["modules"]
    assert set(recorded) == set(integrity.ENFORCEMENT_MODULES)


def test_regeneration_reports_failure_when_verification_still_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a write that did not take must not report success.

    The command returns 0 on a clean verify. If it returned 0 unconditionally it would be a
    recovery path that always claims to have worked — the same class of defect as the missing
    flag, one layer further in.
    """
    from agentteams.cli import commands

    for module in integrity.ENFORCEMENT_MODULES:
        target = tmp_path / module
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(integrity, "verify", lambda root: ["synthetic mismatch"])

    class _Args:
        output = str(tmp_path)
        project = None

    assert commands._run_write_integrity_manifest(_Args()) == 1


def test_the_committed_manifest_is_reachable_by_its_own_instructions() -> None:
    """End to end, against the real repository: the note names a real, wired command."""
    note = json.loads(
        (REPO_ROOT / integrity.MANIFEST_REL_PATH).read_text(encoding="utf-8")
    )["note"]
    assert "--write-integrity-manifest" in note
    parser = build_parser()
    registered = {opt for action in parser._actions for opt in action.option_strings}
    assert "--write-integrity-manifest" in registered, "exact flag not defined (prefix match "
    args = parser.parse_args(["--write-integrity-manifest"])
    assert getattr(args, "write_integrity_manifest", False) is True
