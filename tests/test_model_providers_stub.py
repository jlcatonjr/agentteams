"""Deterministic stub provider (P06): scripted, callable and echo modes; script files are custody-read data."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import model_providers as mp
from tests._model_provider_fakes import block_network, user

TOOL_REPLY = {"content": [{"type": "tool_use", "id": "t1", "name": "run_code", "input": {"src": "1"}}],
              "stop": "tool_use", "usage": {"input_tokens": 3, "output_tokens": 2, "cost": 0.5}}
TEXT_REPLY = {"content": [{"type": "text", "text": "done"}]}


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)        # any network attempt by the stub fails the test


def _call(p: mp.Provider, messages: list | None = None) -> mp.ModelReply:
    return mp.complete(p, "stub", messages or [user("hi")], max_tokens=16, timeout=5)


def test_scripted_order_and_exhaustion() -> None:
    stub = mp.StubProvider([TOOL_REPLY, TEXT_REPLY])
    p = mp.Provider(mp.PROVIDERS["stub"], None, "", stub)
    first = _call(p)
    assert first.stop == "tool_use" and first.content[0]["id"] == "t1" and first.usage == mp.Usage(0.5, 3, 2)
    assert _call(p).content == ({"type": "text", "text": "done"},)
    with pytest.raises(mp.ModelProviderError) as err:
        _call(p)
    assert err.value.kind == "protocol" and "exhausted" in err.value.message
    assert [r["messages"] for r in stub.requests] == [[user("hi")]] * 3


def test_callable_mode_sees_the_request() -> None:
    seen = []

    def answer(messages: list, system: str | None, tools: tuple) -> dict:
        seen.append((messages[-1]["content"][0]["text"], system, [t.name for t in tools]))
        return TEXT_REPLY

    p = mp.Provider(mp.PROVIDERS["stub"], None, "", mp.StubProvider(answer))
    tool = mp.ToolSpec("run_code", "d", {"type": "object"})
    mp.complete(p, "stub", [user("q")], system="s", tools=[tool], max_tokens=8, timeout=5)
    assert seen == [("q", "s", ["run_code"])]


def test_callable_bad_reply_is_protocol() -> None:
    p = mp.Provider(mp.PROVIDERS["stub"], None, "", mp.StubProvider(lambda m, s, t: {"content": [{"type": "x"}]}))
    with pytest.raises(mp.ModelProviderError) as err:
        _call(p)
    assert err.value.kind == "protocol"


def test_echo_is_deterministic() -> None:
    a = _call(mp.get_provider("stub"), [user("same input")])
    b = _call(mp.get_provider("stub"), [user("same input")])
    c = _call(mp.get_provider("stub"), [user("other input")])
    assert a == b and a.content[0]["text"].startswith("stub:") and len(a.content[0]["text"]) == 17
    assert a.content != c.content
    assert all(c in "0123456789abcdef" for c in a.content[0]["text"][5:])
    assert a.stop == "end_turn" and a.usage.input_tokens == len("same input") // 4


def test_script_file(tmp_path: Path) -> None:
    f = tmp_path / "script.json"
    f.write_text(json.dumps([TEXT_REPLY]), encoding="utf-8")
    p = mp.get_provider("stub", script_path=f)
    assert _call(p).content == ({"type": "text", "text": "done"},)


@pytest.mark.parametrize("content", ['[{"content": [], "content": []}]', '[{"content": [], "usage": {"cost": NaN}}]',
                                     '{"content": []}', '[{"content": [{"type": "image"}]}]', "not json",
                                     '[{"content": [], "stop": "maybe"}]', '[{"content": [], "extra": 1}]',
                                     '[{"content": [], "usage": {"cost": true}}]',
                                     '[{"content": [], "usage": {"input_tokens": -1}}]'])
def test_bad_script_files(tmp_path: Path, content: str) -> None:
    f = tmp_path / "script.json"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("stub", script_path=f)
    assert err.value.kind == "protocol"


def test_symlinked_and_oversize_script_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps([TEXT_REPLY]), encoding="utf-8")
    link = tmp_path / "link.json"
    os.symlink(real, link)
    with pytest.raises(mp.ModelProviderError):
        mp.get_provider("stub", script_path=link)
    big = tmp_path / "big.json"
    big.write_text(json.dumps([{"content": [{"type": "text", "text": "x" * (70 * 1024)}]}]), encoding="utf-8")
    with pytest.raises(mp.ModelProviderError):
        mp.get_provider("stub", script_path=big)


def test_script_path_only_for_stub(tmp_path: Path) -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("anthropic", environ={"ANTHROPIC_API_KEY": "k-123456"}, script_path=tmp_path / "s.json")
    assert err.value.kind == "config" and "only valid for the stub" in err.value.message
