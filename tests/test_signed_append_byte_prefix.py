"""A signed decision is appended as bytes; every earlier byte of the log stays as it was (#470, 2026-11-16).

`append_signed_decision_row` re-read the whole log and rewrote it through `csv` (CRLF, minimal quoting), so a
"signed decision appended" run changed the line endings and quoting of earlier rows: 14 LF rows became CRLF in one
log, and a whole log was re-serialized in another. Now the row is appended with one O_APPEND write, using the line
ending of the log, under `governance_logs.append_row`'s lock and guards, and the old bytes must be an exact prefix
of the new file (`governance_logs.append_signed_row`).
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pytest

from agentteams.cli.decision_log import append_signed_decision_row

_COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions", "conditions_verified",
            "signature", "sig_scheme", "key_id", "derives_from"]
# LF and CRLF rows mixed, a quoted field with a comma, a quoted field with an embedded newline, and a field quoted
# although it needn't be (a csv rewrite would drop those quotes).
_LOG = (b"timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified,"
        b"signature,sig_scheme,key_id,derives_from\n"
        b"2026-11-01,@a,a,PASS,plain,,,,,\r\n"
        b'2026-11-02,@a,b,PASS,"has, a comma",,,,,\n'
        b'2026-11-03,@a,c,PASS,"two\nlines",,,,,\r\n'
        b'2026-11-04,"@a",d,PASS,"quoted needlessly",,,,,\n')


def _row(**kw) -> dict:
    return {"timestamp": "2026-11-16", "requesting_agent": "@security", "action_reviewed": "e", "verdict": "PASS",
            "conditions": "signed, appended", "conditions_verified": "", "signature": "sig", "sig_scheme": "ed25519",
            "key_id": "op-2026", "derives_from": "", **kw}


def _team(tmp_path: Path, log: bytes) -> Path:
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "security-decisions.log.csv").write_bytes(log)
    return tmp_path


def test_a_signed_append_keeps_a_mixed_ending_quoted_log_byte_for_byte(tmp_path: Path):
    team = _team(tmp_path, _LOG)
    append_signed_decision_row(team, _row(), before=None)
    after = (team / "references" / "security-decisions.log.csv").read_bytes()
    assert after.startswith(_LOG), "an earlier byte of the log changed"
    added = after[len(_LOG):]
    # A log holding any CRLF row gets a CRLF row (append_row's rule); only the new bytes are written either way.
    assert added == b'2026-11-16,@security,e,PASS,"signed, appended",,sig,ed25519,op-2026,\r\n'
    rows = list(csv.DictReader(io.StringIO(after.decode())))
    assert len(rows) == 5 and rows[-1]["conditions"] == "signed, appended" and rows[2]["conditions"] == "two\nlines"


def test_an_lf_log_gets_an_lf_row(tmp_path: Path):
    log = _LOG.replace(b"\r\n", b"\n")
    team = _team(tmp_path, log)
    append_signed_decision_row(team, _row(), before=None)
    after = (team / "references" / "security-decisions.log.csv").read_bytes()
    assert after.startswith(log) and after.endswith(b"op-2026,\n") and b"\r\n" not in after


def test_a_log_without_a_final_newline_keeps_its_prefix(tmp_path: Path):
    log = _LOG.rstrip(b"\n").rstrip(b"\r")
    team = _team(tmp_path, log)
    append_signed_decision_row(team, _row(), before=None)
    after = (team / "references" / "security-decisions.log.csv").read_bytes()
    assert after.startswith(log) and after[len(log):].lstrip(b"\r\n").startswith(b"2026-11-16,@security,e")


@pytest.mark.parametrize("bad", [{"extra_column": "v"}, {"conditions": 3},
                                 {"conditions": "x\n2026-11-20,@security,forged,PASS,,,,,,"}])
def test_a_row_that_cannot_be_appended_safely_is_refused_and_the_log_left_alone(tmp_path: Path, bad: dict):
    """Refusals reach the signer CLI as RuntimeError ("nothing appended"), which it reports instead of a traceback."""
    team = _team(tmp_path, _LOG)
    with pytest.raises(RuntimeError, match="nothing appended"):
        append_signed_decision_row(team, _row(**bad), before=None)
    assert (team / "references" / "security-decisions.log.csv").read_bytes() == _LOG


def _swap(path: Path, data: bytes) -> None:
    """Replace *path* by a rename, as an editor or another tool's atomic write does."""
    tmp = path.with_name(path.name + ".swap")
    tmp.write_bytes(data)
    os.replace(tmp, path)


