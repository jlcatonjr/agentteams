"""Guards for ``tests/_fresh_intel.py`` (GROK-RETRO-R7).

The helper makes subprocess builds hermetic by seeding a synthetic cache for ``--security-offline``.
These tests pin that it does so through the real producer and the real freshness gate, that it
cannot relabel stale data, and that the gate stays unpatched outside the modules that opt in.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentteams import security_refs
from agentteams.cli import security_gate
from tests._fresh_intel import OFFLINE_FLAG, seed_offline_intel

TESTS = Path(__file__).resolve().parent


def _placeholders(out: Path) -> dict[str, str]:
    return security_refs.build_security_placeholders(output_dir=out, offline=True, max_items=5)


def test_seeded_cache_passes_the_real_gate(tmp_path):
    out = tmp_path / ".claude" / "agents"
    assert seed_offline_intel(out, "claude") == [OFFLINE_FLAG]
    seeded = json.loads((out / "references" / "security-vulnerability-watch.json").read_text(encoding="utf-8"))
    placeholders = _placeholders(out)
    # The seed itself was read. A rejected or unreadable cache also yields "fresh" offline (the
    # timestamp falls back to now), so status alone would not prove the seed is valid.
    assert placeholders["SECURITY_DATA_GENERATED_AT"] == seeded["generated_at"]
    assert placeholders["SECURITY_DATA_FRESHNESS_STATUS"] == "fresh"
    assert placeholders["SECURITY_DATA_PAYLOAD_DIGEST"] == security_gate.security_intelligence_digest(placeholders)
    security_gate._assert_security_intelligence_fresh(placeholders, output_dir=out)


def test_seeded_cache_carries_no_advisories(tmp_path):
    out = tmp_path / "agents"
    seed_offline_intel(out, "claude")
    payload = json.loads((out / "references" / "security-vulnerability-watch.json").read_text(encoding="utf-8"))
    assert payload["vulnerabilities"] == [] and payload["osv_packages"] == []


def test_seed_lands_where_goose_reads_it(tmp_path):
    seed_offline_intel(tmp_path, "goose")
    assert (tmp_path / ".goose" / "recipes" / "references" / "security-vulnerability-watch.json").is_file()


def test_the_offline_route_does_not_relabel_an_old_cache(tmp_path):
    # Control: the helper works because its timestamp is current, not because the route skips the TTL.
    out = tmp_path / "agents"
    seed_offline_intel(out, "claude")
    cache = out / "references" / "security-vulnerability-watch.json"
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["generated_at"] = (datetime.now(UTC) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="stale"):
        security_gate._assert_security_intelligence_fresh(_placeholders(out), output_dir=out)


def test_the_gate_is_not_patched_globally():
    # Hermeticity is per module (a seeded cache, or a module-scoped patch). A session-wide patch
    # would silently stop the gate's own tests from exercising it.
    assert "_assert_security_intelligence_fresh" not in (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert security_gate._assert_security_intelligence_fresh.__module__ == "agentteams.cli.security_gate"
