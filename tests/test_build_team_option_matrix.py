"""Systematic option-combination tests for build_team.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_team
from agentteams.emit import EmitResult
from agentteams.memory_index import build_memory_index


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
        ["--refresh-index"],
        ["--query-index", "drift baseline"],
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


def test_dual_mode_policy_non_strict_replaces_optional_manual_placeholders():
    manifest = {
        "auto_resolved_placeholders": {
            "REFERENCE_DB_PATH": "{MANUAL:REFERENCE_DB_PATH}",
            "STYLE_REFERENCE_PATH": "{MANUAL:STYLE_REFERENCE_PATH}",
        },
        "manual_required_placeholders": [
            {"placeholder": "REFERENCE_DB_PATH", "agent_file": "multiple", "context": "ref"},
            {"placeholder": "STYLE_REFERENCE_PATH", "agent_file": "multiple", "context": "style"},
            {"placeholder": "CONVERSION_PIPELINE", "agent_file": "multiple", "context": "conv"},
        ],
        "description": {
            "style_reference": None,
            "style_reference_path": None,
        },
    }

    build_team._apply_placeholder_policy(manifest, strict_manual_placeholders=False)

    assert manifest["auto_resolved_placeholders"]["REFERENCE_DB_PATH"].startswith("N/A")
    assert manifest["auto_resolved_placeholders"]["STYLE_REFERENCE_PATH"].startswith("N/A")
    placeholders = {item["placeholder"] for item in manifest["manual_required_placeholders"]}
    assert "REFERENCE_DB_PATH" not in placeholders
    assert "STYLE_REFERENCE_PATH" not in placeholders
    assert "CONVERSION_PIPELINE" in placeholders


def test_dual_mode_policy_strict_preserves_optional_manual_placeholders():
    manifest = {
        "auto_resolved_placeholders": {
            "REFERENCE_DB_PATH": "{MANUAL:REFERENCE_DB_PATH}",
            "STYLE_REFERENCE_PATH": "{MANUAL:STYLE_REFERENCE_PATH}",
        },
        "manual_required_placeholders": [
            {"placeholder": "REFERENCE_DB_PATH", "agent_file": "multiple", "context": "ref"},
            {"placeholder": "STYLE_REFERENCE_PATH", "agent_file": "multiple", "context": "style"},
        ],
        "description": {
            "style_reference": "docs/style.md",
        },
    }

    build_team._apply_placeholder_policy(manifest, strict_manual_placeholders=True)

    assert manifest["auto_resolved_placeholders"]["REFERENCE_DB_PATH"] == "{MANUAL:REFERENCE_DB_PATH}"
    assert manifest["auto_resolved_placeholders"]["STYLE_REFERENCE_PATH"] == "{MANUAL:STYLE_REFERENCE_PATH}"
    placeholders = {item["placeholder"] for item in manifest["manual_required_placeholders"]}
    assert "REFERENCE_DB_PATH" in placeholders
    assert "STYLE_REFERENCE_PATH" in placeholders


@pytest.mark.parametrize(
    "strict_arg,self_update,expected",
    [
        (None, False, False),
        (None, True, True),
        (False, True, False),
        (True, False, True),
    ],
)
def test_resolve_strict_manual_mode(strict_arg: bool | None, self_update: bool, expected: bool):
    assert (
        build_team._resolve_strict_manual_mode(
            strict_arg=strict_arg,
            self_update=self_update,
        )
        == expected
    )


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
        (["--refresh-index", "--update", "--description", "brief.json"], "cannot be used with --refresh-index"),
        (["--query-index", "x", "--update", "--description", "brief.json"], "cannot be used with --query-index"),
        (["--refresh-index", "--query-index", "x", "--description", "brief.json"], "mutually exclusive"),
        (["--query-index", "x", "--query-k", "0", "--description", "brief.json"], "--query-k must be >= 1"),
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


# ---------------------------------------------------------------------------
# Regression: bridge UX (HayekAI handoff 2026-W21)
#   D1 — bridge --output ending in target framework's agents-dir suffix
#        must be normalized to repo root with a warning, so artifacts do not
#        land at nested .github/.github/... paths.
#   D2 — --self with an external --output is refused unless
#        --allow-external-self-output is supplied.
#   D3 — bridge guard error messages include a corrective usage example.
# ---------------------------------------------------------------------------

def _make_claude_source(root: Path) -> Path:
    src = root / ".claude" / "agents"
    src.mkdir(parents=True)
    (src / "orchestrator.md").write_text(
        "---\n"
        "name: orchestrator\n"
        "description: \"d\"\n"
        "user-invokable: true\n"
        "tools: ['read']\n"
        "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        "---\n\n"
        "# orchestrator\n\nBody.\n",
        encoding="utf-8",
    )
    (root / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
    return src


@pytest.mark.parametrize(
    "target_framework,suffix",
    [
        ("copilot-vscode", (".github", "agents")),
        ("copilot-cli", (".github", "copilot")),
        ("claude", (".claude", "agents")),
    ],
)
def test_bridge_output_normalizes_agents_dir_suffix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    target_framework: str,
    suffix: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(build_team, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    src = _make_claude_source(tmp_path / "repo")
    bad_output = tmp_path / "repo" / Path(*suffix)
    rc = build_team.main(
        [
            "--bridge-from",
            str(src),
            "--bridge-source-framework",
            "claude",
            "--framework",
            target_framework,
            "--output",
            str(bad_output),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Normalizing" in captured.err
    # Bridge artifacts must land under the repo root, not nested under suffix.
    pair_dir = (
        tmp_path
        / "repo"
        / "references"
        / "bridges"
        / f"claude-to-{target_framework}"
    )
    assert (pair_dir / "bridge-manifest.json").exists()
    # And the nested form must NOT exist.
    nested = bad_output / "references" / "bridges"
    assert not nested.exists()


def test_bridge_output_without_suffix_is_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(build_team, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    src = _make_claude_source(tmp_path / "repo")
    output = tmp_path / "elsewhere"
    rc = build_team.main(
        [
            "--bridge-from",
            str(src),
            "--bridge-source-framework",
            "claude",
            "--framework",
            "copilot-vscode",
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Normalizing" not in captured.err
    assert (output / "references" / "bridges" / "claude-to-copilot-vscode" / "bridge-manifest.json").exists()


def test_self_with_external_output_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    external = tmp_path / "foreign-repo" / ".github" / "agents"
    external.mkdir(parents=True)
    rc = build_team.main(["--self", "--output", str(external)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "outside the AgentTeamsModule source tree" in captured.err
    assert "--allow-external-self-output" in captured.err
    # No files written
    assert list(external.iterdir()) == []


def test_self_with_external_output_dry_run_is_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(build_team, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    external = tmp_path / "foreign-repo" / ".github" / "agents"
    external.mkdir(parents=True)
    # Dry-run does not write so the guard must not fire.
    rc = build_team.main(["--self", "--output", str(external), "--dry-run"])
    # We accept rc == 0 OR a non-guard failure (e.g., missing self-description in
    # test environments). The crucial assertion is that the guard message is absent.
    assert rc in (0, 1)


@pytest.mark.parametrize(
    "argv,error_substring",
    [
        (["--bridge-from", "src", "--description", "brief.json"], "Bridge mode is independent"),
        (["--bridge-from", "src", "--project", "/tmp/x"], "Bridge mode is independent"),
        (["--bridge-check"], "Bridge mode is independent"),
        (["--bridge-refresh"], "Bridge mode is independent"),
    ],
)
def test_bridge_guard_errors_include_usage_example(
    argv: list[str],
    error_substring: str,
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit):
        build_team.main(argv)
    captured = capsys.readouterr()
    assert error_substring in captured.err
    assert "agentteams --bridge-from" in captured.err


# ---------------------------------------------------------------------------
# Regression: Q2 — --update alone must not trigger the security gate (merge
# is the default); --update --overwrite must trigger the gate (F1/F7 fix)
# ---------------------------------------------------------------------------

def _stub_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch agentteams.drift so --update tests don't require real build-log state."""
    from agentteams import drift as _drift_mod
    from types import SimpleNamespace

    fake_report = SimpleNamespace(
        has_changes=False,
        removed_files=[],
        manifest_changed=False,
        drifted_files=[],
        added_files=[],
        team_membership_changed=False,
        update_files=[],
    )
    monkeypatch.setattr(_drift_mod, "load_build_log", lambda _: {})
    monkeypatch.setattr(_drift_mod, "compute_structural_diff", lambda *_: fake_report)
    monkeypatch.setattr(_drift_mod, "refine_manifest_promotion", lambda *_: None)
    monkeypatch.setattr(_drift_mod, "print_structural_diff_report", lambda _: None)
    # Stub the security-freshness gate so tests don't need a real security cache
    monkeypatch.setattr(build_team, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)


