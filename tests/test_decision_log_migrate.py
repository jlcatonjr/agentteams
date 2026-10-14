"""``--migrate-decision-log``: a legacy team decisions log gains the signing columns, nothing else.

Pins plan ``decision-log-migration`` Revision 1: the header becomes a superset (legacy columns in
place, the missing ``SIGNED_DECISION_COLUMNS`` appended, never ``prev_digest``) that still classifies;
data lines stay byte-identical; the run is backed up, idempotent and refuses malformed headers and
laundering rows; the revocation of live unsigned authorizing rows is listed and needs ``--yes``; and
afterwards a signed row appends while the gate reads the legacy rows as unsigned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_team
from agentteams.cli import decision_log_migrate as dlm
from agentteams.cli.decision_log import (
    SIGNED_DECISION_COLUMNS,
    _read_decision_rows,
    _security_decision_schema_kind,
    append_signed_decision_row,
    sign_decision_row,
)
from agentteams.cli.parser import _build_parser
from agentteams.cli.parser_validate import _validate_option_combinations
from agentteams.cli.security_gate import check_clearance

_KEY = "decision-log-migrate-test-key"
_LEGACY = "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified,consumed"
_ROWS = [
    "2026-05-21T00:00:00Z,security,prune,PASS,,verified,no",                          # line 2: revoked
    '2026-05-22T00:00:00Z,security,overwrite,PASS,"a, quoted ""cond""",verified,yes',  # line 3: consumed
    "2026-05-23T00:00:00Z,security,restore-backup,HALT,,,",                            # line 4: restrictive
    "2026-05-24T00:00:00Z,security,restore-backup,HALT-RETRACTED,why,verified,yes",    # line 5: revoked
    "2026-05-25T00:00:00Z ,security,stale-remediate,CONDITIONAL PASS,x,pending,",      # line 6: revoked
]


def _legacy_bytes(ending: str = "\n") -> bytes:
    return (ending.join([_LEGACY, *_ROWS]) + ending).encode("utf-8")


def _team(tmp_path: Path, data: bytes) -> Path:
    """A Claude team dir whose decisions log holds exactly *data*."""
    out = tmp_path / "proj" / ".claude" / "agents"
    (out / "references").mkdir(parents=True)
    (out / dlm.DECISION_LOG_REL).write_bytes(data)
    return out


def _run(out: Path, *extra: str) -> int:
    return build_team.main(["--migrate-decision-log", "--framework", "claude", "--output", str(out), *extra])


def _log(out: Path) -> bytes:
    return (out / dlm.DECISION_LOG_REL).read_bytes()


def _backups(out: Path) -> list[Path]:
    root = out / ".agentteams-backups"
    return sorted(root.iterdir()) if root.is_dir() else []


@pytest.fixture(autouse=True)
def _no_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTTEAMS_DECISION_SIGNING_KEY", raising=False)


# --- the header ----------------------------------------------------------------------------------


def test_header_is_a_superset_that_still_classifies(tmp_path: Path) -> None:
    out = _team(tmp_path, _legacy_bytes())
    assert _run(out, "--yes") == 0
    header = _log(out).split(b"\n", 1)[0].decode()
    columns = header.split(",")
    legacy = _LEGACY.split(",")
    assert columns[: len(legacy)] == legacy                       # legacy columns kept in place
    assert columns[len(legacy):] == dlm.missing_signing_columns(legacy)
    assert set(SIGNED_DECISION_COLUMNS) - {"prev_digest"} <= set(columns)
    assert "prev_digest" not in columns                           # chaining stays opt-in
    assert {"signature", "sig_scheme", "key_id", "derives_from"} <= set(columns)
    assert _security_decision_schema_kind(columns) == "legacy"
    read = _read_decision_rows(out)
    assert read is not None and len(read[0]) == len(_ROWS)
    assert all(row[c] == "" for row in read[0] for c in dlm.missing_signing_columns(legacy))


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_data_lines_are_byte_identical(tmp_path: Path, ending: str) -> None:
    before = _legacy_bytes(ending)
    out = _team(tmp_path, before)
    assert _run(out, "--yes") == 0
    after = _log(out)
    old_header, old_rest = before.split(ending.encode(), 1)
    new_header, new_rest = after.split(ending.encode(), 1)
    assert new_rest == old_rest
    assert new_header.startswith(old_header + b",")
    assert b"\r" not in new_header


