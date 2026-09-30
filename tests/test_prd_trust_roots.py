"""PR-D (2026-09-30): the remaining sandbox trust roots and small items.

* comment-only control-plane stubs (write-if-absent, ``O_CREAT|O_EXCL|O_NOFOLLOW``) and the proof
  that a stub reads exactly like an absent file in every reader;
* the emission predicate matrix (sandbox x management predicate x framework);
* the launcher anchor fix: a framework's entries are required only for an agentteams team
  (``references/build-log.json``), the marker itself is ro-bound, rosters follow the switch;
* ``permissions.deny`` / Seatbelt coverage of the rosters; the Claude ``denyWrite`` is unchanged;
* ``integrity.verify`` on a missing manifest, ``--verify-integrity`` and the gate hook's ``ask``;
* the in-sandbox ``--update`` preflight (non-mutating, no FIFO hang);
* F-2: ``--sign-decision`` team detection and verify-before-append;
* the non-default ``--output`` warning naming every protected path.

Labels: permissions.deny additions are emitted, product-UNVERIFIED; Seatbelt is UNVERIFIED (no
macOS host); launcher probes are mechanism-verified where bwrap works. The HMAC grant route is
NOT closed here (addressed separately by the Ed25519-grants change).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import control_plane_io as cpio
from agentteams import integrity
from agentteams.cli import decision_log as dl
from agentteams.cli import management_directives as md
from agentteams.cli.artifacts import MANAGEMENT_AUTHORITY_REL_PATH
from agentteams.cli.generate_helpers import (
    _emit_privilege_artifacts,
    _preflight_sandboxed_write,
    _warn_sandbox_deny_path_mismatch,
)
from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile
from agentteams.frameworks._sandbox_emit import (
    CONTROL_PLANE_STUB_TEXT,
    GRANT_ROSTER_PROJECT_REL,
    TEAM_MARKER_REL,
    _build_sandbox_block,
    governed_roster_paths,
    protected_write_paths,
)

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
HOOK_TEMPLATE = REPO / "agentteams" / "templates" / "universal" / "hooks" / "constitutional-gate.py"
_BWRAP = shutil.which("bwrap")
_linux_bwrap = pytest.mark.skipif(not (sys.platform.startswith("linux") and _BWRAP),
                                  reason="confine-run's Linux branch needs bwrap on PATH")


def _bwrap_usable() -> bool:
    if not _BWRAP or not sys.platform.startswith("linux"):
        return False
    probe = subprocess.run([_BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-user", "true"],
                           capture_output=True, text=True)
    return probe.returncode == 0


_bwrap = pytest.mark.skipif(not _bwrap_usable(), reason="bubblewrap not usable on this host")


# --- single source + reader locks --------------------------------------------------------------

def test_roster_names_are_locked_to_their_readers():
    names = {p.rsplit("/", 1)[1] for p in governed_roster_paths("claude")}
    assert dl._DECISION_AUTHORS_FILE in names
    assert md.AUTHORIZED_MANAGERS_REL.rsplit("/", 1)[1] in names
    assert MANAGEMENT_AUTHORITY_REL_PATH.rsplit("/", 1)[1] in names
    assert set(CONTROL_PLANE_STUB_TEXT) == names
    assert governed_roster_paths("goose")[0].startswith(".goose/recipes/references/")
    assert GRANT_ROSTER_PROJECT_REL == f"references/{dl._DECISION_AUTHORS_FILE}"
    assert TEAM_MARKER_REL == "references/build-log.json"


def test_claude_denywrite_unchanged_by_prd():
    deny = _build_sandbox_block(None, None, platform="linux")["filesystem"]["denyWrite"]
    assert deny == [".claude/agents/references/agent-privilege.json",
                    ".claude/hooks/constitutional-gate.py",
                    ".claude/agents/references/authorized-verify-keys", ".claude"]


def test_seatbelt_denies_rosters_after_the_workspace_allow_without_a_references_ancestor():
    prof = _build_seatbelt_profile(["."])
    block = prof.index(";; --- Control-plane protection")
    assert prof.index("(allow file-write*") < block
    for rel in (*governed_roster_paths("goose"), GRANT_ROSTER_PROJECT_REL,
                ".claude/hooks/constitutional-gate.py"):
        assert f'"/{rel}"))' in prof[block:], rel
    rename = prof[prof.index("(deny file-write-unlink file-write-create"):]
    rename = rename[:rename.index("\n)")]
    assert '"/references"))' not in rename  # no create-deny on a project-root `references` dir
    assert '"/.goose/recipes/references"))' in rename
    assert "UNVERIFIED" in prof


def test_goose_control_plane_keeps_the_claude_hook_and_launcher_covers_it():
    """3c: a goose-confined agent must not replace the hook the next Claude session runs."""
    assert ".claude/hooks/constitutional-gate.py" in protected_write_paths("goose")
    body = re.search(r"CONTROL_PLANE_REL=\(([^)]*)\)", LAUNCHER.read_text()).group(1).split()
    assert set(protected_write_paths("goose")) <= set(body)


# --- stubs: stub == absent for every reader -----------------------------------------------------

def _team(tmp_path: Path, stub: bool) -> Path:
    root = tmp_path / ("stub" if stub else "absent")
    (root / "references").mkdir(parents=True)
    if stub:
        for name, text in CONTROL_PLANE_STUB_TEXT.items():
            (root / "references" / name).write_text(text, encoding="utf-8")
    return root


def _outcome(fn):
    try:
        return ("ok", fn())
    except RuntimeError as exc:  # every reader refuses with RuntimeError (GrantError included)
        return ("raise", type(exc).__name__, str(exc))


def _waiver(root: Path):
    from agentteams.cli.security_gate import _validate_security_waiver

    row = {"action_reviewed": "x", "conditions_verified": "verified", "approver": "security",
           "ticket_id": "T-1", "reason_code": "r", "expires_at": "2000-01-01T00:00:00Z"}
    return _validate_security_waiver(row, action="x", output_dir=root)


READERS = {
    "decision_authors": lambda r: dl._approved_decision_authors(r),
    "waiver_approver": _waiver,
    "manager_roster": lambda r: md._read_manager_roster(r),
    "roster_names_a_manager": lambda r: md._roster_names_a_manager(r),
    "manager_on_roster": lambda r: md._manager_on_roster("@ops", r),
}
from agentteams.cli import grants as _grants  # noqa: E402

# The grant readers (the Ed25519-grants change may move them; include whichever exist).
if hasattr(_grants, "_roster_names_an_approver"):
    READERS["grant_roster_names_an_approver"] = lambda r: _grants._roster_names_an_approver(r)
if hasattr(_grants, "_assert_approver_on_roster"):
    READERS["grant_assert_approver"] = lambda r: _grants._assert_approver_on_roster("@security", r)


@pytest.mark.parametrize("reader", sorted(READERS))
def test_stub_equals_absent(tmp_path, reader):
    fn = READERS[reader]
    assert _outcome(lambda: fn(_team(tmp_path, True))) == _outcome(lambda: fn(_team(tmp_path, False)))


def test_management_authority_stub_declares_nothing():
    data = json.loads(CONTROL_PLANE_STUB_TEXT["management-authority.json"])
    assert data["is_management_repo"] is False and data["authorized_managers"] == []


# --- stubs: never overwrite, never follow a symlink ----------------------------------------------

_CLAUDE_ON = {"framework": "claude", "host_features": ["claude:sandbox"], "enforce_decision_signing": True}


def test_stub_never_overwrites_operator_roster_or_follows_a_symlink(tmp_path):
    team = tmp_path / "agents"
    refs = team / "references"
    refs.mkdir(parents=True)
    populated = "# mine\nalice\n"
    (refs / "security-approvers.txt").write_text(populated, encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched\n", encoding="utf-8")
    (refs / "authorized-managers.txt").symlink_to(outside)
    (refs / "management-authority.json").symlink_to(tmp_path / "dangling.json")
    for _ in range(2):
        assert cpio.write_control_plane_stubs(team, "claude", _CLAUDE_ON) == []
    assert (refs / "security-approvers.txt").read_text() == populated
    assert outside.read_text() == "untouched\n"
    assert not (tmp_path / "dangling.json").exists()
    assert (refs / "management-authority.json").is_symlink()


def test_create_exclusive_refuses_a_planted_symlink_even_if_raced(tmp_path):
    target = tmp_path / "victim"
    link = tmp_path / "roster"
    link.symlink_to(target)
    assert cpio._create_exclusive(link, "x") is False
    assert not target.exists()


def test_stub_survives_update_paths_through_the_wrapper(tmp_path):
    team = tmp_path / ".claude" / "agents"
    (team / "references").mkdir(parents=True)
    roster = team / "references" / "security-approvers.txt"
    roster.write_text("# operator\nbob\n", encoding="utf-8")
    before = roster.read_bytes()
    for _ in range(3):  # generate, update, heal all call the one wrapper
        _emit_privilege_artifacts(dict(_CLAUDE_ON), team)
    assert roster.read_bytes() == before
    assert (team / "references" / "authorized-managers.txt").read_text() == \
        CONTROL_PLANE_STUB_TEXT["authorized-managers.txt"]


@pytest.mark.parametrize("framework", ["claude", "goose"])
@pytest.mark.parametrize("sandbox", [True, False])
@pytest.mark.parametrize("managers", [[], ["ops"]])
@pytest.mark.parametrize("is_mgmt", [True, False])
def test_management_emission_predicate_matrix(tmp_path, framework, sandbox, managers, is_mgmt):
    manifest = {"framework": framework, "enforce_decision_signing": True,
                "authorized_managers": managers, "is_management_repo": is_mgmt}
    if sandbox:
        manifest["host_features"] = [f"{framework}:sandbox"]
    team = tmp_path / "agents"
    team.mkdir()
    _emit_privilege_artifacts(manifest, team)
    refs = team / "references"
    predicate = bool(managers) or is_mgmt
    cfg = refs / "management-authority.json"
    assert cfg.exists() == (sandbox or predicate)
    if cfg.exists():
        data = json.loads(cfg.read_text())
        is_stub = data.get("note", "").startswith("stub")
        assert is_stub == (not predicate)
    roster = refs / "authorized-managers.txt"
    assert roster.exists() == (sandbox or bool(managers))
    if roster.exists():
        assert (roster.read_text() == CONTROL_PLANE_STUB_TEXT["authorized-managers.txt"]) == (not managers)
    assert (refs / "security-approvers.txt").exists() == sandbox
    if not sandbox and not predicate:  # byte-identical to before PR-D: only the switch
        assert sorted(p.name for p in refs.iterdir()) == ["agent-privilege.json"]


def test_stub_predicate_is_per_framework():
    assert cpio.stubs_enabled("claude", {"host_features": ["goose:sandbox"]}) is False
    assert cpio.stubs_enabled("goose", {"host_features": ["claude:sandbox"]}) is False
    assert cpio.stubs_enabled("codex", {"privilege_profile": "confined"}) is False
    assert cpio.stubs_enabled("goose", {"privilege_profile": "confined"}) is True


def test_multi_sync_projects_the_stubs(tmp_path):
    from agentteams import multi_sync

    written = multi_sync._emit_privilege_artifacts(
        tmp_path, "claude", {"privilege_profile": "confined"}, dry_run=False)
    refs = multi_sync.framework_agents_dir(tmp_path, "claude") / "references"
    for name in CONTROL_PLANE_STUB_TEXT:
        assert (refs / name).is_file() and str((refs / name).resolve()) in written


# --- launcher anchor ----------------------------------------------------------------------------

def _check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(LAUNCHER), *args, "--check", "--", "true"],
                          capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})


def _claude_team(p: Path, *, marker: bool = True, rosters: bool = True) -> Path:
    for rel in (*protected_write_paths("claude"), *(governed_roster_paths("claude") if rosters else ())):
        q = p / rel
        if q.name == "authorized-verify-keys":
            q.mkdir(parents=True, exist_ok=True)
            (q / "README.md").write_text("x\n")
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text("{}\n")
    if marker:
        (p / ".claude/agents" / TEAM_MARKER_REL).write_text("{}\n")
    return p


@_linux_bwrap
def test_launcher_hand_written_claude_agents_without_build_log_passes(tmp_path):
    p = tmp_path / "proj"
    (p / ".claude" / "agents").mkdir(parents=True)
    (p / ".claude" / "agents" / "reviewer.md").write_text("---\nname: r\n---\n")
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    assert "control-plane (ro): <none>" in r.stdout


@_linux_bwrap
def test_launcher_dies_on_missing_roster_when_team_exists_and_binds_the_marker(tmp_path):
    p = _claude_team(tmp_path / "proj", rosters=False)
    r = _check("--scratch", str(p))
    assert r.returncode == 2 and "security-approvers.txt" in r.stderr
    assert not (p / ".claude/agents/references/security-approvers.txt").exists()
    p2 = _claude_team(tmp_path / "ok")
    r = _check("--scratch", str(p2))
    assert r.returncode == 0, r.stderr
    assert str(p2.resolve() / ".claude/agents" / TEAM_MARKER_REL) in r.stdout


@_linux_bwrap
def test_launcher_rosters_follow_the_switch(tmp_path):
    """A team whose switch is absent is refused on the SWITCH, never on a roster first."""
    p = _claude_team(tmp_path / "proj", rosters=False)
    (p / ".claude/agents/references/agent-privilege.json").unlink()
    r = _check("--scratch", str(p))
    assert r.returncode == 2 and "agent-privilege.json" in r.stderr
    assert "security-approvers.txt" not in r.stderr


@_linux_bwrap
@pytest.mark.parametrize("marker", [True, False])
def test_launcher_goose_only_sandbox_with_a_claude_team_follows_the_anchor(tmp_path, marker):
    p = tmp_path / "proj"
    goose = p / ".goose" / "recipes" / "references"
    goose.mkdir(parents=True)
    for rel in (*protected_write_paths("goose"), *governed_roster_paths("goose")):
        q = p / rel
        if q.name == "authorized-verify-keys":
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text("{}\n")
    (goose / "build-log.json").write_text("{}\n")
    (p / ".claude" / "agents" / "references").mkdir(parents=True)  # a claude team, entries missing
    if marker:
        (p / ".claude" / "agents" / TEAM_MARKER_REL).write_text("{}\n")
    r = _check("--scratch", str(p))
    assert r.returncode == (2 if marker else 0), (r.stdout, r.stderr)
    if not marker:  # goose-only coverage: the Claude hook the goose control plane lists is bound
        assert str(p.resolve() / ".claude/hooks/constitutional-gate.py") in r.stdout
        assert str(p.resolve() / ".goose/recipes/references/security-approvers.txt") in r.stdout


@_linux_bwrap
def test_launcher_project_root_roster_is_protect_if_present(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    r = _check("--scratch", str(p))
    assert r.returncode == 0 and "<none>" in r.stdout
    (p / "references").mkdir()
    (p / "references" / "security-approvers.txt").write_text("alice\n")
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    assert str(p.resolve() / GRANT_ROSTER_PROJECT_REL) in r.stdout


@_linux_bwrap
def test_launcher_refuses_a_symlinked_marker(tmp_path):
    p = _claude_team(tmp_path / "proj", marker=False)
    real = tmp_path / "elsewhere.json"
    real.write_text("{}\n")
    (p / ".claude/agents" / TEAM_MARKER_REL).symlink_to(real)
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
refs = ".claude/agents/references/"
attempt("roster", lambda: open(refs + "security-approvers.txt", "a").write("me\n"))
attempt("marker", lambda: os.unlink(refs + "build-log.json"))
attempt("grant", lambda: open("references/security-approvers.txt", "a").write("me\n"))
attempt("inside", lambda: open(refs + "log.csv", "a").write("row\n"))
"""


