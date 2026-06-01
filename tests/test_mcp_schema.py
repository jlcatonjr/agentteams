"""Tests for schemas/mcp-server.schema.json — the cross-field hard-gate
invariants (allOf if/then) added after the step-1 adversarial audit (H2)."""

from __future__ import annotations

import json
from pathlib import Path

_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "schemas" / "mcp-server.schema.json").read_text()
)


def _v():
    import jsonschema

    return jsonschema.Draft7Validator(_SCHEMA)


def _server(**kw):
    base = {
        "artifact_type": "mcp-server",
        "mcp_server_schema_version": "1.0",
        "server_id": "s",
        "domain": "d",
        "trust_tier": "first-party",
        "transport": "stdio",
        "tools": [{"name": "q", "side_effects": "read"}],
        "scope": [],
        "progressive_disclosure": "lazy",
        "security_review": {"required": False},
    }
    base.update(kw)
    return base


def test_schema_is_valid_draft7():
    import jsonschema
    jsonschema.Draft7Validator.check_schema(_SCHEMA)


def test_baseline_first_party_read_server_is_valid():
    assert _v().is_valid(_server())


def test_destructive_tool_requires_security_review_true():
    bad = _server(tools=[{"name": "rm", "side_effects": "destructive"}])
    assert not _v().is_valid(bad)
    ok = _server(tools=[{"name": "rm", "side_effects": "destructive"}], security_review={"required": True})
    assert _v().is_valid(ok)


def test_third_party_requires_security_review_true():
    bad = _server(trust_tier="third-party-untrusted")
    assert not _v().is_valid(bad)
    ok = _server(trust_tier="third-party-untrusted", security_review={"required": True})
    assert _v().is_valid(ok)


def test_http_transport_requires_auth():
    assert not _v().is_valid(_server(transport="http"))
    assert _v().is_valid(_server(transport="http", auth={"mechanism": "env", "credential_ref": "X", "url": "https://h"}))


def test_inline_secret_credential_ref_is_rejected():
    bad = _server(auth={"mechanism": "env", "credential_ref": "postgres://u:p@h/db"})
    assert not _v().is_valid(bad)


def test_additional_properties_rejected():
    assert not _v().is_valid(_server(activation_requires_authorization=True))
