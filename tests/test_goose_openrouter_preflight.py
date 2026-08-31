"""Offline unit tests for scripts/goose-openrouter-preflight.py.

All tests are deterministic and network-free: the OpenRouter catalog is a fixture
and `fetch_catalog`/`live_probe` are never invoked (the live probe needs a key and
a goose subprocess). Mirrors the importlib loader pattern of test_verify_env.py.
"""
from __future__ import annotations

import importlib.util
import json
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "goose-openrouter-preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("goose_or_preflight", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not perform any network I/O
    return module


gop = _load()

# A small, deterministic stand-in for the OpenRouter /models payload list.
FIXTURE = [
    {"id": "qwen/qwen3.6-35b-a3b", "supported_parameters": ["tools", "reasoning"]},
    {"id": "qwen/qwen3-30b-a3b", "supported_parameters": ["tools"]},
    {"id": "vendor/vision-only", "supported_parameters": ["response_format"]},
    {"id": "qwen/qwen3-next-80b-a3b-instruct:free", "supported_parameters": ["tools"]},
]


# --- parse_goose_config -----------------------------------------------------

def test_parse_config_reads_top_level_and_ignores_nested_block():
    text = (
        "GOOSE_PROVIDER: openrouter\n"
        "GOOSE_MODEL: qwen/qwen3.6:35b-a3b\n"
        "GOOSE_MODE: auto\n"
        "extensions:\n"
        "  developer:\n"
        "    GOOSE_MODEL: should-be-ignored\n"
    )
    cfg = gop.parse_goose_config(text)
    assert cfg == {
        "GOOSE_PROVIDER": "openrouter",
        "GOOSE_MODEL": "qwen/qwen3.6:35b-a3b",
        "GOOSE_MODE": "auto",
    }


# --- parse_goose_providers_block (newer providers:/active_provider: schema) -

_V2_TEXT = (
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
)


def test_parse_providers_block_matches_real_schema_shape():
    block = gop.parse_goose_providers_block(_V2_TEXT)
    assert block["active_provider"] == "openrouter"
    assert block["model"] == "qwen/qwen3.6-27b"
    assert block["models_by_provider"] == {
        "ollama": "qwen3.6:35b-a3b",
        "openrouter": "qwen/qwen3.6-27b",
    }


def test_parse_providers_block_returns_none_for_old_schema():
    assert gop.parse_goose_providers_block("GOOSE_PROVIDER: openrouter\nGOOSE_MODEL: x\n") is None


def test_parse_providers_block_tolerates_blank_and_comment_lines():
    text = (
        "providers:\n"
        "\n"
        "  openrouter:\n"
        "    # a comment inside the block\n"
        "    enabled: true\n"
        "\n"
        "    model: qwen/qwen3.6-27b\n"
        "active_provider: openrouter\n"
    )
    block = gop.parse_goose_providers_block(text)
    assert block["model"] == "qwen/qwen3.6-27b"


def test_parse_providers_block_dangling_active_provider_resolves_to_none_model():
    text = (
        "providers:\n"
        "  ollama:\n"
        "    model: qwen3.6:35b-a3b\n"
        "active_provider: openrouter\n"  # names a provider absent from the block above
    )
    block = gop.parse_goose_providers_block(text)
    assert block["active_provider"] == "openrouter"
    assert block["model"] is None


def test_parse_providers_block_matches_by_key_name_not_position():
    # Real files interleave enabled/configured around model in varying order per provider —
    # a positional parser would silently grab the wrong field.
    text = (
        "providers:\n"
        "  openrouter:\n"
        "    configured: true\n"
        "    model: qwen/qwen3.6-27b\n"
        "    enabled: true\n"
        "active_provider: openrouter\n"
    )
    assert gop.parse_goose_providers_block(text)["model"] == "qwen/qwen3.6-27b"


# --- resolve_models (env overrides config; divergence) ----------------------

def test_resolve_models_env_overrides_and_flags_divergence():
    r = gop.resolve_models(
        env={"GOOSE_MODEL": "qwen/qwen3.6-35b-a3b"},
        config={"GOOSE_PROVIDER": "openrouter", "GOOSE_MODEL": "qwen/qwen3.6:35b-a3b"},
    )
    assert r["provider"] == "openrouter"
    assert r["config_model"] == "qwen/qwen3.6:35b-a3b"   # what plain `goose run` uses
    assert r["env_model"] == "qwen/qwen3.6-35b-a3b"      # goose-or override
    assert r["primary_model"] == "qwen/qwen3.6:35b-a3b"  # config is primary
    assert r["primary_source"] == "config.yaml"
    assert r["diverges"] is True


