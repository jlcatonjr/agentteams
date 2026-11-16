"""test_constitutional_redteam.py — the red-team battery, run as a standing regression.

The battery was written on 2026-08-06 to audit whether this project's constitution (C-1..C-5)
could be overridden. It found 21 working exploits. Every one of them is now either fixed, or
recorded as a deliberate limitation with its residue named.

Keeping it in `tmp/` would have meant the exploit inventory and the test suite drift apart the
moment someone refactors a control. So it runs here, on every suite run, and the assertion is
the one that matters: **no probe may return EXPLOITED.** A regression in any of the fixes
re-opens a measured attack, and the run says which one.

The battery's other outcome classes are permitted and are asserted individually below, so a
fix that quietly downgrades itself to "documented limitation" also fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.redteam.registry import (
    ACCEPTED_WEAKNESSES_REL,
    MIN_REASON_CHARS,
    load_accepted_weaknesses,
)
from tests.constitutional_redteam_battery import (  # noqa: E402
    DEFENDED,
    EXPLOITED,
    PROBES,
    RESULTS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The deployed agent instance the live-tree probes read. Absent from a public-release / CI
#: checkout, which strips the deployed .claude/ instance.
_AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

#: Probes allowed to return something other than DEFENDED, each with the reason. A probe not in
#: this map MUST be DEFENDED. Adding an entry is a deliberate act that has to state a reason —
#: which is the point: it makes accepting a weakness visible in a diff rather than in a tally.
#:
#: **Read from CSV rather than declared here** since the standing daily audit shipped: F-6 in
#: ``agentteams/redteam/checks_report.py`` enforces the same property on every daily run, and
#: two copies of an exemption list drift the moment someone edits one. The four assertions
#: below are unchanged; only where they read the ledger from moved.
ALLOWED_NON_DEFENDED: dict[str, tuple[str, str]] = load_accepted_weaknesses(REPO_ROOT)


@pytest.fixture(scope="module")
def results() -> dict:
    """Run every probe once and index the outcomes by probe id."""
    RESULTS.clear()
    # Deliberately uncaught (CH-24): a probe that raises errors this fixture, which errors every
    # test in the module. That is the loud failure a security battery should have — swallowing
    # it into a PROBE-ERROR row would let a battery of broken probes report all-clear.
    for probe in PROBES:
        probe()
    return {p.pid: p for p in RESULTS}


def test_every_probe_ran(results: dict) -> None:
    """Anti-vacuity: the assertions below say nothing if the battery silently shrank."""
    assert len(results) == len(PROBES), (
        f"{len(PROBES)} probes defined but {len(results)} results recorded — a probe returned "
        f"without calling record(), so its attack was never scored"
    )


@pytest.mark.skipif(not _AGENTS_DIR.exists(), reason="deployed .claude/ agent instance absent from this checkout (public release / CI)")
def test_no_measured_exploit_survives(results: dict) -> None:
    """The headline assertion. Any EXPLOITED result is a re-opened attack."""
    exploited = [
        f"{p.pid} [{p.article}/{p.tier}] {p.name} — {p.evidence[:160]}"
        for p in results.values() if p.outcome == EXPLOITED
    ]
    assert not exploited, "constitutional exploits are live again:\n" + "\n".join(exploited)


@pytest.mark.skipif(not _AGENTS_DIR.exists(), reason="deployed .claude/ agent instance absent from this checkout (public release / CI)")
def test_every_non_defended_probe_is_explicitly_accepted(results: dict) -> None:
    """A weakness may be accepted, but only on the record.

    Without this, a fix could regress from DEFENDED to PARTIAL and the suite would stay green
    because PARTIAL is not EXPLOITED. Accepting a weakness has to cost a diff.
    """
    unexpected = []
    for p in results.values():
        if p.outcome == DEFENDED:
            continue
        allowed = ALLOWED_NON_DEFENDED.get(p.pid)
        if allowed is None or allowed[0] != p.outcome:
            expected = allowed[0] if allowed else DEFENDED
            unexpected.append(f"{p.pid}: got {p.outcome}, accepted value is {expected}")
    assert not unexpected, (
        "probe outcomes changed without an accompanying entry in ALLOWED_NON_DEFENDED:\n"
        + "\n".join(unexpected)
    )


def test_the_accepted_list_has_no_stale_entries(results: dict) -> None:
    """The other direction: a probe that got FIXED must lose its exemption.

    Otherwise the accepted list silently accumulates permission for weaknesses that no longer
    exist, and stops describing the system.
    """
    stale = [
        pid for pid, (outcome, _) in ALLOWED_NON_DEFENDED.items()
        if pid in results and results[pid].outcome == DEFENDED
    ]
    assert not stale, (
        f"these probes now DEFEND and no longer need an exemption: {stale}. "
        f"Remove them from ALLOWED_NON_DEFENDED."
    )


def test_controls_still_pass(results: dict) -> None:
    """Anti-vacuity: the control probes must be DEFENDED, or the battery proves nothing.

    A battery whose controls fail is measuring a broken harness, not a secure system — and it
    would report all-clear either way.
    """
    failed_controls = [
        p.pid for p in results.values()
        if p.name.startswith("CONTROL:") and p.outcome != DEFENDED
    ]
    assert not failed_controls, f"control probes failed: {failed_controls}"


@pytest.mark.skipif(not (REPO_ROOT / "references" / "redteam-findings.log.csv").exists(), reason="redteam operational data removed for public release / absent in this checkout")
def test_the_ledger_is_populated_and_every_reason_is_substantive() -> None:
    """Anti-vacuity for the CSV move: an empty or unreadable ledger must not read as clean.

    Before the move, ``ALLOWED_NON_DEFENDED`` was a literal in this file, so it could not
    silently become empty. Read from CSV it can — a renamed file, a bad header, a botched
    merge — and an empty exemption map makes `test_every_non_defended_probe_is_explicitly_
    accepted` *stricter*, which looks like a pass right up until it fails loudly for the wrong
    reason. This asserts the ledger is actually there and actually says something.
    """
    assert ALLOWED_NON_DEFENDED, (
        f"{ACCEPTED_WEAKNESSES_REL} read as empty; the accepted-weakness ledger is the single "
        f"source of truth for both this suite and the daily audit's F-6 check"
    )
    thin = {
        pid: len(reason)
        for pid, (_, reason) in ALLOWED_NON_DEFENDED.items()
        if len(reason) < MIN_REASON_CHARS
    }
    assert not thin, f"exemptions with a reason shorter than {MIN_REASON_CHARS} chars: {thin}"


# --------------------------------------------------------------------------- E1 measures a render, not a laptop (#478)

def _e1_with(monkeypatch: pytest.MonkeyPatch, agents: dict[str, str]):
    import tests.constitutional_redteam_battery as battery

    monkeypatch.setattr(battery, "_e1_rendered_agents", lambda: agents)
    before = len(battery.RESULTS)
    battery.e1_capability_key_not_honoured()
    row = battery.RESULTS[before]
    del battery.RESULTS[before:]
    return row


def test_e1_reports_an_emitted_allowed_tools_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sensitivity: a subagent whose limit sits in `allowed-tools:` is EXPLOITED."""
    row = _e1_with(monkeypatch, {"b/worker.md": "---\nname: w\nallowed-tools: Read\ntools: Read\n---\n"})
    assert row.outcome == EXPLOITED


