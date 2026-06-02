"""Tests for the AI bad-habits catalog (post-2026-06-02 refocus).

The catalog now covers ONLY code-quality / correctness / process habits;
security-class habits are @security's domain. The upstream-source watch +
network probe were removed. These tests guard:
- the curated snapshot shape (no sources/offline/probe);
- the render carries no security/upstream content and no "watch/daily" prose;
- propose/apply allow-list + CI guard still hold;
- catalog integrity: no security/CWE/LLM ids, ids contiguous, cross-links real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import ai_bad_habits


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "references").mkdir(parents=True)
    return tmp_path


# --- snapshot + render ------------------------------------------------------

def test_refresh_returns_catalog_only(tmp_path: Path) -> None:
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path))
    assert set(snap) == {"catalog"}          # no "sources"/"offline"
    assert snap["catalog"]
    assert all("source" not in row for row in snap["catalog"])  # no CWE/LLM source col


def test_refresh_rejects_non_path() -> None:
    with pytest.raises(TypeError):
        ai_bad_habits.refresh_snapshot("not-a-path")  # type: ignore[arg-type]


def test_refresh_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ai_bad_habits.refresh_snapshot(tmp_path / "does-not-exist")


def test_render_has_no_security_or_upstream_content(tmp_path: Path) -> None:
    md = ai_bad_habits.render_watch(ai_bad_habits.refresh_snapshot(_repo(tmp_path)))
    assert md.startswith("<!-- GENERATED FILE")
    # No upstream-watch machinery or duplicated security taxonomy leaks in.
    # (CWE/OWASP may appear ONLY in the @security boundary note, never as
    # enumerated catalog content or a sources/freshness table.)
    for forbidden in ("Tracked upstream sources", "How this is checked daily",
                      "Watch status", "daily probe", "Pinned edition",
                      "Prompt Injection", "drift-suspected"):
        assert forbidden not in md, f"render must not contain {forbidden!r}"
    # Carries the @security ownership boundary + scope statement.
    assert "@security" in md
    assert "Security-class" in md
    # CWE/OWASP appear only in the boundary sentence that hands them to @security.
    assert "owned by `@security`" in md


def test_content_hash_stable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    a = ai_bad_habits.refresh_snapshot(repo)
    b = ai_bad_habits.refresh_snapshot(repo)
    assert ai_bad_habits.content_hash(a) == ai_bad_habits.content_hash(b)


# --- propose / write / apply ------------------------------------------------

def test_propose_idempotent_after_write(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ai_bad_habits.write_watch(repo)
    proposal = ai_bad_habits.propose_watch_patch(repo)
    assert proposal["changes"] == []
    assert proposal["dedup_hash"] == ai_bad_habits.content_hash(
        ai_bad_habits.refresh_snapshot(repo)
    )


def test_propose_detects_missing_artifact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo)
    assert {c["path"] for c in proposal["changes"]} == {ai_bad_habits.WATCH_REL}


def test_apply_writes_allowlisted_path(monkeypatch, tmp_path: Path) -> None:
    # Marker set so apply runs regardless of the ambient CI env.
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo)
    result = ai_bad_habits.apply_watch_patch(proposal, repo)
    assert result["applied"] == [ai_bad_habits.WATCH_REL]
    assert (repo / ai_bad_habits.WATCH_REL).exists()
    assert ai_bad_habits.propose_watch_patch(repo)["changes"] == []


def test_apply_refuses_in_ci_without_marker(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo)
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("AGENTTEAMS_ALLOW_CI_APPLY", raising=False)
    with pytest.raises(RuntimeError, match="AGENTTEAMS_ALLOW_CI_APPLY"):
        ai_bad_habits.apply_watch_patch(proposal, repo)


def test_apply_allows_in_ci_with_marker(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    proposal = ai_bad_habits.propose_watch_patch(repo)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AGENTTEAMS_ALLOW_CI_APPLY", "1")
    assert ai_bad_habits.apply_watch_patch(proposal, repo)["applied"]


def test_apply_rejects_path_outside_allowlist(monkeypatch, tmp_path: Path) -> None:
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


# --- catalog integrity (allocation invariants) ------------------------------

_VALID_CH = {f"CH-{n:02d}" for n in range(1, 26)}
_ALLOWED_CATEGORIES = {"hygiene", "correctness", "process"}


def test_no_security_or_llm_allocation() -> None:
    # The refocus invariant: security taxonomies do not reappear in the catalog.
    for entry in ai_bad_habits.LOCAL_CATALOG:
        assert entry["category"] in _ALLOWED_CATEGORIES, entry["id"]
        blob = entry["id"] + entry["habit"] + entry["fix"] + "".join(entry["cross_links"])
        assert "CWE-" not in blob, f"{entry['id']} reintroduces a CWE id"
        # No OWASP LLM ids (LLM01..LLM10) as a *source/taxonomy* reference.
        for n in range(1, 11):
            assert f"LLM0{n}" not in blob and f"LLM{n}0" not in blob, entry["id"]


def test_crosslinks_are_real_ch_rules_only() -> None:
    for entry in ai_bad_habits.LOCAL_CATALOG:
        for link in entry["cross_links"]:
            assert link in _VALID_CH, f"{entry['id']} cross-links non-CH rule {link}"


def test_known_corrective_mappings() -> None:
    by_id = {e["id"]: e for e in ai_bad_habits.LOCAL_CATALOG}
    assert by_id["BH-02"]["cross_links"] == ("CH-04",)   # debug prints
    assert by_id["BH-04"]["cross_links"] == ("CH-08",)   # dup code
    assert by_id["BH-05"]["cross_links"] == ("CH-21",)   # tests omitted
    assert by_id["BH-07"]["cross_links"] == ("CH-23",)   # output shape-validation
    # The two correctness entries keep only the quality angle and point security
    # readers at @security.
    assert "@security" in by_id["BH-06"]["fix"]           # hallucinated deps
    assert "@security" in by_id["BH-07"]["fix"]           # unvalidated output


def test_bh_ids_unique_and_contiguous() -> None:
    ids = [e["id"] for e in ai_bad_habits.LOCAL_CATALOG]
    assert len(ids) == len(set(ids)) == 9
    nums = sorted(int(i.split("-")[1]) for i in ids)
    assert nums == list(range(1, 10))


def test_catalog_placeholder_resolves_with_no_unresolved_token() -> None:
    ph = ai_bad_habits.build_catalog_placeholders()
    assert set(ph) == {"AI_BAD_HABITS_CATALOG"}
    body = ph["AI_BAD_HABITS_CATALOG"]
    assert "{AI_BAD_HABITS_CATALOG}" not in body
    assert "BH-01" in body and "BH-09" in body
    assert "Corrective pattern" in body
    assert "Tracked upstream sources" not in body


def test_render_composes_catalog_body(tmp_path: Path) -> None:
    snap = ai_bad_habits.refresh_snapshot(_repo(tmp_path))
    full = ai_bad_habits.render_watch(snap)
    body = ai_bad_habits.build_catalog_placeholders()["AI_BAD_HABITS_CATALOG"]
    # Every catalog id in the shared body also appears in the root render.
    for entry in ai_bad_habits.LOCAL_CATALOG:
        assert entry["id"] in body
        assert entry["id"] in full
