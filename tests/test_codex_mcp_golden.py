"""Codex MCP config output is byte-identical across the shared-splice refactor (@security step-12b, P2-D).

The fixture was generated from ``codex_mcp_emit`` BEFORE it was generalised for Grok
(tmp/by-week/2026-W42/grok-integration.plan.md §12). Never regenerate it to make a failure pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.codex_mcp_emit import emit_codex_mcp_config

_CASES = json.loads((Path(__file__).parent / "fixtures" / "codex_mcp_golden" / "cases.json").read_text())


@pytest.mark.parametrize("name", sorted(_CASES))
def test_codex_output_is_byte_identical(name: str, tmp_path: Path) -> None:
    case = _CASES[name]
    (tmp_path / ".codex").mkdir()
    if case["existing"]:
        (tmp_path / ".codex" / "config.toml").write_text(case["existing"])
    res = emit_codex_mcp_config(servers=case["servers"], features=["codex:mcp"], output_root=tmp_path)
    assert (tmp_path / ".codex" / "config.toml").read_text() == case["output"]
    assert (res.wired, res.not_wired, res.dropped_unmanaged, res.errors) == (
        case["wired"], case["not_wired"], case["dropped_unmanaged"], case["errors"])
