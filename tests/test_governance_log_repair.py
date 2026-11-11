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
    assert "Nothing written" in out and f"--apply {plan.digest}" in out


def test_apply_repairs_only_the_bad_rows_and_keeps_a_backup(tmp_path):
    log = _log(tmp_path, GOOD, SPLIT, GOOD.replace(",1,", ",4,"), ending="\r\n")
    before = log.read_bytes()
    backup = apply_repair(log, plan_repair(log).digest)
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
        apply_repair(log, digest)
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
        apply_repair(log, plan.digest)


def test_a_chained_log_is_refused(tmp_path):
    log = _log(tmp_path, GOOD + ",", SPLIT + ",", header=HEADER + ",prev_digest")
    plan = plan_repair(log)
    assert "hash-chained" in plan.refusal and not plan.fixable
    with pytest.raises(ValueError, match="hash-chained"):
        apply_repair(log, plan.digest)


def test_a_signed_malformed_row_is_refused(tmp_path):
    log = _log(tmp_path, GOOD + ",", SPLIT + ",sig==", header=HEADER + ",signature")
    plan = plan_repair(log)
    assert plan.items[0].fixed is None and "signature" in plan.items[0].reason


def test_a_well_formed_log_has_nothing_to_repair(tmp_path, capsys):
    log = _log(tmp_path, GOOD)
    assert plan_repair(log).items == []
    assert governance_logs.main(["repair", str(log)]) == 0
    assert "nothing to repair" in capsys.readouterr().out


def test_cli_preview_then_apply(tmp_path, capsys):
    log = _log(tmp_path, GOOD, SPLIT)
    assert governance_logs.main(["repair", str(log)]) == 0
    out = capsys.readouterr().out
    digest = out.rsplit("--apply ", 1)[1].strip()
    assert governance_logs.main(["repair", str(log), "--apply", "0" * 64]) == 1
    assert governance_logs.main(["repair", str(log), "--apply", digest]) == 0
    assert governance_logs.main(["check", str(tmp_path)]) == 0
