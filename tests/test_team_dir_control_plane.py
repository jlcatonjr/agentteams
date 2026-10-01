"""The ``.github/agents`` (copilot) and ``.codex/agents`` (codex) team-dir control planes (2026-09-30).

Plan: ``tmp/by-week/2026-W40/team-dir-control-plane.plan.md`` (binding revisions at its end). Those
team dirs hold the same framework-neutral trust roots as ``.claude/agents``/``.goose/recipes`` (the
``enforce_decision_signing`` switch, the verify-key store, the operator rosters, the team marker)
and were protected by no arm. Layers, labelled honestly:

* **Emitted config** (always runs): the Claude block's ``denyWrite`` gains ``.codex`` /
  ``.github/agents`` / ``.goose`` only when that team is PRESENT at generation (a missing deny path
  stops bwrap); ``permissions.deny`` gains ``Edit(...)`` rules for the sibling trust-root FILES; the
  goose Seatbelt profile gains the sibling entries (UNVERIFIED: no macOS host); copilot/codex teams
  get the roster stubs and the verify-key store sentinel whenever the switch is emitted.
* **Launcher** (``confine-run.sh``; Linux + bwrap): the four team dirs, required entries anchored on
  each team's build-log marker, ``.github/workflows`` never protected, ``.codex/config.toml``
  protect-if-present; mechanism-verified with raw probes through the launcher itself.
* **Product arm** (Claude Code's own bwrap): ``test_os_sandbox_product_enforcement.py``
  (``RUN_CLAUDE_SANDBOX_ITEST=1``).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import control_plane_io as cpio
from agentteams.cli.generate_helpers import (
    _apply_sibling_team_denies,
    _warn_live_sandbox_deny_paths_missing,
    _warn_sibling_teams_under_claude_sandbox,
)
from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile
from agentteams.frameworks._sandbox_emit import (
    _PROTECTED_WRITE_PATHS,
    CODEX_CONFIG_REL,
    CONTROL_PLANE_STUB_TEXT,
    SIBLING_DENY_DIRS_KEY,
    TEAM_MARKER_REL,
    VERIFY_KEY_STORE_SENTINEL_REL,
    VERIFY_KEY_STORE_SENTINEL_TEXT,
    _build_sandbox_block,
    control_plane_ancestors,
    governed_roster_paths,
    permission_deny_rules,
    present_sibling_deny_dirs,
    protected_write_paths,
    sibling_deny_dirs,
    team_agents_dir,
    team_marker_path,
)
from agentteams.frameworks.registry import FRAMEWORKS

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
_BWRAP = shutil.which("bwrap")
_linux_bwrap = pytest.mark.skipif(not (sys.platform.startswith("linux") and _BWRAP),
                                  reason="confine-run's Linux branch needs bwrap on PATH")


def _bwrap_usable() -> bool:
    if not (sys.platform.startswith("linux") and _BWRAP):
        return False
    probe = subprocess.run([_BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                            "--unshare-user", "true"], capture_output=True, text=True)
    return probe.returncode == 0


_bwrap = pytest.mark.skipif(not _bwrap_usable(), reason="bubblewrap not usable on this host")


def _team(p: Path, key: str, *, marker: bool = True, rosters: bool = True) -> Path:
    """Materialise a sandboxed ``key`` team's control plane (switch, store, rosters, marker)."""
    for rel in (*protected_write_paths(key), *(governed_roster_paths(key) if rosters else ())):
        q = p / rel
        if q.name == "authorized-verify-keys":
            q.mkdir(parents=True, exist_ok=True)
            (q / "README.md").write_text("x\n", encoding="utf-8")
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text("{}\n", encoding="utf-8")
    if marker:
        (p / team_marker_path(key)).write_text("{}\n", encoding="utf-8")
    return p


# --- T1: emit shape ------------------------------------------------------------------------------

