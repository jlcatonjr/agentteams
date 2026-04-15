"""Tests for Copilot CLI auto-correction support."""

from __future__ import annotations

from pathlib import Path
import json


def test_run_copilot_autocorrect_uses_standalone_copilot(tmp_path, monkeypatch):
    from agentteams.audit import AuditFinding, AuditResult
    from agentteams import remediate

    output_dir = tmp_path / ".github" / "agents"
    output_dir.mkdir(parents=True)
    audit_result = AuditResult(
        static_findings=[
            AuditFinding(
                category="CONFLICT",
                code="TEST_FINDING",
                severity="error",
                file="orchestrator.agent.md",
                description="Example issue to repair.",
            )
        ]
    )
    captured: dict[str, object] = {}

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/copilot" if name == "copilot" else None

    class _Proc:
        returncode = 0
        stdout = "fixed"
        stderr = ""

    def fake_run(command, cwd, capture_output, text, timeout):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return _Proc()

    monkeypatch.setattr(remediate.shutil, "which", fake_which)
    monkeypatch.setattr(remediate.subprocess, "run", fake_run)

    result = remediate.run_copilot_autocorrect(
        output_dir=output_dir,
        manifest={"project_name": "DemoProject"},
        audit_result=audit_result,
    )

    assert result.succeeded
    assert result.attempted
    assert captured["command"][0] == "/usr/local/bin/copilot"
    assert "-p" in captured["command"]
    assert "--allow-all-tools" in captured["command"]
    assert "--no-ask-user" in captured["command"]
    assert "--model" in captured["command"]
    prompt = captured["command"][captured["command"].index("-p") + 1]
    assert "TEST_FINDING" in prompt
    assert "DemoProject" in prompt
    assert captured["cwd"] == output_dir.parent


def test_build_main_auto_correct_reruns_audit(tmp_path, monkeypatch):
    import build_team
    import agentteams.audit as audit_module
    import agentteams.remediate as remediate_module
    from agentteams.audit import AuditFinding, AuditResult
    from agentteams.remediate import RemediationResult

    first_audit = AuditResult(
        static_findings=[
            AuditFinding(
                category="CONFLICT",
                code="AUTO_FIX_ME",
                severity="error",
                file="orchestrator.agent.md",
                description="This should trigger remediation.",
            )
        ]
    )
    clean_audit = AuditResult()
    audit_calls: list[Path] = []
    remediation_calls: list[Path] = []

    def fake_run_post_audit(output_dir, manifest, rendered_files=None, ai_audit=True):
        audit_calls.append(output_dir)
        return first_audit if len(audit_calls) == 1 else clean_audit

    def fake_run_copilot_autocorrect(*, output_dir, manifest, audit_result):
        remediation_calls.append(output_dir)
        return RemediationResult(
            attempted=True,
            succeeded=True,
            message="Fixed by copilot.",
            command=["copilot", "-p", "fix"],
        )

    monkeypatch.setattr(audit_module, "run_post_audit", fake_run_post_audit)
    monkeypatch.setattr(remediate_module, "run_copilot_autocorrect", fake_run_copilot_autocorrect)

    brief = Path(__file__).parent.parent / "examples" / "software-project" / "brief.json"
    output_dir = tmp_path / ".github" / "agents"

    rc = build_team.main([
        "--description",
        str(brief),
        "--output",
        str(output_dir),
        "--overwrite",
        "--yes",
        "--post-audit",
        "--auto-correct",
    ])

    assert rc == 0
    assert len(audit_calls) == 2
    assert remediation_calls == [output_dir.resolve()]


def test_build_main_update_audits_emitted_files_from_disk(tmp_path, monkeypatch):
    import build_team
    import agentteams.audit as audit_module

    brief = Path(__file__).parent.parent / "examples" / "software-project" / "brief.json"
    output_dir = tmp_path / ".github" / "agents"

    first_rc = build_team.main([
        "--description",
        str(brief),
        "--output",
        str(output_dir),
        "--overwrite",
        "--yes",
    ])
    assert first_rc == 0

    build_log = output_dir / "references" / "build-log.json"
    payload = json.loads(build_log.read_text(encoding="utf-8"))
    payload["manifest_fingerprint"] = "outdated"
    build_log.write_text(json.dumps(payload), encoding="utf-8")

    captured: list[object] = []

    def fake_run_post_audit(output_dir, manifest, rendered_files=None, ai_audit=True):
        captured.append(rendered_files)
        return audit_module.AuditResult()

    monkeypatch.setattr(audit_module, "run_post_audit", fake_run_post_audit)

    update_rc = build_team.main([
        "--description",
        str(brief),
        "--output",
        str(output_dir),
        "--update",
        "--yes",
        "--post-audit",
    ])

    assert update_rc == 0
    assert captured == [None]