#!/usr/bin/env python3
"""gen_book_workflow_graphs.py — directed graphs for the 12 orchestrator workflows (book figures).

Ported from jlcatonjr/jameslcaton@bf7a81fe
`workingpapers/agentTeams.ATheoreticallyGroundedApproach/figures/workflow_graphs.py` when the book's canonical home
moved to agentteams (plan `book-researchteam-separation`, 2026-11-14). The graph definitions are
unchanged; only the output location (`docs_src/book/figures/`, SVG only) and the CLI changed. PDF figures
for the LaTeX edition will be produced from these SVGs by a planned `scripts/gen_book_latex.py`
(not yet in the repository).

Run: `python3 scripts/gen_book_workflow_graphs.py`  (requires graphviz `dot` on PATH and the `graphviz` Python package)

Original description:
Generate directed graphs for all 12 orchestrator workflows.

Exhaustive inventory of workflows documented in orchestrator.agent.md:

  WF 1:  Chapter Outline Dev.             — process step (update reference)
  WF 2:  Chapter Expert Instantiation     — process steps + agents
  WF 3:  Chapter Drafting                 — loop (review cycle), sub-invocation
  WF 4:  Chapter Revision                 — sequential chain
  WF 5:  Code Hygiene Audit               — conditional (corrections needed?)
  WF 6:  Review Cycle                     — four audits + consolidation
  WF 7:  Documentation Maintenance        — propagation chain
  WF 8:  Cleanup                          — conditional + security gate
  WF 9:  LaTeX Production                 — sequential chain
  WF 10: Bibliography Operations          — conditional (citations changed?)
    WF 11: Cross-Repository Coordination    — two-branch conditional (orchestrator?)
    WF 12: Adaptive Plan Execution          — iterative post-step gate loop

Color key:
  Navy (#1B2A4A)       Orchestrator (entry point)
  Steel blue (#4682B4) Functional agents (governance)
  Dark goldenrod (#B8860B) Domain agents (production)
  Slate gray (#708090) Decision diamonds (conditional branches)
  Light gray (#E8EDF2) Process steps (non-agent actions)
  Firebrick  (#B22222) Loop-back edges (iteration)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import graphviz
except ImportError:  # pragma: no cover - exercised only without the dev tool installed
    sys.exit("gen_book: the 'graphviz' Python package is required (pip install graphviz)")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs_src" / "book" / "figures"



# ── Colors (consistent with agent_team_structure.py) ─────────────────
NAVY   = '#1B2A4A'
STEEL  = '#4682B4'
AMBER  = '#B8860B'
SLATE  = '#708090'
LTGRAY = '#E8EDF2'
EDGE_C = '#333333'
LOOP_C = '#B22222'
BG     = '#FAFAFA'


_OUT: dict[str, Path] = {"dir": OUT_DIR}


def node(g: graphviz.Digraph, nid: str, label: str, kind: str = 'func') -> None:
    """Add a styled node.

    Args:
        g: Graph to add the node to.
        nid: Node id.
        label: Node label.
        kind: One of ``orch``, ``func``, ``domain``, ``decision``, ``process``, ``end``.

    Raises:
        ValueError: If ``kind`` is not one of the known node kinds.
    """
    base = dict(fontname='Helvetica', fontsize='14')
    if kind == 'orch':
        g.node(nid, label, shape='box', style='filled,rounded',
               fillcolor=NAVY, fontcolor='white', margin='0.3,0.15', **base)
    elif kind == 'func':
        g.node(nid, label, shape='box', style='filled,rounded',
               fillcolor=STEEL, fontcolor='white', margin='0.3,0.15', **base)
    elif kind == 'domain':
        g.node(nid, label, shape='box', style='filled,rounded',
               fillcolor=AMBER, fontcolor='white', margin='0.3,0.15', **base)
    elif kind == 'decision':
        g.node(nid, label, shape='diamond', style='filled',
               fillcolor=SLATE, fontcolor='white', margin='0.3,0.2', **base)
    elif kind == 'process':
        g.node(nid, label, shape='box', style='filled,rounded,dashed',
               fillcolor=LTGRAY, fontcolor='#333333', margin='0.3,0.15', **base)
    elif kind == 'end':
        g.node(nid, label, shape='oval', style='filled',
               fillcolor=LTGRAY, fontcolor='#333333', margin='0.2,0.12', **base)
    else:
        raise ValueError(f'unknown node kind: {kind!r}')


def make_graph(name: str, title: str) -> graphviz.Digraph:
    """Create a base digraph with common attributes.

    Args:
        name: Graph name.
        title: Bold title shown above the graph.

    Returns:
        A styled, empty digraph.
    """
    g = graphviz.Digraph(name=name, format='pdf')
    g.attr(rankdir='TB', splines='true', bgcolor=BG,
           fontname='Helvetica', fontsize='16',
           label=f'<<B>{title}</B>>', labelloc='t',
           nodesep='0.8', ranksep='0.9', pad='0.3')
    g.attr('node', fontname='Helvetica', fontsize='14', penwidth='1.2')
    g.attr('edge', fontname='Helvetica', fontsize='11',
           color=EDGE_C, arrowsize='0.8')
    return g


def save(g: graphviz.Digraph, filename: str) -> None:
    """Render one workflow graph to SVG in the current output directory.

    Args:
        g: Graph to render.
        filename: Output stem (no extension).
    """
    path = _OUT["dir"] / filename
    g.format = 'svg'
    g.render(str(path), cleanup=True)
    print(f'  {filename}.svg')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 1: Chapter Outline Development
#
# Trigger: "Outline Chapter N" / "Develop outline for X"
# Agents:  book-content-expert, conflict-auditor
# Feature: Process step (update reference file) between agents
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf01() -> None:
    """Render Workflow 1 (see the section comment above)."""
    g = make_graph('wf01', 'Workflow 1: Chapter Outline Development')

    node(g, 'orch', 'Orchestrator',                               'orch')
    node(g, 'bce',  '@book-content-expert\nResearch sources',      'domain')
    node(g, 'upd',  'Update\nchapter-outlines.reference.md',      'process')
    node(g, 'ca',   '@conflict-auditor\nVerify outline consistency', 'func')

    g.edge('orch', 'bce', label='  1  ')
    g.edge('bce',  'upd', label='  2  ')
    g.edge('upd',  'ca',  label='  3  ')

    save(g, 'workflow-01-chapter-outline')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 2: Chapter Expert Instantiation
#
# Trigger: "Create chapter expert for Chapter N"
# Agents:  conflict-auditor, agent-updater
# Feature: Three process steps (read template, read outlines,
#          populate + write) before agent invocations
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf02() -> None:
    """Render Workflow 2 (see the section comment above)."""
    g = make_graph('wf02', 'Workflow 2: Chapter Expert Instantiation')

    node(g, 'orch',  'Orchestrator',                                  'orch')
    node(g, 'tmpl',  'Read\nchapter-expert.template.md',              'process')
    node(g, 'spec',  'Read\nchapter-outlines.reference.md',           'process')
    node(g, 'write', 'Populate placeholders\nWrite chapter-NN-expert.agent.md', 'process')
    node(g, 'ca',    '@conflict-auditor\nVerify cross-refs',          'func')
    node(g, 'au',    '@agent-updater\nRegister new agent',            'func')

    g.edge('orch',  'tmpl',  label='  1  ')
    g.edge('tmpl',  'spec',  label='  2  ')
    g.edge('spec',  'write', label='  3-5  ')
    g.edge('write', 'ca',    label='  6  ')
    g.edge('ca',    'au',    label='  7  ')

    save(g, 'workflow-02-chapter-expert-instantiation')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 3: Chapter Drafting
#
# Trigger: "Draft Chapter N" / "Write the chapter on X"
# Agents:  navigator, chapter-NN-expert, bibliography-manager,
#          book-content-expert, expert-critic, argument-weaver,
#          voice-style, html-document-builder,
#          conflict-auditor, agent-updater
# Feature: Review loop (ACCEPT/REVISE) between chapter expert and
#          book-content-expert; critique loop from expert-critic back
#          to book-content-expert; sub-invocation of bibliography-manager
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf03() -> None:
    """Render Workflow 3 (see the section comment above)."""
    g = make_graph('wf03', 'Workflow 3: Chapter Drafting')

    node(g, 'orch',    'Orchestrator',                          'orch')
    node(g, 'nav',     '@navigator\nVerify location',            'func')
    node(g, 'chexp_b', '@chapter-NN-expert\nPrepare Chapter Brief', 'domain')
    node(g, 'bib',     '@bibliography-manager\nVerify citation keys', 'domain')
    node(g, 'adv',     '@adversarial\nReview Chapter Brief',     'func')
    node(g, 'bce',     '@book-content-expert\nDraft chapter HTML', 'domain')
    node(g, 'chexp_r', '@chapter-NN-expert\nReview vs. checklist', 'domain')
    node(g, 'dec',     'ACCEPT?',                                'decision')
    node(g, 'ec',      '@expert-critic\nAudit prose quality',    'func')
    node(g, 'aw',      '@argument-weaver\nRepair cohesion',      'func')
    node(g, 'vs',      '@voice-style\nThree-pass audit',           'domain')
    node(g, 'hdb',     '@html-document-builder\nValidate + convert', 'domain')
    node(g, 'ca',      '@conflict-auditor\nConsistency check',   'func')
    node(g, 'au',      '@agent-updater\nUpdate tracking',        'func')

    # Place chexp_b and bib on the same rank (side invocation)
    with g.subgraph() as s:
        s.attr(rank='same')
        s.node('chexp_b')
        s.node('bib')

    g.edge('orch',    'nav',     label='  1  ')
    g.edge('nav',     'chexp_b', label='  2  ')
    g.edge('chexp_b', 'bib',     label='  verify keys  ', style='dashed')
    g.edge('chexp_b', 'adv',     label='  3  ')
    g.edge('adv',     'chexp_b', label='  REVISE  ',
           color=LOOP_C, style='dashed', constraint='false')
    g.edge('adv',     'bce',     label='  4  ')
    g.edge('bce',     'chexp_r', label='  5  ')
    g.edge('chexp_r', 'dec')
    g.edge('dec',     'ec',      label='  ACCEPT  ')
    g.edge('dec',     'bce',     label='  REVISE  ',
           color=LOOP_C, style='dashed', constraint='false')
    g.edge('ec',      'bce',     label='  REVISE  ',
           color=LOOP_C, style='dashed', constraint='false')
    g.edge('ec',      'aw',      label='  7  ')
    g.edge('aw',      'vs',      label='  8  ')
    g.edge('vs',      'hdb',     label='  9  ')
    g.edge('hdb',     'ca',      label='  10 ')
    g.edge('ca',      'au',      label='  11 ')

    save(g, 'workflow-03-chapter-drafting')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 4: Chapter Revision
#
# Trigger: "Revise Chapter N" / "Incorporate review feedback"
# Agents:  book-content-expert, adversarial, expert-critic,
#          argument-weaver, voice-style, conflict-auditor,
#          bibliography-manager
# Feature: Full prose-quality chain after revision;
#          expert-critic feedback loop to book-content-expert
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf04() -> None:
    """Render Workflow 4 (see the section comment above)."""
    g = make_graph('wf04', 'Workflow 4: Chapter Revision')

    node(g, 'orch', 'Orchestrator',                             'orch')
    node(g, 'bce',  '@book-content-expert\nRevise chapter',     'domain')
    node(g, 'adv',  '@adversarial\nReview revision plan',       'func')
    node(g, 'ec',   '@expert-critic\nAudit prose quality',      'func')
    node(g, 'aw',   '@argument-weaver\nRepair cohesion',        'func')
    node(g, 'vs',   '@voice-style\nThree-pass audit',             'domain')
    node(g, 'ca',   '@conflict-auditor\nVerify no contradictions', 'func')
    node(g, 'bib',  '@bibliography-manager\nVerify citations',  'domain')

    g.edge('orch', 'bce', label='  1  ')
    g.edge('bce',  'adv', label='  2  ')
    g.edge('adv',  'bce', label='  REVISE  ',
           color=LOOP_C, style='dashed', constraint='false')
    g.edge('adv',  'ec',  label='  3  ')
    g.edge('ec',   'bce', label='  REVISE  ',
           color=LOOP_C, style='dashed', constraint='false')
    g.edge('ec',   'aw',  label='  4  ')
    g.edge('aw',   'vs',  label='  5  ')
    g.edge('vs',   'ca',  label='  6  ')
    g.edge('ca',   'bib', label='  7  ')

    save(g, 'workflow-04-chapter-revision')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 5: Code Hygiene Audit
#
# Trigger: "Verify technical accuracy" / "Audit code examples"
# Agents:  code-hygiene, book-content-expert (conditional),
#          expert-critic, argument-weaver, voice-style (all conditional
#          on text edits), agent-updater, conflict-auditor
# Feature: Conditional branch — prose-quality chain only fires when
#          book-content-expert has edited chapter text
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf05() -> None:
    """Render Workflow 5 (see the section comment above)."""
    g = make_graph('wf05', 'Workflow 5: Code Hygiene Audit')

    node(g, 'orch', 'Orchestrator',                               'orch')
    node(g, 'ch',   '@code-hygiene\nFull audit',                   'func')
    node(g, 'rev',  'Review findings',                             'process')
    node(g, 'dec',  'Corrections\nneeded?',                        'decision')
    node(g, 'bce',  '@book-content-expert\nUpdate chapter drafts', 'domain')
    node(g, 'ec',   '@expert-critic\nAudit corrected passages',    'func')
    node(g, 'aw',   '@argument-weaver\nRepair cohesion',           'func')
    node(g, 'vs',   '@voice-style\nThree-pass audit',                'domain')
    node(g, 'au',   '@agent-updater\nUpdate docs',                 'func')
    node(g, 'ca',   '@conflict-auditor\nVerify consistency',       'func')

    g.edge('orch', 'ch',  label='  1  ')
    g.edge('ch',   'rev', label='  2  ')
    g.edge('rev',  'dec')
    g.edge('dec',  'bce', label='  Yes  ')
    g.edge('dec',  'au',  label='  No  ', style='dashed')
    g.edge('bce',  'ec',  label='  4  ')
    g.edge('ec',   'bce', label='  REVISE  ',
           color=LOOP_C, style='dashed', constraint='false')
    g.edge('ec',   'aw',  label='  5  ')
    g.edge('aw',   'vs',  label='  6  ')
    g.edge('vs',   'au',  label='  7  ')
    g.edge('au',   'ca',  label='  8  ')

    save(g, 'workflow-05-code-hygiene')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 6: Review Cycle
#
# Trigger: "Review all chapters" / "Run consistency audit"
# Agents:  conflict-auditor, code-hygiene, bibliography-manager,
#          voice-style
# Feature: Four independent audits run sequentially, then consolidated
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf06() -> None:
    """Render Workflow 6 (see the section comment above)."""
    g = make_graph('wf06', 'Workflow 6: Review Cycle')

    node(g, 'orch', 'Orchestrator',                          'orch')
    node(g, 'ca',   '@conflict-auditor\nDetect contradictions', 'func')
    node(g, 'ch',   '@code-hygiene\nVerify code examples',    'func')
    node(g, 'bib',  '@bibliography-manager\nVerify citations', 'domain')
    node(g, 'vs',   '@voice-style\nThree-pass audit',           'domain')
    node(g, 'cons', 'Consolidate findings\nPresent to user',  'process')

    g.edge('orch', 'ca',   label='  1  ')
    g.edge('ca',   'ch',   label='  2  ')
    g.edge('ch',   'bib',  label='  3  ')
    g.edge('bib',  'vs',   label='  4  ')
    g.edge('vs',   'cons', label='  5  ')

    save(g, 'workflow-06-review-cycle')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 7: Documentation Maintenance (Propagation Chain)
#
# Trigger: "Update agent docs" / "Project structure changed"
# Agents:  agent-updater, agent-refactor, conflict-auditor
# Feature: The propagation chain — each agent adds a distinct
#          verification (content, structure, consistency)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf07() -> None:
    """Render Workflow 7 (see the section comment above)."""
    g = make_graph('wf07', 'Workflow 7: Documentation Maintenance')

    node(g, 'orch', 'Orchestrator',                             'orch')
    node(g, 'au',   '@agent-updater\nSync docs with changes',   'func')
    node(g, 'ar',   '@agent-refactor\nExtraction + compliance', 'func')
    node(g, 'ca',   '@conflict-auditor\nVerify consistency',    'func')

    g.edge('orch', 'au', label='  1  ')
    g.edge('au',   'ar', label='  2  ')
    g.edge('ar',   'ca', label='  3  ')

    save(g, 'workflow-07-doc-maintenance')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 8: Cleanup
#
# Trigger: "Clean up project" / "Remove stale files"
# Agents:  code-hygiene, adversarial (conditional), security
#          (conditional), cleanup, agent-updater
# Feature: Security gate — @security clearance required before any
#          file deletion proceeds
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf08() -> None:
    """Render Workflow 8 (see the section comment above)."""
    g = make_graph('wf08', 'Workflow 8: Cleanup')

    node(g, 'orch', 'Orchestrator',                          'orch')
    node(g, 'ch',   '@code-hygiene\nIdentify candidates',     'func')
    node(g, 'dec',  'Deletions\nneeded?',                     'decision')
    node(g, 'adv',  '@adversarial\nReview deletion plan',     'func')
    node(g, 'sec',  '@security\nClearance review',            'func')
    node(g, 'cln',  '@cleanup\nRemove approved files',        'func')
    node(g, 'au',   '@agent-updater\nUpdate docs',            'func')
    node(g, 'done', 'Done',                                   'end')

    g.edge('orch', 'ch',   label='  1  ')
    g.edge('ch',   'dec')
    g.edge('dec',  'adv',  label='  Yes  ')
    g.edge('dec',  'done', label='  No  ', style='dashed')
    g.edge('adv',  'sec',  label='  3  ')
    g.edge('sec',  'cln',  label='  4  ')
    g.edge('cln',  'au',   label='  5  ')

    save(g, 'workflow-08-cleanup')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 9: LaTeX Production
#
# Trigger: "Generate manuscript" / "Compile LaTeX" / "Build PDF"
# Agents:  html-document-builder, bibliography-manager,
#          latex-specialist, cleanup
# Feature: Sequential production pipeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf09() -> None:
    """Render Workflow 9 (see the section comment above)."""
    g = make_graph('wf09', 'Workflow 9: LaTeX Production')

    node(g, 'orch', 'Orchestrator',                              'orch')
    node(g, 'hdb',  '@html-document-builder\nConvert HTML to LaTeX', 'domain')
    node(g, 'bib',  '@bibliography-manager\nVerify BibTeX entries', 'domain')
    node(g, 'ltx',  '@latex-specialist\nCompile manuscript',      'domain')
    node(g, 'cln',  '@cleanup\nRemove build artifacts',          'func')

    g.edge('orch', 'hdb', label='  1  ')
    g.edge('hdb',  'bib', label='  2  ')
    g.edge('bib',  'ltx', label='  3  ')
    g.edge('ltx',  'cln', label='  4  ')

    save(g, 'workflow-09-latex-production')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 10: Bibliography Operations
#
# Trigger: "Add citation" / "Update bibliography" / "Verify citations"
# Agents:  bibliography-manager, conflict-auditor (conditional)
# Feature: Conditional branch — conflict-auditor only if citations changed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf10() -> None:
    """Render Workflow 10 (see the section comment above)."""
    g = make_graph('wf10', 'Workflow 10: Bibliography Operations')

    node(g, 'orch', 'Orchestrator',                            'orch')
    node(g, 'bib',  '@bibliography-manager\nPerform operation', 'domain')
    node(g, 'dec',  'Citations\nchanged?',                      'decision')
    node(g, 'ca',   '@conflict-auditor\nVerify cross-refs',     'func')
    node(g, 'done', 'Done',                                     'end')

    g.edge('orch', 'bib', label='  1  ')
    g.edge('bib',  'dec')
    g.edge('dec',  'ca',   label='  Yes  ')
    g.edge('dec',  'done', label='  No  ', style='dashed')

    save(g, 'workflow-10-bibliography-operations')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 11: Cross-Repository Coordination
#
# Trigger: Schema/template/protocol change in adjacent repos,
#          or explicit "Assess cross-repo impact"
# Agents:  repo-liaison, security (conditional), conflict-auditor
# Feature: Two-branch conditional after Impact Report review:
#          (A) no-orchestrator repos → security gate → apply change
#          (B) with-orchestrator repos → compose Coordination Request
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf11() -> None:
    """Render Workflow 11 (see the section comment above)."""
    g = make_graph('wf11', 'Workflow 11: Cross-Repository Coordination')

    node(g, 'orch',   'Orchestrator',                                       'orch')
    node(g, 'rl_id',  '@repo-liaison\nRead adjacent-repos.md\nIdentify affected repos', 'func')
    node(g, 'rl_rpt', '@repo-liaison\nInspect affected repos\nProduce Impact Report',   'func')
    node(g, 'dec',    'Review Impact Report\nAPPROVE / REJECT / DEFER?',    'decision')
    node(g, 'dec2',   'Has own\norchestrator?',                              'decision')
    node(g, 'sec',    '@security\nClearance for write',                      'func')
    node(g, 'rl_app', '@repo-liaison\nApply minimal change\nRecord in registry', 'func')
    node(g, 'rl_cr',  '@repo-liaison\nCompose Coordination Request\nSave to references/', 'func')
    node(g, 'ca',     '@conflict-auditor\nVerify no internal contradictions', 'func')

    # Place the two APPROVED sub-paths on the same rank
    with g.subgraph() as s:
        s.attr(rank='same')
        s.node('sec')
        s.node('rl_cr')

    g.edge('orch',   'rl_id',  label='  1  ')
    g.edge('rl_id',  'rl_rpt', label='  2  ')
    g.edge('rl_rpt', 'dec',    label='  3  ')
    g.edge('dec',    'dec2',   label='  APPROVE  ')
    g.edge('dec',    'ca',     label='  REJECT/DEFER  ', style='dashed')
    g.edge('dec2',   'sec',    label='  No  ')
    g.edge('dec2',   'rl_cr',  label='  Yes  ')
    g.edge('sec',    'rl_app', label='  4a  ')
    g.edge('rl_app', 'ca',     label='  6  ')
    g.edge('rl_cr',  'ca',     label='  5b  ')

    save(g, 'workflow-11-cross-repository-coordination')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Workflow 12: Adaptive Plan Execution
#
# Trigger: Execution of a multi-step adaptive plan where completed-step
#          learning can revise remaining pending steps.
# Agents:  adversarial, conflict-auditor
# Feature: Iterative post-step gate loop after each completed step
#          before advancing to the next step.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def wf12() -> None:
    """Render Workflow 12 (see the section comment above)."""
    g = make_graph('wf12', 'Workflow 12: Adaptive Plan Execution')

    node(g, 'orch',  'Orchestrator',                                  'orch')
    node(g, 'exec',  'Execute next approved step',                    'process')
    node(g, 'mark',  'Mark step done\nUpdate step tracker',          'process')
    node(g, 'adv',   '@adversarial\nReview remaining pending steps', 'func')
    node(g, 'ca',    '@conflict-auditor\nCheck revised sequence',    'func')
    node(g, 'rev',   'Revise pending rows\n(scope, order, notes)',   'process')
    node(g, 'dec',   'Pending steps\nremaining?',                    'decision')
    node(g, 'done',  'Plan complete',                                 'end')

    g.edge('orch', 'exec', label='  start  ')
    g.edge('exec', 'mark', label='  1  ')
    g.edge('mark', 'adv',  label='  2  ')
    g.edge('adv',  'ca',   label='  3  ')
    g.edge('ca',   'rev',  label='  4  ')
    g.edge('rev',  'dec')
    g.edge('dec',  'exec', label='  Yes  ', color=LOOP_C, style='dashed', constraint='false')
    g.edge('dec',  'done', label='  No  ')

    save(g, 'workflow-12-adaptive-plan-execution')


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed namespace with ``out_dir``.
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out-dir", type=Path, default=OUT_DIR,
                   help="directory to write SVGs into (default: docs_src/book/figures)")
    return p.parse_args(argv)


WORKFLOWS = (wf01, wf02, wf03, wf04, wf05, wf06, wf07, wf08, wf09, wf10, wf11, wf12)


def main(argv: list[str] | None = None) -> int:
    """Render every ``workflow-NN-*.svg``.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 on success).
    """
    args = _parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _OUT["dir"] = args.out_dir
    print('Generating workflow directed graphs...\n')
    for wf in WORKFLOWS:
        wf()
    print(f'\nAll {len(WORKFLOWS)} workflow graphs generated.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