def test_copilot_and_codex_trust_root_table():
    for key, agents in (("copilot", ".github/agents"), ("codex", ".codex/agents")):
        refs = f"{agents}/references"
        assert team_agents_dir(key) == agents
        assert team_marker_path(key) == f"{agents}/{TEAM_MARKER_REL}"
        assert protected_write_paths(key) == (f"{refs}/agent-privilege.json",
                                              f"{refs}/authorized-verify-keys")
        assert governed_roster_paths(key) == (f"{refs}/security-approvers.txt",
                                              f"{refs}/authorized-managers.txt",
                                              f"{refs}/management-authority.json")
    assert _PROTECTED_WRITE_PATHS == (".claude/agents/references/agent-privilege.json",
                                      ".claude/hooks/constitutional-gate.py",
                                      ".claude/agents/references/authorized-verify-keys")


def test_claude_denywrite_is_unchanged_without_siblings_and_appends_only_present_ones():
    base = [*_PROTECTED_WRITE_PATHS, ".claude"]
    assert _build_sandbox_block(None, platform="linux")["filesystem"]["denyWrite"] == base
    full = _build_sandbox_block(None, platform="linux",
                                sibling_deny_dirs=(".codex", ".github/agents", ".goose"))
    assert full["filesystem"]["denyWrite"] == [*base, ".codex", ".github/agents", ".goose"]
    assert full["filesystem"]["allowWrite"] == ["."]  # no ".github" mount (binding revision 1)
    assert ".github" not in full["filesystem"]["denyWrite"]


def test_present_sibling_detection_needs_a_real_dir_chain_and_a_regular_marker(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    assert present_sibling_deny_dirs(p) == ()
    (p / ".github" / "agents").mkdir(parents=True)  # hand-written copilot agents: no marker
    (p / ".github" / "workflows").mkdir()
    assert present_sibling_deny_dirs(p) == ()
    _team(p, "copilot")
    _team(p, "codex")
    _team(p, "goose")
    assert present_sibling_deny_dirs(p) == (".codex", ".github/agents", ".goose")
    # symlinked marker / agents dir / top-level dir are all rejected
    marker = p / team_marker_path("codex")
    marker.unlink()
    (tmp_path / "m.json").write_text("{}\n")
    marker.symlink_to(tmp_path / "m.json")
    shutil.move(str(p / ".github"), str(tmp_path / "gh"))
    (p / ".github").symlink_to(tmp_path / "gh")
    shutil.move(str(p / ".goose" / "recipes"), str(tmp_path / "recipes"))
    (p / ".goose" / "recipes").symlink_to(tmp_path / "recipes")
    assert present_sibling_deny_dirs(p) == ()


def test_sibling_key_is_computed_only_and_filtered(tmp_path):
    # A JSON brief/manifest can only yield a list: ignored. A tuple is filtered to the allowed set.
    assert sibling_deny_dirs({SIBLING_DENY_DIRS_KEY: [".codex"]}) == ()
    assert sibling_deny_dirs({SIBLING_DENY_DIRS_KEY: (".codex", "/etc", ".github", ".codex")}) == (".codex",)
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    m = {"framework": "claude", SIBLING_DENY_DIRS_KEY: (".goose",)}
    assert _apply_sibling_team_denies(m, tmp_path, agents) == ()  # the input value is discarded
    assert m[SIBLING_DENY_DIRS_KEY] == ()
    _team(tmp_path, "codex")
    # --output naming the agents dir itself: the project root is derived from the default sub-path
    assert _apply_sibling_team_denies(m, agents, agents) == (".codex",)


def test_brief_supplied_sibling_key_never_reaches_the_block():
    from agentteams import analyze

    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    brief[SIBLING_DENY_DIRS_KEY] = [".codex", "/etc"]
    manifest = analyze.build_manifest(brief, framework="claude")
    assert sibling_deny_dirs(manifest) == ()


def _claude_example(manifest: dict) -> dict:
    files = dict(FRAMEWORKS["claude"]().extra_output_files(manifest))
    return json.loads(files["../settings.hooks.example.json"])


def test_claude_adapter_threads_the_computed_key_only():
    m = {"framework": "claude", "privilege_profile": "confined"}
    assert _claude_example(m)["sandbox"]["filesystem"]["denyWrite"][-1] == ".claude"
    m[SIBLING_DENY_DIRS_KEY] = [".codex"]
    assert _claude_example(m)["sandbox"]["filesystem"]["denyWrite"][-1] == ".claude"
    m[SIBLING_DENY_DIRS_KEY] = (".github/agents", ".codex")
    assert _claude_example(m)["sandbox"]["filesystem"]["denyWrite"][-2:] == [".codex", ".github/agents"]


def _generate(project: Path, framework: str, out: str, monkeypatch, *, profile: str | None = None) -> None:
    import build_team
    from agentteams.cli import security_gate

    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *a, **k: None)
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    if profile:
        brief["privilege_profile"] = profile
    path = project / f"brief-{framework}.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    offline = [] if framework == "goose" else ["--security-offline"]  # goose refuses offline intel
    rc = build_team.main(["--description", str(path), "--framework", framework,
                          "--output", str(project / out), "--yes", "--no-scan", *offline])
    assert rc == 0


