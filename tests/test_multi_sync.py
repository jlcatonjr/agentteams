"""test_multi_sync.py — the pinned multi-framework sync policy.

Focus: the reconciliation policy (the novel logic) is proven directly with
synthetic CAI dicts through the real classifier, so these tests need no
framework files and run everywhere (including CI). The pin contract and
git-based change detection get their own unit coverage.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from agentteams import multi_sync as ms
from agentteams import sync_pin
from agentteams.sync_classifier import classify_sync


def _cai(body="BODY", desc="orig-desc", caps=None):
    agent = {
        "slug": "a1",
        "name": "Agent One",
        "description": desc,
        "body_markdown": body,
    }
    if caps is not None:
        agent["capabilities"] = caps
    return {"schema_version": "2.0", "agents": [agent]}


def _report(canonical, native, baseline, framework):
    return classify_sync(canonical, native, baseline, framework=framework)


def _body_of(cai):
    return cai["agents"][0]["body_markdown"]


# ---------------------------------------------------------------------------
# Reconciliation policy
# ---------------------------------------------------------------------------

def test_peer_clean_change_is_absorbed():
    baseline = _cai(body="orig")
    canonical = copy.deepcopy(baseline)
    native = _cai(body="peer-edited")  # peer moved; canonical unchanged
    report = _report(canonical, native, baseline, "claude")
    out, conflicts, applied = ms._reconcile(canonical, report, framework="claude", is_pin=False)
    assert _body_of(out) == "peer-edited"
    assert applied == 1
    assert conflicts == []


def test_pin_wins_both_moved_conflict():
    baseline = _cai(body="orig")
    canonical = _cai(body="canonical-side")   # canonical moved
    native = _cai(body="pin-side")            # pin moved differently -> conflict
    report = _report(canonical, native, baseline, "copilot-vscode")
    out, conflicts, applied = ms._reconcile(canonical, report, framework="copilot-vscode", is_pin=True)
    assert _body_of(out) == "pin-side"        # pin is authoritative
    assert applied == 1
    assert any("pin-wins" in c.resolution for c in conflicts)


def test_peer_conflict_keeps_canonical_and_logs():
    baseline = _cai(body="orig")
    canonical = _cai(body="canonical-side")   # canonical (= pin authority) moved
    native = _cai(body="peer-side")           # peer moved differently -> conflict
    report = _report(canonical, native, baseline, "goose")
    out, conflicts, applied = ms._reconcile(canonical, report, framework="goose", is_pin=False)
    assert _body_of(out) == "canonical-side"  # pin wins; peer discarded
    assert applied == 0
    assert any(c.classification == "both-moved-conflict" for c in conflicts)
    assert any("pin-wins" in c.resolution for c in conflicts)


def test_peer_capability_change_is_withheld_not_fanned_out():
    base_caps = {"tool_scopes": ["read"], "raw": {}, "model_hint": None}
    peer_caps = {"tool_scopes": ["read", "execute"], "raw": {}, "model_hint": None}
    baseline = _cai(caps=base_caps)
    canonical = copy.deepcopy(baseline)        # canonical capability unchanged
    native = _cai(caps=peer_caps)              # peer widened a grant (clean native-moved)
    report = _report(canonical, native, baseline, "claude")
    out, conflicts, applied = ms._reconcile(canonical, report, framework="claude", is_pin=False)
    # The peer grant must NOT silently reach canonical (and thus every framework).
    assert out["agents"][0]["capabilities"] == base_caps
    assert applied == 0
    assert any("withheld" in c.resolution for c in conflicts)


def test_pin_capability_change_is_authoritative():
    base_caps = {"tool_scopes": ["read"], "raw": {}, "model_hint": None}
    pin_caps = {"tool_scopes": ["read", "execute"], "raw": {}, "model_hint": None}
    baseline = _cai(caps=base_caps)
    canonical = copy.deepcopy(baseline)
    native = _cai(caps=pin_caps)               # the PIN widened its own grant
    report = _report(canonical, native, baseline, "copilot-vscode")
    out, conflicts, applied = ms._reconcile(canonical, report, framework="copilot-vscode", is_pin=True)
    assert out["agents"][0]["capabilities"] == pin_caps  # pin capability is authoritative
    assert applied == 1


# ---------------------------------------------------------------------------
# Pin contract
# ---------------------------------------------------------------------------

def test_pin_contract_round_trip(tmp_path: Path):
    assert sync_pin.read_pin(tmp_path) is None
    sync_pin.save_pin(
        tmp_path,
        pinned_framework="copilot-vscode",
        frameworks=["copilot-vscode", "claude"],
        last_synced_commit="abc123",
    )
    doc = sync_pin.read_pin(tmp_path)
    assert doc["pinned_framework"] == "copilot-vscode"
    assert doc["frameworks"] == ["copilot-vscode", "claude"]
    assert doc["last_synced_commit"] == "abc123"
    sync_pin.update_last_synced_commit(tmp_path, "def456")
    assert sync_pin.read_pin(tmp_path)["last_synced_commit"] == "def456"


def test_pin_rejects_pin_outside_sync_set(tmp_path: Path):
    with pytest.raises(ValueError):
        sync_pin.save_pin(
            tmp_path, pinned_framework="goose", frameworks=["copilot-vscode", "claude"],
        )


# ---------------------------------------------------------------------------
# git change detection
# ---------------------------------------------------------------------------

def _git(root: Path, *args):
    import subprocess
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def test_detect_changed_frameworks_by_path(tmp_path: Path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".github" / "agents").mkdir(parents=True)
    (tmp_path / ".github" / "agents" / "a.agent.md").write_text("x", encoding="utf-8")
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "a.md").write_text("y", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    anchor = ms._current_head(tmp_path)
    # No new commit yet.
    assert ms._detect_changed_frameworks(tmp_path, ["copilot-vscode", "claude"], anchor) == []
    # Edit only the claude file and COMMIT it (detection is commit-to-commit).
    (tmp_path / ".claude" / "agents" / "a.md").write_text("changed", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "edit claude")
    hit = ms._detect_changed_frameworks(tmp_path, ["copilot-vscode", "claude"], anchor)
    assert hit == ["claude"]


def test_detect_falls_back_to_all_without_anchor(tmp_path: Path):
    fws = ["copilot-vscode", "claude"]
    assert ms._detect_changed_frameworks(tmp_path, fws, "") == fws


# ---------------------------------------------------------------------------
# Regression: unquoted YAML dates must not break JSON serialization
# ---------------------------------------------------------------------------

def test_unquoted_yaml_date_in_front_matter_stays_json_safe(tmp_path: Path):
    """PyYAML parses `date: 2026-08-12` as a datetime.date; the CAI dict must
    still serialize (canonical materialize / sync baselines both json.dumps it)."""
    import json

    from agentteams.interop import export_to_cai

    agents = tmp_path / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.agent.md").write_text(
        "---\nname: A\ndescription: d\ndate: 2026-08-12\ntools: [read]\n---\n\nBody.\n",
        encoding="utf-8",
    )
    cai = export_to_cai(agents, "copilot-vscode")
    # Must not raise TypeError: Object of type date is not JSON serializable.
    json.dumps(cai)


# ---------------------------------------------------------------------------
# Directory-collision guard (P1, 2026-08-15): copilot-vscode and copilot-cli
# now share one physical agents directory. Two frameworks writing genuinely
# different content (handoffs kept vs. stripped) to the same path is a real
# data-loss risk with no correct merge — see the module docstring on
# _reject_directory_collisions for the full reproduction.
# ---------------------------------------------------------------------------

def _write_agent(agents_dir: Path, slug: str, *, with_handoff: bool) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    handoff_fm = "handoffs:\n  - label: \"Security review\"\n    agent: security\n" if with_handoff else ""
    handoff_body = "\n\n## Handoffs\n\n- **Security review** -> `security`\n" if with_handoff else ""
    (agents_dir / f"{slug}.agent.md").write_text(
        "---\n"
        f"name: {slug.title()}\n"
        "description: d\n"
        "user-invocable: true\n"
        "tools: ['read', 'edit']\n"
        f"{handoff_fm}"
        "---\n\n"
        "Body prose.\n"
        f"{handoff_body}",
        encoding="utf-8",
    )


def test_reject_directory_collisions_flags_copilot_vscode_and_cli(tmp_path: Path):
    with pytest.raises(ValueError, match="share one physical agents directory"):
        ms._reject_directory_collisions(tmp_path, ["copilot-vscode", "copilot-cli"])


def test_reject_directory_collisions_allows_non_colliding_set(tmp_path: Path):
    ms._reject_directory_collisions(tmp_path, ["copilot-vscode", "claude", "goose"])  # must not raise


def test_sync_init_default_frameworks_now_collides(tmp_path: Path):
    """Calling sync_init with no explicit `frameworks=` now always includes
    both copilot-vscode and copilot-cli (both are core-registered), so the
    unqualified default must raise rather than silently corrupt the shared
    directory. Callers must narrow `frameworks=` explicitly."""
    _write_agent(tmp_path / ".github" / "agents", "orchestrator", with_handoff=False)
    with pytest.raises(ValueError, match="share one physical agents directory"):
        ms.sync_init(tmp_path, pin="copilot-vscode")


def test_sync_init_rejects_before_any_write(tmp_path: Path):
    """The guard must fire before touching disk — no partial/corrupted output
    from a rejected sync_init call."""
    agents = tmp_path / ".github" / "agents"
    _write_agent(agents, "orchestrator", with_handoff=True)
    before = (agents / "orchestrator.agent.md").read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        ms.sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "copilot-cli"])

    after = (agents / "orchestrator.agent.md").read_text(encoding="utf-8")
    assert after == before
    assert not (tmp_path / ".agentteams" / "pin.json").exists()


def test_run_sync_rejects_collision_from_persisted_pin(tmp_path: Path):
    """Defense in depth: a pin document can be hand-authored or written via
    sync_pin.save_pin directly, bypassing sync_init's own guard. run_sync must
    independently refuse a colliding persisted sync set too."""
    _write_agent(tmp_path / ".github" / "agents", "orchestrator", with_handoff=False)
    sync_pin.save_pin(
        tmp_path,
        pinned_framework="copilot-vscode",
        frameworks=["copilot-vscode", "copilot-cli"],
        last_synced_commit="",
    )
    with pytest.raises(ValueError, match="share one physical agents directory"):
        ms.run_sync(tmp_path)


def test_sync_init_two_frameworks_sharing_a_path_with_real_handoff_content(tmp_path: Path):
    """End-to-end reproduction of the original risk (kept here as a regression
    guard, not just a code-path check): before the fix, copilot-cli's
    handoff-stripped write silently clobbered copilot-vscode's handoff-bearing
    write at the shared path. Now sync_init must refuse outright instead."""
    agents = tmp_path / ".github" / "agents"
    _write_agent(agents, "orchestrator", with_handoff=True)
    _write_agent(agents, "security", with_handoff=False)
    before = (agents / "orchestrator.agent.md").read_text(encoding="utf-8")
    assert "handoffs:" in before  # sanity: the fixture really has one

    with pytest.raises(ValueError):
        ms.sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "copilot-cli"])

    # Untouched — not silently stripped of its handoffs.
    assert (agents / "orchestrator.agent.md").read_text(encoding="utf-8") == before

