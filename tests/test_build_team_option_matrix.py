"""Systematic option-combination tests for build_team.py."""

from __future__ import annotations

from pathlib import Path

import pytest

import build_team
from agentteams.emit import EmitResult


def _build_min_manifest(framework: str) -> dict:
    """Return a minimal manifest sufficient for build_team.main() test harnesses."""
    return {
        "project_name": "MatrixProject",
        "project_type": "software",
        "framework": framework,
        "selected_archetypes": ["primary-producer"],
        "components": [],
        "agent_slug_list": ["orchestrator"],
        "auto_resolved_placeholders": {},
        "manual_required_placeholders": [],
        "output_files": [
            {
                "path": "orchestrator.agent.md",
                "template": "universal/orchestrator.template.md",
                "type": "agent",
                "component_slug": None,
            },
            {
                "path": "team-builder.agent.md",
                "template": "builder/team-builder-copilot-vscode.template.md",
                "type": "builder",
                "component_slug": None,
            },
            {
                "path": "../copilot-instructions.md",
                "template": "copilot-instructions.template.md",
                "type": "instructions",
                "component_slug": None,
            },
            {
                "path": "SETUP-REQUIRED.md",
                "template": "",
                "type": "setup-required",
                "component_slug": None,
            },
        ],
        "tools": [],
    }


def _stub_core_pipeline(monkeypatch: pytest.MonkeyPatch, framework: str, captured: dict) -> None:
    """Patch build_team dependencies so tests can exercise option logic deterministically."""

    def _fake_load(_desc_path, scan_project=True):
        return {
            "project_name": "MatrixProject",
            "project_goal": "Exercise option matrix",
            "existing_project_path": str(Path.cwd()),
            "components": [],
            "tools": [],
            "deliverables": [],
        }

    monkeypatch.setattr(build_team.ingest, "load", _fake_load)
    monkeypatch.setattr(build_team.ingest, "validate", lambda _desc: [])
    monkeypatch.setattr(build_team.analyze, "build_manifest", lambda _desc, framework=framework: _build_min_manifest(framework))
    monkeypatch.setattr(build_team.render, "render_all", lambda _manifest, templates_dir: [
        ("orchestrator.agent.md", "# Orchestrator\n"),
        ("team-builder.agent.md", "# Team Builder\n"),
        ("../copilot-instructions.md", "# Instructions\n"),
        ("SETUP-REQUIRED.md", "# Setup\n"),
    ])
    monkeypatch.setattr(build_team.render, "compute_template_hashes", lambda _manifest, templates_dir: {})
    monkeypatch.setattr(build_team.render, "validate_cross_refs", lambda _rendered: [])

    from agentteams import graph as _graph
    monkeypatch.setattr(_graph, "generate_graph_document", lambda file_map, project_name: "# Graph\n")

    from agentteams import security_refs as _security_refs
    monkeypatch.setattr(_security_refs, "build_security_placeholders", lambda **kwargs: {})

    from agentteams import liaison_logs as _liaison_logs
    monkeypatch.setattr(_liaison_logs, "migrate_inline_logs", lambda *_a, **_k: type("R", (), {"rows_moved": 0, "changelog_rows_moved": 0, "coord_log_rows_moved": 0})())
    monkeypatch.setattr(_liaison_logs, "init_csv_stubs", lambda _p: [])

    def _fake_emit_all(rendered_files, **kwargs):
        captured["rendered_files"] = list(rendered_files)
        captured["emit_kwargs"] = kwargs
        return EmitResult(written=[], merged=[], skipped=[], errors=[], dry_run=kwargs.get("dry_run", False))

    monkeypatch.setattr(build_team.emit, "emit_all", _fake_emit_all)
    monkeypatch.setattr(build_team.emit, "print_summary", lambda *_a, **_k: None)


