"""HMAC keys from key files (hmac-key-autoresolve): the @security ruling's five conditions, one test each.

Design and ruling: references/plans/hmac-key-autoresolve.plan.md (local). The variable wins; otherwise
``~/.config/agentteams/keys/<kind>-hmac.key`` is read under proposals.use_key_file's custody rules, and any
failure refuses.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import agentteams.cli.signed_ledger as sl
from agentteams.cli import decision_log, grants, management_directives, security_gate

KEY = "k" * 16 + "0123456789abcdef0123456789abcdef"   # 48 bytes, not one repeated character
WAIVER = "AGENTTEAMS_WAIVER_SIGNING_KEY"


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty key and fingerprint dirs the helper reads, the install-location check passing, no HMAC env keys."""
    d = tmp_path / "keys"
    d.mkdir(mode=0o700)
    monkeypatch.setattr(sl, "hmac_key_dir", lambda: d)
    monkeypatch.setattr(sl, "hmac_fingerprint_dir", lambda: tmp_path / "verify-keys")
    monkeypatch.setattr(sl, "_file_key_location_refusals", lambda roots: [])
    for name in sl.HMAC_KEY_FILES:
        monkeypatch.delenv(name, raising=False)
    return d


def _put(keys: Path, env: str, text: str = KEY, mode: int = 0o600, record: bool = True) -> Path:
    """Write a key file and, unless ``record`` is False, record its fingerprint as the operator would."""
    p = keys / sl.HMAC_KEY_FILES[env]
    p.write_text(text + "\n", encoding="utf-8")
    p.chmod(mode)
    if record and text and mode == 0o600:
        kind = sl.HMAC_KEY_FILES[env].split("-", 1)[0]
        sl.record_hmac_fingerprint(kind)
    return p


def test_no_env_no_file_is_unchanged(keys):
    assert sl.load_hmac_key(WAIVER) == ""


def test_the_file_is_read_when_the_env_is_unset(keys):
    _put(keys, WAIVER)
    assert sl.load_hmac_key(WAIVER) == KEY


def test_env_wins_and_both_present_warns_once_naming_the_variable(keys, monkeypatch, capsys):
    """Condition 2 + M4: a set variable still wins; the warning says whether the file differs, and to delete
    a file you did not create."""
    _put(keys, WAIVER, "f" * 8 + KEY)
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY
    assert sl.load_hmac_key(WAIVER) == KEY
    err = capsys.readouterr().err
    assert err.count(f"Warning: {WAIVER} is set and") == 1
    assert "a DIFFERENT key" in err and "If you did not create that file, delete it" in err


def test_both_present_and_equal_says_so(keys, monkeypatch, capsys):
    _put(keys, WAIVER)
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY
    assert "(the same key)" in capsys.readouterr().err


def test_env_alone_prints_nothing(keys, monkeypatch, capsys):
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_file_others_can_read_is_refused(keys, mode):
    _put(keys, WAIVER, mode=mode)
    with pytest.raises(RuntimeError, match="mode 0600"):
        sl.load_hmac_key(WAIVER)


def test_a_symlinked_key_file_is_refused(keys, tmp_path):
    real = tmp_path / "elsewhere.key"
    real.write_text(KEY, encoding="utf-8")
    real.chmod(0o600)
    (keys / sl.HMAC_KEY_FILES[WAIVER]).symlink_to(real)
    with pytest.raises(RuntimeError, match="cannot be read"):
        sl.load_hmac_key(WAIVER)


def test_a_symlinked_key_dir_is_refused(tmp_path, monkeypatch):
    """Condition 1 + M5: the file must sit in the key directory itself, not behind a link to somewhere else."""
    real = tmp_path / "agent-writable"
    real.mkdir(mode=0o700)
    link = tmp_path / "keys"
    link.symlink_to(real)
    monkeypatch.setattr(sl, "hmac_key_dir", lambda: link)
    monkeypatch.setattr(sl, "hmac_fingerprint_dir", lambda: tmp_path / "verify-keys")
    monkeypatch.setattr(sl, "_file_key_location_refusals", lambda roots: [])
    monkeypatch.delenv(WAIVER, raising=False)
    _put(link, WAIVER, record=False)
    with pytest.raises(RuntimeError, match="not behind a symlink|cannot be read"):
        sl.load_hmac_key(WAIVER)


