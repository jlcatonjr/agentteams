"""governance_logs repair: preview, digest-bound apply, backup, refusals (operator decision 2026-11-11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import governance_logs
from agentteams.governance_log_repair import apply_repair, plan_repair, render_plan

HEADER = "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner"
GOOD = "2026-11-01,p,1,prune,PASS,,verified,ev,security"
# The field shape: an unquoted comma inside conditions_verified, the tail starting with a space.
SPLIT = "2026-11-06,p,2,prune,PASS,cond,verified 2026-11-09: all branches absent, so nothing was deleted,ev,orch"
SPLIT2 = "2026-11-11,p,3,merge,PASS,pinned,verified: conflict resolved (both kept), 9/9 CI, merged e87bf3a0,,operator"


def _yes(plan, digest):
    return True


def _log(tmp_path: Path, *rows: str, header: str = HEADER, ending: str = "\n") -> Path:
    p = tmp_path / "security-decisions.log.csv"
    p.write_bytes((ending.join([header, *rows]) + ending).encode("utf-8"))
    return p


def test_preview_proposes_the_comma_split_fix_and_writes_nothing(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT, SPLIT2)
    before = log.read_bytes()
    plan = plan_repair(log)
    assert log.read_bytes() == before
    assert [i.record for i in plan.items] == [2, 3] and plan.fixable
    assert plan.items[0].fixed[6] == "verified 2026-11-09: all branches absent, so nothing was deleted"
    assert plan.items[1].fixed[6] == "verified: conflict resolved (both kept), 9/9 CI, merged e87bf3a0"
    assert plan.items[1].fixed[7] == "" and plan.items[1].fixed[8] == "operator"
    out = render_plan(plan)
    assert "Nothing written" in out and f"--apply {plan.digest}" in out and "as read (by position)" in out


def test_apply_repairs_only_the_bad_rows_and_keeps_a_backup(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT, GOOD.replace(",1,", ",4,"), ending="\r\n")
    before = log.read_bytes()
    backup = apply_repair(log, plan_repair(log).digest, confirm=_yes)
    assert backup.read_bytes() == before
    assert governance_logs.check_log(log) == []
    after = log.read_bytes().decode()
    assert after.startswith(HEADER + "\r\n" + GOOD + "\r\n")                      # untouched bytes kept
    assert after.endswith(GOOD.replace(",1,", ",4,") + "\r\n")
    assert '"verified 2026-11-09: all branches absent, so nothing was deleted"' in after
    assert "\r\n" in after and "\n\n" not in after


def test_apply_refuses_a_stale_or_wrong_digest(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT)
    digest = plan_repair(log).digest
    with log.open("a") as fh:
        fh.write(GOOD.replace(",1,", ",9,") + "\n")                                # file changed after preview
    before = log.read_bytes()
    with pytest.raises(ValueError, match="digest does not match"):
        apply_repair(log, digest, confirm=_yes)
    assert log.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*"))


@pytest.mark.parametrize("row, why", [
    ("2026-11-06,p,2,prune,PASS,cond,a,b,c,d", "ambiguous"),                    # extra field, no leading space
    ("2026-11-06,p,2,prune,PASS,cond, a, b,ev,orch", "ambiguous"),               # 1 extra, 2 candidates
    ("2026-11-06,p,2,prune,PASS", "fewer than the header"),
])
def test_no_guess_for_any_other_shape(tmp_path, row, why):
    log = _log(tmp_path, GOOD, row)
    plan = plan_repair(log)
    assert plan.items[0].fixed is None and why in plan.items[0].reason
    assert not plan.fixable
    with pytest.raises(ValueError, match="has a safe fix; nothing written"):
        apply_repair(log, plan.digest, confirm=_yes)


def test_a_chained_log_is_refused(tmp_path):
    log = _log(tmp_path, GOOD + ",", SPLIT + ",", header=HEADER + ",prev_digest")
    plan = plan_repair(log)
    assert "signed or chained" in plan.refusal and not plan.fixable
    with pytest.raises(ValueError, match="signed or chained"):
        apply_repair(log, plan.digest, confirm=_yes)


@pytest.mark.parametrize("col", ["signature", " signature", "prev_digest ", "key_id", "text_digest"])
def test_a_signed_chained_or_digested_log_is_refused(tmp_path, col):
    log = _log(tmp_path, GOOD + ",", SPLIT + ",", header=HEADER + "," + col)
    plan = plan_repair(log)
    assert plan.refusal and not plan.fixable


def test_a_well_formed_log_has_nothing_to_repair(tmp_path, capsys):
    log = _log(tmp_path, GOOD)
    assert plan_repair(log).items == []
    assert governance_logs.main(["repair", str(log)]) == 0
    assert "nothing to repair (check_log is clean)" in capsys.readouterr().out


def test_cli_apply_needs_an_interactive_terminal_and_a_matching_confirmation(tmp_path, capsys, monkeypatch):
    import sys

    log = _log(tmp_path, GOOD, SPLIT)
    assert governance_logs.main(["repair", str(log)]) == 0
    digest = capsys.readouterr().out.rsplit("--apply ", 1)[1].strip()
    before = log.read_bytes()
    # No TTY (an agent): refused.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert governance_logs.main(["repair", str(log), "--apply", digest]) == 1
    assert "interactive terminal" in capsys.readouterr().err and log.read_bytes() == before
    # TTY, wrong confirmation: refused.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "deadbeef")
    assert governance_logs.main(["repair", str(log), "--apply", digest]) == 1
    assert log.read_bytes() == before
    # TTY, matching confirmation: applied.
    monkeypatch.setattr("builtins.input", lambda _prompt="": digest[:8])
    assert governance_logs.main(["repair", str(log), "--apply", digest]) == 0
    assert governance_logs.main(["check", str(tmp_path)]) == 0


# --------------------------------------------------------------------------- @security HALT conditions


def test_a_halt_is_never_turned_into_a_pass(tmp_path):
    """@security's repro: the only space-led fragment is the HALT; re-joining it would shift PASS into status."""
    log = _log(tmp_path, GOOD, "2026-11-11,s,1,act-x, HALT,PASS,once verified,pending,ev,me")
    plan = plan_repair(log)
    assert plan.items[0].fixed is None and not plan.fixable


