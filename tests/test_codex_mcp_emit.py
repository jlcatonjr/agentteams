"""Tests for agentteams.codex_mcp_emit and agentteams.toml_write (open-items
remediation OPEN-6): splicing wirable MCP servers into Codex's real, live
.codex/config.toml without disturbing the rest of the file.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agentteams import codex_mcp_emit as ce
from agentteams import toml_write


def _server(**kw):
    base = {
        "artifact_type": "mcp-server",
        "mcp_server_schema_version": "1.0",
        "server_id": "vk-pg",
        "domain": "data-access",
        "trust_tier": "first-party",
        "transport": "stdio",
        "command": "npx",
        "auth": {"mechanism": "none"},
        "tools": [{"name": "q", "side_effects": "read"}],
        "scope": ["x"],
        "progressive_disclosure": "lazy",
        "security_review": {"required": False},
    }
    base.update(kw)
    # A kw value of None means "omit this key" (e.g. command=None for an
    # http-transport server) rather than "set it to null" — the schema
    # requires command to be a non-empty string when present at all.
    return {k: v for k, v in base.items() if v is not None}


def _config_path(out_root: Path) -> Path:
    return out_root / ".codex" / "config.toml"


# --- toml_write.py -----------------------------------------------------------


def test_toml_write_round_trips_through_tomllib():
    text = toml_write.render_table(
        "mcp_servers.context7",
        {"command": "npx", "args": ["-y", "@upstash/context7-mcp"], "env_vars": ["API_KEY"]},
    )
    parsed = tomllib.loads(text)
    assert parsed == {
        "mcp_servers": {
            "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"], "env_vars": ["API_KEY"]}
        }
    }


def test_toml_write_escapes_special_characters():
    text = toml_write.render_table("mcp_servers.x", {"command": 'echo "hi"\\path\nline'})
    parsed = tomllib.loads(text)
    assert parsed["mcp_servers"]["x"]["command"] == 'echo "hi"\\path\nline'


def test_toml_write_omits_none_values():
    text = toml_write.render_table("mcp_servers.x", {"command": "cmd", "args": None})
    assert "args" not in tomllib.loads(text)["mcp_servers"]["x"]


def test_toml_write_rejects_unsupported_scalar_type():
    import pytest

    with pytest.raises(TypeError):
        toml_write.render_table("mcp_servers.x", {"bad": 3.14})


# --- gating --------------------------------------------------------------


def test_gated_off_without_feature(tmp_path):
    r = ce.emit_codex_mcp_config(servers=[_server()], features=[], output_root=tmp_path)
    assert r.gated_off and not r.written
    assert not _config_path(tmp_path).exists()


def test_empty_servers_is_noop(tmp_path):
    r = ce.emit_codex_mcp_config(servers=[], features=["codex:mcp"], output_root=tmp_path)
    assert not r.gated_off and not r.written


# --- wirable-bar parity with goose (stricter than Claude's inert bar) --------


def test_first_party_read_only_is_wired(tmp_path):
    r = ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)
    assert r.wired == ["vk-pg"]
    parsed = tomllib.loads(_config_path(tmp_path).read_text())
    assert parsed["mcp_servers"]["vk-pg"]["command"] == "npx"


def test_third_party_not_wired(tmp_path):
    r = ce.emit_codex_mcp_config(
        servers=[_server(trust_tier="third-party-vetted", security_review={"required": True})],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == [] and "vk-pg" in r.not_wired
    assert not _config_path(tmp_path).exists()


def test_write_tool_not_wired(tmp_path):
    """Stricter than Claude's inert bar (which allows write): Codex is a live
    config, so even a first-party write-tool server is not auto-wired."""
    r = ce.emit_codex_mcp_config(
        servers=[_server(tools=[{"name": "q", "side_effects": "write"}])],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == [] and "vk-pg" in r.not_wired


def test_security_review_required_not_wired(tmp_path):
    r = ce.emit_codex_mcp_config(
        servers=[_server(security_review={"required": True})],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == []


def test_schema_invalid_server_routed_to_errors(tmp_path):
    bad = _server()
    del bad["domain"]  # required field
    r = ce.emit_codex_mcp_config(servers=[bad], features=["codex:mcp"], output_root=tmp_path)
    assert r.errors and "vk-pg" in r.errors[0]
    assert r.wired == []


# --- transport-specific mapping ----------------------------------------------


def test_stdio_env_mechanism_maps_to_env_vars(tmp_path):
    r = ce.emit_codex_mcp_config(
        servers=[_server(auth={"mechanism": "env", "credential_ref": "VK_PG_DSN"})],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == ["vk-pg"]
    parsed = tomllib.loads(_config_path(tmp_path).read_text())
    assert parsed["mcp_servers"]["vk-pg"]["env_vars"] == ["VK_PG_DSN"]


def test_http_transport_maps_to_url_and_bearer_token(tmp_path):
    r = ce.emit_codex_mcp_config(
        servers=[_server(
            transport="http", command=None,
            auth={"mechanism": "env", "credential_ref": "FIGMA_OAUTH_TOKEN", "url": "https://mcp.figma.com/mcp"},
        )],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == ["vk-pg"]
    parsed = tomllib.loads(_config_path(tmp_path).read_text())
    entry = parsed["mcp_servers"]["vk-pg"]
    assert entry["url"] == "https://mcp.figma.com/mcp"
    assert entry["bearer_token_env_var"] == "FIGMA_OAUTH_TOKEN"


def test_http_transport_without_url_not_wired(tmp_path):
    r = ce.emit_codex_mcp_config(
        servers=[_server(transport="http", command=None, auth={"mechanism": "none"})],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == [] and "no auth.url" in r.not_wired["vk-pg"]


def test_oauth_mechanism_not_expressible(tmp_path):
    r = ce.emit_codex_mcp_config(
        servers=[_server(auth={"mechanism": "oauth"})],
        features=["codex:mcp"], output_root=tmp_path,
    )
    assert r.wired == [] and "oauth" in r.not_wired["vk-pg"]


# --- splice-preserves-unrelated-content --------------------------------------


def test_splice_preserves_hand_authored_content(tmp_path):
    config = _config_path(tmp_path)
    config.parent.mkdir(parents=True)
    original = (
        "# operator sandbox settings — do not remove\n"
        'sandbox_mode = "workspace-write"\n'
        "\n"
        "[mcp_servers.hand-authored]\n"
        'command = "old-command"\n'
    )
    config.write_text(original, encoding="utf-8")

    ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)
    new_text = config.read_text(encoding="utf-8")

    assert "# operator sandbox settings — do not remove" in new_text
    assert 'sandbox_mode = "workspace-write"' in new_text
    assert "hand-authored" not in new_text  # old mcp_servers table replaced, not accumulated
    parsed = tomllib.loads(new_text)
    assert parsed["sandbox_mode"] == "workspace-write"
    assert parsed["mcp_servers"]["vk-pg"]["command"] == "npx"


def test_splice_is_idempotent_on_rerun(tmp_path):
    ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)
    first = _config_path(tmp_path).read_text(encoding="utf-8")
    ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)
    second = _config_path(tmp_path).read_text(encoding="utf-8")
    assert first == second
    assert second.count("BEGIN agentteams-managed") == 1


def test_invalid_existing_toml_refuses_to_splice(tmp_path):
    config = _config_path(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text("this is not [valid toml\n", encoding="utf-8")
    r = ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)
    assert r.errors and "not valid TOML" in r.errors[0]
    assert config.read_text(encoding="utf-8") == "this is not [valid toml\n"  # untouched


def test_multiline_string_containing_fake_table_header_refuses_to_splice(tmp_path):
    """2026-08-10 @security HALT (step E.5): the text-level splice regex has
    no TOML string-quoting awareness. A hand-authored file with a multi-line
    string whose CONTENT merely looks like a [mcp_servers.*] header (e.g. a
    documentation example) makes the naive line-scan under- or over-match,
    corrupting or deleting real content. This is not a contrived adversarial
    case -- it's exactly the shape of a config a real operator could write.
    The content-preservation check must catch it and refuse to write, not
    silently corrupt the file."""
    config = _config_path(tmp_path)
    config.parent.mkdir(parents=True)
    tricky = (
        '[some_table]\n'
        'notes = """\n'
        'Example config:\n'
        '[mcp_servers.fake]\n'
        'command = "not-real"\n'
        '"""\n'
        'real_key = "should-survive"\n'
    )
    config.write_text(tricky, encoding="utf-8")

    r = ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)

    assert not r.written
    assert r.errors
    assert config.read_text(encoding="utf-8") == tricky  # untouched, not corrupted


