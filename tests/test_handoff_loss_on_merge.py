"""``--update --merge`` must not silently replace a project's hand-written handoffs.

**The defect (portal, 2026-10-11 fleet audit).** The orchestrator lost its two project-specific handoffs
(``agents:`` still listed both targets), and agent-refactor, cleanup and conflict-resolution lost theirs.
No notice said so.

**Root cause.** The three-way front-matter merge applies the template's value when the on-disk value
equals the baseline. ``build-log.json`` recorded the baseline as the front matter *as written*, and a merge
writes the project's own value whenever it keeps one. So one run after a project's edit was kept, the
baseline held the project's value, the disk matched it, and the merge "applied the template to an untouched
key". portal's build log held its hand-written handoffs as the baseline.

The fixture is an anonymised excerpt of portal's orchestrator front matter.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import emit, fleet
from agentteams import front_matter_baseline as fmb
from agentteams.front_matter_merge import _front_matter_keys, _merge_front_matter

_PROJECT_HANDOFFS = (
    "handoffs:\n"
    "  - label: Security Review (REQUIRED before destructive ops)\n"
    "    agent: security\n"
    "    prompt: \"Review the planned action for security risks before proceeding.\"\n"
    "    send: false\n"
    "  - label: Frontend App\n"
    "    agent: acme-frontend\n"
    "    prompt: \"Work on Vue 3 components and the admin UI.\"\n"
    "    send: false\n"
    "  - label: Pricing Subsystem\n"
    "    agent: pricing\n"
    "    prompt: \"Work on the pricing dashboard and tier management.\"\n"
    "    send: false\n"
)
_TEMPLATE_HANDOFFS = (
    "handoffs:\n"
    "  - label: Security Review\n"
    "    agent: security\n"
    "    prompt: \"Review the planned action for credentials or destructive operations.\"\n"
    "    send: false\n"
    "  - label: Navigate Project\n"
    "    agent: navigator\n"
    "    prompt: \"Locate files.\"\n"
    "    send: false\n"
)
_BODY = (
    "<!-- AGENTTEAMS:BEGIN rules v=1 -->\n## Rules\n\n1. Route everything.\n<!-- AGENTTEAMS:END rules -->\n\n"
    "## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — notes.\n"
)


def _doc(handoffs: str, body: str = _BODY) -> str:
    return ("---\nname: Orchestrator — Acme\ndescription: \"Lead agent\"\n"
            "agents: ['security', 'navigator', 'acme-frontend', 'pricing']\n" + handoffs + "---\n" + body)


def _deploy(root: Path, text: str, log: dict) -> Path:
    target = root / "orchestrator.agent.md"
    root.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "references" / "build-log.json").write_text(json.dumps(log), encoding="utf-8")
    return target


def _emit(root: Path, render: str):
    return emit.emit_all([("orchestrator.agent.md", render)], output_dir=root, dry_run=False,
                         overwrite=False, merge=True, yes=True)


# --- the root cause, reproduced -------------------------------------------------------------------------

def test_a_polluted_legacy_baseline_no_longer_replaces_hand_written_handoffs(tmp_path):
    """portal's exact shape: the as-written baseline equals the project's handoffs."""
    project = _doc(_PROJECT_HANDOFFS)
    legacy_log = {"front_matter_baseline": {"orchestrator.agent.md": _front_matter_keys(project)}}
    target = _deploy(tmp_path, project, legacy_log)
    result = _emit(tmp_path, _doc(_TEMPLATE_HANDOFFS))
    written = target.read_text(encoding="utf-8")
    assert "agent: acme-frontend" in written and "agent: pricing" in written
    assert "Security Review (REQUIRED before destructive ops)" in written, "the project's wording is kept"
    assert any("'handoffs' differs from the template" in n for n in result.notices), "and the divergence named"


def test_the_legacy_loader_drops_block_and_capability_keys_only():
    log = {"front_matter_baseline": {"a.md": {"handoffs": "\n  - label: x", "tools": "['read']",
                                              "agents": "['x']", "argument-hint": "a goal"}}}
    assert fmb.load(log) == {"a.md": {"argument-hint": "a goal"}}
    log[fmb.VERSION_KEY] = fmb.BASELINE_VERSION
    assert fmb.load(log)["a.md"]["handoffs"] == "\n  - label: x"


def test_the_baseline_records_only_what_agentteams_authored():
    written = {"a.md": {"handoffs": "\n  - label: mine", "argument-hint": "same"}}
    template = {"a.md": {"handoffs": "\n  - label: template", "argument-hint": "same"}}
    assert fmb.compute(written, template, None) == {"a.md": {"argument-hint": "same"}}
    previous = {"a.md": {"handoffs": "\n  - label: template-v1"}}
    assert fmb.compute(written, template, previous)["a.md"]["handoffs"] == "\n  - label: template-v1"


def test_a_kept_edit_stays_kept_on_every_later_run():
    """The one-run protection, closed: run 1 keeps the edit; run 2's template change must not take it."""
    template_v1 = _doc(_TEMPLATE_HANDOFFS)
    project = _doc(_PROJECT_HANDOFFS)
    base_v1 = fmb.compute({"o": _front_matter_keys(template_v1)}, {"o": _front_matter_keys(template_v1)}, None)
    merged, _applied, _props = _merge_front_matter(template_v1, project, base_v1["o"])
    base_v2 = fmb.compute({"o": merged}, {"o": _front_matter_keys(template_v1)}, base_v1)
    template_v2 = _doc(_TEMPLATE_HANDOFFS.replace("Locate files.", "Locate files and modules."))
    merged2, applied2, props2 = _merge_front_matter(template_v2, project, base_v2["o"])
    assert "acme-frontend" in merged2["handoffs"] and not applied2
    assert any("changed in BOTH" in p for p in props2)