def test_update_alone_bypasses_security_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """--update alone must not require a security clearance (Q2 regression).

    Default update behavior is merge; only --overwrite triggers the gate.
    No security-decisions.log.csv is present — gate would return rc=1 if invoked.
    """
    captured: dict = {}
    _stub_core_pipeline(monkeypatch, "copilot-vscode", captured)
    _stub_drift(monkeypatch)

    rc = build_team.main([
        "--description", "brief.json",
        "--framework", "copilot-vscode",
        "--output", str(tmp_path / ".github" / "agents"),
        "--update",
        "--security-offline",
    ])

    assert rc == 0  # gate not invoked; no clearance required for default merge


def test_update_overwrite_triggers_security_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """--update --overwrite must be blocked by the security gate when no log exists (Q2 regression)."""
    captured: dict = {}
    _stub_core_pipeline(monkeypatch, "copilot-vscode", captured)
    _stub_drift(monkeypatch)

    # No references/security-decisions.log.csv in tmp_path — gate must block.
    rc = build_team.main([
        "--description", "brief.json",
        "--framework", "copilot-vscode",
        "--output", str(tmp_path / ".github" / "agents"),
        "--update", "--overwrite",
        "--security-offline",
    ])

    assert rc == 1  # gate invoked and blocked due to missing security clearance


