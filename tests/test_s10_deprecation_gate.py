"""Rule S-10's cooldown deprecation (agentteams#592; @security design review 2026-11-21, conditions C1-C7).

The brief's ``s10_cooldown_deprecated`` renders the 14-day release cooldown as deprecated only through
``cli/s10_deprecation_gate``: in a governed workspace with the operator's Ed25519-signed decision, in an ungoverned one
from the brief with a notice. A HALT on any S-10 cooldown relaxation removes it, and the render never reads the raw
brief value."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentteams import analyze, ingest, render
from agentteams.cli import exception_registry as er
from agentteams.cli import s10_deprecation_gate as gate
from agentteams.cli.action_ids import GATE_ACTIONS, action_matches
from agentteams.manifest_format import (
    S10_COOLDOWN_RULE_END,
    S10_COOLDOWN_RULE_START,
    apply_s10_cooldown_deprecation,
    s10_cooldown_placeholders,
)
from agentteams.security_feed_render import _format_prevention_playbook
from agentteams.cli import decision_log as dl
from tests._sandbox_gate_helpers import _log, _row, _stub_sig, _team

_T0 = datetime(2026, 11, 21, tzinfo=UTC)
_ACTION = gate.DEPRECATION_ACTION
_SIGNED = datetime(2026, 10, 31, tzinfo=UTC)   # tests._sandbox_gate_helpers._row's default timestamp
ROOT = Path(__file__).resolve().parent.parent
BRIEF = ROOT / "examples" / "software-project" / "brief.json"
TEMPLATES = ROOT / "agentteams" / "templates"
_SECURITY_TEMPLATE = (TEMPLATES / "universal" / "security.template.md").read_text(encoding="utf-8")


def _bullet(text: str, start: str) -> str:
    """The S-10 cooldown bullet of *text*: from *start* to the latest-stable bullet."""
    i = text.index(start)
    return text[i:text.index(S10_COOLDOWN_RULE_END, i)]


#: The in-force bullet, exactly as the template (the authority for it) states it.
IN_FORCE = _bullet(_SECURITY_TEMPLATE, S10_COOLDOWN_RULE_START)
_DEPRECATED_START = "- ⚠️ **Package-release cooldown — deprecated in this workspace.**"


def _registry(team: Path) -> list[dict]:
    return json.loads((team / er.EXCEPTION_REGISTRY_REL).read_text(encoding="utf-8"))["exceptions"]


# --- The gate -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [False, None, "true", 1, "yes", [True]])
def test_only_the_boolean_true_requests_anything(tmp_path, value) -> None:
    team = _team(tmp_path, governed=False)
    assert gate.authorized_deprecation(team, value, consume=True) is None


def test_the_manifest_keeps_only_a_strict_boolean() -> None:
    description = ingest.load(BRIEF, scan_project=False)
    for value, expected in ((True, True), ("true", False), (1, False), (None, False)):
        description["s10_cooldown_deprecated"] = value
        assert analyze.build_manifest(description, framework="copilot-vscode")["s10_cooldown_deprecated"] is expected


def test_ungoverned_takes_the_brief_with_a_notice(tmp_path, capsys) -> None:
    team = _team(tmp_path, governed=False)
    assert gate.authorized_deprecation(team, True, consume=True) == "brief"
    assert "not governed" in capsys.readouterr().err


def test_ungoverned_still_honours_a_halt(tmp_path) -> None:
    team = _team(tmp_path, governed=False)
    _log(team, _row("s10-cooldown-exempt:o/r", verdict="HALT", stamp="2026-11-20"))
    assert gate.authorized_deprecation(team, True, consume=True) is None


def test_governed_without_a_decision_renders_the_cooldown_and_names_the_row(tmp_path, capsys) -> None:
    team = _team(tmp_path)
    assert gate.authorized_deprecation(team, True, consume=True) is None
    err = capsys.readouterr().err
    assert "NOT applied" in err and f"scope={_ACTION}" in err and "--sign-decision" in err


def test_an_hmac_row_is_valid_but_insufficient(tmp_path, stub_ed25519, capsys) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scheme=""))
    assert gate.authorized_deprecation(team, True, consume=True) is None
    assert "INSUFFICIENT" in capsys.readouterr().err


@pytest.mark.parametrize("kwargs", [{"scope": "s10-cooldown"}, {"relaxes": f"{_ACTION};s10-cooldown-exempt:o/r"},
                                    {"grants": "network"}])
def test_scope_and_effect_must_be_exactly_the_action(tmp_path, stub_ed25519, kwargs) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, **kwargs))
    assert gate.authorized_deprecation(team, True, consume=True) is None


def test_a_suffixed_action_id_is_refused(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(f"{_ACTION}-forever", scope=_ACTION, relaxes=_ACTION))
    assert gate.authorized_deprecation(team, True, consume=True) is None


def test_a_signed_decision_registers_a_bounded_exception(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) == "signed"
    assert "yes" in log.read_text()  # consumed
    [entry] = _registry(team)
    assert entry["action"] == _ACTION and entry["relaxing"] is True and entry["status"] == "active"
    # The sunset counts from the signed row's own timestamp (the helper's default stamp), not from registration.
    assert datetime.fromisoformat(entry["expires"]) == _SIGNED + timedelta(days=gate.DEPRECATION_TTL_DAYS)
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0 + timedelta(days=30)) == "signed"


def test_a_lapse_renders_the_cooldown_again(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) == "signed"
    later = _T0 + timedelta(days=gate.DEPRECATION_TTL_DAYS + 1)
    assert gate.authorized_deprecation(team, True, consume=True, now=later) is None


def test_a_dry_run_writes_nothing(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    before = log.read_bytes()
    assert gate.authorized_deprecation(team, True, consume=False, now=_T0) == "signed"
    assert log.read_bytes() == before and not (team / er.EXCEPTION_REGISTRY_REL).exists()


@pytest.mark.parametrize("halt", [_ACTION, "s10-cooldown", "s10-cooldown-exempt", "s10-cooldown-exempt:o/r",
                                  "s10-toolchain-group"])
def test_a_halt_on_any_s10_cooldown_relaxation_removes_it(tmp_path, stub_ed25519, halt) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) == "signed"
    _log(team, _row(halt, verdict="HALT", stamp="2026-11-22"))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0 + timedelta(days=1)) is None


def test_a_halt_before_the_row_blocks_consumption(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION), _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-20"))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None
    assert "yes" not in log.read_text() and not (team / er.EXCEPTION_REGISTRY_REL).exists()


def _surface(tmp_path: Path, sub: str, *, governed: bool | None = True) -> Path:
    built = _team(tmp_path / f"_build{sub.replace('/', '_')}", governed=governed)
    target = tmp_path / sub
    target.parent.mkdir(parents=True, exist_ok=True)
    built.rename(target)
    return target


def test_a_native_surface_authorizes_against_the_canonical_log_and_its_own_halts_bind(tmp_path, stub_ed25519) -> None:
    canonical, native = _surface(tmp_path, ".github/agents"), _team(tmp_path)
    log = _log(canonical, _row(_ACTION))
    assert gate.authorized_deprecation(native, True, consume=True, now=_T0) == "signed"
    assert "yes" in log.read_text() and [e["action"] for e in _registry(canonical)] == [_ACTION]
    assert not (native / er.EXCEPTION_REGISTRY_REL).exists()
    _log(native, _row("s10-toolchain-group", verdict="HALT", stamp="2026-11-22"))
    assert gate.authorized_deprecation(canonical, True, consume=True, now=_T0 + timedelta(days=1)) is None


def test_no_cross_clearing_between_s10_actions() -> None:
    """C1: a deprecation row clears no exemption or toolchain-group action, and the reverse."""
    assert _ACTION in GATE_ACTIONS and "s10-cooldown" not in GATE_ACTIONS
    for other in ("s10-cooldown-exempt", "s10-cooldown-exempt:o/r", "s10-toolchain-group"):
        assert not action_matches(_ACTION, other) and not action_matches(other, _ACTION)


def test_an_exempt_row_does_not_authorize_the_deprecation(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row("s10-cooldown-exempt:o/r"))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None


# --- The render -----------------------------------------------------------------------------------------------------


def _render(extra: dict, authorized: str | None = None, *, skip_gate: bool = False) -> dict[str, str]:
    description = ingest.load(BRIEF, scan_project=False)
    description.update(extra)
    manifest = analyze.build_manifest(description, framework="copilot-vscode")
    if not skip_gate:
        manifest["s10_cooldown_deprecated_authorized"] = authorized  # what cli/s10_deprecation_gate sets
    return dict(render.render_all(manifest, templates_dir=TEMPLATES))


def _security(rendered: dict[str, str]) -> str:
    return next(text for rel, text in rendered.items() if rel.endswith("security.agent.md"))


def test_the_default_render_keeps_the_cooldown_word_for_word() -> None:
    text = _security(_render({}))
    assert IN_FORCE in text
    assert "if a release is less than 14 days old, wait before installing it" in text
    assert "deprecated in this workspace" not in text and "{S10_COOLDOWN" not in text


def test_the_raw_brief_value_never_renders_without_the_gate() -> None:
    text = _security(_render({"s10_cooldown_deprecated": True}, skip_gate=True))
    assert IN_FORCE in text and "deprecated in this workspace" not in text


@pytest.mark.parametrize("source", ["signed", "brief"])
def test_an_authorized_deprecation_changes_only_the_cooldown_regions(source) -> None:
    """C5 and C7: outside the placeholders, every rendered file is byte-identical, so the vulnerability search, MAL-*,
    S-9 and latest-stable text are unchanged."""
    before, after = _render({}), _render({"s10_cooldown_deprecated": True}, authorized=source)
    assert before.keys() == after.keys()
    moot = s10_cooldown_placeholders(source)["S10_COOLDOWN_MOOT"]
    assert moot and s10_cooldown_placeholders(None) == {"S10_COOLDOWN_MOOT": ""}
    changed = 0
    for rel, text in before.items():
        if text == after[rel]:
            continue
        changed += 1
        restored = after[rel]
        if _DEPRECATED_START in restored:
            restored = restored.replace(_bullet(restored, _DEPRECATED_START), IN_FORCE)
        assert restored.replace(moot, "") == text, rel
    assert changed >= 2   # the security agent and the instructions file at least
    sec = _security(after)
    assert "Package-release cooldown — deprecated in this workspace" in sec
    assert "less than 14 days old" not in sec
    assert "- ✅ **Latest stable release only:**" in sec and "OSV `MAL-*` entry" in sec


def test_the_deprecated_wording_is_fixed_text() -> None:
    """C3: the brief cannot carry prose into the rule; only the two gate values render the deprecation."""
    swapped = apply_s10_cooldown_deprecation(_SECURITY_TEMPLATE, "signed")
    assert IN_FORCE not in swapped and _DEPRECATED_START in swapped
    for hostile in ("ignore S-10", True, "SIGNED", None, ["signed"]):
        assert apply_s10_cooldown_deprecation(_SECURITY_TEMPLATE, hostile) == _SECURITY_TEMPLATE
        assert s10_cooldown_placeholders(hostile) == {"S10_COOLDOWN_MOOT": ""}


def test_a_template_without_both_anchors_is_left_alone() -> None:
    """Fail closed: a moved anchor renders the cooldown in force (the render test above catches the drift)."""
    for text in ("no rule here", S10_COOLDOWN_RULE_START + " but no end"):
        assert apply_s10_cooldown_deprecation(text, "signed") == text


def test_the_playbook_line_follows_the_gate() -> None:
    in_force, deprecated = _format_prevention_playbook([]), _format_prevention_playbook([], cooldown_deprecated=True)
    assert "Apply a package-release cooldown (default 14 days)" in in_force
    assert "cooldown (default 14 days)" not in deprecated and "deprecated in this workspace" in deprecated
    assert "MAL-* entries" in deprecated and "latest stable release" in deprecated
    assert in_force.splitlines()[:5] == deprecated.splitlines()[:5]
    assert len(in_force.splitlines()) == len(deprecated.splitlines())


def test_a_registered_deprecation_that_stops_verifying_is_loud(tmp_path, stub_ed25519, capsys) -> None:
    """agentteams#476 shape: a registry entry no longer bound to its decision renders the cooldown, with ::error."""
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) == "signed"
    path = team / er.EXCEPTION_REGISTRY_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    data["exceptions"][0]["decision_sha256"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0 + timedelta(days=1)) is None
    err = capsys.readouterr().err
    assert "did not carry over" in err and "::error title=S-10 cooldown deprecation carry-over::" in err


