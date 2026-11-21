"""The operator-signed first-party toolchain group (agentteams v1.1.2; operator rulings R5, T1–T3,
no sunset, 2026-11-13).

The group file lifts Rule S-10's cooldown, and a pin move's per-release operator row, for releases of the
repositories it lists, in the workspaces it lists, with no per-repository row, no sunset and no cap slot. Everything
else fails closed: a tampered byte, another workspace, an unknown key, a missing Ed25519 backend, a HALT, a rolled-back
sequence. None of those fails the render; each prints a notice and an ``::error::`` line.
"""

from __future__ import annotations

import contextlib
import json
import os
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


# --- Templates: generic, and the toolchain-group section reaches every constitution copy --------------------------------------------

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
    try:  # a malformed tag line is refused outright since the #598 review; a well-formed non-final one is a problem
        problems = tg.verify_pins(lockfile, ["acme/toolkit"], signers, remote_base=str(base))
    except tg.ToolchainLockError:
        return
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


# --- researchteam-f8: no group file -> exit 2 before any team-id lookup; the brief is found like analyze finds it ---

def _verb_args(team, **kw):
    import argparse

    base = dict(verify_toolchain_group=False, sign_toolchain_group=None, verify_toolchain_pin=None, output=str(team),
                project=None, team_id=None, description=None, allowed_signers="x")
    return argparse.Namespace(**{**base, **kw})


def test_no_group_file_falls_back_and_never_exits_2(tmp_path, capsys) -> None:
    """#555: with no group file the check still runs; a lockfile pinning nothing first-party fails closed (1)."""
    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    _root, team, _private = _project(tmp_path)
    lock = tmp_path / "lock"
    lock.write_text(f"x=other/thing@{'0' * 40}\n")
    assert run_toolchain_group(_verb_args(team, verify_toolchain_pin=str(lock))) == 1
    assert "nothing was verified" in capsys.readouterr().err


def test_the_team_id_comes_from_the_projects_own_brief(tmp_path, capsys) -> None:
    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    (root / "brief.json").write_text(json.dumps({"project_name": "Consumer", "team_id": _TEAM}))
    assert run_toolchain_group(_verb_args(team, verify_toolchain_group=True)) == 0
    assert "acme/toolkit" in capsys.readouterr().out


# --- conflict audit (2026-11-14): the verify verb agrees with a render ----------------------------------------

def test_the_verify_verb_refuses_an_ungoverned_workspace(tmp_path, capsys) -> None:
    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    root, team, private = _project(tmp_path, governed=False)
    _write(root, _doc(private))
    assert run_toolchain_group(_verb_args(team, verify_toolchain_group=True, team_id=_TEAM)) == 1
    assert "not governed" in capsys.readouterr().err


def test_the_verify_verb_exits_1_when_every_member_is_halted(tmp_path, capsys) -> None:
    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    _log(team, _row("s10-cooldown-exempt:acme/toolkit", verdict="HALT", stamp="2026-11-14"))
    assert run_toolchain_group(_verb_args(team, verify_toolchain_group=True, team_id=_TEAM)) == 1
    assert "every group member is HALTed" in capsys.readouterr().err


# --- #555: Rule S-9's signed-tag check runs in every tier (@security ruling, 2026-11-20) --------------------------

@pytest.fixture
def t3(tmp_path, signed_remote, monkeypatch):
    """An ungoverned (T3) workspace owned by acme, with --verify-toolchain-pin reading tags from the local remotes."""
    import functools

    base, signers, sha = signed_remote
    root, team, _private = _project(tmp_path, governed=False)
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    return root, team, signers, sha


def _pin_args(team, lock, signers, **kw):
    return _verb_args(team, verify_toolchain_pin=str(lock), allowed_signers=str(signers), **kw)


def _run_pin(args) -> int:
    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    return run_toolchain_group(args)


