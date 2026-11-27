"""``open_halts``: a read-only listing of HALT rows that agrees with the gate's own HALT check.

Every case asks both ``open_halts(team_dir, action)`` and the gate (``decision_log._assert_no_unretracted_halt``) and
requires the same answer: an exact HALT, a family HALT across a colon, a HALT whose id is joined with ``/``, a
retracted HALT, a HALT retracted only by a sibling's id, an id-less HALT, and a HALT on a ``derives_from`` ancestor.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl
from agentteams.cli.action_ids import halt_row_handle
from agentteams.cli.open_halts import HaltRow, open_halts

COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions", "conditions_verified",
           "derives_from"]


def _row(action: str, verdict: str, **over: str) -> dict[str, str]:
    row = {"timestamp": "2026-11-01T00:00:00Z", "requesting_agent": "security", "action_reviewed": action,
           "verdict": verdict, "conditions_verified": "verified"}
    if verdict == "HALT-RETRACTED":
        row["conditions"] = "the HALT no longer applies"
    return row | over


def _log(team: Path, *rows: dict[str, str]) -> Path:
    path = team / "references" / "security-decisions.log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _gate_blocks(team: Path, action: str) -> bool:
    try:
        dl._assert_no_unretracted_halt(team, action=action)
    except RuntimeError:
        return True
    return False


def _agree(team: Path, action: str) -> list[HaltRow]:
    found = open_halts(team, action)
    assert bool(found) == _gate_blocks(team, action), (action, found)
    return found


def test_an_exact_halt(tmp_path):
    _log(tmp_path, _row("overwrite", "HALT"))
    assert _agree(tmp_path, "overwrite") == [HaltRow(1, "overwrite", True, None, "overwrite")]
    assert _agree(tmp_path, "prune") == []   # negative control


def test_a_family_halt_binds_its_instances_across_a_colon(tmp_path):
    _log(tmp_path, _row("server-apply", "HALT"))
    assert [h.row for h in _agree(tmp_path, "server-apply:remove-path:host:/srv/x")] == [1]
    assert _agree(tmp_path, "server-applyx") == []   # negative control: no delimiter, no family


@pytest.mark.parametrize("halt, blocks", [
    ("family", False), ("family/gate", False), ("family/gate/study", True), ("family/gate/study:sha256-abc", True),
])
def test_a_slash_joined_id_makes_no_family(tmp_path, halt, blocks):
    _log(tmp_path, _row(halt, "HALT"))
    assert bool(_agree(tmp_path, "family/gate/study:sha256-abc")) is blocks


def test_a_wider_slash_joined_halt_blocks_its_tokens(tmp_path):
    _log(tmp_path, _row("family/gate/study", "HALT"))
    assert _agree(tmp_path, "family/gate") and _agree(tmp_path, "gate")   # the token rule over-blocks


def test_a_retracted_halt(tmp_path):
    _log(tmp_path, _row("overwrite", "HALT"), _row("overwrite", "HALT-RETRACTED"))
    assert _agree(tmp_path, "overwrite") == []
    assert open_halts(tmp_path) == [HaltRow(1, "overwrite", False, 2, "overwrite")]
    # Negative control: a retraction without evidence retracts nothing.
    _log(tmp_path, _row("overwrite", "HALT"), _row("overwrite", "HALT-RETRACTED", conditions=""))
    assert [h.row for h in _agree(tmp_path, "overwrite")] == [1]
    assert open_halts(tmp_path) == [HaltRow(1, "overwrite", True, None, "overwrite")]


def test_a_halt_retracted_only_by_a_siblings_id_stays_open(tmp_path):
    family = "s10-cooldown-exempt"
    _log(tmp_path, _row(family, "HALT"), _row(f"{family}:o/b", "HALT-RETRACTED"))
    assert [h.row for h in _agree(tmp_path, family)] == [1]
    assert [h.row for h in _agree(tmp_path, f"{family}:o/a")] == [1]
    assert open_halts(tmp_path) == [HaltRow(1, family, True, None, family)]


def test_sibling_halts_are_judged_one_by_one(tmp_path):
    family = "s10-cooldown-exempt"
    _log(tmp_path, _row(f"{family}:o/a", "HALT"), _row(f"{family}:o/b", "HALT"), _row(f"{family}:o/b", "HALT-RETRACTED"))
    assert [h.row for h in _agree(tmp_path, f"{family}:o/a")] == [1]
    assert _agree(tmp_path, f"{family}:o/b") == []
    assert [h.row for h in _agree(tmp_path, family)] == [1]
    assert [(h.row, h.is_open, h.retracted_by) for h in open_halts(tmp_path)] == [(1, True, None), (2, False, 3)]


def test_an_idless_halt_blocks_every_action_until_its_row_handle_retracts_it(tmp_path):
    halt = _row("", "HALT")
    handle = halt_row_handle(1, halt)
    _log(tmp_path, halt)
    for action in ("overwrite", "prune", "server-apply:x"):
        assert _agree(tmp_path, action) == [HaltRow(1, handle, True, None, action)]
    _log(tmp_path, halt, _row(handle, "HALT-RETRACTED"))
    assert _agree(tmp_path, "overwrite") == []
    assert open_halts(tmp_path) == [HaltRow(1, handle, False, 2, handle)]
    # Negative control: a retraction naming the gate action does not retract it.
    _log(tmp_path, halt, _row("overwrite", "HALT-RETRACTED"))
    assert [h.row for h in _agree(tmp_path, "overwrite")] == [1]


def test_a_halt_on_a_lineage_ancestor_blocks_the_derived_action(tmp_path):
    _log(tmp_path, _row("parent-op", "HALT"), _row("child-op", "PASS", derives_from="parent-op"))
    assert _agree(tmp_path, "child-op") == [HaltRow(1, "parent-op", True, None, "parent-op")]
    _log(tmp_path, _row("parent-op", "HALT"), _row("child-op", "PASS"))   # negative control: no declared lineage
    assert _agree(tmp_path, "child-op") == []


def test_it_reads_only_and_refuses_what_the_gate_refuses(tmp_path):
    path = _log(tmp_path, _row("overwrite", "HALT"))
    before = path.read_bytes()
    open_halts(tmp_path)
    open_halts(tmp_path, "overwrite")
    assert path.read_bytes() == before
    assert open_halts(tmp_path / "absent") == []
    path.write_text("timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified,prev_digest\n"
                    "2026-11-01,security,overwrite,HALT,,,\n2026-11-02,security,prune,PASS,,verified,0000\n")
    with pytest.raises(RuntimeError, match="chain broken"):
        open_halts(tmp_path)
    assert _gate_blocks(tmp_path, "prune")


@pytest.mark.parametrize("team_dir, action, error", [
    ("references", None, TypeError), (None, None, TypeError), (Path("."), 7, TypeError), (Path("."), "  ", ValueError),
])
def test_it_refuses_inputs_of_the_wrong_kind(team_dir, action, error):
    with pytest.raises(error):
        open_halts(team_dir, action)


def test_a_lineage_that_cannot_be_walked_refuses_as_the_gate_does(tmp_path):
    _log(tmp_path, _row("child-op", "PASS", derives_from="nowhere"))
    with pytest.raises(RuntimeError, match="lineage traversal for action 'child-op' failed closed"):
        open_halts(tmp_path, "child-op")
    assert _gate_blocks(tmp_path, "child-op")