def test_an_untouched_template_value_still_updates(tmp_path):
    """Negative control: the fix must not freeze template-authored handoffs."""
    template_v1 = _doc(_TEMPLATE_HANDOFFS)
    log = {"front_matter_baseline": {"orchestrator.agent.md": _front_matter_keys(template_v1)},
           fmb.VERSION_KEY: fmb.BASELINE_VERSION}
    target = _deploy(tmp_path, template_v1, log)
    _emit(tmp_path, _doc(_TEMPLATE_HANDOFFS.replace("Locate files.", "Locate files and modules.")))
    assert "Locate files and modules." in target.read_text(encoding="utf-8")


def test_the_run_log_writes_a_version_2_baseline(tmp_path):
    """Through build_team's writer: the hand-written block is not recorded as agentteams-authored."""
    import build_team

    project = _doc(_PROJECT_HANDOFFS)
    _deploy(tmp_path / ".github" / "agents", project,
            {"front_matter_baseline": {"orchestrator.agent.md": _front_matter_keys(project)}})
    out = tmp_path / ".github" / "agents"
    result = _emit(out, _doc(_TEMPLATE_HANDOFFS))
    manifest = {"project_name": "Acme", "framework": "copilot-vscode", "project_type": "x",
                "selected_archetypes": [], "components": []}
    build_team._write_run_log(manifest, result, out, {})
    log = json.loads((out / "references" / "build-log.json").read_text(encoding="utf-8"))
    assert log[fmb.VERSION_KEY] == fmb.BASELINE_VERSION
    assert "handoffs" not in log["front_matter_baseline"].get("orchestrator.agent.md", {})
    again = _emit(out, _doc(_TEMPLATE_HANDOFFS.replace("Locate files.", "Locate files and modules.")))
    assert "agent: acme-frontend" in (out / "orchestrator.agent.md").read_text(encoding="utf-8")
    assert not any(fmb.HANDOFF_KEPT_PREFIX in n for n in again.notices)


# --- the last line of defence -----------------------------------------------------------------------------

def test_a_dropped_hand_written_handoff_is_restored_and_announced():
    content, notices = fmb.restore_dropped_handoffs(_doc(_TEMPLATE_HANDOFFS), _doc(_PROJECT_HANDOFFS), None)
    keys = _front_matter_keys(content)
    agents = [a for (a, _), _ in fmb.handoff_items(keys["handoffs"])]
    assert agents == ["security", "navigator", "acme-frontend", "pricing"]
    assert any(n.startswith(fmb.HANDOFF_KEPT_PREFIX) and "'Frontend App' -> acme-frontend" in n for n in notices)
    assert any("wording NOT kept" in n and "security" in n for n in notices), "a reworded handoff is named"
    assert content.endswith(_BODY), "the body is untouched"


def test_a_handoff_agentteams_wrote_may_be_retired_by_the_template():
    baseline = {"handoffs": _front_matter_keys(_doc(_PROJECT_HANDOFFS))["handoffs"]}
    content, notices = fmb.restore_dropped_handoffs(_doc(_TEMPLATE_HANDOFFS), _doc(_PROJECT_HANDOFFS), baseline)
    assert "agent: acme-frontend" not in content
    assert any("the template retired" in n for n in notices)
    assert not any(n.startswith(fmb.HANDOFF_KEPT_PREFIX) for n in notices)


def test_nothing_happens_when_nothing_is_dropped():
    assert fmb.restore_dropped_handoffs(_doc(_PROJECT_HANDOFFS), _doc(_PROJECT_HANDOFFS), None) == (
        _doc(_PROJECT_HANDOFFS), [])


def test_a_whole_body_migration_that_takes_the_renders_front_matter_keeps_the_handoffs(tmp_path):
    """The legacy `content`-only migration replaces everything up to the last fence, front matter included."""
    legacy = _doc(_PROJECT_HANDOFFS, "<!-- AGENTTEAMS:BEGIN content v=1 -->\n## Rules\n\nold\n"
                                     "<!-- AGENTTEAMS:END content -->\n")
    target = _deploy(tmp_path, legacy, {})
    result = _emit(tmp_path, _doc(_TEMPLATE_HANDOFFS))
    written = target.read_text(encoding="utf-8")
    assert "agent: acme-frontend" in written and "agent: pricing" in written
    assert any(fmb.HANDOFF_KEPT_PREFIX in n for n in result.notices)


def test_fleet_reviews_a_restored_handoff(tmp_path):
    out = f"    Notice: orchestrator.agent.md: {fmb.HANDOFF_KEPT_PREFIX}(s) the update would have dropped: x\n"
    cls = fleet._classify(tmp_path, "HEAD", "", out)
    assert cls["handoffs_kept"] and cls["shrink_notices"] == []
