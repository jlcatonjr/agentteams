"""WASM plan P17 §2: turning a sandbox off is governed end to end, on every framework, through the real CLI (D7).

The per-plan tests drive ``finalize_privilege_wiring`` on Claude (``test_os_sandbox_governance_wiring.py``,
``test_wasm_governance_wiring.py``) and the gate itself (``test_sandbox_relaxation_gate.py``). This file renders a
real team twice, on then off, into each framework's canonical agents dir, so the record the first render leaves is
what the second render's gate reads:

* governed (``enforce_decision_signing`` on disk before the render): ``--os-sandbox off`` / ``--wasm-sandbox off``
  after on exits 1 naming ``sandbox-os-off:<fw>`` / ``sandbox-wasm-off:<fw>`` and leaves the record on; an HMAC-signed
  row is valid but insufficient and an Ed25519 row that does not verify is refused; an operator Ed25519-signed row (stubbed, as in the per-plan tests: the ``signing``
  extra is not installed here) clears it, records ``ed25519-decision``, and registers the standing OS-off exception;
* ungoverned: the same transition succeeds with the ``SANDBOX RELAXATION`` notices and ``notice-only`` records;
* Rule-12 regression: ``confined`` + ``os_sandbox: off`` + ``write_policy: orchestrator-only`` refuses with the
  needs-the-session-sandbox message on the frameworks that support the write policy (claude, codex; goose as the suite opts it in), from the brief
  and from ``--os-sandbox off``; every other framework refuses the write policy itself.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

import build_team
from agentteams.cli import decision_log as dl
from agentteams.cli import exception_registry as er
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.frameworks.registry import agents_dir_for
from tests._fresh_intel import OFFLINE_FLAG, seed_offline_intel
from tests._sandbox_gate_helpers import _COLUMNS, _KEY_ID, _baseline_row, _log, _row, _stub_sig
from tests._wasm_framework_helpers import _INTEL_AT

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
WASM_BRIEF = json.loads((REPO / "examples" / "software-project" / "wasm-runtime.brief.json").read_text(encoding="utf-8"))
VENDOR = ("claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md")
#: WASM on with no native-exec keep, so the first render needs no signature (D12's default keep has its own tests).
_WASM_ON = {"enabled": True, "native_exec_keep": []}
#: Frameworks that take ``write_policy: orchestrator-only`` (claude; codex through its confined runner; goose only
#: because the suite sets ``AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH=1``, ``tests/conftest.py``).
_WRITE_POLICY_HOSTS = frozenset({"claude", "codex", "goose"})
_GRANT_TOKENS = "wasm:primary-producer:read:docs;wasm:primary-producer:scratch"


class Team:
    """One project rendered repeatedly for one framework; ``render`` returns ``(rc, stderr, relaxation records)``."""

    def __init__(self, base: Path, framework: str, *, governed: bool) -> None:
        self.fw = framework
        self.root = base / f"{framework}-{'gov' if governed else 'ungov'}"
        self.root.mkdir()
        self.dir = self.root / ".agentteams-wasm" / "agents" if framework == "wasm" else agents_dir_for(framework,
                                                                                                         self.root)
        brief = dict(WASM_BRIEF) if framework == "wasm" else {**BRIEF, "wasm_sandbox": _WASM_ON}
        brief["enforce_decision_signing"] = governed
        self.brief = base / f"{self.root.name}.brief.json"
        self.brief.write_text(json.dumps(brief), encoding="utf-8")
        refs = self.dir / "references"
        (refs / "authorized-verify-keys").mkdir(parents=True)
        (refs / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_text("stub-key")
        if governed:   # read from disk before the render (a render cannot flip its own governance)
            (refs / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": True}))

    def render(self, *extra: str, brief: Path | None = None) -> tuple[int, str, list[dict[str, str]]]:
        if self.fw == "wasm":
            (self.dir / "references" / "security-vulnerability-watch.json").write_text(json.dumps(
                {"generated_at": _INTEL_AT, "sources": [], "vulnerabilities": [], "osv_packages": []}))
            intel = ["--output", str(self.dir), OFFLINE_FLAG]
        else:
            intel = seed_offline_intel(self.dir, self.fw)
        records: list[dict[str, str]] = []
        real = gate.authorize

        def spy(*a: Any, **k: Any) -> list[dict[str, str]]:
            out = real(*a, **k)
            records.extend(out)
            return out

        err = io.StringIO()
        with pytest.MonkeyPatch.context() as mp, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            mp.setattr(gate, "authorize", spy)
            rc = build_team.main(["--description", str(brief or self.brief), "--framework", self.fw, "--project",
                                  str(self.root), "--yes", "--no-scan", *intel, *extra])
        return rc, err.getvalue(), records

    def sign(self, *rows: dict[str, str]) -> None:
        """The operator's steps: upgrade the render-created decisions log to the signing schema, append rows."""
        log = self.dir / "references" / "security-decisions.log.csv"
        if not log.exists():   # the helper creates it with the signing header and the baseline row
            _log(self.dir, *rows)
            return
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert build_team.main(["--migrate-decision-log", "--output", str(self.dir), "--yes"]) == 0
        if "approved-baseline" not in log.read_text(encoding="utf-8"):   # the ancestor every lineage names
            rows = ({c: "" for c in _COLUMNS} | _baseline_row(), *rows)
        _log(self.dir, *rows)

    def state(self) -> dict[str, Any]:
        return json.loads((self.dir / "references" / "build-log.json").read_text(encoding="utf-8"))["sandbox_state"]


