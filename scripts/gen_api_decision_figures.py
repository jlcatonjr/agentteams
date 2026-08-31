#!/usr/bin/env python3
"""gen_api_decision_figures.py — decision-tree figures for the API reference.

Three per-page decision trees rendered with Graphviz `dot -Tsvg`, in the same house style as the
security-workflow subgraphs (`scripts/gen_security_workflow_graphs.py`): titled, role-coloured,
slate decision diamonds, red refuse/terminal path. Decision trees are branch structures, not
data-flows, so they use `dot` rather than the `agentteams.svg_render` DAG primitive.

Each tree's branch structure is verified against live source (line refs in the per-figure comments):
- emit action selection      → agentteams/emit.py:607-771 (real write-path, not the dry-run mirror)
- research backend fallback  → agentteams/research/backends.py:338-343 + search.py (web_search)
- model-routing tier         → agentteams/model_routing.py:50-53 (agent_tier)

Writes `docs_src/book/figures/api-clusters/api-decision-*.svg`. Requires graphviz `dot` on PATH.
Run: `python3 scripts/gen_api_decision_figures.py`
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs_src" / "book" / "figures" / "api-clusters"

#: role -> (fillcolor, shape, fontcolor) — reused from the security-workflow palette.
ROLE_STYLE: dict[str, tuple[str, str, str]] = {
    "goal": ("#1b2a4a", "box", "white"),
    "step": ("#4682b4", "box", "white"),
    "data": ("#e8edf2", "box", "#333333"),
    "decision": ("#708090", "diamond", "white"),
    "void": ("#b22222", "box", "white"),
}

#: backing files asserted to exist so a rename breaks the build loudly.
ARTIFACTS = (
    "agentteams/emit.py",
    "agentteams/research/backends.py",
    "agentteams/research/search.py",
    "agentteams/model_routing.py",
)


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


def build_dot(title: str, nodes: list[tuple[str, str, str]], edges: list[tuple]) -> str:
    lines = [
        "digraph ad {",
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


# D3 — emit_all per-file outcome. VERIFIED emit.py: root split `merge and target.exists()` (:608);
# machine-managed map (:616); shrink+halt -> shrink_blocked/refuse (:700); parse-error -> skipped
# (:726); clean merge changed? -> merged/unchanged (:732); non-merge exists&!overwrite -> skipped
# (:757); else on-disk identical? -> unchanged/written (:761). shrink_blocked is NOT `skipped`.
EMIT = (
    "api-decision-emit-action",
    "emit_all: per-file outcome (real write path)",
    [
        ("start", "each (path, content)", "data"),
        ("dmerge", "merge mode\n& file exists?", "decision"),
        ("dmm", "machine-managed\nmap path?", "decision"),
        ("dmmid", "existing == new?", "decision"),
        ("dhalt", "shrink notice\n& policy = halt?", "decision"),
        ("dparse", "merge parse error\n(no fence markers)?", "decision"),
        ("dchg", "content changed?", "decision"),
        ("dexow", "exists &\nnot overwrite?", "decision"),
        ("dondisk", "identical\non disk?", "decision"),
        ("written", "written", "goal"),
        ("merged", "merged", "step"),
        ("unchanged", "unchanged", "data"),
        ("skipped", "skipped", "data"),
        ("shrinkblk", "shrink_blocked\n(refuse write)", "void"),
    ],
    [
        ("start", "dmerge"),
        ("dmerge", "dmm", " yes "), ("dmerge", "dexow", " no "),
        ("dmm", "dmmid", " yes "), ("dmm", "dhalt", " no "),
        ("dmmid", "unchanged", " yes "), ("dmmid", "merged", " no "),
        ("dhalt", "shrinkblk", " yes ", True), ("dhalt", "dparse", " no "),
        ("dparse", "skipped", " yes "), ("dparse", "dchg", " no "),
        ("dchg", "unchanged", " no "), ("dchg", "merged", " yes "),
        ("dexow", "skipped", " yes "), ("dexow", "dondisk", " no "),
        ("dondisk", "unchanged", " yes "), ("dondisk", "written", " no "),
    ],
)

# D4 — research web_search backend fallback. VERIFIED against search.py:283-308: web_search ==
# web_search_verbose (:172); _try_chain over _BACKEND_ORDER (DDG-HTML, DDG-Lite, SearXNG, Brave,
# backends.py:338-343); no hits AND not challenged -> honest empty immediately (:290); only a
# challenge (202) triggers _broadening_forms -> PROGRESSIVE broaden toward a floor + retry (:296-308);
# still challenged-empty -> empty with "challenged" note (:298), distinct from "nothing matched".
RESEARCH = (
    "api-decision-research-backends",
    "research web_search: backend fallback",
    [
        ("query", "web query", "data"),
        ("chain", "try backends in order:\nDDG-HTML -> DDG-Lite ->\nSearXNG (host) -> Brave (key)\n"
                  "(unavailable ones skipped;\nfailures degrade, never raise)", "step"),
        ("dhit", "any backend\nreturned hits?", "decision"),
        ("dchal", "was the request\nchallenged (202)?", "decision"),
        ("broaden", "_broadening_forms:\nprogressively broaden\ntoward a floor + retry chain", "step"),
        ("dhit2", "hits after\nbroadening?", "decision"),
        ("results", "results", "goal"),
        ("zero", "honest zero\n(nothing matched)", "data"),
        ("chalzero", "challenged, no results\n(do not read as\n'nothing matched')", "void"),
    ],
    [
        ("query", "chain"), ("chain", "dhit"),
        ("dhit", "results", " yes "), ("dhit", "dchal", " no "),
        ("dchal", "zero", " no "), ("dchal", "broaden", " yes "),
        ("broaden", "dhit2"),
        ("dhit2", "results", " yes "), ("dhit2", "chalzero", " no ", True),
    ],
)

# D5 — model_routing.agent_tier. VERIFIED :50-53: cheap iff slug in governance_agents OR in
# _ALWAYS_CHEAP_SLUGS (critic, retrieval-policy, navigator, reference-manager, memory-index-query);
# else primary. `fallback` is a MODEL_TIERS resolution tier, NOT an agent_tier output.
ROUTING = (
    "api-decision-model-routing-tier",
    "model_routing.agent_tier: tier role",
    [
        ("slug", "agent slug + manifest", "data"),
        ("dgov", "in governance_agents?", "decision"),
        ("dcheap", "in _ALWAYS_CHEAP_SLUGS?\n(critic, retrieval-policy, navigator,\n"
                   "reference-manager, memory-index-query)", "decision"),
        ("cheap", "cheap", "goal"),
        ("primary", "primary", "goal"),
    ],
    [
        ("slug", "dgov"),
        ("dgov", "cheap", " yes "), ("dgov", "dcheap", " no "),
        ("dcheap", "cheap", " yes "), ("dcheap", "primary", " no "),
    ],
)

FIGURES = [EMIT, RESEARCH, ROUTING]


def _dot_available() -> bool:
    try:
        subprocess.run(["dot", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def main() -> int:
    if not _dot_available():
        raise SystemExit("graphviz `dot` not found on PATH — install graphviz to render the SVGs.")
    for art in ARTIFACTS:
        if not (REPO_ROOT / art).exists():
            raise SystemExit(f"decision figure references missing artifact: {art}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, title, nodes, edges in FIGURES:
        dot = build_dot(title, nodes, edges)
        proc = subprocess.run(["dot", "-Tsvg"], input=dot, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"dot failed for {slug}:\n{proc.stderr}")
        out = OUT_DIR / f"{slug}.svg"
        out.write_text(proc.stdout, encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT)} — {len(nodes)} nodes, {len(edges)} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
