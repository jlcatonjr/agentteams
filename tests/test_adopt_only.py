"""``--adopt-only SLUG[,SLUG...]``: adopt a named subset, touching only the orchestrator's adopted rows.

End to end against a real generated team (data-pipeline example): the named rows and nothing else
are added, the rest of the orchestrator and every other path under the project stay byte-identical,
a second run is a no-op, a later ``--update --merge`` carries the rows with zero diff, and on the
Copilot surface the ``agents:`` extension demands exactly the named slugs through the
``adopt-orphans-merge`` gate. Claude has no ``agents:`` list, so no decision is needed there.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import shutil
from pathlib import Path

import pytest

import build_team
from agentteams.cli import adopt_only
from agentteams.cli.parser import _build_parser
from agentteams.cli.parser_validate import _validate_option_combinations

REPO_ROOT = Path(__file__).parent.parent
BRIEF = REPO_ROOT / "examples" / "data-pipeline" / "brief.json"
_WAIVER_KEY = "adopt-only-waiver-key"
_DIRS = {"claude": (".claude", "agents"), "copilot-vscode": (".github", "agents")}
_EXT = {"claude": ".md", "copilot-vscode": ".agent.md"}
_ORPHANS = {
    "alpha-agent": "tools: Read, Grep, Glob",
    "beta-agent": "tools: Read, Edit, Bash",
}


def _seed_waiver(out: Path) -> None:
    refs = out / "references"
    refs.mkdir(parents=True, exist_ok=True)
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "wf-ao",
        "action_reviewed": "security-intel-freshness", "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "99", "uses": "0", "approver": "security", "ticket_id": "AO",
        "reason_code": "T", "conditions_verified": "verified", "signature": "",
    }
    signed = ["waiver_id", "action_reviewed", "expires_at", "max_uses", "uses", "approver",
              "ticket_id", "reason_code", "conditions_verified"]
    w["signature"] = hmac.new(_WAIVER_KEY.encode(), "|".join(w[k] for k in signed).encode(),
                              hashlib.sha256).hexdigest()
    cols = ["timestamp", "waiver_id", *signed, "signature"]
    (refs / "security-waivers.log.csv").write_text(
        ",".join(cols) + "\n" + ",".join(w[k] for k in cols) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One generated project per framework, with two orphan agent files added."""
    if not BRIEF.exists():
        pytest.skip("data-pipeline brief not found")
    roots: dict[str, Path] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _WAIVER_KEY)
        for fw, parts in _DIRS.items():
            root = tmp_path_factory.mktemp(f"adopt-only-{fw}") / "proj"
            out = root.joinpath(*parts)
            _seed_waiver(out)
            assert build_team.main([
                "--description", str(BRIEF), "--framework", fw, "--output", str(out),
                "--yes", "--no-scan", "--security-offline",
            ]) == 0
            for slug, tools in _ORPHANS.items():
                (out / f"{slug}{_EXT[fw]}").write_text(
                    f"---\nname: {slug} — Bespoke\ndescription: \"Does {slug} things.\"\n{tools}\n---\n\nBody.\n",
                    encoding="utf-8")
            roots[fw] = root
    return roots