@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "claude"])
@pytest.mark.parametrize(
    "mode_flags",
    [
        [],
        ["--overwrite"],
        ["--merge"],
        ["--update"],
        ["--update", "--merge"],
        ["--update", "--prune"],
        ["--check"],
        ["--scan-security"],
        ["--post-audit"],
        ["--post-audit", "--auto-correct"],
        ["--enrich"],
    ],
)
def test_parser_accepts_framework_mode_matrix(framework: str, mode_flags: list[str]):
    parser = build_team._build_parser()
    args = parser.parse_args(["--description", "brief.json", "--framework", framework, *mode_flags])
    assert args.framework == framework


def test_parser_rejects_mutually_exclusive_overwrite_and_merge():
    parser = build_team._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--description", "brief.json", "--overwrite", "--merge"])


@pytest.mark.parametrize(
    "mode_flags, expected_overwrite, expected_merge",
    [
        ([], False, False),
        (["--overwrite"], True, False),
        (["--merge"], False, True),
    ],
)
def test_main_propagates_write_mode_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode_flags: list[str],
    expected_overwrite: bool,
    expected_merge: bool,
):
    captured: dict = {}
    _stub_core_pipeline(monkeypatch, "copilot-vscode", captured)

    rc = build_team.main([
        "--description",
        "brief.json",
        "--framework",
        "copilot-vscode",
        "--output",
        str(tmp_path / ".github" / "agents"),
        "--dry-run",
        "--security-offline",
        *mode_flags,
    ])

    assert rc == 0
    assert captured["emit_kwargs"]["overwrite"] == expected_overwrite
    assert captured["emit_kwargs"]["merge"] == expected_merge


@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "claude"])
def test_main_finalizes_framework_specific_output_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    framework: str,
):
    captured: dict = {}
    _stub_core_pipeline(monkeypatch, framework, captured)

    rc = build_team.main([
        "--description",
        "brief.json",
        "--framework",
        framework,
        "--output",
        str(tmp_path / ".github" / "agents"),
        "--dry-run",
        "--security-offline",
    ])

    assert rc == 0
    final_paths = [p for p, _ in captured["rendered_files"]]

    if framework == "copilot-vscode":
        assert "orchestrator.agent.md" in final_paths
        assert "team-builder.agent.md" in final_paths
        assert "../copilot-instructions.md" in final_paths
    elif framework == "copilot-cli":
        assert "orchestrator.md" in final_paths
        assert "team-builder.md" in final_paths
        assert "../copilot-instructions.md" in final_paths
    else:
        assert "orchestrator.md" in final_paths
        assert "team-builder.md" in final_paths
        assert "../CLAUDE.md" in final_paths


def test_main_post_audit_implicitly_enables_enrich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}
    _stub_core_pipeline(monkeypatch, "copilot-vscode", captured)

    from agentteams import enrich as _enrich

    enrich_called = {"scan_defaults": 0}

    monkeypatch.setattr(_enrich, "scan_defaults", lambda *a, **k: enrich_called.__setitem__("scan_defaults", enrich_called["scan_defaults"] + 1) or [])
    monkeypatch.setattr(_enrich, "auto_enrich", lambda findings, file_map, manifest, project_path=None: (file_map, findings))
    monkeypatch.setattr(_enrich.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        _enrich,
        "export_csv",
        lambda findings, out_path: (out_path.parent.mkdir(parents=True, exist_ok=True), out_path.write_text("placeholder,status\n", encoding="utf-8")),
    )
    monkeypatch.setattr(_enrich, "generate_setup_required", lambda findings, manifest: "# Setup Required\n")
    monkeypatch.setattr(_enrich, "print_enrich_summary", lambda findings: None)

    rc = build_team.main([
        "--description",
        "brief.json",
        "--framework",
        "copilot-vscode",
        "--output",
        str(tmp_path / ".github" / "agents"),
        "--dry-run",
        "--post-audit",
        "--security-offline",
    ])

    assert rc == 0
    assert enrich_called["scan_defaults"] == 1