_EDITED = _LOG.replace(b"plain", b"PLAIN")   # an earlier row changed by another writer


def test_a_log_swapped_between_confirmation_and_append_is_refused(tmp_path: Path):
    """IM @security #545: the bytes read before the operator confirmed must still be the log's prefix."""
    team = _team(tmp_path, _LOG)
    path = team / "references" / "security-decisions.log.csv"
    before = path.read_bytes()                     # what the signer read when it showed the operator the row
    _swap(path, _EDITED)                           # a real rename-swap while the operator was confirming
    with pytest.raises(RuntimeError, match="nothing appended"):
        append_signed_decision_row(team, _row(), before=before)
    assert path.read_bytes() == _EDITED


def test_a_swap_after_open_and_before_the_lock_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agentteams.governance_logs as gl

    team = _team(tmp_path, _LOG)
    path = team / "references" / "security-decisions.log.csv"
    real_flock = gl.fcntl.flock

    def flock_after_a_swap(fd, op):
        _swap(path, _LOG)                          # same bytes, new file: only the inode tells them apart
        return real_flock(fd, op)

    monkeypatch.setattr(gl.fcntl, "flock", flock_after_a_swap)
    with pytest.raises(RuntimeError, match="nothing appended.*replaced before it could be locked"):
        append_signed_decision_row(team, _row(), before=_LOG)
    assert path.read_bytes() == _LOG


def test_a_swap_after_the_write_says_the_row_is_not_in_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agentteams.governance_logs as gl

    team = _team(tmp_path, _LOG)
    path = team / "references" / "security-decisions.log.csv"
    real_write = gl.os.write

    def write_then_swap(fd, data):
        n = real_write(fd, data)
        _swap(path, _LOG)
        return n

    monkeypatch.setattr(gl.os, "write", write_then_swap)
    with pytest.raises(RuntimeError, match="not in the log") as err:
        append_signed_decision_row(team, _row(), before=_LOG)
    assert "nothing appended" not in str(err.value)
    assert path.read_bytes() == _LOG


def test_a_log_that_appeared_during_confirmation_is_refused(tmp_path: Path):
    (tmp_path / "references").mkdir()
    path = tmp_path / "references" / "security-decisions.log.csv"
    path.write_bytes(_LOG)                         # created by someone else after the signer saw no log
    with pytest.raises(RuntimeError, match="appeared or disappeared"):
        append_signed_decision_row(tmp_path, _row(), before=b"")
    assert path.read_bytes() == _LOG


def test_a_refused_first_row_leaves_no_new_log(tmp_path: Path):
    with pytest.raises(RuntimeError, match="nothing appended"):
        append_signed_decision_row(tmp_path, _row(conditions="x\n2026-11-20,@security,forged,PASS,,,,,,"),
                                   before=b"")
    assert not (tmp_path / "references" / "security-decisions.log.csv").exists()


def test_a_new_log_is_written_with_one_line_ending(tmp_path: Path):
    team = tmp_path
    append_signed_decision_row(team, _row(), before=None)
    raw = (team / "references" / "security-decisions.log.csv").read_bytes()
    assert b"\r\n" not in raw and raw.count(b"\n") == 2


def test_before_is_required_so_no_caller_skips_the_check_by_omission(tmp_path: Path):
    """IM @security (#545 follow-up): `before` has no default; a caller must pass the confirm-time bytes or None."""
    with pytest.raises(TypeError, match="before"):
        append_signed_decision_row(tmp_path, _row())