def test_resolve_models_no_divergence_when_env_unset():
    r = gop.resolve_models(env={}, config={"GOOSE_PROVIDER": "openrouter", "GOOSE_MODEL": "x"})
    assert r["diverges"] is False


def test_resolve_models_schema_source_v2_preferred_over_v1():
    # A providers_block, when present, wins over flat keys — active_provider: is what
    # current goose itself actually reads.
    r = gop.resolve_models(
        env={},
        config={"GOOSE_PROVIDER": "ollama", "GOOSE_MODEL": "stale-flat-key"},
        providers_block={"active_provider": "openrouter", "model": "qwen/qwen3.6-27b",
                          "models_by_provider": {"openrouter": "qwen/qwen3.6-27b"}},
    )
    assert r["schema_source"] == "v2"
    assert r["provider"] == "openrouter"
    assert r["config_model"] == "qwen/qwen3.6-27b"


def test_resolve_models_schema_source_v1_when_no_providers_block():
    r = gop.resolve_models(env={}, config={"GOOSE_PROVIDER": "openrouter", "GOOSE_MODEL": "x"})
    assert r["schema_source"] == "v1"


def test_resolve_models_schema_source_none_when_neither():
    r = gop.resolve_models(env={}, config={})
    assert r["schema_source"] == "none"


# --- validate_model (tools is a gate) ---------------------------------------

def test_validate_model_cases():
    idx = gop.index_catalog(FIXTURE)
    assert gop.validate_model("qwen/qwen3.6-35b-a3b", idx)["ok"] is True
    absent = gop.validate_model("qwen/qwen3.6:35b-a3b", idx)
    assert absent["exists"] is False and absent["ok"] is False
    vision = gop.validate_model("vendor/vision-only", idx)
    assert vision["exists"] is True and vision["supports_tools"] is False and vision["ok"] is False


# --- suggest_fix (guarded colon->hyphen) ------------------------------------

def test_suggest_fix_colon_to_hyphen():
    idx = gop.index_catalog(FIXTURE)
    assert gop.suggest_fix("qwen/qwen3.6:35b-a3b", idx) == "qwen/qwen3.6-35b-a3b"


def test_suggest_fix_does_not_rewrite_legit_free_variant():
    idx = gop.index_catalog(FIXTURE)
    # base exists -> a real (if offline) :free variant must not be hyphen-rewritten
    assert gop.suggest_fix("qwen/qwen3-30b-a3b:free", idx) is None
    # suffix is a known variant -> never auto-suggest
    assert gop.suggest_fix("qwen/qwen3.6:free", idx) is None
    # already valid -> no suggestion
    assert gop.suggest_fix("qwen/qwen3.6-35b-a3b", idx) is None


# --- classify_goose_output (exit code is NOT a signal; classify text) -------

_NONCE_SENTINEL = gop._SENTINEL_PREFIX + "deadbeefdeadbeefdead"


@pytest.mark.parametrize("text,verdict,code", [
    (f"...{_NONCE_SENTINEL}...", "pass", 0),
    ("Bad request (400): qwen/qwen3.6:35b-a3b is not a valid model ID", "invalid-model", 1),
    ("Error: 401 Unauthorized — invalid api key", "auth-error", 2),
    ("I've reached the maximum number of actions allowed.", "inconclusive", 2),
    ("(model replied but did nothing)", "early-stop", 1),
])
def test_classify_goose_output(text, verdict, code):
    res = gop.classify_goose_output(text, _NONCE_SENTINEL)
    assert res["verdict"] == verdict
    assert res["exit"] == code


def test_a_narrated_prompt_echo_cannot_pass_the_probe():
    """The 2026-08-08 false-pass: chat-mode narration echoed a guessable sentinel. The
    sentinel is now a per-run nonce that exists only in a temp file — a transcript that
    merely repeats the probe PROMPT (which names the path, never the contents) must fail."""
    text, sentinel, path = gop._probe_fixture()
    try:
        assert sentinel not in text, "the prompt must never contain the sentinel"
        res = gop.classify_goose_output(f"I would run: {text}", sentinel)
        assert res["verdict"] != "pass"
        with open(path, encoding="utf-8") as fh:
            executed_output = fh.read()
        assert gop.classify_goose_output(executed_output, sentinel)["verdict"] == "pass"
    finally:
        import os as _os
        _os.unlink(path)