def test_a_fragment_that_would_land_in_a_decision_column_is_not_joined(tmp_path):
    log = _log(tmp_path, GOOD, "2026-11-11,s,1,act-x, extra words,PASS,c,verified,ev,me")
    plan = plan_repair(log)
    assert plan.items[0].fixed is None and "decision" in plan.items[0].reason


def test_apply_rewrites_in_place_so_a_waiting_append_lands_in_the_repaired_file(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT)
    inode = log.stat().st_ino
    apply_repair(log, plan_repair(log).digest, confirm=_yes)
    assert log.stat().st_ino == inode, "the log was replaced, not rewritten in place"
    governance_logs.append_row(log, ["2026-11-12", "p", "9", "prune", "HALT", "x", "", "e", "security"])
    assert governance_logs.check_log(log) == [] and log.read_text().rstrip().endswith("HALT,x,,e,security")


def test_line_spans_ignore_characters_splitlines_would_split_on(tmp_path):
    odd = "2026-11-01,p,1,prune,PASS,a\x85b\u2028c,verified,ev,security"
    log = _log(tmp_path, odd, SPLIT)
    apply_repair(log, plan_repair(log).digest, confirm=_yes)
    text = log.read_text(encoding="utf-8")
    assert "a\x85b\u2028c" in text and governance_logs.check_log(log) == []


def test_numbering_matches_check_log_with_blank_rows(tmp_path):
    log = _log(tmp_path, GOOD, "", SPLIT)
    plan = plan_repair(log)
    assert any(f"record {plan.items[0].record} has 10 fields" in p for p in governance_logs.check_log(log))


def test_an_open_quote_is_reported_not_called_clean(tmp_path, capsys):
    log = tmp_path / "security-decisions.log.csv"
    log.write_text(HEADER + "\n" + GOOD + "\n" + '2026-11-02,p,2,prune,PASS,"open\n', encoding="utf-8")
    plan = plan_repair(log)
    assert not plan.clean and not plan.fixable and plan.other_problems
    assert governance_logs.main(["repair", str(log)]) == 1


