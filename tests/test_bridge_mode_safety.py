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


# ---------------------------------------------------------------------------
# A check verdict must carry its own date
#
# bridge-check.report.md is a snapshot of one run, but its wording is
# present-tense ("artifacts ARE fresh") and it is only rewritten when
# --bridge-check runs. The copilot-cli report sat committed at PASS for a week
# while six source files drifted: the check last ran on 2026-07-22 when the
# manifest was fresh, wrote PASS, and nothing re-ran it. A reader consulting the
# file to answer "is this bridge current?" got a confident, wrong yes.
# ---------------------------------------------------------------------------


def _check_report(tmp_path: Path, *, stale: bool) -> str:
    from agentteams.bridge_sources import _run_bridge_check

    manifest = tmp_path / "bridge-manifest.json"
    manifest.write_text(
        '{"generated_at": "2026-01-01T00:00:00+00:00", "inventory_count": 2, '
        '"source_hashes": [{"path": "agents/a.md", "sha256": "aaa"}]}',
        encoding="utf-8",
    )
    rows = [{"path": "agents/a.md", "sha256": "bbb" if stale else "aaa"}]
    ok, report = _run_bridge_check(manifest_path=manifest, source_hash_rows=rows)
    assert ok is not stale
    return report


def test_check_report_records_when_it_ran(tmp_path: Path) -> None:
    """Both a PASS and a FAIL must be datable without re-running the check."""
    for stale in (False, True):
        report = _check_report(tmp_path, stale=stale)
        assert "Checked at:" in report, f"no check timestamp (stale={stale}): {report}"
        assert "Manifest generated at: 2026-01-01T00:00:00+00:00" in report, (
            f"report does not carry the manifest's own date (stale={stale}): {report}"
        )


def test_passing_report_warns_that_the_verdict_can_go_stale() -> None:
    """The dangerous direction is a PASS that outlives its conditions."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        report = _check_report(Path(tmp), stale=False)
    assert "PASS" in report
    assert "may have changed since" in report, (
        f"a PASS report does not caveat its own freshness: {report}"
    )
    assert "Re-run" in report, "the report does not say how to get a current verdict"


# ---------------------------------------------------------------------------
# Committed bridge artifacts must not carry absolute paths
#
# `1937cbc fix(bridge): relativize bridge-manifest.json's source_dir (OPSEC)`
# fixed one field in one file and left the sibling artifacts the same run writes
# still absolute: agent-inventory.md's source-file column and
# bridge-merge.report.md's per-file lines. Six tracked files under
# references/bridges/ carried the operator's home directory and username for a
# week afterwards. A one-field fix on a multi-file emission is how that happens.
# ---------------------------------------------------------------------------

_HOME_PREFIX_BYTES_RE = re.compile(rb"(/Users/|/home/|[A-Za-z]:\\Users\\)[A-Za-z0-9._-]+")

BRIDGES_DIR = REPO_ROOT / "references/bridges"


def test_rel_to_root_relativizes_inside_and_leaves_outside_alone(tmp_path: Path) -> None:
    from agentteams.bridge import rel_to_root

    root = tmp_path / "repo"
    (root / ".github/agents").mkdir(parents=True)
    assert rel_to_root(root / ".github/agents", root) == ".github/agents"
    assert rel_to_root(root / "AGENTS.md", root) == "AGENTS.md"
    # A path outside the root is information, not a leak — leave it intact.
    outside = tmp_path / "elsewhere/thing.md"
    assert rel_to_root(outside, root) == str(outside)
    # Already-relative input is returned unchanged rather than mangled.
    assert rel_to_root("AGENTS.md", root) == "AGENTS.md"


def test_no_committed_bridge_artifact_contains_an_absolute_home_path() -> None:
    """Scope: every file under references/bridges/, not just the manifest.

    The regression this guards is a fix applied to one emission site while its
    siblings keep leaking.
    """
    if not BRIDGES_DIR.exists():
        return
    offenders: dict[str, str] = {}
    for path in sorted(BRIDGES_DIR.rglob("*")):
        if not path.is_file():
            continue
        # Bytes, not text: a binary artifact would otherwise need a decode error
        # swallowed to skip it, and CH-24 forbids adding a swallow-and-continue.
        # The pattern is ASCII, so matching on bytes loses nothing.
        match = _HOME_PREFIX_BYTES_RE.search(path.read_bytes())
        if match:
            offenders[str(path.relative_to(REPO_ROOT))] = match.group(0).decode(
                "utf-8", errors="replace"
            )
    assert not offenders, (
        "bridge artifact(s) embed an absolute home path, leaking the operator's "
        f"username and directory layout: {offenders}. Render paths through "
        "agentteams.bridge.rel_to_root(path, output_root)."
    )
