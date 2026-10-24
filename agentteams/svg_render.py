"""Deterministic, dependency-free SVG renderer for small directed graphs.

Produces a standalone ``.svg`` document (byte-identical for identical input) from a
neutral node/edge spec, using a layered left-to-right layout with barycenter crossing
reduction (or, opt-in via ``render_svg(direction="TB")``, the same layout transposed
top-to-bottom: ranks as rows, edges bottom-of-source to top-of-target). Used by :mod:`agentteams.graph` (agent topology) and
:mod:`agentteams.architecture` (module dependencies) so the rendered diagram can live in
its own ``.svg`` file that the companion ``.md`` references.

Design constraints (see ``tmp/by-week/2026-W27/svg-graph-protocol-2026-07-03.plan.md``):

* **Stdlib only.** No Graphviz / Mermaid CLI — those add a system dependency and vary output
  by tool version, which would break the pre-commit hook's byte-identical no-churn contract.
* **Deterministic.** Integer coordinates only, monospace font (so text can never overflow the
  box it was measured for), sorted iteration everywhere, no timestamps, no ``float`` in output.
* **Layered layout with crossing reduction.** A naive within-rank ordering renders the dense
  agent-topology hub as a hairball; barycenter sweeps materially reduce crossings.
* **Waypoint (dummy-node) edge routing.** An edge spanning more than one rank is broken at
  every intermediate rank by a zero-width *dummy node* that joins the columns and the
  barycenter sweeps like a real node. The edge is then drawn as a single multi-segment
  spline through its dummy chain, with all vertical movement confined to the inter-column
  gaps — so long, back, and cross-hub edges route *between* boxes instead of straight
  through them. (Without this a majority of edges in a dense graph pierce unrelated boxes.)
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
_DUMMY_H = 10      # vertical lane reserved for an edge routing waypoint (dummy node)
# Top-to-bottom (``direction="TB"``) geometry: ranks are rows, members side by side.
_TB_RANK_GAP = 48  # vertical gap between rank rows
_TB_NODE_GAP = 24  # horizontal gap between items in a row
_DUMMY_W = 10      # horizontal lane reserved for a routing waypoint in a row

#: Accepted ``render_svg(direction=...)`` values.
_DIRECTIONS = frozenset({"LR", "TB"})

#: ``(box, edge_lines, total_w, total_h)`` returned by the per-direction layouts.
_Layout = tuple[dict[str, tuple[int, int, int, int]], list[str], int, int]


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


def _order_key(node: object) -> tuple[int, object]:
    """Type-homogeneous, deterministic sort key for a real (str) or dummy (tuple) node.

    Real node ids are plain strings; routing waypoints (dummy nodes) are
    ``("d", edge_index, rank)`` tuples. A raw ``sorted`` over the mixed set would
    compare ``str`` against ``tuple`` and raise ``TypeError`` under Python 3, so
    every comparison is funnelled through this key: reals sort before dummies, reals
    by id, dummies by their (integer) edge index.
    """
    if isinstance(node, tuple):
        return (1, node[1])
    return (0, node)


def _order_within_ranks(
    columns: dict[int, list[object]],
    neighbours: dict[object, list[object]],
) -> None:
    """Barycenter sweeps to reduce edge crossings (mutates ``columns`` in place).

    ``neighbours`` is the adjacency of the *routing* graph — real nodes plus the
    per-edge dummy waypoint chains — so long edges pull their waypoints into line
    and the crossing count drops for the whole spline, not just its endpoints.
    """
    for _sweep in range(4):
        for rank in sorted(columns):
            order = columns[rank]
            position = {node: idx for idx, node in enumerate(order)}
            # Position of each node in whichever ranks its neighbours occupy.
            other_pos: dict[object, list[int]] = {}
            for other_rank, members in columns.items():
                if other_rank == rank:
                    continue
                for idx, node in enumerate(members):
                    other_pos[node] = other_pos.get(node, [])
                    other_pos[node].append(idx)

            def barycenter(node: object) -> tuple[int, int, tuple[int, object]]:
                bucket: list[int] = []
                for nbr in neighbours.get(node, []):
                    bucket.extend(other_pos.get(nbr, []))
                if not bucket:
                    # No cross-rank neighbours: keep current relative position.
                    return (1, position[node], _order_key(node))
                # Scaled-integer average → no float in the sort key.
                return (0, (sum(bucket) * 1000) // len(bucket), _order_key(node))

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


def _layout_lr(
    columns: dict[int, list[object]],
    by_id: dict[str, SvgNode],
    rank: dict[str, int],
    drawn: list[tuple[str, str, bool]],
    edge_chain: list[list[tuple[str, int, int]]],
    dummy_rank: dict[tuple[str, int, int], int],
) -> _Layout:
    """Left-to-right placement: ranks are columns, rank members stacked vertically.

    Args:
        columns: rank → ordered members (real node ids and dummy waypoint keys).
        by_id: real node id → node.
        rank: real node id → rank.
        drawn: drawn edges as ``(src, dst, both_directions)``.
        edge_chain: per drawn edge, its dummy waypoints ordered source → target.
        dummy_rank: dummy waypoint key → rank.

    Returns:
        ``(box, edge_lines, total_w, total_h)`` — integer node boxes, ``<path>``
        lines, and the document width/height.
    """
    max_rank = max(columns) if columns else 0

    def _is_dummy(node: object) -> bool:
        return isinstance(node, tuple)

    def _item_h(node: object) -> int:
        return _DUMMY_H if _is_dummy(node) else _NODE_H

    # Column widths (real nodes only; dummies are zero-width lanes) and x offsets.
    col_w = {
        r: max((_node_width(by_id[n].label) for n in members if not _is_dummy(n)),
               default=_MIN_W)
        for r, members in columns.items()
    }
    x_left: dict[int, int] = {}
    cursor = _MARGIN
    for r in range(max_rank + 1):
        x_left[r] = cursor
        cursor += col_w.get(r, 0) + _H_GAP
    content_w = cursor - _H_GAP if columns else _MARGIN

    # Column heights (mixed real/dummy item heights) and vertical centering.
    col_h = {
        r: sum(_item_h(n) for n in members) + _V_GAP * (len(members) - 1) if members else 0
        for r, members in columns.items()
    }
    content_h = max(col_h.values(), default=0)

    # Final integer coordinates: real nodes → box (x, y, w, h); dummies → lane centre.
    box: dict[str, tuple[int, int, int, int]] = {}
    dummy_c: dict[tuple[str, int, int], tuple[int, int]] = {}
    for r, members in columns.items():
        y = _MARGIN + (content_h - col_h[r]) // 2
        for node in members:
            h = _item_h(node)
            if _is_dummy(node):
                dummy_c[node] = (x_left[r] + col_w[r] // 2, y + h // 2)
            else:
                width = _node_width(by_id[node].label)
                x = x_left[r] + (col_w[r] - width) // 2
                box[node] = (x, y, width, _NODE_H)
            y += h + _V_GAP


    # Edges. Each drawn edge is ONE <path> whose vertical movement is confined to the
    # inter-column gaps: in-column segments are horizontal lanes (a node's own y-row
    # or a dummy's reserved slot), which never overlap another box.
    def _colL(r: int) -> int:
        return x_left[r]

    def _colR(r: int) -> int:
        return x_left[r] + col_w[r]

    max_x = content_w
    edge_lines: list[str] = []
    for ei, (src, dst, both) in enumerate(drawn):
        rs, rd = rank[src], rank[dst]
        sx, sy, sw, sh = box[src]
        syc = sy + sh // 2
        dx, dy, dw, dh = box[dst]
        dyc = dy + dh // 2
        stops: list[tuple[int, int]] = []
        if rd > rs:                                    # forward (rightward)
            stops += [(sx + sw, syc), (_colR(rs), syc)]
            for key in edge_chain[ei]:
                r, (cxp, cyp) = dummy_rank[key], dummy_c[key]
                stops += [(_colL(r), cyp), (_colR(r), cyp)]
            stops += [(_colL(rd), dyc), (dx, dyc)]
        elif rd < rs:                                  # back (leftward), rendered dashed
            stops += [(sx, syc), (_colL(rs), syc)]
            for key in edge_chain[ei]:
                r, (cxp, cyp) = dummy_rank[key], dummy_c[key]
                stops += [(_colR(r), cyp), (_colL(r), cyp)]
            stops += [(_colR(rd), dyc), (dx + dw, dyc)]
        else:                                          # same rank: bulge into the gap
            bulge = _colR(rs) + _H_GAP // 2
            stops += [(sx + sw, syc), (bulge, syc), (bulge, dyc), (dx + dw, dyc)]
        # Collapse consecutive duplicate points (e.g. a full-column-width box).
        pts = [stops[0]]
        for p in stops[1:]:
            if p != pts[-1]:
                pts.append(p)
        segs = [f"M{pts[0][0]},{pts[0][1]}"]
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            max_x = max(max_x, x1, x2)
            if y1 == y2:                               # horizontal lane → straight line
                segs.append(f"L{x2},{y2}")
            else:                                      # gap transition → S-curve
                mx = (x1 + x2) // 2
                segs.append(f"C{mx},{y1} {mx},{y2} {x2},{y2}")
        dashed = "" if both or rd > rs else ' stroke-dasharray="4,3"'
        marker_start = ' marker-start="url(#arrow)"' if both else ""
        edge_lines.append(
            f'  <path d="{" ".join(segs)}" '
            f'fill="none" stroke="#8a8a8a" stroke-width="1.5"{dashed}'
            f'{marker_start} marker-end="url(#arrow)"/>'
        )

    total_w = max_x + _MARGIN
    total_h = content_h + 2 * _MARGIN
    return box, edge_lines, total_w, total_h


def _layout_tb(
    columns: dict[int, list[object]],
    by_id: dict[str, SvgNode],
    rank: dict[str, int],
    drawn: list[tuple[str, str, bool]],
    edge_chain: list[list[tuple[str, int, int]]],
    dummy_rank: dict[tuple[str, int, int], int],
) -> _Layout:
    """Top-to-bottom placement: ranks are rows, rank members placed side by side.

    The transpose of :func:`_layout_lr`. Every edge leaves the bottom edge of its
    source box (top edge for a back-edge) and enters the top (bottom) edge of its
    target; within a row it runs as a vertical lane (the box's own centre-line or a
    dummy waypoint's reserved slot), so all horizontal movement is confined to the
    inter-row gaps and never crosses an unrelated box.

    Args:
        columns: rank → ordered members (real node ids and dummy waypoint keys).
        by_id: real node id → node.
        rank: real node id → rank.
        drawn: drawn edges as ``(src, dst, both_directions)``.
        edge_chain: per drawn edge, its dummy waypoints ordered source → target.
        dummy_rank: dummy waypoint key → rank.

    Returns:
        ``(box, edge_lines, total_w, total_h)`` — integer node boxes, ``<path>``
        lines, and the document width/height.
    """
    max_rank = max(columns) if columns else 0

    def _item_w(node: object) -> int:
        if isinstance(node, tuple):
            return _DUMMY_W
        return _node_width(by_id[node].label)

    row_w = {
        r: sum(_item_w(n) for n in members) + _TB_NODE_GAP * (len(members) - 1)
        for r, members in columns.items()
    }
    content_w = max(row_w.values(), default=0)
    y_top = {r: _MARGIN + r * (_NODE_H + _TB_RANK_GAP) for r in range(max_rank + 1)}
    content_h = (
        (max_rank + 1) * _NODE_H + max_rank * _TB_RANK_GAP if columns else 0
    )

    box: dict[str, tuple[int, int, int, int]] = {}
    dummy_x: dict[tuple[str, int, int], int] = {}
    for r, members in columns.items():
        x = _MARGIN + (content_w - row_w[r]) // 2
        for node in members:
            w = _item_w(node)
            if isinstance(node, tuple):
                dummy_x[node] = x + w // 2
            else:
                box[node] = (x, y_top[r], w, _NODE_H)
            x += w + _TB_NODE_GAP

    def _rowT(r: int) -> int:
        return y_top[r]

    def _rowB(r: int) -> int:
        return y_top[r] + _NODE_H

    max_y = content_h + _MARGIN
    edge_lines: list[str] = []
    for ei, (src, dst, both) in enumerate(drawn):
        rs, rd = rank[src], rank[dst]
        sx, sy, sw, sh = box[src]
        sxc = sx + sw // 2
        dx, dy, dw, dh = box[dst]
        dxc = dx + dw // 2
        stops: list[tuple[int, int]] = []
        if rd > rs:                                    # forward (downward)
            stops += [(sxc, sy + sh), (sxc, _rowB(rs))]
            for key in edge_chain[ei]:
                r, cx = dummy_rank[key], dummy_x[key]
                stops += [(cx, _rowT(r)), (cx, _rowB(r))]
            stops += [(dxc, _rowT(rd)), (dxc, dy)]
        elif rd < rs:                                  # back (upward), rendered dashed
            stops += [(sxc, sy), (sxc, _rowT(rs))]
            for key in edge_chain[ei]:
                r, cx = dummy_rank[key], dummy_x[key]
                stops += [(cx, _rowB(r)), (cx, _rowT(r))]
            stops += [(dxc, _rowB(rd)), (dxc, dy + dh)]
        else:                                          # same rank: bulge into the gap
            bulge = _rowB(rs) + _TB_RANK_GAP // 2
            stops += [(sxc, sy + sh), (sxc, bulge), (dxc, bulge), (dxc, dy + dh)]
        pts = [stops[0]]
        for p in stops[1:]:
            if p != pts[-1]:
                pts.append(p)
        segs = [f"M{pts[0][0]},{pts[0][1]}"]
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            max_y = max(max_y, y1, y2)
            if x1 == x2 or y1 == y2:                   # straight lane → line
                segs.append(f"L{x2},{y2}")
            else:                                      # gap transition → S-curve
                my = (y1 + y2) // 2
                segs.append(f"C{x1},{my} {x2},{my} {x2},{y2}")
        dashed = "" if both or rd > rs else ' stroke-dasharray="4,3"'
        marker_start = ' marker-start="url(#arrow)"' if both else ""
        edge_lines.append(
            f'  <path d="{" ".join(segs)}" '
            f'fill="none" stroke="#8a8a8a" stroke-width="1.5"{dashed}'
            f'{marker_start} marker-end="url(#arrow)"/>'
        )

    total_w = content_w + 2 * _MARGIN
    total_h = max_y + _MARGIN
    return box, edge_lines, total_w, total_h


def render_svg(
    nodes: list[SvgNode],
    edges: list[SvgEdge],
    *,
    palette: dict[str, tuple[str, str]] | None = None,
    title: str = "",
    direction: str = "LR",
) -> str:
    """Render ``nodes``/``edges`` to a deterministic standalone SVG document.

    Args:
        nodes: graph nodes (order irrelevant — sorted internally).
        edges: directed edges; a mutual pair (a→b and b→a) is drawn once with an
            arrowhead at each end.
        palette: ``kind`` → ``(fill, stroke)``; missing kinds fall back to grey.
        title: optional ``<title>`` for accessibility.
        direction: ``"LR"`` (default) lays ranks out as columns, left to right;
            ``"TB"`` lays ranks out as rows, top to bottom, with the nodes of a rank
            side by side and edges running from the bottom of the source box to the
            top of the target. The ``"LR"`` output is unchanged by this option.

    Returns:
        SVG XML as a string, byte-identical for identical input.

    Raises:
        ValueError: if ``direction`` is neither ``"LR"`` nor ``"TB"``.
    """
    if direction not in _DIRECTIONS:
        raise ValueError(
            f"direction must be one of {sorted(_DIRECTIONS)}, got {direction!r}"
        )
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

    # Deduplicate to the edges actually drawn: a mutual pair (a→b and b→a) becomes a
    # single ``(a, b, both=True)`` entry. ``directed`` is sorted, so ``drawn`` — and
    # therefore every dummy-node id derived from a drawn edge's index — is
    # order-independent.
    drawn: list[tuple[str, str, bool]] = []
    seen_pair: set[tuple[str, str]] = set()
    for src, dst in directed:
        key = (min(src, dst), max(src, dst))
        both = key in mutual
        if both:
            if key in seen_pair:
                continue
            seen_pair.add(key)
        drawn.append((src, dst, both))

    # Routing waypoints (dummy nodes). An edge spanning more than one rank gets one
    # dummy per intermediate rank so it can be threaded *between* boxes rather than
    # drawn straight through them. Dummies join the layered columns and the
    # crossing-reduction sweeps like ordinary nodes.
    columns: dict[int, list[object]] = {}
    for node_id in ids:
        columns.setdefault(rank[node_id], []).append(node_id)
    dummy_rank: dict[tuple[str, int, int], int] = {}
    edge_chain: list[list[tuple[str, int, int]]] = []   # per drawn edge, src-side→dst-side
    for ei, (src, dst, _both) in enumerate(drawn):
        lo, hi = sorted((rank[src], rank[dst]))
        chain = [("d", ei, r) for r in range(lo + 1, hi)]
        for key in chain:
            dummy_rank[key] = key[2]
            columns.setdefault(key[2], []).append(key)
        if rank[src] > rank[dst]:
            chain.reverse()
        edge_chain.append(chain)

    # Adjacency of the routing graph (reals + dummy chains) for the barycenter sweeps.
    neighbours: dict[object, list[object]] = {}
    for ei, (src, dst, _both) in enumerate(drawn):
        prev: object = src
        for key in edge_chain[ei]:
            neighbours.setdefault(prev, []).append(key)
            neighbours.setdefault(key, []).append(prev)
            prev = key
        neighbours.setdefault(prev, []).append(dst)
        neighbours.setdefault(dst, []).append(prev)

    for members in columns.values():
        members.sort(key=_order_key)
    _order_within_ranks(columns, neighbours)

    layout = _layout_tb if direction == "TB" else _layout_lr
    box, edge_lines, total_w, total_h = layout(
        columns, by_id, rank, drawn, edge_chain, dummy_rank
    )

    out: list[str] = []

    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" width="{total_w}" height="{total_h}" '
        'font-family="monospace">'
    )
    out.append(f"  <title>{escape(title)}</title>" if title else "  <title>graph</title>")
    out.append(_arrow_marker())
    out += edge_lines

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
