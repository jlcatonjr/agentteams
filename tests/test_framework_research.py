"""Regression tests for agentteams.framework_research.

All network calls are stubbed; no fetches occur. Snapshots live in a
temp tree so that production state under tmp/daily-pipeline is never
touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import framework_research as fr


def _write_minimal_module(repo_root: Path) -> None:
    """Lay down just enough of the module tree for _load_local_adapter_constants."""
    (repo_root / "agentteams" / "frameworks").mkdir(parents=True)
    (repo_root / "agentteams" / "frameworks" / "claude.py").write_text(
        '_CLAUDE_DEFAULT_ALLOWED_TOOLS = "Bash, Read, Write, Edit"\n'
        '_CLAUDE_REQUIRED_KEYS = {"name", "description"}\n',
        encoding="utf-8",
    )
    (repo_root / "references").mkdir()
    (repo_root / "references" / "claude-agent-infrastructure-expert.md").write_text(
        "# Claude Agent Infrastructure Expert Reference\n\n"
        "Purpose: canonical guidance.\n",
        encoding="utf-8",
    )
    (repo_root / "references" / "copilot-agent-infrastructure-expert.md").write_text(
        "# Copilot Agent Infrastructure Expert Reference\n\n"
        "Purpose: canonical guidance.\n",
        encoding="utf-8",
    )


def _write_snapshot(repo_root: Path, *, with_drift: bool = True) -> None:
    snap_path = repo_root / fr.SNAPSHOT_REL
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    claude_tokens = {
        "front_matter_keys_present": ["model", "name", "tools"] if with_drift else ["description", "name"],
        "locations_present": [".claude/agents", "CLAUDE.md"],
    }
    keys_diff = (
        {"matched": ["name"], "missing_upstream": ["description"], "new_upstream": ["model", "tools"]}
        if with_drift
        else {"matched": ["description", "name"], "missing_upstream": [], "new_upstream": []}
    )
    snapshot = {
        "schema_version": "1.1",
        "framework": "claude",
        "source_url": fr.CLAUDE_DOC_URL,
        "generated_at": "2026-05-25T00:00:00Z",
        "generated_on": "2026-05-25",
        "fetch_status": "ok",
        "fetch_error": "",
        "raw_bytes": 100,
        "upstream_tokens": claude_tokens,
        "local_adapter": {"required_front_matter_keys": ["description", "name"], "default_allowed_tools": ["Bash"]},
        "keys_diff": keys_diff,
        "frameworks": {
            "claude": {
                "label": "Claude Code Sub-Agents",
                "source_url": fr.CLAUDE_DOC_URL,
                "expert_ref": fr.EXPERT_REF_REL,
                "fetch_status": "ok",
                "upstream_tokens": claude_tokens,
            },
            "copilot_vscode": {
                "label": "GitHub Copilot — VS Code",
                "source_url": "https://example.invalid/vscode",
                "expert_ref": fr.COPILOT_EXPERT_REF_REL,
                "fetch_status": "ok",
                "upstream_tokens": {
                    "front_matter_keys_present": ["description", "tools"] if with_drift else [],
                    "locations_present": [".github/agents"],
                },
            },
        },
    }
    snap_path.write_text(json.dumps(snapshot), encoding="utf-8")


def test_build_framework_placeholders_keys(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    placeholders = fr.build_framework_placeholders(tmp_path, offline=True)
    expected = {
        "FRAMEWORK_RESEARCH_FRAMEWORK",
        "FRAMEWORK_RESEARCH_SOURCE_URL",
        "FRAMEWORK_RESEARCH_GENERATED_ON",
        "FRAMEWORK_RESEARCH_FETCH_STATUS",
        "FRAMEWORK_RESEARCH_TABLE",
        "FRAMEWORK_RESEARCH_STALE_BANNER",
        "FRAMEWORK_RESEARCH_DIFF_SUMMARY",
    }
    assert expected <= set(placeholders.keys())
    assert "claude" in placeholders["FRAMEWORK_RESEARCH_TABLE"]
    assert "copilot_vscode" in placeholders["FRAMEWORK_RESEARCH_TABLE"]


def test_propose_no_drift_returns_empty(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    # Empty snapshot file → no proposal
    proposal = fr.propose_module_patch(tmp_path)
    assert proposal["changes"] == []


def test_propose_with_drift_targets_both_refs(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    proposal = fr.propose_module_patch(tmp_path)
    paths = {c["path"] for c in proposal["changes"]}
    assert fr.EXPERT_REF_REL in paths
    assert fr.COPILOT_EXPERT_REF_REL in paths
    for change in proposal["changes"]:
        assert change["operation"] == "append_or_replace_section"
        assert "Observed Upstream Tokens" in change["new_text"]


def test_apply_refuses_outside_allow_list(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    # GitHub Actions sets CI=true unconditionally; that gate fires before
    # the allow-list check. Unset it so this test exercises the allow-list
    # path specifically. The CI gate has its own dedicated test below.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AGENTTEAMS_ALLOW_CI_APPLY", raising=False)
    bad_proposal = {
        "changes": [
            {
                "path": "agentteams/frameworks/claude.py",
                "operation": "append_or_replace_section",
                "new_text": "evil",
            }
        ]
    }
    with pytest.raises(RuntimeError, match="allow-list"):
        fr.apply_module_patch(bad_proposal, tmp_path)


def test_apply_refuses_in_ci(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("AGENTTEAMS_ALLOW_CI_APPLY", raising=False)
    proposal = fr.propose_module_patch(tmp_path)
    assert proposal["changes"], "expected drift in fixture"
    with pytest.raises(RuntimeError, match="CI"):
        fr.apply_module_patch(proposal, tmp_path)


def test_apply_runs_in_ci_with_explicit_marker(tmp_path, monkeypatch):
    """T2.D3: AGENTTEAMS_ALLOW_CI_APPLY=1 lifts the CI refusal."""
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    proposal = fr.propose_module_patch(tmp_path)
    result = fr.apply_module_patch(proposal, tmp_path)
    assert result["applied"], "explicit marker should permit CI apply"


def test_apply_writes_observation_blocks(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=True)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.delenv("CI", raising=False)
    proposal = fr.propose_module_patch(tmp_path)
    result = fr.apply_module_patch(proposal, tmp_path)
    assert sorted(result["applied"]) == sorted({fr.EXPERT_REF_REL, fr.COPILOT_EXPERT_REF_REL})
    claude_text = (tmp_path / fr.EXPERT_REF_REL).read_text(encoding="utf-8")
    copilot_text = (tmp_path / fr.COPILOT_EXPERT_REF_REL).read_text(encoding="utf-8")
    assert "Observed Upstream Tokens — `claude`" in claude_text
    assert "Observed Upstream Tokens — `copilot_vscode`" in copilot_text


def test_refresh_snapshot_offline_returns_cached(tmp_path, monkeypatch):
    _write_minimal_module(tmp_path)
    _write_snapshot(tmp_path, with_drift=False)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    snap = fr.refresh_snapshot(tmp_path, offline=True)
    assert snap.get("framework") == "claude"
    assert snap.get("generated_on") == "2026-05-25"


def test_dedup_hash_stable_across_dates(tmp_path, monkeypatch):
    """1.0.0-rc.2: proposal.dedup_hash must be identical across runs when
    only generated_on differs (token-set unchanged). This is the property
    the auto-PR dedup step relies on; if it breaks, the workflow opens a
    fresh PR every day on no-drift days.
    """
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    monkeypatch.delenv("CI", raising=False)

    # Day 1.
    _write_snapshot(tmp_path, with_drift=True)
    snap_path = tmp_path / fr.SNAPSHOT_REL
    p1 = fr.propose_module_patch(tmp_path)
    h1 = p1.get("dedup_hash")
    assert h1, "proposal must carry a dedup_hash"

    # Day 2: same token-set, different generated_on.
    body = json.loads(snap_path.read_text(encoding="utf-8"))
    body["generated_on"] = "2026-05-26"
    body["generated_at"] = "2026-05-26T00:00:00Z"
    snap_path.write_text(json.dumps(body), encoding="utf-8")
    p2 = fr.propose_module_patch(tmp_path)
    h2 = p2.get("dedup_hash")
    assert h1 == h2, f"dedup_hash drifted with date: {h1} vs {h2}"


def test_splice_no_blank_line_accumulation():
    """1.0.0-rc.2: repeated splice of the same heading must not grow
    leading blank lines on each pass. Surfaced from PR #6's diff which
    showed +1 blank line above the heading per day.
    """
    initial = (
        "# Header\n\n"
        "Some prose.\n\n"
        "## Observed Upstream Tokens — `claude` (Daily Pipeline)\n\n"
        "Old content.\n"
    )
    block = "\n## Observed Upstream Tokens — `claude` (Daily Pipeline)\n\nNew content.\n"
    once = fr._splice_observation_block(initial, block, fid="claude")
    twice = fr._splice_observation_block(once, block, fid="claude")
    thrice = fr._splice_observation_block(twice, block, fid="claude")
    # No run of 3+ consecutive newlines should appear.
    import re as _re

    assert not _re.search(r"\n{3,}", thrice), \
        f"blank-line accumulation:\n{thrice!r}"
    # And the body content stays exactly once.
    assert thrice.count("New content.") == 1


# --- MAP-19: STALE_DAYS env-var override ---

def test_stale_days_default(monkeypatch):
    """MAP-19: STALE_DAYS must default to 7 when env var is unset."""
    import importlib
    monkeypatch.delenv("AGENTTEAMS_STALE_DAYS", raising=False)
    importlib.reload(fr)
    assert fr.STALE_DAYS == 7
    # No explicit cleanup reload needed: reloading here already leaves
    # fr.STALE_DAYS = 7, which is the default. Asymmetry with Test B is
    # intentional — Test B sets a non-default value and must restore it.


def test_stale_days_env_var_override(monkeypatch):
    """MAP-19: AGENTTEAMS_STALE_DAYS env var must override the hardcoded 7."""
    import importlib
    monkeypatch.setenv("AGENTTEAMS_STALE_DAYS", "14")
    importlib.reload(fr)
    try:
        assert fr.STALE_DAYS == 14
    finally:
        # Unconditional cleanup — restore default so other tests are not
        # affected even if the assertion above fails.
        monkeypatch.delenv("AGENTTEAMS_STALE_DAYS", raising=False)
        importlib.reload(fr)


def test_staleness_banner_respects_stale_days(monkeypatch):
    """MAP-19: _staleness_banner must use the current STALE_DAYS value,
    not a cached literal. A snapshot 8 days old should be quiet at
    STALE_DAYS=9 but noisy at STALE_DAYS=7.
    """
    import datetime as dt

    eight_days_ago = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {"generated_at": eight_days_ago}

    # With STALE_DAYS=9: snapshot is 8 days old → no banner.
    monkeypatch.setattr(fr, "STALE_DAYS", 9)
    assert fr._staleness_banner(snapshot) == ""

    # With STALE_DAYS=7: snapshot is 8 days old → banner fires.
    monkeypatch.setattr(fr, "STALE_DAYS", 7)
    banner = fr._staleness_banner(snapshot)
    assert "STALE DATA" in banner
    assert "threshold 7 days" in banner


def test_stale_days_non_numeric_raises(monkeypatch):
    """MAP-19: A non-numeric AGENTTEAMS_STALE_DAYS must raise ValueError at
    import time with a message that names the env var so operators can trace
    the crash. Covers strings like 'abc' and floats like '7.5'."""
    import importlib
    for bad_value in ("abc", "7.5", ""):
        monkeypatch.setenv("AGENTTEAMS_STALE_DAYS", bad_value)
        try:
            with pytest.raises(ValueError, match="AGENTTEAMS_STALE_DAYS"):
                importlib.reload(fr)
        finally:
            monkeypatch.delenv("AGENTTEAMS_STALE_DAYS", raising=False)
            importlib.reload(fr)


def test_stale_days_non_positive_raises(monkeypatch):
    """MAP-19: AGENTTEAMS_STALE_DAYS=0 or a negative value must be rejected.
    Zero makes every snapshot appear stale (age >= 0 is always true);
    negative values do the same."""
    import importlib
    for bad_value in ("0", "-1", "-7"):
        monkeypatch.setenv("AGENTTEAMS_STALE_DAYS", bad_value)
        try:
            with pytest.raises(ValueError, match="AGENTTEAMS_STALE_DAYS"):
                importlib.reload(fr)
        finally:
            monkeypatch.delenv("AGENTTEAMS_STALE_DAYS", raising=False)
            importlib.reload(fr)


# --- scoped tool-permission drift probe (2026-07-30) ------------------------
#
# claude.py maps the `retrieval` token to `Bash(python -m agentteams.research:*)`. Whether a
# given Claude Code version honours that scope inside SUB-AGENT front matter is not verifiable
# from inside this repo, and the failure direction is unsafe (an ignored scope grants MORE).
# references/retrieval-transport-policy.md records the uncertainty; this probe makes it
# TRACKED rather than merely stated.

def test_scoped_tool_probe_reports_evidence_when_markers_are_present():
    from agentteams.framework_research import _scan_scoped_tool_support

    result = _scan_scoped_tool_support(
        "Use allowed-tools: Bash(git diff:*) to restrict a command."
    )
    assert result["status"] == "evidence-found"
    assert "bash(" in result["markers_seen"]


def test_scoped_tool_probe_reports_no_evidence_when_absent():
    from agentteams.framework_research import _scan_scoped_tool_support

    result = _scan_scoped_tool_support("Sub-agents accept a comma-separated tools list.")
    assert result["status"] == "no-evidence"
    assert result["markers_seen"] == []


def test_scoped_tool_probe_never_claims_support():
    """Evidence is not a verdict.

    A marker anywhere on the page could be describing slash commands or settings-file
    permissions rather than sub-agent front matter. The probe must never emit a status a reader
    could mistake for confirmation.
    """
    from agentteams.framework_research import _scan_scoped_tool_support

    result = _scan_scoped_tool_support("allowed-tools: Bash(ls:*)")
    assert result["status"] != "supported"
    assert "EVIDENCE ONLY" in result["claim"]
    assert "retrieval-transport-policy" in result["claim"]


def test_scan_tokens_carries_the_probe_without_dropping_existing_fields():
    from agentteams.framework_research import _scan_tokens

    scanned = _scan_tokens("name: x\ndescription: y\ntools: z\nmodel: m\n.claude/agents")
    assert "front_matter_keys_present" in scanned
    assert "locations_present" in scanned
    assert scanned["scoped_tool_permissions"]["status"] == "no-evidence"


# ---------------------------------------------------------------------------
# Phase 1: six-wide diff + fetch integrity + HTML-to-text
# (assessment §3.1, §3.3). Provider docs are HTML and can relocate/empty; the
# scan must not read a moved or empty page as "no drift", and every framework —
# not just claude — gets a per-framework keys_diff.
# ---------------------------------------------------------------------------


def test_html_to_text_strips_tags_scripts_and_unescapes():
    raw = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var name = 'evil:';</script></head>"
        "<body><h1>Docs</h1><p>name: value &amp; more</p></body></html>"
    )
    text = fr._html_to_text(raw)
    assert "color:red" not in text  # <style> body dropped
    assert "var name" not in text  # <script> body dropped
    assert "name: value & more" in text  # tags gone, entity unescaped
    assert "<" not in text and ">" not in text


def test_scan_framework_offline_still_carries_per_framework_diff():
    entry = fr.FRAMEWORK_REGISTRY["copilot_vscode"]
    result = fr._scan_framework(entry, offline=True)
    assert result["fetch_status"] == "skipped"
    assert "keys_diff" in result
    # With nothing observed, everything expected is "missing_upstream".
    assert set(result["keys_diff"]["missing_upstream"]) == set(entry["expected_keys"])


def test_scan_framework_flags_moved_host_not_no_drift(monkeypatch):
    entry = fr.FRAMEWORK_REGISTRY["codex"]

    def fake_fetch(url, timeout=10):
        return ("<html>generic landing page</html>", {
            "status": 200,
            "final_url": "https://elsewhere.example/generic",
            "host_changed": True,
        })

    monkeypatch.setattr(fr, "_fetch_with_meta", fake_fetch)
    result = fr._scan_framework(entry, offline=False)
    assert result["fetch_status"] == "moved"
    assert result["host_changed"] is True
    assert "elsewhere.example" in result["final_url"]
    # A moved page must NOT be scanned and reported as no-drift.
    assert result["upstream_tokens"] == {}


def test_scan_framework_flags_empty_page(monkeypatch):
    entry = fr.FRAMEWORK_REGISTRY["claude"]

    def fake_fetch(url, timeout=10):
        return ("<html><body>tiny</body></html>", {
            "status": 200,
            "final_url": entry["source_url"],
            "host_changed": False,
        })

    monkeypatch.setattr(fr, "_fetch_with_meta", fake_fetch)
    result = fr._scan_framework(entry, offline=False)
    assert result["fetch_status"] == "empty"
    assert result["upstream_tokens"] == {}


def test_scan_framework_ok_scans_stripped_html_and_diffs(monkeypatch):
    entry = fr.FRAMEWORK_REGISTRY["claude"]
    body = (
        "<html><body><h1>Sub-agents</h1>"
        "<pre>name: x\ndescription: y\ntools: z</pre>"
        "<p>Place files in .claude/agents and CLAUDE.md.</p>"
        + ("<p>filler paragraph to clear the min-bytes floor.</p>" * 20)
        + "</body></html>"
    )

    def fake_fetch(url, timeout=10):
        return (body, {"status": 200, "final_url": entry["source_url"], "host_changed": False})

    monkeypatch.setattr(fr, "_fetch_with_meta", fake_fetch)
    result = fr._scan_framework(entry, offline=False)
    assert result["fetch_status"] == "ok"
    observed = result["upstream_tokens"]["front_matter_keys_present"]
    assert {"name", "description", "tools"} <= set(observed)
    # 'model' is expected but absent from this doc → reported as missing_upstream.
    assert "model" in result["keys_diff"]["missing_upstream"]
    assert ".claude/agents" in result["upstream_tokens"]["locations_present"]


def test_refresh_snapshot_is_six_wide(monkeypatch, tmp_path):
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)

    def fake_fetch(url, timeout=10):
        body = (
            "<html><body>name: a description: b tools: c model: d title: t "
            "instructions: i prompt: p project_doc_max_bytes: 1 mcp_servers: s "
            ".claude/agents CLAUDE.md .github/agents .goose/recipes AGENTS.md .codex"
            + (" pad" * 300) + "</body></html>"
        )
        return (body, {"status": 200, "final_url": url, "host_changed": False})

    monkeypatch.setattr(fr, "_fetch_with_meta", fake_fetch)
    snap = fr.refresh_snapshot(tmp_path, offline=False)
    assert snap["schema_version"] == "1.2"
    frameworks = snap["frameworks"]
    assert set(frameworks) == set(fr.FRAMEWORK_REGISTRY)
    # Every framework now carries its own keys_diff, not just claude.
    for fid, entry in frameworks.items():
        assert "keys_diff" in entry, f"{fid} missing per-framework keys_diff"
        assert entry["fetch_status"] == "ok"


def test_fleet_wide_empty_is_not_swallowed_by_cache(monkeypatch, tmp_path):
    """A fleet-wide 'empty' (e.g. captive-portal stubs) is a real regression and must
    NOT be replaced by yesterday's all-ok cached snapshot (adversarial F4)."""
    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    _write_snapshot(tmp_path, with_drift=False)  # prior cached snapshot, generated_on 2026-05-25

    def stub(url, timeout=10):
        return ("<html>x</html>", {"status": 200, "final_url": url, "host_changed": False})

    monkeypatch.setattr(fr, "_fetch_with_meta", stub)
    snap = fr.refresh_snapshot(tmp_path, offline=False)
    assert snap["generated_on"] != "2026-05-25", "fleet-wide empty was swallowed by the cache"
    assert all(f["fetch_status"] == "empty" for f in snap["frameworks"].values())