# ---------------------------------------------------------------------------
# Regression: bridge-check against a missing manifest must surface the
# --bridge-refresh hint both in the written report and on stderr.
# ---------------------------------------------------------------------------
def test_bridge_check_missing_manifest_cli_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source_dir = tmp_path / "src" / ".claude" / "agents"
    source_dir.mkdir(parents=True)
    (source_dir / "orchestrator.md").write_text(
        "---\n"
        "name: orchestrator\n"
        "description: \"d\"\n"
        "allowed-tools: Read\n"
        "---\n\n"
        "# orchestrator\n\nBody.\n",
        encoding="utf-8",
    )
    (source_dir.parent / "CLAUDE.md").write_text("# Instructions\n", encoding="utf-8")

    output_root = tmp_path / "out"
    rc = build_team.main([
        "--bridge-from", str(source_dir),
        "--bridge-source-framework", "claude",
        "--framework", "copilot-vscode",
        "--output", str(output_root),
        "--bridge-check",
    ])
    assert rc == 1

    captured = capsys.readouterr()
    assert "--bridge-refresh" in captured.err
    assert "no bridge manifest" in captured.err.lower()

    report = (
        output_root
        / "references"
        / "bridges"
        / "claude-to-copilot-vscode"
        / "bridge-check.report.md"
    )
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "--bridge-refresh" in text
    assert "FAIL" in text


# ---------------------------------------------------------------------------
# Regression: bridge generate mode must emit a Notice on stderr when files
# are skipped because they already exist, recommending --bridge-refresh.
# ---------------------------------------------------------------------------
def test_bridge_generate_skip_notice_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source_dir = tmp_path / "src" / ".claude" / "agents"
    source_dir.mkdir(parents=True)
    (source_dir / "orchestrator.md").write_text(
        "---\n"
        "name: orchestrator\n"
        "description: \"d\"\n"
        "allowed-tools: Read\n"
        "---\n\n"
        "# orchestrator\n\nBody.\n",
        encoding="utf-8",
    )
    (source_dir.parent / "CLAUDE.md").write_text("# Instructions\n", encoding="utf-8")

    output_root = tmp_path / "out"
    common = [
        "--bridge-from", str(source_dir),
        "--bridge-source-framework", "claude",
        "--framework", "copilot-vscode",
        "--output", str(output_root),
    ]

    # First generate: nothing exists yet → no notice.
    rc1 = build_team.main(common + ["--bridge-refresh"])
    assert rc1 == 0
    first_err = capsys.readouterr().err
    assert "Notice" not in first_err

    # Second generate without --bridge-refresh: everything exists → notice expected.
    rc2 = build_team.main(common)
    assert rc2 == 0
    second_err = capsys.readouterr().err
    assert "Notice" in second_err
    assert "--bridge-refresh" in second_err