def test_t3_a_verified_same_owner_pin_passes(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 0
    assert "never clears the cooldown" in capsys.readouterr().out


@pytest.mark.parametrize(("tag", "why"), [("v1.0.1", "not SSH-signed"), ("v1.0.3", "does not verify"),
                                          ("v1.0.0-rc.1", "not a final release tag"), (None, "no tk.tag line")])
def test_t3_a_bad_tag_fails(t3, tmp_path, capsys, tag, why) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\n" + (f"tk.tag={tag}\n" if tag else ""))
    assert _run_pin(_pin_args(team, lock, signers)) == 1
    assert why in capsys.readouterr().err


def test_t3_no_first_party_pin_fails_closed(t3, tmp_path) -> None:
    root, team, signers, _sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"x=elsewhere/thing@{'0' * 40}\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 1


def test_t3_a_brief_that_omits_a_same_owner_pin_still_checks_it(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    (root / "brief.json").write_text(json.dumps({"project_name": "Consumer", "s10_cooldown_exempt_repos": []}))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.1\n")     # unsigned: must still be checked and fail
    assert _run_pin(_pin_args(team, lock, signers)) == 1


def test_t3_the_brief_adds_another_owners_repo(t3, tmp_path) -> None:
    root, team, signers, _sha = t3
    (root / "brief.json").write_text(json.dumps({"project_name": "Consumer",
                                                 "s10_cooldown_exempt_repos": ["other/lib"]}))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"lib=other/lib@{'1' * 40}\nlib.tag=v1.0.0\n")   # other/lib is not in the remotes: fails
    assert _run_pin(_pin_args(team, lock, signers)) == 1


def test_t3_pin_repos_not_locked_fails(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers, pin_repos="acme/toolkit,acme/missing")) == 1
    assert "does not pin" in capsys.readouterr().err


def test_t3_the_workspaces_own_repo_is_excluded_and_named(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"self=acme/consumer@{'2' * 40}\ntk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 0
    assert "self=acme/consumer: this workspace's own repository, not checked" in capsys.readouterr().out


def test_t3_no_origin_excludes_nothing(tmp_path, signed_remote, monkeypatch) -> None:
    import functools

    base, signers, sha = signed_remote
    root, team, _private = _project(tmp_path, governed=False, origin="https://example.invalid/x/y.git")
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    # no GitHub origin: no owner match, nothing excluded; an explicit --pin-repos is still checked
    assert _run_pin(_pin_args(team, lock, signers)) == 1
    assert _run_pin(_pin_args(team, lock, signers, pin_repos="acme/toolkit")) == 0


def test_a_halted_exemption_warns_but_the_pin_is_still_checked(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    _log(team, _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-20"))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 0
    err = capsys.readouterr().err
    assert "::warning title=S-10 cooldown exemption revoked::acme/toolkit" in err and "does not clear it" in err


def test_governed_without_a_group_file_falls_back(tmp_path, signed_remote, monkeypatch) -> None:
    import functools

    base, signers, sha = signed_remote
    root, team, _private = _project(tmp_path)          # governed, no group file
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 0


def test_the_verb_has_no_exit_2_path() -> None:
    src = (ROOT / "agentteams" / "cli" / "toolchain_group_commands.py").read_text()
    body = src[src.index("def _verify_pin_command"):]
    assert "return 2" not in body


# --- @security / @code-hygiene review of 7bbd495a -----------------------------------------------------------------

def test_an_unreadable_named_brief_fails_closed(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers, description=str(tmp_path / "missing.json"))) == 1
    assert "cannot be read" in capsys.readouterr().err


def test_a_discovered_brief_that_is_not_json_fails_closed(t3, tmp_path) -> None:
    root, team, signers, sha = t3
    (root / "brief.json").write_text("{not json")
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 1


@pytest.mark.parametrize("hint", ["acme/toolkit", ["acme/toolkit", 3], [""]])
def test_a_malformed_exempt_list_fails_closed(t3, tmp_path, capsys, hint) -> None:
    root, team, signers, sha = t3
    (root / "brief.json").write_text(json.dumps({"project_name": "Consumer", "s10_cooldown_exempt_repos": hint}))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 1
    assert "must be a list" in capsys.readouterr().err


def test_a_mixed_case_github_origin_still_matches_the_owner(tmp_path, signed_remote, monkeypatch) -> None:
    import functools

    base, signers, sha = signed_remote
    root, team, _private = _project(tmp_path, governed=False, origin="https://GitHub.com/Acme/Consumer.git")
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.1\n")    # unsigned: must be checked and fail
    assert tg.workspace_repo(root) == "Acme/Consumer"
    assert _run_pin(_pin_args(team, lock, signers)) == 1


def test_a_governed_workspace_with_a_broken_group_file_refuses(tmp_path, signed_remote, monkeypatch) -> None:
    import functools

    base, signers, sha = signed_remote
    root, team, _private = _project(tmp_path)
    (root / tg.GROUP_FILE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / tg.GROUP_FILE_REL).write_text("{broken")
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers, team_id=_TEAM)) == 1