def test_generation_emits_present_siblings_stubs_and_sentinel_and_never_persists_the_key(
    tmp_path, monkeypatch,
):
    """T1/T3/T10 end to end: codex + copilot (cooperative: mixed profile) + claude teams in one
    project. The claude block names the present siblings; every copilot/codex trust root exists;
    the transient key is written nowhere; the launcher accepts the project (the dogfood check)."""
    p = (tmp_path / "proj").resolve()
    (p / ".github" / "workflows").mkdir(parents=True)
    _generate(p, "codex", ".codex/agents", monkeypatch)
    _generate(p, "copilot-vscode", ".github/agents", monkeypatch, profile="cooperative")
    _generate(p, "claude", ".claude/agents", monkeypatch)
    deny = json.loads((p / ".claude/settings.hooks.example.json").read_text())["sandbox"]
    assert deny["filesystem"]["denyWrite"][-2:] == [".codex", ".github/agents"]
    for key in ("copilot", "codex"):
        for rel in (*protected_write_paths(key), *governed_roster_paths(key), team_marker_path(key)):
            assert (p / rel).exists(), rel
        sentinel = p / team_agents_dir(key) / VERIFY_KEY_STORE_SENTINEL_REL
        assert VERIFY_KEY_STORE_SENTINEL_TEXT in sentinel.read_text()  # fenced, as for claude
    for f in p.rglob("*"):
        if f.is_file() and f.suffix in {".json", ".md", ".toml", ".csv", ".txt", ".yaml"}:
            assert SIBLING_DENY_DIRS_KEY not in f.read_text(encoding="utf-8", errors="ignore"), f
    if sys.platform.startswith("linux") and _BWRAP:
        r = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--check", "--", "true"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


_TEAM_OUT = {"claude": ".claude/agents", "goose": ".goose/recipes",
             "copilot-vscode": ".github/agents", "codex": ".codex/agents"}


def _launcher_check(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(LAUNCHER), "--scratch", str(project), "--check", "--", "true"],
                          capture_output=True, text=True)


@_linux_bwrap
@pytest.mark.parametrize("profile", ["confined", "cooperative"])
@pytest.mark.parametrize("framework", sorted(_TEAM_OUT))
def test_every_generated_team_passes_the_launcher_check(tmp_path, monkeypatch, framework, profile):
    """Follow-up #1 (2026-09-30): a freshly generated team of any framework and profile must not
    brick the launcher (confined goose on Linux and cooperative claude used to: no store sentinel)."""
    p = (tmp_path / "proj").resolve()
    (p / ".github" / "workflows").mkdir(parents=True)
    _generate(p, framework, _TEAM_OUT[framework], monkeypatch, profile=profile)
    r = _launcher_check(p)
    assert r.returncode == 0, r.stderr


@_linux_bwrap
def test_mixed_confined_goose_and_cooperative_claude_pass_the_launcher_check(tmp_path, monkeypatch):
    p = (tmp_path / "proj").resolve()
    (p / ".github" / "workflows").mkdir(parents=True)
    _generate(p, "goose", ".goose/recipes", monkeypatch, profile="confined")
    _generate(p, "claude", ".claude/agents", monkeypatch, profile="cooperative")
    r = _launcher_check(p)
    assert r.returncode == 0, r.stderr
    # Fail closed is kept: a team whose sentinel is deleted is refused, with the regenerate hint.
    (p / ".claude/agents" / VERIFY_KEY_STORE_SENTINEL_REL).unlink()
    (p / ".claude/agents" / VERIFY_KEY_STORE_SENTINEL_REL).parent.rmdir()
    r = _launcher_check(p)
    assert r.returncode == 2 and "agentteams --update" in r.stderr, r.stderr


