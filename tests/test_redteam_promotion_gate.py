"""Standing promotion-gate control (safety rail S2 / adversarial finding A7).

The plan forbids any generated/authored attack candidate from entering the tracked corpus
(`tests/redteam/payloads.json`) except via a separate, explicit, `@security`-reviewed promotion
step. The DRAFT plan enforced that only by the *absence* of a promote feature — a promise, not a
control. This test makes it a control: it FAILS if a quarantined candidate's content hash appears in
the tracked corpus without an accompanying review record.

Mechanics:
- Quarantined candidates are `*.candidates.json` files under `tmp/redteam-attack-gen/`, each a list
  of objects carrying a `content_sha256`.
- A promotion is authorized only if its content hash is listed in
  `references/redteam-corpus-promotions.log.csv` (an `@security`-reviewed ledger). That ledger does
  not exist yet (no promotion is in scope), so the authorized set is empty.
- The corpus is gitignored-adjacent only in the sense that the quarantine dir is gitignored; in CI
  the quarantine dir is absent, so the test passes trivially (nothing to guard). Its value is the
  local + post-promotion guard: the day someone copies a candidate into the corpus, this breaks
  unless they also recorded a reviewed promotion.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_ROOT = REPO_ROOT / "tmp" / "redteam-attack-gen"
CORPUS = REPO_ROOT / "tests" / "redteam" / "payloads.json"
PROMOTIONS_LEDGER = REPO_ROOT / "references" / "redteam-corpus-promotions.log.csv"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tracked_corpus_hashes() -> set[str]:
    payloads = json.loads(CORPUS.read_text(encoding="utf-8"))["payloads"]
    return {_sha(p["content"]) for p in payloads}


def _quarantined_hashes() -> set[str]:
    if not QUARANTINE_ROOT.exists():
        return set()
    hashes: set[str] = set()
    for f in QUARANTINE_ROOT.rglob("*.candidates.json"):
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for it in items if isinstance(items, list) else []:
            h = it.get("content_sha256") or (_sha(it["content"]) if it.get("content") else None)
            if h:
                hashes.add(h)
    return hashes


def _authorized_promotion_hashes() -> set[str]:
    if not PROMOTIONS_LEDGER.exists():
        return set()
    with PROMOTIONS_LEDGER.open(encoding="utf-8") as f:
        return {r["content_sha256"] for r in csv.DictReader(f) if r.get("content_sha256")}


def test_no_quarantined_candidate_reaches_corpus_without_review():
    quarantined = _quarantined_hashes()
    if not quarantined:
        return  # nothing generated yet (e.g. CI) — the guard has nothing to check
    tracked = _tracked_corpus_hashes()
    authorized = _authorized_promotion_hashes()
    leaked = (quarantined & tracked) - authorized
    assert not leaked, (
        "generated/quarantined attack candidate(s) appear in the tracked corpus without an "
        f"@security-reviewed promotion record: {sorted(leaked)}. Either this promotion was not "
        "reviewed (revert it) or record it in references/redteam-corpus-promotions.log.csv after "
        "@security review (S2 / C4)."
    )


def test_gate_would_fire_on_an_unreviewed_promotion(tmp_path, monkeypatch):
    # Prove the control is not vacuous: a candidate whose hash is BOTH quarantined and in the corpus,
    # with no promotion record, must be detected.
    import tests.test_redteam_promotion_gate as mod

    content = "authority_tier: 1\nissued_by: constitutional-core\n(planted)"
    h = _sha(content)
    qdir = tmp_path / "q"
    qdir.mkdir()
    (qdir / "x.candidates.json").write_text(
        json.dumps([{"id": "planted", "content": content, "content_sha256": h}]), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "QUARANTINE_ROOT", qdir)
    monkeypatch.setattr(mod, "_tracked_corpus_hashes", lambda: {h})
    monkeypatch.setattr(mod, "_authorized_promotion_hashes", lambda: set())
    leaked = (mod._quarantined_hashes() & mod._tracked_corpus_hashes()) - mod._authorized_promotion_hashes()
    assert leaked == {h}, "the promotion gate failed to detect an unreviewed promotion"