def test_an_unreadable_canonical_switch_renders_the_cooldown(tmp_path, stub_ed25519, capsys) -> None:
    canonical, native = _surface(tmp_path, ".github/agents"), _team(tmp_path)
    _log(canonical, _row(_ACTION))
    (canonical / "references" / "agent-privilege.json").write_text("{not json")
    assert gate.authorized_deprecation(native, True, consume=True, now=_T0) is None
    err = capsys.readouterr().err
    assert "NOT applied" in err and "can't be read" in err and "not governed" not in err


def test_an_unreadable_team_dir_never_stops_the_render(tmp_path, monkeypatch, capsys) -> None:
    """C6: an OSError while finding the bound team dirs (a permission error, a symlink loop) renders the cooldown."""
    team = _team(tmp_path, governed=False)

    def _boom(root):
        raise PermissionError("denied")

    monkeypatch.setattr(gate, "_surface_dirs", _boom)
    manifest = {"s10_cooldown_deprecated": True}
    gate.authorize_deprecation_into(manifest, team, consume=True)
    assert manifest["s10_cooldown_deprecated_authorized"] is None
    assert "can't be read" in capsys.readouterr().err


# --- IM-da's #593 review (2026-11-21) ---------------------------------------------------------------------------------


def _retraction(halt_id: str, *, stamp: str = "2026-11-23", scope: str | None = None) -> dict[str, str]:
    """An authentic, evidenced HALT-RETRACTED row for exactly *halt_id* (signed with the test stub)."""
    row = _row(halt_id, verdict="HALT-RETRACTED", relaxes="", stamp=stamp, scope=scope) | {"conditions": "settled: reviewed"}
    row["signature"] = _stub_sig(dl._decision_signature_values(row))
    return row