def test_second_run_is_a_no_op(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _team(tmp_path, _legacy_bytes())
    assert _run(out, "--yes") == 0
    once, backups = _log(out), _backups(out)
    capsys.readouterr()
    assert _run(out, "--yes") == 0
    assert _log(out) == once and _backups(out) == backups
    assert "already current" in capsys.readouterr().out


def test_backup_holds_the_original_log(tmp_path: Path) -> None:
    before = _legacy_bytes()
    out = _team(tmp_path, before)
    assert _run(out, "--yes") == 0
    [backup] = _backups(out)
    assert (backup / dlm.DECISION_LOG_REL).read_bytes() == before


# --- the revocation ------------------------------------------------------------------------------


def test_dry_run_lists_revoked_rows_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _legacy_bytes()
    out = _team(tmp_path, before)
    assert _run(out, "--dry-run") == 0
    text = capsys.readouterr().out
    assert "line 2: PASS prune" in text
    assert "line 5: HALT-RETRACTED restore-backup" in text
    assert "line 6: CONDITIONAL PASS stale-remediate" in text
    assert "overwrite" not in text            # consumed
    assert "line 4" not in text               # a HALT restricts; it is not revoked
    assert _log(out) == before and _backups(out) == []


def test_dry_run_json_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _team(tmp_path, _legacy_bytes())
    capsys.readouterr()
    assert _run(out, "--dry-run", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "migrate-decision-log" and report["current"] is False
    assert [(r["line"], r["action"]) for r in report["revoked"]] == [
        (2, "prune"), (5, "restore-backup"), (6, "stale-remediate")]
    assert report["header_after"] == _LEGACY.split(",") + report["added_columns"]


def test_real_run_requires_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _legacy_bytes()
    out = _team(tmp_path, before)
    assert _run(out) == 1
    captured = capsys.readouterr()
    assert "line 2: PASS prune" in captured.out and "--yes" in captured.err
    assert _log(out) == before and _backups(out) == []


# --- refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize("data", [
    b"",                                                    # no header
    b"foo,bar\n1,2\n",                                      # unknown schema
    b"\xef\xbb\xbf" + _legacy_bytes(),                      # byte-order mark
    (_LEGACY + ",verdict\n").encode(),                      # duplicate column
    (_LEGACY + ",\n").encode(),                             # empty column name
], ids=["empty", "unknown", "bom", "duplicate", "empty-name"])
def test_malformed_or_unknown_header_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                data: bytes) -> None:
    out = _team(tmp_path, data)
    assert _run(out, "--yes") == 1
    assert "[MIGRATE-DECISION-LOG] refused" in capsys.readouterr().err
    assert _log(out) == data and _backups(out) == []


def test_missing_log_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "proj" / ".claude" / "agents"
    out.mkdir(parents=True)
    assert _run(out, "--yes") == 1
    assert "no regular decisions log" in capsys.readouterr().err


def test_a_value_past_the_old_header_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = (_LEGACY + "\n2026-05-21T00:00:00Z,security,prune,PASS,,verified,no,forged-sig\n").encode()
    out = _team(tmp_path, data)
    assert _run(out, "--yes") == 1
    assert "no laundering" in capsys.readouterr().err
    assert _log(out) == data and _backups(out) == []


def test_flag_exclusivity(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_parser()
    for flag in ("--update", "--no-backup", "--adopt-orphans"):
        argv = ["--migrate-decision-log", flag] + (["--update"] if flag == "--adopt-orphans" else [])
        with pytest.raises(SystemExit):
            _validate_option_combinations(parser, parser.parse_args(argv))
        assert "--migrate-decision-log and" in capsys.readouterr().err


# --- after the migration -------------------------------------------------------------------------


def test_signed_row_appends_and_legacy_rows_read_as_unsigned(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    out = _team(tmp_path, _legacy_bytes())
    signed = {"date": "2026-10-13", "action_reviewed": "prune", "verdict": "PASS", "conditions": "",
              "conditions_verified": "verified", "author": "security"}
    signed["signature"] = sign_decision_row(signed, key=_KEY)

    allowed, _ = check_clearance(out, action="prune")
    assert allowed                                           # unsigned legacy PASS clears pre-migration
    with pytest.raises(RuntimeError, match="lacks signing column"):
        append_signed_decision_row(out, signed)

    assert _run(out, "--yes") == 0
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", _KEY)
    allowed, reason = check_clearance(out, action="prune")
    assert not allowed and "no signature" in reason           # the intended revocation

    append_signed_decision_row(out, signed)
    allowed, reason = check_clearance(out, action="prune")
    assert allowed, reason
    read = _read_decision_rows(out)
    assert read is not None and read[0][-1]["signature"] == signed["signature"]