def test_e1_reports_any_agent_without_a_recognised_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `tools:`/`disallowedTools:` inherits every tool, read-only or not."""
    assert _e1_with(monkeypatch, {"b/security.md": "---\nname: security\ndescription: s\n---\n"}).outcome == EXPLOITED
    assert _e1_with(monkeypatch, {"b/primary-producer.md": "---\nname: p\n---\n"}).outcome == EXPLOITED


@pytest.mark.parametrize("tools", ["Bash, Read, Write, Edit", "Read, Edit", "Read, Grep, Bash"])
def test_e1_reports_a_read_only_agent_emitted_with_write_tools(monkeypatch: pytest.MonkeyPatch, tools: str) -> None:
    """@adversarial #478 review: a present `tools:` key is not enough; a read-only agent must not get write tools."""
    row = _e1_with(monkeypatch, {"b/security.md": f"---\nname: security\ntools: {tools}\n---\n"})
    assert row.outcome == EXPLOITED and "b/security.md" in row.evidence


def test_e1_allows_a_scoped_bash_for_a_read_only_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _e1_with(monkeypatch, {"b/security.md": "---\nname: security\ntools: Read, Grep, Bash(python -m x:*)\n---\n"})
    assert row.outcome == DEFENDED


def test_e1_the_adapters_fallback_grants_no_write_tools() -> None:
    """The adapter's fallback for a tool list it cannot parse stays read-only (#511, IM item 42).

    It was `Bash, Read, Write, Edit`, the path E1's widened check was written against: a read-only template that hit
    it was emitted with write access. If the fallback ever widens again, E1 would only catch it for a template that
    happens to hit the fallback; this pins the fallback itself.
    """
    from agentteams.frameworks.claude import _map_allowed_tools
    import tests.constitutional_redteam_battery as battery

    with pytest.warns(UserWarning, match="no usable tool scope"):
        fallback = _map_allowed_tools("---\nname: security\ntools:\n  - read\n  - search\n---\n")
    assert not set(t.strip() for t in fallback.split(",")) & battery._E1_WRITE_TOOLS


def test_e1_read_only_set_comes_from_the_templates() -> None:
    import tests.constitutional_redteam_battery as battery

    slugs = battery._e1_read_only_slugs()
    assert {"security", "code-hygiene", "adversarial", "conflict-auditor"} <= slugs
    assert "git-operations" not in slugs and "primary-producer" not in slugs


def test_e1_is_defended_when_every_agent_states_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _e1_with(monkeypatch, {"b/security.md": "---\nname: security\ntools: Read, Grep\n---\n"})
    assert row.outcome == DEFENDED


def test_e1_raises_on_an_empty_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anti-vacuity: no rendered subagent file is a probe defect, never DEFENDED (0/0 was the #478 bug)."""
    import tests.constitutional_redteam_battery as battery

    import agentteams.cli.render_pipeline as rp

    monkeypatch.setattr(rp, "_build_final_rendered", lambda *a, **k: [("../CLAUDE.md", "x")])
    with pytest.raises(RuntimeError, match="nothing to measure"):
        battery._e1_rendered_agents()


def test_e1_reads_no_gitignored_path() -> None:
    src = (REPO_ROOT / "tests" / "constitutional_redteam_battery.py").read_text(encoding="utf-8")
    body = src.split("def e1_capability_key_not_honoured", 1)[1].split("\ndef ", 1)[0]
    assert '".claude"' not in body and "CLAUDE_AGENTS" not in src
