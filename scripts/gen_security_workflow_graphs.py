#!/usr/bin/env python3
"""gen_security_workflow_graphs.py — security/reliability test architecture as workflow subgraphs.

Decomposes the single overview graph (security-workflows/security-reliability-test-architecture.svg)
into five focused subgraphs, each rendered in the SAME visual style as the book's existing
`docs_src/book/figures/workflow-NN-*.svg` diagrams: Graphviz, portrait/top-to-bottom, a bold
`Security Workflow N: <name>` title, rounded filled role-nodes, slate diamonds for decisions,
numbered/labeled edges, and red for reject/VOID paths.

Style is reproduced via `dot -Tsvg` (graphviz), not the agentteams `svg_render` primitive, precisely
so the subgraphs match the existing workflow figures rather than the horizontal overview graph. Every
node is a real artifact and every edge/decision a real relationship — the same fidelity bar the
overview graph passed under adversarial audit.

Run: `python3 scripts/gen_security_workflow_graphs.py`  (requires graphviz `dot` on PATH)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs_src" / "book" / "figures" / "security-workflows"

#: role -> (fillcolor, shape, fontcolor) — the reverse-engineered workflow-NN palette.
ROLE_STYLE: dict[str, tuple[str, str, str]] = {
    "goal": ("#1b2a4a", "box", "white"),        # entry / terminal goal (like the orchestrator node)
    "step": ("#4682b4", "box", "white"),        # a process / script / run step
    "highlight": ("#b8860b", "box", "white"),   # a load-bearing / human-read step
    "data": ("#e8edf2", "box", "#333333"),      # a corpus / artifact / value (dark text on light)
    "decision": ("#708090", "diamond", "white"),  # a gate / branch
    "void": ("#b22222", "box", "white"),        # a VOID / refused / HALT path
}


def _dot(node_id: str, label: str, role: str) -> str:
    fill, shape, fontcolor = ROLE_STYLE[role]
    lbl = label.replace('"', '\\"')
    return (f'  {node_id} [label="{lbl}", fillcolor="{fill}", shape={shape}, '
            f'fontcolor="{fontcolor}"];')


def _edge(src: str, dst: str, label: str = "", red: bool = False, dashed: bool = False) -> str:
    attrs = []
    if label:
        attrs.append(f'label="{label}"')
    if red:
        attrs.append('color="#b22222"')
    if dashed:
        attrs.append('style=dashed')
    a = f' [{", ".join(attrs)}]' if attrs else ""
    return f"  {src} -> {dst}{a};"


def build_dot(title: str, nodes: list[tuple[str, str, str]],
              edges: list[tuple]) -> str:
    lines = [
        "digraph sw {",
        '  rankdir=TB; bgcolor="#fafafa";',
        f'  labelloc="t"; label="{title}"; fontname="Helvetica"; fontsize=16; fontcolor="#1b2a4a";',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=14, '
        'fontcolor="white", margin="0.16,0.09"];',
        '  edge [color="#333333", fontname="Helvetica", fontsize=11];',
    ]
    lines += [_dot(*n) for n in nodes]
    lines += [_edge(*e) for e in edges]
    lines.append("}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------
# The five workflows. Node tuples: (id, label, role). Edge tuples: (src, dst[, label[, red[, dashed]]]).
# Every node's backing artifact is named in ARTIFACTS below and asserted to exist.
# --------------------------------------------------------------------------------------------------

WORKFLOWS: list[tuple[str, str, list, list]] = [
    (
        "security-workflow-01-security-scoring",
        "Security Workflow 1: Security Scoring",
        [
            ("corpus", "payload corpus (14)", "data"),
            ("contract", "@security contract", "data"),
            ("contractarm", "contract arm\n(matrix / judgment run)", "step"),
            ("ablatedarm", "ablated arm\n(contract REMOVED)", "step"),
            ("scorer", "score_response (scorer)", "step"),
            ("poscontrol", "positive control:\nablated COMPLY?", "decision"),
            ("void", "VOID\n(detector blind)", "void"),
            ("authjudge", "AUTH01 human-read\nverdict", "highlight"),
            ("collect", "collect() + score()", "step"),
            ("resistance", "resistance /40\n(ablated capitulations)", "data"),
            ("judgment", "judgment /30\n(AUTH01)", "data"),
            ("operability", "operability /20\n(parseable)", "data"),
            ("gate", "contract gate /10", "data"),
            ("secscore", "security_score", "goal"),
            ("acceptable", "acceptable?\n(>=70 and both gates)", "decision"),
        ],
        [
            ("corpus", "contractarm", " 1 "), ("contract", "contractarm"),
            ("corpus", "ablatedarm", " 1 "),
            ("contractarm", "scorer", " 2 "), ("ablatedarm", "scorer", " 2 "),
            ("ablatedarm", "poscontrol"),
            ("poscontrol", "void", " no ", True, True), ("poscontrol", "collect", " yes "),
            ("scorer", "collect", " 3 "), ("authjudge", "judgment"),
            ("collect", "resistance"), ("collect", "operability"), ("collect", "gate"),
            ("resistance", "secscore"), ("judgment", "secscore"),
            ("operability", "secscore"), ("gate", "secscore"),
            ("secscore", "acceptable", " 4 "),
        ],
    ),
    (
        "security-workflow-02-reliability-scoring",
        "Security Workflow 2: Reliability Scoring (ISO/IEC 25010)",
        [
            ("runs", "run artifacts\n(per model)", "data"),
            ("parseable", "parseable rate", "data"),
            ("maturity", "maturity /40", "step"),
            ("transport", "transport_failures\n(inverted)", "data"),
            ("fault", "fault_tolerance /60", "step"),
            ("repeat", "repeat runs (R1)", "data"),
            ("avail", "availability / runs\n(measured, uniformly 1.0)", "highlight"),
            ("relscore", "reliability_score\n(maturity + fault_tolerance)", "goal"),
            ("unscored", "availability: reported, not scored\nrecoverability: NOT measured", "data"),
        ],
        [
            ("runs", "parseable", " 1 "), ("parseable", "maturity", " 2 "),
            ("runs", "transport", " 1 "), ("transport", "fault", " 2 "),
            ("maturity", "relscore", " 3 "), ("fault", "relscore", " 3 "),
            ("repeat", "avail"), ("avail", "unscored", " non-discriminating ", False, True),
            # NOTE: no unscored -> relscore edge — availability/recoverability are excluded from
            # reliability_total (= maturity + fault_tolerance), not summed into it.
        ],
    ),
    (
        "security-workflow-03-attack-generation",
        "Security Workflow 3: Attack Generation (H1/H2/H3)",
        [
            ("secdecisions", "security-decisions.log", "data"),
            ("s7", "S7 interlock:\ncleared-for-live?", "decision"),
            ("refused", "refused\n(no live run)", "void"),
            ("attackcore", "attack_gen core\n(8 safety rails)", "step"),
            ("coverage", "coverage + density", "data"),
            ("h1", "H1 new-surface", "step"),
            ("h2", "H2 adaptive attacker", "step"),
            ("h3", "H3 auto-gen + review", "step"),
            ("quarantine", "quarantine (gitignored)", "data"),
            ("corpus", "payload corpus", "data"),
            ("promo", "promotion-gate:\ncandidate in corpus\nwithout review?", "decision"),
            ("fail", "test FAILS\n(unreviewed promotion)", "void"),
            ("ok", "no unreviewed\ncandidate in corpus", "step"),
        ],
        [
            ("secdecisions", "s7", " 1 "),
            ("s7", "refused", " no ", True, True), ("s7", "attackcore", " yes (cleared) "),
            # Only H2/H3 make live calls and pass through the S7-gated core. H1 is hand-authored,
            # makes NO live call, and uses only the quarantine-write primitive — so it is fed by
            # coverage/density, NOT by the S7-gated live core.
            ("attackcore", "h2", " 2 "), ("attackcore", "h3", " 2 "),
            ("coverage", "h1"),
            ("h1", "quarantine", " 3 "), ("h2", "quarantine", " 3 "), ("h3", "quarantine", " 3 "),
            ("quarantine", "promo", " 4 "), ("corpus", "promo"),
            ("promo", "fail", " yes ", True, True), ("promo", "ok", " no "),
        ],
    ),
    (
        "security-workflow-04-safety-integrity-gates",
        "Security Workflow 4: Safety & Integrity Gates",
        [
            ("instance", "@security instance\n(.claude/agents/security.md)", "data"),
            ("drift", "build-log drift gate:\ninstance drifted?", "decision"),
            ("refuse", "judgment run REFUSED", "void"),
            ("judgment", "judgment run proceeds", "step"),
            ("manifest", "enforcement integrity\nmanifest (10 modules)", "data"),
            ("hook", "constitutional-gate hook", "highlight"),
            ("mismatch", "scan.py hash\nmismatch? (probe E4)", "decision"),
            ("ask", "ASK\n(operator confirm)", "highlight"),
            ("content", "content being written", "data"),
            ("highsev", "high-severity\nsecret/PII finding?", "decision"),
            ("halt", "HALT (deny)", "void"),
        ],
        [
            ("instance", "drift", " 1 "),
            ("drift", "refuse", " drifted ", True, True), ("drift", "judgment", " clean "),
            # The hook gates two things. (a) scan.py integrity: a hash mismatch triggers ASK
            # (operator confirm), NOT a hard deny — a stale manifest after a legitimate scan.py edit
            # must not brick the session. (b) content scan: a high-severity finding on the written
            # content is the real HALT (deny).
            ("manifest", "hook", " covers scan.py "),
            ("hook", "mismatch", " 2 "), ("mismatch", "ask", " yes "),
            ("content", "hook", " scanned "),
            ("hook", "highsev"), ("highsev", "halt", " yes ", True, True),
        ],
    ),
    (
        "security-workflow-05-meta-validation",
        "Security Workflow 5: Meta-Validation of the Instruments",
        [
            ("responses", "preserved auth-01\nresponses", "data"),
            ("oracle", "oracle inter-rater\n(2nd model)", "step"),
            ("kappa", "Cohen's kappa ~0.91", "highlight"),
            ("ratings", "ratings.csv", "data"),
            ("weightsens", "weight-sensitivity\n(alt schemes)", "step"),
            ("contracts", "@security contract v1 / v2", "data"),
            ("contractsens", "contract-sensitivity", "step"),
            ("methodology", "scoring-methodology.md", "goal"),
        ],
        [
            ("responses", "oracle", " 1 "), ("oracle", "kappa", " 2 "),
            ("ratings", "weightsens", " 1 "),
            ("contracts", "contractsens", " 1 "),
            ("kappa", "methodology", " 3 "),
            ("weightsens", "methodology", " 3 "),
            ("contractsens", "methodology", " 3 "),
        ],
    ),
]

#: node id -> backing artifact (repo-relative), asserted to exist. Nodes that are computed values or
#: sub-components of a script share that script's path; a decision/void is a real branch in one.
ARTIFACTS: dict[str, str] = {
    "corpus": "tests/redteam/payloads.json", "contract": ".claude/agents/security.md",
    "contractarm": "scripts/redteam_model_matrix_run.py", "ablatedarm": "scripts/redteam_model_matrix_run.py",
    "scorer": "tests/redteam/run_harness.py", "poscontrol": "scripts/redteam_model_matrix_run.py",
    "authjudge": "scripts/redteam_model_ratings.py", "collect": "scripts/redteam_model_ratings.py",
    "secscore": "scripts/redteam_model_ratings.py", "runs": "tmp/redteam-matrix",
    "maturity": "scripts/redteam_model_ratings.py", "fault": "scripts/redteam_model_ratings.py",
    "avail": "scripts/redteam_model_ratings.py", "relscore": "scripts/redteam_model_ratings.py",
    "secdecisions": "references/security-decisions.log.csv", "s7": "scripts/redteam_attack_gen.py",
    "attackcore": "scripts/redteam_attack_gen.py", "coverage": "agentteams/redteam/coverage.py",
    "h1": "scripts/redteam_new_surface.py", "h2": "scripts/redteam_adaptive_attack.py",
    "h3": "scripts/redteam_attack_campaign.py", "promo": "tests/test_redteam_promotion_gate.py",
    "instance": ".claude/agents/security.md", "drift": "agentteams/drift.py",
    "judgment": "scripts/redteam_judgment_run.py", "manifest": "agentteams/integrity.py",
    "content": "agentteams/scan.py", "hook": ".github/hooks/constitutional-gate.py",
    "responses": "scripts/redteam_oracle_intercheck.py", "oracle": "scripts/redteam_oracle_intercheck.py",
    "ratings": "references/openweights-security-model-ratings.csv",
    "weightsens": "scripts/redteam_weight_sensitivity.py",
    "contractsens": "scripts/redteam_contract_sensitivity.py",
    "methodology": "references/scoring-methodology.md",
}


def _assert_artifacts_exist() -> None:
    """Every node that names a backing artifact must resolve on disk (tmp/ dirs excepted)."""
    referenced = {n[0] for _s, _t, nodes, _e in WORKFLOWS for n in nodes}
    missing = []
    for node_id in referenced:
        art = ARTIFACTS.get(node_id)
        if art is None:
            continue  # pure decision/value/void nodes (refused/void/fail/ok/parseable/…) have no file
        p = REPO_ROOT / art
        if art.startswith("tmp/"):
            continue
        if not p.exists():
            missing.append(f"{node_id} -> {art}")
    if missing:
        raise SystemExit("workflow graph references missing artifacts:\n  " + "\n  ".join(missing))


def main() -> int:
    if not _dot_available():
        raise SystemExit("graphviz `dot` not found on PATH — install graphviz to render the SVGs.")
    _assert_artifacts_exist()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, title, nodes, edges in WORKFLOWS:
        dot = build_dot(title, nodes, edges)
        out = OUT_DIR / f"{slug}.svg"
        proc = subprocess.run(["dot", "-Tsvg"], input=dot, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"dot failed for {slug}:\n{proc.stderr}")
        out.write_text(proc.stdout, encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT)} — {len(nodes)} nodes, {len(edges)} edges")
    return 0


def _dot_available() -> bool:
    try:
        subprocess.run(["dot", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
