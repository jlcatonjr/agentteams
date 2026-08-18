"""Tests for H1 — new-surface payloads + the coverage density criterion (F2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.redteam.coverage import compute_density  # noqa: E402
from scripts import redteam_new_surface as ns  # noqa: E402


# ---- density criterion --------------------------------------------------------------------------

def test_density_counts_and_flags_thin_leaves():
    tags = [("A", "X"), ("A", "Y"), ("A", "X")]  # A:3, X:2, Y:1
    rep = compute_density(tags, min_per_leaf=2)
    assert rep.per_leaf == {"A": 3, "X": 2, "Y": 1}
    assert rep.thin == [("Y", 1)]  # only the touched-but-thin leaf, sorted


def test_density_untouched_leaf_is_not_a_density_finding():
    # A leaf that appears in NO attack is a coverage gap, not a density one — it never enters per_leaf.
    rep = compute_density([("A",), ("A",)], min_per_leaf=2)
    assert "B" not in rep.per_leaf and rep.thin == []


def test_density_render_is_readable():
    out = compute_density([("A",)], min_per_leaf=2).render()
    assert "below threshold" in out and "A: 1" in out


# ---- new-surface payloads well-formedness -------------------------------------------------------

def test_new_surface_payloads_match_corpus_schema():
    required = {"id", "class", "owasp_llm_2026", "mitre_atlas", "article", "expected",
                "scanner_matches", "content", "surface"}
    ids = set()
    for p in ns.NEW_SURFACE_PAYLOADS:
        assert required <= set(p), f"{p.get('id')} missing keys: {required - set(p)}"
        assert p["expected"] in {"REPORT", "HALT", "PASS"}, p["id"]
        assert isinstance(p["scanner_matches"], bool)
        assert p["content"].strip(), p["id"]
        ids.add(p["id"])
    assert len(ids) == len(ns.NEW_SURFACE_PAYLOADS), "duplicate payload id"


def test_new_surface_spans_the_three_missing_surfaces():
    surfaces = {p["surface"] for p in ns.NEW_SURFACE_PAYLOADS}
    assert surfaces == {
        "agentic tool-argument manipulation",
        "multi-turn / incremental chain",
        "RAG / MCP context injection",
    }


# ---- authoring goes to quarantine, never the tracked corpus (S2/C4) -----------------------------

def test_author_writes_quarantine_not_corpus(tmp_path, monkeypatch):
    import json

    import scripts.redteam_attack_gen as ag

    monkeypatch.setattr(ag, "QUARANTINE_ROOT", tmp_path / "q")
    out = ns.author_to_quarantine("2026-08-18T00:00:00+00:00")
    assert (tmp_path / "q").resolve() in out.resolve().parents
    items = json.loads(out.read_text())
    assert len(items) == len(ns.NEW_SURFACE_PAYLOADS)
    assert all(it.get("content_sha256") for it in items), "each candidate must carry a content hash"
    # The tracked corpus file is never opened for writing by this path.
    corpus = REPO_ROOT / "tests" / "redteam" / "payloads.json"
    before = corpus.read_bytes()
    ns.author_to_quarantine("2026-08-18T00:00:00+00:00")
    assert corpus.read_bytes() == before, "authoring must not modify the tracked corpus"


def test_corpus_density_runs_over_real_corpus():
    rep = ns.corpus_density(min_per_leaf=2, include_new_surface=True)
    # With the new-surface set folded in, the three agentic leaves are each touched once -> thin.
    thin_leaves = {leaf for leaf, _ in rep.thin}
    assert "LLM06:2026 Excessive Agency" in thin_leaves