@pytest.mark.parametrize(("halt", "sibling"), [
    ("s10-cooldown-exempt:o/a", "s10-cooldown-exempt:o/b"),
    ("s10-cooldown-foo", _ACTION),
    ("s10-cooldown", _ACTION),
    ("s10-cooldown-exempt", "s10-cooldown-exempt:o/b"),
])
def test_a_sibling_retraction_does_not_clear_a_family_halt(tmp_path, stub_ed25519, monkeypatch, halt, sibling) -> None:
    """Blocker 1 (IM-da's probe): one authentic, unscoped retraction of a sibling must not clear the family. Since
    #596 the core scan refuses it too; this gate keeps its own exact-id scan as defence in depth (OD7), so each layer
    is checked here with the other switched off."""
    def build(sub: str) -> Path:
        team = _team(tmp_path / sub)
        _log(team, _row(halt, verdict="HALT", stamp="2026-11-20"), _row(sibling, verdict="HALT", stamp="2026-11-21"),
             _retraction(sibling, scope=""), _row(_ACTION, stamp="2026-11-21"))   # the signed row comes last
        return team
    assert gate.authorized_deprecation(build("both"), True, consume=True, now=_T0) is None
    with monkeypatch.context() as m:   # the gate's exact-id scan alone
        m.setattr(gate.security_gate, "_assert_no_unretracted_halt", lambda halt_dir, action: None)
        assert gate.authorized_deprecation(build("gate-only"), True, consume=True, now=_T0) is None
    with monkeypatch.context() as m:   # the core scan alone (#596)
        m.setattr(gate, "_assert_no_open_family_halt", lambda halt_dir: None)
        assert gate.authorized_deprecation(build("core-only"), True, consume=True, now=_T0) is None
    # Positive control: with both layers off, the same log authorizes, so the refusals above come from the scans.
    monkeypatch.setattr(gate.security_gate, "_assert_no_unretracted_halt", lambda halt_dir, action: None)
    monkeypatch.setattr(gate, "_assert_no_open_family_halt", lambda halt_dir: None)
    assert gate.authorized_deprecation(build("neither"), True, consume=True, now=_T0) == "signed"


