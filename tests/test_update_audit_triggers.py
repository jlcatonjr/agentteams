import pytest
from pathlib import Path

_ACTIVE_AGENTS_DIR = Path(".github/agents")
_SKIP_IF_NO_ACTIVE_AGENTS = pytest.mark.skipif(
    not (_ACTIVE_AGENTS_DIR / "orchestrator.agent.md").exists(),
    reason=".github/agents/ is gitignored; active-agent tests only run locally against generated output",
)


def _assert_in_order(text: str, parts: list[str]) -> None:
    last = -1
    for part in parts:
        index = text.find(part)
        assert index != -1, f"missing text: {part}"
        assert index > last, f"text out of order: {part}"
        last = index


def test_orchestrator_template_routes_post_update_chain() -> None:
    text = Path("agentteams/templates/universal/orchestrator.template.md").read_text(encoding="utf-8")

    assert "After any investigation or fix: delegate to `@agent-updater`, then `@adversarial`, then `@conflict-auditor` before closing" in text
    _assert_in_order(
        text,
        [
            "### Workflow 6: Documentation Maintenance",
            "1. Invoke `@agent-updater` → sync docs with changes, run the repository change census, and evaluate docs/API impact",
            "2. Invoke `@adversarial` → challenge the repository change census, docs/API impact decision, and synchronized workflow assumptions before closeout",
            "3. Invoke `@conflict-auditor` → verify consistency after documentation synchronization",
        ],
    )


@_SKIP_IF_NO_ACTIVE_AGENTS
def test_active_orchestrator_routes_post_update_chain() -> None:
    text = Path(".github/agents/orchestrator.agent.md").read_text(encoding="utf-8")

    assert "After any investigation or fix: delegate to `@agent-updater` for a repository change census and docs/API impact evaluation, then `@adversarial`, then `@conflict-auditor` before closing" in text
    _assert_in_order(
        text,
        [
            "### Workflow 6: Documentation Maintenance",
            "1. Invoke `@agent-updater` → sync docs with changes, run the repository change census, and decide whether the published site or API docs need updates",
            "2. Invoke `@adversarial` → challenge the repository change census, docs/API impact decision, and synchronized workflow assumptions before closeout",
            "3. Invoke `@conflict-auditor` → verify consistency after documentation synchronization",
        ],
    )


def test_agent_updater_template_hands_off_to_adversarial_then_conflict() -> None:
    text = Path("agentteams/templates/universal/agent-updater.template.md").read_text(encoding="utf-8")

    assert "agents: ['adversarial', 'conflict-auditor', 'agent-refactor']" in text
    _assert_in_order(
        text,
        [
            "  - label: Refactor Agent Docs",
            "  - label: Run Adversarial Review",
            "  - label: Run Conflict Audit",
            "10. Hand off to `@agent-refactor` for extraction opportunities",
            "11. Hand off to `@adversarial` to challenge the repository change census, docs/API impact decision, and any newly synchronized assumptions before closeout",
            "12. Hand off to `@conflict-auditor` to verify consistency",
        ],
    )


@_SKIP_IF_NO_ACTIVE_AGENTS
def test_active_agent_updater_hands_off_to_adversarial_then_conflict() -> None:
    text = Path(".github/agents/agent-updater.agent.md").read_text(encoding="utf-8")

    assert "agents: ['adversarial', 'conflict-auditor', 'agent-refactor']" in text
    _assert_in_order(
        text,
        [
            "  - label: Refactor Agent Docs",
            "  - label: Run Adversarial Review",
            "  - label: Run Conflict Audit",
            "11. Hand off to `@agent-refactor` for extraction opportunities",
            "12. Hand off to `@adversarial` to challenge the repository change census, docs/API impact decision, and any newly synchronized assumptions before closeout",
            "13. Hand off to `@conflict-auditor` to verify consistency",
        ],
    )