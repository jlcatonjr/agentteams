"""A directive scope is refused unless it names a benign task (IM item 05, G-24-1).

``scope_is_allowed`` was a stem denylist: any non-empty scope naming no governance root and no listed stem passed, so
``rewrite-history``, ``git-clean-fdx`` and ``publish-release`` were authorizable although C-4 says destructive scopes
are mechanically auto-refused. Operator rulings (2026-11-15): the directive gate is an allowlist of benign leading
verbs, with the denylist and trust-root checks still on top; the effect classifier gains only the high-precision
destructive stems, so existing decision rows like "sign-off" keep their class.
"""

from __future__ import annotations

import pytest

from agentteams.cli import effect_classifier as ec
from agentteams.cli import management_directives as md

IM_PROBES = ["erase-old-logs", "unlink-build-dir", "clobber-readme", "git-clean-fdx", "rewrite-history",
             "shred-cache", "chmod-777-repo", "publish-release", "sign-row", "rotate-key"]


@pytest.mark.parametrize("scope", IM_PROBES)
def test_every_im_probe_is_refused(scope):
    assert md.scope_is_allowed(scope) is False


@pytest.mark.parametrize("scope", [
    "review-vulnscan-handoff-20261101010101",   # the production directive scope (vulnscan/handoff.py)
    "draft-weekly-report", "draft-monthly-report", "refresh-readme", "format the sandbox docs",
    "summarize findings", "Reviewing the audit", "tests for parser", "lint docs_src", "audit_parser_flow",
])
def test_a_benign_leading_verb_is_allowed(scope):
    assert md.scope_is_allowed(scope) is True


@pytest.mark.parametrize("scope", ["frobnicate-the-repo", "x", "something-else", "please do it", "chown-repo",
                                   "-review", "  "])
def test_an_unknown_or_missing_leading_verb_is_refused(scope):
    assert md.scope_is_allowed(scope) is False


@pytest.mark.parametrize("scope", ["review-and-publish-release", "draft-release-notes-and-tag", "review then sign",
                                   "check-and-rotate-keys", "review-then-delete-stale-branches",
                                   "draft the constitution", "review security-approvers.txt", "review AGENTS.md"])
def test_a_benign_verb_does_not_launder_a_destructive_or_governance_tail(scope):
    assert md.scope_is_allowed(scope) is False


def test_the_docstring_no_longer_claims_a_denylist_is_the_whole_gate():
    doc = md.scope_is_allowed.__doc__ or ""
    assert "BENIGN_SCOPE_VERBS" in doc


# --- the effect classifier keeps its vocabulary, plus the precise stems ------------------------------------------

@pytest.mark.parametrize("scope", ["erase-old-logs", "unlink-build-dir", "clobber-readme", "git-clean-fdx",
                                   "rewrite-history", "shred-cache", "chmod-777-repo", "chown-repo"])
def test_a_precise_destructive_stem_makes_a_decision_row_relaxing(scope):
    assert md.scope_names_refused_effect(scope) is True
    assert ec._text_signal({"action_reviewed": scope, "verdict": "PASS"}, kind="decision") == ec.RELAXING


@pytest.mark.parametrize("scope", ["Operator sign-off: residual risk", "tag-release.sh fresh-clone hardening",
                                   "release/* branch ruleset + v* tag ruleset", "approve the docs build"])
def test_decision_rows_outside_the_precise_stems_keep_their_class(scope):
    # Operator ruling: the classifier does not take the allowlist or the false-positive-heavy stems.
    assert md.scope_names_refused_effect(scope) is False
    assert ec._text_signal({"action_reviewed": scope, "verdict": "PASS"}, kind="decision") == ec.NON_RELAXING
