"""``--framework wasm`` adapter (WASM plan P08): registration, layout, rendered format, byte stability, no secrets.

Mirrors ``tests/test_grok_adapter.py`` / ``tests/test_agents_md_framework.py``. The live run of a rendered team is in
``tests/test_wasm_framework_cli.py``; the loader in ``tests/test_wasm_framework_loader.py``; tool translation in
``tests/test_wasm_framework_scoping.py``; the WASM / OS switch interaction in ``tests/test_wasm_framework_state.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import model_providers as MP
from agentteams.frameworks.base import FrameworkAdapter
from agentteams.frameworks.format_spec import FORMAT_SPECS, spec_by_adapter_id
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.frameworks.wasm import WasmAdapter
from agentteams.wasm_agent import team as T
from agentteams.wasm_agent import tools as AT
from tests._wasm_framework_helpers import REPO, WASM_BRIEF, project, render, team_files, write_brief

_AGENT = """---
name: Navigator — Demo
description: "Finds files"
user-invocable: true
tools: {tools}
agents:
  - orchestrator
  - security
  - not-in-team
model: ["Claude Opus 4.8 (copilot)"]
handoffs:
  - label: Back
    agent: orchestrator
    prompt: "Return."
    send: false
---

# Navigator

Body under .github/agents.

## Handoffs

