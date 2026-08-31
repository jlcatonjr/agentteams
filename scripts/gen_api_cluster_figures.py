#!/usr/bin/env python3
"""gen_api_cluster_figures.py — cluster-level data-flow/topology figures for the API Reference.

Drives the agentteams directed-graph renderer (`agentteams.svg_render`, the same primitive behind
the agent-team graph, the repository architecture map, and the security-workflow figures) with a
node/edge set per *documentation cluster* under `docs_src/api-reference/`. Each figure is embedded on
its cluster's lead page to give a reader the shape of a family the per-page docs only describe in prose.

Every node with a non-empty `artifact` is a real file (asserted to exist below); pure data-label
nodes carry `artifact=""` and are not asserted. Every edge is a real data-flow / uses relationship.
Deterministic: same input -> byte-identical SVG.

Writes `docs_src/book/figures/api-clusters/api-cluster-*.svg`.

Run: `python3 scripts/gen_api_cluster_figures.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams.svg_render import SvgEdge, SvgNode, auto_palette, render_svg  # noqa: E402

OUT_DIR = REPO_ROOT / "docs_src" / "book" / "figures" / "api-clusters"

# Each figure: (slug, title, nodes, edges).
# nodes: (id, label, kind, artifact)  — artifact "" means "data label, not a file".
# edges: (src, dst)

# --- C1 Pipeline: ingest -> analyze -> render -> emit, with two carve-outs ---
PIPELINE_NODES = [
    ("brief", "brief.json / .md", "data", ""),
    ("ingest", "ingest", "stage", "agentteams/ingest.py"),
    ("desc", "description dict", "data", ""),
    ("analyze", "analyze", "stage", "agentteams/analyze.py"),
    ("mfmt", "manifest_format (carve-out)", "carveout", "agentteams/manifest_format.py"),
    ("manifest", "team manifest", "data", ""),
    ("render", "render", "stage", "agentteams/render.py"),
    ("adapter", "framework adapter", "stage", "agentteams/frameworks/registry.py"),
    ("emit", "emit", "stage", "agentteams/emit.py"),
    ("fences", "fences (carve-out)", "carveout", "agentteams/fences.py"),
    ("files", "agent files on disk", "data", ""),
]
PIPELINE_EDGES = [
    ("brief", "ingest"), ("ingest", "desc"), ("desc", "analyze"),
    ("analyze", "mfmt"), ("analyze", "manifest"), ("manifest", "render"),
    ("render", "adapter"), ("adapter", "emit"), ("emit", "fences"), ("emit", "files"),
]

# --- C8 Pinned Sync: peer frameworks <-> canonical hub, pin authoritative ---
SYNC_NODES = [
    ("frameworks", "native framework teams", "data", ""),
    ("canonical", "canonical hub", "hub", "agentteams/canonical.py"),
    ("multisync", "multi_sync orchestrator", "module", "agentteams/multi_sync.py"),
    ("classifier", "sync_classifier (3-way)", "module", "agentteams/sync_classifier.py"),
    ("baseline", "sync_baseline snapshot", "module", "agentteams/sync_baseline.py"),
    ("pin", "sync_pin contract", "module", "agentteams/sync_pin.py"),
    ("conflictlog", "conflict-log.csv", "data", ""),
]
SYNC_EDGES = [
    ("frameworks", "canonical"), ("canonical", "frameworks"),
    ("multisync", "canonical"), ("multisync", "classifier"),
    ("classifier", "baseline"), ("baseline", "canonical"),
    ("pin", "multisync"), ("multisync", "conflictlog"),
]

# --- C6 Manifest/Docs: one steps.csv, three readers, distinct consumers ---
STEPS_NODES = [
    ("csv", "<slug>.steps.csv", "data", ""),
    ("plansteps", "plan_steps (dict rows)", "module", "agentteams/plan_steps.py"),
    ("todo", "plan_steps_todo (strict 11-col)", "module", "agentteams/plan_steps_todo.py"),
    ("parallel", "parallel_plan (tolerant 7-col)", "module", "agentteams/parallel_plan.py"),
    ("handoff", "handoff_payloads.audit_handoff_chain", "module", "agentteams/handoff_payloads.py"),
    ("todoout", "TodoWrite projection", "data", ""),
    ("waves", "WaveSchedule (compute_waves)", "data", ""),
    ("findings", "typed-payload Findings", "data", ""),
]
STEPS_EDGES = [
    ("csv", "plansteps"), ("csv", "todo"), ("csv", "parallel"), ("csv", "handoff"),
    ("todo", "todoout"), ("parallel", "waves"), ("handoff", "findings"),
]

# --- C7 Host features: --target-host-features token -> emitter -> output path ---
HOSTFEAT_NODES = [
    ("flag", "--target-host-features", "data", ""),
    ("validate", "host_features.parse/validate", "hub", "agentteams/host_features.py"),
    ("subagents", "bridge_subagents", "module", "agentteams/bridge_subagents.py"),
    ("hooks", "hooks_emit", "module", "agentteams/hooks_emit.py"),
    ("split", "instructions_split", "module", "agentteams/instructions_split.py"),
    ("schedule", "schedule_emit", "module", "agentteams/schedule_emit.py"),
    ("mcp", "mcp_emit", "module", "agentteams/mcp_emit.py"),
    ("codexmcp", "codex_mcp_emit", "module", "agentteams/codex_mcp_emit.py"),
    ("goosesub", "bridge_subagents_goose", "module", "agentteams/bridge_subagents_goose.py"),
    ("claudeout", ".claude/ artifacts", "data", ""),
    ("codexout", ".codex/config.toml", "data", ""),
    ("gooseout", ".goose/recipes/", "data", ""),
]
HOSTFEAT_EDGES = [
    ("flag", "validate"),
    ("validate", "subagents"), ("validate", "hooks"), ("validate", "split"),
    ("validate", "schedule"), ("validate", "mcp"), ("validate", "codexmcp"),
    ("validate", "goosesub"),
    ("subagents", "claudeout"), ("hooks", "claudeout"), ("split", "claudeout"),
    ("schedule", "claudeout"), ("mcp", "claudeout"),
    ("codexmcp", "codexout"), ("goosesub", "gooseout"),
]

# --- C4 Enrichment/retrieval: two index families + satellites ---
RETRIEVAL_NODES = [
    ("prose", "work summaries + docs", "data", ""),
    ("memidx", "memory_index (BM25 / sparse vector)", "module", "agentteams/memory_index.py"),
    ("incremental", "memory_index_incremental", "module", "agentteams/memory_index_incremental.py"),
    ("scripts", "repo scripts", "data", ""),
    ("codesrc", "code_sources (resolve/partition)", "module", "agentteams/code_sources.py"),
    ("codeidx", "code_index (code & API)", "module", "agentteams/code_index.py"),
    ("query", "--query-index / --query-code", "data", ""),
]
RETRIEVAL_EDGES = [
    ("prose", "memidx"), ("memidx", "incremental"), ("memidx", "query"),
    ("scripts", "codesrc"), ("codesrc", "codeidx"), ("codeidx", "query"),
]

# --- C3 post-emit audit chain: emit -> audit -> remediate, with standing guards ---
# Edges verified against source: audit.py:24,205 imports/calls living_doc (livingdoc->audit);
# remediate.py:39,54,161 consumes audit findings (audit->remediate); scan.py scans generated files
# (emit->scan); integrity.py:35-49 ENFORCEMENT_MODULES covers scan.py + redteam checks
# (integrity->scan, integrity->redteam). feature_audit is NOT imported by audit -> omitted.
AUDITCHAIN_NODES = [
    ("emit", "emit (agent files)", "stage", "agentteams/emit.py"),
    ("audit", "audit (static + AI)", "stage", "agentteams/audit.py"),
    ("remediate", "remediate (auto-correct)", "stage", "agentteams/remediate.py"),
    ("livingdoc", "living_doc (Rule 7)", "feeder", "agentteams/living_doc.py"),
    ("scan", "scan (content scanner)", "guard", "agentteams/scan.py"),
    ("integrity", "integrity (enforcer hash manifest)", "guard", "agentteams/integrity.py"),
    ("redteam", "redteam (standing battery)", "guard", "agentteams/redteam/registry.py"),
]
AUDITCHAIN_EDGES = [
    ("emit", "audit"), ("audit", "remediate"), ("livingdoc", "audit"),
    ("emit", "scan"), ("integrity", "scan"), ("integrity", "redteam"),
]

# --- C5 git-hooks: three independent, separately-guarded refreshes (NOT one fan-out) ---
# Verified git_hooks.py:384-408: agent-files staged -> pipeline-graph; *.py staged -> architecture;
# opt-in script/dep files -> gitignored code-index cache (no git add). Distinct trigger conditions.
GITHOOKS_NODES = [
    ("hook", "pre-commit block (git_hooks)", "stage", "agentteams/git_hooks.py"),
    ("c_agent", "agent files staged", "cond", ""),
    ("c_py", "*.py files staged", "cond", ""),
    ("c_dep", "script/dep files staged (opt-in)", "cond", ""),
    ("r_graph", "refresh pipeline-graph", "refresh", "agentteams/graph.py"),
    ("r_arch", "refresh architecture maps", "refresh", "agentteams/architecture.py"),
    ("r_code", "warm code-index (gitignored)", "refresh", "agentteams/code_index.py"),
    ("o_graph", "pipeline-graph.md/.svg", "data", ""),
    ("o_arch", "architecture-graph + modules.svg", "data", ""),
    ("o_code", "code-index cache", "data", ""),
]
GITHOOKS_EDGES = [
    ("hook", "c_agent"), ("hook", "c_py"), ("hook", "c_dep"),
    ("c_agent", "r_graph"), ("c_py", "r_arch"), ("c_dep", "r_code"),
    ("r_graph", "o_graph"), ("r_arch", "o_arch"), ("r_code", "o_code"),
]

FIGURES = [
    ("api-cluster-pipeline", "Pipeline: ingest to emit", PIPELINE_NODES, PIPELINE_EDGES),
    ("api-cluster-audit-chain", "Post-emit audit chain + standing guards", AUDITCHAIN_NODES, AUDITCHAIN_EDGES),
    ("api-fig-git-hooks", "git-hooks: three guarded refreshes", GITHOOKS_NODES, GITHOOKS_EDGES),
    ("api-cluster-pinned-sync", "Multi-framework pinned sync", SYNC_NODES, SYNC_EDGES),
    ("api-cluster-plan-steps", "steps.csv: three readers + a chain check", STEPS_NODES, STEPS_EDGES),
    ("api-cluster-host-features", "Host-feature token to emitter to output", HOSTFEAT_NODES, HOSTFEAT_EDGES),
    ("api-cluster-retrieval", "Retrieval: memory & code indexes", RETRIEVAL_NODES, RETRIEVAL_EDGES),
]


def _assert(nodes: list[tuple[str, str, str, str]], edges: list[tuple[str, str]], slug: str) -> None:
    ids = {n[0] for n in nodes}
    for node_id, _label, _kind, artifact in nodes:
        if artifact and not (REPO_ROOT / artifact).exists():
            raise SystemExit(f"[{slug}] node {node_id!r} artifact missing: {artifact}")
    for src, dst in edges:
        if src not in ids or dst not in ids:
            raise SystemExit(f"[{slug}] edge {src!r}->{dst!r} references unknown node")


def _render(nodes: list[tuple[str, str, str, str]], edges: list[tuple[str, str]], title: str) -> str:
    kinds = [n[2] for n in nodes]
    palette = auto_palette(kinds)
    svg_nodes = [SvgNode(id=n[0], label=n[1], kind=n[2]) for n in nodes]
    svg_edges = [SvgEdge(src=s, dst=d) for s, d in edges]
    return render_svg(svg_nodes, svg_edges, palette=palette, title=title)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, title, nodes, edges in FIGURES:
        _assert(nodes, edges, slug)
        svg = _render(nodes, edges, title)
        out = OUT_DIR / f"{slug}.svg"
        out.write_text(svg, encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT)} — {len(nodes)} nodes, {len(edges)} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
