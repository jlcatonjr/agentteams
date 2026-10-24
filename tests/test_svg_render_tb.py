"""Tests for ``render_svg(direction=...)`` — the opt-in top-to-bottom (TB) layout.

Also pins the default left-to-right (LR) output to a golden hash captured from the
renderer *before* the ``direction`` option existed, so the default stays
byte-identical. No broad/bare ``except`` and no swallow handlers (code-hygiene
ratchet). XML validity is asserted by letting ``parseString`` raise.
"""

from __future__ import annotations

import hashlib
import re
from xml.dom.minidom import parseString

import pytest

from agentteams.svg_render import SvgEdge, SvgNode, render_svg

#: sha256 of ``_golden_render()`` produced by the pre-change (LR-only) renderer.
_LR_GOLDEN_SHA256 = "35e7f4163593245f6571e570dc2e9c9b62500717fb9d4fcb3e4274ff2138fbc1"
_LR_GOLDEN_LEN = 2159


def _golden_spec() -> tuple[list[SvgNode], list[SvgEdge]]:
    """Small graph exercising long, back, mutual, and isolated-node cases."""
    nodes = [
        SvgNode("a", "alpha", "x"), SvgNode("b", "beta"),
        SvgNode("c", "gamma-long-label", "y"), SvgNode("d", "d"), SvgNode("e", "iso"),
    ]
    edges = [
        SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("a", "c"), SvgEdge("a", "d"),
        SvgEdge("d", "c"), SvgEdge("c", "a"), SvgEdge("b", "d"), SvgEdge("d", "b"),
    ]
    return nodes, edges


def _render(direction: str | None = None) -> str:
    nodes, edges = _golden_spec()
    kwargs = {} if direction is None else {"direction": direction}
    return render_svg(nodes, edges, palette={"x": ("#fff", "#000")}, title="golden",
                      **kwargs)


def _dense_spec() -> tuple[list[SvgNode], list[SvgEdge]]:
    ids = ["a", "b", "c", "d", "e", "f", "g"]
    nodes = [SvgNode(i, i.title() * 3, "k") for i in ids]
    edges = [
        SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("c", "d"), SvgEdge("d", "e"),
        SvgEdge("e", "f"), SvgEdge("f", "g"), SvgEdge("a", "g"), SvgEdge("a", "e"),
        SvgEdge("g", "a"), SvgEdge("f", "b"), SvgEdge("b", "a"), SvgEdge("c", "h"),
    ]
    return nodes, edges


def _viewbox(svg: str) -> tuple[int, int]:
    vb = parseString(svg).documentElement.getAttribute("viewBox").split()
    assert vb[:2] == ["0", "0"]
    return int(vb[2]), int(vb[3])


def _rects(svg: str) -> list[tuple[int, int, int, int]]:
    doc = parseString(svg)
    return [
        tuple(int(r.getAttribute(a)) for a in ("x", "y", "width", "height"))
        for r in doc.getElementsByTagName("rect")
    ]


def test_lr_default_is_byte_identical_to_pre_change_golden():
    svg = _render()
    assert len(svg) == _LR_GOLDEN_LEN
    assert hashlib.sha256(svg.encode()).hexdigest() == _LR_GOLDEN_SHA256
    assert _render("LR") == svg


def test_invalid_direction_raises():
    nodes, edges = _golden_spec()
    for bad in ("RL", "BT", "tb", ""):
        with pytest.raises(ValueError, match="direction"):
            render_svg(nodes, edges, direction=bad)


def test_tb_is_valid_xml_and_deterministic():
    nodes, edges = _dense_spec()
    first = render_svg(nodes, edges, direction="TB")
    parseString(first)
    assert render_svg(list(reversed(nodes)), list(reversed(edges)), direction="TB") == first
    assert render_svg(nodes, edges, direction="TB") == first


def test_tb_same_node_and_edge_count_as_lr():
    for nodes, edges in (_golden_spec(), _dense_spec()):
        lr = render_svg(nodes, edges)
        tb = render_svg(nodes, edges, direction="TB")
        assert tb.count("<rect ") == lr.count("<rect ") == len(nodes)
        assert tb.count("<path ") == lr.count("<path ")
        assert tb.count('stroke-dasharray') == lr.count('stroke-dasharray')
        assert tb.count('marker-start') == lr.count('marker-start')


def test_tb_every_box_inside_viewbox_and_integral():
    nodes, edges = _dense_spec()
    svg = render_svg(nodes, edges, direction="TB")
    width, height = _viewbox(svg)
    for x, y, w, h in _rects(svg):
        assert 0 <= x and x + w <= width
        assert 0 <= y and y + h <= height
    for d in re.findall(r'\bd="([^"]*)"', svg):
        assert not re.search(r"\d+\.\d+", d), d
        for num in re.findall(r"-?\d+", d):
            assert 0 <= int(num) <= max(width, height)


def test_tb_chain_is_taller_than_wide_and_lr_wider_than_tall():
    ids = ["n1", "n2", "n3", "n4", "n5", "n6"]
    nodes = [SvgNode(i, i) for i in ids]
    edges = [SvgEdge(a, b) for a, b in zip(ids, ids[1:])]
    tb_w, tb_h = _viewbox(render_svg(nodes, edges, direction="TB"))
    lr_w, lr_h = _viewbox(render_svg(nodes, edges))
    assert tb_h > tb_w
    assert lr_w > lr_h


def test_tb_ranks_are_rows_and_edges_run_bottom_to_top():
    nodes = [SvgNode("a", "a"), SvgNode("b", "b"), SvgNode("c", "c")]
    edges = [SvgEdge("a", "b"), SvgEdge("a", "c")]
    svg = render_svg(nodes, edges, direction="TB")
    a, b, c = _rects(svg)                      # rects are emitted in sorted id order
    # b and c share rank 1: same row, side by side, below a.
    assert b[1] == c[1] > a[1] + a[3]
    assert b[0] + b[2] <= c[0] or c[0] + c[2] <= b[0]
    for d in re.findall(r'\bd="([^"]*)"', svg):
        nums = [int(n) for n in re.findall(r"-?\d+", d)]
        start, end = (nums[0], nums[1]), (nums[-2], nums[-1])
        assert start == (a[0] + a[2] // 2, a[1] + a[3])      # bottom-centre of source
        assert end in {(t[0] + t[2] // 2, t[1]) for t in (b, c)}   # top-centre of target


def test_tb_long_edge_threads_waypoints_between_rows():
    nodes = [SvgNode(i, i) for i in "abcd"]
    edges = [SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("c", "d"), SvgEdge("a", "d")]
    svg = render_svg(nodes, edges, direction="TB")
    assert svg.count("<path ") == 4
    boxes = _rects(svg)
    long_path = max(re.findall(r'\bd="([^"]*)"', svg), key=len)
    nums = [int(n) for n in re.findall(r"-?\d+", long_path)]
    pts = list(zip(nums[0::2], nums[1::2]))
    # Every vertical lane segment (same x) of the routed spline avoids unrelated boxes.
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 != x2:
            continue
        lo, hi = sorted((y1, y2))
        for bx, by, bw, bh in boxes[1:3]:      # b and c sit on the intermediate rows
            overlaps_y = lo < by + bh and hi > by
            assert not (overlaps_y and bx < x1 < bx + bw)