@_bwrap
def test_mechanism_launcher_makes_rosters_and_marker_read_only(tmp_path):
    p = _claude_team(tmp_path / "proj").resolve()
    (p / "references").mkdir()
    (p / "references" / "security-approvers.txt").write_text("alice\n")
    res = subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--", sys.executable, "-c",
                          _PROBE, str(p)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = dict(line.split(" ", 1) for line in res.stdout.splitlines())
    assert out["roster"] == "EROFS" and out["grant"] == "EROFS"
    assert out["marker"] in {"EBUSY", "EROFS"}
    assert out["inside"] == "OK"


# --- permissions.deny ---------------------------------------------------------------------------

def test_permission_deny_rules_cover_rosters():
    from agentteams.frameworks._sandbox_emit import permission_deny_rules

    rules = permission_deny_rules("claude")
    for rel in (*governed_roster_paths("claude"), GRANT_ROSTER_PROJECT_REL,
                ".claude/settings.json", ".claude/settings.local.json"):
        assert f"Edit(/{rel})" in rules, rel
    assert "Edit(/.claude/hooks/**)" in rules


# --- integrity: a missing manifest --------------------------------------------------------------

def _git_repo_tracking_manifest(root: Path) -> Path:
    (root / "references").mkdir(parents=True)
    (root / integrity.MANIFEST_REL_PATH).write_text("{}\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "add", integrity.MANIFEST_REL_PATH], check=True, env=env)
    (root / integrity.MANIFEST_REL_PATH).unlink()
    return root


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_missing_manifest_is_benign_in_an_ordinary_consumer(tmp_path):
    assert integrity.verify(tmp_path) == []


@needs_git
def test_missing_manifest_is_a_finding_when_the_repo_tracks_one(tmp_path):
    root = _git_repo_tracking_manifest(tmp_path / "r")
    findings = integrity.verify(root)
    assert [f.reason for f in findings] == ["manifest-missing"]
    assert "missing" in findings[0].describe()


def test_missing_manifest_is_a_finding_when_the_scanner_is_inside_the_project(tmp_path, monkeypatch):
    fake = tmp_path / ".venv" / "lib" / "site-packages" / "agentteams" / "scan.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")

    class _Spec:
        origin = str(fake)

    monkeypatch.setattr(integrity.importlib.util, "find_spec", lambda name: _Spec())
    assert [f.reason for f in integrity.verify(tmp_path)] == ["manifest-missing"]
    # the top-level agentteams/scan.py layout (e.g. site-packages as repo_root) is not flagged
    top = tmp_path / "top"
    (top / "agentteams").mkdir(parents=True)
    _Spec.origin = str(top / "agentteams" / "scan.py")
    assert integrity.verify(top) == []


