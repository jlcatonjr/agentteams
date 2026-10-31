"""P20 test plan: the cross-orchestrator requests under ``references/cross-orchestrator-requests/`` keep the
Protocol-3 header shape of ``2026-W40-sandbox-launch.md`` and carry no absolute home path; the 2026-W44 WASM request
has its P20 substrate-switch section filled (not the P19 placeholder) and names what it projects exactly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams import sandbox_projection as sp

DIR = Path(__file__).resolve().parents[1] / "references" / "cross-orchestrator-requests"
REQUESTS = sorted(DIR.glob("*.md"))
W44 = DIR / "2026-W44-wasm-grant-spec-and-substrate.md"
_HOME = re.compile(r"(/Users/|/home/)[A-Za-z0-9._-]+")


def test_the_requests_exist():
    assert W44 in REQUESTS and (DIR / "2026-W40-sandbox-launch.md") in REQUESTS


@pytest.mark.parametrize("path", REQUESTS, ids=lambda p: p.name)
def test_protocol_three_headers_and_no_home_paths(path):
    text = path.read_text(encoding="utf-8")
    head = text.split("\n---", 1)[0]
    for label in ("**From:**", "**Date:**", "**Protocol:** 3", "**Clearance:**", "**Channel:**"):
        assert label in head, f"{path.name}: missing {label}"
    assert not _HOME.search(text), f"{path.name}: absolute home path"


def test_the_w44_request_has_the_substrate_section_and_an_approval():
    text = W44.read_text(encoding="utf-8")
    section = text.split("## 4. Substrate switch (P20)", 1)[1].split("\n## Honesty ceilings", 1)[0]
    assert "Placeholder" not in section
    assert "## Approval" in text and "Request B" in text.split("## Approval", 1)[1]
    for needle in (sp.PROJECTION_REL, sp.SCHEMA_ID, "require_enforced", "sandbox-wasm-off:model-substrate",
                   "paid_cell.mjs:73", "launch_wave.mjs:150", "wasm_runner", "os_sandbox", "wasm_sandbox"):
        assert needle in section, needle
    for field in sp.FIELDS:  # the example names every projected field
        assert f'"{field}"' in section, field