def test_fleet_wide_failure_falls_back_to_cache(monkeypatch, tmp_path):
    """A transient total network failure DOES fall back to cache (don't clobber good data)."""
    import urllib.error

    _write_minimal_module(tmp_path)
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    _write_snapshot(tmp_path, with_drift=False)

    def boom(url, timeout=10):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(fr, "_fetch_with_meta", boom)
    snap = fr.refresh_snapshot(tmp_path, offline=False)
    assert snap["generated_on"] == "2026-05-25", "transient failure should reuse cached snapshot"


# ---------------------------------------------------------------------------
# Phase 3: unified provider-freshness view (reconcile watcher + manual register)
# ---------------------------------------------------------------------------


def test_freshness_view_is_six_wide_and_flags_register_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "_snapshot_path", lambda root: tmp_path / fr.SNAPSHOT_REL)
    # A register that covers only claude (by its real source_url) — the others are gaps.
    reg = tmp_path / fr.PROVIDER_REGISTER_REL
    reg.parent.mkdir(parents=True, exist_ok=True)
    claude_url = fr._FORMAT_SPECS["claude"].source_url
    reg.write_text(
        "| doc_id | provider | url | governs | last_verified | window_days |\n"
        "|---|---|---|---|---|---|\n"
        f"| claude | Anthropic | {claude_url} | keys | 2026-09-01 | 90 |\n",
        encoding="utf-8",
    )
    rows = fr.build_provider_freshness_view(tmp_path)
    assert {r["provider"] for r in rows} == set(fr.FRAMEWORK_REGISTRY)
    by = {r["provider"]: r for r in rows}
    assert by["claude"]["in_register"] is True
    assert by["claude"]["register_last_verified"] == "2026-09-01"
    # agents_md and codex are real coverage gaps (not in the register).
    assert by["agents_md"]["in_register"] is False
    assert by["codex"]["in_register"] is False
    # No snapshot present → fetch_status 'never', no crash.
    assert by["goose"]["fetch_status"] == "never"
    table = fr.render_provider_freshness_view(rows)
    assert "coverage gap" in table.lower()