def test_the_team_id_lookup_ignores_a_brief_naming_another_project(tmp_path) -> None:
    """@security re-review of 32a95d09: _team_id keeps resolve_brief's 2026-11-02 guard."""
    from agentteams.cli.toolchain_group_commands import _team_id

    root, team, _private = _project(tmp_path)
    (root / "brief.json").write_text(json.dumps({"team_id": "spoofed", "existing_project_path": "../elsewhere"}))
    assert _team_id(_verb_args(team), root) is None


# --- IM-da review of #593 (the #588 class): a dir that cannot be stat'd refuses; real chmod (3.14 never raises) ---

_needs_real_chmod = pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="chmod does not bind root")


@contextlib.contextmanager
def _locked(*paths: Path):
    """chmod 000 each path for the block, then restore it so tmp_path can be removed."""
    modes = [(p, p.stat().st_mode) for p in paths]
    try:
        for p, _mode in modes:
            p.chmod(0)
        yield
    finally:
        for p, mode in reversed(modes):
            p.chmod(mode)


def _surface_case(tmp_path: Path, root: Path, case: str) -> tuple[Path, ...]:
    """Set up .claude/agents for *case* (a HALT on the group family inside, where it can hold one); return the
    paths to lock."""
    claude = root / ".claude"
    claude.mkdir()
    if case in ("parent_000", "surface_000"):
        surface = claude / "agents"
        (surface / "references").mkdir(parents=True)
        _log(surface, _row(tg.FAMILY, verdict="HALT", stamp="2026-11-21"))
        return (claude,) if case == "parent_000" else (surface,)
    if case == "dangling":
        (claude / "agents").symlink_to(tmp_path / "gone", target_is_directory=True)
        return ()
    locked = tmp_path / "locked"
    (locked / "agents" / "references").mkdir(parents=True)
    _log(locked / "agents", _row(tg.FAMILY, verdict="HALT", stamp="2026-11-21"))
    (claude / "agents").symlink_to(locked / "agents", target_is_directory=True)
    return (locked,)


_SURFACE_CASES = ["parent_000", "surface_000", "dangling", "link_to_unstatable"]


@_needs_real_chmod
@pytest.mark.parametrize("case", _SURFACE_CASES)
def test_an_unstatable_surface_renders_no_group(tmp_path, capsys, case) -> None:
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    with _locked(*_surface_case(tmp_path, root, case)):
        assert _authorize(team) == []
    assert "fail-closed" in capsys.readouterr().err


@_needs_real_chmod
@pytest.mark.parametrize("case", _SURFACE_CASES)
def test_an_unstatable_surface_fails_the_pin_check(t3, tmp_path, capsys, case) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    with _locked(*_surface_case(tmp_path, root, case)):
        assert _run_pin(_pin_args(team, lock, signers)) == 1
    assert "fail-closed" in capsys.readouterr().err


@_needs_real_chmod
@pytest.mark.parametrize("lock_rel", [".github", ".github/agents"])
def test_an_unstatable_canonical_dir_never_falls_back_to_the_brief(tmp_path, capsys, lock_rel) -> None:
    """Before: is_dir()/exists() read False, governance fell back to the run's ungoverned .claude/agents, and the
    brief alone exempted acme/toolkit."""
    root, _team, _private = _project(tmp_path)
    native = root / ".claude" / "agents"
    native.mkdir(parents=True)
    with _locked(root / lock_rel):
        assert _authorize(native, hint=["acme/toolkit"]) == []


