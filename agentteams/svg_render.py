"""Deterministic, dependency-free SVG renderer for small directed graphs.

Produces a standalone ``.svg`` document (byte-identical for identical input) from a
neutral node/edge spec, using a layered left-to-right layout with barycenter crossing
reduction. Used by :mod:`agentteams.graph` (agent topology) and
:mod:`agentteams.architecture` (module dependencies) so the rendered diagram can live in
its own ``.svg`` file that the companion ``.md`` references.

Design constraints (see ``tmp/by-week/2026-W27/svg-graph-protocol-2026-07-03.plan.md``):

* **Stdlib only.** No Graphviz / Mermaid CLI — those add a system dependency and vary output
  by tool version, which would break the pre-commit hook's byte-identical no-churn contract.
* **Deterministic.** Integer coordinates only, monospace font (so text can never overflow the
  box it was measured for), sorted iteration everywhere, no timestamps, no ``float`` in output.
* **Layered layout with crossing reduction.** A naive within-rank ordering renders the dense
  agent-topology hub as a hairball; barycenter sweeps materially reduce crossings.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

# ---------------------------------------------------------------------------
# Public spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SvgNode:
    """A graph node. ``kind`` selects a (fill, stroke) pair from the palette."""

    id: str
    label: str
    kind: str = "default"


@dataclass(frozen=True)
class SvgEdge:
    """A directed edge ``src`` → ``dst`` (node ids)."""

    src: str
    dst: str


#: Fallback (fill, stroke) when a node ``kind`` is absent from the caller's palette.
_DEFAULT_STYLE = ("#f5f5f5", "#999999")

#: Ordered (fill, stroke) pool for auto-assigning colours to node groups.
_PALETTE_POOL: list[tuple[str, str]] = [
    ("#e8eefb", "#1b3fa0"),
    ("#eef6ee", "#3f8f4f"),
    ("#fff8e8", "#ccaa44"),
    ("#ffe8e8", "#cc6666"),
    ("#f0e8ff", "#8a5cc6"),
    ("#e8fbff", "#3f8f9f"),
    ("#fbe8f4", "#b0559a"),
    ("#f4fbe8", "#7a9f3f"),
]


def auto_palette(keys: list[str]) -> dict[str, tuple[str, str]]:
    """Deterministically map group keys → (fill, stroke), cycling a fixed pool.

    Keys are sorted first so the colour assignment is stable across runs.
    """
    return {
        key: _PALETTE_POOL[i % len(_PALETTE_POOL)]
        for i, key in enumerate(sorted(set(keys)))
    }

# Layout geometry — all integers so coordinates stay integral and byte-stable.
_CHAR_ADV = 8      # px advance per character at the monospace font size below
_FONT_SIZE = 13
_PAD_X = 12        # horizontal padding inside a node box
_NODE_H = 30       # node box height
_H_GAP = 72        # horizontal gap between rank columns
_V_GAP = 18        # vertical gap between nodes in a column
_MARGIN = 16       # outer margin
_MIN_W = 48        # minimum node width


def _node_width(label: str) -> int:
    """Monospace box width wide enough to hold ``label`` (integer px)."""
    return max(_MIN_W, _CHAR_ADV * len(label) + 2 * _PAD_X)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _clean_edges(ids: set[str], edges: list[SvgEdge]) -> list[tuple[str, str]]:
    """Sorted, de-duplicated, self-loop-free directed edges between known nodes."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for edge in sorted(edges, key=lambda e: (e.src, e.dst)):
        pair = (edge.src, edge.dst)
        if edge.src == edge.dst or pair in seen:
            continue
        if edge.src not in ids or edge.dst not in ids:
            continue
        seen.add(pair)
        out.append(pair)
    return out


