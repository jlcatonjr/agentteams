"""test_cli_reference_flag_parity.py — the published CLI Reference must document every flag.

The 2026-08-06 staleness audit found `docs_src/cli-reference.md` opening with

    All flags for the `agentteams` command (entry point: `build_team.py`).

while documenting 71 of 90. The missing 19 were not obscure: `--pin-templates` is a security
feature (S4.6), and `--reconcile-front-matter` gates a capability-grant change (C-3). A reader
taking the page at its word would conclude neither exists.

The audit's finding was not "19 flags are missing." It was that **every documentation surface
with a standing check was current and every surface without one had drifted**. `agentteams.1`
covers the same 90 flags and was complete, because `ci.yml` regenerates and diffs it. This
file is the check the prose surface never had.

**Why a test and not a generator.** The man page can be generated because groff needs only the
help string. The CLI Reference carries semantics, worked examples and option-pair interactions
that no generator produces. So the prose stays authored and the *coverage* is mechanised: a
new flag must be written up, or the build fails.

**The ratchet is empty, and that is the point.** It exists so a deliberate future exemption has
a recorded home with a reason attached, following `LENGTH_ALLOWLIST`, `_UNCHECKED_SCHEMAS` and
`_UNDOCUMENTED`. It seeded at zero only because the 19 sections were written first — seeding it
at 19 would have enshrined the debt in the instrument meant to retire it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams.cli.parser import _build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_REFERENCE = REPO_ROOT / "docs_src" / "cli-reference.md"
MAN_PAGE = REPO_ROOT / "agentteams.1"

#: Flags deliberately absent from the published reference. EMPTY, and must stay that way
#: unless someone records a reason. Never add an entry to make a failure go away — the
#: failure means a flag shipped undocumented, which is the defect this file exists to catch.
_UNDOCUMENTED_FLAGS: frozenset[str] = frozenset()

#: `--help` is argparse's own and is not a documented feature of the tool.
_NOT_A_FEATURE: frozenset[str] = frozenset({"--help"})


def _parser_flags() -> set[str]:
    """Every long option the parser actually defines — the authority."""
    return {
        opt
        for action in _build_parser()._actions
        for opt in action.option_strings
        if opt.startswith("--")
    } - _NOT_A_FEATURE


def _flags_mentioned_in(path: Path) -> set[str]:
    return set(re.findall(r"(--[a-z0-9][a-z0-9\-]*)", path.read_text(encoding="utf-8")))


def test_cli_reference_documents_every_flag() -> None:
    """The ratchet."""
    missing = sorted(_parser_flags() - _flags_mentioned_in(CLI_REFERENCE) - _UNDOCUMENTED_FLAGS)
    assert not missing, (
        f"{len(missing)} flag(s) exist in the parser but are absent from "
        f"docs_src/cli-reference.md: {missing}. The page states it documents ALL flags, so a "
        "missing one is a false claim, not just a gap. Add a `### `--flag`` section — or add "
        "the flag to _UNDOCUMENTED_FLAGS with a reason."
    )


def test_the_ratchet_has_no_stale_entries() -> None:
    """A flag that gained documentation must leave the baseline, or it hides the next gap."""
    documented = _flags_mentioned_in(CLI_REFERENCE)
    stale = sorted(f for f in _UNDOCUMENTED_FLAGS if f in documented)
    assert not stale, f"these are documented now and should leave _UNDOCUMENTED_FLAGS: {stale}"


def test_the_reference_claims_no_flag_the_parser_lacks() -> None:
    """The other direction: a documented flag that no longer exists sends readers at nothing.

    Scoped to `### `--flag`` headings. Prose legitimately mentions non-flags (`git reset
    --hard` appears in the `--revert-migration` section), and matching bare prose would make
    this fail on a correct document — which is how a check gets deleted rather than fixed.
    """
    headings = set(re.findall(r"^###\s+`(--[a-z0-9\-]+)", CLI_REFERENCE.read_text(encoding="utf-8"), re.M))
    phantom = sorted(headings - _parser_flags() - _NOT_A_FEATURE)
    assert not phantom, (
        f"cli-reference.md has a section for flag(s) the parser does not define: {phantom}. "
        "Either the flag was removed and its section should go, or it was renamed."
    )


def test_the_measurement_is_not_vacuous() -> None:
    """A regression in either extractor would empty both sides and pass forever."""
    flags = _parser_flags()
    assert len(flags) > 50, f"only {len(flags)} parser flags found — the introspection regressed"
    mentioned = _flags_mentioned_in(CLI_REFERENCE)
    assert len(mentioned) > 50, f"only {len(mentioned)} flags found in the doc — the scan regressed"


def test_the_man_page_remains_the_complete_surface() -> None:
    """`agentteams.1` is generated, CI-diffed, and must cover everything.

    Pinned here so the two surfaces cannot silently diverge: if the man page ever falls
    behind, the CLI Reference's completeness claim loses the thing that backstops it.
    """
    missing = sorted(_parser_flags() - _flags_mentioned_in(MAN_PAGE))
    assert not missing, (
        f"agentteams.1 is missing {missing} — regenerate with `python -m agentteams.man > agentteams.1`"
    )


@pytest.mark.parametrize(
    "flag",
    ["--pin-templates", "--reconcile-front-matter", "--query-code", "--goose-source"],
)
def test_specific_flags_from_the_audit_are_documented(flag: str) -> None:
    """Regression pins for four of the 19 the audit found missing.

    Named individually because a future edit could satisfy the aggregate check while dropping
    one of these — and these are the ones with security or capability consequences.
    """
    assert flag in _flags_mentioned_in(CLI_REFERENCE), f"{flag} lost its documentation again"