def test_an_exact_retraction_clears_its_halt(tmp_path, stub_ed25519) -> None:
    """The core family scan authenticates a retraction against each family it scans, so a scoped retraction must list
    them all (or carry no scope); this gate's own scan then needs the retraction's id to be the HALT's exact id."""
    halt = "s10-cooldown-exempt:o/a"
    for i, scope in enumerate(("", f"{halt};s10-cooldown-exempt;s10-cooldown")):
        team = _team(tmp_path / str(i))
        _log(team, _row(_ACTION), _row(halt, verdict="HALT", stamp="2026-11-20"), _retraction(halt, scope=scope))
        assert gate.authorized_deprecation(team, True, consume=True, now=_T0) == "signed", scope


def test_an_unsigned_or_unevidenced_retraction_clears_nothing(tmp_path, stub_ed25519) -> None:
    for i, mutate in enumerate((lambda r: r | {"signature": "stub" + "0" * 64}, lambda r: r | {"conditions": ""})):
        team = _team(tmp_path / str(i))
        _log(team, _row(_ACTION), _row("s10-toolchain-group", verdict="HALT", stamp="2026-11-20"),
             mutate(_retraction("s10-toolchain-group")))
        assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None


def test_a_bad_signature_is_refused(tmp_path, stub_ed25519, capsys) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, sign=lambda values: "stub" + "f" * 64))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None
    assert "NOT applied" in capsys.readouterr().err