@pytest.fixture
def base(tmp_path: Path) -> Path:
    return Path(str(tmp_path)).resolve()


def _ok(result: tuple[int, str, list]) -> list[dict[str, str]]:
    rc, err, records = result
    assert rc == 0, err[-1500:]
    return records


# --- governed --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("framework", VENDOR)
def test_governed_on_to_off_refuses_unsigned_and_hmac_then_proceeds_with_the_operator_signature(
        base: Path, framework: str, stub_ed25519: None) -> None:
    team = Team(base, framework, governed=True)
    assert _ok(team.render("--os-sandbox", "on")) == []                    # off -> on is free
    assert (team.state()["os"], team.state()["wasm"]) == ("on", "on")

    for flag, action in (("--os-sandbox", f"sandbox-os-off:{framework}"),
                         ("--wasm-sandbox", f"sandbox-wasm-off:{framework}")):
        rc, err, _ = team.render("--os-sandbox", "on", flag, "off")
        assert rc == 1 and action in err, err[-800:]
        assert (team.state()["os"], team.state()["wasm"]) == ("on", "on")  # the refused render recorded nothing

    for kind, flag in (("os", "--os-sandbox"), ("wasm", "--wasm-sandbox")):
        action = f"sandbox-{kind}-off:{framework}"
        team.sign(_row(action, scheme="hmac"))                              # valid HMAC: insufficient (Rule 15b)
        rc, err, _ = team.render("--os-sandbox", "on", flag, "off")
        assert rc == 1 and action in err and "REQUIRES ed25519" in err, err[-800:]
        team.sign(_row(action, sign=lambda values: "stub" + "0" * 64))      # an Ed25519 row that does not verify
        rc, err, _ = team.render("--os-sandbox", "on", flag, "off")
        assert rc == 1 and action in err, err[-800:]
        assert (team.state()["os"], team.state()["wasm"]) == ("on", "on")
    assert er.list_exceptions(team.dir) == []

    team.sign(_row(f"sandbox-os-off:{framework}"), _row(f"sandbox-wasm-off:{framework}"))
    records = _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "off"))
    assert {(r["action"], r["authorization"]) for r in records} == {
        (f"sandbox-os-off:{framework}", "ed25519-decision"), (f"sandbox-wasm-off:{framework}", "ed25519-decision")}
    assert (team.state()["os"], team.state()["wasm"]) == ("off", "off")
    # OS-off is standing (registered with a sunset); WASM-off is not (P15 carve-out).
    assert [e["action"] for e in er.list_exceptions(team.dir)] == [f"sandbox-os-off:{framework}"]


