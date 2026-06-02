"""Tests for the AI bad-habits watch daily-pipeline stage.

Covers the audit-mandated behaviours:
- offline refresh never raises and never touches the network (CH-24 boundary);
- the rendered watch is stable (pinned/confirmed both render as 'tracking') so
  there is no day-over-day commit noise;
- propose is idempotent right after a refresh;
- the CI guard raises without AGENTTEAMS_ALLOW_CI_APPLY;
- apply refuses paths/ops outside the allow-list;
- every catalog cross-link is a real CH-/S- id (guards the audited AE fixes);
- BH ids are unique and contiguous.
"""

from __future__ import annotations

import socket
import urllib.error
from pathlib import Path

import pytest

from agentteams import ai_bad_habits


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "references").mkdir(parents=True)
    return tmp_path


def test_offline_refresh_never_touches_network(monkeypatch, tmp_path: Path) -> None:
    def _boom(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("offline refresh must not hit the network")

    monkeypatch.setattr(ai_bad_habits._urlrequest, "urlopen", _boom)
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=True)
    assert snap["offline"] is True
    assert all(s["fetch_status"] == "offline" for s in snap["sources"])
    assert all(s["freshness"] == "pinned" for s in snap["sources"])


def test_online_probe_failure_degrades_to_pinned(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        ai_bad_habits._urlrequest,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("no net")),
    )
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=False)
    assert all(s["freshness"] == "pinned" for s in snap["sources"])
    assert all(s["fetch_status"] == "offline" for s in snap["sources"])


def test_online_interstitial_200_does_not_manufacture_drift(monkeypatch, tmp_path: Path) -> None:
    # A 200 response whose body is too short to be the real page (CAPTCHA /
    # Cloudflare / redirect stub) must NOT be read as drift — it degrades to
    # pinned, so a bot-challenge page cannot open a false-positive PR.
    class _Resp:
        def __init__(self, body): self._b = body.encode()
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(ai_bad_habits._urlrequest, "urlopen",
                        lambda *a, **k: _Resp("Just a moment... checking your browser"))
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=False)
    assert all(s["freshness"] == "pinned" for s in snap["sources"])
    assert all(s["fetch_status"] == "interstitial" for s in snap["sources"])


def test_online_timeout_degrades_to_pinned(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        ai_bad_habits._urlrequest,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(socket.timeout("slow")),
    )
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=False)
    assert all(s["freshness"] == "pinned" for s in snap["sources"])


