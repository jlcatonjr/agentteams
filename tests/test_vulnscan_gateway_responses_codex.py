"""The gateway's Responses route for Codex (phase 4): the shapes the pinned Codex sends, deny by default otherwise.

Observed with Codex 0.162.1 against a loopback stub (2026-10-24): no ``max_output_tokens`` (the gateway then
reserves ``--responses-reserve-tokens``), a ``client_metadata`` object of ids, MCP tools grouped in a
``namespace`` or deferred behind a client-run ``tool_search``, and tools carried in an ``additional_tools`` input
item for code-mode models. Driven like ``test_vulnscan_gateway_dialects.py``: no port, no network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentteams.vulnscan import gateway
from agentteams.vulnscan import gateway_dialects as d
from tests.test_vulnscan_gateway_dialects import (  # noqa: F401
    MODEL,
    Upstream,
    _drive,
    _handler,
    _json,
    _request,
    upstream,
)

NAMESPACE = {"type": "namespace", "name": "mcp__vulnscan", "description": "Tools in the mcp__vulnscan namespace.",
             "tools": [{"type": "function", "name": "get_summary", "parameters": {"type": "object"}}]}
TOOL_SEARCH = {"type": "tool_search", "execution": "client", "description": "search deferred tools",
               "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}
METADATA = {"thread_id": "t-1", "turn_id": "u-1", "x-codex-installation-id": "i-1",
            "x-codex-turn-metadata": json.dumps({"sandbox": "seccomp", "sandbox_mode": "read-only"})}


def codex_body(**extra: Any) -> bytes:
    """A body shaped like Codex 0.162.1's (``max_output_tokens`` absent)."""
    body = {"model": MODEL, "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "x"}]}],
            "tools": [{"type": "function", "name": "list_mcp_resources", "parameters": {}}, NAMESPACE, TOOL_SEARCH,
                      {"type": "custom", "name": "apply_patch", "format": {"type": "grammar", "syntax": "lark",
                                                                           "definition": "start: x"}}],
            "tool_choice": "auto", "parallel_tool_calls": True, "reasoning": {"effort": "medium"}, "store": False,
            "stream": True, "include": ["reasoning.encrypted_content"], "prompt_cache_key": "t-1",
            "text": {"verbosity": "low"}, "client_metadata": METADATA, **extra}
    return json.dumps({k: v for k, v in body.items() if v is not None}).encode()


def check(raw: bytes, reserve: int = 0) -> d.Checked:
    return d.check_request(d.RESPONSES, raw, models={MODEL}, max_output_tokens=32_000, responses_reserve_tokens=reserve)


def test_codex_bodies_need_the_reserve_and_pass_with_it() -> None:
    with pytest.raises(d.PolicyError, match="max_output_tokens is required"):
        check(codex_body())                                         # the old rule, still the default
    checked = check(codex_body(), reserve=32_000)
    assert checked.max_output == 32_000 and json.loads(checked.body)["store"] is False
    assert json.loads(checked.body)["max_output_tokens"] == 32_000  # rc.4 Low 4: the reserve is enforced upstream too
    assert check(codex_body(max_output_tokens=500), reserve=32_000).max_output == 500
    with pytest.raises(d.PolicyError, match="between 1 and 32000"):
        check(codex_body(max_output_tokens=10 ** 6), reserve=32_000)


@pytest.mark.parametrize(("tools", "why"), [
    ([{**NAMESPACE, "tools": [{**NAMESPACE}]}], "tool type 'namespace' is not allowed"),     # no nesting
    ([{**NAMESPACE, "tools": [{"type": "web_search"}]}], "server-side tool 'web_search'"),
    ([{**NAMESPACE, "tools": [TOOL_SEARCH]}], "tool type 'tool_search' is not allowed"),
    ([{**TOOL_SEARCH, "execution": "server"}], "tool_search with execution 'server'"),
    ([{"type": "tool_search"}], "tool_search with execution None"),
    ([{"type": "mcp", "server_url": "https://x.invalid"}], "server-side tool 'mcp'"),
])
def test_tool_shapes_outside_the_allowlist_are_refused(tools: list, why: str) -> None:
    with pytest.raises(d.PolicyError, match=why):
        check(codex_body(tools=tools), reserve=1000)


@pytest.mark.parametrize("meta", [{"a": {"nested": "x"}}, {"a": 1}, {f"k{i}": "v" for i in range(33)}, {"a": "x" * 5000},
                                  {"bad key": "v"}, ["x"]])
def test_client_metadata_must_be_a_small_flat_object_of_strings(meta: object) -> None:
    with pytest.raises(d.PolicyError, match="client_metadata must be an object"):
        check(codex_body(client_metadata=meta), reserve=1000)


def test_tools_carried_in_the_input_are_checked_too() -> None:
    item = {"type": "additional_tools", "role": "developer",
            "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "custom", "name": "exec"}]}]}
    check(codex_body(input=[item]), reserve=1000)                   # allowed types (the self-test judges the names)
    bad = {**item, "tools": [{"type": "web_search"}]}
    with pytest.raises(d.PolicyError, match="server-side tool 'web_search'"):
        check(codex_body(input=[bad]), reserve=1000)


def test_the_handler_reserves_the_configured_amount(upstream: Upstream) -> None:  # noqa: F811
    upstream.script.append(_json({"object": "response", "usage": {"input_tokens": 3, "output_tokens": 2, "cost": 0.0001}}))
    meter = gateway.Meter(1.0, None, max_tokens=40_000)
    assert _drive(_handler(meter, responses_reserve_tokens=32_000), _request("/v1/responses", codex_body()))[0] == 200
    assert meter.reserved_tokens == 0 and meter.completion_tokens == 2
    small = gateway.Meter(1.0, None, max_tokens=20_000)             # the reservation alone crosses the token cap
    status, body = _drive(_handler(small, responses_reserve_tokens=32_000), _request("/v1/responses", codex_body()))
    assert status == 402 and "tokens" in json.loads(body)["error"]["message"]
    status, _ = _drive(_handler(gateway.Meter(1.0, None)), _request("/v1/responses", codex_body()))
    assert status == 400                                            # no reserve configured: refused as before


def test_the_reserve_flag_is_bounded_by_the_ceiling(tmp_path: Any) -> None:
    key = tmp_path / "k.env"
    key.write_text("OPENAI_API_KEY=sk-test-not-real\n")
    base = ["--model", MODEL, "--listen", "127.0.0.1:9", "--dialects", "responses", "--upstream-responses",
            "https://resp.invalid/v1", "--key-file-responses", str(key), "--max-output-tokens", "1000"]
    for bad in ("-1", "1001"):
        with pytest.raises(SystemExit) as exc:
            gateway.main([*base, "--responses-reserve-tokens", bad])
        assert exc.value.code == 2
