"""Tests for agentteams.release_required_checks: the required-check proposal a release PR needs (git-procedures §I).

The proposal is pure; GitHub is never contacted here (``_gh`` is replaced where a test reaches it). The fixture is
the #262 switch (ubuntu-24.04 → 26.04).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentteams.release_required_checks as rrc

GHA = 15368
MAIN = {c: GHA for c in ("wasm-live", "macos-seatbelt", "docs-build", "template-lineage",
                         "test (ubuntu-24.04, 3.11)", "test (ubuntu-24.04, 3.12)",
                         "test (macos-latest, 3.11)", "test (macos-latest, 3.12)")}


def _pr(*names: str, conclusion: str = "success") -> dict[str, tuple[str, int]]:
    return {n: (conclusion, GHA) for n in names}


RELEASE = _pr("wasm-live", "macos-seatbelt", "docs-build", "template-lineage", "ubuntu-26-confinement",
              "test (ubuntu-26.04, 3.11)", "test (ubuntu-26.04, 3.12)",
              "test (macos-latest, 3.11)", "test (macos-latest, 3.12)")


def test_runner_bump_is_proposed_as_a_rename() -> None:
    p = rrc.propose(MAIN, RELEASE)
    assert "test (ubuntu-26.04, 3.11)" in p.new and "test (ubuntu-24.04, 3.11)" not in p.new
    assert p.replaced["test (ubuntu-24.04, 3.11)"] == ["test (ubuntu-26.04, 3.11)", "test (ubuntu-26.04, 3.12)"]
    assert "test (macos-latest, 3.11)" in p.new
    assert p.not_green == []


def test_a_new_job_is_never_required_implicitly() -> None:
    """ubuntu-26-confinement is new on the PR; requiring it is a decision, so only --add does it."""
    assert "ubuntu-26-confinement" not in rrc.propose(MAIN, RELEASE).new
    assert "ubuntu-26-confinement" in rrc.propose(MAIN, RELEASE, add=["ubuntu-26-confinement"]).new


def test_a_required_check_the_pr_lost_refuses_unless_dropped() -> None:
    pr = {k: v for k, v in RELEASE.items() if k != "wasm-live"}
    with pytest.raises(rrc.Refused, match="wasm-live"):
        rrc.propose(MAIN, pr)
    assert "wasm-live" not in rrc.propose(MAIN, pr, drop=["wasm-live"]).new


def test_not_green_checks_are_reported() -> None:
    pr = dict(RELEASE)
    pr["test (ubuntu-26.04, 3.12)"] = ("", GHA)          # still running
    pr["docs-build"] = ("failure", GHA)
    assert rrc.propose(MAIN, pr).not_green == ["docs-build", "test (ubuntu-26.04, 3.12)"]


def test_bad_add_or_drop_refuses() -> None:
    with pytest.raises(rrc.Refused, match="--add"):
        rrc.propose(MAIN, RELEASE, add=["nonexistent"])
    with pytest.raises(rrc.Refused, match="--drop"):
        rrc.propose(MAIN, RELEASE, drop=["ubuntu-26-confinement"])


def test_unchanged_names_need_no_change() -> None:
    same = _pr(*MAIN)
    p = rrc.propose(MAIN, same)
    assert p.new == p.old and p.replaced == {}


def test_fingerprint_binds_head_sets_and_strict() -> None:
    p = rrc.propose(MAIN, RELEASE)
    fp = rrc.fingerprint(p, strict=False, pr=512, head_sha="a" * 40)
    assert fp == rrc.fingerprint(rrc.propose(MAIN, RELEASE), strict=False, pr=512, head_sha="a" * 40)
    assert fp != rrc.fingerprint(p, strict=True, pr=512, head_sha="a" * 40)
    assert fp != rrc.fingerprint(p, strict=False, pr=512, head_sha="b" * 40)
    assert fp != rrc.fingerprint(rrc.propose(MAIN, RELEASE, add=["ubuntu-26-confinement"]),
                                 strict=False, pr=512, head_sha="a" * 40)


# --------------------------------------------------------------------------- security review (2026-11-13) findings

def test_a_renamed_check_keeps_its_app_pin() -> None:
    pr = dict(RELEASE)
    pr["test (ubuntu-26.04, 3.11)"] = ("success", -1)     # a commit status: any token can post it
    with pytest.raises(rrc.Refused, match="commit status"):
        rrc.propose(MAIN, pr)
    pr["test (ubuntu-26.04, 3.11)"] = ("success", 999)    # another app
    with pytest.raises(rrc.Refused, match="app 999"):
        rrc.propose(MAIN, pr)


def test_add_refuses_an_unpinnable_status() -> None:
    pr = dict(RELEASE, **{"ci/status": ("success", -1)})
    with pytest.raises(rrc.Refused, match="commit status"):
        rrc.propose(MAIN, pr, add=["ci/status"])


def test_never_proposes_an_empty_set() -> None:
    with pytest.raises(rrc.Refused, match="no checks at all"):
        rrc.propose(MAIN, RELEASE, drop=list(MAIN))


def _backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current: dict[str, int]) -> tuple[Path, list]:
    monkeypatch.setattr(rrc, "BACKUP_DIR", tmp_path)
    patched: list = []
    monkeypatch.setattr(rrc, "_protection", lambda repo, branch: (False, dict(current)))
    monkeypatch.setattr(rrc, "_refuse_rulesets", lambda repo, branch: None)
    monkeypatch.setattr(rrc, "_patch", lambda repo, branch, strict, checks: (patched.append(checks), current.clear(),
                                                                     current.update(checks)))
    p = rrc.propose(MAIN, RELEASE)
    fp = rrc.fingerprint(p, strict=False, pr=512, head_sha="a" * 40)
    f = tmp_path / "main-required-checks-20261113T000000Z.json"
    f.write_text(json.dumps({"repo": "o/r", "pr": 512, "head": "a" * 40, "fingerprint": fp,
                                 "before": {"strict": False, "checks": sorted(p.old.items())},
                                 "after": {"strict": False, "checks": sorted(p.new.items())}}))
    return f, patched


def test_restore_is_shown_then_applied_by_fingerprint(tmp_path, monkeypatch, capsys) -> None:
    current = dict(rrc.propose(MAIN, RELEASE).new)
    f, patched = _backup(tmp_path, monkeypatch, current)
    assert rrc._restore("o/r", f, None) == 0 and patched == []
    fp = capsys.readouterr().out.split("fingerprint ")[1].split()[0]
    with pytest.raises(rrc.Refused, match="does not match"):
        rrc._restore("o/r", f, "0" * 12)
    assert rrc._restore("o/r", f, fp) == 0 and patched == [MAIN]


def test_restore_refuses_a_file_outside_its_backup_dir_or_tampered(tmp_path, monkeypatch) -> None:
    current = dict(rrc.propose(MAIN, RELEASE).new)
    f, _ = _backup(tmp_path, monkeypatch, current)
    other = tmp_path / "elsewhere"
    other.mkdir()
    stray = other / f.name
    stray.write_text(f.read_text())
    with pytest.raises(rrc.Refused, match="only a backup"):
        rrc._restore("o/r", stray, None)
    doc = json.loads(f.read_text())
    doc["before"]["checks"] = []                           # crafted: "restore" to require nothing
    f.write_text(json.dumps(doc))
    with pytest.raises(rrc.Refused, match="fingerprint"):
        rrc._restore("o/r", f, None)


def test_preview_shows_app_pins_and_flags_unpinned(capsys) -> None:
    p = rrc.Proposal(old={"a": GHA}, new={"a": -1, "b": GHA})
    rrc._show(p)
    out = capsys.readouterr().out
    assert "a [any source: UNPINNED]   (app changes)" in out and "b [app 15368]   (new)" in out


def test_restore_refuses_a_backup_that_changes_strict(tmp_path, monkeypatch) -> None:
    current = dict(rrc.propose(MAIN, RELEASE).new)
    f, patched = _backup(tmp_path, monkeypatch, current)
    doc = json.loads(f.read_text())
    doc["before"]["strict"] = True                         # main has strict=False
    p = rrc.Proposal(old=dict(doc["before"]["checks"]), new=dict(doc["after"]["checks"]))
    doc["fingerprint"] = rrc.fingerprint(p, strict=True, pr=512, head_sha="a" * 40)
    f.write_text(json.dumps(doc))
    with pytest.raises(rrc.Refused, match="strict"):
        rrc._restore("o/r", f, None)
    assert patched == []


def test_preview_escapes_control_characters(capsys) -> None:
    rrc._show(rrc.Proposal(old={"a": GHA}, new={"a": GHA, "b\x1b[2K\rhidden": GHA}))
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\\x1b[2K\\rhidden" in out


# --------------------------------------------------------------------------- shipped to generated teams (2026-11-14)

def _fake_gh(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def gh(*args: str, stdin: str | None = None) -> str:
        calls.append(args)
        for key, out in answers.items():
            if key in " ".join(args):
                if out == "FAIL":
                    raise rrc.Refused("gh api failed: HTTP 403")
                return out
        raise AssertionError(f"unexpected gh call {args}")

    monkeypatch.setattr(rrc, "_gh", gh)
    return calls


def test_the_default_branch_is_read_never_assumed(monkeypatch) -> None:
    """A team whose default branch is master switches master's checks, and a PR into main is refused there."""
    _fake_gh(monkeypatch, {"repos/o/r -q": "master\n", "rules/branches/master": "[]",
                           "branches/master/protection": json.dumps({"strict": False, "contexts": ["ci"]}),
                           "pr view": json.dumps({"state": "OPEN", "baseRefName": "main", "headRefName": "dev",
                                                  "headRefOid": "a" * 40, "isCrossRepository": False})})
    assert rrc.run(["--repo", "o/r", "--pr", "7", "--integration-branch", "dev"]) == 1


