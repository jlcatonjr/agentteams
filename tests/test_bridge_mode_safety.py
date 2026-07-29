"""test_bridge_mode_safety.py — the bridge must not misreport or misadvise its mode.

``references/bridge-refresh-safety.md`` is built entirely around choosing the
right bridge mode: ``--bridge-check`` is read-only, ``--bridge-merge`` re-renders
only fenced regions, and ``--bridge-refresh`` overwrites target entry files
unconditionally. Its origin is the 2026-05-27 incident, where a refresh against
unfenced user-authored files destroyed their content.

Two defects in the operator-facing surface undercut that policy, both found while
running a merge under it:

1. The run banner printed only ``check`` or ``generate``. A ``--bridge-merge`` run
   displayed as ``generate`` and a ``--bridge-refresh`` was indistinguishable from
   a bare invocation, so the banner could not confirm the intended mode ran.
2. When ``--bridge-merge`` correctly skipped unfenced files, the run advised
   "Pass ``--bridge-refresh``" — recommending the destructive mode for exactly the
   files the policy protects, and calling it "recommended when bridge state is
   incomplete or stale", which is when an operator is most likely to comply.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.bridge import skip_notice
from agentteams.cli.commands import _bridge_mode_label

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PY = REPO_ROOT / "agentteams/bridge.py"


def test_every_mode_is_distinguishable_in_the_banner() -> None:
    """No two modes may render the same label."""
    labels = {
        "check": _bridge_mode_label(True, False, False),
        "refresh": _bridge_mode_label(False, True, False),
        "merge": _bridge_mode_label(False, False, True),
        "generate": _bridge_mode_label(False, False, False),
    }
    assert len(set(labels.values())) == 4, f"modes are not distinguishable: {labels}"
    for mode, label in labels.items():
        assert mode in label, f"{mode} mode renders as {label!r}, which does not name it"


def test_refresh_label_states_that_it_overwrites() -> None:
    """The destructive mode must say so where the operator reads it."""
    label = _bridge_mode_label(False, True, False)
    assert "overwrite" in label.lower(), (
        f"refresh label {label!r} does not warn that it overwrites target entry files"
    )


def test_check_label_is_not_confused_with_a_writing_mode() -> None:
    """check_only wins over any other flag combination."""
    assert _bridge_mode_label(True, True, True) == "check"


def test_merge_skip_notice_never_recommends_refresh() -> None:
    """A skip under --bridge-merge is the contract, not a shortfall.

    Tests the notice text itself rather than grepping ``bridge.py``: a source-text
    assertion flagged an explanatory *comment* that named the flag, which is the
    brittleness that made ``skip_notice`` a separate function in the first place.
    """
    notice = skip_notice(6, merge_only=True)

    # Any mention of the destructive flag must be a warning against it. Split on the
    # prohibition and assert the flag appears only after it.
    warning_marker = "Do NOT reach for --bridge-refresh"
    assert warning_marker in notice, f"merge notice does not warn against refresh: {notice}"
    before_warning = notice.split(warning_marker)[0]
    assert "--bridge-refresh" not in before_warning, (
        f"merge notice names --bridge-refresh before warning against it: {notice}"
    )
    assert "recommend" not in before_warning.lower(), (
        f"merge notice recommends something before the warning: {notice}"
    )
    assert "AGENTTEAMS-BRIDGE" in notice, (
        "the merge notice should name the missing fence — that is the reason for the skip"
    )
    assert "6" in notice, "the notice should report how many files were skipped"


def test_non_merge_skip_notice_gates_refresh_behind_the_safety_reference() -> None:
    """Outside merge mode, refresh may be mentioned — but never bare."""
    notice = skip_notice(6, merge_only=False)
    assert "--bridge-merge" in notice, "merge should be offered as the non-destructive option"
    assert "bridge-refresh-safety.md" in notice, (
        f"refresh is named without pointing at the mandatory Pre-Flight reference: {notice}"
    )
    assert "overwrites target entry files unconditionally" in notice, (
        f"refresh is named without stating that it is destructive: {notice}"
    )


def test_the_two_notices_are_different() -> None:
    """The regression this guards is the two cases sharing one message."""
    assert skip_notice(1, merge_only=True) != skip_notice(1, merge_only=False)


def test_notice_is_reached_through_run_bridge() -> None:
    """Guard the wiring, not just the helper.

    A pure function that nothing calls would pass every test above while the live
    run still emitted the old advice.
    """
    source = BRIDGE_PY.read_text(encoding="utf-8")
    assert re.search(r"result\.notices\.append\(\s*skip_notice\(", source), (
        "run_bridge does not build its skip notice via skip_notice(); the tested "
        "helper is not the code path operators see"
    )
