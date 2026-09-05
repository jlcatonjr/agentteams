"""test_management_emit.py — emit surface for the management-repository endowment.

Exercises ``_write_management_authority_config`` (artifacts.py), the opt-in, byte-identical-
when-off writer that mirrors ``_write_agent_privilege_config``:

* a manifest with authorized managers emits management-authority.json + the authorized-managers
  roster + a header-only management-directives ledger stub;
* a manifest that only marks is_management_repo emits the switch with NO roster/ledger;
* a manifest with neither field emits NOTHING (existing teams stay byte-identical);
* an existing (signed) ledger is NEVER clobbered on re-emit.
"""

from __future__ import annotations

import json

from agentteams.cli import management_directives as md
from agentteams.cli.artifacts import (
    MANAGEMENT_AUTHORITY_REL_PATH,
    _write_management_authority_config,
)


def test_authorized_managers_emits_config_roster_and_ledger_stub(tmp_path):
    manifest = {
        "project_name": "T",
        "authorized_managers": ["mgr-team"],
        "is_management_repo": False,
    }
    path = _write_management_authority_config(tmp_path, manifest)

    # (a) management-authority.json with correct content.
    assert path == tmp_path / MANAGEMENT_AUTHORITY_REL_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["is_management_repo"] is False
    assert payload["authorized_managers"] == ["mgr-team"]
    assert isinstance(payload.get("note"), str) and payload["note"]

    # (a) the authorized-managers roster contains the manager team-id, and its non-comment
    # lines are exactly the roster entries.
    roster_path = tmp_path / md.AUTHORIZED_MANAGERS_REL
    roster_text = roster_path.read_text(encoding="utf-8")
    assert "mgr-team" in roster_text
    body = [
        line.strip()
        for line in roster_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert body == ["mgr-team"]

    # (a) a header-only management-directives ledger stub (no data rows).
    ledger_path = tmp_path / md.MGMT_DIRECTIVES_LOG_REL
    assert ledger_path.read_text(encoding="utf-8") == ",".join(md.MGMT_DIRECTIVE_COLUMNS) + "\n"


def test_management_repo_flag_only_emits_config_without_roster(tmp_path):
    manifest = {"project_name": "T", "is_management_repo": True, "authorized_managers": []}
    path = _write_management_authority_config(tmp_path, manifest)

    # (b) management-authority.json with is_management_repo true.
    assert path == tmp_path / MANAGEMENT_AUTHORITY_REL_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["is_management_repo"] is True
    assert payload["authorized_managers"] == []

    # (b) NO roster and NO ledger stub when there are no authorized managers.
    assert not (tmp_path / md.AUTHORIZED_MANAGERS_REL).exists()
    assert not (tmp_path / md.MGMT_DIRECTIVES_LOG_REL).exists()


def test_neither_field_emits_nothing(tmp_path):
    # (c) off: neither field set -> nothing written, byte-identical to an untouched team.
    manifest = {"project_name": "T"}
    assert _write_management_authority_config(tmp_path, manifest) is None
    assert not (tmp_path / MANAGEMENT_AUTHORITY_REL_PATH).exists()
    assert not (tmp_path / md.AUTHORIZED_MANAGERS_REL).exists()
    assert not (tmp_path / md.MGMT_DIRECTIVES_LOG_REL).exists()

    # Also off for a falsy is_management_repo + explicitly-empty roster.
    assert _write_management_authority_config(
        tmp_path, {"is_management_repo": False, "authorized_managers": []}
    ) is None
    assert not (tmp_path / MANAGEMENT_AUTHORITY_REL_PATH).exists()


def test_existing_ledger_is_never_clobbered(tmp_path):
    # A ledger that already holds a (here, sentinel) signed row must survive a re-emit — the
    # stub is created ONLY when the ledger is absent.
    ledger_path = tmp_path / md.MGMT_DIRECTIVES_LOG_REL
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    preexisting = ",".join(md.MGMT_DIRECTIVE_COLUMNS) + "\n" + "existing-signed-row\n"
    ledger_path.write_text(preexisting, encoding="utf-8")

    _write_management_authority_config(tmp_path, {"authorized_managers": ["mgr-team"]})

    assert ledger_path.read_text(encoding="utf-8") == preexisting
