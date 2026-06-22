"""Offline unit tests for scripts/goose-openrouter-preflight.py.

All tests are deterministic and network-free: the OpenRouter catalog is a fixture
and `fetch_catalog`/`live_probe` are never invoked (the live probe needs a key and
a goose subprocess). Mirrors the importlib loader pattern of test_verify_env.py.
"""
from __future__ import annotations

import importlib.util
import json
import io
from contextlib import redirect_stdout
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

@pytest.mark.parametrize("text,verdict,code", [
    (f"...{gop._SENTINEL}...", "pass", 0),
    ("Bad request (400): qwen/qwen3.6:35b-a3b is not a valid model ID", "invalid-model", 1),
    ("Error: 401 Unauthorized — invalid api key", "auth-error", 2),
    ("I've reached the maximum number of actions allowed.", "inconclusive", 2),
    ("(model replied but did nothing)", "early-stop", 1),
])
def test_classify_goose_output(text, verdict, code):
    res = gop.classify_goose_output(text)
    assert res["verdict"] == verdict
    assert res["exit"] == code


# --- offline syntax heuristic ----------------------------------------------

def test_offline_syntax_suspect():
    assert gop.offline_syntax_suspect("openrouter", "qwen/qwen3.6:35b-a3b") is True
    assert gop.offline_syntax_suspect("openrouter", "qwen/qwen3.6-35b-a3b") is False
    assert gop.offline_syntax_suspect("openrouter", "x/y:free") is False  # known variant
    assert gop.offline_syntax_suspect("ollama", "qwen3.6:35b-a3b") is False  # ollama tag is fine


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