# --- offline syntax heuristic ----------------------------------------------

def test_offline_syntax_suspect():
    assert gop.offline_syntax_suspect("openrouter", "qwen/qwen3.6:35b-a3b") is True
    assert gop.offline_syntax_suspect("openrouter", "qwen/qwen3.6-35b-a3b") is False
    assert gop.offline_syntax_suspect("openrouter", "x/y:free") is False  # known variant
    assert gop.offline_syntax_suspect("ollama", "qwen3.6:35b-a3b") is False  # ollama tag is fine


# --- endpoint listing (--providers) -----------------------------------------

_ENDPOINTS = [
    {"provider_name": "Alibaba", "supported_parameters": ["tools", "reasoning"],
     "context_length": 262144, "quantization": None},
    {"provider_name": "Chutes", "supported_parameters": ["tools"],
     "context_length": 262144, "quantization": "fp8"},
    {"provider_name": "VisionOnly", "supported_parameters": ["response_format"],
     "context_length": 8192, "quantization": "fp8"},
]


def test_format_endpoints_marks_tools_support_per_backend():
    out = gop.format_endpoints("qwen/qwen3.6-27b", _ENDPOINTS)
    assert "Alibaba" in out and "Chutes" in out
    # tools=yes/NO must reflect each backend's own supported_parameters
    alibaba_line = next(l for l in out.splitlines() if "Alibaba" in l)
    vision_line = next(l for l in out.splitlines() if "VisionOnly" in l)
    assert "tools=yes" in alibaba_line
    assert "tools=NO" in vision_line


def test_format_endpoints_warns_tools_flag_is_not_reliability():
    # The whole point of the --providers surface: tools=yes does NOT mean the backend
    # extracts the model's native tool-call template correctly (that's the dead-turn bug).
    out = gop.format_endpoints("m", _ENDPOINTS)
    assert "NOT equally reliable" in out
    assert "goose-openrouter-route-proxy.py" in out


def test_main_providers_lists_endpoints_without_reading_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gop, "fetch_endpoints", lambda model, timeout: _ENDPOINTS)
    # No --config passed and no config needed: --providers short-circuits before _build_report.
    def _boom(*a, **k):
        raise AssertionError("--providers must not fetch the model catalog")
    monkeypatch.setattr(gop, "fetch_catalog", _boom)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gop.main(["--providers", "qwen/qwen3.6-27b"])
    assert code == 0
    assert "Alibaba" in buf.getvalue()


def test_main_providers_json_shape(monkeypatch):
    monkeypatch.setattr(gop, "fetch_endpoints", lambda model, timeout: _ENDPOINTS)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gop.main(["--providers", "m", "--json"])
    report = json.loads(buf.getvalue())
    assert code == 0
    assert report["model"] == "m"
    assert [e["provider_name"] for e in report["endpoints"]] == ["Alibaba", "Chutes", "VisionOnly"]


def test_main_providers_setup_error_exits_2(monkeypatch, capsys):
    def _raise(model, timeout):
        raise gop.SetupError("no such model")
    monkeypatch.setattr(gop, "fetch_endpoints", _raise)
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = gop.main(["--providers", "bogus/model"])
    assert code == 2
    assert "no such model" in buf.getvalue()


# --- end-to-end via main(), fetch stubbed, no network, no secret leak -------

def _run_main(monkeypatch, tmp_path, model, argv_extra=()):
    monkeypatch.setattr(gop, "fetch_catalog", lambda timeout: FIXTURE)
    monkeypatch.delenv("GOOSE_PROVIDER", raising=False)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"GOOSE_PROVIDER: openrouter\nGOOSE_MODEL: {model}\nGOOSE_MODE: auto\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gop.main(["--config", str(cfg), "--json"])
    return code, json.loads(buf.getvalue())


def test_main_reports_invalid_model_with_fix(monkeypatch, tmp_path):
    code, report = _run_main(monkeypatch, tmp_path, "qwen/qwen3.6:35b-a3b")
    assert code == 1
    assert report["fix"] == "qwen/qwen3.6-35b-a3b"
    assert report["checks"][0]["exists"] is False
    # secret hygiene: nothing key-shaped is serialized
    assert "OPENROUTER_API_KEY" not in json.dumps(report)


def test_main_passes_for_valid_model(monkeypatch, tmp_path):
    code, report = _run_main(monkeypatch, tmp_path, "qwen/qwen3.6-35b-a3b")
    assert code == 0
    assert report["checks"][0]["ok"] is True
    assert report["fix"] is None