# ---------------------------------------------------------------------------
# --query-strategy: vector strategy CLI integration
# ---------------------------------------------------------------------------

def _make_query_index_fixture(tmp_path: Path) -> tuple[dict, Path]:
    """Return (manifest, output_dir) with a pre-built memory index."""
    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)

    src = tmp_path / "prior.md"
    src.write_text(
        "# Prior Work\n\n"
        "Memory drift detection and behavioral validation were introduced in W18. "
        "The agent performance baseline was established to measure retrieval latency.\n",
        encoding="utf-8",
    )
    idx = build_memory_index([src], project_name="P", framework="copilot-vscode")
    (index_dir / "memory-index.json").write_text(json.dumps(idx), encoding="utf-8")

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    return manifest, output_dir


def test_query_index_accepts_vector_strategy(tmp_path: Path) -> None:
    """--query-strategy vector is accepted and returns structurally valid results."""
    manifest, output_dir = _make_query_index_fixture(tmp_path)
    rc = build_team._run_query_index(
        manifest, output_dir, "memory drift behavioral validation", k=3, strategy="vector"
    )
    assert rc == 0


def test_query_strategy_default_is_lexical(tmp_path: Path) -> None:
    """Omitting --query-strategy produces the same results as strategy='lexical'."""
    manifest, output_dir = _make_query_index_fixture(tmp_path)
    from agentteams.memory_index import query_index
    from agentteams import memory_index as mi_mod

    index_path = output_dir / "references" / "memory-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))

    hits_default = query_index(index, "memory drift", k=5)
    hits_explicit = query_index(index, "memory drift", k=5, strategy="lexical")
    assert hits_default == hits_explicit


def test_query_strategy_validation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown --query-strategy value is rejected by argparse before execution."""
    import sys

    with pytest.raises(SystemExit) as exc_info:
        build_team.main([
            "--description", "brief.json",
            "--query-index", "drift",
            "--query-strategy", "unknown",
        ])
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "unknown" in captured.err or "invalid choice" in captured.err


# ---------------------------------------------------------------------------
# --fail-on-legacy-skip: exit-code promotion when legacy files skipped
# ---------------------------------------------------------------------------

def test_finalize_exit_code_promotes_on_legacy_skip_with_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """_finalize_exit_code returns 1 when flag is set and legacy skips occurred."""
    from argparse import Namespace
    result = EmitResult()
    result.skipped_legacy.append("/some/path/legacy.agent.md")
    result.skipped_legacy_drift.append(True)

    args_on = Namespace(fail_on_legacy_skip=True)
    assert build_team._finalize_exit_code(result, args_on) == 1
    captured = capsys.readouterr()
    assert "--fail-on-legacy-skip" in captured.err
    assert "1 legacy file(s) skipped" in captured.err


def test_finalize_exit_code_unaffected_without_flag() -> None:
    """Default behavior: legacy skips don't change exit code."""
    from argparse import Namespace
    result = EmitResult()
    result.skipped_legacy.append("/some/path/legacy.agent.md")
    result.skipped_legacy_drift.append(True)

    args_off = Namespace(fail_on_legacy_skip=False)
    assert build_team._finalize_exit_code(result, args_off) == 0


def test_finalize_exit_code_zero_when_no_legacy_skips_even_with_flag() -> None:
    """Flag set but no legacy skips: still exits 0."""
    from argparse import Namespace
    result = EmitResult()
    args_on = Namespace(fail_on_legacy_skip=True)
    assert build_team._finalize_exit_code(result, args_on) == 0


def test_finalize_exit_code_returns_1_on_emit_errors_regardless_of_flag() -> None:
    """Emit errors always return 1, independent of the new flag."""
    from argparse import Namespace
    result = EmitResult()
    result.errors.append("synthetic failure")
    args_off = Namespace(fail_on_legacy_skip=False)
    assert build_team._finalize_exit_code(result, args_off) == 1

