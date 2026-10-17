"""Tests for the integrity-verification feature (remediation Plan 2):
drift.verify_output_integrity, emit.verify_backup, the file_hashes coverage
widening, and the --verify-integrity / --verify-backup CLI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import build_team
from agentteams import drift, emit

# Real fence markers — _extract_fenced_regions only flags FENCE-BROKEN when a
# genuine AGENTTEAMS:BEGIN/END pair is present but malformed.
_FENCED = "<!-- AGENTTEAMS:BEGIN content v=1 -->\n{body}\n<!-- AGENTTEAMS:END content -->\n"


def _short(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _seed(d: Path, files: dict[str, str]) -> None:
    (d / "references").mkdir(parents=True, exist_ok=True)
    fh = {}
    for name, body in files.items():
        p = d / name
        p.write_text(body, encoding="utf-8")
        fh[name] = _short(p)
    (d / "references" / "build-log.json").write_text(json.dumps({"file_hashes": fh}))


# ---------------------------------------------------------------------------
# drift.verify_output_integrity classification
# ---------------------------------------------------------------------------

def test_classifies_all_statuses(tmp_path):
    _seed(tmp_path, {
        "ok.md": _FENCED.format(body="x"),
        "mod.md": _FENCED.format(body="ORIGINAL"),
        "trunc.md": "non-empty at build\n",
        "broke.md": _FENCED.format(body="v"),
        "gone.md": "present at build\n",
    })
    # mutate
    (tmp_path / "mod.md").write_text(_FENCED.format(body="CHANGED"))           # MODIFIED
    (tmp_path / "trunc.md").write_text("")                                      # TRUNCATED
    (tmp_path / "broke.md").write_text("<!-- AGENTTEAMS:BEGIN content v=1 -->\nno end\n")  # FENCE-BROKEN
    (tmp_path / "gone.md").unlink()                                             # MISSING

    status = {e["rel_path"]: e["status"] for e in drift.verify_output_integrity(tmp_path)}
    assert status == {
        "ok.md": "OK", "mod.md": "MODIFIED", "trunc.md": "TRUNCATED",
        "broke.md": "FENCE-BROKEN", "gone.md": "MISSING",
    }


def test_unknown_without_build_log(tmp_path):
    assert drift.verify_output_integrity(tmp_path) == []  # no build-log → cannot verify


def test_pristine_tree_all_ok(tmp_path):
    _seed(tmp_path, {"a.md": _FENCED.format(body="a"), "b.md": "plain\n"})
    assert all(e["status"] == "OK" for e in drift.verify_output_integrity(tmp_path))


# ---------------------------------------------------------------------------
# emit.verify_backup (backup-integrity sub-mode)
# ---------------------------------------------------------------------------

def test_verify_backup_pass_fail_and_no_manifest(tmp_path):
    (tmp_path / "a.agent.md").write_text("alpha\n")
    (tmp_path / "b.agent.md").write_text("beta\n")
    res = emit.backup_output_dir(tmp_path, reason="test")
    assert {e["status"] for e in emit.verify_backup(res.backup_path)} == {"PASS"}

    # bit-rot one backup copy
    (res.backup_path / "a.agent.md.agentteams-bak").write_text("TAMPERED\n")  # stored inert
    by_file = {e["source_path"]: e["status"] for e in emit.verify_backup(res.backup_path)}
    assert by_file["a.agent.md"] == "FAIL" and by_file["b.agent.md"] == "PASS"

    # a backup dir without a manifest → cannot verify (empty)
    bare = tmp_path / "bare"
    bare.mkdir()
    assert emit.verify_backup(bare) == []


# ---------------------------------------------------------------------------
# file_hashes coverage widening (Step 1)
# ---------------------------------------------------------------------------

def test_compute_file_hashes_covers_every_supplied_path(tmp_path):
    from agentteams.cli.artifacts import _compute_file_hashes
    written = tmp_path / "w.md"; written.write_text("w\n")
    unchanged = tmp_path / "u.md"; unchanged.write_text("u\n")
    # The caller now passes written+merged+unchanged; the hasher must cover them all.
    hashes = _compute_file_hashes([str(written), str(unchanged)], tmp_path)
    assert set(hashes) == {"w.md", "u.md"}


# ---------------------------------------------------------------------------
# CLI exit-code semantics (the exit code IS the gate)
# ---------------------------------------------------------------------------

def test_cli_verify_integrity_exit_codes(tmp_path):
    _seed(tmp_path, {"a.md": _FENCED.format(body="a")})
    assert build_team.main(["--verify-integrity", "--output", str(tmp_path)]) == 0  # clean
    (tmp_path / "a.md").write_text("")  # truncate → suspect
    assert build_team.main(["--verify-integrity", "--output", str(tmp_path)]) == 1


def test_cli_verify_integrity_modified_is_advisory_exit_0(tmp_path):
    _seed(tmp_path, {"a.md": _FENCED.format(body="orig")})
    (tmp_path / "a.md").write_text(_FENCED.format(body="user edited this"))  # MODIFIED, fences intact
    assert build_team.main(["--verify-integrity", "--output", str(tmp_path)]) == 0


def test_cli_verify_integrity_no_baseline_exit_0(tmp_path):
    assert build_team.main(["--verify-integrity", "--output", str(tmp_path)]) == 0  # UNKNOWN, not failure


def test_cli_verify_backup(tmp_path):
    (tmp_path / "x.agent.md").write_text("content\n")
    emit.backup_output_dir(tmp_path, reason="test")
    assert build_team.main(["--verify-backup", "--output", str(tmp_path)]) == 0


# ---------------------------------------------------------------------------
# The gate's interpreter pin is machine-local (Rule S-8): a clean checkout never has it
# ---------------------------------------------------------------------------

def _seed_with_gate_pin(tmp_path: Path, *, ignore_file: bool) -> Path:
    """An agents dir whose build log records ../hooks/agentteams-python, as a Claude render's does."""
    from agentteams.frameworks.claude import GATE_PIN_GITIGNORE, GATE_PIN_NAME

    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir()
    (hooks / GATE_PIN_NAME).write_text("/opt/somewhere/bin/python3\n")
    _seed(agents, {"a.md": _FENCED.format(body="a")})
    log_path = agents / "references" / "build-log.json"
    log = json.loads(log_path.read_text())
    log["file_hashes"][f"../hooks/{GATE_PIN_NAME}"] = _short(hooks / GATE_PIN_NAME)
    if ignore_file:
        (hooks / ".gitignore").write_text(GATE_PIN_GITIGNORE)
        log["file_hashes"]["../hooks/.gitignore"] = _short(hooks / ".gitignore")
    log_path.write_text(json.dumps(log))
    (hooks / GATE_PIN_NAME).unlink()  # a clean checkout: the pin is gitignored, so it never arrives
    return agents


