"""Tests for F-CODEIDX Phase B — code_sources (import graph + API resolution).

The load-bearing guarantee is T7: the collectors NEVER execute third-party code
(static ``ast`` + ``importlib.metadata`` only). ``jsonschema`` is a runtime
dependency of agentteams, so it is a reliable installed fixture.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest

from agentteams import code_sources as cs


# ------------------------------ import extraction ------------------------------

def test_extract_imports_is_static_not_executed():
    # If this were exec'd rather than parsed, the raise would fire.
    text = (
        "raise RuntimeError('module body must not run')\n"
        "import requests\n"
        "import os.path\n"
        "from collections import OrderedDict\n"
        "from . import sibling\n"
        "from ..pkg import thing\n"
    )
    names = cs.extract_imports(text)
    assert "requests" in names
    assert "os" in names  # top-level of os.path
    assert "collections" in names
    assert "sibling" not in names  # relative import skipped (local)
    assert "pkg" not in names      # relative import skipped (local)


def test_extract_imports_syntax_error_is_empty():
    assert cs.extract_imports("def (:\n  oops") == set()


# ------------------------------ classification ------------------------------

def test_classify_external_excludes_stdlib_and_local(tmp_path):
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "localmod.py").write_text("")
    ext = cs.classify_external(
        {"os", "sys", "json", "requests", "jsonschema", "mypkg", "localmod"},
        tmp_path,
    )
    assert "requests" in ext and "jsonschema" in ext
    assert "os" not in ext and "sys" not in ext and "json" not in ext  # stdlib
    assert "mypkg" not in ext and "localmod" not in ext  # local


# ------------------------------ NO EXECUTION (T7) ------------------------------

def test_collect_api_never_imports_third_party(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("third-party code must not be imported (T7)")

    monkeypatch.setattr(importlib, "import_module", boom)
    monkeypatch.setattr(importlib.util, "find_spec", boom)
    src = tmp_path / "s.py"
    src.write_text("import jsonschema\n")
    coll = cs.collect_api([src], tmp_path)  # must not raise
    assert "jsonschema" in coll.external_imports


def test_collect_api_populates_api_doc_for_installed_dep(tmp_path):
    src = tmp_path / "s.py"
    src.write_text("import jsonschema\n")
    coll = cs.collect_api([src], tmp_path)
    # api-doc is METADATA-backed (robust) — shipped before api-module (R2-M2)
    assert coll.api_doc_units
    assert any(u["source_kind"] == "api-doc" for u in coll.api_doc_units)
    assert coll.dependency_fingerprint  # non-empty
    assert ("jsonschema" in coll.resolved_source) or ("jsonschema" in coll.declared_only)


def test_collect_api_declared_only_for_unresolvable(tmp_path):
    src = tmp_path / "s.py"
    src.write_text("import totallynotinstalled_xyz\n")
    coll = cs.collect_api([src], tmp_path)
    assert "totallynotinstalled_xyz" in coll.external_imports
    assert "totallynotinstalled_xyz" in coll.declared_only
    stubs = [u for u in coll.api_module_units if (u["provenance"] or {}).get("declared_only")]
    assert any((u["provenance"] or {}).get("distribution") == "totallynotinstalled_xyz" for u in stubs)


# ------------------------------ symbol extraction + caps ------------------------------

def test_public_symbol_units_excludes_private_and_caps(tmp_path):
    body = "def _hidden():\n    return 0\n"
    body += "\n".join(f"def pub{i}():\n    return {i}\n" for i in range(100))
    f = tmp_path / "m.py"
    f.write_text(body)
    units = cs._public_symbol_units(f, rel="m", provenance={"distribution": "m", "version": None})
    assert units
    assert len(units) <= cs._MAX_API_SYMBOLS_PER_MODULE
    names = {u["symbol"] for u in units}
    assert "_hidden" not in names
    assert all(u["source_kind"] == "api-module" for u in units)
    assert all(u["signature"] for u in units)


def test_public_symbol_units_respects_byte_cap(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("x = 'a'\n" * (cs._MAX_BYTES_PER_API_FILE // 6 + 100))
    assert cs._public_symbol_units(f, rel="big", provenance={}) == []


# ------------------------------ dependency fingerprint (R2-M1) ------------------------------

def test_dependency_fingerprint_changes_on_version_bump(monkeypatch, tmp_path):
    src = tmp_path / "s.py"
    src.write_text("import jsonschema\n")
    fp1 = cs.compute_dependency_fingerprint([src], tmp_path)
    monkeypatch.setattr(cs, "_distribution_version", lambda d: "999.0.0")
    fp2 = cs.compute_dependency_fingerprint([src], tmp_path)
    assert fp1 and fp2 and fp1 != fp2


def test_dependency_fingerprint_changes_on_new_import(tmp_path):
    s1 = tmp_path / "a.py"
    s1.write_text("import jsonschema\n")
    fp1 = cs.compute_dependency_fingerprint([s1], tmp_path)
    s2 = tmp_path / "b.py"
    s2.write_text("import jsonschema\nimport pytest\n")
    fp2 = cs.compute_dependency_fingerprint([s1, s2], tmp_path)
    assert fp1 != fp2


def test_dependency_manifest_texts_reads_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    texts = cs.dependency_manifest_texts(tmp_path)
    assert any("name='x'" in t for t in texts)
