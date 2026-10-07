"""P5b: pattern write_scopes and the operator-owned confined_programs file (orchestrator-only-writes pilot)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import confinement as C
from agentteams import proposal_runner as R
from agentteams import proposals as P
from agentteams.frameworks._sandbox_emit import permission_deny_rules
from agentteams.write_policy import _ORCHESTRATOR_SECTION

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
JAIL = {"lean-prover": {"exec": ["~/.elan"], "write": ["lean/.lake"]}}


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Every confined file lands under a throwaway HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in P.KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(P, "_FILE_KEY", None)
    return home


def _policy(scopes, *, protected=(), brief_rel="brief.json"):
    return P.load_policy({"agent_policies": {"a": {"write_scopes": list(scopes)}},
                          "protected_paths": list(protected)}, brief_rel=brief_rel)


# --- pattern write_scopes ----------------------------------------------------------------------


@pytest.mark.parametrize("rel, ok", [
    ("reports/dossiers/p2/strategy.md", True),
    ("Reports/Dossiers/P2/Strategy.md", True),           # case-folded, like literal scopes
    ("reports/dossiers/a/b/strategy.md", False),          # `*` never crosses `/`
    ("reports/dossiers/strategy.md", False),              # nor matches an empty segment
    ("reports/dossiers/.git/strategy.md", False),         # nor a dot-segment
    ("reports/dossiers/p2/strategy.md.bak", False),
])
def test_pattern_scope_matches_one_segment(rel, ok):
    assert P._in_scope(rel, ["reports/dossiers/*/strategy.md"]) is ok


def test_directory_pattern_covers_everything_below_it():
    scopes = ["lean/blueprint/", "reports/dossiers/*/"]
    assert P._in_scope("reports/dossiers/p2/sources/a.pdf", scopes)
    assert P._in_scope("lean/blueprint/src/content.tex", scopes)
    assert not P._in_scope("reports/dossiers/p2", scopes)   # the segment itself is not "below"
    assert not P._in_scope("reports/other/p2/a.md", scopes)


@pytest.mark.parametrize("scope, why", [
    ("*/strategy.md", "literal project directory"),
    ("reports/**/strategy.md", "'\\*\\*'"),
    ("/reports/*/x.md", "project-relative"),
    ("~/*/x.md", "project-relative"),
    ("reports/../*/x.md", "'\\.\\.'"),
    ("reports//*.md", "empty"),
    (".claude/agents/*.md", "control plane"),
])
def test_unsafe_pattern_scopes_are_refused_at_load(scope, why):
    with pytest.raises(P.ProposalError, match=why):
        _policy([scope])


def test_an_unsafe_pattern_never_matches_even_unloaded():
    # _in_scope is also called on scopes load_policy never saw (the runner's write-root brief check).
    assert not P._in_scope("x/strategy.md", ["*/strategy.md"])
    assert not P._in_scope("reports/a/b/x.md", ["reports/**/x.md"])


def test_a_pattern_scope_covering_the_brief_is_refused():
    with pytest.raises(P.ProposalError, match="covers the brief"):
        _policy(["briefs/*.json"], brief_rel="briefs/brief.json")


def test_non_string_scopes_are_refused():
    with pytest.raises(P.ProposalError, match="list of strings"):
        _policy([["reports/"]])


def test_protected_paths_still_win_over_a_pattern_scope(tmp_path):
    # Rule 12: the new pattern form must not open a path the protected list closes.
    policy = _policy(["reports/dossiers/*/"], protected=["reports/dossiers/*/fidelity.json"])
    with pytest.raises(P.ProposalError, match="protected path"):
        P._check_destination(tmp_path, "reports/dossiers/p2/fidelity.json", policy, "a")
    assert P._check_destination(tmp_path, "reports/dossiers/p2/strategy.md", policy, "a") == \
        "reports/dossiers/p2/strategy.md"


def test_control_plane_still_refused_under_a_pattern_scope(tmp_path):
    policy = _policy(["src/*/"])
    with pytest.raises(P.ProposalError, match="control plane"):
        P._check_destination(tmp_path, ".claude/agents/x.md", policy, "a")


# --- the operator-owned confined file ----------------------------------------------------------


def test_path_is_per_project_and_under_the_operator_dir(tmp_path, _home):
    a, b = tmp_path / "x" / "proj", tmp_path / "y" / "proj"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    pa, pb = C.confined_file_for(a), C.confined_file_for(b)
    assert pa != pb and pa.name.startswith("proj-") and pa.parent == _home / ".config/agentteams/confined"


def test_absent_file_reads_as_none(proj):
    assert C.read_confined_file(proj) is None


def test_install_then_read_round_trips_with_mode_0600(proj):
    path = C.install_confined_file(proj, JAIL)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    data, sha = C.read_confined_file(proj)
    assert data == JAIL and len(sha) == 64


@pytest.mark.parametrize("damage, why", [
    (lambda p: p.chmod(0o620), "group- or world-writable"),
    (lambda p: p.parent.chmod(0o777), "group- or world-writable"),
    (lambda p: (p.unlink(), p.symlink_to(p.parent / "elsewhere.json")), "symlink"),
    (lambda p: p.write_text("not json"), "not valid JSON"),
    (lambda p: p.write_text("[1]"), "JSON object"),
    (lambda p: p.write_text(" " * (C.MAX_CONFINED_BYTES + 1)), "larger than"),
])
def test_custody_failures_refuse(proj, damage, why):
    path = C.install_confined_file(proj, JAIL)
    (path.parent / "elsewhere.json").write_text("{}")
    damage(path)
    with pytest.raises(C.ConfinementError, match=why):
        C.read_confined_file(proj)


def test_file_replaces_the_brief_block_and_both_at_once_refuse():
    brief = {"agent_policies": {}, "confined_programs": JAIL}
    with pytest.raises(P.ProposalError, match="both the brief and the operator file"):
        P.load_policy(brief, confined_file=(JAIL, "sha"))
    policy = P.load_policy({"agent_policies": {}}, confined_file=(JAIL, "sha"))
    assert policy.confined == JAIL and policy.confined_file_sha == "sha"


def test_the_file_is_validated_like_the_brief_block():
    with pytest.raises(P.ProposalError, match="absolute or ~/"):
        P.load_policy({}, confined_file=({"a": {"exec": ["relative/bin"]}}, "sha"))


def _git_project(tmp_path: Path) -> Path:
    root = tmp_path / "gitproj"
    root.mkdir()
    brief = {"project_name": "P", "project_goal": "A small project for runner tests.",
             "write_policy": "orchestrator-only", "agent_policies": {"lean-prover": {"write_scopes": ["lean/"]}}}
    (root / "brief.json").write_text(json.dumps(brief))
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


def test_runner_stops_when_the_file_changes(tmp_path, monkeypatch):
    key = tmp_path / "keys" / "proposal-ledger.key"
    key.parent.mkdir()
    key.write_text("runner-secret\n")
    key.chmod(0o600)
    monkeypatch.setattr(P, "KEY_DIR", str(key.parent))
    monkeypatch.setenv(P.KEY_FILE_ENV, str(key))
    root = _git_project(tmp_path)
    policy = P.load_policy(json.loads((root / "brief.json").read_text()), brief_rel="brief.json")
    runner = R.Runner(root, root / "brief.json", policy)
    try:
        assert runner.serve_once() == 0
        C.install_confined_file(root, JAIL)          # appears after start: the pinned "none" no longer holds
        with pytest.raises(R.RunnerError, match="confined_programs file changed"):
            runner.serve_once()
    finally:
        runner.close()


# --- CLI ---------------------------------------------------------------------------------------


def _cli(*args, cwd, home):
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run([PY, str(REPO / "build_team.py"), *args], cwd=cwd, capture_output=True, text=True,
                          timeout=120, env=env)


def test_cli_install_validates_then_installs(tmp_path, _home):
    root = _git_project(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"lean-prover": {"exec": ["relative"]}}))
    out = _cli("--install-confined", str(bad), "--project", str(root), "--description", str(root / "brief.json"),
               cwd=root, home=_home)
    assert out.returncode == 1 and "refused" in out.stderr
    assert not C.confined_file_for(root).exists()
    good = tmp_path / "good.json"
    good.write_text(json.dumps(JAIL))
    args = ("--install-confined", str(good), "--project", str(root), "--description", str(root / "brief.json"))
    # Without a review hash: prints the exact JSON and its sha256, installs nothing.
    out = _cli(*args, cwd=root, home=_home)
    digest = hashlib.sha256(C.confined_bytes(JAIL)).hexdigest()
    assert out.returncode == 1 and C.confined_bytes(JAIL).decode() in out.stdout and digest in out.stdout
    assert not C.confined_file_for(root).exists()
    # A hash that doesn't match what was reviewed (the file changed since) is refused.
    out = _cli(*args, "--confirm-review-sha256", "0" * 64, cwd=root, home=_home)
    assert out.returncode == 1 and "does not match" in out.stderr and not C.confined_file_for(root).exists()
    out = _cli(*args, "--confirm-review-sha256", digest, cwd=root, home=_home)
    assert out.returncode == 0, out.stderr
    out = _cli("--confined-path", "--project", str(root), cwd=root, home=_home)
    assert out.returncode == 0 and out.stdout.strip().endswith("(present)")
    assert json.loads(C.confined_file_for(root).read_text()) == JAIL


def test_cli_install_refuses_when_the_brief_has_its_own_block(tmp_path, _home):
    root = _git_project(tmp_path)
    brief = json.loads((root / "brief.json").read_text())
    brief["confined_programs"] = JAIL
    (root / "brief.json").write_text(json.dumps(brief))
    src = tmp_path / "c.json"
    src.write_text(json.dumps(JAIL))
    out = _cli("--install-confined", str(src), "--project", str(root), "--description", str(root / "brief.json"),
               cwd=root, home=_home)
    assert out.returncode == 1 and "both the brief and the operator file" in out.stderr


# --- agent reach -------------------------------------------------------------------------------


def test_built_in_edit_tools_are_denied_the_operator_stores():
    for framework in ("claude", "goose"):
        rules = permission_deny_rules(framework)
        assert "Edit(~/.config/agentteams/confined/**)" in rules
        assert "Edit(~/.config/agentteams/confined)" in rules    # the bare path: no squatting a file there
        assert "Edit(~/.config/agentteams/keys/**)" in rules
    assert C.CONFINED_DIR == "~/.config/agentteams/confined"


def test_orchestrator_is_told_to_hand_the_user_a_script():
    text = _ORCHESTRATOR_SECTION
    assert "--install-confined" in text and "--confirm-review-sha256" in text
    assert "or write a script for it" in text and "Don't run it yourself" in text


def test_a_file_inside_the_project_is_refused(tmp_path, monkeypatch):
    # HOME inside the project would put the operator file where agents can write.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with pytest.raises(C.ConfinementError, match="inside the project"):
        C.install_confined_file(tmp_path, JAIL)       # refused at install...
    path = C.confined_file_for(tmp_path)
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_bytes(C.confined_bytes(JAIL))
    path.chmod(0o600)
    with pytest.raises(C.ConfinementError, match="inside the project"):
        C.read_confined_file(tmp_path)                 # ...and at read, for a file placed by other means


def test_wildcards_in_confined_write_roots_are_refused():
    # Rule 12: write roots feed literal containment checks; a `[` would make them pattern scopes and never match.
    with pytest.raises(P.ProposalError, match="wildcard"):
        P.load_policy({}, confined_file=({"a": {"exec": ["~/.elan"], "write": ["cfg[1]"]}}, "sha"))


def test_a_file_swapped_after_the_checks_is_refused(proj, monkeypatch):
    path = C.install_confined_file(proj, JAIL)
    other = path.parent / "other.json"
    other.write_text(json.dumps(JAIL))
    other.chmod(0o600)
    real_lstat = os.lstat

    swapped = []

    def lstat_then_swap(p, *a, **k):
        st = real_lstat(p, *a, **k)
        if str(p) == str(path):
            swapped.append(True)
            if len(swapped) == 2:            # 1st: os.path.lexists; 2nd: the custody check. Swap right after it.
                os.replace(other, path)
        return st

    monkeypatch.setattr(C.os, "lstat", lstat_then_swap)
    with pytest.raises(C.ConfinementError, match="changed while it was being read"):
        C.read_confined_file(proj)


def test_a_fifo_never_hangs_the_reader(proj):
    path = C.install_confined_file(proj, JAIL)
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(C.ConfinementError, match="real file"):
        C.read_confined_file(proj)