@needs_git
def test_verify_integrity_cli_reports_a_missing_shipped_manifest(tmp_path, capsys):
    import build_team

    root = _git_repo_tracking_manifest(tmp_path / "r")
    assert build_team.main(["--verify-integrity", "--output", str(root)]) == 1
    err = capsys.readouterr().err
    assert "MISMATCH" in err and "integrity manifest is missing" in err


def _hook(tmp_path: Path, *, fail_closed: bool) -> Path:
    text = HOOK_TEMPLATE.read_text(encoding="utf-8")
    if fail_closed:
        text = text.replace("_FAIL_CLOSED_ON_ERROR = False", "_FAIL_CLOSED_ON_ERROR = True", 1)
    hook = tmp_path / "gate.py"
    hook.write_text(text, encoding="utf-8")
    return hook


@needs_git
@pytest.mark.parametrize("fail_closed", [True, False])
def test_gate_hook_asks_on_a_missing_manifest_only_when_fail_closed(tmp_path, fail_closed):
    root = _git_repo_tracking_manifest(tmp_path / "r")
    hook = _hook(tmp_path, fail_closed=fail_closed)
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "hi\n"}}),
        capture_output=True, text=True, cwd=str(root), timeout=60,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr
    if fail_closed:
        decision = json.loads(proc.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "ask"
        assert "integrity manifest is missing" in decision["permissionDecisionReason"]
    else:
        assert not proc.stdout.strip()


# --- 3b preflight -------------------------------------------------------------------------------

def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"check": False, "dry_run": False, **kw})


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_preflight_refuses_a_write_denied_team_dir_before_writing(tmp_path, capsys):
    team = tmp_path / "agents"
    refs = team / "references"
    refs.mkdir(parents=True)
    refs.chmod(0o555)
    try:
        before = sorted(os.listdir(refs))
        assert _preflight_sandboxed_write(_args(), team) == 2
        assert "write-denied" in capsys.readouterr().err
        assert _preflight_sandboxed_write(_args(dry_run=True), team) is None
        assert "would be refused" in capsys.readouterr().err
        assert sorted(os.listdir(refs)) == before
    finally:
        refs.chmod(0o755)