def test_splice_backs_up_existing_file_before_writing(tmp_path):
    config = _config_path(tmp_path)
    config.parent.mkdir(parents=True)
    original = '[some_table]\nkey = "value"\n'
    config.write_text(original, encoding="utf-8")

    ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)

    backup = config.with_name(config.name + ".bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original


def test_unmanaged_mcp_server_replacement_is_surfaced_not_silent(tmp_path):
    """2026-08-10 adversarial finding (step E.9): a pre-existing
    [mcp_servers.*] table not declared in this run's servers[] (hand-authored,
    or from before the managed-block marker existed) is deliberately replaced
    (_strip_unmanaged_mcp_tables' own job -- a re-run must replace, not
    accumulate), but that replacement was previously invisible: the content-
    preservation check intentionally excludes mcp_servers from its
    comparison, so there was zero operator-facing signal, unlike the
    symmetric not_wired case which is surfaced. Compounds with the
    single-generation .bak: a second divergent run overwrites the only
    remaining copy of the original content. Must be surfaced, not silent."""
    config = _config_path(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text(
        '[mcp_servers.hand-authored]\ncommand = "manual"\n', encoding="utf-8"
    )

    r = ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path)

    assert r.dropped_unmanaged == ["hand-authored"]
    # still replaced (that half of the behavior is intentional, tested
    # elsewhere) -- this test is specifically about the signal existing
    assert "hand-authored" not in _config_path(tmp_path).read_text(encoding="utf-8")


def test_unmanaged_replacement_reported_via_cli_pipeline_wiring(tmp_path, capsys):
    from agentteams.cli.artifacts import _emit_codex_mcp_if_enabled

    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[mcp_servers.hand-authored]\ncommand = "manual"\n', encoding="utf-8")

    _emit_codex_mcp_if_enabled(
        {"host_features": ["codex:mcp"], "mcp_servers": [_server()]}, tmp_path
    )
    out = capsys.readouterr().out
    assert "hand-authored" in out
    assert "not declared to agentteams" in out


# --- dry-run -------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    r = ce.emit_codex_mcp_config(servers=[_server()], features=["codex:mcp"], output_root=tmp_path, dry_run=True)
    assert r.wired == ["vk-pg"]
    assert not _config_path(tmp_path).exists()


# --- pipeline wiring (cli/artifacts.py) --------------------------------------


def test_emit_codex_mcp_if_enabled_noop_without_servers(tmp_path):
    from agentteams.cli.artifacts import _emit_codex_mcp_if_enabled

    _emit_codex_mcp_if_enabled({"host_features": ["codex:mcp"]}, tmp_path)
    assert not _config_path(tmp_path).exists()


def test_emit_codex_mcp_if_enabled_writes_when_gated_on(tmp_path):
    from agentteams.cli.artifacts import _emit_codex_mcp_if_enabled

    _emit_codex_mcp_if_enabled(
        {"host_features": ["codex:mcp"], "mcp_servers": [_server()]}, tmp_path
    )
    assert _config_path(tmp_path).is_file()
