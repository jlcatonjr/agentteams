"""Rule 11(f): the operator-signed first-party toolchain group (agentteams v1.1.2; operator rulings R5, T1–T3,
no sunset, 2026-11-13).

The group file lifts Rule S-10's cooldown, and a pin move's per-release operator row, for releases of the
repositories it lists, in the workspaces it lists, with no per-repository row, no sunset and no cap slot. Everything
else fails closed: a tampered byte, another workspace, an unknown key, a missing Ed25519 backend, a HALT, a rolled-back
sequence. None of those fails the render; each prints a notice and an ``::error::`` line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import analyze, ingest, render
from agentteams.cli import exception_registry as er
from agentteams.cli import s10_exempt_gate as gate
from agentteams.cli import toolchain_group as tg
from agentteams.fences import _TEMPLATE_AUTHORITATIVE_FENCES, _extract_fenced_regions, _merge_fenced_content
from agentteams.frameworks.registry import FRAMEWORKS
from tests._sandbox_gate_helpers import _log, _row

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "agentteams" / "templates"
BRIEF = ROOT / "examples" / "research-project" / "brief.json"
_OWN = "acme/consumer"
_GROUP = ["acme/toolkit", "acme/builder", _OWN]
_TEAM = "consumer"
_POLICY = "first-party-release-policy"


@pytest.fixture(autouse=True)
def _decisions_verify(stub_ed25519):
    """Decision rows (the lineage parents) verify by the stub; the group file itself is really Ed25519-signed."""


def _keypair() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


def _project(tmp_path: Path, *, governed: bool = True, origin: str = f"https://github.com/{_OWN}.git",
             key_id: str = "op-2026") -> tuple[Path, Path, str]:
    """A git project with a canonical .github/agents team; returns (root, team dir, private PEM)."""
    root = tmp_path / "proj"
    team = root / ".github" / "agents"
    (team / "references" / "authorized-verify-keys").mkdir(parents=True)
    private, public = _keypair()
    (team / "references" / "authorized-verify-keys" / f"{key_id}.pub.pem").write_text(public)
    (team / "references" / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": governed}))
    (team / "references" / "build-log.json").write_text("{}")
    _log(team, _row(_POLICY))                     # the recorded decision the group file derives from
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin], check=True)
    return root, team, private


def _doc(private: str, *, group=None, workspaces=None, sequence: int = 1, key_id: str = "op-2026",
         derives_from: str = _POLICY) -> dict:
    from agentteams.cli import signed_ledger as sl

    doc = {"version": tg.FORMAT_VERSION, "scope": tg.SCOPE, "group": list(group or _GROUP),
           "workspaces": workspaces or [{"repo": f"repo:{_OWN}", "team": f"team:{_TEAM}"}],
           "purpose": tg.PURPOSE, "sequence": sequence, "key_id": key_id, "derives_from": derives_from}
    doc["signature"] = sl.ed25519_sign(private, tg.signed_values(doc))
    return doc


def _write(root: Path, doc: dict) -> Path:
    path = root / tg.GROUP_FILE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return path


def _authorize(team: Path, *, hint=None, consume: bool = True) -> list[str]:
    manifest = {"team_id": _TEAM, "s10_cooldown_exempt_repos": hint or []}
    gate.authorize_into(manifest, team, consume=consume)
    return manifest["s10_cooldown_exempt_authorized"]


# --- The happy path -----------------------------------------------------------------------------------------------

def test_a_valid_signed_group_renders_with_no_row_no_sunset_and_no_cap_slot(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]       # its own repository is left out
    registry = er.load_registry(team)
    assert registry["exceptions"] == [] and registry[tg.HIGH_WATER_KEY] == 1   # no exception, no cap slot
    er.assert_within_aggregate_cap(team, adding=1)                      # the default cap of 1 is still free
    assert "::error" not in capsys.readouterr().err


def test_the_rendered_rule_names_the_group(tmp_path) -> None:
    description = ingest.load(BRIEF, scan_project=False)
    manifest = analyze.build_manifest(description, framework="copilot-vscode")
    manifest["s10_cooldown_exempt_authorized"] = ["acme/toolkit", "acme/builder"]
    text = next(t for rel, t in render.render_all(manifest, templates_dir=TEMPLATES) if rel.endswith("security.agent.md"))
    assert "does not apply** to releases of these\n  repositories: `acme/toolkit`, `acme/builder`." in text


# --- Fail-closed cases: each renders an empty group, a notice and an ::error:: line, and never fails the render ---

@pytest.mark.parametrize("mutate", [
    lambda d: d.update(group=[*d["group"], "acme/intruder"]),                   # a widened group
    lambda d: d.update(sequence=d["sequence"] + 1),                             # any signed field
    lambda d: d.update(signature=("0" if d["signature"][0] != "0" else "1") + d["signature"][1:]),
])
def test_a_tampered_file_renders_no_group(tmp_path, capsys, mutate) -> None:
    root, team, private = _project(tmp_path)
    doc = _doc(private)
    mutate(doc)
    _write(root, doc)
    assert _authorize(team) == []
    err = capsys.readouterr().err
    assert "does not verify" in err and "::error title=S-10 toolchain group refused::" in err


def test_an_unlisted_workspace_renders_no_group_and_an_error_without_failing(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path, origin="git@github.com:acme/elsewhere.git")
    _write(root, _doc(private))                                         # a copy of another workspace's file
    assert _authorize(team) == []
    err = capsys.readouterr().err
    assert "repo:acme/elsewhere, team:consumer) is not listed" in err and "::error title=S-10 toolchain group" in err


def test_the_team_id_is_part_of_the_binding(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, workspaces=[{"repo": f"repo:{_OWN}", "team": "team:someone-else"}]))
    assert _authorize(team) == []
    assert "is not listed" in capsys.readouterr().err


def test_an_unknown_or_removed_key_renders_no_group(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, key_id="op-2099"))
    assert _authorize(team) == []
    assert "no Ed25519 verify key for key_id 'op-2099'" in capsys.readouterr().err


def test_a_missing_backend_is_an_environment_error(tmp_path, capsys, monkeypatch) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    monkeypatch.setattr(tg, "backend_available", lambda: False)
    assert _authorize(team) == []
    err = capsys.readouterr().err
    assert "::error title=S-10 toolchain group environment::" in err and "'signing' extra" in err


def test_a_brief_edit_alone_exempts_nothing_in_a_governed_workspace(tmp_path) -> None:
    _root, team, _private = _project(tmp_path)
    assert _authorize(team, hint=["acme/toolkit"]) == []


def test_the_brief_hint_beyond_the_signed_group_is_named(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    assert _authorize(team, hint=["acme/toolkit", "acme/extra"]) == ["acme/toolkit", "acme/builder"]
    assert "the brief also lists acme/extra" in capsys.readouterr().err


@pytest.mark.parametrize("surface", [".github/agents", ".claude/agents", ".codex/agents"])
def test_a_halt_on_the_family_in_any_surface_ends_the_group(tmp_path, capsys, surface) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    halt_dir = root / surface
    (halt_dir / "references").mkdir(parents=True, exist_ok=True)
    _log(halt_dir, _row(tg.FAMILY, verdict="HALT", stamp="2026-11-13"))
    assert _authorize(team) == []
    assert "HALT" in capsys.readouterr().err


def test_a_rolled_back_sequence_is_refused(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, sequence=2))
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]
    _write(root, _doc(private, sequence=1, group=[*_GROUP, "acme/old-member"]))
    assert _authorize(team) == []
    assert "rollback refused" in capsys.readouterr().err


def test_a_dry_run_records_no_sequence(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, sequence=3))
    assert _authorize(team, consume=False) == ["acme/toolkit", "acme/builder"]
    assert tg.HIGH_WATER_KEY not in er.load_registry(team)


def test_an_ungoverned_workspace_keeps_the_brief_alone_path(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path, governed=False)
    _write(root, _doc(private))
    assert _authorize(team, hint=["acme/toolkit"]) == ["acme/toolkit"]
    assert "not governed" in capsys.readouterr().err


# --- Migration: legacy per-repository rows ---------------------------------------------------------------------

def test_with_a_verified_group_no_new_per_repository_row_is_consumed(tmp_path, stub_ed25519) -> None:
    root, team, private = _project(tmp_path)
    log = _log(team, _row("s10-cooldown-exempt:acme/legacy"))
    _write(root, _doc(private))
    assert _authorize(team, hint=["acme/legacy"]) == ["acme/toolkit", "acme/builder"]
    assert "yes" not in log.read_text() and er.load_registry(team)["exceptions"] == []


def test_without_a_group_the_legacy_row_still_works(tmp_path, stub_ed25519) -> None:
    _root, team, _private = _project(tmp_path)
    _log(team, _row("s10-cooldown-exempt:acme/legacy"))
    assert _authorize(team, hint=["acme/legacy"]) == ["acme/legacy"]


# --- agentteams#476: a registered exemption that does not carry over says why ------------------------------------

def test_a_registered_exemption_that_cannot_be_verified_reports_the_backend(tmp_path, stub_ed25519, capsys,
                                                                             monkeypatch) -> None:
    from agentteams.cli import decision_log as dl

    _root, team, _private = _project(tmp_path)
    _log(team, _row("s10-cooldown-exempt:acme/legacy"))
    assert _authorize(team, hint=["acme/legacy"]) == ["acme/legacy"]           # registered, row consumed
    capsys.readouterr()

    def no_backend(*_a, **_k):
        raise RuntimeError("Ed25519 signing/verification requires the 'signing' extra (cryptography); it is not "
                           "installed")
    monkeypatch.setattr(dl, "verify_by_scheme", no_backend)
    assert _authorize(team, hint=["acme/legacy"]) == []
    err = capsys.readouterr().err
    assert "did not carry over" in err and "'signing' extra" in err
    assert "::error title=S-10 carve-out environment::acme/legacy" in err


# --- Signing, trust root, domain separation ----------------------------------------------------------------------

def test_the_operator_signs_the_file_in_place(tmp_path, monkeypatch) -> None:
    from agentteams.cli import operator_signing as ops

    root, team, private = _project(tmp_path)
    keyfile = tmp_path / "op.pem"
    keyfile.write_text(private)
    unsigned = {k: v for k, v in _doc(private).items() if k != "signature"}
    path = _write(root, unsigned)
    monkeypatch.setattr(ops, "_signing_preflight", lambda *_a, **_k: (str(keyfile), False))
    monkeypatch.setattr(ops, "_confirm", lambda *_a, **_k: True)
    assert tg.sign_group_file(path, team) == 0
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]


def test_the_group_file_is_a_trust_root() -> None:
    from agentteams.cli.governance_targets import target_is_governance_root

    assert target_is_governance_root(tg.GROUP_FILE_REL)
    assert target_is_governance_root(".agentteams/toolchain_group.json")


def test_a_group_payload_never_reads_as_a_decision_or_grant_payload() -> None:
    from agentteams.cli.grants import payload_claims_grant_purpose

    values = tg.signed_values({"purpose": tg.PURPOSE, "version": 2, "scope": tg.SCOPE, "sequence": 1,
                               "key_id": "op-2026", "group": ["a/b"], "derives_from": "p",
                               "workspaces": [{"repo": "repo:a/c", "team": "team:c"}]})
    assert values[0] == tg.PURPOSE and not payload_claims_grant_purpose(values)


@pytest.mark.parametrize("bad", [
    {"version": 1}, {"derives_from": ""}, {"derives_from": "Bad Id"}, {"scope": "everything"}, {"purpose": "agentteams-grant-v1"}, {"sequence": 0},
    {"group": ["bare"]}, {"group": []}, {"workspaces": [{"repo": "acme/x", "team": "team:x"}]},
    {"extra": "field"}, {"key_id": "../op"},
])
def test_a_malformed_file_is_refused(tmp_path, bad, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, {**_doc(private), **bad})
    assert _authorize(team) == []
    assert "::error" in capsys.readouterr().err


# --- Templates: generic, and Rule 11(f) reaches every constitution copy --------------------------------------------

def test_the_shipped_rule_names_no_specific_repository() -> None:
    """Rule 11: ship the machinery, never a specific exception (the group lives only in the signed file)."""
    import re

    security = (TEMPLATES / "universal" / "security.template.md").read_text(encoding="utf-8")
    bullet = security[security.index("**First-party toolchain group"): security.index("- ✅ Pin exact versions")]
    constitution = (TEMPLATES / "copilot-instructions.template.md").read_text(encoding="utf-8")
    rule = _extract_fenced_regions(constitution)["toolchain_group_rule"]
    for text in (bullet, rule):
        assert not re.search(r"`[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*`", text.replace("`.agentteams/toolchain-group.json`", ""))


@pytest.fixture(scope="module")
def instructions() -> tuple[dict, str]:
    manifest = analyze.build_manifest(ingest.load(BRIEF, scan_project=False), framework="copilot-vscode")
    text = (TEMPLATES / "copilot-instructions.template.md").read_text(encoding="utf-8")
    return manifest, render.resolve_placeholders(text, manifest["auto_resolved_placeholders"])


@pytest.mark.parametrize("framework", ["copilot-vscode", "claude", "agents-md"])
def test_rule_11f_is_fenced_and_restored_by_merge_while_a_local_clause_survives(instructions, framework) -> None:
    manifest, body = instructions
    fresh = body if framework == "copilot-vscode" else FRAMEWORKS[framework]().render_instructions_file(body, manifest)
    assert "toolchain_group_rule" in _TEMPLATE_AUTHORITATIVE_FENCES
    assert "## First-party toolchain group (a bounded constraint-relaxing authorization)" in _extract_fenced_regions(fresh)["toolchain_group_rule"]
    local = "    (e) **Carve-out: a local rollout** — project text, renewable term, counts against the cap.\n"
    if framework == "agents-md":  # AGENTS.md: the baseline list is fenced; a project extends it under its own heading
        from agentteams.frameworks._agents_md_rules import EXTENSIONS_HEADING

        eol = fresh.index("\n", fresh.index(EXTENSIONS_HEADING)) + 1
    else:
        eol = fresh.index("\n", fresh.index("11. **Constraint-relaxing authorizations")) + 1
    begin = fresh.index("<!-- AGENTTEAMS:BEGIN toolchain_group_rule")
    end = fresh.index("<!-- AGENTTEAMS:END toolchain_group_rule -->") + len("<!-- AGENTTEAMS:END toolchain_group_rule -->\n")
    # An existing team: generated before v1.1.2 (no 11(f) block) and carrying its own local Rule 11 clause.
    on_disk = fresh[:eol] + local + fresh[eol:begin] + fresh[end:]
    merged = _merge_fenced_content(fresh, on_disk, rel_path="copilot-instructions.md").merged_content
    assert local in merged                                                            # kept verbatim
    if framework != "agents-md":
        assert "## Project-Specific Rules" in merged and "## Project-Specific Rules" in on_disk
        own_rules = on_disk.replace("## Project-Specific Rules\n", "## Project-Specific Rules\n\n- Local rule: keep me.\n", 1)
        kept = _merge_fenced_content(fresh, own_rules, rel_path="copilot-instructions.md").merged_content
        assert "- Local rule: keep me." in kept and local in kept
    assert merged.count("## First-party toolchain group (a bounded constraint-relaxing authorization)") == 1                  # restored once
    tampered = merged.replace("it does not expire", "it may be extended by any agent")
    remerged = _merge_fenced_content(fresh, tampered, preserve_on_shrink=True).merged_content
    assert "it does not expire" in remerged and "any agent" not in remerged         # template wins


# --- Rule S-9 for the group: --verify-toolchain-pin ---------------------------------------------------------------

def _git(*args: str, cwd: Path | None = None) -> str:
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin:/usr/local/bin",
           "GIT_AUTHOR_NAME": "op", "GIT_AUTHOR_EMAIL": "op@example.invalid", "GIT_COMMITTER_NAME": "op",
           "GIT_COMMITTER_EMAIL": "op@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def signed_remote(tmp_path):
    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen not available")
    key = tmp_path / "release"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    signers = tmp_path / "allowed_signers"
    signers.write_text(f"op@example.invalid {key.with_suffix('.pub').read_text().strip()}\n")
    base = tmp_path / "remotes"
    work = base / "acme" / "toolkit"
    work.mkdir(parents=True)
    _git("init", "-q", cwd=work)
    (work / "f").write_text("x")
    _git("add", "f", cwd=work)
    _git("commit", "-qm", "c", cwd=work)
    sha = _git("rev-parse", "HEAD", cwd=work)
    sign = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={key}"]
    _git(*sign, "tag", "-s", "v1.0.0", "-m", "release", cwd=work)
    _git("tag", "-a", "v1.0.1", "-m", "unsigned", cwd=work)
    other = tmp_path / "not-release"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    _git("-c", "gpg.format=ssh", "-c", f"user.signingkey={other}", "tag", "-s", "v1.0.3", "-m", "other key", cwd=work)
    _git("tag", "v1.0.2", cwd=work)
    return base, signers, sha


@pytest.mark.parametrize(("lock", "ok", "why"), [
    ("tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n", True, ""),
    ("tk=acme/toolkit@{other}\ntk.tag=v1.0.0\n", False, "peels to"),
    ("tk=acme/toolkit@{sha}\ntk.tag=v1.0.1\n", False, "not SSH-signed"),
    ("tk=acme/toolkit@{sha}\ntk.tag=v1.0.2\n", False, "lightweight"),
    ("tk=acme/toolkit@{sha}\ntk.tag=v1.0.3\n", False, "does not verify"),
    ("tk=acme/toolkit@{sha}\n", False, "no tk.tag line"),
    ("other=acme/not-in-group@{other}\n", True, ""),
    ("tk=acme/toolkit@{sha}\ntk.tag=v1.0.0-rc.1\n", False, "not a final release tag"),
])
def test_a_pin_to_a_group_member_needs_a_release_key_tag_on_the_locked_commit(tmp_path, signed_remote,
                                                                             lock, ok, why) -> None:
    base, signers, sha = signed_remote
    lockfile = tmp_path / "toolchain.lock"
    lockfile.write_text(lock.format(sha=sha, other="0" * 40))
    problems = tg.verify_pins(lockfile, ["acme/toolkit"], signers, remote_base=str(base))
    assert (problems == []) is ok, problems
    if why:
        assert why in problems[0]


# --- @security re-review (2026-11-13) ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["s10-cooldown-exempt:acme/toolkit", "s10-cooldown-exempt"])
def test_a_halt_on_the_per_repository_exemption_binds_the_group(tmp_path, stub_ed25519, capsys, action) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _log(team, _row(action, verdict="HALT", stamp="2026-11-13"))
    # The HALT matcher over-matches on purpose (action_ids: "on the HALT side a false match only blocks"), so a HALT
    # on one member's exemption also matches the family check and drops every member, as the legacy gate does.
    assert _authorize(team) == []
    assert "::error title=S-10 toolchain group HALT::acme/toolkit" in capsys.readouterr().err


@pytest.mark.parametrize(("url", "repo"), [
    ("https://github.com/acme/consumer.git", "acme/consumer"),
    ("git@github.com:acme/consumer.git", "acme/consumer"),
    ("ssh://git@github.com/acme/consumer", "acme/consumer"),
    ("https://evil.example/x/github.com/acme/consumer", None),
    ("https://github.com.evil.example/acme/consumer", None),
])
def test_the_origin_must_be_github_itself(tmp_path, url, repo) -> None:
    root = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", url], check=True)
    assert tg.workspace_repo(root) == repo


def test_a_signed_tag_re_pushed_under_another_name_is_refused(tmp_path, signed_remote) -> None:
    base, signers, sha = signed_remote
    work = base / "acme" / "toolkit"
    obj = _git("rev-parse", "refs/tags/v1.0.0", cwd=work)
    _git("update-ref", "refs/tags/v9.9.9", obj, cwd=work)       # the v1.0.0 tag object under a new name
    lockfile = tmp_path / "toolchain.lock"
    lockfile.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v9.9.9\n")
    [problem] = tg.verify_pins(lockfile, ["acme/toolkit"], signers, remote_base=str(base))
    assert "names a different tag" in problem


def test_pins_checked_are_counted(tmp_path, signed_remote) -> None:
    base, signers, sha = signed_remote
    lockfile = tmp_path / "toolchain.lock"
    lockfile.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\nx=acme/other@{'0' * 40}\n")
    checked: list[str] = []
    assert tg.verify_pins(lockfile, ["acme/toolkit"], signers, remote_base=str(base), checked=checked) == []
    assert checked == ["tk"]


def test_the_verify_verb_applies_the_member_halt_filter(tmp_path, stub_ed25519, capsys) -> None:
    import argparse

    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    args = argparse.Namespace(verify_toolchain_group=True, sign_toolchain_group=None, verify_toolchain_pin=None,
                              output=str(team), project=None, team_id=_TEAM, description=None)
    assert run_toolchain_group(args) == 0
    assert "acme/toolkit, acme/builder" in capsys.readouterr().out
    _log(team, _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-13"))
    run_toolchain_group(args)
    out = capsys.readouterr()
    assert "acme/toolkit" not in out.out and "::error title=S-10 toolchain group HALT::acme/toolkit" in out.err


def test_a_halt_in_a_symlinked_surface_binds(tmp_path, stub_ed25519, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    real = tmp_path / "elsewhere"
    (real / "references").mkdir(parents=True)
    _log(real, _row(tg.FAMILY, verdict="HALT", stamp="2026-11-13"))
    (root / ".claude").mkdir()
    (root / ".claude" / "agents").symlink_to(real, target_is_directory=True)
    assert _authorize(team) == []


# --- v1.1.3: signed derives_from lineage (operator ruling, 2026-11-13) --------------------------------------------

def test_the_lineage_is_signed(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    doc = _doc(private)
    doc["derives_from"] = "approved-baseline"          # another recorded decision, but not the one signed
    _write(root, doc)
    assert _authorize(team) == []
    assert "does not verify" in capsys.readouterr().err


@pytest.mark.parametrize(("derives", "why"), [
    ("never-recorded", "which no authorizing decision records"),
    (f"{_POLICY};never-recorded", "which no authorizing decision records"),
])
def test_an_unrecorded_parent_renders_no_group(tmp_path, capsys, derives, why) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, derives_from=derives))
    assert _authorize(team) == []
    err = capsys.readouterr().err
    assert why in err and "::error title=S-10 toolchain group" in err


@pytest.mark.parametrize("halted", [_POLICY, "approved-baseline"])     # the parent, or its ancestor
def test_a_halt_up_the_chain_ends_the_group(tmp_path, capsys, halted) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]
    _log(team, _row(halted, verdict="HALT", stamp="2026-11-13"))
    assert _authorize(team) == []
    assert "lineage does not hold" in capsys.readouterr().err


def test_a_halt_up_the_chain_in_a_native_surface_binds(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    native = root / ".claude" / "agents"
    (native / "references").mkdir(parents=True)
    _log(native, _row(_POLICY, verdict="HALT", stamp="2026-11-13"))
    assert _authorize(team) == []


def test_a_parent_whose_row_does_not_verify_renders_no_group(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _log(team, _row("forged-policy", sign=lambda _v: "stub" + "0" * 64))
    _write(root, _doc(private, derives_from="forged-policy"))
    assert _authorize(team) == []
    assert "lineage does not hold" in capsys.readouterr().err


# --- IM @security delta (SEC-IM-PR481) ------------------------------------------------------------------------

def test_load_group_returns_the_signed_lowercased_ids(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, group=["Acme/ToolKit", _OWN]))
    assert _authorize(team) == ["acme/toolkit"]


def test_a_gpg_signed_tag_is_refused(tmp_path, signed_remote) -> None:
    base, signers, sha = signed_remote
    work = base / "acme" / "toolkit"
    fake = (f"object {sha}\ntype commit\ntag v2.0.0\ntagger op <op@example.invalid> 1794600000 +0000\n\nrelease\n"
            "-----BEGIN PGP SIGNATURE-----\n\niQEz\n-----END PGP SIGNATURE-----\n")
    obj = subprocess.run(["git", "mktag"], cwd=work, input=fake, capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin:/usr/local/bin", "GIT_CONFIG_GLOBAL": "/dev/null"}).stdout.strip()
    _git("update-ref", "refs/tags/v2.0.0", obj, cwd=work)
    lockfile = tmp_path / "toolchain.lock"
    lockfile.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v2.0.0\n")
    [problem] = tg.verify_pins(lockfile, ["acme/toolkit"], signers, remote_base=str(base))
    assert "not SSH-signed" in problem


@pytest.mark.parametrize("tag", ["1.0.0", "v1.0.0-rc.1", "v\u0661.0.0"])
def test_only_a_v_prefixed_ascii_final_tag_is_accepted(tmp_path, signed_remote, tag) -> None:
    base, signers, sha = signed_remote
    lockfile = tmp_path / "toolchain.lock"
    lockfile.write_text(f"tk=acme/toolkit@{sha}\ntk.tag={tag}\n", encoding="utf-8")
    problems = tg.verify_pins(lockfile, ["acme/toolkit"], signers, remote_base=str(base))
    assert problems and ("not a final release tag" in problems[0] or "no tk.tag line" in problems[0])


# --- @security v1.1.3 delta conditions ------------------------------------------------------------------------

def test_a_halt_on_an_ancestor_only_in_a_surface_log_binds(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    native = root / ".claude" / "agents"
    (native / "references").mkdir(parents=True)
    _log(native, _row("approved-baseline", verdict="HALT", stamp="2026-11-13"))   # the parent's ancestor
    assert _authorize(team) == []
    assert "lineage does not hold" in capsys.readouterr().err


def test_an_hmac_parent_row_is_refused(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _log(team, _row("hmac-policy", relaxes="", derives="", scheme=""))
    _write(root, _doc(private, derives_from="hmac-policy"))
    assert _authorize(team) == []
    assert "not Ed25519-signed by the operator" in capsys.readouterr().err


def test_a_non_relaxing_ed25519_root_parent_is_accepted(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _log(team, _row("s10-first-party-releases-2026-11", relaxes="", derives=""))
    _write(root, _doc(private, derives_from="s10-first-party-releases-2026-11"))
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]


def test_every_listed_parent_must_resolve(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, derives_from=f"{_POLICY};release-policy-1.0"))
    assert _authorize(team) == []


@pytest.mark.parametrize("parent", ["toolchain-group-policy", "s10-toolchain_group-x", "verify-key-rotation"])
def test_a_trust_root_parent_id_is_refused(tmp_path, capsys, parent) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private, derives_from=parent))
    assert _authorize(team) == []
    assert "trust-root id" in capsys.readouterr().err


# --- IM @security #485 delta -----------------------------------------------------------------------------------

def test_an_ssh_block_followed_by_a_pgp_block_is_refused() -> None:
    ssh = "-----BEGIN SSH SIGNATURE-----\nU1NIU0lH\n-----END SSH SIGNATURE-----\n"
    pgp = "-----BEGIN PGP SIGNATURE-----\niQEz\n-----END PGP SIGNATURE-----\n"
    head = "object 0\ntype commit\ntag v1.0.0\ntagger op <op@x> 0 +0000\n\nrelease\n"
    assert tg._ssh_signed(head + ssh)
    assert not tg._ssh_signed(head + ssh + pgp)
    assert not tg._ssh_signed(head + pgp + ssh)
    assert not tg._ssh_signed(head + ssh + ssh)
    assert not tg._ssh_signed(head + ssh + "trailing text\n")


def test_a_lineage_cycle_renders_no_group(tmp_path, capsys) -> None:
    root, team, private = _project(tmp_path)
    _log(team, _row("cycle-a", relaxes="", derives="cycle-b"), _row("cycle-b", relaxes="", derives="cycle-a"))
    _write(root, _doc(private, derives_from="cycle-a"))
    assert _authorize(team) == []
    assert "lineage does not hold" in capsys.readouterr().err


@pytest.mark.parametrize(("verdict", "conditions"), [("CONDITIONAL PASS", "pending"), ("FAIL", "")])
def test_a_later_pending_or_refusing_decision_blocks_the_parent(tmp_path, capsys, verdict, conditions) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]
    later = _row(_POLICY, verdict=verdict, stamp="2026-11-14")
    later["conditions_verified"] = conditions
    _log(team, later)
    assert _authorize(team) == []
    assert "not an authorizing one" in capsys.readouterr().err


def test_a_retracted_halt_does_not_disable_the_parent(tmp_path) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _log(team, _row(_POLICY, verdict="HALT", stamp="2026-11-13"))
    assert _authorize(team) == []
    retract = _row(_POLICY, verdict="HALT-RETRACTED", stamp="2026-11-14")
    retract["conditions"] = "settled"
    retract["signature"] = __import__("tests._sandbox_gate_helpers", fromlist=["_stub_sig"])._stub_sig(
        __import__("agentteams.cli.decision_log", fromlist=["x"])._decision_signature_values(retract))
    _log(team, retract)
    assert _authorize(team) == ["acme/toolkit", "acme/builder"]