def test_a_failed_recheck_restores_the_original_bytes(tmp_path, monkeypatch):
    import agentteams.governance_log_repair as glr

    log = _log(tmp_path, GOOD, SPLIT)
    before = log.read_bytes()
    digest = plan_repair(log).digest
    calls = {"n": 0}
    real = glr.check_log

    def flaky(path):
        calls["n"] += 1
        return real(path) if calls["n"] == 1 else ["forced failure"]

    monkeypatch.setattr(glr, "check_log", flaky)
    with pytest.raises(ValueError, match="original restored"):
        apply_repair(log, digest, confirm=_yes)
    assert log.read_bytes() == before


def test_apply_rejects_a_malformed_digest(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT)
    with pytest.raises(ValueError, match="64-character"):
        apply_repair(log, "abc", confirm=_yes)


# --------------------------------------------------------------------------- @security re-review conditions


def test_c1_conditions_verified_never_moves(tmp_path):
    """A fragment re-joined into `conditions` would shift "verified" from evidence into conditions_verified."""
    log = _log(tmp_path, GOOD, "2026-11-11,s,1,act-x,CONDITIONAL PASS,cond, more,verified,ev,own")
    plan = plan_repair(log)
    assert plan.items[0].fixed is None and "conditions_verified" in plan.items[0].reason


def test_c1_a_rejoin_into_conditions_verified_itself_is_allowed(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT)
    assert plan_repair(log).fixable


def test_c2_a_failed_write_restores_the_original(tmp_path, monkeypatch):
    import agentteams.governance_log_repair as glr

    log = _log(tmp_path, GOOD, SPLIT)
    before = log.read_bytes()
    digest = plan_repair(log).digest
    real = glr._write_in_place
    calls = {"n": 0}

    def failing(fd, data):
        calls["n"] += 1
        if calls["n"] == 2:            # 1st: the backup; 2nd: the log rewrite
            raise OSError("disk full")
        return real(fd, data)

    monkeypatch.setattr(glr, "_write_in_place", failing)
    with pytest.raises(OSError):
        apply_repair(log, digest, confirm=_yes)
    assert log.read_bytes() == before


def test_c2_write_then_truncate_never_leaves_a_shorter_valid_file(tmp_path):
    import os

    from agentteams.governance_log_repair import _write_in_place

    p = tmp_path / "f"
    p.write_bytes(b"0123456789")
    fd = os.open(p, os.O_RDWR)
    try:
        _write_in_place(fd, b"abc")
    finally:
        os.close(fd)
    assert p.read_bytes() == b"abc"


def test_c2_the_gate_reader_takes_a_shared_lock(tmp_path, monkeypatch):
    from agentteams import governance_logs as gl
    from agentteams.cli import decision_log as dl

    if gl.fcntl is None:
        pytest.skip("no flock on this platform")
    seen = []
    real = gl.fcntl.flock

    def spy(fd, op):
        seen.append(op)
        return real(fd, op)

    monkeypatch.setattr(gl.fcntl, "flock", spy)
    out = tmp_path / "team"
    (out / "references").mkdir(parents=True)
    (out / "references" / "security-decisions.log.csv").write_text(HEADER + "\n" + GOOD + "\n", encoding="utf-8")
    dl._read_decision_rows(out)
    assert gl.fcntl.LOCK_SH in seen


def test_c3_apply_without_confirm_needs_a_terminal(tmp_path, monkeypatch):
    import sys

    log = _log(tmp_path, GOOD, SPLIT)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(ValueError, match="interactive terminal"):
        apply_repair(log, plan_repair(log).digest)
    with pytest.raises(ValueError, match="confirmation did not match"):
        apply_repair(log, plan_repair(log).digest, confirm=lambda plan, digest: False)


def test_c4_only_governance_logs_are_repaired(tmp_path):
    p = tmp_path / "other.csv"
    p.write_text(HEADER + "\n" + SPLIT + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a governance log"):
        apply_repair(p, plan_repair(p).digest, confirm=_yes)