def test_missing_lineage_is_refused(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, derives=""))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None
    team2 = _team(tmp_path / "b")
    _log(team2, _row(_ACTION, derives="no-such-decision"))
    assert gate.authorized_deprecation(team2, True, consume=True, now=_T0) is None


def test_a_full_aggregate_cap_is_refused(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    (team / er.EXCEPTION_REGISTRY_REL).write_text(json.dumps({"cap": {"max_active_relaxing": 0}, "exceptions": []}))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None
    assert "yes" not in log.read_text()


def test_a_malformed_decisions_log_is_refused(tmp_path, stub_ed25519) -> None:
    for i, governed in enumerate((True, False)):
        team = _team(tmp_path / str(i), governed=governed)
        (team / "references" / "security-decisions.log.csv").write_bytes(b"timestamp,verdict\n\xff\xfe\x00broken\n")
        assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None


def test_an_edited_registry_expiry_does_not_extend_the_sunset(tmp_path, stub_ed25519) -> None:
    """Finding 4: carry-over counts the sunset from the signed row, never from the registry's unsigned expires."""
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) == "signed"
    path = team / er.EXCEPTION_REGISTRY_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    data["exceptions"][0]["expires"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0 + timedelta(days=400)) is None


def test_a_stale_signed_row_is_not_consumed(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION, stamp="2026-01-01"))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None
    assert "yes" not in log.read_text()


def test_the_ttl_lives_in_the_pinned_gate() -> None:
    """Finding 3: the sunset is defined in the integrity-pinned gate module only."""
    from agentteams import integrity, manifest_format

    assert gate.DEPRECATION_TTL_DAYS == 90 and not hasattr(manifest_format, "S10_DEPRECATION_TTL_DAYS")
    assert "agentteams/cli/s10_deprecation_gate.py" in Path(integrity.__file__).read_text(encoding="utf-8")


def test_the_review_trigger_table_names_the_field() -> None:
    """Finding 5."""
    assert ("| Setting the brief's `s10_cooldown_deprecated` to `true`, or recording an `s10-cooldown-deprecated` "
            "decision | Constraint relaxation") in _SECURITY_TEMPLATE


# --- @security re-check of 819d06b1: the signed date field, and no future dates ------------------------------------


def test_the_sunset_counts_from_the_signed_date_column() -> None:
    """The signature covers ``date`` when present, else ``timestamp`` (``decision_log._decision_signature_values``): the
    sunset reads the same field, so a ``date``-schema log works and an unsigned ``timestamp`` beside it can't stretch it."""
    both = {"date": "2026-01-01", "timestamp": "2026-11-20"}
    assert gate._signed_at(both) == datetime(2026, 1, 1, tzinfo=UTC)
    assert dl._decision_signature_values(both | {"verdict": "PASS"}).count("2026-01-01") == 1
    assert "lapsed" in (gate._lapse_reason(both, _T0) or "")
    assert gate._signed_at({"date": "2026-11-20"}) == datetime(2026, 11, 20, tzinfo=UTC)
    assert gate._signed_at({"timestamp": "2026-11-20T10:00:00Z"}) == datetime(2026, 11, 20, 10, tzinfo=UTC)
    assert "unreadable date" in (gate._lapse_reason({"date": "yesterday"}, _T0) or "")


def test_a_future_dated_row_is_refused(tmp_path, stub_ed25519, capsys) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION, stamp="2027-11-21"))
    assert gate.authorized_deprecation(team, True, consume=True, now=_T0) is None
    assert "in the future" in capsys.readouterr().err and "yes" not in log.read_text()
    team2 = _team(tmp_path / "b")
    _log(team2, _row(_ACTION, stamp="2026-11-21T12:00:00+00:00"))   # within the skew allowance
    assert gate.authorized_deprecation(team2, True, consume=True, now=_T0) == "signed"