@pytest.mark.parametrize("framework", ["claude", "agents-md"])
def test_governed_rerender_after_a_signed_off_continues_under_the_standing_exception(
        base: Path, framework: str, stub_ed25519: None) -> None:
    team = Team(base, framework, governed=True)
    _ok(team.render("--os-sandbox", "on"))
    team.sign(_row(f"sandbox-os-off:{framework}"), _row(f"sandbox-wasm-off:{framework}"))
    _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "off"))
    records = _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "off"))   # same flags, nothing new signed
    assert [(r["action"], r["authorization"]) for r in records] == [(f"sandbox-os-off:{framework}",
                                                                     "active-exception")]


def _forge_off_record(team: Team) -> None:
    """What a session that can write the build-log might do: record "was off" without any operator decision."""
    log = team.dir / "references" / "build-log.json"
    data = json.loads(log.read_text(encoding="utf-8"))
    data["sandbox_state"] = {**data["sandbox_state"], "os": "off", "wasm": "off", "wasm_languages": [],
                             "wasm_grants": {}}
    log.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("framework", ["claude", "agents-md"])
@pytest.mark.parametrize("tamper", ["no-record", "forged-off-record"])
def test_stale_evidence_still_gates_without_the_operator_authorization(
        base: Path, framework: str, tamper: str, stub_ed25519: None) -> None:
    """Rule 12 on the P17 fix: the record outranks leftover files only where the operator excused the off state."""
    team = Team(base, framework, governed=True)
    _ok(team.render("--os-sandbox", "on"))
    assert (team.root / "sandbox" / "confine-run.sh").exists()   # the evidence the off render would leave
    if tamper == "no-record":
        (team.dir / "references" / "build-log.json").unlink()
    else:
        _forge_off_record(team)
    rc, err, _ = team.render("--os-sandbox", "off", "--wasm-sandbox", "off")
    assert rc == 1 and f"sandbox-os-off:{framework}" in err, err[-800:]
    assert "no unconsumed decision" in err, err[-800:]
    assert er.list_exceptions(team.dir) == []


def test_a_forged_off_record_after_a_signed_off_and_back_on_needs_a_new_os_signature(
        base: Path, stub_ed25519: None) -> None:
    """The standing exception excuses the OS evidence; going back on retires it, so a forged record gains nothing."""
    team = Team(base, "claude", governed=True)
    _ok(team.render("--os-sandbox", "on"))
    team.sign(_row("sandbox-os-off:claude"), _row("sandbox-wasm-off:claude"))
    _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "off"))
    _ok(team.render("--os-sandbox", "on"))   # tightening retires the OS-off exception
    assert [e for e in er.list_exceptions(team.dir) if (e.get("status") or "") == "active"] == []
    _forge_off_record(team)
    rc, err, _ = team.render("--os-sandbox", "off")
    assert rc == 1 and "sandbox-os-off:claude" in err, err[-800:]


def _forge_wasm_off_record(team: Team) -> None:
    log = team.dir / "references" / "build-log.json"
    data = json.loads(log.read_text(encoding="utf-8"))
    data["sandbox_state"] = {**data["sandbox_state"], "wasm": "off", "wasm_languages": [], "wasm_grants": {}}
    log.write_text(json.dumps(data), encoding="utf-8")


