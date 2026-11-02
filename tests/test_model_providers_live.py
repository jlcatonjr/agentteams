"""Opt-in live calls (P06): one text turn and one tool-use round per configured preset.

Runs only with ``AGENTTEAMS_LIVE_MODEL_TESTS=1``, the preset's key in the environment and
``AGENTTEAMS_LIVE_<PRESET>_MODEL`` naming the model (no model id is written here). Asserts normalised shapes only.
"""

from __future__ import annotations

import os

import pytest

from agentteams import model_providers as mp
from tests.conftest import skip_no_live_model_tests

LIVE_PRESETS = ("anthropic", "openai", "openrouter", "xai", "gemini")
TOOL = mp.ToolSpec("add", "Add two integers and return the sum.", {
    "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]})


def _live(preset: str) -> tuple[mp.Provider, str]:
    model = os.environ.get(f"AGENTTEAMS_LIVE_{preset.upper()}_MODEL", "").strip()
    if not model:
        pytest.skip(f"set AGENTTEAMS_LIVE_{preset.upper()}_MODEL to run the live {preset} test")
    try:
        provider = mp.get_provider(preset)
    except mp.ModelProviderError as exc:
        pytest.skip(f"{preset}: {exc.message}")
    if os.environ.get("CI") and os.environ.get(mp.CI_ALLOW_ENV) != "1":
        pytest.skip(f"CI is set; {mp.CI_ALLOW_ENV}=1 allows live calls")
    return provider, model


@skip_no_live_model_tests
@pytest.mark.parametrize("preset", LIVE_PRESETS)
def test_live_text_turn(preset: str) -> None:
    provider, model = _live(preset)
    reply = mp.complete(provider, model, [{"role": "user", "content": [{"type": "text", "text": "Say OK."}]}],
                        max_tokens=256, timeout=60)
    assert reply.stop in mp.STOPS and reply.provider == preset
    assert all(b["type"] in ("text", "tool_use") for b in reply.content)
    assert reply.usage.output_tokens >= 0


@skip_no_live_model_tests
@pytest.mark.parametrize("preset", LIVE_PRESETS)
def test_live_tool_round(preset: str) -> None:
    provider, model = _live(preset)
    history = [{"role": "user", "content": [{"type": "text", "text": "Use the add tool to add 2 and 3."}]}]
    first = mp.complete(provider, model, history, tools=[TOOL], max_tokens=1024, timeout=60)
    uses = [b for b in first.content if b["type"] == "tool_use"]
    assert first.stop == "tool_use" and uses, first.raw_stop
    history.append({"role": "assistant", "content": list(first.content)})
    history.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": u["id"], "content": str(sum(int(v) for v in u["input"].values()))}
        for u in uses]})
    second = mp.complete(provider, model, history, tools=[TOOL], max_tokens=1024, timeout=60)
    assert second.stop in ("end_turn", "max_tokens") and second.content