def test_preflight_passes_a_writable_team_and_leaves_nothing(tmp_path):
    team = tmp_path / "agents"
    (team / "references").mkdir(parents=True)
    switch = team / "references" / "agent-privilege.json"
    switch.write_text("{}\n")
    stamp = switch.stat().st_mtime_ns
    assert _preflight_sandboxed_write(_args(), team) is None
    assert sorted(os.listdir(team / "references")) == ["agent-privilege.json"]
    assert switch.read_text() == "{}\n" and switch.stat().st_mtime_ns == stamp
    assert _preflight_sandboxed_write(_args(), tmp_path / "does-not-exist") is None


def test_file_probe_ignores_mode_444_enoent_and_symlinks(tmp_path):
    ro = tmp_path / "ro.json"
    ro.write_text("{}")
    ro.chmod(0o444)
    assert cpio._probe_file(ro) is None  # atomic rename still replaces it: not a denial
    assert cpio._probe_file(tmp_path / "missing") is None  # ENOENT = can't tell
    (tmp_path / "link").symlink_to(ro)
    assert cpio._probe_file(tmp_path / "link") is None


def test_preflight_does_not_hang_on_a_planted_fifo(tmp_path):
    refs = tmp_path / "agents" / "references"
    refs.mkdir(parents=True)
    os.mkfifo(refs / "security-approvers.txt")
    os.mkfifo(refs / "agent-privilege.json")
    code = ("import sys; from pathlib import Path; from agentteams.control_plane_io import "
            "probe_team_write_denied as p; print(p(Path(sys.argv[1])))")
    res = subprocess.run([sys.executable, "-c", code, str(tmp_path / "agents")], capture_output=True,
                         text=True, timeout=20, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert res.returncode == 0 and res.stdout.strip() == "None", res.stderr


@_bwrap
def test_mechanism_preflight_sees_a_read_only_mount(tmp_path):
    team = (tmp_path / "agents").resolve()
    (team / "references").mkdir(parents=True)
    (team / "references" / "agent-privilege.json").write_text("{}\n")
    code = ("import sys; from pathlib import Path; from agentteams.control_plane_io import "
            "probe_team_write_denied as p; r = p(Path(sys.argv[1])); print('DENIED' if r else 'OK')")
    res = subprocess.run([_BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                          "--unshare-user", "--setenv", "PYTHONPATH", str(REPO), "--",
                          sys.executable, "-c", code, str(team)], capture_output=True, text=True)
    assert res.stdout.strip() == "DENIED", res.stderr


# --- F-2: --sign-decision ------------------------------------------------------------------------

def _keyfile(tmp_path: Path):
    crypto = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    keyfile = tmp_path / "op.key"
    keyfile.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    pub = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return keyfile, pub


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "date": "2026-09-30", "action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
        "effect_grants": "write:cross-repo", "derives_from": "approved-cleanup",
        "key_id": "op-1", "author": "security"}))
    return spec