def _set_decision_cells(team: Team, scope: str, **cells: str) -> None:
    """Edit the unsigned cells of the newest row scoped to *scope*, as a session that can write the log might."""
    path = team.dir / "references" / "security-decisions.log.csv"
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fields, rows = list(reader.fieldnames or []), list(reader)
    [*_, row] = [r for r in rows if r.get("scope") == scope]
    row.update(cells)
    fields += [c for c in cells if c not in fields]   # security_gate adds `consumed` the same way on first use
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_a_forged_wasm_off_record_without_a_decision_is_refused(base: Path, stub_ed25519: None) -> None:
    team = Team(base, "claude", governed=True)
    _ok(team.render("--os-sandbox", "on"))
    _forge_wasm_off_record(team)
    rc, err, _ = team.render("--os-sandbox", "on", "--wasm-sandbox", "off")
    assert rc == 1 and "sandbox-wasm-off:claude" in err, err[-800:]


def test_a_spent_wasm_off_decision_does_not_excuse_a_later_halt(base: Path, stub_ed25519: None) -> None:
    """C-2: the spent-decision excuse skips authorize(), so it must repeat authorize()'s HALT check itself."""
    team = Team(base, "claude", governed=True)
    _ok(team.render("--os-sandbox", "on"))
    team.sign(_row("sandbox-os-off:claude"), _row("sandbox-wasm-off:claude"))
    _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "off"))
    team.sign(_row("sandbox-wasm-off:claude", verdict="HALT"))
    rc, err, _ = team.render("--os-sandbox", "off", "--wasm-sandbox", "off")
    assert rc == 1, err[-800:]


def test_an_unverified_conditional_pass_marked_consumed_does_not_excuse(base: Path, stub_ed25519: None) -> None:
    """``consumed`` is unsigned: flipping it on a CONDITIONAL PASS with pending conditions must not make it spent."""
    team = Team(base, "claude", governed=True)
    _ok(team.render("--os-sandbox", "on"))
    row = _row("sandbox-wasm-off:claude", verdict="CONDITIONAL PASS")
    row["conditions_verified"] = "pending"
    row["signature"] = _stub_sig(dl._decision_signature_values(row))
    team.sign(row)
    _forge_wasm_off_record(team)
    _set_decision_cells(team, "sandbox-wasm-off:claude", consumed="yes")
    rc, err, _ = team.render("--os-sandbox", "on", "--wasm-sandbox", "off")
    assert rc == 1 and "sandbox-wasm-off:claude" in err, err[-800:]


@pytest.mark.xfail(strict=True, reason="disclosed W11R gap: the spent decision is bound to its scope, not to a render; "
                   "a session that rewrites the build-log after a later WASM-on render can replay it (owner: security)")
def test_a_spent_wasm_off_decision_cannot_be_replayed_after_wasm_went_back_on(base: Path, stub_ed25519: None) -> None:
    team = Team(base, "claude", governed=True)
    _ok(team.render("--os-sandbox", "on"))
    team.sign(_row("sandbox-os-off:claude"), _row("sandbox-wasm-off:claude"))
    _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "off"))
    _ok(team.render("--os-sandbox", "off", "--wasm-sandbox", "on"))
    _forge_wasm_off_record(team)
    rc, err, _ = team.render("--os-sandbox", "off", "--wasm-sandbox", "off")
    assert rc == 1 and "sandbox-wasm-off:claude" in err, err[-800:]


