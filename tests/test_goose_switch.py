"""CLI integration tests for the goose source/model switch (agentteams.cli.app.main).

Hermetic: every invocation passes --goose-config <tmp> so the `goose info` subprocess
is never reached (explicit path wins), and no network is touched.
"""
from __future__ import annotations

import pytest

from agentteams.cli.app import main


def _cfg(tmp_path, body="GOOSE_PROVIDER: openrouter\nGOOSE_MODEL: qwen/qwen3.6-35b-a3b\nGOOSE_MODE: auto\n"):
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_goose_show(tmp_path, capsys):
    p = _cfg(tmp_path)
    code = main(["--goose-show", "--goose-config", str(p)])
    out = capsys.readouterr().out
    assert code == 0
    assert "GOOSE_PROVIDER: openrouter" in out
    assert "known sources" in out and "ollama" in out


def test_switch_source_applies_default_model(tmp_path, capsys):
    p = _cfg(tmp_path)
    code = main(["--goose-source", "ollama", "--goose-config", str(p)])
    assert code == 0
    text = p.read_text(encoding="utf-8")
    assert "GOOSE_PROVIDER: ollama" in text
    assert "GOOSE_MODEL: qwen3.6:35b-a3b" in text  # ollama default applied


def test_switch_model_only_keeps_provider(tmp_path):
    p = _cfg(tmp_path, "GOOSE_PROVIDER: openrouter\nGOOSE_MODEL: qwen/qwen3.6-35b-a3b\n")
    code = main(["--goose-model", "qwen/qwen3-30b-a3b", "--goose-config", str(p)])
    assert code == 0
    text = p.read_text(encoding="utf-8")
    assert "GOOSE_PROVIDER: openrouter" in text and "GOOSE_MODEL: qwen/qwen3-30b-a3b" in text


def test_provider_aware_mismatch_rejected(tmp_path, capsys):
    p = _cfg(tmp_path)
    before = p.read_text(encoding="utf-8")
    code = main(["--goose-source", "ollama", "--goose-model", "qwen/qwen3-30b-a3b", "--goose-config", str(p)])
    assert code == 2
    assert "No change written" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == before  # unchanged


def test_openrouter_colon_tag_rejected(tmp_path, capsys):
    p = _cfg(tmp_path)
    code = main(["--goose-source", "openrouter", "--goose-model", "qwen/qwen3.6:35b-a3b", "--goose-config", str(p)])
    assert code == 2
    assert "hyphen" in capsys.readouterr().out.lower()


def test_unknown_source_errors(tmp_path, capsys):
    p = _cfg(tmp_path)
    code = main(["--goose-source", "frobnicate", "--goose-config", str(p)])
    assert code == 2
    assert "unknown source" in capsys.readouterr().out.lower()


def test_env_override_masking_warning(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GOOSE_MODEL", "qwen/qwen3.6-35b-a3b")
    p = _cfg(tmp_path)
    main(["--goose-source", "openrouter", "--goose-config", str(p)])
    out = capsys.readouterr().out
    assert "env override active" in out and "masked" in out


def test_goose_switch_mutually_exclusive_with_self(tmp_path):
    # parser.error raises SystemExit(2)
    with pytest.raises(SystemExit) as exc:
        main(["--goose-show", "--self"])
    assert exc.value.code == 2
