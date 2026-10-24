"""``grant sign --from-registry``: the registry proposes, the operator reviews the change per owner. Network-free.

Checkouts are fake repositories (a ``.git`` directory with a config file), which is all
:func:`agentteams.vulnscan.registry.identity` reads; git is never run here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.cli import operator_signing as ops_  # noqa: E402
from agentteams.vulnscan import __main__ as cli  # noqa: E402
from agentteams.vulnscan import grant, paths, registry  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.delenv("CI", raising=False)
    return operator_home(tmp_path, monkeypatch)


def _checkout(path: Path, origin: str | None = None) -> Path:
    (path / ".git").mkdir(parents=True)
    cfg = "[core]\n\tbare = false\n" + (f'[remote "origin"]\n\turl = {origin}\n' if origin else "")
    (path / ".git" / "config").write_text(cfg)
    return path


def _entry(path: Path, level: str = "remediate", owner: str | None = None) -> registry.Entry:
    ident = registry.identity(path)
    return registry.Entry(identity=ident, fix_checkout=str(path.resolve()), checkouts=[str(path.resolve())],
                          level=level, owner=owner or registry.owner_of(ident), enrolled="2026-10-01T00:00:00+00:00")


def _sign_previous(env: dict, roots: list[str]) -> dict:
    rec = grant.build(ops=["stage-fix", "open-pr"], repo_roots=roots, hosts=[], days=7, max_uses=5, key_id="op",
                      governance_dir=str(env["gov"]))
    signed = sign_grant(env["pem"], rec)
    grant.install(signed)
    return signed


def _world(env: dict) -> dict:
    r = env["tmp"] / "repos"
    return {"kept": _checkout(r / "kept", "https://github.example.com/acme/kept.git"),
            "gone": _checkout(r / "gone", "https://github.example.com/acme/gone.git"),
            "new_acme": _checkout(r / "new-acme", "git@github.example.com:acme/new-acme.git"),
            "new_other": _checkout(r / "new-other", "https://github.example.com/other/tool"),
            "scan": _checkout(r / "scan", "https://github.example.com/acme/scan-only")}


def test_proposal_takes_only_remediate_fix_checkouts_once(env: dict) -> None:
    w = _world(env)
    entries = [_entry(w["kept"]), _entry(w["new_acme"]), _entry(w["scan"], "scan"),
               _entry(w["new_other"], "off"), _entry(w["gone"], "unanswered")]
    dup = registry.Entry(**{**entries[0].__dict__, "identity": "local:/elsewhere"})   # same fix checkout twice
    roots, left_out = grant.registry_proposal([*entries, dup], {})
    assert sorted(roots) == sorted(str(w[k].resolve()) for k in ("kept", "new_acme")) and not left_out
    assert roots[str(w["kept"].resolve())] == "github.example.com/acme"


def test_proposal_groups_by_the_current_origin_owner_and_flags_a_changed_origin(env: dict) -> None:
    w = _world(env)
    moved = _entry(w["new_acme"])
    (w["new_acme"] / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.example.com/else/x\n')
    roots, _left = grant.registry_proposal([_entry(w["kept"]), moved], {})
    owner = roots[str(w["new_acme"].resolve())]
    assert owner.startswith("github.example.com/else (ORIGIN CHANGED: enrolled as github.example.com/acme/new-acme")
    assert roots[str(w["kept"].resolve())] == "github.example.com/acme"
    out: list[str] = []
    asked: list[str] = []
    grant.review_registry_roots(roots, [], [], ask=lambda p: asked.append(p) or "n", say=out.append)
    assert any("github.example.com/else (ORIGIN CHANGED" in a for a in asked)   # the operator sees it per owner


def test_diff_shows_additions_and_removals_per_owner_and_only_counts_unchanged(env: dict) -> None:
    w = _world(env)
    _sign_previous(env, [str(w["kept"]), str(w["gone"])])
    registry.save([_entry(w["kept"]), _entry(w["new_acme"]), _entry(w["new_other"]), _entry(w["scan"], "scan")])
    out: list[str] = []
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "y" if "acme" in prompt else "n"

    roots = grant.roots_from_registry({}, [], str(env["gov"]), ask=ask, say=out.append, tty=lambda: True)
    text = "\n".join(out)
    assert roots == sorted([str(w["kept"].resolve()), str(w["new_acme"].resolve())])
    assert "1 unchanged" in text and "2 to add" in text and "1 to remove" in text
    assert str(w["kept"].resolve()) not in text                       # unchanged roots: a count, not a list
    assert "removed:" in text and str(w["gone"].resolve()) in text and "github.example.com/acme" in text
    assert str(w["scan"].resolve()) not in text                       # scan level is never proposed
    assert len(asked) == 2 and any("github.example.com/other" in a for a in asked)
    assert "dropped 1 additions of github.example.com/other" in text


def test_no_answer_drops_every_addition(env: dict) -> None:
    w = _world(env)
    registry.save([_entry(w["new_acme"]), _entry(w["new_other"])])
    out: list[str] = []
    roots = grant.roots_from_registry({}, [], str(env["gov"]), ask=lambda p: "", say=out.append, tty=lambda: True)
    assert roots == [] and "no previous signed grant" in "\n".join(out)


def test_explicit_repo_roots_are_kept_and_shown(env: dict) -> None:
    w = _world(env)
    registry.save([_entry(w["new_acme"])])
    out: list[str] = []
    roots = grant.roots_from_registry({}, [str(w["new_other"])], str(env["gov"]), ask=lambda p: "n",
                                      say=out.append, tty=lambda: True)
    assert roots == [str(w["new_other"].resolve())]
    assert "given with --repo-root" in "\n".join(out)


def test_protected_origin_is_never_proposed(env: dict) -> None:
    """A separate clone of the governance team (same origin) is left out, though it is enrolled at remediate."""
    gov_origin = "https://github.example.com/acme/governance.git"
    _checkout(env["gov"], gov_origin)
    clone = _checkout(env["tmp"] / "repos" / "gov-clone", "git@github.example.com:acme/governance")
    team = _checkout(env["tmp"] / "team", "https://github.example.com/acme/team")
    team_clone = _checkout(env["tmp"] / "repos" / "team-copy", "https://github.example.com/acme/team.git")
    w = _world(env)
    registry.save([_entry(clone), _entry(team_clone), _entry(w["new_acme"])])
    out: list[str] = []
    roots = grant.roots_from_registry({"agent": {"team_dir": str(team)}}, [], str(env["gov"]), ask=lambda p: "y",
                                      say=out.append, tty=lambda: True)
    assert roots == [str(w["new_acme"].resolve())]
    text = "\n".join(out)
    assert "not proposed (protected)" in text and "github.example.com/acme/governance" in text
    assert "github.example.com/acme/team" in text


def test_refused_on_ci_and_without_a_terminal(env: dict, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture) -> None:
    w = _world(env)
    registry.save([_entry(w["new_acme"])])
    out: list[str] = []
    assert grant.roots_from_registry({}, [], str(env["gov"]), say=out.append, tty=lambda: False) is None
    assert "interactively" in out[-1]
    monkeypatch.setenv("CI", "true")
    assert grant.roots_from_registry({}, [], str(env["gov"]), say=out.append, tty=lambda: True) is None
    assert "CI" in out[-1]
    monkeypatch.delenv("CI")
    # through the CLI: pytest's stdin is not a terminal, so nothing is built or signed
    code = cli.main(["grant", "sign", "--from-registry", "--op", "stage-fix", "--key-id", "op",
                     "--governance-dir", str(env["gov"])])
    assert code == 2 and "interactively" in capsys.readouterr().out
    assert not grant.grants_dir().is_dir() or not list(grant.grants_dir().glob("vg-*.json"))


def test_unreadable_registry_refuses(env: dict) -> None:
    path = registry.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    out: list[str] = []
    assert grant.roots_from_registry({}, [], str(env["gov"]), say=out.append, tty=lambda: True) is None
    assert "not valid JSON" in out[-1]


def test_a_forged_or_unsigned_file_is_not_the_baseline(env: dict) -> None:
    """A planted 'previous grant' listing the additions cannot make them look unchanged (and skip the question)."""
    w = _world(env)
    signed = _sign_previous(env, [str(w["kept"])])
    forged = {**signed, "grant_id": "vg-" + "f" * 16, "issued": "2026-12-01T00:00:00+00:00",
              "repo_roots": [str(w["kept"].resolve()), str(w["new_acme"].resolve())]}
    (grant.grants_dir() / f"{forged['grant_id']}.json").write_text(json.dumps(forged))
    (grant.grants_dir() / "vg-0000000000000000.json").write_text("{not json")
    assert grant.previous_grant()["grant_id"] == signed["grant_id"]
    registry.save([_entry(w["kept"]), _entry(w["new_acme"])])
    asked: list[str] = []
    grant.roots_from_registry({}, [], str(env["gov"]), ask=lambda p: asked.append(p) or "y", say=lambda s: None,
                              tty=lambda: True)
    assert len(asked) == 1 and "acme" in asked[0]


def test_an_expired_signed_grant_is_still_the_baseline(env: dict) -> None:
    w = _world(env)
    signed = _sign_previous(env, [str(w["kept"])])
    expired = {**signed, "issued": "2026-01-01T00:00:00+00:00", "expires": "2026-01-08T00:00:00+00:00"}
    expired = sign_grant(env["pem"], {k: v for k, v in expired.items() if k != "signature"})
    grant.revoke(signed["grant_id"])
    grant.install(expired)
    assert not grant.load_active()[0]                                  # not valid now ...
    assert grant.previous_grant()["grant_id"] == expired["grant_id"]   # ... but the previous grant


def test_review_digest_covers_the_full_root_list(env: dict) -> None:
    """The signing review is unchanged: its digest binds every root, unchanged ones included, not just the diff."""
    w = _world(env)
    _sign_previous(env, [str(w["kept"])])
    registry.save([_entry(w["kept"]), _entry(w["new_acme"])])
    roots = grant.roots_from_registry({}, [], str(env["gov"]), ask=lambda p: "y", say=lambda s: None,
                                      tty=lambda: True)
    rec = grant.build(ops=["stage-fix"], repo_roots=roots, hosts=[], days=7, max_uses=5, key_id="op",
                      governance_dir=str(env["gov"]))
    assert ops_.review_choices(rec)["repo_roots"] == sorted(str(w[k].resolve()) for k in ("kept", "new_acme"))
    only_diff = grant.build(ops=["stage-fix"], repo_roots=[str(w["new_acme"])], hosts=[], days=7, max_uses=5,
                            key_id="op", governance_dir=str(env["gov"]))
    digest = ops_.review_digest("vulnscan-remediation-grant", ops_.review_choices(rec), paths.config_dir(), "op")
    assert digest != ops_.review_digest("vulnscan-remediation-grant", ops_.review_choices(only_diff),
                                        paths.config_dir(), "op")