@_needs_real_chmod
def test_an_unstatable_group_file_fails_the_pin_check(tmp_path, signed_remote, monkeypatch, capsys) -> None:
    """Before: exists() read False, the group was skipped, and its members' pins went unchecked."""
    import functools

    base, signers, sha = signed_remote
    root, team, private = _project(tmp_path)
    _write(root, _doc(private))
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    with _locked((root / tg.GROUP_FILE_REL).parent):
        assert _run_pin(_pin_args(team, lock, signers, team_id=_TEAM)) == 1
        assert _run_verify_group(team) == 1
    assert "cannot be read" in capsys.readouterr().err


def _run_verify_group(team: Path) -> int:
    from agentteams.cli.toolchain_group_commands import run_toolchain_group

    return run_toolchain_group(_verb_args(team, verify_toolchain_group=True, team_id=_TEAM))


# --- IM-da review of #598 (part A) ---------------------------------------------------------------------------------

@pytest.mark.parametrize("named_by", ["brief", "pin_repos"])
def test_an_origin_claiming_a_named_repo_never_excludes_its_pin(tmp_path, signed_remote, monkeypatch, named_by) -> None:
    """A consumer whose (unsigned) origin claims to be acme/toolkit must still verify a toolkit pin it names."""
    import functools

    base, signers, sha = signed_remote
    root, team, _private = _project(tmp_path, governed=False, origin="https://github.com/acme/toolkit.git")
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    kw = {}
    if named_by == "brief":
        (root / "brief.json").write_text(json.dumps({"project_name": "Consumer",
                                                     "s10_cooldown_exempt_repos": ["acme/toolkit"]}))
    else:
        kw["pin_repos"] = "acme/toolkit"
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.1\n")      # unsigned: must be checked and fail
    assert _run_pin(_pin_args(team, lock, signers, **kw)) == 1
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers, **kw)) == 0


@pytest.mark.parametrize("text", [
    "tk=acme/toolkit@{bad}\ntk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n",          # an unreleased pin first: bootstrap installs it
    "tk=acme/toolkit@{sha}\ntk.tag=v1.0.1\ntk.tag=v1.0.0\n",                  # a duplicated tag
    "tk=acme/toolkit@{upper}\ntk.tag=v1.0.0\n",                               # uppercase hex
    "tk = acme/toolkit@{sha}\ntk.tag=v1.0.0\n",                               # spaces around '='
    "tk=acme/toolkit@{sha}\ntk.tag = v1.0.0\n",
])
def test_a_duplicated_or_malformed_lock_line_fails_closed(t3, tmp_path, capsys, text) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(text.format(sha=sha, bad="1" * 40, upper=sha.upper()))
    assert _run_pin(_pin_args(team, lock, signers)) == 1
    err = capsys.readouterr().err   # not a bare "malformed": the tmp path carries this test's name
    assert "twice; one line per name" in err or "looks like a pin or tag but is malformed" in err


def test_comments_and_unrelated_lock_lines_are_still_ignored(t3, tmp_path) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"# pins\npython=3.11\n\ntk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 0


# --- @security delta review of 3992fcf0 -------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "tk\t=acme/toolkit@{sha}\ntk.tag=v1.0.0\n",                              # a tab before '='
    "tk=acme/toolkit@{sha} # pinned for 1.3\ntk.tag=v1.0.0\n",                # a trailing comment
])
def test_a_tab_or_trailing_comment_is_refused(t3, tmp_path, capsys, text) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_text(text.format(sha=sha))
    assert _run_pin(_pin_args(team, lock, signers)) == 1
    assert "looks like a pin or tag but is malformed" in capsys.readouterr().err


def test_a_crlf_lockfile_still_verifies(t3, tmp_path) -> None:
    root, team, signers, sha = t3
    lock = tmp_path / "toolchain.lock"
    lock.write_bytes(f"tk=acme/toolkit@{sha}\r\ntk.tag=v1.0.0\r\n".encode())
    assert _run_pin(_pin_args(team, lock, signers)) == 0


def test_an_origin_claiming_a_group_member_fails_the_signed_workspace_match(tmp_path, signed_remote, monkeypatch,
                                                                          capsys) -> None:
    """The group excludes the workspace's own repo only after the signed workspace match (toolchain_group.load_group):
    a consumer signed as acme/consumer whose origin claims acme/toolkit gets no group and fails closed."""
    import functools

    base, signers, sha = signed_remote
    root, team, private = _project(tmp_path, origin="https://github.com/acme/toolkit.git")
    _write(root, _doc(private))                                          # signed for repo:acme/consumer only
    monkeypatch.setattr(tg, "verify_pins", functools.partial(tg.verify_pins, remote_base=str(base)))
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.1\n")
    assert _run_pin(_pin_args(team, lock, signers, team_id=_TEAM)) == 1
    assert "is not listed in the signed" in capsys.readouterr().err


