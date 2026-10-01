"""The ``origin: "interop"`` team marker of a projected team (2026-10-01).

Plan: ``tmp/by-week/2026-W40/self-updating-agents-and-codex-marker.plan.md`` section B, with the
binding "Codex marker" conditions (security 12-14). No interop path used to write
``references/build-log.json``, so a projected ``.codex/agents`` team was invisible to fleet, the
freshness scan, ``--check``, the Claude present-sibling denies and the launcher.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import control_plane_io, drift, fleet, framework_freshness, projection_marker
from agentteams.frameworks._sandbox_emit import (
    CONTROL_PLANE_STUB_TEXT,
    VERIFY_KEY_STORE_SENTINEL_REL,
    VERIFY_KEY_STORE_SENTINEL_TEXT,
    _build_sandbox_block,
    present_sibling_deny_dirs,
)
from agentteams.interop import run_interop

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
_linux_bwrap = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and shutil.which("bwrap")),
    reason="confine-run's Linux branch needs bwrap on PATH")

_AGENT = (
    "---\nname: Orchestrator\ndescription: \"demo\"\nuser-invocable: true\ntools: ['read']\n---\n\n"
    "# Orchestrator\n\nBody.\n"
)


def _source(root: Path, *, build_log: dict | None = None, switch: bool | None = None) -> Path:
    src = root / ".github" / "agents"
    src.mkdir(parents=True)
    (src / "orchestrator.agent.md").write_text(_AGENT, encoding="utf-8")
    (root / ".github" / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")
    if build_log is not None or switch is not None:
        (src / "references").mkdir()
    if build_log is not None:
        (src / "references" / "build-log.json").write_text(json.dumps(build_log), encoding="utf-8")
    if switch is not None:
        (src / "references" / "agent-privilege.json").write_text(
            json.dumps({"enforce_decision_signing": switch}), encoding="utf-8")
    return src


def _project(tmp_path: Path, **kw) -> tuple[Path, Path, Path]:
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    src = _source(root, **kw)
    return root, src, root / ".codex" / "agents"


def _marker(agents: Path) -> dict:
    return json.loads((agents / "references" / "build-log.json").read_text(encoding="utf-8"))


_CONTROL_RELS = ("references/agent-privilege.json", VERIFY_KEY_STORE_SENTINEL_REL,
                 *(f"references/{n}" for n in CONTROL_PLANE_STUB_TEXT))


# --- writing the marker -------------------------------------------------------------------------

def test_interop_into_codex_writes_control_plane_then_marker(tmp_path, monkeypatch):
    root, src, agents = _project(
        tmp_path, build_log={"project_name": "Demo", "agent_slug_list": ["orchestrator"]}, switch=True)
    seen: list[bool] = []
    original = projection_marker._write_marker_file

    def spy(refs, text):
        seen.append(all((agents / rel).is_file() for rel in _CONTROL_RELS))
        return original(refs, text)

    monkeypatch.setattr(projection_marker, "_write_marker_file", spy)
    result = run_interop(src, "codex", agents, source_framework="copilot-vscode")
    assert result.success, result.errors
    assert seen == [True], "the marker must be written after the whole control plane"
    assert result.marker_files[-1] == str(agents / "references" / "build-log.json")
    assert [Path(p) for p in result.marker_files[:-1]] == [agents / r for r in _CONTROL_RELS]
    log = _marker(agents)
    assert log["origin"] == "interop" and log["template_hashes"] == {}
    assert log["framework"] == "codex" and log["schema_version"] == "1.5"
    assert log["project_name"] == "Demo" and log["agent_slug_list"] == ["orchestrator"]
    assert log["source_dir"] == ".github/agents" and log["source_framework"] == "copilot-vscode"
    assert len(log["source_build_log_sha256"]) == 64
    assert ".codex/agents/orchestrator.toml" in log["files_written"]
    assert set(log["file_hashes"]) >= {"orchestrator.toml"}
    assert (agents / VERIFY_KEY_STORE_SENTINEL_REL).read_text() == VERIFY_KEY_STORE_SENTINEL_TEXT
    assert json.loads((agents / "references/agent-privilege.json").read_text())[
        "enforce_decision_signing"] is True
    assert any(".codex is now a recognised agentteams team" in n for n in result.notices)
    # --verify-integrity reads the marker's file_hashes in the native format
    assert {e["status"] for e in drift.verify_output_integrity(agents)} == {"OK"}


def test_absent_source_switch_projects_false(tmp_path):
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    assert json.loads((agents / "references/agent-privilege.json").read_text())[
        "enforce_decision_signing"] is False
    assert _marker(agents)["source_build_log_sha256"] is None


def test_no_marker_when_a_stub_write_fails(tmp_path, monkeypatch):
    root, src, agents = _project(tmp_path)

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(control_plane_io, "write_control_plane_stubs", boom)
    result = run_interop(src, "codex", agents)
    assert not result.success
    assert any("NO team marker" in e and "read-only" in e for e in result.errors)
    assert not (agents / "references" / "build-log.json").exists()
    assert result.marker_files == []


def test_never_clobbers_a_native_build_log(tmp_path):
    root, src, agents = _project(tmp_path)
    (agents / "references").mkdir(parents=True)
    native = json.dumps({"framework": "codex", "template_hashes": {"t": "abc"}})
    (agents / "references" / "build-log.json").write_text(native, encoding="utf-8")
    result = run_interop(src, "codex", agents, overwrite=True)
    assert result.success and result.marker_files == []
    assert (agents / "references" / "build-log.json").read_text() == native
    assert not (agents / "references" / "agent-privilege.json").exists()  # nothing written


def test_an_existing_interop_marker_is_refreshed(tmp_path):
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    first = _marker(agents)
    result = run_interop(src, "codex", agents, overwrite=True)
    # unchanged projection: the committed marker is kept byte-identical (no timestamp churn)
    assert result.success and _marker(agents) == first
    assert not any("recognised agentteams team" in n for n in result.notices)  # first time only
    # a real change refreshes it
    next(src.glob("*.agent.md")).write_text(next(src.glob("*.agent.md")).read_text() + "\nchanged\n")
    result = run_interop(src, "codex", agents, overwrite=True)
    assert result.success and result.marker_files and _marker(agents)["file_hashes"] != first["file_hashes"]


def test_symlinked_references_dir_is_refused(tmp_path):
    root, src, agents = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    agents.mkdir(parents=True)
    (agents / "references").symlink_to(elsewhere, target_is_directory=True)
    result = run_interop(src, "codex", agents)
    assert not result.success and any("symlink" in e for e in result.errors)
    assert list(elsewhere.iterdir()) == []


def test_symlinked_marker_is_never_followed(tmp_path):
    root, src, agents = _project(tmp_path)
    (agents / "references").mkdir(parents=True)
    target = tmp_path / "victim.json"
    target.write_text("{}", encoding="utf-8")
    (agents / "references" / "build-log.json").symlink_to(target)
    run_interop(src, "codex", agents)
    assert target.read_text() == "{}"
    assert (agents / "references" / "build-log.json").is_symlink()


def test_dry_run_and_skills_only_write_no_marker(tmp_path):
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents, dry_run=True).marker_files == []
    assert not (root / ".codex").exists()
    claude = root / ".claude" / "agents"
    claude.mkdir(parents=True)
    (claude / "orchestrator.md").write_text("---\nname: O\ndescription: d\n---\n\n# O\n", encoding="utf-8")
    skill = root / ".claude" / "skills" / "recall"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: recall\ndescription: d\n---\n\nRun.\n", encoding="utf-8")
    result = run_interop(claude, "codex", agents, skills_only=True)
    assert result.marker_files == [] and not (root / ".codex").exists()


def test_claude_target_gets_no_marker(tmp_path):
    root, src, _ = _project(tmp_path)
    claude = root / ".claude" / "agents"
    assert run_interop(src, "claude", claude).marker_files == []
    assert not (claude / "references" / "build-log.json").exists()


def test_multi_sync_projection_writes_the_marker(tmp_path):
    from agentteams import multi_sync

    root, src, agents = _project(tmp_path)
    multi_sync.sync_init(root, pin="copilot-vscode", frameworks=["copilot-vscode", "codex"])
    log = _marker(agents)
    assert log["origin"] == "interop" and log["source_framework"] == "canonical"
    assert all((agents / rel).is_file() for rel in _CONTROL_RELS)


# --- the marker authorises nothing (cond 13) ----------------------------------------------------

def test_freshness_and_drift_report_unverifiable(tmp_path, capsys):
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    report = framework_freshness.scan(root, REPO / "agentteams" / "templates")
    (render,) = report.renders
    assert render.interop and render.unverifiable and not render.is_stale
    assert report.freshest is None  # never "the freshest current render"
    framework_freshness.print_report(report, project_root=root)
    out = capsys.readouterr().out
    assert drift.INTEROP_UNVERIFIABLE in out and "All renders are current" not in out
    dreport = drift.detect_drift(agents, REPO / "agentteams" / "templates")
    assert dreport.unverifiable == drift.INTEROP_UNVERIFIABLE and not dreport.changed_templates
    drift.print_drift_report(dreport)
    out = capsys.readouterr().out
    assert drift.INTEROP_UNVERIFIABLE in out and "No drift detected" not in out


def test_check_fails_on_an_interop_marker(tmp_path, capsys):
    from agentteams.cli.generate_helpers import _handle_check
    from agentteams.frameworks.registry import FRAMEWORKS

    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    rc = _handle_check(argparse.Namespace(strict_prompt_roots=False), agents, {},
                       FRAMEWORKS["codex"](), "Demo")
    assert rc == 1
    out = capsys.readouterr().out
    assert drift.INTEROP_UNVERIFIABLE in out and "unverifiable (interop)" in out


def test_marker_never_vouches_for_an_overwrite(tmp_path):
    from agentteams import emit
    from agentteams.cli.output_target import _looks_agentteams_generated

    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    assert emit._unmodified_since_build(agents) == frozenset()
    assert drift.native_baseline(_marker(agents), agents) == {}
    bare = tmp_path / "bare"
    (bare / "references").mkdir(parents=True)
    shutil.copy(agents / "references" / "build-log.json", bare / "references" / "build-log.json")
    assert _looks_agentteams_generated(bare) is False


# --- recognition: fleet, bridge, Claude siblings, launcher --------------------------------------

def test_fleet_discovers_codex_and_hints_an_interop_refresh(tmp_path):
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    shutil.rmtree(root / ".github")  # only the Codex team qualifies the workspace now
    assert fleet.discover_workspaces(tmp_path, "all") == [root]
    assert fleet.discover_workspaces(tmp_path, "both") == []
    assert fleet._plan_targets(root, "all") == ["codex-interop"]
    hint = fleet._codex_interop_hint(root)
    assert "--interop-from .github/agents --framework codex --output . --overwrite" in hint
    assert ".codex/agents" in fleet._agent_paths(root)


def test_fleet_native_codex_team_gets_a_native_update(tmp_path):
    root = tmp_path / "ws"
    refs = root / ".codex" / "agents" / "references"
    refs.mkdir(parents=True)
    (refs / "build-log.json").write_text(json.dumps({"framework": "codex"}), encoding="utf-8")
    assert fleet._plan_targets(root, "all") == ["codex-direct"]
    argv = fleet._target_argv("codex-direct", root, root / "brief.json", False, "preserve")
    assert argv[argv.index("--framework") + 1] == "codex" and "--update" in argv


def test_bridge_notice_names_the_interop_refresh(tmp_path):
    from agentteams import bridge

    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    notice = bridge.native_team_notice(output_root=root, target_framework="codex",
                                       source_dir=root / ".agentteams" / "canonical")
    assert "INTEROP-PROJECTED" in notice and "--interop-from .github/agents" in notice


def test_codex_becomes_a_present_sibling_and_the_advisory_fires(tmp_path, capsys):
    """Cond 14: the Claude block then denies .codex whole, so in-session interop into it fails."""
    from agentteams.cli.generate_helpers import _advise_codex_projection_outside_session

    root, src, agents = _project(tmp_path)
    assert present_sibling_deny_dirs(root) == ()
    assert run_interop(src, "codex", agents).success
    present = present_sibling_deny_dirs(root)
    assert present == (".codex",)
    block = _build_sandbox_block(None, platform="linux", sibling_deny_dirs=present)
    assert ".codex" in block["filesystem"]["denyWrite"]
    manifest = {"framework": "claude", "host_features": ["claude:sandbox"]}
    assert _advise_codex_projection_outside_session(manifest, root, present) is True
    assert "outside any Claude session" in capsys.readouterr().err
    assert _advise_codex_projection_outside_session({"framework": "codex"}, root, present) is False


def _launcher_check(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(LAUNCHER), "--scratch", str(project), "--check", "--", "true"],
                          capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})


@_linux_bwrap
def test_launcher_accepts_an_interop_codex_team_and_requires_its_store(tmp_path):
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    r = _launcher_check(root)
    assert r.returncode == 0, r.stderr
    assert str(agents / "references" / "authorized-verify-keys") in r.stdout
    shutil.rmtree(agents / "references" / "authorized-verify-keys")
    r = _launcher_check(root)
    assert r.returncode == 2 and "missing although" in r.stderr, r.stderr
    assert "--interop-from <source> --framework codex --output . --overwrite" in r.stderr


# --- @security conditions on d1830d0 (C1-C5) ----------------------------------------------------

def _switch(agents: Path) -> object:
    return json.loads((agents / "references/agent-privilege.json").read_text())["enforce_decision_signing"]


@pytest.mark.parametrize("where", ["source", "target"])
def test_c1_signing_governed_team_always_projects_true(tmp_path, where):
    root, src, agents = _project(tmp_path, switch=False)
    gov = src if where == "source" else agents
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "signing-governed.marker").write_text("governed\n", encoding="utf-8")
    assert run_interop(src, "codex", agents).success
    assert _switch(agents) is True


def test_c1_governed_target_with_a_false_switch_is_refused(tmp_path):
    root, src, agents = _project(tmp_path)
    (agents / "references").mkdir(parents=True)
    (agents / "signing-governed.marker").write_text("governed\n", encoding="utf-8")
    (agents / "references/agent-privilege.json").write_text(
        json.dumps({"enforce_decision_signing": False}), encoding="utf-8")
    result = run_interop(src, "codex", agents)
    assert not result.success and any("signing-governed" in e for e in result.errors)
    assert not (agents / "references/build-log.json").exists()


@pytest.mark.parametrize("kind", ["malformed", "non-bool", "symlink", "directory"])
def test_c2_unusable_source_switch_refuses_switch_and_marker(tmp_path, kind):
    root, src, agents = _project(tmp_path)
    sw = src / "references" / "agent-privilege.json"
    sw.parent.mkdir(parents=True)
    if kind == "malformed":
        sw.write_text("{not json", encoding="utf-8")
    elif kind == "non-bool":
        sw.write_text(json.dumps({"enforce_decision_signing": "yes"}), encoding="utf-8")
    elif kind == "symlink":
        real = tmp_path / "real.json"
        real.write_text(json.dumps({"enforce_decision_signing": True}), encoding="utf-8")
        sw.symlink_to(real)
    else:
        sw.mkdir()
    result = run_interop(src, "codex", agents)
    assert not result.success and any("switch" in e for e in result.errors)
    assert not (agents / "references/agent-privilege.json").exists()
    assert not (agents / "references/build-log.json").exists()


def test_c3_references_swapped_for_a_link_before_the_marker_is_refused(tmp_path, monkeypatch):
    root, src, agents = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    original = projection_marker._control_plane_hole

    def swap(agents_dir):
        hole = original(agents_dir)
        refs = agents_dir / "references"
        shutil.move(str(refs), str(tmp_path / "moved"))
        refs.symlink_to(elsewhere, target_is_directory=True)
        return hole

    monkeypatch.setattr(projection_marker, "_control_plane_hole", swap)
    result = run_interop(src, "codex", agents)
    assert not result.success and any("not a real directory" in e for e in result.errors)
    assert list(elsewhere.iterdir()) == []


def test_c4_source_hint_only_names_a_known_dir_and_quotes_it(tmp_path):
    from agentteams import bridge

    assert projection_marker.safe_source_hint(".github/agents") == ".github/agents"
    for evil in ("x; rm -rf ~", "/abs/.github/agents", "../.github/agents", 3, None,
                 ".agentteams/canonical"):
        assert projection_marker.safe_source_hint(evil) == "<source>"
    root, src, agents = _project(tmp_path)
    assert run_interop(src, "codex", agents).success
    log = _marker(agents)
    log["source_dir"] = "$(touch /tmp/pwned)"
    (agents / "references/build-log.json").write_text(json.dumps(log), encoding="utf-8")
    hint = fleet._codex_interop_hint(root)
    notice = bridge.native_team_notice(output_root=root, target_framework="codex",
                                       source_dir=root / ".agentteams" / "canonical")
    for text in (hint, notice):
        assert "pwned" not in text and "--interop-from <source>" in text


def test_c5_a_native_build_log_appearing_before_the_replace_is_kept(tmp_path, monkeypatch):
    root, src, agents = _project(tmp_path)
    native = json.dumps({"framework": "codex", "template_hashes": {"t": "x"}})
    original = projection_marker._origin_at

    def race(dir_fd):
        (agents / "references" / "build-log.json").write_text(native, encoding="utf-8")
        return original(dir_fd)

    monkeypatch.setattr(projection_marker, "_origin_at", race)
    result = run_interop(src, "codex", agents)
    assert not result.success and any("native build-log" in e for e in result.errors)
    assert (agents / "references" / "build-log.json").read_text() == native
    assert not [p for p in (agents / "references").iterdir() if p.name.endswith(".tmp")]


def test_marker_is_stable_across_reprojection_and_location(tmp_path):
    """researchteam render-consistency: a temp-dir re-projection must equal the committed marker
    apart from generated_at, and an unchanged in-place re-run must not rewrite it."""
    import json as _json

    from agentteams import projection_marker as pm

    def project(root):
        src = root / ".github" / "agents"
        (src / "references").mkdir(parents=True)
        (src / "references" / "build-log.json").write_text(_json.dumps({"project_name": "P", "agent_slug_list": ["a"]}))
        dst = root / ".codex" / "agents"
        dst.mkdir(parents=True)
        f = dst / "a.toml"
        f.write_text("x = 1\n")
        res = pm.write_projection_marker(dst, "codex", source_dir=src, source_framework="copilot-vscode",
                                         files_written=[str(f)])
        assert not res.error, res.error
        return dst / "references" / "build-log.json"

    a = _json.loads(project(tmp_path / "repo").read_text())
    b = _json.loads(project(tmp_path / "tmpcopy").read_text())
    a.pop("generated_at"); b.pop("generated_at")
    assert a == b and a["source_dir"] == ".github/agents"
    assert "/" + str(tmp_path).strip("/") not in _json.dumps(a)
    marker = tmp_path / "repo" / ".codex" / "agents" / "references" / "build-log.json"
    before = marker.read_bytes()
    f = tmp_path / "repo" / ".codex" / "agents" / "a.toml"
    res = pm.write_projection_marker(f.parent, "codex", source_dir=tmp_path / "repo" / ".github" / "agents",
                                     source_framework="copilot-vscode", files_written=[str(f)])
    assert res.skipped and marker.read_bytes() == before