def test_an_empty_file_is_refused(keys):
    _put(keys, WAIVER, text="", record=False)
    with pytest.raises(RuntimeError, match="is empty"):
        sl.load_hmac_key(WAIVER)


def test_a_file_key_is_refused_from_an_install_an_agent_could_edit(keys, monkeypatch):
    """Q3: refuse, not warn, as the Ed25519 signers do; the variable stays usable."""
    _put(keys, WAIVER)
    monkeypatch.setattr(sl, "_file_key_location_refusals",
                        lambda roots: ["the agentteams package (/x) is inside a git work tree"])
    with pytest.raises(RuntimeError, match="install an agent may be able to edit"):
        sl.load_hmac_key(WAIVER)
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY


def test_the_error_type_is_the_callers(keys):
    _put(keys, "AGENTTEAMS_GRANT_SIGNING_KEY", mode=0o644)
    with pytest.raises(grants.GrantError):
        grants._signing_key()


def test_grant_and_management_keys_come_from_their_files(keys):
    _put(keys, "AGENTTEAMS_GRANT_SIGNING_KEY")
    _put(keys, "AGENTTEAMS_MANAGEMENT_SIGNING_KEY", "m" * 8 + KEY)
    assert grants._signing_key() == KEY
    assert management_directives._signing_key() == "m" * 8 + KEY


def test_the_decision_key_file_signs_but_does_not_switch_enforcement_on(keys):
    """Condition 3: _decision_signing_active stays tied to the variable and the log header, never the file."""
    _put(keys, "AGENTTEAMS_DECISION_SIGNING_KEY")
    assert decision_log._decision_signing_active(["date", "action_reviewed", "verdict"]) is False
    row = {"date": "2026-11-14", "action_reviewed": "x", "verdict": "PASS", "conditions": "", "author": "security"}
    assert decision_log._hmac_decision_row(dict(row)) == decision_log._hmac_decision_row(dict(row), key=KEY)