def test_a_ruleset_requiring_checks_refuses(monkeypatch, capsys) -> None:
    _fake_gh(monkeypatch, {"repos/o/r -q": "main\n",
                           "rules/branches/main": json.dumps([{"type": "required_status_checks"}])})
    assert rrc.run(["--repo", "o/r", "--pr", "7"]) == 1
    assert "ruleset" in capsys.readouterr().err


def test_no_admin_token_refuses_with_a_handoff(monkeypatch, capsys) -> None:
    _fake_gh(monkeypatch, {"repos/o/r -q": "main\n", "rules/branches/main": "[]", "protection": "FAIL"})
    assert rrc.run(["--repo", "o/r", "--pr", "7"]) == 1
    assert "admin token" in capsys.readouterr().err


def test_the_integration_branch_comes_from_the_flag(monkeypatch, capsys) -> None:
    """A release PR from 'develop' is refused unless the integration branch is 'develop'."""
    meta = json.dumps({"state": "OPEN", "baseRefName": "main", "headRefName": "develop", "headRefOid": "a" * 40,
                       "isCrossRepository": False})
    _fake_gh(monkeypatch, {"repos/o/r -q": "main\n", "rules/branches/main": "[]", "pr view": meta,
                           "protection": json.dumps({"strict": False, "contexts": []})})
    assert rrc.run(["--repo", "o/r", "--pr", "7", "--integration-branch", "dev"]) == 1
    assert "only a release PR (dev)" in capsys.readouterr().err


def test_printed_commands_use_the_module(capsys) -> None:
    assert rrc.COMMAND == "python3 -m agentteams.release_required_checks"