def test_clean_checkout_without_the_gitignored_gate_pin_verifies(tmp_path):
    # Regression (rc.14, found in baseAgent): a fresh clone of a Claude team failed --verify-integrity with
    # [MISSING] ../hooks/agentteams-python, a file that is never committed by design.
    agents = _seed_with_gate_pin(tmp_path, ignore_file=True)
    status = {e["rel_path"]: e["status"] for e in drift.verify_output_integrity(agents)}
    assert status["../hooks/agentteams-python"] == "LOCAL"
    assert build_team.main(["--verify-integrity", "--output", str(agents)]) == 0


def test_gate_pin_without_its_ignore_file_is_still_missing(tmp_path):
    # Rule 12: the exemption needs the ignore file that makes the pin untracked; without it, absence is a finding.
    agents = _seed_with_gate_pin(tmp_path, ignore_file=False)
    status = {e["rel_path"]: e["status"] for e in drift.verify_output_integrity(agents)}
    assert status["../hooks/agentteams-python"] == "MISSING"
    assert build_team.main(["--verify-integrity", "--output", str(agents)]) == 1


def test_only_the_pin_is_exempt_not_other_hook_files(tmp_path):
    from agentteams.frameworks.claude import GATE_PIN_GITIGNORE

    agents = _seed_with_gate_pin(tmp_path, ignore_file=True)
    hooks = tmp_path / ".claude" / "hooks"
    (hooks / ".gitignore").write_text(GATE_PIN_GITIGNORE + "/constitutional-gate.py\n")
    gate = hooks / "constitutional-gate.py"
    gate.write_text("# gate\n")
    log_path = agents / "references" / "build-log.json"
    log = json.loads(log_path.read_text())
    log["file_hashes"]["../hooks/constitutional-gate.py"] = _short(gate)
    log_path.write_text(json.dumps(log))
    gate.unlink()  # a tracked enforcement file going missing must still fail, even if someone ignores it
    status = {e["rel_path"]: e["status"] for e in drift.verify_output_integrity(agents)}
    assert status["../hooks/constitutional-gate.py"] == "MISSING"
    assert build_team.main(["--verify-integrity", "--output", str(agents)]) == 1


def test_a_gate_replaced_by_a_dangling_link_to_the_pin_is_missing(tmp_path):
    # @security (rc.14 review): the pin check runs on the unresolved recorded path, and never on a symlink.
    from agentteams.frameworks.claude import GATE_PIN_NAME

    agents = _seed_with_gate_pin(tmp_path, ignore_file=True)
    hooks = tmp_path / ".claude" / "hooks"
    gate = hooks / "constitutional-gate.py"
    gate.write_text("# gate\n")
    log_path = agents / "references" / "build-log.json"
    log = json.loads(log_path.read_text())
    log["file_hashes"]["../hooks/constitutional-gate.py"] = _short(gate)
    log_path.write_text(json.dumps(log))
    gate.unlink()
    gate.symlink_to(GATE_PIN_NAME)  # dangling: the pin is absent on a clean checkout
    status = {e["rel_path"]: e["status"] for e in drift.verify_output_integrity(agents)}
    assert status["../hooks/constitutional-gate.py"] == "MISSING"
    assert build_team.main(["--verify-integrity", "--output", str(agents)]) == 1


def test_an_undecodable_ignore_file_leaves_the_pin_missing(tmp_path):
    agents = _seed_with_gate_pin(tmp_path, ignore_file=True)
    (tmp_path / ".claude" / "hooks" / ".gitignore").write_bytes(b"\xff\xfe/agentteams-python\n")
    status = {e["rel_path"]: e["status"] for e in drift.verify_output_integrity(agents)}
    assert status["../hooks/agentteams-python"] == "MISSING"
