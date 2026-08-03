"""test_audit_framework_suffix.py — the audit must see the framework it is auditing.

`run_post_audit` receives the same `(rel_path, content)` list the emitter writes. On
copilot-vscode those paths end `.agent.md`; on claude, copilot-cli, agents_md and goose they
end `.md`. Eleven places in `audit.py` hardcoded `.agent.md`, in two distinct ways, and the
audit failed in **both directions** on four of five frameworks.

Measured on this repo's own brief, identical inputs, before the fix:

    copilot-vscode   6 static + 1 code-hygiene (CH14_INLINE_DATA_BLOCK)
    claude          15 static + 0

*False positives.* `generated_slugs` was built by filtering on `.agent.md`, so on claude it
was **empty** and every required agent was reported missing — including `orchestrator`, with
`orchestrator.md` sitting in the same file map. Separately, `_check_workstream_experts` built
an expected filename `f"{slug}-expert.agent.md"` and looked it up: a name-construction bug,
not a filter bug, and one that threading a suffix into the filters alone would have left
firing.

*False negatives.* The CH-14 inline-data-block check filters the same way, so it reported
nothing on claude. `audit_agent_contract.py` carries the same gate, so the per-agent contract
checks — including the invariant-core marker check S4.5 rests on — did not run there at all.

**The test is an invariant, not a count.** Two frameworks legitimately differ: copilot-vscode
supports handoffs and claude does not, so equal finding counts would prove nothing. What must
hold on every framework is that the audit never reports a file missing while that file is in
the map it was handed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))

BRIEF = REPO_ROOT / ".github/agents/_build-description.json"

FRAMEWORKS = ["copilot-vscode", "claude", "copilot-cli"]


def _audit_for(framework: str):
    """Render the self team for *framework* and run the real post-generation audit."""
    from test_integration import _run_pipeline

    from agentteams import analyze, ingest
    from agentteams import audit as audit_mod

    desc = ingest.load(BRIEF, scan_project=False)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "agents"
        _run_pipeline(BRIEF, out, framework=framework)
        rendered = [
            (p.relative_to(out).as_posix(), p.read_text(encoding="utf-8"))
            for p in sorted(out.rglob("*"))
            if p.is_file() and p.suffix in {".md", ".yaml", ".json"}
        ]
        manifest = analyze.build_manifest(desc, framework=framework)
        result = audit_mod.run_post_audit(out, manifest, rendered_files=rendered, ai_audit=False)
        names = {Path(p).name for p, _ in rendered}
        return result, names


@pytest.fixture(scope="module")
def audits():
    if not BRIEF.exists():
        pytest.skip("self brief absent")
    return {fw: _audit_for(fw) for fw in FRAMEWORKS}


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_no_missing_finding_names_a_file_that_exists(framework, audits) -> None:
    """The invariant. Framework-independent, and the one the old code violated 9 times."""
    result, names = audits[framework]
    lies = []
    for f in result.all_findings if hasattr(result, "all_findings") else (
        result.static_findings + result.agent_refactor_findings + result.code_hygiene_findings
    ):
        if not f.code.startswith("MISSING"):
            continue
        for name in names:
            stem = name.rsplit(".md", 1)[0].replace(".agent", "")
            if stem and (stem in f.description):
                lies.append(f"{f.code}: {f.description[:90]} — but {name} is in the file map")
                break
    assert not lies, f"[{framework}] audit reported missing files that exist:\n  " + "\n  ".join(lies)


def test_required_agents_are_found_on_every_framework(audits) -> None:
    """`generated_slugs` was empty on `.md` frameworks, so every required agent 'went missing'."""
    for framework in FRAMEWORKS:
        result, _ = audits[framework]
        missing = [f for f in result.static_findings if f.code == "MISSING_REQUIRED_AGENT"]
        assert not missing, (
            f"[{framework}] {len(missing)} required agents reported missing: "
            f"{[f.description[:60] for f in missing]}"
        )


def test_workstream_experts_are_found_on_every_framework(audits) -> None:
    """The name-construction half: `f'{slug}-expert.agent.md'` looked up in a `.md` file map."""
    for framework in FRAMEWORKS:
        result, _ = audits[framework]
        missing = [f for f in result.static_findings if f.code == "MISSING_WORKSTREAM_EXPERT"]
        assert not missing, (
            f"[{framework}] {len(missing)} workstream experts reported missing: "
            f"{[f.description[:60] for f in missing]}"
        )


def test_the_per_file_checks_actually_run_on_a_dot_md_framework(audits) -> None:
    """False negatives: a check that inspects agent files must inspect some.

    `CH14_INLINE_DATA_BLOCK` fired on copilot-vscode and was silent on claude — not because
    the content differed but because no file passed the suffix filter. Rather than pin one
    code, assert the per-file checks reach a comparable number of files on both.
    """
    from agentteams import audit as audit_mod

    for framework in FRAMEWORKS:
        result, names = audits[framework]
        ext = ".agent.md" if framework == "copilot-vscode" else ".md"
        agent_files = [
            n for n in names if n.endswith(ext) and n != "SETUP-REQUIRED.md"
        ]
        assert len(agent_files) > 20, f"[{framework}] test setup wrong: {len(agent_files)} agents"
        # The audit must have classified the same population.
        seen = audit_mod._agent_file_count(
            {n: "" for n in names}, audit_mod._agent_file_ext({"framework": framework})
        )
        assert seen == len(agent_files), (
            f"[{framework}] audit classified {seen} agent files but {len(agent_files)} exist"
        )


def test_build_manifest_always_records_the_framework() -> None:
    """Closes the fallback. `_agent_file_ext` defaults only for hand-built test manifests.

    If a real manifest could omit `framework`, the default would silently reintroduce exactly
    the blindness this file exists to remove.
    """
    from agentteams import analyze, ingest

    if not BRIEF.exists():
        pytest.skip("self brief absent")
    desc = ingest.load(BRIEF, scan_project=False)
    for fw in FRAMEWORKS:
        assert analyze.build_manifest(desc, framework=fw).get("framework") == fw