@_linux_bwrap
def test_multi_sync_cooperative_projection_keeps_the_launcher_working(tmp_path, monkeypatch):
    """An agentteams team (marker present) projected by multi_sync without confining privilege
    still gets its store sentinel (write-if-absent), so a confined sibling's launcher accepts it."""
    from agentteams import multi_sync

    p = (tmp_path / "proj").resolve()
    (p / ".github" / "workflows").mkdir(parents=True)
    _generate(p, "codex", ".codex/agents", monkeypatch, profile="confined")
    _generate(p, "copilot-vscode", ".github/agents", monkeypatch, profile="cooperative")
    agents = p / ".github" / "agents"
    store = (agents / VERIFY_KEY_STORE_SENTINEL_REL).parent
    shutil.rmtree(store)  # as left by an agentteams that predates the unconditional sentinel
    assert _launcher_check(p).returncode == 2
    written = multi_sync._emit_privilege_artifacts(p, "copilot-vscode", {"privilege_profile": "cooperative"},
                                                   dry_run=False)
    assert written == [str((agents / VERIFY_KEY_STORE_SENTINEL_REL).resolve())]
    r = _launcher_check(p)
    assert r.returncode == 0, r.stderr
    # write-if-absent: an existing store README is never overwritten
    (agents / VERIFY_KEY_STORE_SENTINEL_REL).write_text("operator text", encoding="utf-8")
    assert multi_sync._emit_privilege_artifacts(p, "copilot-vscode", None, dry_run=False) == []
    assert (agents / VERIFY_KEY_STORE_SENTINEL_REL).read_text() == "operator text"


def _cooperative_team_without_store(p: Path) -> Path:
    agents = p / ".github" / "agents"
    (agents / "references").mkdir(parents=True)
    (agents / "references" / "build-log.json").write_text("{}", encoding="utf-8")
    return agents


def test_multi_sync_sentinel_write_refuses_a_symlinked_store_parent(tmp_path):
    from agentteams import multi_sync

    agents = _cooperative_team_without_store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (agents / "references" / "authorized-verify-keys").symlink_to(outside)
    assert multi_sync._emit_privilege_artifacts(tmp_path, "copilot-vscode", None, dry_run=False) == []
    assert not any(outside.iterdir())


def test_multi_sync_sentinel_write_never_follows_a_dangling_symlink(tmp_path):
    from agentteams import multi_sync

    agents = _cooperative_team_without_store(tmp_path)
    store = agents / "references" / "authorized-verify-keys"
    store.mkdir()
    target = tmp_path / "victim.txt"
    (store / "README.md").symlink_to(target)
    assert multi_sync._emit_privilege_artifacts(tmp_path, "copilot-vscode", None, dry_run=False) == []
    assert not target.exists()


def test_multi_sync_sentinel_write_skips_non_agentteams_dirs(tmp_path):
    from agentteams import multi_sync

    (tmp_path / ".github" / "agents").mkdir(parents=True)  # hand-written: no build-log marker
    assert multi_sync._emit_privilege_artifacts(tmp_path, "copilot-vscode", None, dry_run=False) == []
    assert not (tmp_path / ".github" / "agents" / "references").exists()


# --- T2: permissions.deny ------------------------------------------------------------------------

def test_permission_rules_cover_every_sibling_trust_root_file_but_no_agents_dir():
    rules = permission_deny_rules("claude")
    for key in ("copilot", "codex", "goose"):
        store = protected_write_paths(key)[-1]
        assert f"Edit(/{store}/**)" in rules
        for rel in (protected_write_paths(key)[0], *governed_roster_paths(key), team_marker_path(key)):
            assert f"Edit(/{rel})" in rules, rel
    assert f"Edit(/{CODEX_CONFIG_REL})" in rules and "Edit(/.goose/sandbox.sb)" in rules
    for broad in ("Edit(/.github/agents/**)", "Edit(/.github/**)", "Edit(/.codex/**)",
                  "Edit(/.codex/agents/**)", "Edit(/.github/agents/_build-description.json)"):
        assert broad not in rules
    assert len(rules) == len(set(rules))


