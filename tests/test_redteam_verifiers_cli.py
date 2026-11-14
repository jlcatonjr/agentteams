"""test_redteam_verifiers_cli.py — F-1 sensitivity / negative-control pairs for ``agentteams/cli``.

Companion to ``tests/test_redteam_verifiers.py``. Each verifier below was found by F-1 with no
ledger row and no test that exercised it *directly*: it was covered only through a caller (so a
regression in the helper could be masked by its caller) or not at all. Every test here belongs
to a pair named in ``references/redteam-verifiers.csv``:

* a **sensitivity test** proves the verifier's output changes when a relevant input changes;
* a **negative control** proves it does not change for irrelevant input, or that a valid input
  passes.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentteams.cli import adopt_merge_gate as amg
from agentteams.cli import clearance_binding as cb
from agentteams.cli import decision_log as dl
from agentteams.cli import management_directives as md
from agentteams.cli import signed_ledger as sl
from agentteams.cli.action_ids import action_matches

_HMAC_KEY = "k-0123456789abcdef0123456789abcdef"
_FIELDS = ["2026-11-12", "grant-cross-repo-write", "PASS", "verified"]


# ---------------------------------------------------------------------------
# action_ids.action_matches
# ---------------------------------------------------------------------------

def test_action_matches_changes_with_the_action_id() -> None:
    """SENSITIVITY: the same row id matches its action (and decorated instances) but not another action.

    A HALT or PASS row binds an action only through this matcher, so a matcher that returned a
    constant would make every HALT bind everything, or nothing.
    """
    assert action_matches("prune", "prune") is True
    for sep in "-_.:":
        assert action_matches(f"prune{sep}001", "prune") is True
    assert action_matches("prune", "overwrite") is False
    assert action_matches("prunex", "prune") is False      # a longer id is not an instance
    assert action_matches("pre-prune", "prune") is False   # a prefix is not the action
    assert action_matches("anything", "") is False         # an empty action matches nothing


def test_action_matches_ignores_case_and_surrounding_whitespace() -> None:
    """NEGATIVE CONTROL: case and padding are irrelevant; the match decision does not change."""
    for reviewed, action in (("prune-001", "prune"), ("overwrite", "prune")):
        expected = action_matches(reviewed, action)
        assert action_matches(f"  {reviewed.upper()} ", f" {action.title()}  ") is expected


# ---------------------------------------------------------------------------
# adopt_merge_gate._check_slugs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["../escape", "Upper", "-lead", "has space", "a/b", ""])
def test_check_slugs_refuses_a_malformed_slug(bad: str) -> None:
    """SENSITIVITY: one malformed slug among good ones turns acceptance into a refusal naming it."""
    amg._check_slugs(["good-agent"])
    with pytest.raises(amg.AdoptMergeError, match="malformed agent slug"):
        amg._check_slugs(["good-agent", bad])


def test_check_slugs_accepts_well_formed_slugs_in_any_order() -> None:
    """NEGATIVE CONTROL: valid slugs pass, and their order or repetition is irrelevant."""
    amg._check_slugs([])
    amg._check_slugs(["a", "agent-2", "x9"])
    amg._check_slugs(["x9", "a", "agent-2", "a"])


@pytest.mark.xfail(strict=True, reason=(
    "_SLUG_RE uses '$', which matches before a trailing newline, so 'agent\\n' is accepted; "
    "\\Z (or re.fullmatch) would refuse it. Reported, not fixed (source is out of scope)."))
def test_check_slugs_refuses_a_trailing_newline() -> None:
    """Known gap: a slug with a trailing newline should be refused like any other malformed slug."""
    with pytest.raises(amg.AdoptMergeError):
        amg._check_slugs(["agent\n"])


# ---------------------------------------------------------------------------
# clearance_binding.check_file_clearance
# ---------------------------------------------------------------------------

def _decisions(team: Path, rows: list[tuple[str, str]]) -> None:
    cols = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions",
            "conditions_verified"]
    (team / "references").mkdir(parents=True, exist_ok=True)
    with (team / "references" / "security-decisions.log.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for action, verdict in rows:
            writer.writerow({"timestamp": "2026-11-08T00:00:00Z", "requesting_agent": "security",
                             "action_reviewed": action, "verdict": verdict, "conditions": "",
                             "conditions_verified": ""})


def test_check_file_clearance_ignores_a_halt_on_an_unrelated_scope(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: a sha-bound PASS still clears when an unrelated scope carries a HALT."""
    team = tmp_path / "team"
    script = tmp_path / "run.sh"
    script.write_text("echo one\n")
    _decisions(team, [(cb.clearance_action("roster-batch", script), "PASS")])
    assert cb.check_file_clearance(team, "roster-batch", script) == (True, "")
    _decisions(team, [(cb.clearance_action("roster-batch", script), "PASS"),
                      ("unrelated-scope", "HALT")])
    assert cb.check_file_clearance(team, "roster-batch", script) == (True, "")


# ---------------------------------------------------------------------------
# decision_log._assert_ed25519_sufficient (called directly, not through the authenticity gate)
# ---------------------------------------------------------------------------

_ED_KEY_ID = "op-f1"


def _ed25519_team(root: Path) -> str:
    """Install one Ed25519 verify key under ``root``; return the private PEM."""
    pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    store = root / "references" / "authorized-verify-keys"
    store.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    (store / f"{_ED_KEY_ID}.pub.pem").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode("utf-8")