def _back_edges(ids: list[str], directed: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Back-edges of a DFS in sorted order (iterative; deterministic)."""
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for src, dst in directed:
        adj[src].append(dst)
    for key in adj:
        adj[key].sort()

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}
    back: set[tuple[str, str]] = set()

    for root in ids:
        if color[root] != WHITE:
            continue
        # Stack frames carry an index into the node's (sorted) adjacency list.
        stack: list[tuple[str, int]] = [(root, 0)]
        color[root] = GRAY
        while stack:
            node, idx = stack[-1]
            if idx < len(adj[node]):
                stack[-1] = (node, idx + 1)
                nxt = adj[node][idx]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, 0))
                elif color[nxt] == GRAY:
                    back.add((node, nxt))
            else:
                color[node] = BLACK
                stack.pop()
    return back


def _rank_nodes(ids: list[str], dag: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path rank on the acyclic edge set (source nodes at rank 0)."""
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for src, dst in dag:
        adj[src].append(dst)
        indeg[dst] += 1

    # Kahn topological order; smallest id first keeps it deterministic.
    ready = sorted(i for i in ids if indeg[i] == 0)
    rank = {i: 0 for i in ids}
    topo: list[str] = []
    while ready:
        node = ready.pop(0)
        topo.append(node)
        newly: list[str] = []
        for nxt in adj[node]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                newly.append(nxt)
        for nxt in sorted(newly):
            # insert keeping ``ready`` sorted
            lo, hi = 0, len(ready)
            while lo < hi:
                mid = (lo + hi) // 2
                if ready[mid] < nxt:
                    lo = mid + 1
                else:
                    hi = mid
            ready.insert(lo, nxt)

    for node in topo:
        for nxt in adj[node]:
            if rank[node] + 1 > rank[nxt]:
                rank[nxt] = rank[node] + 1
    return rank


def _order_within_ranks(
    columns: dict[int, list[str]],
    directed: list[tuple[str, str]],
) -> None:
    """Barycenter sweeps to reduce edge crossings (mutates ``columns`` in place)."""
    neighbours: dict[str, list[str]] = {}
    for src, dst in directed:
        neighbours.setdefault(src, []).append(dst)
        neighbours.setdefault(dst, []).append(src)

    for _sweep in range(4):
        for rank in sorted(columns):
            order = columns[rank]
            position = {node: idx for idx, node in enumerate(order)}
            # Position of each node in whichever ranks its neighbours occupy.
            other_pos: dict[str, list[int]] = {}
            for other_rank, members in columns.items():
                if other_rank == rank:
                    continue
                for idx, node in enumerate(members):
                    other_pos[node] = other_pos.get(node, [])
                    other_pos[node].append(idx)

            def barycenter(node: str) -> tuple[int, int, str]:
                bucket: list[int] = []
                for nbr in neighbours.get(node, []):
                    bucket.extend(other_pos.get(nbr, []))
                if not bucket:
                    # No cross-rank neighbours: keep current relative position.
                    return (1, position[node], node)
                # Scaled-integer average → no float in the sort key.
                return (0, (sum(bucket) * 1000) // len(bucket), node)

            columns[rank] = sorted(order, key=barycenter)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _arrow_marker() -> str:
    return (
        '  <defs>\n'
        '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
        '      <polygon points="0,0 10,5 0,10" fill="#8a8a8a"/>\n'
        '    </marker>\n'
        '  </defs>'
    )


def render_svg(
    nodes: list[SvgNode],
    edges: list[SvgEdge],
    *,
    palette: dict[str, tuple[str, str]] | None = None,
    title: str = "",
) -> str:
    """Render ``nodes``/``edges`` to a deterministic standalone SVG document.

    Args:
        nodes: graph nodes (order irrelevant — sorted internally).
        edges: directed edges; a mutual pair (a→b and b→a) is drawn once with an
            arrowhead at each end.
        palette: ``kind`` → ``(fill, stroke)``; missing kinds fall back to grey.
        title: optional ``<title>`` for accessibility.

    Returns:
        SVG XML as a string, byte-identical for identical input.
    """
    palette = palette or {}
    nodes = sorted(nodes, key=lambda n: n.id)
    ids = [n.id for n in nodes]
    id_set = set(ids)
    by_id = {n.id: n for n in nodes}

    directed = _clean_edges(id_set, edges)
    directed_set = set(directed)
    mutual = {
        (min(a, b), max(a, b))
        for a, b in directed
        if (b, a) in directed_set
    }

    back = _back_edges(ids, directed)
    dag = [pair for pair in directed if pair not in back]
    rank = _rank_nodes(ids, dag)

    columns: dict[int, list[str]] = {}
    for node_id in ids:
        columns.setdefault(rank[node_id], []).append(node_id)
    for members in columns.values():
        members.sort()
    _order_within_ranks(columns, directed)

    max_rank = max(columns) if columns else 0

    # Column widths and x offsets (left-to-right).
    col_w = {
        r: max((_node_width(by_id[i].label) for i in members), default=_MIN_W)
        for r, members in columns.items()
    }
    x_left: dict[int, int] = {}
    cursor = _MARGIN
    for r in range(max_rank + 1):
        x_left[r] = cursor
        cursor += col_w.get(r, 0) + _H_GAP
    content_w = cursor - _H_GAP if columns else _MARGIN

    # Column heights and vertical centering.
    col_h = {
        r: len(members) * (_NODE_H + _V_GAP) - _V_GAP
        for r, members in columns.items()
    }
    content_h = max(col_h.values(), default=0)

    # Final integer box coordinates: id -> (x, y, w, h).
    box: dict[str, tuple[int, int, int, int]] = {}
    for r, members in columns.items():
        start_y = _MARGIN + (content_h - col_h[r]) // 2
        for idx, node_id in enumerate(members):
            width = _node_width(by_id[node_id].label)
            x = x_left[r] + (col_w[r] - width) // 2
            y = start_y + idx * (_NODE_H + _V_GAP)
            box[node_id] = (x, y, width, _NODE_H)

    total_w = content_w + _MARGIN
    total_h = content_h + 2 * _MARGIN

    out: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" width="{total_w}" height="{total_h}" '
        'font-family="monospace">',
        f"  <title>{escape(title)}</title>" if title else "  <title>graph</title>",
        _arrow_marker(),
    ]

    # Edges (drawn first so nodes sit on top).
    drawn_pairs: set[tuple[str, str]] = set()
    for src, dst in directed:
        key = (min(src, dst), max(src, dst))
        both_ends = key in mutual
        if both_ends:
            if key in drawn_pairs:
                continue
            drawn_pairs.add(key)
        sx, sy, sw, sh = box[src]
        dx, dy, _dw, dh = box[dst]
        x1, y1 = sx + sw, sy + sh // 2
        x2, y2 = dx, dy + dh // 2
        mx = (x1 + x2) // 2
        dashed = "" if both_ends or rank[dst] > rank[src] else ' stroke-dasharray="4,3"'
        marker_start = ' marker-start="url(#arrow)"' if both_ends else ""
        out.append(
            f'  <path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" '
            f'fill="none" stroke="#8a8a8a" stroke-width="1.5"{dashed}'
            f'{marker_start} marker-end="url(#arrow)"/>'
        )

    # Nodes.
    baseline = _FONT_SIZE // 2
    for node_id in ids:
        x, y, w, h = box[node_id]
        node = by_id[node_id]
        fill, stroke = palette.get(node.kind, _DEFAULT_STYLE)
        cx = x + w // 2
        cy = y + h // 2 + baseline - 1
        out.append(
            f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
        )
        out.append(
            f'  <text x="{cx}" y="{cy}" font-size="{_FONT_SIZE}" '
            f'text-anchor="middle" fill="#000000">{escape(node.label)}</text>'
        )

    out.append("</svg>")
    return "\n".join(out) + "\n"