def _waiver() -> dict[str, str]:
    return {"action_reviewed": "x", "conditions_verified": "verified", "approver": "alice", "ticket_id": "T-1",
            "reason_code": "r", "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "max_uses": "5", "uses": "0", "signature": "0" * 64}


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_waiver_with_an_unreadable_key_file_and_no_env_fails_closed(keys):
    """Condition 5: inside a session sandbox the key file is read-denied; a waiver check must refuse, not pass."""
    _put(keys, WAIVER, mode=0o000)
    with pytest.raises(RuntimeError, match="waiver|cannot be read|mode 0600"):
        security_gate._validate_security_waiver(_waiver(), action="x")


def test_a_waiver_with_no_key_anywhere_is_not_configured(keys):
    with pytest.raises(RuntimeError, match="not configured.*waiver-hmac.key"):
        security_gate._validate_security_waiver(_waiver(), action="x")


# --- @security implementation review (2026-11-14): M1 to M6 ---

def test_a_planted_key_file_without_a_recorded_fingerprint_is_refused(keys):
    """M2: a key file is trusted only against the operator's recorded fingerprint."""
    _put(keys, WAIVER, record=False)
    with pytest.raises(RuntimeError, match="no usable recorded fingerprint.*--record-hmac-fingerprint waiver"):
        sl.load_hmac_key(WAIVER)


def test_a_replaced_key_file_no_longer_matches_its_fingerprint(keys):
    p = _put(keys, WAIVER)
    p.write_text("r" * 8 + KEY, encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match the fingerprint"):
        sl.load_hmac_key(WAIVER)


def test_a_fingerprint_writable_by_others_is_refused(keys, tmp_path):
    _put(keys, WAIVER)
    (tmp_path / "verify-keys" / "waiver-hmac.fp").chmod(0o666)
    with pytest.raises(RuntimeError, match="no usable recorded fingerprint"):
        sl.load_hmac_key(WAIVER)


def test_the_fingerprint_reveals_nothing_and_is_stable():
    fp = sl.hmac_key_fingerprint(KEY)
    assert len(fp) == 64 and KEY not in fp and fp == sl.hmac_key_fingerprint(KEY)


def test_record_refuses_an_unknown_kind_and_a_missing_key(keys):
    with pytest.raises(ValueError):
        sl.record_hmac_fingerprint("nope")
    with pytest.raises(RuntimeError, match="cannot read"):
        sl.record_hmac_fingerprint("grant")


def test_record_is_refused_from_an_install_an_agent_could_edit(keys, monkeypatch):
    (keys / "waiver-hmac.key").write_text(KEY, encoding="utf-8")
    (keys / "waiver-hmac.key").chmod(0o600)
    monkeypatch.setattr(sl, "_file_key_location_refusals", lambda roots: ["inside a git work tree"])
    with pytest.raises(RuntimeError, match="refusing to record"):
        sl.record_hmac_fingerprint("waiver")


def test_the_real_install_location_check_refuses_this_checkout(monkeypatch):
    """M3: unstubbed, the check sees this test run's agentteams package inside a git work tree."""
    monkeypatch.setattr(sl, "_location_cache", {})
    refusals = sl._file_key_location_refusals(sl._project_roots(()))
    assert refusals and any("work tree" in r or "current directory" in r for r in refusals)


def test_project_roots_include_the_callers_project_and_key_the_cache(tmp_path, monkeypatch):
    """M3: a team dir's project (its .claude/settings.json) counts, and the cache is per set of roots."""
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    team = proj / ".claude" / "agents"
    team.mkdir()
    assert proj.resolve() in sl._project_roots((team,))
    seen: list[tuple] = []
    import agentteams.cli.signer_location as loc
    monkeypatch.setattr(sl, "_location_cache", {})
    monkeypatch.setattr(loc, "verify_install_location", lambda roots, cwd=None: seen.append(tuple(roots)) or [])
    sl._file_key_location_refusals([proj]); sl._file_key_location_refusals([proj]); sl._file_key_location_refusals([tmp_path])
    assert len(seen) == 2


def test_an_unreadable_cwd_is_the_callers_error(keys, monkeypatch):
    """M6: an OSError while checking the install is raised as the caller's error type."""
    _put(keys, "AGENTTEAMS_GRANT_SIGNING_KEY")
    def boom(dirs):
        raise FileNotFoundError("cwd gone")
    monkeypatch.setattr(sl, "_project_roots", boom)
    with pytest.raises(grants.GrantError, match="cannot check where agentteams is installed"):
        grants._signing_key()


@pytest.mark.parametrize("root", ["~", "~/.config", "~/.config/agentteams", "~/.config/agentteams/keys/x",
                                  "~/.config/agentteams/verify-keys"])
def test_write_roots_at_inside_or_above_the_key_dirs_are_refused(root):
    """M1: a write root above either operator directory is refused too (it used to be only at or inside)."""
    from agentteams.frameworks._sandbox_emit import assert_roots_clear_of_signing_keys

    with pytest.raises(ValueError, match="at, inside or above"):
        assert_roots_clear_of_signing_keys([root])


def test_ordinary_write_roots_still_pass():
    from agentteams.frameworks._sandbox_emit import assert_roots_clear_of_signing_keys

    assert_roots_clear_of_signing_keys([".", "~/githubrepositories/x", "/opt/work"])


def test_every_emitted_sandbox_write_denies_the_key_dirs():
    """M1: the rendered Claude block's denyWrite and goose Seatbelt profile both name the two operator dirs."""
    from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile
    from agentteams.frameworks._sandbox_emit import _build_sandbox_block, signing_key_deny_write

    want = signing_key_deny_write()
    assert want == ["~/.config/agentteams/keys", "~/.config/agentteams/verify-keys"]
    deny = _build_sandbox_block(None)["filesystem"]["denyWrite"]
    assert all(p in deny for p in want)
    absolute = _build_sandbox_block(None, resolve_abspath=True)["filesystem"]["denyWrite"]
    assert all(p in absolute for p in signing_key_deny_write(resolve_abspath=True))
    profile = _build_seatbelt_profile(None)
    text = profile if isinstance(profile, str) else "\n".join(profile)
    write_block = text[text.index("(deny file-write*\n", text.index("Operator signing-key isolation")):]
    write_block = write_block[:write_block.index(")\n", write_block.index("verify-keys"))]
    assert ".config/agentteams/keys" in write_block and ".config/agentteams/verify-keys" in write_block
    from agentteams.frameworks import _sandbox_emit as se
    confine = Path(se.__file__).parents[1] / "templates" / "universal" / "sandbox" / "confine-run.sh"
    assert '(deny file-write* (subpath \\"$HOME/.config/agentteams/verify-keys\\"))' in confine.read_text(encoding="utf-8")