@pytest.mark.parametrize(
    "argv,error_substring",
    [
        (["--convert-from", "src", "--description", "brief.json"], "cannot be used with --convert-from"),
        (["--convert-from", "src", "--update"], "cannot be used with --convert-from"),
        (["--interop-from", "src", "--description", "brief.json"], "cannot be used with --interop-from"),
        (["--interop-from", "src", "--update"], "cannot be used with --interop-from"),
        (["--bridge-from", "src", "--description", "brief.json"], "cannot be used with --bridge-from"),
        (["--bridge-from", "src", "--update"], "cannot be used with --bridge-from"),
        (["--convert-from", "src", "--interop-from", "src2"], "mutually exclusive"),
        (["--bridge-from", "src", "--convert-from", "src2"], "mutually exclusive"),
        (["--bridge-from", "src", "--interop-from", "src2"], "mutually exclusive"),
        (["--bridge-check"], "requires --bridge-from"),
        (["--bridge-refresh"], "requires --bridge-from"),
        (["--bridge-from", "src", "--bridge-check", "--bridge-refresh"], "cannot be combined"),
        (["--auto-correct", "--description", "brief.json"], "requires --post-audit"),
        (["--prune", "--description", "brief.json"], "can only be used with --update"),
    ],
)
def test_main_rejects_incompatible_option_pairs_stderr(
    argv: list[str],
    error_substring: str,
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit):
        build_team.main(argv)
    captured = capsys.readouterr()
    assert error_substring in captured.err


@pytest.mark.parametrize("interop_mode", ["direct", "bundle"])
def test_main_accepts_interop_pipeline_modes(tmp_path: Path, interop_mode: str):
    source_dir = tmp_path / "src" / ".github" / "agents"
    source_dir.mkdir(parents=True)
    (source_dir / "orchestrator.agent.md").write_text(
        "---\n"
        "name: orchestrator\n"
        "description: \"d\"\n"
        "user-invokable: false\n"
        "tools: ['read']\n"
        "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        "---\n\n"
        "# orchestrator\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    (source_dir.parent / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")

    output_dir = tmp_path / "dst" / ".claude" / "agents"
    rc = build_team.main(
        [
            "--interop-from",
            str(source_dir),
            "--framework",
            "claude",
            "--interop-mode",
            interop_mode,
            "--output",
            str(output_dir),
        ]
    )

    assert rc == 0
    assert (output_dir / "orchestrator.md").exists()
    if interop_mode == "bundle":
        assert (
            output_dir
            / "references"
            / "interop"
            / "copilot-vscode-to-claude"
            / "interop-manifest.json"
        ).exists()


@pytest.mark.parametrize("bridge_check", [False, True])
def test_main_accepts_bridge_pipeline(tmp_path: Path, bridge_check: bool):
    source_dir = tmp_path / "src" / ".github" / "agents"
    source_dir.mkdir(parents=True)
    (source_dir / "orchestrator.agent.md").write_text(
        "---\n"
        "name: orchestrator\n"
        "description: \"d\"\n"
        "user-invokable: true\n"
        "tools: ['read']\n"
        "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        "---\n\n"
        "# orchestrator\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    (source_dir.parent / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")

    output_root = tmp_path / "out"
    args = [
        "--bridge-from",
        str(source_dir),
        "--framework",
        "claude",
        "--output",
        str(output_root),
    ]
    if bridge_check:
        # Need generated artifacts first for meaningful bridge check.
        rc_first = build_team.main(args)
        assert rc_first == 0
        args.append("--bridge-check")

    rc = build_team.main(args)
    assert rc == 0
    if bridge_check:
        assert (
            output_root
            / "references"
            / "bridges"
            / "copilot-vscode-to-claude"
            / "bridge-check.report.md"
        ).exists()
    else:
        assert (
            output_root
            / "references"
            / "bridges"
            / "copilot-vscode-to-claude"
            / "bridge-manifest.json"
        ).exists()
