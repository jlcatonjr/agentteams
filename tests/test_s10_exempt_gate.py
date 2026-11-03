"""The Rule 15 gate for Rule S-10's local-repository carve-out (@security condition 3, 2026-11-03): listing a
repository in the brief is not enough in a governed workspace; it needs the operator's Ed25519-signed decision."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from agentteams.cli import exception_registry as er
from agentteams.cli import s10_exempt_gate as gate
from tests._decision_gate_helpers import _log, _row, _team

_T0 = datetime(2026, 11, 3, tzinfo=UTC)
_REPO = "jlcatonjr/agentteams"
_ACTION = "s10-cooldown-exempt:jlcatonjr/agentteams"


def _registry(team) -> list[dict]:
    return json.loads((team / er.EXCEPTION_REGISTRY_REL).read_text(encoding="utf-8"))["exceptions"]


def test_ungoverned_renders_the_listed_repositories_with_a_notice(tmp_path, capsys) -> None:
    team = _team(tmp_path, governed=False)
    assert gate.authorized_exempt_repos(team, [_REPO, "bare", "x/y"], consume=True) == [_REPO, "x/y"]
    assert "not governed" in capsys.readouterr().err


def test_governed_without_a_decision_leaves_it_out_and_names_the_row(tmp_path, capsys) -> None:
    team = _team(tmp_path)
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []
    err = capsys.readouterr().err
    assert "NOT exempted" in err and f"scope={_ACTION}" in err and "--sign-decision" in err


def test_an_hmac_row_is_valid_but_insufficient(tmp_path, stub_ed25519, capsys) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scheme=""))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []
    assert "INSUFFICIENT" in capsys.readouterr().err


def test_scope_and_effect_must_be_exactly_the_action(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scope="s10-cooldown-exempt"))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []
    team2 = _team(tmp_path / "b")
    _log(team2, _row(_ACTION, relaxes=f"{_ACTION};s10-cooldown-exempt:jlcatonjr/other"))
    assert gate.authorized_exempt_repos(team2, [_REPO], consume=True) == []


def test_a_halt_on_the_family_blocks_it(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION), _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-02"))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []


def test_a_signed_decision_registers_a_bounded_exception_that_later_renders_reuse(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO]
    assert "yes" in log.read_text()  # consumed
    [entry] = _registry(team)
    assert entry["action"] == _ACTION and entry["status"] == "active" and entry["relaxing"] is True
    assert datetime.fromisoformat(entry["expires"]) == _T0 + timedelta(days=gate.S10_EXEMPT_TTL_DAYS)
    # A later render: the row is spent, the registry entry carries it.
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0 + timedelta(days=30)) == [_REPO]
    # After the sunset it lapses: the cooldown applies again until a fresh decision is signed.
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0 + timedelta(days=91)) == []


def test_a_dry_run_or_check_writes_nothing(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    before = log.read_bytes()
    assert gate.authorized_exempt_repos(team, [_REPO], consume=False, now=_T0) == [_REPO]
    assert log.read_bytes() == before and not (team / er.EXCEPTION_REGISTRY_REL).exists()


def test_only_the_signed_repository_is_exempted(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_exempt_repos(team, [_REPO, "jlcatonjr/researchteam"], consume=True, now=_T0) == [_REPO]


def test_a_halt_after_registration_removes_it_at_once(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO]
    _log(team, _row(_ACTION, verdict="HALT", stamp="2026-11-04"))   # on the instance
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0 + timedelta(days=1)) == []
    team2 = _team(tmp_path / "b")
    _log(team2, _row(_ACTION))
    assert gate.authorized_exempt_repos(team2, [_REPO], consume=True, now=_T0) == [_REPO]
    _log(team2, _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-04"))   # on the family
    assert gate.authorized_exempt_repos(team2, [_REPO], consume=True, now=_T0 + timedelta(days=1)) == []


def test_case_only_duplicates_render_once(tmp_path) -> None:
    team = _team(tmp_path, governed=False)
    assert gate.authorized_exempt_repos(team, ["JLCatonJr/AgentTeams", _REPO], consume=False) == ["JLCatonJr/AgentTeams"]


# --- Native surfaces authorize against the canonical .github/agents (researchteam #139, rc.15) ------------------


def _native(tmp_path, sub: str = ".claude/agents", *, governed: bool | None = True):
    """A team at ``<tmp>/<sub>`` (the fixture builds .claude/agents; any other dir is moved into place)."""
    if sub == ".claude/agents":
        return _team(tmp_path, governed=governed)
    built = _team(tmp_path / f"_build{sub.replace('/', '_')}", governed=governed)
    target = tmp_path / sub
    target.parent.mkdir(parents=True, exist_ok=True)
    built.rename(target)
    return target


def _canonical(tmp_path, *, governed: bool | None = True):
    return _native(tmp_path, ".github/agents", governed=governed)


def test_a_native_surface_authorizes_against_the_canonical_log(tmp_path, stub_ed25519) -> None:
    canonical, native = _canonical(tmp_path), _native(tmp_path)
    log = _log(canonical, _row(_ACTION))
    assert gate.governance_team_dir(native) == canonical
    assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == [_REPO]
    assert "yes" in log.read_text()                         # consumed in the canonical log
    assert [e["action"] for e in _registry(canonical)] == [_ACTION]
    assert not (native / er.EXCEPTION_REGISTRY_REL).exists()  # nothing is written to the surface's own dir


def test_every_surface_shares_one_consumption_and_one_exception(tmp_path, stub_ed25519) -> None:
    canonical = _canonical(tmp_path)
    surfaces = [_native(tmp_path), _native(tmp_path, ".goose/recipes"), _native(tmp_path, ".codex/agents")]
    _log(canonical, _row(_ACTION))
    for team in [*surfaces, canonical]:   # a native surface rendering first consumes; the rest carry over
        assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO], team
    assert len(_registry(canonical)) == 1


def test_a_halt_in_the_surfaces_own_log_still_binds(tmp_path, stub_ed25519) -> None:
    canonical, native = _canonical(tmp_path), _native(tmp_path)
    log = _log(canonical, _row(_ACTION))
    _log(native, _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-02"))   # family HALT, own log
    assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == []
    assert "yes" not in log.read_text()                     # refused before anything was consumed
    # The instance too, and after registration: the canonical exception does not override the surface's HALT.
    other = _native(tmp_path, ".goose/recipes")
    assert gate.authorized_exempt_repos(canonical, [_REPO], consume=True, now=_T0) == [_REPO]
    _log(other, _row(_ACTION, verdict="HALT", stamp="2026-11-04"))
    assert gate.authorized_exempt_repos(other, [_REPO], consume=True, now=_T0 + timedelta(days=1)) == []
    assert gate.authorized_exempt_repos(canonical, [_REPO], consume=True, now=_T0 + timedelta(days=1)) == [_REPO]


def test_the_canonical_dir_is_used_only_when_it_is_real_and_governed(tmp_path, stub_ed25519) -> None:
    # Ungoverned canonical: the surface keeps its own (empty) log.
    canonical, native = _canonical(tmp_path / "a", governed=False), _native(tmp_path / "a")
    _log(canonical, _row(_ACTION))
    assert gate.governance_team_dir(native) == native
    assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == []
    # A governed canonical dir with no log yet still governs: it authorizes nothing (fail-closed).
    canonical_b = _canonical(tmp_path / "b")
    native_b = _native(tmp_path / "b")
    assert gate.governance_team_dir(native_b) == canonical_b
    assert gate.authorized_exempt_repos(native_b, [_REPO], consume=True, now=_T0) == []
    # A .github/agents that is a symlink out of the project is never followed.
    outside = _team(tmp_path / "outside")
    _log(outside, _row(_ACTION))
    proj = tmp_path / "c"
    (proj / ".github").mkdir(parents=True)
    (proj / ".github" / "agents").symlink_to(outside, target_is_directory=True)
    native_c = _native(proj)
    assert gate.governance_team_dir(native_c) == native_c
    assert gate.authorized_exempt_repos(native_c, [_REPO], consume=True, now=_T0) == []
    # Neither is a symlinked .github.
    proj_d = tmp_path / "d"
    proj_d.mkdir()
    (proj_d / ".github").symlink_to(outside.parent.parent / ".claude", target_is_directory=True)
    native_d = _native(proj_d)
    assert gate.governance_team_dir(native_d) == native_d
    # A non-default layout has no canonical dir.
    odd = _team(tmp_path / "e")
    moved = tmp_path / "e" / "team"
    odd.rename(moved)
    assert gate.governance_team_dir(moved) == moved


def test_an_ungoverned_surface_under_a_governed_canonical_gets_no_brief_alone_pass(tmp_path, stub_ed25519, capsys) -> None:
    canonical = _canonical(tmp_path)
    native = _native(tmp_path, governed=None)    # no switch of its own: alone it would trust the brief
    assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == []
    assert "not governed" not in capsys.readouterr().err
    _log(canonical, _row(_ACTION))                # and the canonical row is what authorizes it
    assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == [_REPO]


def test_a_dry_run_through_the_canonical_dir_writes_nothing(tmp_path, stub_ed25519) -> None:
    canonical, native = _canonical(tmp_path), _native(tmp_path)
    log = _log(canonical, _row(_ACTION))
    before = log.read_bytes()
    assert gate.authorized_exempt_repos(native, [_REPO], consume=False, now=_T0) == [_REPO]
    assert log.read_bytes() == before and not (canonical / er.EXCEPTION_REGISTRY_REL).exists()


def test_an_unreadable_canonical_switch_exempts_nothing_and_the_render_goes_on(tmp_path, stub_ed25519, capsys) -> None:
    canonical = _canonical(tmp_path)
    _log(canonical, _row(_ACTION))
    (canonical / "references" / "agent-privilege.json").write_text("{not json")
    for native in (_native(tmp_path), _native(tmp_path, ".goose/recipes", governed=None)):
        assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == []
        err = capsys.readouterr().err
        assert "NOT exempted" in err and "can't be read" in err and "not governed" not in err


def test_a_symlinked_canonical_dir_inside_the_project_is_refused_too(tmp_path, stub_ed25519) -> None:
    real = _native(tmp_path, "real/agents")       # inside the project: only the symlink check refuses it
    _log(real, _row(_ACTION))
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "agents").symlink_to(real, target_is_directory=True)
    native = _native(tmp_path)
    assert gate.governance_team_dir(native) == native
    assert gate.authorized_exempt_repos(native, [_REPO], consume=True, now=_T0) == []


def test_agents_md_and_wasm_team_dirs_use_the_canonical_dir(tmp_path, stub_ed25519) -> None:
    canonical = _canonical(tmp_path)
    _log(canonical, _row(_ACTION))
    for sub in (".agents", ".agentteams-wasm/agents"):
        team = _native(tmp_path, sub)
        assert gate.governance_team_dir(team) == canonical, sub
        assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO], sub


def test_authorize_into_redirects_a_native_output_dir(tmp_path, stub_ed25519) -> None:
    canonical, native = _canonical(tmp_path), _native(tmp_path, ".codex/agents")
    _log(canonical, _row(_ACTION))
    manifest = {"s10_cooldown_exempt_repos": [_REPO]}
    gate.authorize_into(manifest, native, consume=True)   # what generate.py calls with the run's output_dir
    assert manifest["s10_cooldown_exempt_authorized"] == [_REPO]
