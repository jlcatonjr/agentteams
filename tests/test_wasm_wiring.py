"""WASM plan P11: ``frameworks/_wasm_wiring`` — who gets ``agentteams_wasm``, where, and R1 protection of each file.

* WASM off: no record, no file, no front-matter change (byte-identical renders).
* Claude: an execute-class agent gets its own copy (exact block, exact tool name); an auditor never does; the
  runner block stays first and byte-identical; every emitted file that launches the server is in
  ``protected_config_paths`` and therefore in the emitted session ``denyWrite`` (shell side) and an ``Edit`` deny.
* ``control_plane_advisory`` / ``verify_sandbox_wiring`` report "not protected" whenever a launching config on disk is
  missing from the live denyWrite, whatever the manifest lists (@security, P10 review).
* ``_wasm_wiring`` imports no P13/P14 module (dependency direction P01 -> P11 -> P13/P14).
"""

from __future__ import annotations

import ast
import json
import posixpath
from pathlib import Path

import pytest

from agentteams import analyze, wasm_mcp
from agentteams.frameworks import _wasm_wiring as ww
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.sandbox_state import effective_sandbox_state
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
SHELL = "---\nname: Nav\ndescription: \"d\"\ntools: ['read', 'search', 'execute']\n---\n\nbody\n"
AUDITOR = "---\nname: Sec\ndescription: \"d\"\ntools: ['read', 'search']\n---\n\nbody\n"


def _manifest(framework: str, *, wasm: bool = True, **brief: object) -> dict:
    manifest = analyze.build_manifest({**BRIEF, **brief}, framework=framework)
    if wasm:
        manifest[WASM_SANDBOX_CLI_KEY] = "on"
    return manifest


def _render(framework: str, manifest: dict, content: str, slug: str) -> str:
    return FRAMEWORKS[framework]().render_agent_file(content, slug, manifest)


def test_wasm_off_is_inert_and_byte_identical(monkeypatch):
    manifest = _manifest("claude", wasm=False)
    adapter = FRAMEWORKS["claude"]()
    plain_agent = adapter.render_agent_file(SHELL, "navigator", manifest)
    plain_extras = adapter.extra_output_files(manifest)
    assert ww.WIRING_KEY not in manifest
    assert "agentteams_wasm" not in plain_agent and not any("wasm" in p for p, _ in plain_extras)
    # The same render with every P11 entry point stubbed out: identical bytes.
    monkeypatch.setattr(ww, "wasm_agent_entry", lambda *a, **k: None)
    monkeypatch.setattr(ww, "output_files", lambda *a, **k: [])
    assert adapter.render_agent_file(SHELL, "navigator", manifest) == plain_agent
    assert adapter.extra_output_files(manifest) == plain_extras
    for fw in FRAMEWORKS:
        assert ww.wiring_row(_manifest("claude", wasm=False), fw) is None


def test_claude_eligible_agent_gets_its_own_exact_copy_and_auditor_none():
    manifest = _manifest("claude")
    nav = _render("claude", manifest, SHELL, "navigator")
    args = wasm_mcp.agent_launch_args(effective_sandbox_state(manifest), "navigator")
    block = "mcpServers:\n" + wasm_mcp.claude_entry(args, wasm_mcp.interpreter())
    assert block.rstrip("\n") in nav
    assert "tools: Read, Grep, Glob, Bash, mcp__agentteams_wasm__run_wasm\n" in nav
    sec = _render("claude", manifest, AUDITOR, "security")
    assert "agentteams_wasm" not in sec and "mcpServers" not in sec
    row = manifest[ww.WIRING_KEY]["frameworks"]["claude"]
    assert row["agents"] == ["navigator"] and row["status"] == "wired-per-agent" and row["verified"]


def test_runner_block_stays_first_and_byte_identical(monkeypatch):
    from agentteams import runner_mcp
    from agentteams.frameworks import claude

    grant = {"tools": ["run_command"]} if "run_command" in runner_mcp.TOOLS else {"tools": [runner_mcp.TOOLS[0]]}
    monkeypatch.setattr(claude, "_runner_grant", lambda slug, manifest: grant)
    off = _render("claude", _manifest("claude", wasm=False), SHELL, "worker")
    runner = runner_mcp.claude_block("worker", grant["tools"], "staged", runner_mcp.interpreter())
    assert runner.rstrip("\n") in off and "agentteams_wasm" not in off
    on = _render("claude", _manifest("claude"), SHELL, "worker")
    head = on.split("\n---\n", 1)[0]
    assert head.index("agentteams_runner") < head.index("agentteams_wasm")
    assert runner in head + "\n"  # the runner entry is unchanged, the wasm entry follows it
    assert head.count("mcpServers:") == 1


def test_every_launching_file_is_protected_and_reaches_the_session_deny_write():
    manifest = _manifest("claude", privilege_profile="confined")
    adapter = FRAMEWORKS["claude"]()
    for slug, content in (("navigator", SHELL), ("security", AUDITOR), ("git-operations", SHELL)):
        adapter.render_agent_file(content, slug, manifest)
    extras = dict(adapter.extra_output_files(manifest))
    protected = manifest[ww.WIRING_KEY]["protected_config_paths"]
    assert {".claude/agents/navigator.md", ".claude/agents/git-operations.md", ww.CLAUDE_EXAMPLE} <= set(protected)
    assert ".claude/agents/security.md" not in protected
    # Every emitted file that launches the server, mapped to its project-relative path, is listed (Rule 12).
    launching = {posixpath.normpath(f".claude/agents/{p}") for p, text in extras.items()
                 if ww.LAUNCH_MARKER in text and not posixpath.normpath(f".claude/agents/{p}").startswith(".agentteams/")}
    assert launching and launching <= set(protected), launching - set(protected)
    data = json.loads(extras["../settings.hooks.example.json"])
    deny_write = data["sandbox"]["filesystem"]["denyWrite"]
    deny = data["permissions"]["deny"]
    for path in protected:
        assert path in deny_write, path  # shell side, not only the Edit tool
        assert f"Edit(/{path})" in deny


