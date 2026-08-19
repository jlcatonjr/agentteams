#!/usr/bin/env python3
"""gen_security_reliability_graph.py — directed graph of the security/reliability TEST architecture.

Drives the agentteams directed-graph renderer (`agentteams.svg_render`, the same primitive behind the
agent-team graph and the repository architecture map) with a *domain* node/edge set: the subsystem
that facilitates the security and reliability tests, from the payload corpus through the scoring
pipeline to the ratings output, plus the attack-generation harnesses that extend it, the safety/
integrity gates that make it trustworthy, and the meta-validation that checks the instruments.

Every node is a real artifact (a module, script, corpus, output, or gate) and every edge a real
data-flow or control/guard relationship — verified against the code. Deterministic: same input →
byte-identical SVG. Writes `docs_src/book/figures/security-reliability-test-architecture.svg`.

Run: `python3 scripts/gen_security_reliability_graph.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.svg_render import SvgEdge, SvgNode, auto_palette, render_svg  # noqa: E402

OUT_SVG = REPO_ROOT / "docs_src" / "book" / "figures" / "security-reliability-test-architecture.svg"

#: (id, label, kind, backing artifact) — the `kind` groups the node into a coloured layer; the
#: artifact column is the ground-truth file/symbol the node stands for (asserted to exist below).
NODES: list[tuple[str, str, str, str]] = [
    # --- inputs / corpus ---
    ("contract", "@security contract", "1-input", ".claude/agents/security.md"),
    ("payloads", "payload corpus (14)", "1-input", "tests/redteam/payloads.json"),
    ("taxonomy", "external taxonomy", "1-input", "references/redteam-external-taxonomy.json"),
    # --- execution harness ---
    ("matrix", "model-matrix run", "2-harness", "scripts/redteam_model_matrix_run.py"),
    ("judgment", "judgment run + drift gate", "2-harness", "scripts/redteam_judgment_run.py"),
    ("scorer", "score_response (scorer)", "2-harness", "tests/redteam/run_harness.py"),
    ("authjudge", "AUTH01 human-read verdict", "2-harness", "scripts/redteam_model_ratings.py"),
    # --- scoring ---
    ("collect", "collect() + score()", "3-scoring", "scripts/redteam_model_ratings.py"),
    ("secscore", "security_score", "3-scoring", "scripts/redteam_model_ratings.py"),
    ("relscore", "reliability_score", "3-scoring", "scripts/redteam_model_ratings.py"),
    ("avail", "availability / runs (R1)", "3-scoring", "scripts/redteam_model_ratings.py"),
    # --- outputs ---
    ("ratings", "ratings.csv + provenance", "4-output", "references/openweights-security-model-ratings.csv"),
    ("methodology", "scoring-methodology.md", "4-output", "references/scoring-methodology.md"),
    ("provenance", "provenance stamp", "4-output", "agentteams/provenance.py"),
    # --- attack-generation harnesses ---
    ("attackcore", "attack_gen core (8 rails)", "5-attackgen", "scripts/redteam_attack_gen.py"),
    ("h1", "H1 new-surface", "5-attackgen", "scripts/redteam_new_surface.py"),
    ("h2", "H2 adaptive attacker", "5-attackgen", "scripts/redteam_adaptive_attack.py"),
    ("h3", "H3 auto-gen + review judge", "5-attackgen", "scripts/redteam_attack_campaign.py"),
    ("coverage", "coverage + density", "5-attackgen", "agentteams/redteam/coverage.py"),
    ("quarantine", "quarantine (gitignored)", "5-attackgen", "tmp/redteam-attack-gen"),
    # --- safety / integrity gates ---
    ("s7", "S7 live-clearance interlock", "6-gate", "scripts/redteam_attack_gen.py"),
    ("secdecisions", "security-decisions.log", "6-gate", "references/security-decisions.log.csv"),
    ("promotiongate", "promotion-gate (S2)", "6-gate", "tests/test_redteam_promotion_gate.py"),
    ("integrity", "integrity manifest", "6-gate", "agentteams/integrity.py"),
    ("scan", "secret/PII scanner", "6-gate", "agentteams/scan.py"),
    # --- meta-validation ---
    ("oracle", "oracle inter-rater (kappa)", "7-meta", "scripts/redteam_oracle_intercheck.py"),
    ("weightsens", "weight-sensitivity", "7-meta", "scripts/redteam_weight_sensitivity.py"),
    ("contractsens", "contract-sensitivity", "7-meta", "scripts/redteam_contract_sensitivity.py"),
]

#: Directed edges — each a real data-flow (→ feeds) or control/guard relationship.
EDGES: list[tuple[str, str]] = [
    # core scoring flow: corpus + contract -> harness -> scorer -> scoring -> outputs
    ("payloads", "matrix"), ("payloads", "judgment"),
    ("contract", "matrix"), ("contract", "judgment"),
    ("matrix", "scorer"), ("judgment", "scorer"),
    ("scorer", "collect"), ("authjudge", "collect"),
    ("collect", "secscore"), ("collect", "relscore"), ("collect", "avail"),
    ("secscore", "ratings"), ("relscore", "ratings"), ("avail", "ratings"),
    ("provenance", "ratings"), ("ratings", "methodology"),
    # attack-generation: core -> H1/H2/H3 -> quarantine; taxonomy -> coverage -> H1
    ("attackcore", "h1"), ("attackcore", "h2"), ("attackcore", "h3"),
    ("taxonomy", "coverage"), ("coverage", "h1"),
    ("h1", "quarantine"), ("h2", "quarantine"), ("h3", "quarantine"),
    ("contract", "attackcore"), ("attackcore", "scorer"), ("provenance", "attackcore"),
    # safety / integrity gates. The promotion gate GUARDS the corpus boundary (it checks that no
    # quarantined candidate reaches `payloads` without review) — a terminal guard, NOT a feed into
    # the corpus, so there is deliberately no promotiongate->payloads edge (that would rank an input
    # as downstream of quarantine and misread the flow).
    ("secdecisions", "s7"), ("s7", "attackcore"),
    ("quarantine", "promotiongate"),
    ("integrity", "judgment"), ("scan", "contract"),
    # meta-validation of the instruments
    ("scorer", "oracle"), ("authjudge", "oracle"),
    ("ratings", "weightsens"), ("contract", "contractsens"),
    ("oracle", "methodology"), ("weightsens", "methodology"), ("contractsens", "methodology"),
]


def _assert_artifacts_exist() -> None:
    """Ground-truth check: every node's backing artifact must exist on disk (quarantine may not)."""
    missing = []
    for node_id, _label, _kind, artifact in NODES:
        path = REPO_ROOT / artifact.split("::")[0]
        # the quarantine dir is created on demand (gitignored) — its absence is expected
        if node_id == "quarantine":
            continue
        if not path.exists():
            missing.append(f"{node_id} -> {artifact}")
    if missing:
        raise SystemExit("graph references artifacts that do not exist:\n  " + "\n  ".join(missing))


def build_svg() -> str:
    _assert_artifacts_exist()
    node_ids = {n[0] for n in NODES}
    for src, dst in EDGES:
        if src not in node_ids or dst not in node_ids:
            raise SystemExit(f"edge references an unknown node: {src} -> {dst}")
    kinds = [n[2] for n in NODES]
    palette = auto_palette(kinds)
    svg_nodes = [SvgNode(id=n[0], label=n[1], kind=n[2]) for n in NODES]
    svg_edges = [SvgEdge(src=s, dst=d) for s, d in EDGES]
    return render_svg(svg_nodes, svg_edges, palette=palette,
                      title="Security & reliability test architecture")


def main() -> int:
    svg = build_svg()
    OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
    OUT_SVG.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT_SVG.relative_to(REPO_ROOT)} — {len(NODES)} nodes, {len(EDGES)} edges, "
          f"{len(svg):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