def _sign(tmp_path, monkeypatch, team: Path) -> int:
    import build_team

    return build_team.main(["--sign-decision", str(_spec(tmp_path)), "--output", str(team)])


def test_sign_decision_refuses_non_team_output(tmp_path, monkeypatch, capsys):
    keyfile, _ = _keyfile(tmp_path)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    assert _sign(tmp_path, monkeypatch, tmp_path / "proj") == 1
    assert "not an agentteams team dir" in capsys.readouterr().err
    assert not (tmp_path / "proj" / "references" / "security-decisions.log.csv").exists()


@pytest.mark.parametrize("marker", ["agent-privilege.json", "build-log.json"])
def test_sign_decision_detects_a_team_by_either_marker(tmp_path, monkeypatch, marker):
    keyfile, pub = _keyfile(tmp_path)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    team = tmp_path / "agents"
    (team / "references" / "authorized-verify-keys").mkdir(parents=True)
    (team / "references" / marker).write_text("{}\n")
    (team / "references" / "authorized-verify-keys" / "op-1.pub.pem").write_bytes(pub)
    assert _sign(tmp_path, monkeypatch, team) == 0
    assert "ed25519" in (team / "references" / "security-decisions.log.csv").read_text()


def test_sign_decision_refuses_key_mismatch(tmp_path, monkeypatch, capsys):
    keyfile, _ = _keyfile(tmp_path)
    (tmp_path / "other").mkdir()
    _, other_pub = _keyfile(tmp_path / "other")  # a different key: the public/private mismatch
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    team = tmp_path / "agents"
    (team / "references" / "authorized-verify-keys").mkdir(parents=True)
    (team / "references" / "agent-privilege.json").write_text("{}\n")
    (team / "references" / "authorized-verify-keys" / "op-1.pub.pem").write_bytes(other_pub)
    assert _sign(tmp_path, monkeypatch, team) == 1
    assert "does not verify" in capsys.readouterr().err
    assert not (team / "references" / "security-decisions.log.csv").exists()