# --- T3: the stub/sentinel predicate matrix ------------------------------------------------------

@pytest.mark.parametrize("plat", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("profile", ["cooperative", "confined", "exclusive"])
@pytest.mark.parametrize("framework", ["claude", "goose", "copilot-vscode", "copilot-cli", "codex"])
@pytest.mark.parametrize("switch", [True, False])
def test_sentinel_and_stub_predicate_matrix(monkeypatch, plat, profile, framework, switch):
    monkeypatch.setattr(sys, "platform", plat)
    m = {"framework": framework, "privilege_profile": profile}
    if switch:
        m["enforce_decision_signing"] = True
    rels = [rel for rel, _ in FRAMEWORKS[framework]().extra_output_files(m)]
    assert len(rels) == len(set(rels)), rels
    if framework in ("claude", "goose", "copilot-vscode", "copilot-cli", "codex"):
        # 2026-09-30: one predicate for every team framework; the sentinel is unconditional.
        assert cpio.stubs_enabled(framework, m) is (switch or profile != "cooperative")
        assert VERIFY_KEY_STORE_SENTINEL_REL in rels


@pytest.mark.parametrize("framework", sorted(FRAMEWORKS))
def test_no_framework_emits_a_rel_path_twice(framework):
    m = {"framework": framework, "privilege_profile": "confined", "enforce_decision_signing": True}
    rels = [rel for rel, _ in FRAMEWORKS[framework]().extra_output_files(m)]
    assert len(rels) == len(set(rels)), rels


@pytest.mark.parametrize("framework", ["codex", "copilot-vscode"])
def test_multi_sync_projects_copilot_and_codex_stubs_and_sentinel(tmp_path, framework):
    from agentteams import multi_sync

    written = multi_sync._emit_privilege_artifacts(
        tmp_path, framework, {"privilege_profile": "confined"}, dry_run=False)
    agents = multi_sync.framework_agents_dir(tmp_path, framework)
    for rel in (VERIFY_KEY_STORE_SENTINEL_REL, *(f"references/{n}" for n in CONTROL_PLANE_STUB_TEXT)):
        assert (agents / rel).is_file() and str((agents / rel).resolve()) in written, rel


# --- T5: launcher --------------------------------------------------------------------------------

def _check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(LAUNCHER), *args, "--check", "--", "true"],
                          capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})


def _effective_binds(stdout: str) -> list[tuple[str, str]]:
    line = next(ln for ln in stdout.splitlines() if ln.strip().startswith("effective"))
    argv = line.split(":", 1)[1].split()
    out = []
    for i, tok in enumerate(argv):
        if tok == "--":
            break
        if tok in {"--bind", "--ro-bind"}:
            out.append((tok, argv[i + 2]))
    return out


@_linux_bwrap
@pytest.mark.parametrize("key", ["copilot", "codex"])
@pytest.mark.parametrize("which", ["switch", "store", "approvers", "managers", "authority"])
def test_launcher_refuses_a_missing_entry_when_the_team_exists(tmp_path, key, which):
    p = _team((tmp_path / "proj").resolve(), key)
    rel = {"switch": protected_write_paths(key)[0], "store": protected_write_paths(key)[1],
           "approvers": governed_roster_paths(key)[0], "managers": governed_roster_paths(key)[1],
           "authority": governed_roster_paths(key)[2]}[which]
    target = p / rel
    (shutil.rmtree if target.is_dir() else os.unlink)(target)
    r = _check("--scratch", str(p))
    assert r.returncode == 2 and "missing although" in r.stderr, (r.stdout, r.stderr)
    assert not target.exists()


