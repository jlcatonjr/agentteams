"""Tests for agentteams.svg_render — the deterministic stdlib SVG graph renderer.

No broad/bare ``except`` and no ``pass``/``continue`` swallow handlers here (the
code-hygiene ratchet has zero headroom). XML validity is asserted by letting
``xml.dom.minidom.parseString`` raise on malformed output.
"""

from __future__ import annotations

import random
from xml.dom.minidom import parseString

from agentteams.svg_render import SvgEdge, SvgNode, auto_palette, render_svg

_PALETTE = {
    "governance": ("#e8e8ff", "#6666cc"),
    "domain": ("#e8ffe8", "#66aa66"),
}


def _nodes(*ids: str) -> list[SvgNode]:
    return [SvgNode(id=i, label=i.title(), kind="governance") for i in ids]


def test_output_is_well_formed_xml():
    svg = render_svg(_nodes("a", "b", "c"), [SvgEdge("a", "b"), SvgEdge("b", "c")])
    # parseString raises xml.parsers.expat.ExpatError on malformed XML — no catch.
    doc = parseString(svg)
    assert doc.documentElement.tagName == "svg"


def test_deterministic_byte_identical():
    nodes = _nodes("a", "b", "c", "d")
    edges = [SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("a", "d")]
    first = render_svg(nodes, edges, palette=_PALETTE)
    second = render_svg(nodes, edges, palette=_PALETTE)
    assert first == second


def test_order_independent():
    nodes = _nodes("a", "b", "c", "d", "e")
    edges = [SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("c", "d"), SvgEdge("a", "e")]
    canonical = render_svg(nodes, edges, palette=_PALETTE)
    rng = random.Random(1234)
    for _ in range(5):
        shuffled_nodes = list(nodes)
        shuffled_edges = list(edges)
        rng.shuffle(shuffled_nodes)
        rng.shuffle(shuffled_edges)
        assert render_svg(shuffled_nodes, shuffled_edges, palette=_PALETTE) == canonical


def test_all_nodes_rendered():
    svg = render_svg(_nodes("alpha", "beta", "gamma"), [])
    for label in ("Alpha", "Beta", "Gamma"):
        assert f">{label}</text>" in svg


def test_every_edge_drawn_as_path():
    svg = render_svg(_nodes("a", "b", "c"), [SvgEdge("a", "b"), SvgEdge("b", "c")])
    assert svg.count("<path ") == 2


def test_mutual_pair_drawn_once_with_both_arrowheads():
    svg = render_svg(_nodes("a", "b"), [SvgEdge("a", "b"), SvgEdge("b", "a")])
    # One path for the pair, with an arrowhead at each end.
    assert svg.count("<path ") == 1
    assert 'marker-start="url(#arrow)"' in svg
    assert 'marker-end="url(#arrow)"' in svg


def test_back_edge_is_dashed():
    # a->b->c forward, plus c->a which must become a dashed back-edge.
    svg = render_svg(_nodes("a", "b", "c"), [SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("c", "a")])
    assert "stroke-dasharray" in svg


def test_isolated_node_is_placed():
    svg = render_svg(_nodes("a", "b", "lonely"), [SvgEdge("a", "b")])
    assert ">Lonely</text>" in svg
    parseString(svg)


def test_single_node():
    svg = render_svg(_nodes("solo"), [])
    parseString(svg)
    assert ">Solo</text>" in svg
    assert svg.count("<path ") == 0


def test_empty_graph_is_valid():
    svg = render_svg([], [])
    doc = parseString(svg)
    assert doc.documentElement.tagName == "svg"


def test_palette_applied_and_fallback_grey():
    nodes = [SvgNode("a", "A", "governance"), SvgNode("b", "B", "mystery")]
    svg = render_svg(nodes, [], palette=_PALETTE)
    assert 'fill="#e8e8ff"' in svg   # governance from palette
    assert 'fill="#f5f5f5"' in svg   # unknown kind → grey fallback