def test_sign_decision_refuses_missing_store_key(tmp_path, monkeypatch, capsys):
    keyfile, _ = _keyfile(tmp_path)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    team = tmp_path / "agents"
    (team / "references").mkdir(parents=True)
    (team / "references" / "build-log.json").write_text("{}\n")
    assert _sign(tmp_path, monkeypatch, team) == 1
    assert "no Ed25519 verify key" in capsys.readouterr().err
    assert not (team / "references" / "security-decisions.log.csv").exists()


# --- 3a: the non-default --output warning -------------------------------------------------------

@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_nondefault_output_warning_names_every_protected_path(tmp_path, capsys, framework):
    manifest = {"framework": framework, "host_features": [f"{framework}:sandbox"]}
    out = tmp_path / "custom" / "team"
    _warn_sandbox_deny_path_mismatch(manifest, out)
    err = capsys.readouterr().err
    for rel in ("references/agent-privilege.json", "references/authorized-verify-keys",
                *(f"references/{n}" for n in CONTROL_PLANE_STUB_TEXT)):
        assert str(out / rel) in err, rel
    default = tmp_path / (".claude/agents" if framework == "claude" else ".goose/recipes")
    _warn_sandbox_deny_path_mismatch(manifest, default)
    assert capsys.readouterr().err == ""


def test_manifest_expected_never_runs_a_planted_fsmonitor(tmp_path):
    """@security PR-D C1: the gate hook runs this outside the sandbox, in a repo the agent can
    write. A `core.fsmonitor` planted in the repo's own .git/config must not execute."""
    import shutil as _sh
    import subprocess as _sp

    if _sh.which("git") is None:
        pytest.skip("git not available")
    from agentteams import integrity

    repo = tmp_path / "repo"
    repo.mkdir()
    _sp.run(["git", "init", "-q", str(repo)], check=True)
    marker = tmp_path / "PWNED"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _sp.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True)
    integrity._manifest_expected(repo)
    assert not marker.exists(), "a repo-planted core.fsmonitor ran outside the sandbox"
