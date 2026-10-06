"""Tests for agentteams/tool_version_source.py — tool versions read from the project's pin file.

Three contracts: ``resolve_tool_version`` is a pure file read; a brief WITHOUT ``version_source``
renders byte-for-byte what it rendered before the feature existed; a brief WITH it renders a doc
that never presents the brief's version as fixed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import ingest
from agentteams.analyze import build_manifest
from agentteams.analyze_tools import detect_reference_tools, detect_tool_agents
from agentteams.render import render_all, resolve_placeholders
from agentteams.tool_version_source import (
    expand_docs_url,
    major_minor,
    resolve_tool_version,
    version_source_errors,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "agentteams" / "templates"
SCHEMA = TEMPLATES.parent / "schemas" / "project-description.schema.json"

_SOURCE = {
    "file": "lean/lean-toolchain",
    "pattern": r"v(?P<version>[0-9][0-9.]*)",
    "resolver": "mathagents run lean_docs",
}
_LEAN = {
    "name": "Lean 4",
    "category": "build-system",
    "version": "4.24.0",
    "docs_url": "https://lean-lang.org/doc/reference/{version}/",
    "version_source": _SOURCE,
}
_PANDOC = {"name": "Pandoc", "category": "cli", "version": "3.1", "docs_url": "https://pandoc.org/MANUAL.html"}
_NUMPY = {"name": "numpy", "category": "library", "version": "2.0"}


def _description(*tools: dict) -> dict:
    return {
        "project_name": "P",
        "project_goal": "Prove things.",
        "deliverables": ["d"],
        "tools": [dict(t) for t in tools],
    }


def _render(description: dict, framework: str) -> dict[str, str]:
    manifest = build_manifest(description, framework=framework)
    return dict(render_all(manifest, templates_dir=TEMPLATES))


# ---------------------------------------------------------------------------
# resolve_tool_version
# ---------------------------------------------------------------------------

def _pin(tmp_path: Path, text: str) -> Path:
    (tmp_path / "lean").mkdir()
    (tmp_path / "lean" / "lean-toolchain").write_text(text, encoding="utf-8")
    return tmp_path


def test_resolve_reads_the_pin_file(tmp_path):
    root = _pin(tmp_path, "leanprover/lean4:v4.25.1\n")
    assert resolve_tool_version(_LEAN, root) == "4.25.1"


@pytest.mark.parametrize("text", ["", "leanprover/lean4:nightly\n"])
def test_resolve_returns_none_when_the_pattern_does_not_match(tmp_path, text):
    assert resolve_tool_version(_LEAN, _pin(tmp_path, text)) is None


def test_resolve_returns_none_without_a_pin_file_or_source(tmp_path):
    assert resolve_tool_version(_LEAN, tmp_path) is None
    assert resolve_tool_version(_PANDOC, tmp_path) is None


def test_resolve_refuses_a_path_outside_the_project_root(tmp_path):
    (tmp_path / "outside").write_text("v9.9.9", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    tool = {**_LEAN, "version_source": {**_SOURCE, "file": "../outside"}}
    assert resolve_tool_version(tool, root) is None


def test_docs_url_placeholders_expand():
    assert expand_docs_url("https://x/{version}/{major_minor}/", "4.24.0") == "https://x/4.24.0/4.24/"
    assert expand_docs_url("https://x/{version}/", "") == "https://x/{version}/"
    assert expand_docs_url("https://x/latest/", "4.24.0") == "https://x/latest/"
    assert major_minor("v4.24.0") == "v4.24" and major_minor("18") == "18"


# ---------------------------------------------------------------------------
# Validation — ingest layer and schema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source, fragment", [
    ({**_SOURCE, "pattern": "v([0-9.]+"}, "does not compile"),
    ({**_SOURCE, "pattern": r"v([0-9.]+)"}, "named group (?P<version>"),
    ({"pattern": _SOURCE["pattern"]}, "file is required"),
    ({**_SOURCE, "file": "/etc/lean-toolchain"}, "relative to the project root"),
    ({**_SOURCE, "file": "../lean-toolchain"}, "relative to the project root"),
    ({**_SOURCE, "resolver": ""}, "resolver must be a non-empty string"),
    ({**_SOURCE, "extra": 1}, "unknown key(s): extra"),
])
def test_invalid_version_source_is_reported_by_ingest(source, fragment):
    errors = ingest.validate(_description({**_LEAN, "version_source": source}))
    assert any(fragment in e and "Lean 4" in e for e in errors), errors


def test_valid_version_source_passes_ingest():
    assert ingest.validate(_description(_LEAN, _PANDOC)) == []
    assert version_source_errors(_PANDOC) == []


def test_schema_accepts_version_source_and_requires_the_named_group():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    tools_schema = schema["properties"]["tools"]["items"]
    jsonschema.validate(_LEAN, tools_schema)
    for bad in ({**_SOURCE, "pattern": r"v([0-9.]+)"}, {**_SOURCE, "file": "../x"}, {"file": "x"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**_LEAN, "version_source": bad}, tools_schema)


# ---------------------------------------------------------------------------
# Carry-through and rendering
# ---------------------------------------------------------------------------

def test_specs_carry_version_source_only_when_declared():
    lean_spec, pandoc_spec = detect_tool_agents([_LEAN, _PANDOC])
    assert lean_spec["version_source"] == _SOURCE
    assert "version_source" not in pandoc_spec
    ref = detect_reference_tools([{**_NUMPY, "version_source": _SOURCE}, _NUMPY])
    assert ref[0]["version_source"] == _SOURCE and "version_source" not in ref[1]


@pytest.mark.parametrize("framework", ["copilot-vscode", "claude"])
def test_output_without_version_source_is_byte_identical(framework):
    """The regression the feature must not cause: a brief that does not use it sees no change.

    "Before" is the template as it was — the current one with the single inserted
    ``{TOOL_VERSION_RESOLUTION}`` token removed — filled from the old placeholder values
    (``TOOL_VERSION`` = the brief's version, ``TOOL_DOCS_URL`` = its docs URL).
    """
    description = _description(_PANDOC, _NUMPY, {**_LEAN, "version_source": None})
    del description["tools"][2]["version_source"]
    description["tools"][2]["docs_url"] = "https://lean-lang.org/doc/reference/latest/"
    manifest = build_manifest(description, framework=framework)
    rendered = dict(render_all(manifest, templates_dir=TEMPLATES))
    checked = 0
    from agentteams.render import _build_placeholder_map_for_file
    for spec in manifest["output_files"]:
        if not spec.get("tool_slug") or spec["path"] not in rendered:
            continue
        template = spec["template"]
        if not (TEMPLATES / template).exists():
            template = spec["fallback_template"]
        old_template = (TEMPLATES / template).read_text(encoding="utf-8").replace(
            "{TOOL_VERSION_RESOLUTION}", "")
        mapping = _build_placeholder_map_for_file(
            manifest, component_slug=spec.get("component_slug"), file_spec=spec)
        tool = next(t for t in manifest["tool_agents"] + manifest["reference_tools"]
                    if t["slug"] == spec["tool_slug"])
        mapping.pop("TOOL_VERSION_RESOLUTION")
        mapping["TOOL_VERSION"] = tool.get("tool_version", "")
        mapping["TOOL_DOCS_URL"] = tool.get("docs_url", "") or "{MANUAL:TOOL_DOCS_URL}"
        expected = resolve_placeholders(old_template, mapping)
        actual = rendered[spec["path"]]
        assert expected in actual, f"{spec['path']} changed for a brief without version_source"
        assert "Version resolution" not in actual and "{TOOL_VERSION_RESOLUTION}" not in actual
        checked += 1
    assert checked == 3


@pytest.mark.parametrize("framework", ["copilot-vscode", "claude"])
def test_version_sourced_doc_instructs_resolution(framework):
    rendered = _render(_description(_LEAN), framework)
    (path, doc), = [(p, c) for p, c in rendered.items() if "lean-4" in p]
    assert "(version pinned in lean/lean-toolchain; generation-time default 4.24.0)" in doc
    assert "**Lean 4 4.24.0**" not in doc and "`Lean 4` `4.24.0`" not in doc
    assert "**Version resolution.**" in doc
    assert "Read `lean/lean-toolchain`" in doc and "`v(?P<version>[0-9][0-9.]*)`" in doc
    assert "Run `mathagents run lean_docs`" in doc
    assert "Consult `https://lean-lang.org/doc/reference/{version}/` with `{version}` replaced" in doc
    assert "`4.24.0` is the generation-time default" in doc
    assert "{TOOL_VERSION_RESOLUTION}" not in doc


def test_version_sourced_reference_doc_without_resolver_or_templated_url():
    tool = {**_NUMPY, "docs_url": "https://numpy.org/doc/stable/",
            "version_source": {"file": "pyproject.toml", "pattern": r"numpy==(?P<version>[0-9.]+)"}}
    rendered = _render(_description(tool), "copilot-vscode")
    doc = rendered["references/ref-numpy-reference.md"]
    assert "(version pinned in pyproject.toml; generation-time default 2.0)" in doc
    assert "Run `" not in doc
    assert "2. Consult the documentation above for the resolved version" in doc


def test_docs_url_placeholder_expands_to_version_without_version_source():
    tool = {**_PANDOC, "docs_url": "https://pandoc.org/{major_minor}/MANUAL.html"}
    rendered = _render(_description(tool), "copilot-vscode")
    doc = next(c for p, c in rendered.items() if "pandoc" in p)
    assert "https://pandoc.org/3.1/MANUAL.html" in doc and "{major_minor}" not in doc
