"""Tests for the living-document conformance check and CH-13 cycle detection.

Both checks exist because a rule the module *declares* had no instrument on the
module's side. Both were scoped by measurement rather than assumption, and in
each case the naive scope produced a useless result:

* dates over all emitted files → 65 signals, 1 real violation (1.5% precision)
* cycles over all imports → 3 findings, none actionable

The tests below pin the corrected scopes, because it is the scope and not the
detection that makes either check worth having.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import architecture
from agentteams.living_doc import find_dated_prose, unfenced_spans

# --------------------------------------------------------------------------
# Living-document conformance
# --------------------------------------------------------------------------


def test_dated_prose_in_agent_file_is_flagged() -> None:
    files = {"git-operations.agent.md": "Body\n\n*(Moved on 2026-05-27 to survive merge)*\n"}
    hits = find_dated_prose(files)
    assert len(hits) == 1
    path, stamp, line = hits[0]
    assert path == "git-operations.agent.md"
    assert stamp == "2026-05-27"


def test_plain_dated_line_in_agent_prose_is_caught() -> None:
    """A date in prose is evidence of archaeology; audit.py reports it as a warning."""
    assert find_dated_prose({"a.agent.md": "text 2026-01-01 text"})


def test_fenced_content_is_exempt() -> None:
    """Fenced regions re-render on every update, so they cannot go stale."""
    body = (
        "prose\n"
        "<!-- AGENTTEAMS:BEGIN threat_intelligence v=1 -->\n"
        "- `CVE-2025-1` | vendor | added 2026-07-27 |\n"
        "<!-- AGENTTEAMS:END threat_intelligence -->\n"
        "more prose\n"
    )
    assert find_dated_prose({"security.agent.md": body}) == []


def test_reference_files_are_exempt() -> None:
    """Reference files are where the policy says volatile data belongs."""
    files = {
        "references/security-vulnerability-watch.reference.md": "added 2026-07-27",
        "vendor.reference.md": "generated 2026-06-23",
    }
    assert find_dated_prose(files) == []


def test_non_agent_files_are_ignored() -> None:
    assert find_dated_prose({"README.md": "released 2026-01-01"}) == []


def test_repeated_date_reported_once_per_file() -> None:
    files = {"a.agent.md": "on 2026-05-27 and again 2026-05-27 and 2026-05-27"}
    assert len(find_dated_prose(files)) == 1


def test_unfenced_spans_excludes_fenced_regions() -> None:
    text = "AAA<!-- AGENTTEAMS:BEGIN x -->BBB<!-- AGENTTEAMS:END x -->CCC"
    spans = unfenced_spans(text)
    assert any(text[a:b].startswith("AAA") for a, b in spans)
    assert not any("BBB" in text[a:b] for a, b in spans)


# --------------------------------------------------------------------------
# CH-13 circular imports
# --------------------------------------------------------------------------


def test_deferred_imports_do_not_form_a_load_time_cycle(tmp_path: Path) -> None:
    """The distinction the whole check turns on.

    ``a`` imports ``b`` at module level; ``b`` imports ``a`` inside a function.
    That is the standard way to break a cycle, and it must not be reported.
    """
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from demo import b\n", encoding="utf-8")
    (pkg / "b.py").write_text(
        "def later():\n    from demo import a\n    return a\n", encoding="utf-8"
    )

    graph = architecture.build_architecture(tmp_path, pkg)
    naive = architecture.detect_import_cycles(graph)
    strict = architecture.detect_import_cycles(
        graph, edges=architecture.module_level_edges(pkg, "demo")
    )
    assert naive, "walking all imports should see the apparent cycle"
    assert strict == [], "a deferred import is not a load-time cycle"


def test_genuine_load_time_cycle_is_detected(tmp_path: Path) -> None:
    pkg = tmp_path / "demo2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from demo2 import b\n", encoding="utf-8")
    (pkg / "b.py").write_text("from demo2 import a\n", encoding="utf-8")

    graph = architecture.build_architecture(tmp_path, pkg)
    cycles = architecture.detect_import_cycles(
        graph, edges=architecture.module_level_edges(pkg, "demo2")
    )
    assert len(cycles) == 1
    assert set(cycles[0]) == {"demo2.a", "demo2.b"}


def test_this_package_has_no_load_time_cycles() -> None:
    """Regression guard: the module's own package must stay acyclic at import."""
    root = Path(__file__).resolve().parent.parent
    pkg = architecture.discover_package_root(root)
    assert pkg is not None
    graph = architecture.build_architecture(root, pkg)
    cycles = architecture.detect_import_cycles(
        graph, edges=architecture.module_level_edges(pkg, pkg.name)
    )
    assert cycles == [], f"load-time import cycle(s): {cycles}"