@pytest.fixture
def project(pristine: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return a factory copying the pristine project for a framework into this test's tmp dir."""
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", _WAIVER_KEY)

    def _make(fw: str) -> tuple[Path, Path]:
        root = tmp_path / fw / "proj"
        shutil.copytree(pristine[fw], root, symlinks=True)
        return root, root.joinpath(*_DIRS[fw])

    return _make


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


def _adopt(fw: str, out: Path, slugs: str, *extra: str) -> int:
    return build_team.main([
        "--description", str(BRIEF), "--framework", fw, "--output", str(out),
        "--adopt-only", slugs, "--no-scan", "--security-offline", *extra,
    ])


def _orch_rel(fw: str) -> str:
    return "/".join(_DIRS[fw]) + f"/orchestrator{_EXT[fw]}"


# --- claude: rows only, no gate ----------------------------------------------------------------


def test_adds_exactly_the_named_row_and_nothing_else(project) -> None:
    root, out = project("claude")
    before = _snapshot(root)
    assert _adopt("claude", out, "alpha-agent") == 0
    after = _snapshot(root)
    orch = _orch_rel("claude")
    # Every other path under the project is byte-unchanged; no decision, record or backup on Claude.
    assert _changed(before, after) == {orch}
    old, new = before[orch].decode(), after[orch].decode()
    added = [ln for ln in new.splitlines(keepends=True) if ln not in old.splitlines(keepends=True)]
    assert len(added) == 1 and "`@alpha-agent` *(adopted)*" in added[0]
    # The rest of the orchestrator is byte-identical: removing the one row restores the original.
    assert new.replace(added[0], "", 1) == old
    assert "@beta-agent" not in new  # another orphan's row is NOT added


def test_second_run_is_a_no_op(project) -> None:
    root, out = project("claude")
    assert _adopt("claude", out, "alpha-agent") == 0
    once = _snapshot(root)
    assert _adopt("claude", out, "alpha-agent") == 0
    assert _snapshot(root) == once


def test_existing_adopted_rows_are_kept_when_adding_another(project) -> None:
    root, out = project("claude")
    assert _adopt("claude", out, "alpha-agent") == 0
    first = (out / "orchestrator.md").read_text(encoding="utf-8")
    assert _adopt("claude", out, "beta-agent") == 0
    second = (out / "orchestrator.md").read_text(encoding="utf-8")
    assert "`@alpha-agent` *(adopted)*" in second and "`@beta-agent` *(adopted)*" in second
    beta_row = next(ln for ln in second.splitlines(keepends=True) if "`@beta-agent`" in ln)
    assert second.replace(beta_row, "", 1) == first


@pytest.mark.parametrize("slugs, named", [
    ("nope", "nope"),
    ("security", "security"),                  # emitted by this build: not an orphan
    ("alpha-agent,nope", "nope"),              # one bad slug refuses the whole run
])
def test_non_orphan_slug_refuses_the_whole_run(project, capsys, slugs: str, named: str) -> None:
    root, out = project("claude")
    before = _snapshot(root)
    assert _adopt("claude", out, slugs) == 1
    err = capsys.readouterr().err
    assert "[ADOPT-ONLY] refused" in err and named in err
    assert _snapshot(root) == before


def test_malformed_slug_refuses(project, capsys) -> None:
    root, out = project("claude")
    before = _snapshot(root)
    assert _adopt("claude", out, "Bad Slug") == 1
    assert "malformed" in capsys.readouterr().err
    assert _snapshot(root) == before


@pytest.mark.parametrize("flag", ["--overwrite", "--migrate", "--prune", "--adopt-orphans"])
def test_flag_exclusivity(flag: str, capsys) -> None:
    parser = _build_parser()
    argv = ["--description", str(BRIEF), "--adopt-only", "alpha-agent", flag]
    if flag in ("--adopt-orphans", "--prune"):
        argv.append("--update")
    with pytest.raises(SystemExit):
        _validate_option_combinations(parser, parser.parse_args(argv))
    assert "--adopt-only and" in capsys.readouterr().err


def test_dry_run_json_reports_rows_and_tools_and_writes_nothing(project, capsys) -> None:
    root, out = project("claude")
    before = _snapshot(root)
    capsys.readouterr()
    assert _adopt("claude", out, "alpha-agent,beta-agent", "--dry-run", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert _snapshot(root) == before
    assert report["mode"] == "adopt-only" and report["dry_run"] is True
    assert report["slugs"] == ["alpha-agent", "beta-agent"]
    added = report["routing_rows"]["added"]
    assert len(added) == 2 and report["routing_rows"]["removed"] == []
    assert report["agents_list"] is False and report["agents_to_add"] == []
    assert report["adopt_orphans_merge"] is None
    assert report["tools"]["alpha-agent"] == {"line": "tools: Read, Grep, Glob", "wider_than_read_only": False}
    assert report["tools"]["beta-agent"] == {"line": "tools: Read, Edit, Bash", "wider_than_read_only": True}


def test_update_after_adopt_only_carries_the_rows_with_zero_diff(project, monkeypatch) -> None:
    from agentteams.cli.decision_log import sign_decision_row

    root, out = project("claude")
    assert _adopt("claude", out, "alpha-agent") == 0
    adopted = (out / "orchestrator.md").read_bytes()
    key = "adopt-only-decision-key"
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", key)
    row = {"timestamp": "2026-05-03T00:00:00Z", "requesting_agent": "security",
           "action_reviewed": "overwrite", "verdict": "PASS", "conditions": "",
           "conditions_verified": "verified"}
    row["signature"] = sign_decision_row(row, key=key)
    cols = list(row)
    (out / "references" / "security-decisions.log.csv").write_text(
        ",".join(cols) + "\n" + ",".join(row[c] for c in cols) + "\n", encoding="utf-8")
    assert build_team.main([
        "--description", str(BRIEF), "--framework", "claude", "--output", str(out),
        "--update", "--merge", "--yes", "--no-scan", "--security-offline",
    ]) == 0
    assert (out / "orchestrator.md").read_bytes() == adopted


# --- unit: the splice --------------------------------------------------------------------------


def test_splice_keeps_indentation_and_other_rows() -> None:
    text = ("x\n  <!-- AGENTTEAMS:BEGIN routing_table_rows v=1 -->\n  | a | `@a` | b |\n"
            "  | z | `@old` *(adopted)* | n |\n  <!-- AGENTTEAMS:END routing_table_rows -->\ny\n")
    new = adopt_only.splice_adopted_rows(text, "\n| p | `@new` *(adopted)* | q |\n| z | `@old` *(adopted)* | n |")
    assert new == ("x\n  <!-- AGENTTEAMS:BEGIN routing_table_rows v=1 -->\n  | a | `@a` | b |\n"
                   "  | p | `@new` *(adopted)* | q |\n  | z | `@old` *(adopted)* | n |\n"
                   "  <!-- AGENTTEAMS:END routing_table_rows -->\ny\n")
    assert adopt_only.splice_adopted_rows(new, "\n| p | `@new` *(adopted)* | q |\n| z | `@old` *(adopted)* | n |") == new


def test_splice_refuses_without_a_fence() -> None:
    with pytest.raises(adopt_only.AdoptOnlyError, match="exactly one"):
        adopt_only.splice_adopted_rows("no fence\n", "")


def test_absent_tools_line_is_flagged(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    p.write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
    assert adopt_only.tools_grant(p) == {"line": None, "wider_than_read_only": True}
    p.write_text("---\nname: x\ntools:\n  - Read\n  - Grep\n---\n", encoding="utf-8")
    assert adopt_only.tools_grant(p) == {"line": "tools: Read, Grep", "wider_than_read_only": False}


# --- copilot: the agents: list goes through the gate with exactly the named slugs --------------


_COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions",
            "conditions_verified", "scope", "derives_from", "effect_class", "effect_grants",
            "effect_targets", "effect_relaxes", "effect_destructive", "effect_cross_repo",
            "effect_bulk", "sig_scheme", "key_id", "signature"]


def _govern(out: Path, grants: str | None) -> None:
    """Make ``out`` a signing-governed team and record (optionally) a signed adopt decision."""
    pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agentteams.cli import decision_log as dl
    from agentteams.cli import signed_ledger as sl
    from agentteams.cli.adopt_merge_gate import ACTION

    refs = out / "references"
    priv_path = refs / "agent-privilege.json"
    priv = json.loads(priv_path.read_text(encoding="utf-8")) if priv_path.exists() else {}
    priv["enforce_decision_signing"] = True
    priv_path.write_text(json.dumps(priv), encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    (refs / "authorized-verify-keys").mkdir(parents=True, exist_ok=True)
    (refs / "authorized-verify-keys" / "op-ao.pub.pem").write_text(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    rows = [{c: "" for c in _COLUMNS} | {
        "timestamp": "2026-10-01", "action_reviewed": "approved-adoption", "verdict": "PASS",
        "conditions_verified": "verified", "requesting_agent": "security"}]
    if grants is not None:
        row = {c: "" for c in _COLUMNS} | {
            "timestamp": "2026-10-05", "action_reviewed": ACTION, "verdict": "PASS", "scope": ACTION,
            "conditions_verified": "verified", "requesting_agent": "security",
            "derives_from": "approved-adoption", "effect_grants": grants,
            "sig_scheme": "ed25519", "key_id": "op-ao"}
        pem = key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8,
                                encryption_algorithm=serialization.NoEncryption()).decode()
        row["signature"] = sl.ed25519_sign(pem, dl._decision_signature_values(row))
        rows.append(row)
    with (refs / "security-decisions.log.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.usefixtures("signing_preapproved")
def test_copilot_agents_list_extended_with_exactly_the_named_slug(project) -> None:
    from agentteams.cli.adopt_merge_gate import RECORD_REL, existing_agents

    root, out = project("copilot-vscode")
    _govern(out, "agents:alpha-agent")
    before = _snapshot(root)
    assert _adopt("copilot-vscode", out, "alpha-agent") == 0
    after = _snapshot(root)
    orch = _orch_rel("copilot-vscode")
    agents_rel = "/".join(_DIRS["copilot-vscode"])
    allowed = {orch, f"{agents_rel}/references/security-decisions.log.csv", f"{agents_rel}/{RECORD_REL}"}
    changed = _changed(before, after)
    assert orch in changed and f"{agents_rel}/{RECORD_REL}" in changed
    assert {c for c in changed if "/.agentteams-backups/" not in c} <= allowed
    listed = existing_agents(after[orch].decode())
    assert "alpha-agent" in listed and "beta-agent" not in listed
    assert "`@alpha-agent` *(adopted)*" in after[orch].decode()
    assert "@beta-agent" not in after[orch].decode()


@pytest.mark.usefixtures("signing_preapproved")
@pytest.mark.parametrize("grants, slugs", [
    ("agents:alpha-agent;agents:beta-agent", "alpha-agent"),   # superset of the named slugs
    ("agents:alpha-agent", "alpha-agent,beta-agent"),           # subset of the named slugs
    (None, "alpha-agent"),                                      # no decision at all
])
def test_copilot_gate_refuses_anything_but_the_exact_set(project, capsys, grants, slugs) -> None:
    root, out = project("copilot-vscode")
    _govern(out, grants)
    before = _snapshot(root)
    assert _adopt("copilot-vscode", out, slugs) == 1
    assert "[SEC-GATE/DESTRUCTIVE:adopt-orphans-merge] blocked" in capsys.readouterr().err
    assert _snapshot(root) == before


@pytest.mark.usefixtures("signing_preapproved")
def test_copilot_dry_run_json_reports_agents_additions(project, capsys) -> None:
    root, out = project("copilot-vscode")
    _govern(out, "agents:alpha-agent")
    before = _snapshot(root)
    capsys.readouterr()
    assert _adopt("copilot-vscode", out, "alpha-agent", "--dry-run", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert _snapshot(root) == before
    assert report["agents_list"] is True and report["agents_to_add"] == ["alpha-agent"]
    assert report["adopt_orphans_merge"]["clearance"]["status"] == "cleared"
    assert len(report["routing_rows"]["added"]) == 1
