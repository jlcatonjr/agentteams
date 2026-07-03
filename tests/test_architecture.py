"""Tests for agentteams.architecture — repository module-dependency map."""

from __future__ import annotations

from pathlib import Path

from agentteams import architecture as arch


def _mkpkg(tmp_path: Path) -> Path:
    """A small package: pkg/{__init__, core, api, util, sub/__init__, sub/leaf}."""
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("import os\nimport json\n")
    (pkg / "api.py").write_text("from pkg import core\nfrom pkg.sub import leaf\nimport yaml\n")
    (pkg / "util.py").write_text("from . import core\nfrom ..pkg import api\n")
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "sub" / "leaf.py").write_text("from pkg import core\n")
    return repo


def test_discover_package_root_prefers_reponame(tmp_path):
    repo = tmp_path / "repo"
    (repo / "repo").mkdir(parents=True)      # matches repo name
    (repo / "repo" / "__init__.py").write_text("")
    (repo / "other").mkdir()
    (repo / "other" / "__init__.py").write_text("")
    assert arch.discover_package_root(repo) == repo / "repo"


def test_discover_package_root_none_when_no_package(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "loose.py").write_text("x = 1\n")
    assert arch.discover_package_root(repo) is None


def test_build_registers_all_modules(tmp_path):
    repo = _mkpkg(tmp_path)
    g = arch.build_architecture(repo, repo / "pkg")
    names = set(g.nodes)
    assert names == {"pkg", "pkg.core", "pkg.api", "pkg.util", "pkg.sub", "pkg.sub.leaf"}
    assert g.nodes["pkg"].is_package
    assert not g.nodes["pkg.core"].is_package


def test_internal_import_edges(tmp_path):
    repo = _mkpkg(tmp_path)
    g = arch.build_architecture(repo, repo / "pkg")
    # from pkg import core  →  api depends on core
    assert ("pkg.api", "pkg.core") in g.edges
    # from pkg.sub import leaf  →  api depends on the submodule
    assert ("pkg.api", "pkg.sub.leaf") in g.edges
    # relative: from . import core  →  util depends on core
    assert ("pkg.util", "pkg.core") in g.edges
    # sub.leaf → core
    assert ("pkg.sub.leaf", "pkg.core") in g.edges


def test_external_deps_exclude_stdlib(tmp_path):
    repo = _mkpkg(tmp_path)
    g = arch.build_architecture(repo, repo / "pkg")
    ext = g.all_external()
    assert "yaml" in ext          # third-party
    assert "os" not in ext        # stdlib
    assert "json" not in ext      # stdlib


def test_package_edges_drop_intra_package(tmp_path):
    repo = _mkpkg(tmp_path)
    g = arch.build_architecture(repo, repo / "pkg")
    # All modules are within pkg / pkg.sub. api→sub.leaf crosses pkg↔pkg.sub.
    pedges = g.package_edges()
    assert all(s != t for s, t in pedges)       # no self-loops
    assert ("pkg", "pkg.sub") in pedges         # api (pkg) → leaf (pkg.sub)


def test_output_is_deterministic(tmp_path):
    repo = _mkpkg(tmp_path)
    d1 = arch.build_architecture(repo, repo / "pkg").to_markdown_document()
    d2 = arch.build_architecture(repo, repo / "pkg").to_markdown_document()
    assert d1 == d2


def test_generate_document_none_without_package(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert arch.generate_architecture_document(repo) is None


def test_maps_agentteams_itself():
    """End-to-end on the real package: sanity bounds, no crash."""
    repo_root = Path(__file__).parent.parent
    g = arch.build_architecture(repo_root, repo_root / "agentteams")
    assert len(g.nodes) > 50
    assert "agentteams.graph" in g.nodes
    assert "agentteams.cli.generate" in g.nodes
    # cli.app imports generate — a known real edge
    assert ("agentteams.cli.app", "agentteams.cli.generate") in g.edges
    doc = g.to_markdown_document()
    assert "Repository Architecture Map" in doc
    assert "```mermaid" in doc


def test_relative_imports_in_init_resolve_correctly(tmp_path):
    """Regression: relative re-exports in __init__.py must not be dropped or
    turned into phantom external deps (the __package__ off-by-one bug)."""
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("from . import core\nfrom .sub import leaf\n")
    (pkg / "core.py").write_text("x = 1\n")
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "sub" / "leaf.py").write_text("y = 2\n")
    g = arch.build_architecture(repo, pkg)
    assert ("pkg", "pkg.core") in g.edges
    assert ("pkg", "pkg.sub.leaf") in g.edges
    assert g.all_external() == []          # no phantom "sub" external dep


def test_relative_import_never_external(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    # relative import of a name that isn't a submodule → internal edge, not external
    (pkg / "a.py").write_text("from . import notamodule\n")
    g = arch.build_architecture(repo, pkg)
    assert g.all_external() == []


def test_iter_module_files_ignores_ancestor_dir_names(tmp_path):
    """Regression: a checkout under a dir named 'templates' must not empty the map."""
    repo = tmp_path / "templates" / "myrepo"     # 'templates' is an ANCESTOR
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("import os\n")
    g = arch.build_architecture(repo, pkg)
    assert len(g.nodes) == 2                # __init__ + core, not zero


def test_mixed_from_import_records_package_edge(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("DEFAULT = 1\n")
    (pkg / "sub.py").write_text("x = 1\n")
    # `sub` is a submodule; `DEFAULT` is a name in pkg's __init__.
    (pkg / "api.py").write_text("from pkg import sub, DEFAULT\n")
    g = arch.build_architecture(repo, pkg)
    assert ("pkg.api", "pkg.sub") in g.edges     # submodule edge
    assert ("pkg.api", "pkg") in g.edges         # package-__init__ edge (was dropped)


def test_repo_local_vs_third_party_external(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (repo / "sibling.py").write_text("z = 1\n")   # repo-local, outside pkg
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("import sibling\nimport requests\n")
    g = arch.build_architecture(repo, pkg)
    assert g.all_external() == ["requests"]       # third-party only
    assert g.all_repo_local() == ["sibling"]      # repo-local separated


def test_discover_prefers_largest_package_over_alphabetical(tmp_path):
    repo = tmp_path / "myproj"
    (repo / "alpha").mkdir(parents=True)
    (repo / "alpha" / "__init__.py").write_text("")
    zebra = repo / "zebra"
    zebra.mkdir()
    for n in ("__init__", "a", "b", "c"):
        (zebra / f"{n}.py").write_text("")
    # 'alpha' sorts first but 'zebra' is the real product package (more modules).
    assert arch.discover_package_root(repo) == zebra


def test_discover_supports_src_layout(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    assert arch.discover_package_root(repo) == pkg


def test_cli_json_and_markdown(tmp_path, capsys):
    repo = _mkpkg(tmp_path)
    rc = arch.main([str(repo), "--format", "json"])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["root_package"] == "pkg"
    assert "pkg.core" in data["modules"]