def _live_claude(root: Path, deny_write: list[str], deny: list[str]) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "sandbox": {"enabled": True, "filesystem": {"denyWrite": deny_write}},
        "permissions": {"deny": deny}}), encoding="utf-8")


def _launching_agent(root: Path, slug: str) -> str:
    folder = root / ".claude" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.md").write_text(f"---\nmcpServers:\n  - agentteams_wasm:\n      args: [\"{wasm_mcp.PROTECTED_PATH}\"]\n"
                                       "---\n", encoding="utf-8")
    return f".claude/agents/{slug}.md"


def test_launching_config_scan_finds_only_launching_files(tmp_path):
    rel = _launching_agent(tmp_path, "navigator")
    (tmp_path / ".claude" / "agents" / "prose.md").write_text("mentions mcp__agentteams_wasm__run_wasm only\n")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"args": [wasm_mcp.PROTECTED_PATH]}}}))
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text("{}")
    assert ww.launching_config_paths(tmp_path) == (rel, ".mcp.json")


def test_control_plane_advisory_reports_any_launching_file_missing_from_deny_write(tmp_path):
    from agentteams.frameworks import _control_plane_protect as cpp

    manifest = _manifest("claude", privilege_profile="confined")
    rel = _launching_agent(tmp_path, "navigator")
    control = cpp.control_plane_for(manifest)
    base_write = list(cpp.write_denies(False, control))
    base_rules = cpp.permission_rules(False, control)
    _live_claude(tmp_path, base_write, base_rules)  # R1's rules merged, but not the agent file in denyWrite
    advisory = wasm_mcp.control_plane_advisory(tmp_path, "claude", manifest)
    assert advisory and f"denyWrite {rel}" in advisory
    _live_claude(tmp_path, [*base_write, rel], [*base_rules, f"Edit(/{rel})"])
    assert wasm_mcp.control_plane_advisory(tmp_path, "claude", manifest) is None
    _live_claude(tmp_path, [*base_write, ".claude/agents"], base_rules)  # a denied ancestor covers it
    assert wasm_mcp.control_plane_advisory(tmp_path, "claude", manifest) is None


def test_goose_advisory_counts_launching_recipes(tmp_path, monkeypatch):
    from agentteams.frameworks import _control_plane_protect as cpp

    live = tmp_path.parent / f"{tmp_path.name}-goose-config.yaml"   # the profile wired in (GOOSE_SANDBOX live)
    live.write_text("GOOSE_SANDBOX: 1\n")
    monkeypatch.setenv("AGENTTEAMS_GOOSE_CONFIG", str(live))
    manifest = _manifest("goose", privilege_profile="confined")
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "nav.yaml").write_text(f"args: [\"{wasm_mcp.PROTECTED_PATH}\"]\n")
    denies = cpp.seatbelt_denies(False, cpp.control_plane_for(manifest))
    (tmp_path / ".goose" / "sandbox.sb").write_text(
        "(deny file-write*\n" + "\n".join(f'    (subpath "/r/{p}")' for p in denies) + ")\n")
    assert wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) is None  # under the denied recipes dir
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"args": [wasm_mcp.PROTECTED_PATH]}}}))
    assert ".mcp.json" in (wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) or "")


def test_verify_sandbox_wiring_without_a_manifest_checks_the_disk_paths(tmp_path, monkeypatch):
    from agentteams.frameworks import claude
    from agentteams.sandbox_state import SandboxState

    rel = _launching_agent(tmp_path, "navigator")
    state = SandboxState(os=True, os_reason="brief", wasm=True, wasm_reason="brief", wasm_languages=("python",))
    monkeypatch.setattr("agentteams.sandbox_state.verifier_sandbox_state", lambda *a, **k: state)
    monkeypatch.setattr(claude, "_verify_emitted_sandbox_merged", lambda root: (True, []))
    seen: list[tuple[str, ...]] = []

    def merged(root, extra=()):
        seen.append(tuple(extra))
        return False, [f"denyWrite {p}" for p in extra]

    monkeypatch.setattr(claude, "verify_control_plane_merged", merged)
    ok, msgs = claude.verify_sandbox_wiring(tmp_path, None)
    assert not ok and seen == [(rel,)] and "WASM CONTROL PLANE NOT MERGED" in msgs[-1]


def test_wasm_wiring_imports_no_p13_or_p14_module():
    tree = ast.parse((REPO / "agentteams" / "frameworks" / "_wasm_wiring.py").read_text(encoding="utf-8"))
    names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    allowed = {"__future__", "json", "os", "re", "stat", "sys", "tomllib", "collections.abc", "pathlib", "typing",
               "agentteams", "agentteams.wasm_config", "agentteams.sandbox_state",
               "agentteams.frameworks.goose_tool_scoping", "agentteams.frameworks._sandbox_emit"}
    assert names <= allowed, names - allowed


def test_manifest_record_validates_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "agentteams" / "schemas" / "team-manifest.schema.json").read_text(encoding="utf-8"))
    manifest = _manifest("claude")
    _render("claude", manifest, SHELL, "navigator")
    record = {"wasm_wiring": manifest[ww.WIRING_KEY]}
    jsonschema.validate(record, {**schema, "required": []})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"wasm_wiring": {**record["wasm_wiring"], "protected_config_paths": ["/abs"]}},
                            {**schema, "required": []})
