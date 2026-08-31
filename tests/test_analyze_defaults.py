"""Tests for analyze._default_reference_db_path / _default_style_reference_path."""

from __future__ import annotations

from agentteams.analyze import _default_reference_db_path, _default_style_reference_path


def test_no_doc_site_returns_none(tmp_path):
    desc = {"existing_project_path": str(tmp_path)}
    assert _default_reference_db_path(desc) is None
    assert _default_style_reference_path(desc) is None


def test_doc_site_without_docs_dir_returns_none(tmp_path):
    desc = {"doc_site_config_file": "mkdocs.yml", "existing_project_path": str(tmp_path)}
    assert _default_reference_db_path(desc) is None
    assert _default_style_reference_path(desc) is None


def test_doc_site_with_docs_only(tmp_path):
    (tmp_path / "docs").mkdir()
    desc = {"doc_site_config_file": "mkdocs.yml", "existing_project_path": str(tmp_path)}
    assert _default_reference_db_path(desc) == "docs/"
    assert _default_style_reference_path(desc) == "docs/"


def test_doc_site_with_docs_src(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs_src").mkdir()
    desc = {"doc_site_config_file": "mkdocs.yml", "existing_project_path": str(tmp_path)}
    assert _default_reference_db_path(desc) == "docs/"
    assert _default_style_reference_path(desc) == "docs_src/"


def test_no_existing_project_path_returns_none():
    desc = {"doc_site_config_file": "mkdocs.yml"}
    assert _default_reference_db_path(desc) is None
    assert _default_style_reference_path(desc) is None