@_linux_bwrap
def test_launcher_hand_written_copilot_agents_and_workflows_pass_unprotected(tmp_path):
    p = (tmp_path / "proj").resolve()
    (p / ".github" / "agents").mkdir(parents=True)
    (p / ".github" / "agents" / "reviewer.agent.md").write_text("---\nname: r\n---\n")
    (p / ".github" / "workflows").mkdir()
    (p / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    assert "control-plane (ro): <none>" in r.stdout


@_linux_bwrap
def test_launcher_copilot_team_argv_order_and_workflows_never_bound(tmp_path):
    p = _team((tmp_path / "proj").resolve(), "copilot")
    (p / ".github" / "workflows").mkdir()
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    binds = _effective_binds(r.stdout)
    pos = {b: i for i, b in enumerate(binds)}
    root = pos[("--bind", str(p))]
    entries = (*protected_write_paths("copilot"), *governed_roster_paths("copilot"),
               team_marker_path("copilot"))
    anc = [pos[("--bind", str(p / a))] for a in control_plane_ancestors(entries)]
    ro = [pos[("--ro-bind", str(p / e))] for e in entries]
    assert root < min(anc) and max(anc) < min(ro)
    assert ("--bind", str(p / ".github")) in pos  # rename-locked, still writable
    assert not any(dst.startswith(str(p / ".github" / "workflows")) for _, dst in binds)


@_linux_bwrap
def test_launcher_codex_config_is_protect_if_present(tmp_path):
    p = _team((tmp_path / "proj").resolve(), "codex")
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    assert str(p / CODEX_CONFIG_REL) not in r.stdout
    (p / CODEX_CONFIG_REL).write_text('approval_policy = "on-request"\n')
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    assert ("--ro-bind", str(p / CODEX_CONFIG_REL)) in _effective_binds(r.stdout)


@_linux_bwrap
def test_launcher_refuses_a_symlinked_copilot_team_dir(tmp_path):
    p = (tmp_path / "proj").resolve()
    p.mkdir()
    elsewhere = _team((tmp_path / "elsewhere").resolve(), "copilot")
    (p / ".github").symlink_to(elsewhere / ".github")
    r = _check("--scratch", str(p))
    assert r.returncode == 2 and "symlink" in r.stderr


_PROBE = r"""
import errno, os, sys
os.chdir(sys.argv[1])
def attempt(label, fn):
    try:
        fn(); print(label, "OK")
    except OSError as e:
        print(label, errno.errorcode.get(e.errno, e.errno))
for d in (".github", ".github/agents", ".github/agents/references", ".codex", ".codex/agents"):
    attempt("rename:" + d, lambda d=d: os.rename(d, d + ".moved"))
attempt("workflow", lambda: open(".github/workflows/x.yml", "a").write("x\n"))
attempt("gh-switch", lambda: open(".github/agents/references/agent-privilege.json", "w").write("{}"))
attempt("gh-plant", lambda: open(".github/agents/references/authorized-verify-keys/k.pub.pem", "w").write("k"))
attempt("cx-roster", lambda: open(".codex/agents/references/security-approvers.txt", "w").write("me"))
attempt("cx-config", lambda: open(".codex/config.toml", "w").write("x"))
attempt("agent-file", lambda: open(".github/agents/new.agent.md", "w").write("x"))
"""


@_bwrap
def test_mechanism_launcher_locks_copilot_and_codex_roots_workflows_stay_writable(tmp_path):
    p = _team(_team((tmp_path / "proj").resolve(), "copilot"), "codex")
    (p / ".github" / "workflows").mkdir()
    (p / CODEX_CONFIG_REL).write_text("x = 1\n")
    res = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--",
                          sys.executable, "-c", _PROBE, str(p)],
                         capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})
    assert res.returncode == 0, res.stderr
    out = dict(line.split(" ", 1) for line in res.stdout.splitlines())
    for d in (".github", ".github/agents", ".github/agents/references", ".codex", ".codex/agents"):
        assert out[f"rename:{d}"] == "EBUSY", (d, out)
    assert out["workflow"] == "OK" and out["agent-file"] == "OK"
    for k in ("gh-switch", "gh-plant", "cx-roster", "cx-config"):
        assert out[k] == "EROFS", (k, out)
    assert not (p / ".github.moved").exists()


# --- T8: goose Seatbelt (UNVERIFIED on a macOS host) ---------------------------------------------

