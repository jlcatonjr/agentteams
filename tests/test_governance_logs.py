"""agentteams.governance_logs: malformed-row detection and the validated, append-only append."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import governance_logs as gl

REPO = Path(__file__).resolve().parents[1]


def _log(tmp_path: Path, text: str, name: str = "references/security-decisions.log.csv") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    return path


def test_well_formed_log_has_no_problems(tmp_path):
    assert gl.check_log(_log(tmp_path, 'a,b,c\n1,"x, y",3\n')) == []


def test_unclosed_quote_is_reported(tmp_path):
    """The 2026-10-07 failure: an unclosed quote swallows the next row into one long record."""
    path = _log(tmp_path, 'a,b,c\n1,"oops,3\n4,5,6\n7,8,9\n')
    problems = gl.check_log(path)
    assert problems and "record 1" in problems[0]


def test_extra_column_is_reported(tmp_path):
    assert gl.check_log(_log(tmp_path, "a,b\n1,2,3\n"))


def test_append_row_appends_and_keeps_line_endings(tmp_path):
    path = _log(tmp_path, "a,b,c\r\n1,2,3\r\n")
    gl.append_row(path, {"a": "x", "b": "has, comma", "c": 'and "quotes"'})
    gl.append_row(path, ["p", "q", "r"])
    raw = path.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    rows = list(csv.reader(path.open(newline="")))
    assert rows[2] == ["x", "has, comma", 'and "quotes"'] and rows[3] == ["p", "q", "r"]


def test_append_row_adds_a_missing_final_newline(tmp_path):
    path = _log(tmp_path, "a,b\n1,2")
    gl.append_row(path, ["3", "4"])
    assert list(csv.reader(path.open(newline=""))) == [["a", "b"], ["1", "2"], ["3", "4"]]


@pytest.mark.parametrize("row", [["only-one"], {"a": "1", "zzz": "2"}, ["1", 2, "3"]])
def test_append_row_refuses_bad_rows_and_leaves_the_file_untouched(tmp_path, row):
    path = _log(tmp_path, "a,b,c\n1,2,3\n")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        gl.append_row(path, row)
    assert path.read_bytes() == before


def test_append_row_refuses_a_malformed_log(tmp_path):
    path = _log(tmp_path, 'a,b,c\n1,"oops,3\n4,5,6\n')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="malformed"):
        gl.append_row(path, ["7", "8", "9"])
    assert path.read_bytes() == before


def test_check_logs_covers_the_known_paths(tmp_path):
    _log(tmp_path, "a,b\n1,2\n", "references/agentteams-remediation-log.csv")
    _log(tmp_path, "a,b\n1,2,3\n", ".github/agents/references/conflict-log.csv")
    problems = gl.check_logs(tmp_path)
    assert len(problems) == 1 and "conflict-log.csv" in problems[0]


def test_a_team_dir_layout_is_found_too(tmp_path):
    """In a generated team the logs sit in <team>/references/, conflict-log included."""
    _log(tmp_path, "a,b\n1,2,3\n", "references/conflict-log.csv")
    assert [p.name for p in gl.log_paths(tmp_path)] == ["conflict-log.csv"]
    assert gl.check_logs(tmp_path)


def test_unclosed_quote_in_the_last_column_is_reported(tmp_path):
    """The review's case: the field count stays right, but strict parsing hits end of data."""
    problems = gl.check_log(_log(tmp_path, 'a,b,c\n1,2,"oops\n4,5,6\n'))
    assert any("strict parse" in p for p in problems)


def test_swallowed_dated_rows_are_reported_even_when_a_later_quote_closes(tmp_path):
    text = 'date,x,ev\n2026-10-07,a,"oops\n2026-10-07,b,c\n2026-10-08,d,"x"\n'
    problems = gl.check_log(_log(tmp_path, text))
    assert any("swallowed" in p for p in problems)


def test_append_row_refuses_a_value_that_would_look_like_a_swallowed_row(tmp_path):
    path = _log(tmp_path, "date,x,ev\n2026-10-07,a,b\n")
    with pytest.raises(ValueError, match="dated record"):
        gl.append_row(path, ["2026-10-08", "x", "note\n2026-10-09,looks,like a row"])


def test_concurrent_appends_neither_interleave_nor_tear(tmp_path):
    import threading
    path = _log(tmp_path, "a,b\n")
    big = "x" * 20000  # larger than the 8 KiB write buffer the review worried about
    threads = [threading.Thread(target=gl.append_row, args=(path, [str(i), big])) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = list(csv.reader(path.open(newline="")))[1:]
    assert sorted(int(r[0]) for r in rows) == list(range(12)) and all(r[1] == big for r in rows)
    assert gl.check_log(path) == []


def test_verify_integrity_exits_1_on_a_malformed_log(tmp_path):
    _log(tmp_path, 'a,b,c\n1,"oops,3\n4,5,6\n')
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--verify-integrity", "--output", str(tmp_path)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1 and "Governance logs: MALFORMED" in proc.stderr
