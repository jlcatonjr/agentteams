"""#20/#25 hygiene: cryptography floor warning; codex_translation is template-authoritative."""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import fences
from agentteams.cli import signed_ledger

REPO = Path(__file__).resolve().parents[1]


def test_cryptography_floor_matches_the_pin():
    pins = json.loads((REPO / "references" / "dependency-pins.json").read_text())["pins"]
    (crypto,) = [p for p in pins if p["name"] == "cryptography"]
    assert ".".join(map(str, signed_ledger.CRYPTOGRAPHY_MIN_VERSION)) == crypto["version"]


def test_old_cryptography_warns_once(capsys, monkeypatch):
    monkeypatch.setattr(signed_ledger, "_CRYPTO_WARNED", [])
    signed_ledger._warn_if_old_cryptography("42.0.2")
    signed_ledger._warn_if_old_cryptography("42.0.2")
    err = capsys.readouterr().err
    assert err.count("below the pinned 50.0.0") == 1
    signed_ledger._warn_if_old_cryptography("50.0.1")
    assert capsys.readouterr().err == ""


def test_codex_translation_is_template_authoritative():
    assert "codex_translation" in fences._TEMPLATE_AUTHORITATIVE_FENCES


def test_shrunk_codex_translation_propagates_under_preserve():
    def doc(body: str) -> str:
        return ("# agent\n<!-- AGENTTEAMS:BEGIN codex_translation v=1 -->\n" + body
                + "\n<!-- AGENTTEAMS:END codex_translation -->\n")

    old = doc("tools: read, write, shell\nhandoffs: a, b, c, d")
    new = doc("tools: read\nhandoffs: a")
    mr = fences._merge_fenced_content(new, old, preserve_on_shrink=True, rel_path="x.toml")
    assert "handoffs: a\n" in mr.merged_content and "shell" not in mr.merged_content