def test_a_canonical_dir_that_vanishes_between_stats_refuses(tmp_path, monkeypatch) -> None:
    root, _team, _private = _project(tmp_path)
    native = root / ".claude" / "agents"
    native.mkdir(parents=True)
    real = gate.lstat_or_none
    monkeypatch.setattr(gate, "lstat_or_none",
                        lambda p, what: None if Path(p).name == ".github" else real(p, what))  # the race, simulated
    with pytest.raises(RuntimeError, match="vanished"):
        gate.governance_team_dir(native)


# --- @security review of #599 (76433d7e): an unusable discovered brief stops the pin check ------------------------

@pytest.mark.parametrize("case", ["dangling", "symlink", "unstatable"])
def test_an_unusable_agentteams_brief_beside_a_root_brief_fails_the_pin_check(t3, tmp_path, capsys, case) -> None:
    root, team, signers, sha = t3
    if case == "unstatable" and (os.name != "posix" or os.geteuid() == 0):
        pytest.skip("chmod does not bind root")
    (root / "brief.json").write_text(json.dumps({"project_name": "Consumer", "s10_cooldown_exempt_repos": []}))
    (root / ".agentteams").mkdir(exist_ok=True)
    brief = root / ".agentteams" / "brief.json"
    if case == "dangling":
        brief.symlink_to(tmp_path / "gone.json")
    elif case == "symlink":
        (tmp_path / "real.json").write_text(json.dumps({"s10_cooldown_exempt_repos": []}))
        brief.symlink_to(tmp_path / "real.json")
    else:
        brief.write_text("{}")
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")      # verifies: only the brief can fail the run
    with (_locked(root / ".agentteams") if case == "unstatable" else contextlib.nullcontext()):
        assert _run_pin(_pin_args(team, lock, signers)) == 1
    err = capsys.readouterr().err
    assert ("is not this project's brief" in err) if case != "unstatable" else ("cannot be read" in err)


def test_branch_model_refuses_and_multi_sync_warns_on_an_unusable_brief(tmp_path, capsys) -> None:
    """branch_model raises (reading trunk would drop the integration branch's protection); multi_sync warns."""
    from agentteams import branch_model, multi_sync

    (tmp_path / "brief.json").write_text(json.dumps({"project_name": "x", "privilege_profile": "confined"}))
    (tmp_path / ".agentteams").mkdir()
    (tmp_path / ".agentteams" / "brief.json").symlink_to(tmp_path / "gone.json")
    with pytest.raises(ValueError, match="is not this project's brief"):
        branch_model.integration_branch_from_project(tmp_path)
    assert multi_sync._read_brief_privilege(tmp_path) == {}
    assert "is not this project's brief" in capsys.readouterr().err


@pytest.mark.parametrize("content", ["{broken", "[]"])
def test_branch_model_refuses_an_unparseable_brief(tmp_path, content) -> None:
    from agentteams import branch_model

    (tmp_path / "brief.json").write_text(content)
    with pytest.raises(ValueError, match="brief"):
        branch_model.integration_branch_from_project(tmp_path)
    (tmp_path / "empty").mkdir()
    assert branch_model.integration_branch_from_project(tmp_path / "empty") is None   # no brief at all: trunk


def test_a_brief_reached_through_a_symlinked_dir_outside_the_project_fails_the_pin_check(t3, tmp_path, capsys) -> None:
    root, team, signers, sha = t3
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief.json").write_text(json.dumps({"s10_cooldown_exempt_repos": []}))
    (root / ".agentteams").symlink_to(outside, target_is_directory=True)
    lock = tmp_path / "toolchain.lock"
    lock.write_text(f"tk=acme/toolkit@{sha}\ntk.tag=v1.0.0\n")
    assert _run_pin(_pin_args(team, lock, signers)) == 1
    assert "outside the project" in capsys.readouterr().err
