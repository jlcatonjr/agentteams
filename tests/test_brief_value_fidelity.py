"""test_brief_value_fidelity.py — deployed teams must carry the brief's values, not defaults.

Bleed detector (wrong-value-bleed-root-cause plan, step 7; remediation-log HIGH row
2026-08-13). The incident: renders during a window where the un-versioned self brief
lacked ``primary_output_dir``/``reference_db_path`` wrote the analyze-phase defaults
(``src/``, ``N/A - no citation database…``) into deployed agent files, and merge
semantics never healed the un-versioned surfaces. This test makes that state visible:
it fails while any deployed self-team file still carries a resolved default that the
live brief overrides.

Design constraints inherited from the plan's r2 adversarial audit:
- Line-anchored resolved-value patterns, NOT free-text grep — ``content-enricher``'s
  body legitimately quotes the fallback string as an example of what to replace.
- Fence balance asserted via real anchored marker parsing, never substring counts
  (substring counting produced a false 3/1 imbalance finding on prose mentions).
- Explicit ``pathlib`` iteration, never ripgrep-style directory search — the surfaces
  under test are gitignored and silently skipped by ignore-aware tools.
- Similar-length substitution is caught because VALUES are compared, not lengths.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BRIEF = REPO / ".github/agents/_build-description.json"

# The resolved-value line shapes as rendered (e.g. navigator.agent.md).
_PRIMARY_DEFAULT = re.compile(r"^\*\*Primary output directory:\*\*\s+`src/`", re.M)
_REFDB_DEFAULT = re.compile(
    r"^\*\*Reference/dependency database:\*\*\s+`N/A - no citation database", re.M
)
_MARK_BEGIN = re.compile(r"^<!--\s*AGENTTEAMS(?:-BRIDGE)?:BEGIN\s+\S+", re.M)
_MARK_END = re.compile(r"^<!--\s*AGENTTEAMS(?:-BRIDGE)?:END\s+\S+", re.M)

SURFACES = {
    "copilot-vscode": (".github/agents", "*.agent.md"),
    "claude": (".claude/agents", "*.md"),
}


def _brief() -> dict:
    if not BRIEF.exists():
        pytest.skip("self brief absent (gitignored source team not present in this clone)")
    return json.loads(BRIEF.read_text(encoding="utf-8"))


def _surface_files(rel: str, glob: str) -> list[pathlib.Path]:
    root = REPO / rel
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob(glob) if p.is_file())


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_deployed_files_carry_brief_values_not_defaults(surface: str) -> None:
    """A brief that sets a value must win over the analyze-phase default, everywhere."""
    brief = _brief()
    rel, glob = SURFACES[surface]
    files = _surface_files(rel, glob)
    if not files:
        pytest.skip(f"{rel} not present")

    offenders: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        if brief.get("primary_output_dir") not in (None, "", "src/") and _PRIMARY_DEFAULT.search(text):
            offenders.append(f"{f.name}: resolved default `src/` vs brief "
                             f"primary_output_dir={brief['primary_output_dir']!r}")
        if brief.get("reference_db_path") and _REFDB_DEFAULT.search(text):
            offenders.append(f"{f.name}: citation-db fallback vs brief "
                             f"reference_db_path={brief['reference_db_path']!r}")
    assert not offenders, (
        f"[{surface}] wrong-value bleed present (brief value replaced by default):\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_deployed_files_are_fence_balanced(surface: str) -> None:
    """Anchored marker parse; a strip/merge may empty a fence but never unbalance one."""
    _brief()  # same skip condition: these surfaces travel with the self team
    rel, glob = SURFACES[surface]
    files = _surface_files(rel, glob)
    if not files:
        pytest.skip(f"{rel} not present")

    unbalanced = [
        f"{f.name}: {b} BEGIN / {e} END"
        for f in files
        for text in [f.read_text(encoding="utf-8", errors="replace")]
        for b, e in [(len(_MARK_BEGIN.findall(text)), len(_MARK_END.findall(text)))]
        if b != e
    ]
    assert not unbalanced, f"[{surface}] unbalanced fences:\n  " + "\n  ".join(unbalanced)


# Instructions files (CLAUDE.md / copilot-instructions.md) carry brief values in shapes
# the per-agent patterns miss — the 2026-08-13 projection incident reproduced the bleed
# there while the agent-file tests stayed green. These anchored shapes close that gap.
_INSTR_FALLBACK_SHAPES = (
    re.compile(r"verifiable in `N/A - no citation database"),
    re.compile(r"no external dependencies in src/"),
)
INSTRUCTION_FILES = (".claude/CLAUDE.md", ".github/copilot-instructions.md", "CLAUDE.md")


def test_instructions_files_carry_brief_values_not_defaults() -> None:
    brief = _brief()
    if not brief.get("reference_db_path"):
        pytest.skip("brief sets no reference_db_path")
    offenders = [
        f"{rel}: matches {pat.pattern!r}"
        for rel in INSTRUCTION_FILES
        for p in [REPO / rel]
        if p.exists()
        for text in [p.read_text(encoding="utf-8", errors="replace")]
        for pat in _INSTR_FALLBACK_SHAPES
        if pat.search(text)
    ]
    assert not offenders, "instructions-file bleed:\n  " + "\n  ".join(offenders)
