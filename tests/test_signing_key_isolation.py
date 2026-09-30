"""F-1: the operator decision-signing private key is unreadable from every emitted sandbox.

Covers the three emitters (Claude ``sandbox`` block + ``permissions.deny``, goose Seatbelt
profile, ``confine-run.sh``), the write-root guard, the legacy-location transitional deny, the
provisioning script (modes, refusals, ``--migrate``), ``--sign-decision`` location warnings, the
host-local advisory, and the invariant that keeps the env-var HMAC residual a residual: no
relaxing or elevated path accepts an HMAC-only signature.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks import _sandbox_emit as se
from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile
from agentteams.frameworks.claude import ClaudeAdapter, _read_template_asset

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
PROVISION = REPO / "references" / "authorized-verify-keys" / "provision-operator-signing-key.sh"
KEYS = "~/.config/agentteams/keys"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated HOME, so host key files never leak into (or out of) a test."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _settings(manifest: dict) -> dict:
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    return json.loads(files["../settings.hooks.example.json"])


# --- Claude block ----------------------------------------------------------------------------

@pytest.mark.parametrize("manifest", [
    {"privilege_profile": "confined"},
    {"privilege_profile": "exclusive"},
    {"privilege_profile": "cooperative", "host_features": ["claude:sandbox"]},
])
def test_every_claude_profile_denies_the_key_dir(home, manifest):
    d = _settings(manifest)
    fs = d["sandbox"]["filesystem"]
    assert KEYS in fs["denyRead"]
    comment = " ".join(d["_comment"])
    assert "Signing-key isolation (F-1)" in comment
    exclusive = manifest["privilege_profile"] == "exclusive"
    # allowRead and the exclusive comment are independent of the key deny (no over-confinement).
    assert ("allowRead" in fs) is exclusive
    assert ("Read-exclusion (privilege_profile: exclusive)" in comment) is exclusive
    if exclusive:
        assert fs["denyRead"][-1] == KEYS and "~/.ssh" in fs["denyRead"]


def test_permissions_deny_binds_the_builtin_tools(home):
    deny = _settings({"privilege_profile": "confined"})["permissions"]["deny"]
    assert "Read(~/.config/agentteams/keys/**)" in deny
    assert "Read(~/.config/agentteams/*.pem)" in deny
    # R11: Edit(...) covers Edit/Write/MultiEdit; project-anchored with a leading "/".
    assert "Edit(/.claude/agents/references/agent-privilege.json)" in deny
    assert "Edit(/.claude/agents/references/authorized-verify-keys/**)" in deny
    assert "Edit(/.claude/hooks/**)" in deny  # PR-D: subsumes the single gate-hook rule
    # Claude Code 2.1.251: "NotebookEdit(path) is not matched by file permission checks — only
    # Edit(path) rules are". There is no Grep(...)/Glob(...) form either (Read covers them).
    assert not [r for r in deny if r.split("(")[0] in {"NotebookEdit", "Write", "Grep", "Glob"}]
    for rule in deny:
        tool, _, path = rule.partition("(")
        assert path.startswith(("~/", "/.", "/references/")), rule


def test_permissions_deny_ships_only_with_the_sandbox_block(home):
    d = _settings({"host_features": []})
    assert "sandbox" not in d and "permissions" not in d


def test_permissions_deny_merges_into_existing_rules():
    example = json.dumps({"_comment": [], "permissions": {"deny": ["Bash(rm:*)"]}})
    deny = json.loads(se._inject_sandbox_block(example, None))["permissions"]["deny"]
    assert deny[0] == "Bash(rm:*)" and "Read(~/.config/agentteams/keys/**)" in deny


def test_legacy_key_files_are_denied_by_exact_path_only(home):
    legacy = home / ".config" / "agentteams"
    (legacy / "keys").mkdir(parents=True)
    (legacy / "decision-signing-op.pem").write_text("k")
    (legacy / "goose-sources.json").write_text("{}")
    fs = _settings({"privilege_profile": "confined"})["sandbox"]["filesystem"]
    assert fs["denyRead"] == [KEYS, "~/.config/agentteams/decision-signing-op.pem"]
    assert not [p for p in fs["denyRead"] if "*" in p]  # the sandbox takes no globs


def test_resolve_abspath_resolves_the_key_entries(home):
    fs = _settings({"privilege_profile": "exclusive", "resolve_deny_read_abspath": True})[
        "sandbox"]["filesystem"]
    assert str(home / ".config" / "agentteams" / "keys") in fs["denyRead"]
    assert not [p for p in fs["denyRead"] if p.startswith("~")]


@pytest.mark.parametrize("root", ["~/.config/agentteams/keys", "~/.config/agentteams/keys/sub"])
def test_write_root_inside_the_key_dir_is_refused(home, root):
    with pytest.raises(ValueError, match="signing-key directory"):
        se._build_sandbox_block([".", root])
    with pytest.raises(ValueError, match="signing-key directory"):
        _build_seatbelt_profile([".", root], deny_read=None, egress_endpoint=None)
    with pytest.raises(ValueError):
        se._build_sandbox_block([str(home / ".config" / "agentteams" / "keys")])


def test_write_root_beside_the_key_dir_is_allowed(home):
    se._build_sandbox_block([".", "~/.config/agentteams", "~/.config/agentteams/keysX"])


# --- goose Seatbelt --------------------------------------------------------------------------

@pytest.mark.parametrize("deny_network", [False, True])
def test_seatbelt_profile_denies_the_key_dir_in_every_profile(home, deny_network):
    legacy = home / ".config" / "agentteams"
    legacy.mkdir(parents=True)
    (legacy / "decision-signing-op.pem").write_text("k")
    deny_read = ["~/.ssh"] if deny_network else None
    prof = _build_seatbelt_profile(["."], deny_read, None, deny_network=deny_network)
    section = prof.split(";; --- Operator signing-key isolation")[1]
    assert '(subpath (string-append (param "HOME_DIR") "/.config/agentteams/keys"))' in section
    assert '"/.config/agentteams/decision-signing-op.pem"' in section
    assert "regex" not in prof  # exact paths only


# --- confine-run.sh --------------------------------------------------------------------------

def test_constants_are_locked_to_the_shell_literals():
    assert se.SIGNING_KEY_DIR == "~/.config/agentteams/keys"
    shell = se.SIGNING_KEY_DIR.replace("~", "$HOME", 1)
    assert f'MASK+=( "{shell}" )' in LAUNCHER.read_text(encoding="utf-8")
    assert f'CANONICAL_KEY_DIR="{shell}"' in PROVISION.read_text(encoding="utf-8")
    assert f'"$HOME"/{se.LEGACY_SIGNING_KEY_GLOB[2:]}' in LAUNCHER.read_text(encoding="utf-8")


def _bwrap_usable() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
        return False
    probe = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-user", "true"],
                           capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _bwrap_usable(), reason="unprivileged bwrap unavailable on this host")
def test_launcher_masks_the_keys_on_a_real_kernel(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config" / "agentteams"
    (cfg / "keys").mkdir(parents=True)
    (cfg / "keys" / "canary.pem").write_text("SECRET-CANARY")
    (cfg / "decision-signing-x.pem").write_text("LEGACY-CANARY")
    (cfg / "goose-sources.json").write_text("GOOSE-OK")
    excluded = home / "excluded.txt"
    excluded.write_text("EXCLUDED-CANARY")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    script = (
        f"cat {cfg}/keys/canary.pem; cat {cfg}/decision-signing-x.pem; "
        f"cat {cfg}/goose-sources.json; cat {excluded}; true"
    )
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    run = subprocess.run(
        # tmp_path sits under /tmp, which the launcher replaces with a tmpfs: bind the fake HOME
        # back in with --writable. The masks are mounted AFTER the binds, so they still apply.
        ["bash", str(LAUNCHER), "--scratch", str(scratch), "--writable", str(home),
         "--exclude", str(excluded), "--", "sh", "-c", script],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert run.returncode == 0, run.stderr  # an --exclude FILE no longer aborts bwrap
    assert "GOOSE-OK" in run.stdout  # the rest of ~/.config/agentteams stays readable
    for canary in ("SECRET-CANARY", "LEGACY-CANARY", "EXCLUDED-CANARY"):
        assert canary not in run.stdout
    check = subprocess.run(
        ["bash", str(LAUNCHER), "--scratch", str(scratch), "--check", "--", "true"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    line = next(ln for ln in check.stdout.splitlines() if "read-excluded" in ln)
    assert f"{cfg}/keys" in line and f"{cfg}/decision-signing-x.pem" in line


# --- provisioning script ---------------------------------------------------------------------

needs_openssl = pytest.mark.skipif(not shutil.which("openssl"), reason="openssl not installed")


@pytest.fixture()
def repo(tmp_path):
    """A throwaway repo layout holding a copy of the script and one Claude team dir."""
    r = tmp_path / "repo"
    store = r / "references" / "authorized-verify-keys"
    store.mkdir(parents=True)
    shutil.copy(PROVISION, store / PROVISION.name)
    team_refs = r / ".claude" / "agents" / "references"
    team_refs.mkdir(parents=True)
    (team_refs / "agent-privilege.json").write_text("{}\n")
    return r


def _provision(repo: Path, home: Path, *args: str, key_dir: str | None = None,
               team: bool = True):
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    if key_dir is not None:
        env["KEY_DIR"] = key_dir
    script = repo / "references" / "authorized-verify-keys" / PROVISION.name
    extra = ["--team-dir", str(repo / ".claude" / "agents")] if team and "--migrate" not in args else []
    return subprocess.run(["bash", str(script), *extra, *args], capture_output=True, text=True,
                          env=env, cwd=str(repo))


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@needs_openssl
def test_provision_writes_into_a_0700_keys_dir(repo, home):
    run = _provision(repo, home, "op-1")
    assert run.returncode == 0, run.stderr
    keys = home / ".config" / "agentteams" / "keys"
    assert _mode(keys) == 0o700
    assert _mode(keys / "decision-signing-op-1.pem") == 0o600
    # F-2: the public key lands in the TEAM store the gate reads, never the repo-root helper dir.
    assert (repo / ".claude/agents/references/authorized-verify-keys/op-1.pub.pem").is_file()
    assert not (repo / "references" / "authorized-verify-keys" / "op-1.pub.pem").exists()
    assert str(keys / "decision-signing-op-1.pem") in run.stdout


@needs_openssl
def test_provision_without_team_dir_refuses_and_lists_teams(repo, home):
    run = _provision(repo, home, "op-1", team=False)
    assert run.returncode == 2
    assert "no --team-dir" in run.stderr and "--team-dir .claude/agents" in run.stderr
    assert not (home / ".config" / "agentteams" / "keys" / "decision-signing-op-1.pem").exists()
    assert not list((repo / "references" / "authorized-verify-keys").glob("*.pub.pem"))


@needs_openssl
def test_provision_refuses_a_non_team_dir_and_writes_every_team(repo, home, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    run = _provision(repo, home, "--team-dir", str(plain), "op-1", team=False)
    assert run.returncode == 2 and "not an agentteams team dir" in run.stderr
    goose = repo / ".goose" / "recipes" / "references"
    goose.mkdir(parents=True)
    (goose / "build-log.json").write_text("{}\n")
    run = _provision(repo, home, "--team-dir", str(repo / ".goose" / "recipes"), "op-2")
    assert run.returncode == 0, run.stderr
    assert (repo / ".claude/agents/references/authorized-verify-keys/op-2.pub.pem").is_file()
    assert (goose / "authorized-verify-keys" / "op-2.pub.pem").is_file()


@needs_openssl
def test_provision_refuses_a_symlinked_keys_dir(repo, home, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    (home / ".config" / "agentteams").mkdir(parents=True)
    (home / ".config" / "agentteams" / "keys").symlink_to(target)
    run = _provision(repo, home, "op-1")
    assert run.returncode == 2 and "symlink" in run.stderr
    assert not list(target.iterdir())


@needs_openssl
def test_provision_refuses_a_group_readable_keys_dir(repo, home):
    keys = home / ".config" / "agentteams" / "keys"
    keys.mkdir(parents=True)
    keys.chmod(0o750)
    run = _provision(repo, home, "op-1")
    assert run.returncode == 2 and "mode 750" in run.stderr


@needs_openssl
def test_provision_refuses_an_in_repo_key_dir(repo, home):
    run = _provision(repo, home, "op-1", "--allow-unprotected-keydir", key_dir=str(repo / "k"))
    assert run.returncode == 2 and "inside the repo" in run.stderr


@needs_openssl
def test_provision_refuses_a_custom_key_dir_without_the_flag(repo, home, tmp_path):
    custom = tmp_path / "custom"
    run = _provision(repo, home, "op-1", key_dir=str(custom))
    assert run.returncode == 2 and "--allow-unprotected-keydir" in run.stderr
    assert not custom.exists()
    run = _provision(repo, home, "op-1", "--allow-unprotected-keydir", key_dir=str(custom))
    assert run.returncode == 0, run.stderr
    assert "NOT sandbox-denied" in run.stderr
    assert (custom / "decision-signing-op-1.pem").is_file()


@needs_openssl
def test_provision_warns_about_legacy_keys_and_moves_nothing(repo, home):
    legacy = home / ".config" / "agentteams"
    legacy.mkdir(parents=True)
    (legacy / "decision-signing-old.pem").write_text("OLD")
    run = _provision(repo, home, "op-1")
    assert run.returncode == 0, run.stderr
    assert "pre-F-1 location" in run.stderr and "--migrate" in run.stderr
    assert (legacy / "decision-signing-old.pem").read_text() == "OLD"


def test_migrate_moves_without_clobbering_or_deleting(repo, home):
    legacy = home / ".config" / "agentteams"
    keys = legacy / "keys"
    keys.mkdir(parents=True, mode=0o700)
    keys.chmod(0o700)
    (legacy / "decision-signing-a.pem").write_text("A")
    (legacy / "decision-signing-b.pem").write_text("B-OLD")
    (keys / "decision-signing-b.pem").write_text("B-NEW")  # collision: must not be clobbered
    (legacy / "goose-sources.json").write_text("{}")
    (home / ".bashrc").write_text(
        f"export AGENTTEAMS_DECISION_ED25519_KEYFILE={legacy}/decision-signing-a.pem\n")
    before = sorted(p.name for p in legacy.rglob("*") if p.is_file())
    run = _provision(repo, home, "--migrate")
    assert run.returncode == 1  # a skipped file is reported as a non-zero exit
    assert (keys / "decision-signing-a.pem").read_text() == "A"
    assert _mode(keys / "decision-signing-a.pem") == 0o600
    assert not (legacy / "decision-signing-a.pem").exists()
    assert (keys / "decision-signing-b.pem").read_text() == "B-NEW"
    assert (legacy / "decision-signing-b.pem").read_text() == "B-OLD"
    assert "SKIPPED" in run.stderr and ".bashrc" in run.stderr
    assert (legacy / "goose-sources.json").exists()
    after = sorted(p.name for p in legacy.rglob("*") if p.is_file())
    assert len(after) == len(before)  # nothing deleted


# --- --sign-decision warnings + host advisory ------------------------------------------------

def _key(path: Path, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("k")
    path.chmod(mode)
    return path


def test_keyfile_warnings(home, tmp_path):
    keys = home / ".config" / "agentteams" / "keys"
    good = _key(keys / "decision-signing-a.pem")
    assert se.signing_keyfile_warnings(str(good)) == []
    outside = _key(tmp_path / "op.key")
    assert "outside" in " ".join(se.signing_keyfile_warnings(str(outside)))
    legacy = _key(home / ".config" / "agentteams" / "decision-signing-l.pem")
    assert "--migrate" in " ".join(se.signing_keyfile_warnings(str(legacy)))
    wide = _key(keys / "decision-signing-w.pem", 0o644)
    assert "mode 644" in " ".join(se.signing_keyfile_warnings(str(wide)))
    link = keys / "link.pem"
    link.symlink_to(good)
    assert "symlink" in " ".join(se.signing_keyfile_warnings(str(link)))
    moved = _key(keys / "decision-signing-m.pem")
    stale = home / ".config" / "agentteams" / "decision-signing-m.pem"
    assert str(moved) in " ".join(se.signing_keyfile_warnings(str(stale)))


def test_sign_decision_prints_the_location_warning(tmp_path, monkeypatch, capsys):
    import build_team

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "build-log.json").write_text("{}\n")  # a team dir (F-2)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"action_reviewed": "grant-x"}))
    outside = _key(tmp_path / "op.key", 0o644)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(outside))
    build_team.main(["--sign-decision", str(spec), "--output", str(tmp_path)])
    err = capsys.readouterr().err
    assert "Warning:" in err and "outside ~/.config/agentteams/keys" in err and "mode 644" in err


def test_host_advisory_names_legacy_keys(home, capsys):
    from agentteams.cli.generate_helpers import _warn_legacy_signing_keys

    assert _warn_legacy_signing_keys({"privilege_profile": "confined"}) is False
    _key(home / ".config" / "agentteams" / "decision-signing-a.pem")
    assert _warn_legacy_signing_keys({"privilege_profile": "cooperative"}) is False
    assert _warn_legacy_signing_keys({"privilege_profile": "confined"}) is True
    assert "--migrate" in capsys.readouterr().err


def test_settings_example_documents_the_residuals():
    ex = _read_template_asset("hooks/settings.hooks.example.json")
    comment = " ".join(json.loads(se._inject_sandbox_block(ex, None))["_comment"])
    for needle in ("INHERITED", "UNSANDBOXED", "bypassPermissions", "INERT", "UNVERIFIED"):
        assert needle in comment


# --- the env-var HMAC residual stays a residual ----------------------------------------------

def test_relaxing_decision_refuses_a_valid_hmac_even_from_a_key_holder(tmp_path, monkeypatch):
    """An agent that inherits AGENTTEAMS_DECISION_SIGNING_KEY can forge a valid HMAC; the
    elevated path must still refuse it, under either scheme label."""
    from agentteams.cli import decision_log as dl

    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", "inherited-secret")
    refs = tmp_path / "references"
    (refs / "authorized-verify-keys").mkdir(parents=True)
    (refs / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": True}))
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-09-01T00:00:00Z,security,approved-cleanup,PASS,,verified\n")
    for scheme in ("hmac", "", "ed25519"):
        row = {
            "date": "2026-09-14", "action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
            "conditions_verified": "verified", "owner": "security", "prev_digest": "",
            "derives_from": "approved-cleanup", "effect_grants": "write:cross-repo",
            "sig_scheme": scheme, "key_id": "op-2026", "signature": "",
        }
        row["signature"] = dl.sign_decision_row(row)  # a genuinely valid HMAC
        with pytest.raises(RuntimeError):
            dl._assert_authorizing_row_is_authentic(
                row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write")


@pytest.mark.parametrize("scope", [
    "grant-cross-repo-write", "rewrite-agent-privilege.json", "add-authorized-verify-keys",
    "delete-old-reports", "signing-key-rotation",
])
def test_hmac_directive_cannot_authorize_a_relaxing_scope(tmp_path, scope):
    from agentteams.cli import management_directives as md

    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "authorized-managers.txt").write_text("team-manager\n")
    rec = {
        "directive_id": "d-1", "manager_team": "team-manager", "managed_team": "team-managed",
        "task_scope": scope, "expires_at": "2099-01-01T00:00:00Z", "max_uses": "5", "uses": "0",
        "approver": "alice", "prev_digest": "", "timestamp": "2026-09-01T00:00:00Z",
    }
    rec["signature"] = md.sign_directive(rec, key="holder-key")  # a key-holder's valid HMAC
    assert md.verify_directive_signature(rec, key="holder-key")
    ok, reason = md.validate_directive(rec, tmp_path, key="holder-key")
    assert not ok and "refused task_scope" in reason
