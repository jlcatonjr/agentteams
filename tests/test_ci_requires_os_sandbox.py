"""CI keeps requiring a usable OS sandbox where the enforcement tests run (@security, 2026-10-21).

Without the variable, a broken ``sandbox-exec`` on the runner would turn every Seatbelt enforcement test into a silent
skip (``tests/conftest.py::_require_os_sandbox``).
"""
from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _job_block(name: str) -> str:
    text = CI.read_text(encoding="utf-8")
    m = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text)
    assert m, f"job {name} not found in ci.yml"
    return m.group(1)


def test_macos_seatbelt_job_requires_seatbelt_and_blocks() -> None:
    block = _job_block("macos-seatbelt")
    assert re.search(r'AGENTTEAMS_REQUIRE_SEATBELT:\s*"1"', block), "macos-seatbelt must set AGENTTEAMS_REQUIRE_SEATBELT"
    assert "continue-on-error" not in block, "macos-seatbelt must block merges"
    assert "test_os_sandbox_enforcement.py" in block and "test_sandbox_hardening_battery.py" in block