def _relaxing_row(private_pem: str, **over: str) -> dict[str, str]:
    row = {"date": "2026-11-12", "action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
           "conditions_verified": "verified", "owner": "security", "prev_digest": "",
           "effect_grants": "write:cross-repo", "sig_scheme": "ed25519", "key_id": _ED_KEY_ID,
           "signature": ""}
    row.update(over)
    row["signature"] = sl.ed25519_sign(private_pem, dl._decision_signature_values(row))
    return row


def test_ed25519_sufficient_refuses_hmac_and_tampered_rows(tmp_path: Path) -> None:
    """SENSITIVITY: the scheme and every signed field decide; changing either refuses the row."""
    private_pem = _ed25519_team(tmp_path)
    row = _relaxing_row(private_pem)
    dl._assert_ed25519_sufficient(row, output_dir=tmp_path, action="grant-cross-repo-write")
    hmac_row = {**row, "sig_scheme": "hmac"}
    hmac_row["signature"] = dl.sign_decision_row(hmac_row, key=_HMAC_KEY)
    with pytest.raises(RuntimeError, match="REQUIRES ed25519"):
        dl._assert_ed25519_sufficient(hmac_row, output_dir=tmp_path, action="grant-cross-repo-write")
    with pytest.raises(RuntimeError, match="failed Ed25519 verification"):
        dl._assert_ed25519_sufficient({**row, "effect_grants": "write:everything"},
                                      output_dir=tmp_path, action="grant-cross-repo-write")


def test_ed25519_sufficient_ignores_unsigned_columns(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: a valid row passes, and a column outside the signed payload does not matter."""
    private_pem = _ed25519_team(tmp_path)
    row = _relaxing_row(private_pem)
    dl._assert_ed25519_sufficient(row, output_dir=tmp_path, action="grant-cross-repo-write")
    dl._assert_ed25519_sufficient({**row, "notes": "free text", "timestamp": "2026-11-12T09:00:00Z"},
                                  output_dir=tmp_path, action="other-label-for-the-message")


# ---------------------------------------------------------------------------
# management_directives.assert_directive_chain_intact
# ---------------------------------------------------------------------------

def _directive_rows(count: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i in range(count):
        row = {"directive_id": f"d-{i}", "task_scope": "draft-weekly-report",
               "prev_digest": md._directive_chain_digest(rows[-1]) if rows else ""}
        rows.append(row)
    return rows


def test_directive_chain_breaks_on_a_removed_or_edited_row() -> None:
    """SENSITIVITY: removing or editing a row in an intact chain raises naming the broken row."""
    columns = list(md.MGMT_DIRECTIVE_COLUMNS)
    rows = _directive_rows(3)
    md.assert_directive_chain_intact(rows, columns)
    with pytest.raises(md.MgmtDirectiveError, match="chain broken at row 2"):
        md.assert_directive_chain_intact([rows[0], rows[2]], columns)
    edited = [rows[0], {**rows[1], "task_scope": "something-else"}, rows[2]]
    with pytest.raises(md.MgmtDirectiveError, match="chain broken at row 3"):
        md.assert_directive_chain_intact(edited, columns)


def test_directive_chain_ignores_unsigned_columns() -> None:
    """NEGATIVE CONTROL: an intact chain passes, and a column outside the signed fields is irrelevant."""
    columns = list(md.MGMT_DIRECTIVE_COLUMNS)
    rows = _directive_rows(3)
    noisy = [{**r, "not_a_signed_column": f"noise-{i}"} for i, r in enumerate(rows)]
    md.assert_directive_chain_intact(noisy, [*columns, "not_a_signed_column"])
    md.assert_directive_chain_intact(rows, [c for c in columns if c != "prev_digest"])


# ---------------------------------------------------------------------------
# signed_ledger.verify_by_scheme
# ---------------------------------------------------------------------------

def test_verify_by_scheme_rejects_a_changed_field_under_each_scheme() -> None:
    """SENSITIVITY: the same signature stops verifying once any signed field changes."""
    tampered = [*_FIELDS[:-1], "forged"]
    sig = sl.hmac_sign(_HMAC_KEY, _FIELDS)
    assert sl.verify_by_scheme("hmac", _FIELDS, sig, hmac_key=_HMAC_KEY) is True
    assert sl.verify_by_scheme("hmac", tampered, sig, hmac_key=_HMAC_KEY) is False
    assert sl.verify_by_scheme("hmac", _FIELDS, sig, hmac_key=_HMAC_KEY + "x") is False
    pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode("utf-8")
    public_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
    ed_sig = sl.ed25519_sign(private_pem, _FIELDS)
    assert sl.verify_by_scheme("ed25519", _FIELDS, ed_sig, public_pem=public_pem) is True
    assert sl.verify_by_scheme("ed25519", tampered, ed_sig, public_pem=public_pem) is False


def test_verify_by_scheme_ignores_scheme_spelling() -> None:
    """NEGATIVE CONTROL: case, padding and the empty default name the same HMAC scheme."""
    sig = sl.hmac_sign(_HMAC_KEY, _FIELDS)
    for scheme in ("hmac", "", " HMAC ", "Hmac"):
        assert sl.verify_by_scheme(scheme, _FIELDS, sig, hmac_key=_HMAC_KEY) is True