def test_render_is_stable_pinned_and_confirmed_match(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offline_snap = ai_bad_habits.refresh_snapshot(repo, offline=True)
    # Simulate an online run where every source confirmed its pinned edition.
    confirmed_snap = {
        "offline": False,
        "sources": [{**s, "fetch_status": "ok", "freshness": "confirmed"} for s in offline_snap["sources"]],
        "catalog": offline_snap["catalog"],
    }
    # Stable: a confirmed online run renders identically to an offline run, so
    # the committed file does NOT churn day over day.
    assert ai_bad_habits.render_watch(offline_snap) == ai_bad_habits.render_watch(confirmed_snap)


def test_drift_changes_the_render(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = ai_bad_habits.refresh_snapshot(repo, offline=True)
    drifted = {
        "offline": False,
        "sources": [
            ({**s, "freshness": "drift-suspected"} if i == 0 else s)
            for i, s in enumerate(base["sources"])
        ],
        "catalog": base["catalog"],
    }
    assert ai_bad_habits.render_watch(drifted) != ai_bad_habits.render_watch(base)
    assert "⚠️ review" in ai_bad_habits.render_watch(drifted)


def test_content_hash_is_date_independent(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    a = ai_bad_habits.refresh_snapshot(repo, offline=True)
    b = ai_bad_habits.refresh_snapshot(repo, offline=True)
    assert ai_bad_habits.content_hash(a) == ai_bad_habits.content_hash(b)


def test_propose_idempotent_after_write(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ai_bad_habits.write_watch(repo, offline=True)
    proposal = ai_bad_habits.propose_watch_patch(repo, offline=True)
    assert proposal["changes"] == []
    assert proposal["dedup_hash"] == ai_bad_habits.content_hash(
        ai_bad_habits.refresh_snapshot(repo, offline=True)
    )


def test_propose_detects_missing_artifact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo, offline=True)
    assert {c["path"] for c in proposal["changes"]} == {ai_bad_habits.WATCH_REL}


def test_apply_writes_allowlisted_path(monkeypatch, tmp_path: Path) -> None:
    # Set the marker so apply runs regardless of the ambient CI env (GitHub
    # Actions sets CI=true, which would otherwise trip the CI guard first).
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo, offline=True)
    result = ai_bad_habits.apply_watch_patch(proposal, repo)
    assert result["applied"] == [ai_bad_habits.WATCH_REL]
    assert (repo / ai_bad_habits.WATCH_REL).exists()
    assert ai_bad_habits.propose_watch_patch(repo, offline=True)["changes"] == []


def test_apply_refuses_in_ci_without_marker(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo, offline=True)
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("AGENTTEAMS_ALLOW_CI_APPLY", raising=False)
    with pytest.raises(RuntimeError, match="AGENTTEAMS_ALLOW_CI_APPLY"):
        ai_bad_habits.apply_watch_patch(proposal, repo)


def test_apply_allows_in_ci_with_marker(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo, offline=True)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    assert ai_bad_habits.apply_watch_patch(proposal, repo)["applied"]


def test_apply_rejects_path_outside_allowlist(monkeypatch, tmp_path: Path) -> None:
    # Marker set so we test the allow-list check, not the CI guard (which is
    # checked first and would otherwise mask this in CI).
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    repo = _repo(tmp_path)
    bad = {"changes": [{"path": "build_team.py", "operation": "replace_file", "new_text": "x"}]}
    with pytest.raises(RuntimeError, match="outside allow-list"):
        ai_bad_habits.apply_watch_patch(bad, repo)


def test_apply_rejects_unknown_operation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    repo = _repo(tmp_path)
    bad = {"changes": [{"path": ai_bad_habits.WATCH_REL, "operation": "delete", "new_text": ""}]}
    with pytest.raises(RuntimeError, match="unsupported operation"):
        ai_bad_habits.apply_watch_patch(bad, repo)


def test_refresh_rejects_non_path() -> None:
    with pytest.raises(TypeError):
        ai_bad_habits.refresh_snapshot("not-a-path")  # type: ignore[arg-type]


def test_refresh_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ai_bad_habits.refresh_snapshot(tmp_path / "does-not-exist")


# --- catalog integrity (guards against re-introducing the audited AE bugs) ---

_VALID_CH = {f"CH-{n:02d}" for n in range(1, 26)}
_VALID_S = {f"S-{n}" for n in range(1, 9)}


def test_every_crosslink_is_a_real_rule_id() -> None:
    for entry in ai_bad_habits.LOCAL_CATALOG:
        for link in entry["cross_links"]:
            assert link in (_VALID_CH | _VALID_S), f"{entry['id']} cross-links unknown rule {link}"


def test_known_attribution_fixes_hold() -> None:
    by_id = {e["id"]: e for e in ai_bad_habits.LOCAL_CATALOG}
    assert by_id["BH-07"]["cross_links"] == ()          # not CH-09
    assert by_id["BH-11"]["cross_links"] == ()          # not CH-14
    assert by_id["BH-12"]["cross_links"] == ("CH-04",)  # not CH-16
    assert by_id["BH-16"]["cross_links"] == ()          # not CH-23
    assert by_id["BH-14"]["cross_links"] == ("CH-08",)  # CH-05 dropped as over-broad


def test_bh_ids_unique_and_contiguous() -> None:
    ids = [e["id"] for e in ai_bad_habits.LOCAL_CATALOG]
    assert len(ids) == len(set(ids))
    nums = sorted(int(i.split("-")[1]) for i in ids)
    assert nums == list(range(1, len(ids) + 1))


def test_render_links_not_duplicates(tmp_path: Path) -> None:
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=True)
    md = ai_bad_habits.render_watch(snap)
    assert md.startswith("<!-- GENERATED FILE")
    assert "LLM01" in md              # references the id
    assert "single-source-of-truth" in md
    # Must NOT restate canonical OWASP names (single-source — they live in @security).
    assert "Prompt Injection" not in md


# --- P1: per-consumer catalog delivery (build_catalog_placeholders) ---

def test_catalog_placeholder_resolves_with_no_unresolved_token() -> None:
    ph = ai_bad_habits.build_catalog_placeholders()
    assert set(ph) == {"AI_BAD_HABITS_CATALOG"}
    body = ph["AI_BAD_HABITS_CATALOG"]
    # The exact failure mode the audit flagged: shipping the literal placeholder.
    assert "{AI_BAD_HABITS_CATALOG}" not in body
    # Catalog content present.
    assert "BH-01" in body and "BH-17" in body
    assert "Corrective pattern" in body


def test_catalog_body_is_ledger_free(tmp_path: Path) -> None:
    # The per-consumer body is catalog-only — the upstream-edition ledger and
    # the daily-watch footer must live ONLY in the repo-root render_watch().
    body = ai_bad_habits.build_catalog_placeholders()["AI_BAD_HABITS_CATALOG"]
    assert "Tracked upstream sources" not in body
    assert "How this is checked daily" not in body
    # ...but render_watch DOES include both (composition intact).
    full = ai_bad_habits.render_watch(ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=True))
    assert "Tracked upstream sources" in full
    assert "How this is checked daily" in full


def test_catalog_single_source_shared_between_renderers(tmp_path: Path) -> None:
    # Both renderings derive from the same LOCAL_CATALOG: every BH id in the
    # placeholder body also appears in the root watch.
    body = ai_bad_habits.build_catalog_placeholders()["AI_BAD_HABITS_CATALOG"]
    full = ai_bad_habits.render_watch(ai_bad_habits.refresh_snapshot(_repo(tmp_path), offline=True))
    for entry in ai_bad_habits.LOCAL_CATALOG:
        assert entry["id"] in body
        assert entry["id"] in full