def test_main_offline_does_not_fetch(monkeypatch, tmp_path):
    def _boom(timeout):
        raise AssertionError("fetch_catalog must not be called in --offline mode")
    monkeypatch.setattr(gop, "fetch_catalog", _boom)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("GOOSE_PROVIDER: openrouter\nGOOSE_MODEL: qwen/qwen3.6:35b-a3b\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gop.main(["--config", str(cfg), "--offline", "--json"])
    assert code == 1  # colon suffix flagged by syntax heuristic


def test_format_human_does_not_advise_fix_flag_that_would_refuse_on_v2_schema(monkeypatch, tmp_path):
    # Close-out audit finding (2026-07-24): the default (non---fix) human report used to
    # unconditionally print "apply with: ... --fix" even when config.yaml uses the newer
    # providers:/active_provider: schema, where --fix (per test_main_fix_refuses_on_v2_schema
    # above) actually refuses. Advising a command known to fail is a real UX bug even though
    # nothing unsafe happens if the user follows it.
    monkeypatch.setattr(gop, "fetch_catalog", lambda timeout: FIXTURE)
    monkeypatch.delenv("GOOSE_PROVIDER", raising=False)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "providers:\n"
        "  openrouter:\n"
        "    model: qwen/qwen3.6:35b-a3b\n"  # invalid colon slug -> a fix is suggested
        "active_provider: openrouter\n",
        encoding="utf-8",
    )
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = gop.main(["--config", str(cfg)])  # no --fix, no --json: human path
    out = buf.getvalue()
    assert code == 1
    assert "→ fix: change GOOSE_MODEL to 'qwen/qwen3.6-35b-a3b'" in out
    assert "apply with: python scripts/goose-openrouter-preflight.py --fix" not in out
    assert "--fix would refuse" in out


def test_main_fix_refuses_on_v2_schema(monkeypatch, tmp_path):
    # 2026-07-24: --fix against a providers:/active_provider: config must refuse (report
    # fix_refused, leave fix=None, write nothing) rather than silently no-op or corrupt the
    # file with a dead top-level GOOSE_MODEL: line goose's new-schema reader never consults.
    monkeypatch.setattr(gop, "fetch_catalog", lambda timeout: FIXTURE)
    monkeypatch.delenv("GOOSE_PROVIDER", raising=False)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    body = (
        "providers:\n"
        "  openrouter:\n"
        "    enabled: true\n"
        "    model: qwen/qwen3.6:35b-a3b\n"  # invalid colon slug -> suggest_fix finds a fix
        "    configured: true\n"
        "active_provider: openrouter\n"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(body, encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gop.main(["--config", str(cfg), "--fix", "--json"])
    report = json.loads(buf.getvalue())
    assert code == 1
    assert report["resolved"]["schema_source"] == "v2"
    assert report["fix"] is None
    assert "providers:/active_provider:" in report["fix_refused"]
    assert "qwen/qwen3.6-35b-a3b" in report["fix_refused"]  # names the suggested fix value
    assert "openrouter" in report["fix_refused"]  # names the active provider to edit
    assert cfg.read_text(encoding="utf-8") == body  # untouched, no backup written
    assert list(tmp_path.glob("config.yaml.bak-*")) == []


def test_main_fix_refusal_message_has_no_bare_none_for_dangling_active_provider(monkeypatch, tmp_path):
    # Code-hygiene audit finding (2026-07-24), mirrors the goose_config.py regression test: a
    # providers: block with entries but no active_provider: line parses to active_provider=None,
    # which the (unreachable-by-default) fix_refused message would render as literal "None:".
    # Reachable here via an env override (GOOSE_PROVIDER/GOOSE_MODEL) supplying the provider/model
    # resolve_models() needs, since the dangling file itself resolves provider to "".
    monkeypatch.setattr(gop, "fetch_catalog", lambda timeout: FIXTURE)
    monkeypatch.setenv("GOOSE_PROVIDER", "openrouter")
    monkeypatch.setenv("GOOSE_MODEL", "qwen/qwen3.6:35b-a3b")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("providers:\n  openrouter:\n    model: qwen/qwen3.6-27b\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gop.main(["--config", str(cfg), "--fix", "--json"])
    report = json.loads(buf.getvalue())
    assert code == 1
    assert report["resolved"]["schema_source"] == "v2"
    assert "None:" not in report["fix_refused"]
    assert "<provider>:" in report["fix_refused"]
