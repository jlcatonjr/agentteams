"""test_redteam_verifiers_core.py — F-1 sensitivity tests and negative controls, core module family.

Companion to ``tests/test_redteam_verifiers.py`` (read its docstring for why every verifier needs
a pair). These cover verifiers in the audit-contract, carve-out, confinement, proposal, branch
lifecycle, learned-block and shrink-override modules that F-1 found with no ledger row and no
direct test of their own. Each test is named in ``references/redteam-verifiers.csv``.

* a **sensitivity test** proves the verifier's output *changes* when a relevant input changes;
* a **negative control** proves it does *not* change for irrelevant input, or that valid input
  passes.

Stdlib and pytest only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentteams import branch_cleanup as bc
from agentteams import branch_inventory as bi
from agentteams import carveout_checks as ck
from agentteams import confinement as C
from agentteams import learned_blocks as lb
from agentteams import mcp_direct_grants as G
from agentteams import proposal_policy as PP
from agentteams import proposal_runner as R
from agentteams import proposals as P
from agentteams import runner_mcp as M
from agentteams import shrink_allow as SA
from agentteams.audit_agent_contract import _check_instruction_authority_reachable
from agentteams.cli import clearance_binding as cb
from agentteams.frameworks import goose_tool_scoping as scoping
from agentteams.frameworks._sandbox_emit import assert_roots_clear_of_signing_keys

PKG = Path(P.__file__).resolve().parent
READFS_SERVER = PKG / "data" / "goose-readfs-mcp.py"
RUNNER_SERVER = PKG / "data" / "agentteams-runner-mcp.py"


# ---------------------------------------------------------------------------
# audit_agent_contract._check_instruction_authority_reachable
# ---------------------------------------------------------------------------

_IA_REF = "references/instruction-authority.reference.md"


def _ia_files(navigator_body: str) -> dict[str, str]:
    return {
        _IA_REF: "# Instruction authority\n",
        "navigator.agent.md": navigator_body,
        "primary-producer.agent.md": "# Producer\nNo pointer here.\n",
    }


def test_instruction_authority_flags_a_reader_that_loses_its_pointer():
    """Sensitivity: dropping the C-4 pointer from an untrusted-content reader adds a finding,
    and dropping the reference file itself adds another."""
    with_pointer = _ia_files("See references/instruction-authority.reference.md (C-4).\n")
    assert _check_instruction_authority_reachable(with_pointer, agent_ext=".agent.md") == []

    without_pointer = _ia_files("# Navigator\nNo pointer.\n")
    codes = [f.code for f in _check_instruction_authority_reachable(without_pointer, agent_ext=".agent.md")]
    assert codes == ["AR_MISSING_C4_POINTER"]

    no_ref = {k: v for k, v in with_pointer.items() if k != _IA_REF}
    codes = [f.code for f in _check_instruction_authority_reachable(no_ref, agent_ext=".agent.md")]
    assert codes == ["AR_MISSING_INSTRUCTION_AUTHORITY"]


def test_instruction_authority_ignores_agents_that_read_no_untrusted_content():
    """Negative control: an agent outside the untrusted-reader set (primary-producer) without the
    pointer, and a non-agent reference file, raise nothing when the reference is emitted."""
    files = _ia_files("See references/instruction-authority.reference.md (C-4).\n")
    files["references/other.md"] = "no pointer"
    files["primary-producer.agent.md"] = "# Producer\nStill no pointer, and a different body.\n"
    assert _check_instruction_authority_reachable(files, agent_ext=".agent.md") == []


# ---------------------------------------------------------------------------
# branch_cleanup._row_digest (the deletion ledger's hash chain link)
# ---------------------------------------------------------------------------

def _ledger_row(**over: str) -> dict[str, str]:
    row = {c: "" for c in bc.LEDGER_COLUMNS}
    row.update({"timestamp": "2026-11-01T00:00:00Z", "run_id": "r1", "branch": "feat/a", "event": "attempt"})
    row.update(over)
    return row


@pytest.mark.parametrize("column", ["branch", "old_sha", "event", "authorization", "prev_digest"])
def test_row_digest_changes_when_any_ledger_column_changes(column):
    """Sensitivity: editing any ledger column changes the link digest, so read_ledger's chain
    check sees the edit."""
    base = _ledger_row()
    edited = _ledger_row(**{column: "tampered"})
    assert bc._row_digest(base) != bc._row_digest(edited)


def test_row_digest_ignores_keys_outside_the_ledger_columns():
    """Negative control: a key that is not a ledger column does not move the digest, and the same
    row always digests the same."""
    base = _ledger_row()
    assert bc._row_digest(base) == bc._row_digest(dict(base))
    assert bc._row_digest(base) == bc._row_digest({**base, "not_a_column": "noise"})


# ---------------------------------------------------------------------------
# branch_inventory.plan_digest / pr_ref_matches
# ---------------------------------------------------------------------------

def _plan() -> dict:
    return {"repo": "/r", "push_remote": "origin", "default_branch": "main", "default_tip": "a" * 40,
            "items": [{"branch": "feat/a", "tip": "b" * 40}], "generated_at": "2026-11-01", "sha256": ""}


@pytest.mark.parametrize("field, value", [
    ("items", [{"branch": "feat/a", "tip": "c" * 40}]),
    ("default_tip", "d" * 40),
    ("push_remote", "fork"),
])
def test_plan_digest_changes_with_a_binding_field(field, value):
    """Sensitivity: changing a binding field (an item's tip, the default tip, the remote) changes
    the digest load_plan compares against the recorded sha256."""
    assert bi.plan_digest(_plan()) != bi.plan_digest({**_plan(), field: value})


def test_plan_digest_ignores_generated_at_and_its_own_sha256():
    """Negative control: the non-binding fields (generated_at, sha256) do not affect the digest."""
    assert bi.plan_digest(_plan()) == bi.plan_digest({**_plan(), "generated_at": "later", "sha256": "x"})


def test_pr_ref_matches_refuses_a_moved_or_missing_ref(monkeypatch):
    """Sensitivity: a PR ref at another SHA, or no ref at all, is not a match."""
    monkeypatch.setattr(bi, "git", lambda *a, **k: f"{'e' * 40}\trefs/pull/7/head")
    assert bi.pr_ref_matches(Path("."), "origin", 7, "f" * 40) is False
    monkeypatch.setattr(bi, "git", lambda *a, **k: "")
    assert bi.pr_ref_matches(Path("."), "origin", 7, "f" * 40) is False


def test_pr_ref_matches_accepts_the_audited_tip(monkeypatch):
    """Negative control: the ref at exactly the audited tip matches, whether the number is an int
    or a string."""
    monkeypatch.setattr(bi, "git", lambda *a, **k: f"{'f' * 40}\trefs/pull/7/head")
    assert bi.pr_ref_matches(Path("."), "origin", 7, "f" * 40) is True
    assert bi.pr_ref_matches(Path("."), "origin", "7", "f" * 40) is True


# ---------------------------------------------------------------------------
# carveout_checks: lineage, own HALTs, aggregate cap, clearances
# ---------------------------------------------------------------------------

_TEAM = ".claude/agents"
_DECISION_COLS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions",
                  "conditions_verified"]


def _decisions(root: Path, rows: list[tuple[str, str]]) -> None:
    refs = root / _TEAM / "references"
    refs.mkdir(parents=True, exist_ok=True)
    with (refs / "security-decisions.log.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_DECISION_COLS)
        w.writeheader()
        for action, verdict in rows:
            w.writerow({"timestamp": "2026-11-08T00:00:00Z", "requesting_agent": "security",
                        "action_reviewed": action, "verdict": verdict, "conditions": "",
                        "conditions_verified": ""})


def _registry(**entry) -> dict:
    return {"decision_log": _TEAM, "carveouts": [{"id": "co-1", **entry}]}


def test_check_lineage_reports_a_halt_on_the_lineage(tmp_path):
    """Sensitivity: the same lineage id goes from clean to lineage-halt when a HALT is logged."""
    reg = _registry(lineage=["approve-x"])
    _decisions(tmp_path, [("approve-x", "PASS")])
    assert ck.check_lineage(tmp_path, reg, reg["carveouts"]) == []
    _decisions(tmp_path, [("approve-x", "PASS"), ("approve-x", "HALT")])
    assert [p.code for p in ck.check_lineage(tmp_path, reg, reg["carveouts"])] == ["lineage-halt"]


def test_check_lineage_ignores_a_halt_on_an_unrelated_action(tmp_path):
    """Negative control: a HALT on some other action does not touch a cleanly logged lineage."""
    reg = _registry(lineage=["approve-x"])
    _decisions(tmp_path, [("approve-x", "PASS"), ("something-else", "HALT")])
    assert ck.check_lineage(tmp_path, reg, reg["carveouts"]) == []


def test_check_own_halts_stops_a_halted_carveout(tmp_path):
    """Sensitivity: a HALT on carveout:<id> turns a clean result into a 'halted' problem."""
    reg = _registry()
    _decisions(tmp_path, [("carveout:co-1", "PASS")])
    assert ck.check_own_halts(tmp_path, reg, reg["carveouts"]) == []
    _decisions(tmp_path, [("carveout:co-1", "HALT")])
    assert [p.code for p in ck.check_own_halts(tmp_path, reg, reg["carveouts"])] == ["halted"]


def test_check_own_halts_ignores_a_halt_on_another_carveout(tmp_path):
    """Negative control: a HALT on a different carve-out id does not stop this one."""
    reg = _registry()
    _decisions(tmp_path, [("carveout:co-2", "HALT")])
    assert ck.check_own_halts(tmp_path, reg, reg["carveouts"]) == []


def _exceptions(root: Path, cap: int, active: int) -> None:
    refs = root / _TEAM / "references"
    refs.mkdir(parents=True, exist_ok=True)
    exceptions = [{"id": f"x{i}", "status": "active", "relaxing": True, "expires": "2099-01-01T00:00:00Z"}
                  for i in range(active)]
    (refs / "exception-registry.json").write_text(json.dumps(
        {"version": 1, "cap": {"max_active_relaxing": cap}, "exceptions": exceptions}))


def test_check_aggregate_cap_refuses_when_carveouts_push_past_the_cap(tmp_path):
    """Sensitivity: one held exception plus one carve-out passes a cap of 2 and fails a cap of 1."""
    reg = _registry()
    _exceptions(tmp_path, cap=2, active=1)
    assert ck.check_aggregate_cap(tmp_path, reg, reg["carveouts"]) == []
    _exceptions(tmp_path, cap=1, active=1)
    assert [p.code for p in ck.check_aggregate_cap(tmp_path, reg, reg["carveouts"])] == ["aggregate-cap"]


def test_check_aggregate_cap_ignores_inactive_exceptions(tmp_path):
    """Negative control: retired or non-relaxing exceptions do not count toward the cap."""
    reg = _registry()
    refs = tmp_path / _TEAM / "references"
    refs.mkdir(parents=True)
    (refs / "exception-registry.json").write_text(json.dumps({
        "version": 1, "cap": {"max_active_relaxing": 1},
        "exceptions": [{"id": "old", "status": "retired", "relaxing": True, "expires": "2099-01-01T00:00:00Z"},
                       {"id": "plain", "status": "active", "relaxing": False, "expires": "2099-01-01T00:00:00Z"}]}))
    assert ck.check_aggregate_cap(tmp_path, reg, reg["carveouts"]) == []


def test_check_clearances_tracks_the_cleared_files_bytes(tmp_path):
    """Sensitivity: a PASS bound to the file's sha clears it; one changed byte un-clears it."""
    target = tmp_path / "run.sh"
    target.write_text("echo one\n")
    reg = _registry(clearance={"scope": "roster-batch", "file": "run.sh"})
    _decisions(tmp_path, [(cb.clearance_action("roster-batch", target), "PASS")])
    assert ck.check_clearances(tmp_path, reg, reg["carveouts"]) == []
    target.write_text("echo two\n")
    assert [p.code for p in ck.check_clearances(tmp_path, reg, reg["carveouts"])] == ["not-cleared"]


def test_check_clearances_skips_entries_that_declare_no_clearance(tmp_path):
    """Negative control: an entry without a clearance field is not checked, even with no log rows."""
    reg = _registry()
    _decisions(tmp_path, [])
    assert ck.check_clearances(tmp_path, reg, reg["carveouts"]) == []


# ---------------------------------------------------------------------------
# confinement._check_custody
# ---------------------------------------------------------------------------

def test_check_custody_refuses_writable_or_linked_paths(tmp_path):
    """Sensitivity: a group-writable file, a symlink, and a directory passed as a file are refused."""
    f = tmp_path / "confined.json"
    f.write_text("{}")
    os.chmod(f, 0o620)
    with pytest.raises(C.ConfinementError, match="group- or world-writable"):
        C._check_custody(f, directory=False)
    os.chmod(f, 0o600)
    link = tmp_path / "link.json"
    link.symlink_to(f)
    with pytest.raises(C.ConfinementError, match="not a symlink"):
        C._check_custody(link, directory=False)
    with pytest.raises(C.ConfinementError):
        C._check_custody(tmp_path, directory=False)


def test_check_custody_accepts_an_owner_only_file_and_dir(tmp_path):
    """Negative control: an owner-only regular file and directory pass, read bits notwithstanding."""
    f = tmp_path / "confined.json"
    f.write_text("{}")
    os.chmod(f, 0o644)
    os.chmod(tmp_path, 0o755)
    assert C._check_custody(f, directory=False).st_size == 2
    C._check_custody(tmp_path, directory=True)


# ---------------------------------------------------------------------------
# frameworks._sandbox_emit.assert_roots_clear_of_signing_keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("root", ["~/.config/agentteams/keys", "~/.config/agentteams/keys/sub"])
def test_signing_key_roots_are_refused(tmp_path, monkeypatch, root):
    """Sensitivity: a write root at or inside the signing-key directory raises, by ~ or absolute path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="signing-key"):
        assert_roots_clear_of_signing_keys([".", root])
    with pytest.raises(ValueError, match="signing-key"):
        assert_roots_clear_of_signing_keys([os.path.expanduser(root)])


def test_roots_beside_the_signing_key_dir_are_allowed(tmp_path, monkeypatch):
    """Negative control: a sibling sharing the prefix, a relative root named like the key dir,
    and None all pass."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert_roots_clear_of_signing_keys([".", "~/.config/agentteams/keysX", ".config/agentteams/keys"])
    assert_roots_clear_of_signing_keys(None)


# ---------------------------------------------------------------------------
# learned_blocks.content_digest
# ---------------------------------------------------------------------------

def test_content_digest_changes_with_the_block_content():
    """Sensitivity: one changed character in the block content changes the digest agent-doc sync
    compares against the agreed baseline."""
    assert lb.content_digest("- learned rule A\n") != lb.content_digest("- learned rule B\n")


def test_content_digest_is_stable_for_identical_content():
    """Negative control: identical content always gives the same full sha256 digest."""
    text = "- learned rule A\n"
    assert lb.content_digest(text) == lb.content_digest(str(text))
    assert lb.content_digest(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# mcp_direct_grants.check_write_allowed
# ---------------------------------------------------------------------------

def _grant(**over) -> dict:
    now = datetime.now(UTC)
    return {"grant_id": "g1", "expires": (now + timedelta(days=1)).isoformat(), "max_writes": 3, **over}


def test_check_write_allowed_refuses_a_spent_or_expired_grant():
    """Sensitivity: the third write passes, the fourth is refused; an expired grant is refused."""
    G.check_write_allowed(_grant(), 2)
    with pytest.raises(P.ProposalError, match="used its 3 writes"):
        G.check_write_allowed(_grant(), 3)
    with pytest.raises(P.ProposalError, match="expired"):
        G.check_write_allowed(_grant(), 0, now=datetime.now(UTC) + timedelta(days=2))


def test_check_write_allowed_ignores_unrelated_grant_fields():
    """Negative control: fields other than expires/max_writes do not affect a write within limits."""
    G.check_write_allowed(_grant(tools=["write_file"], agent="producer", note="x"), 0)


# ---------------------------------------------------------------------------
# proposal_policy._pattern_scope_matches
# ---------------------------------------------------------------------------

def test_pattern_scope_refuses_extra_segments_and_dot_segments():
    """Sensitivity: '*' never crosses '/', never matches a dot-segment, and a trailing '/' needs
    something below it."""
    pat = "reports/*/x.md"
    assert PP._pattern_scope_matches("reports/a/x.md", pat) is True
    assert PP._pattern_scope_matches("reports/a/b/x.md", pat) is False
    assert PP._pattern_scope_matches("reports/.git/x.md", pat) is False
    assert PP._pattern_scope_matches("reports/a", "reports/*/") is False


def test_pattern_scope_case_folds_the_pattern_and_covers_below_a_directory():
    """Negative control: pattern case does not matter, and anything below a directory pattern
    matches."""
    assert PP._pattern_scope_matches("reports/a/x.md", "Reports/*/X.md") is True
    assert PP._pattern_scope_matches("reports/a/deep/x.pdf", "reports/*/") is True


# ---------------------------------------------------------------------------
# proposal_runner: installed control-plane servers
# ---------------------------------------------------------------------------

def _install(root: Path, rel: str, src: Path, extra: str = "") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(src.read_bytes() + extra.encode())
    return path


def test_check_installed_readfs_ignores_an_unrelated_file_beside_it(tmp_path):
    """Negative control: the shipped readfs copy passes with an unrelated file in .agentteams/bin."""
    _install(tmp_path, scoping.READFS_PROTECTED_PATH, READFS_SERVER)
    (tmp_path / ".agentteams" / "bin" / "notes.txt").write_text("unrelated")
    R.check_installed_readfs(tmp_path)


def test_check_installed_runner_mcp_ignores_an_unrelated_file_beside_it(tmp_path):
    """Negative control: the shipped runner MCP copy passes with an unrelated file beside it."""
    _install(tmp_path, M.PROTECTED_PATH, RUNNER_SERVER)
    (tmp_path / ".agentteams" / "bin" / "notes.txt").write_text("unrelated")
    R.check_installed_runner_mcp(tmp_path)


@pytest.mark.parametrize("which", ["readfs", "runner"])
def test_check_installed_servers_refuses_either_edited_copy(tmp_path, which):
    """Sensitivity: editing either installed server makes the combined check refuse."""
    _install(tmp_path, scoping.READFS_PROTECTED_PATH, READFS_SERVER, "\n# x\n" if which == "readfs" else "")
    _install(tmp_path, M.PROTECTED_PATH, RUNNER_SERVER, "\n# x\n" if which == "runner" else "")
    with pytest.raises(R.RunnerError, match="doesn't match"):
        R.check_installed_servers(tmp_path)


def test_check_installed_servers_passes_shipped_or_absent_copies(tmp_path):
    """Negative control: no installed copies, then both shipped copies, pass."""
    R.check_installed_servers(tmp_path)
    _install(tmp_path, scoping.READFS_PROTECTED_PATH, READFS_SERVER)
    _install(tmp_path, M.PROTECTED_PATH, RUNNER_SERVER)
    R.check_installed_servers(tmp_path)


# ---------------------------------------------------------------------------
# proposals._check_shape / _check_base / _check_destination
# ---------------------------------------------------------------------------

def _change(**over) -> dict:
    return {"kind": "change-proposal", "dispatch": "n", "path": "src/a.py", "base_sha256": "absent",
            "content": "x\n", "rationale": "r", **over}


def test_check_shape_refuses_wrong_kind_missing_or_unknown_keys():
    """Sensitivity: a wrong kind, a missing required key, or an unknown key is refused."""
    with pytest.raises(P.ProposalError, match="not a change-proposal"):
        P._check_shape(_change(kind="delete-proposal"), "change-proposal")
    missing = _change()
    del missing["rationale"]
    with pytest.raises(P.ProposalError, match="missing"):
        P._check_shape(missing, "change-proposal")
    with pytest.raises(P.ProposalError, match="unknown"):
        P._check_shape(_change(shell="rm -rf /"), "change-proposal")


def test_check_shape_accepts_required_plus_optional_keys():
    """Negative control: the required keys, with or without the declared optional ones, pass."""
    P._check_shape(_change(), "change-proposal")
    P._check_shape(_change(gates=["scan"], agent="producer"), "change-proposal")


def test_check_base_refuses_a_stale_base(tmp_path):
    """Sensitivity: a base that doesn't match the file, or 'absent' for an existing file, is stale."""
    (tmp_path / "a.txt").write_text("one\n")
    with pytest.raises(P.ProposalError, match="stale base"):
        P._check_base(tmp_path, "a.txt", hashlib.sha256(b"two\n").hexdigest())
    with pytest.raises(P.ProposalError, match="stale base"):
        P._check_base(tmp_path, "a.txt", "absent")
    with pytest.raises(P.ProposalError, match="sha256 hex digest"):
        P._check_base(tmp_path, "a.txt", "not-a-digest")


def test_check_base_accepts_the_current_digest_or_absent(tmp_path):
    """Negative control: the file's current sha256 passes, and 'absent' passes for a missing file."""
    (tmp_path / "a.txt").write_text("one\n")
    assert P._check_base(tmp_path, "a.txt", hashlib.sha256(b"one\n").hexdigest()).name == "a.txt"
    P._check_base(tmp_path, "new.txt", "absent")


def _dest_policy() -> PP.Policy:
    return PP.Policy(agent_policies={"producer": {"write_scopes": ["src/"]}},
                     protected_paths=["src/locked.py"], brief_rel="brief.json")


@pytest.mark.parametrize("path, why", [
    ("docs/a.md", "outside producer's write_scopes"),
    (".claude/agents/x.md", "control plane"),
    ("src/locked.py", "protected path"),
    ("brief.json", "the brief"),
    ("../escape.py", "outside the project"),
])
def test_check_destination_refuses_paths_outside_policy(tmp_path, path, why):
    """Sensitivity: out-of-scope, control-plane, protected, brief and escaping paths are refused."""
    with pytest.raises(P.ProposalError, match=why):
        P._check_destination(tmp_path, path, _dest_policy(), "producer")


def test_check_destination_accepts_an_in_scope_path(tmp_path):
    """Negative control: a path inside the agent's write scope passes and comes back normalized."""
    assert P._check_destination(tmp_path, "src/a.py", _dest_policy(), "producer") == "src/a.py"
    assert P._check_destination(tmp_path, "src/./pkg/b.py", _dest_policy(), "producer") == "src/pkg/b.py"


# ---------------------------------------------------------------------------
# shrink_allow.body_digest
# ---------------------------------------------------------------------------

_BEGIN = "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
_END = "<!-- AGENTTEAMS:END content -->\n"


def test_body_digest_changes_when_the_reviewed_body_changes():
    """Sensitivity: editing the fenced body changes the digest an override is bound to."""
    a = SA.body_digest(_BEGIN + "- keep retired-doc.md\n" + _END)
    b = SA.body_digest(_BEGIN + "- keep other-doc.md\n" + _END)
    assert a != b and len(a) == 12


def test_body_digest_ignores_markers_and_outer_whitespace():
    """Negative control: the fence marker lines and leading/trailing whitespace don't move the
    digest."""
    fenced = SA.body_digest(_BEGIN + "\n- keep retired-doc.md\n\n" + _END)
    other_markers = SA.body_digest("<!-- AGENTTEAMS:BEGIN content v=2 -->\n- keep retired-doc.md\n"
                                   "<!-- AGENTTEAMS:END content -->\n")
    assert fenced == other_markers
