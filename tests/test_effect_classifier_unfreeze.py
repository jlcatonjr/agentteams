"""Frozen surfaces, Rule 15: an unfreeze is constraint-relaxing, and the class is derived, never self-declared.

The rows are the frozen-surfaces design note's table (tmp/by-week/2026-W42/f3-drafts/design/frozen-surfaces-1.0.x.design.md,
"Rule 15: freeze versus unfreeze"). Before this change an unfreeze derived relaxing only when its author declared
``effect_relaxes``; now it derives relaxing on the text axis (the scope names an unfreeze) and on the target axis (a
write target under a frozen surface's owned path), and needs the operator's Ed25519 signature.
"""

from __future__ import annotations

import pytest

from agentteams.cli import effect_classifier as ec
from agentteams.cli.management_directives import scope_is_allowed

FROZEN = ec.frozen_owned_paths(["codex"])


def _decision(**cells: str) -> dict[str, str]:
    return {"verdict": "PASS", **cells}


def test_a_freeze_row_stays_ordinary():
    row = _decision(scope="freeze-surface codex", effect_targets=".codex/;AGENTS.md")
    # The frozen set in force before a freeze doesn't contain the surface being frozen.
    assert ec.derive_effect_class(row, kind="decision", frozen_paths=()) == ec.NON_RELAXING
    assert not ec.requires_operator_signature(row, kind="decision")


@pytest.mark.parametrize("row", [
    _decision(scope="unfreeze-surface codex"),                                   # scope text only
    _decision(scope="unfreeze-surface codex", effect_targets=".codex/;AGENTS.md"),  # targets, no effect_relaxes
    _decision(action_reviewed="agentteams --update --unfreeze codex"),           # the CLI spelling
    _decision(scope="render with the codex surface unfrozen"),
])
def test_an_unfreeze_derives_relaxing_on_the_text_axis_without_a_brief(row):
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING
    assert ec.is_unfreeze(row, kind="decision")
    assert ec.requires_operator_signature(row, kind="decision")


def test_an_honest_effect_relaxes_still_derives_relaxing():
    row = _decision(scope="unfreeze-surface codex", effect_relaxes="frozen_surfaces:codex")
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING
    assert ec.requires_operator_signature(row, kind="decision")


@pytest.mark.parametrize("target", [".codex/agents/x.toml", "./.codex/config.toml", "AGENTS.md", "agents.md",
                                    ".codex", ".agents/skills/a/SKILL.md"])
def test_an_out_of_vocabulary_render_into_a_frozen_path_is_caught_by_the_target_axis(target):
    row = _decision(scope="refresh-team-surfaces", effect_targets=target)
    assert ec.derive_effect_class(row, kind="decision") == ec.NON_RELAXING  # without the brief: not caught
    assert ec.derive_effect_class(row, kind="decision", frozen_paths=FROZEN) == ec.RELAXING
    assert ec.is_unfreeze(row, kind="decision", frozen_paths=FROZEN)
    assert ec.requires_operator_signature(row, kind="decision", frozen_paths=FROZEN)


@pytest.mark.parametrize("target", [".codexx/a", "docs/AGENTS.md", ".goose/recipes/a.yaml", "scripts/x.py"])
def test_a_target_outside_the_frozen_paths_is_unaffected(target):
    row = _decision(scope="refresh-team-surfaces", effect_targets=target)
    assert ec.derive_effect_class(row, kind="decision", frozen_paths=FROZEN) == ec.NON_RELAXING
    assert not ec.is_unfreeze(row, kind="decision", frozen_paths=FROZEN)


def test_a_self_declared_non_relaxing_unfreeze_is_refused():
    row = _decision(scope="unfreeze-surface codex", effect_class="non-relaxing")
    with pytest.raises(ec.EffectClassifierError):
        ec.classify_row(row, kind="decision")
    row = _decision(scope="refresh-team-surfaces", effect_targets=".codex/a", effect_class="non-relaxing")
    with pytest.raises(ec.EffectClassifierError):
        ec.classify_row(row, kind="decision", frozen_paths=FROZEN)


def test_a_directive_can_never_authorize_an_unfreeze():
    assert not scope_is_allowed("unfreeze-surface-codex")
    assert not scope_is_allowed("render --unfreeze codex")
    assert not scope_is_allowed("codex-unfrozen-render")
    assert scope_is_allowed("refresh-readme")  # unchanged for benign scopes
    row = {"task_scope": "unfreeze-surface-codex"}
    assert ec.derive_effect_class(row, kind="directive") == ec.RELAXING


def test_owned_paths_follow_the_shared_contract():
    assert set(ec.frozen_owned_paths(["goose"])) == {
        ".goose/", "AGENTS.md", ".goosehints", "scripts/goose-run-resilient.py",
        "scripts/goose-openrouter-route-proxy.py", "scripts/goose-coordination-mcp.py"}
    for surface in ("codex", "goose", "agents-md"):
        assert "AGENTS.md" in ec.frozen_owned_paths([surface])
    assert ec.frozen_owned_paths(None) == () and ec.frozen_owned_paths([]) == ()
    both = ec.frozen_owned_paths(["codex", "agents-md"])
    assert len(both) == len(set(both))


@pytest.mark.parametrize("bad", [["codex", "codex"], ["kodex"], "codex", [1], {"codex": True}])
def test_a_bad_frozen_surfaces_value_is_refused_not_ignored(bad):
    with pytest.raises(ValueError):
        ec.frozen_owned_paths(bad)
