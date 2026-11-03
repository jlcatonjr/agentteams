"""Shared helpers for the decision-gate tests (ported to release/1.1 for the S-10 carve-out gate): a team dir, signed decision rows, the decisions log.

Plain functions, not fixtures, because non-test helpers in several modules call them; the ``stub_ed25519`` fixture
that pairs with :func:`_stub_sig` lives in ``tests/conftest.py`` (wave-3 @code-hygiene H8: no test module imports
helpers from another test module).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from agentteams.cli import decision_log as dl

_KEY_ID = "op-2026"
_COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions", "conditions_verified",
            "scope", "derives_from", "effect_class", "effect_grants", "effect_targets", "effect_relaxes",
            "effect_destructive", "effect_cross_repo", "effect_bulk", "sig_scheme", "key_id", "signature"]


def _stub_sig(values) -> str:
    return "stub" + hashlib.sha256("|".join(values).encode()).hexdigest()


def _team(tmp_path: Path, *, governed: bool | None = True) -> Path:
    team = tmp_path / ".claude" / "agents"
    (team / "references" / "authorized-verify-keys").mkdir(parents=True)
    (team / "references" / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_text("stub-key")
    if governed is not None:
        (team / "references" / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": governed}))
    return team


def _row(action: str, *, scope: str | None = None, relaxes: str | None = None, grants: str = "",
         scheme: str = "ed25519", derives: str = "approved-baseline", verdict: str = "PASS", stamp: str = "2026-10-31",
         sign=_stub_sig) -> dict[str, str]:
    row = {c: "" for c in _COLUMNS} | {
        "timestamp": stamp, "requesting_agent": "security", "action_reviewed": action, "verdict": verdict,
        "conditions_verified": "verified", "scope": action if scope is None else scope, "derives_from": derives,
        "effect_grants": grants, "effect_relaxes": (action if not grants else "") if relaxes is None else relaxes,
        "sig_scheme": scheme, "key_id": _KEY_ID,
    }
    if verdict == "PASS":
        row["signature"] = sign(dl._decision_signature_values(row)) if scheme == "ed25519" else "ab" * 32
    return row


def _log(team: Path, *rows: dict[str, str]) -> Path:
    """Append rows to the decisions log (created with an ancestor row the lineage can name)."""
    path = team / "references" / "security-decisions.log.csv"
    existing: list[dict[str, str]] = []
    fields = list(_COLUMNS)
    if path.exists():
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or fields)
            existing = list(reader)
    else:
        existing = [{c: "" for c in _COLUMNS} | {"timestamp": "2026-10-01", "requesting_agent": "security",
                                                 "action_reviewed": "approved-baseline", "verdict": "PASS",
                                                 "conditions_verified": "verified"}]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows([*existing, *rows])
    return path