def _lit(rel: str) -> str:
    return f'(literal (string-append (param "WORKSPACE_ROOT") "/{rel}"))'


def test_seatbelt_denies_sibling_roots_after_the_workspace_allow():
    prof = _build_seatbelt_profile(["."])
    cp = prof.index(";; --- Control-plane protection")
    assert prof.index("(allow file-write*") < cp
    for key in ("copilot", "codex"):
        for rel in (*protected_write_paths(key), *governed_roster_paths(key), team_marker_path(key)):
            assert f'"/{rel}"))' in prof[cp:], rel
    assert f'"/{CODEX_CONFIG_REL}"))' in prof[cp:]
    for anc in (".codex", ".codex/agents", ".codex/agents/references"):
        assert _lit(anc) in prof
    assert _lit(".github") not in prof and _lit(".github/agents") not in prof


def test_seatbelt_github_ancestor_literals_only_with_a_copilot_team():
    prof = _build_seatbelt_profile(["."], copilot_present=True)
    for anc in (".github", ".github/agents", ".github/agents/references"):
        assert _lit(anc) in prof
    assert _lit(".github/workflows") not in prof


def test_goose_output_threads_copilot_presence(monkeypatch):
    from agentteams.frameworks import _goose_sandbox_emit as gse

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(gse, "is_sandbox_capable", lambda *_a, **_k: True)
    m = {"framework": "goose", "privilege_profile": "confined"}
    prof = dict(gse.goose_sandbox_output_files(m))[gse.GOOSE_SANDBOX_PROFILE_REL]
    assert _lit(".github") not in prof
    m[SIBLING_DENY_DIRS_KEY] = (".github/agents",)
    prof = dict(gse.goose_sandbox_output_files(m))[gse.GOOSE_SANDBOX_PROFILE_REL]
    assert _lit(".github") in prof


# --- T9: advisories ------------------------------------------------------------------------------

def _live(p: Path, deny: list[str]) -> None:
    (p / ".claude").mkdir(parents=True, exist_ok=True)
    (p / ".claude" / "settings.json").write_text(json.dumps(
        {"sandbox": {"enabled": True, "failIfUnavailable": True,
                     "filesystem": {"allowWrite": ["."], "denyWrite": deny}}}))


def test_stale_deny_advisory_names_each_missing_entry(tmp_path, capsys):
    p = tmp_path
    assert _warn_live_sandbox_deny_paths_missing(p) == []  # no live settings
    _team(p, "codex")
    _live(p, [".codex", ".github/agents", str(p / "gone"), "~/.agentteams-definitely-absent-x"])
    missing = _warn_live_sandbox_deny_paths_missing(p)
    assert missing == [".github/agents", str(p / "gone"), "~/.agentteams-definitely-absent-x"]
    err = capsys.readouterr().err
    assert "Can't mkdir" in err and "remove '.github/agents'" in err


def test_reverse_advisory_names_present_teams_the_live_block_lacks(tmp_path, capsys):
    p = tmp_path
    agents = p / ".claude" / "agents"
    agents.mkdir(parents=True)
    m = {"framework": "claude"}
    assert _warn_sibling_teams_under_claude_sandbox(m, p, agents, (".codex",)) == []  # no live block
    _live(p, [".claude", "./.codex"])
    assert _warn_sibling_teams_under_claude_sandbox(m, p, agents, (".codex",)) == []
    assert capsys.readouterr().err == ""
    lacking = _warn_sibling_teams_under_claude_sandbox(m, p, agents, (".codex", ".goose"))
    assert lacking == [".goose"] and '[".goose"]' in capsys.readouterr().err
    # the team THIS run generates into its default dir counts as present (fires on generation)
    gh = p / ".github" / "agents"
    lacking = _warn_sibling_teams_under_claude_sandbox({"framework": "copilot-cli"}, p, gh, ())
    assert lacking == [".github/agents"]


def test_advisories_are_silent_for_the_default_layout(tmp_path, capsys):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    _live(tmp_path, [".claude"])
    assert _apply_sibling_team_denies({"framework": "claude"}, tmp_path, agents) == ()
    assert capsys.readouterr().err == ""