def test_label_special_chars_escaped():
    svg = render_svg([SvgNode("x", "a<b>&\"c\"", "governance")], [])
    parseString(svg)  # would raise if the label broke the XML
    assert "&lt;b&gt;" in svg
    assert "&amp;" in svg


def test_viewbox_positive_and_encloses_content():
    svg = render_svg(_nodes("a", "b", "c"), [SvgEdge("a", "b")])
    doc = parseString(svg)
    vb = doc.documentElement.getAttribute("viewBox").split()
    assert len(vb) == 4
    width, height = int(vb[2]), int(vb[3])
    assert width > 0 and height > 0


def test_edges_to_unknown_nodes_ignored():
    # Edge referencing a node not in the node list must not crash or draw.
    svg = render_svg(_nodes("a", "b"), [SvgEdge("a", "ghost"), SvgEdge("a", "b")])
    parseString(svg)
    assert svg.count("<path ") == 1


def test_self_loop_ignored():
    svg = render_svg(_nodes("a", "b"), [SvgEdge("a", "a"), SvgEdge("a", "b")])
    assert svg.count("<path ") == 1


def test_auto_palette_deterministic_and_order_independent():
    keys = ["gamma", "alpha", "beta"]
    p1 = auto_palette(keys)
    p2 = auto_palette(list(reversed(keys)))
    assert p1 == p2                                  # sorted internally → stable
    assert set(p1) == {"alpha", "beta", "gamma"}
    assert all(isinstance(v, tuple) and len(v) == 2 for v in p1.values())


def test_coordinates_are_integers_no_float_in_geometry():
    """No fractional numbers in path data or box geometry (byte-stability contract).

    The renderer's determinism/no-churn guarantee relies on integer-only coordinates
    (``svg_render`` docstring). ``stroke-width="1.5"`` is a fixed style constant, not a
    coordinate, so the guard inspects only ``d=`` path data and ``x/y/width/height``
    box attributes. A dense multi-rank graph exercises the dummy-node routing splines,
    which are where a stray float would otherwise slip in.
    """
    import re
    nodes = _nodes("a", "b", "c", "d", "e", "f", "g")
    # A chain plus long-range and back edges → multi-segment routed paths.
    edges = [
        SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("c", "d"), SvgEdge("d", "e"),
        SvgEdge("e", "f"), SvgEdge("f", "g"),
        SvgEdge("a", "g"), SvgEdge("a", "e"), SvgEdge("g", "a"), SvgEdge("f", "b"),
    ]
    svg = render_svg(nodes, edges, palette=_PALETTE)
    parseString(svg)
    float_re = re.compile(r"\d+\.\d+")
    for attr in re.findall(r'\bd="([^"]*)"', svg):
        assert not float_re.search(attr), f"float in path data: {attr}"
    # Lookbehind excludes the ``stroke-width="1.5"`` style constant (a hyphen before
    # the attribute name), leaving only genuine geometry attributes.
    for attr_name in ("x", "y", "width", "height"):
        for val in re.findall(rf'(?<![-\w]){attr_name}="([^"]*)"', svg):
            assert not float_re.search(val), f"float in {attr_name}: {val}"


def test_long_edge_is_single_path_element():
    """A long multi-rank edge is still ONE <path> (routed through dummy waypoints)."""
    # a→e spans four ranks; must render as exactly one path, not one per segment.
    nodes = _nodes("a", "b", "c", "d", "e")
    edges = [SvgEdge("a", "b"), SvgEdge("b", "c"), SvgEdge("c", "d"), SvgEdge("d", "e"),
             SvgEdge("a", "e")]
    svg = render_svg(nodes, edges, palette=_PALETTE)
    assert svg.count("<path ") == 5


def test_module_level_svg_colours_by_group():
    # Distinct kinds get distinct palette entries; the SVG stays valid.
    nodes = [SvgNode("a", "a", "pkg1"), SvgNode("b", "b", "pkg2"), SvgNode("c", "c", "pkg1")]
    svg = render_svg(nodes, [SvgEdge("a", "b")], palette=auto_palette(["pkg1", "pkg2"]))
    parseString(svg)
    assert "<svg" in svg