- Back to @orchestrator
"""
_TEAM = {"agent_slug_list": ["navigator", "orchestrator", "security"], "project_name": "Demo"}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = project(tmp_path_factory.mktemp("wasmfw"))
    assert render(root, write_brief(root)) == 0
    return root


# --- registration and layout -------------------------------------------------------------------------------------

def test_registered() -> None:
    assert FRAMEWORKS["wasm"] is WasmAdapter
    assert WasmAdapter().framework_id == "wasm"


@pytest.mark.parametrize("given, expected", [
    (".", ".agentteams-wasm/agents"), ("x/.agentteams-wasm", "x/.agentteams-wasm/agents"),
    ("x/.agentteams-wasm/agents", "x/.agentteams-wasm/agents"),
])
def test_paths(given: str, expected: str, tmp_path: Path) -> None:
    a = WasmAdapter()
    assert a.get_agents_dir(tmp_path) == tmp_path / ".agentteams-wasm" / "agents"
    assert a.normalize_output_path(Path(given)) == Path(expected)


def test_layout_hooks() -> None:
    a = WasmAdapter()
    assert a.finalize_output_path("../copilot-instructions.md", "instructions") == "../TEAM.md"
    assert a.finalize_output_path("navigator.agent.md", "agent") == "navigator.md"
    assert a.get_file_extension("agent") == ".md"
    assert a.vscode_tasks_rel_path() is None and not a.has_skill_concept()
    assert not a.supports_handoffs() and a.handoff_delivery_mode() == "manifest"


def test_format_spec_is_the_docs_watch_exemption() -> None:
    spec = spec_by_adapter_id("wasm")
    assert spec is FORMAT_SPECS["wasm"] and spec.upstream is False and spec.source_url == ""
    assert WasmAdapter().required_front_matter_keys() == spec.emitted_front_matter_keys == T.FRONT_MATTER_KEYS


def test_vocabulary_constants_match_the_runtime() -> None:
    from agentteams.frameworks._wasm_tool_scoping import WASM_TOOLS

    assert WASM_TOOLS == AT.known_tools()
    assert T.PROVIDER_NAMES == tuple(MP.PROVIDERS) and T.TIERS == MP.TIERS
    schema = json.loads((REPO / "agentteams" / "schemas" / "project-description.schema.json").read_text())
    assert tuple(schema["properties"]["wasm_runtime"]["properties"]["provider"]["enum"]) == tuple(MP.PROVIDERS)


def test_every_framework_table_names_wasm_or_excludes_it() -> None:
    """Plan R-2: the per-framework tables that must decide about ``wasm`` (include or a deliberate exclude)."""
    from agentteams import control_plane_io, output_plan
    from agentteams.cli.frozen_surfaces import FROZEN_SURFACE_PATHS
    from agentteams.confinement import PROTECTED_REL
    from agentteams.frameworks._sandbox_emit import TEAM_KEY_BY_FRAMEWORK

    assert FROZEN_SURFACE_PATHS["wasm"] == (".agentteams-wasm/",)
    assert ".agentteams-wasm" in PROTECTED_REL
    assert "wasm" not in TEAM_KEY_BY_FRAMEWORK                  # not a vendor session: no session team key
    assert not control_plane_io.stubs_enabled("wasm", {"enforce_decision_signing": True})
    assert '"wasm"' not in Path(output_plan.__file__).read_text()  # no team-builder meta-agent in v1


# --- agent files --------------------------------------------------------------------------------------------------

def test_agent_front_matter_is_exact_and_strict() -> None:
    out = WasmAdapter().render_agent_file(_AGENT.format(tools="['read', 'search', 'execute', 'agent']"),
                                          "navigator", dict(_TEAM))
    parsed = T.parse_agent_file("navigator", out)
    assert parsed.name == "Navigator — Demo" and parsed.description == "Finds files"
    assert parsed.tools == ("run_code", "delegate")              # compute-only: no read tools
    assert parsed.model == "cheap"                               # model_routing tier, never the Copilot label
    assert parsed.delegates == ("orchestrator", "security")      # team members only, never itself
    assert "Claude Opus" not in out and "handoffs" not in out and "## Handoffs" not in out
    assert ".agentteams-wasm/agents" in parsed.body and ".github/agents" not in parsed.body


def test_no_delegates_without_the_agent_scope() -> None:
    out = WasmAdapter().render_agent_file(_AGENT.format(tools="['read', 'execute']"), "navigator", dict(_TEAM))
    assert T.parse_agent_file("navigator", out).delegates == ()


def test_notices_are_recorded_on_the_manifest(capsys: pytest.CaptureFixture[str]) -> None:
    manifest = dict(_TEAM)
    WasmAdapter().render_agent_file(_AGENT.format(tools="['read', 'todo']"), "navigator", manifest)
    assert manifest["_wasm_tool_notices"] and "todo" in manifest["_wasm_tool_notices"][0]
    assert "[WASM-SCOPE] navigator: dropped" in capsys.readouterr().err


def test_instructions_are_neutral_and_noticed() -> None:
    out = WasmAdapter().render_instructions_file("---\nx: y\n---\n# P — Copilot Instructions\n\n.github/agents\n", {})
    assert out.startswith("<!-- TEAM.md") and "Copilot" not in out and ".agentteams-wasm/agents" in out


def test_extra_files_carry_grants_runtime_readme_and_the_neutral_baseline() -> None:
    a = WasmAdapter()
    manifest = {**_TEAM, "privilege_profile": "confined"}
    files = dict(a.extra_output_files(manifest))
    assert {"../grants.json", "../runtime.json", "../README.md"} <= set(files)
    for path, _ in FrameworkAdapter.extra_output_files(a, manifest):
        assert path in files                                     # the neutral launcher still ships
    runtime = json.loads(files["../runtime.json"])
    assert runtime["models"] == {"primary": None, "cheap": None} and runtime["provider"] is None
    assert set(json.loads(files["../grants.json"])["agents"]) == set(_TEAM["agent_slug_list"])


# --- a real render --------------------------------------------------------------------------------------------------

def test_render_layout(rendered: Path) -> None:
    files = team_files(rendered)
    for rel in T.TEAM_FILES_REL:
        assert rel in files
    agents = [r for r in files if r.startswith(T.AGENTS_REL + "/") and r.count("/") == 2 and r.endswith(".md")]
    assert f"{T.AGENTS_REL}/orchestrator.md" in agents and f"{T.AGENTS_REL}/team-builder.md" not in agents
    for rel in agents:
        if rel.endswith("SETUP-REQUIRED.md"):
            continue
        keys = [ln.split(":", 1)[0] for ln in files[rel].decode().split("\n")[1:6]]
        assert tuple(keys) == T.FRONT_MATTER_KEYS, rel
    assert (rendered / T.LOCK_REL).is_file()
    assert json.loads(files[T.RUNTIME_REL])["provider"] == "stub"
    entry = json.loads(files[T.GRANTS_REL])["agents"]["primary-producer"]
    assert [m["guest"] for m in entry["mounts"]] == ["/project/docs", "/scratch"]


def test_rerender_is_byte_identical(rendered: Path, tmp_path: Path) -> None:
    other = project(tmp_path, "other")
    assert render(other, write_brief(other)) == 0
    assert team_files(other) == team_files(rendered)


def test_rendered_files_hold_no_secrets_paths_or_model_ids(rendered: Path) -> None:
    from agentteams.vulnscan.gateway_dlp import Filter

    dlp = Filter()
    for rel, data in team_files(rendered).items():
        text = data.decode("utf-8")
        assert not dlp.scan_block(text), rel
        assert str(rendered) not in text, rel
        assert not re.search(r"\b(claude-[a-z0-9.-]*\d|gpt-\d|gemini-\d|grok-\d)", text), rel


def test_no_model_id_literals_in_the_adapter_modules() -> None:
    for rel in ("agentteams/frameworks/wasm.py", "agentteams/frameworks/_wasm_tool_scoping.py",
                "agentteams/frameworks/_wasm_team_docs.py", "agentteams/wasm_agent/team.py",
                "agentteams/cli/wasm_agent_commands.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert not re.search(r"\b(claude-[a-z0-9.-]*\d|gpt-\d|gemini-\d|grok-\d|o\d-mini)", text), rel


def test_example_brief_is_outside_the_byte_identity_globs() -> None:
    assert WASM_BRIEF.name != "brief.json" and WASM_BRIEF.is_file()
