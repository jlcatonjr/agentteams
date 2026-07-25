"""Unit tests for agentteams/goose_config.py (path protocol, registry, mutation, guards).

Network-free and subprocess-free: the `goose info` runner is injected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentteams.goose_config as gc


class _FakeProc:
    def __init__(self, stdout: str):
        self.stdout = stdout


def _runner(stdout):
    def run(cmd, **kwargs):
        return _FakeProc(stdout)
    return run


# --- goose info parse (padding, missing token, spaced path) -----------------

def test_parse_goose_info_strips_padding_and_status_token():
    padded = "Config yaml:             /Users/x/.config/goose/config.yaml                  "
    assert gc.parse_goose_info_config_path(padded) == "/Users/x/.config/goose/config.yaml"
    missing = "Config yaml:   /tmp/fake/goose/config.yaml ... missing (can create)"
    assert gc.parse_goose_info_config_path(missing) == "/tmp/fake/goose/config.yaml"


def test_parse_goose_info_tolerates_spaces_in_path():
    line = r"Config yaml:   C:\Users\Jane Doe\AppData\Roaming\Block\goose\config\config.yaml"
    assert gc.parse_goose_info_config_path(line).endswith(r"Block\goose\config\config.yaml")
    assert "Jane Doe" in gc.parse_goose_info_config_path(line)


def test_parse_goose_info_none_when_absent():
    assert gc.parse_goose_info_config_path("Version:\n  1.37.0\n") is None


# --- resolve_goose_config_path (precedence + methods) -----------------------

def test_resolve_explicit_wins():
    path, method = gc.resolve_goose_config_path(explicit="/tmp/x.yaml", env={}, platform="linux")
    assert path == Path("/tmp/x.yaml") and method == "explicit"


def test_resolve_uses_goose_info():
    runner = _runner("Config yaml:   /opt/goose/config.yaml\n")
    path, method = gc.resolve_goose_config_path(env={}, platform="linux", runner=runner)
    assert path == Path("/opt/goose/config.yaml") and method == "goose-info"


def test_resolve_falls_back_to_xdg_when_goose_absent():
    def boom(cmd, **kwargs):
        raise FileNotFoundError("goose")
    path, method = gc.resolve_goose_config_path(
        env={"XDG_CONFIG_HOME": "/cfg"}, platform="linux", runner=boom,
    )
    assert path == Path("/cfg/goose/config.yaml") and method == "xdg"


def test_resolve_windows_default():
    def boom(cmd, **kwargs):
        raise FileNotFoundError("goose")
    path, method = gc.resolve_goose_config_path(
        env={"APPDATA": r"C:\Users\J\AppData\Roaming"}, platform="win32", runner=boom,
    )
    assert path.as_posix().endswith("Block/goose/config/config.yaml") and method == "platform-default"


# --- source registry --------------------------------------------------------

def test_builtin_sources_defaults():
    s = gc.load_sources(user_file=Path("/nonexistent.json"))
    assert s["ollama"].default_model == "qwen3.6:35b-a3b"
    assert s["openrouter"].default_model == "qwen/qwen3.6-35b-a3b"
    assert s["openrouter"].key_env == "OPENROUTER_API_KEY"


def test_user_sources_override(tmp_path):
    f = tmp_path / "goose-sources.json"
    f.write_text(json.dumps({"sources": {
        "groq": {"default_model": "llama-3.3-70b", "key_env": "GROQ_API_KEY"},
        "ollama": {"default_model": "llama3:8b"},
    }}), encoding="utf-8")
    s = gc.load_sources(user_file=f)
    assert s["groq"].default_model == "llama-3.3-70b"
    assert s["ollama"].default_model == "llama3:8b"        # user overrides builtin
    assert s["openrouter"].default_model == "qwen/qwen3.6-35b-a3b"  # builtin retained


# --- read/mutate config (preserve nested + comments; backup) ----------------

_CFG = (
    "# my goose config\n"
    "GOOSE_PROVIDER: openrouter\n"
    "GOOSE_MODEL: qwen/qwen3.6-35b-a3b\n"
    "GOOSE_MODE: auto\n"
    "extensions:\n"
    "  developer:\n"
    "    GOOSE_MODEL: nested-must-survive\n"
    "    enabled: true\n"
)


def test_read_config_ignores_nested_block(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    cfg, providers_block = gc.read_config(p)
    assert cfg == {"GOOSE_PROVIDER": "openrouter", "GOOSE_MODEL": "qwen/qwen3.6-35b-a3b", "GOOSE_MODE": "auto"}
    assert providers_block is None  # old (flat-key) schema — no providers:/active_provider: block


def test_set_provider_model_preserves_nested_and_comments(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    backup = gc.set_provider_model(p, provider="ollama", model="qwen3.6:35b-a3b")
    out = p.read_text(encoding="utf-8")
    assert "GOOSE_PROVIDER: ollama" in out
    assert "GOOSE_MODEL: qwen3.6:35b-a3b" in out
    assert "    GOOSE_MODEL: nested-must-survive" in out   # column-0 anchor left nested untouched
    assert "# my goose config" in out
    assert Path(backup).read_text(encoding="utf-8") == _CFG  # backup is the pre-edit content


def test_set_provider_model_creates_missing(tmp_path):
    p = tmp_path / "sub" / "config.yaml"
    backup = gc.set_provider_model(p, provider="openrouter", model="qwen/qwen3.6-35b-a3b")
    assert backup is None
    out = p.read_text(encoding="utf-8")
    assert "GOOSE_PROVIDER: openrouter" in out and "GOOSE_MODE: auto" in out


def test_set_provider_model_inserts_absent_key(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("GOOSE_MODE: auto\n", encoding="utf-8")
    gc.set_provider_model(p, provider="ollama", model="qwen3.6:35b-a3b")
    cfg, _ = gc.read_config(p)
    assert cfg["GOOSE_PROVIDER"] == "ollama" and cfg["GOOSE_MODEL"] == "qwen3.6:35b-a3b"


# --- new (providers:/active_provider:) config.yaml schema (2026-07-24) ------
#
# Shape matches the real live schema goose 1.37.0 `goose configure` writes: a `providers:`
# block with per-provider enabled/model/configured (interleaved key order, matching a real
# machine's config.yaml, not alphabetical), plus a top-level `active_provider:`.

_CFG_V2 = (
    "OLLAMA_HOST: http://localhost:11434\n"
    "GOOSE_MODE: auto\n"
    "providers:\n"
    "  ollama:\n"
    "    enabled: true\n"
    "    model: qwen3.6:35b-a3b\n"
    "    configured: true\n"
    "  openrouter:\n"
    "    enabled: true\n"
    "    model: qwen/qwen3.6-27b\n"
    "    configured: true\n"
    "active_provider: openrouter\n"
    "GOOSE_TELEMETRY_ENABLED: true\n"
)


def test_read_config_recognizes_v2_schema(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG_V2, encoding="utf-8")
    cfg, providers_block = gc.read_config(p)
    assert cfg.get("GOOSE_PROVIDER", "") == "" and cfg.get("GOOSE_MODEL", "") == ""  # no flat keys
    assert providers_block is not None
    assert providers_block["active_provider"] == "openrouter"
    assert providers_block["model"] == "qwen/qwen3.6-27b"
    assert providers_block["models_by_provider"]["ollama"] == "qwen3.6:35b-a3b"


def test_current_status_resolves_v2_schema(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG_V2, encoding="utf-8")
    status = gc.current_status(p)
    # Before this fix: config_provider/config_model were always "" against this exact shape.
    assert status["config_provider"] == "openrouter"
    assert status["config_model"] == "qwen/qwen3.6-27b"
    assert status["schema_source"] == "v2"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "ollama", "model": None},
        {"provider": None, "model": "qwen/qwen3.7-max"},  # `agentteams --goose-model` alone
        {"provider": "ollama", "model": "qwen3.6:35b-a3b"},
    ],
    ids=["provider-only", "model-only", "provider-and-model"],
)
def test_set_provider_model_refuses_v2_target(tmp_path, kwargs):
    # Regression guard (round-1 plan-level audit, empirically reproduced): before this fix,
    # each of these three shapes silently wrote dead GOOSE_PROVIDER:/GOOSE_MODEL: lines and
    # returned a backup path as if it had succeeded, while the real active_provider:/nested
    # model: were left completely unchanged. Cover all three shapes -- the round-2 audit found
    # the model-only shape (`agentteams --goose-model <id>` alone, the most common real
    # invocation per this CLI's own --help text) is structurally distinct from the others.
    p = tmp_path / "config.yaml"
    p.write_text(_CFG_V2, encoding="utf-8")
    with pytest.raises(gc.NewSchemaTargetError):
        gc.set_provider_model(p, **kwargs)
    # No write, no backup — the file must be byte-identical to before the call.
    assert p.read_text(encoding="utf-8") == _CFG_V2
    assert list(tmp_path.glob("config.yaml.bak-*")) == []


def test_set_provider_model_refusal_message_has_no_bare_none_for_dangling_active_provider(tmp_path):
    # Code-hygiene audit finding (2026-07-24): a providers: block with entries but no
    # active_provider: line at all parses to active_provider=None; the model-only CLI shape
    # (`agentteams --goose-model X` alone) also leaves the provider= kwarg None -- before this
    # fix the refusal message rendered the literal Python "None" (f"...providers:\n  {None}:")
    # instead of a usable pointer. Still refuses safely either way; this only guards the message.
    text = (
        "providers:\n"
        "  openrouter:\n"
        "    model: qwen/qwen3.6-27b\n"
        # no active_provider: line anywhere
    )
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(gc.NewSchemaTargetError) as excinfo:
        gc.set_provider_model(p, provider=None, model="qwen/qwen3.7-max")
    assert "None:" not in str(excinfo.value)
    assert "<provider>:" in str(excinfo.value)


def test_set_provider_model_still_works_on_v1_target_alongside_v2_support(tmp_path):
    # Non-regression: the refusal must not fire for an old-schema file.
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    backup = gc.set_provider_model(p, provider="ollama", model="qwen3.6:35b-a3b")
    assert backup is not None
    assert "GOOSE_PROVIDER: ollama" in p.read_text(encoding="utf-8")


# --- guards -----------------------------------------------------------------

def test_model_provider_mismatch():
    assert gc.model_provider_mismatch("ollama", "qwen/qwen3-30b-a3b")      # / under ollama
    assert gc.model_provider_mismatch("openrouter", "qwen/qwen3.6:35b-a3b")  # :tag under openrouter
    assert gc.model_provider_mismatch("openrouter", "bareword")            # not a vendor/slug
    assert gc.model_provider_mismatch("ollama", "qwen3.6:35b-a3b") is None  # valid ollama tag
    assert gc.model_provider_mismatch("openrouter", "qwen/qwen3.6-35b-a3b") is None  # valid slug
    assert gc.model_provider_mismatch("openrouter", "x/y:free") is None    # real :free variant ok


def test_env_override_and_status(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    assert gc.env_override({"GOOSE_MODEL": "x"}) == {"GOOSE_MODEL": "x"}
    assert gc.env_override({}) == {}
    status = gc.current_status(p, {"GOOSE_PROVIDER": "ollama"})
    assert status["config_provider"] == "openrouter"
    assert status["env_override"] == {"GOOSE_PROVIDER": "ollama"}