def test_governed_wasm_framework_os_off_needs_the_signature_and_wasm_cannot_be_turned_off(
        base: Path, stub_ed25519: None) -> None:
    team = Team(base, "wasm", governed=True)
    rc, err, _ = team.render("--os-sandbox", "on")   # the example's primary-producer grant is a widening (D5/C-3)
    assert rc == 1 and "wasm-grant-widen:wasm" in err
    team.sign(_row("wasm-grant-widen:wasm", grants=_GRANT_TOKENS))
    records = _ok(team.render("--os-sandbox", "on"))
    assert [(r["action"], r["authorization"]) for r in records] == [("wasm-grant-widen:wasm", "ed25519-decision")]
    rc, err, _ = team.render("--os-sandbox", "off")
    assert rc == 1 and "sandbox-os-off:wasm" in err and team.state()["os"] == "on"
    rc, err, _ = team.render("--wasm-sandbox", "off")
    assert rc == 1 and "--framework wasm needs WASM confinement" in err
    team.sign(_row("sandbox-os-off:wasm"))
    # Two standing relaxations (the widening and OS-off) meet the shared aggregate cap (METAPLAN §7 accepted
    # limitation): surfaced to the operator, who raises it deliberately (Rule 15d).
    rc, err, _ = team.render("--os-sandbox", "off")
    assert rc == 1 and "over the cap of 1" in err and team.state()["os"] == "on"
    registry = team.dir / "references" / "exception-registry.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    registry.write_text(json.dumps({**data, "cap": {**data.get("cap", {}), "max_active_relaxing": 2}}))
    records = _ok(team.render("--os-sandbox", "off"))
    assert ("sandbox-os-off:wasm", "ed25519-decision") in [(r["action"], r["authorization"]) for r in records]
    assert sorted(e["action"] for e in er.list_exceptions(team.dir)) == ["sandbox-os-off:wasm", "wasm-grant-widen:wasm"]
    runtime = json.loads((team.root / ".agentteams-wasm" / "runtime.json").read_text(encoding="utf-8"))
    assert team.state()["os"] == "off" and runtime["host_os_confine"] == "off"


# --- ungoverned ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("framework", VENDOR)
def test_ungoverned_on_to_off_prints_the_notice_and_records_notice_only(base: Path, framework: str) -> None:
    team = Team(base, framework, governed=False)
    _ok(team.render("--os-sandbox", "on"))
    rc, err, records = team.render("--os-sandbox", "off", "--wasm-sandbox", "off")
    assert rc == 0, err[-1500:]
    assert f"SANDBOX RELAXATION: os sandbox OFF for {framework}" in err
    assert f"sandbox-wasm-off:{framework}" in err or "wasm sandbox OFF" in err, err[-1500:]
    assert {(r["action"], r["authorization"]) for r in records} == {
        (f"sandbox-os-off:{framework}", "notice-only"), (f"sandbox-wasm-off:{framework}", "notice-only")}
    assert (team.state()["os"], team.state()["wasm"]) == ("off", "off")
    assert er.list_exceptions(team.dir) == []


# --- Rule-12 regression: the write policy needs the OS boundary ------------------------------------------------------

@pytest.mark.parametrize("framework", VENDOR)
@pytest.mark.parametrize("source", ["brief", "cli"])
def test_confined_os_off_with_orchestrator_only_is_refused(base: Path, framework: str, source: str) -> None:
    team = Team(base, framework, governed=False)
    brief = {**BRIEF, "privilege_profile": "confined", "write_policy": "orchestrator-only",
             "enforce_decision_signing": False, **({"os_sandbox": "off"} if source == "brief" else {})}
    path = base / f"{framework}-{source}-wp.brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    rc, err, records = team.render(*(["--os-sandbox", "off"] if source == "cli" else []), brief=path)
    assert rc == 1 and records == [] and not (team.dir / "references" / "build-log.json").exists(), err[-800:]
    if framework in _WRITE_POLICY_HOSTS:   # the Rule-12 refusal itself, naming the switch that turned it off
        switch = "--os-sandbox off" if source == "cli" else 'os_sandbox "off"'
        assert f'write_policy "orchestrator-only" needs the session sandbox: {switch} turns it off' in err, err[-800:]
    else:                                  # the write policy is refused on this host before the switch matters
        assert 'write_policy "orchestrator-only" is supported on claude' in err and f"{framework} has" in err, \
            err[-800:]


def test_the_cap_refusal_names_the_registry_path_it_reads(base: Path) -> None:
    team = base / "team"
    (team / "references").mkdir(parents=True)
    with pytest.raises(er.ExceptionRegistryError) as exc:
        er.assert_within_aggregate_cap(team, adding=2)
    assert "over the cap" in str(exc.value) and "references/references/" not in str(exc.value)
