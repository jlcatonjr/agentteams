"""Tests for agentteams.research.verify — no live LLM call anywhere; chat_fn is always a mock."""

from __future__ import annotations

import asyncio
import json

import pytest

from agentteams.research.verify import (
    Claim,
    Verdict,
    _supported_by_evidence,
    audit_claims,
    extract_claims,
    revise,
)

# --- _supported_by_evidence: the deterministic anti-fabrication backstop --------------------


def test_supported_by_evidence_true_when_correction_terms_in_evidence() -> None:
    assert _supported_by_evidence(
        correction="The capital is Paris.",
        claim="The capital is Berlin.",
        evidence="Multiple sources confirm the capital city is Paris.",
    )


def test_supported_by_evidence_false_when_correction_terms_absent() -> None:
    assert not _supported_by_evidence(
        correction="The capital is Madrid.",
        claim="The capital is Berlin.",
        evidence="Multiple sources confirm the capital city is Paris.",
    )


def test_supported_by_evidence_excludes_claim_terms_from_requirement() -> None:
    """A correction that merely repeats the claim's own vocabulary, with no genuinely new
    evidence-backed term, must not pass -- this is why the signature takes `claim` as a THIRD,
    separate argument rather than folding it into `evidence`."""
    assert not _supported_by_evidence(
        correction="The capital is Berlin, not Munich.",
        claim="The capital is Berlin.",
        evidence="Random unrelated evidence with no city names at all whatsoever.",
    )


def test_supported_by_evidence_false_on_empty_required_terms() -> None:
    assert not _supported_by_evidence(correction="", claim="x", evidence="anything")


# --- mock ChatFn fixtures ---------------------------------------------------------------------


def _make_json_chat_fn(response_by_call_index: list[str]):
    calls = []

    async def chat_fn(messages, *, want_json=False):
        calls.append({"messages": messages, "want_json": want_json})
        return response_by_call_index[len(calls) - 1]

    chat_fn.calls = calls
    return chat_fn


# --- extract_claims ----------------------------------------------------------------------------


def test_extract_claims_parses_clean_json() -> None:
    chat_fn = _make_json_chat_fn([json.dumps({"claims": ["The sky is blue.", "Water is wet."]})])
    claims = asyncio.run(extract_claims("some text", chat_fn))
    assert [c.text for c in claims] == ["The sky is blue.", "Water is wet."]
    assert chat_fn.calls[0]["want_json"] is True


def test_extract_claims_calls_chat_fn_with_text_as_user_content() -> None:
    chat_fn = _make_json_chat_fn([json.dumps({"claims": []})])
    asyncio.run(extract_claims("the input text", chat_fn))
    user_messages = [m for m in chat_fn.calls[0]["messages"] if m["role"] == "user"]
    assert any("the input text" in m["content"] for m in user_messages)


def test_extract_claims_degrades_on_markdown_fenced_json() -> None:
    """The degraded path: a mock chat_fn that IGNORES want_json and wraps its JSON in a markdown
    fence, exactly what small/local models commonly do."""
    fenced = "Here you go:\n```json\n" + json.dumps({"claims": ["A fenced claim."]}) + "\n```"
    chat_fn = _make_json_chat_fn([fenced])
    claims = asyncio.run(extract_claims("text", chat_fn))
    assert [c.text for c in claims] == ["A fenced claim."]


def test_extract_claims_degrades_on_prose_wrapped_json() -> None:
    prose = 'Sure! Here is the JSON you asked for: {"claims": ["A prose-wrapped claim."]} Hope that helps!'
    chat_fn = _make_json_chat_fn([prose])
    claims = asyncio.run(extract_claims("text", chat_fn))
    assert [c.text for c in claims] == ["A prose-wrapped claim."]


def test_extract_claims_empty_on_total_garbage() -> None:
    chat_fn = _make_json_chat_fn(["not json at all, sorry"])
    claims = asyncio.run(extract_claims("text", chat_fn))
    assert claims == []


# --- audit_claims --------------------------------------------------------------------------


def test_audit_claims_skips_claims_with_no_evidence() -> None:
    chat_fn = _make_json_chat_fn([])
    claims = [Claim(text="unbacked claim")]
    verdicts = asyncio.run(audit_claims(claims, {}, chat_fn, lens="adversarial"))
    assert verdicts == []
    assert chat_fn.calls == []


def test_audit_claims_per_claim_evidence_never_pooled() -> None:
    """Two claims, each with its OWN distinct evidence string -- confirm each chat_fn call sees
    only its own claim's evidence, never the other claim's."""
    claims = [Claim(text="claim A"), Claim(text="claim B")]
    evidence = {"claim A": "evidence about A only", "claim B": "evidence about B only"}
    chat_fn = _make_json_chat_fn(
        [json.dumps({"contradicted": False}), json.dumps({"contradicted": False})]
    )
    asyncio.run(audit_claims(claims, evidence, chat_fn, lens="adversarial"))
    assert len(chat_fn.calls) == 2
    first_user = next(m["content"] for m in chat_fn.calls[0]["messages"] if m["role"] == "user")
    second_user = next(m["content"] for m in chat_fn.calls[1]["messages"] if m["role"] == "user")
    assert "evidence about A only" in first_user and "evidence about B only" not in first_user
    assert "evidence about B only" in second_user and "evidence about A only" not in second_user


def test_audit_claims_survived_when_not_contradicted() -> None:
    claims = [Claim(text="claim")]
    chat_fn = _make_json_chat_fn([json.dumps({"contradicted": False})])
    verdicts = asyncio.run(audit_claims(claims, {"claim": "evidence"}, chat_fn))
    assert verdicts[0].status == "survived"


def test_audit_claims_refuted_when_contradicted_and_correction_supported() -> None:
    claims = [Claim(text="The capital is Berlin.")]
    evidence = {"The capital is Berlin.": "Official records show the capital city is Paris."}
    chat_fn = _make_json_chat_fn(
        [json.dumps({"contradicted": True, "correction": "The capital is Paris."})]
    )
    verdicts = asyncio.run(audit_claims(claims, evidence, chat_fn))
    assert verdicts[0].status == "refuted"
    assert verdicts[0].correction == "The capital is Paris."


def test_audit_claims_backstop_downgrades_unsupported_correction() -> None:
    """The deterministic backstop overriding an LLM: even though the model SAYS contradicted with
    a correction, if that correction doesn't derive from the claim's own evidence, the verdict
    downgrades back to survived rather than trusting the model at face value."""
    claims = [Claim(text="The capital is Berlin.")]
    evidence = {"The capital is Berlin.": "This evidence says nothing about any city at all."}
    chat_fn = _make_json_chat_fn(
        [json.dumps({"contradicted": True, "correction": "The capital is Madrid."})]
    )
    verdicts = asyncio.run(audit_claims(claims, evidence, chat_fn))
    assert verdicts[0].status == "survived"


def test_audit_claims_lens_selects_different_instruction() -> None:
    claims = [Claim(text="claim")]
    chat_fn = _make_json_chat_fn([json.dumps({"contradicted": False})])
    asyncio.run(audit_claims(claims, {"claim": "evidence"}, chat_fn, lens="conflict"))
    system_msg = next(m["content"] for m in chat_fn.calls[0]["messages"] if m["role"] == "system")
    assert "already established" in system_msg.lower()


# --- revise ----------------------------------------------------------------------------------


def test_revise_returns_original_unchanged_when_nothing_refuted() -> None:
    chat_fn = _make_json_chat_fn([])
    verdicts = [Verdict(claim=Claim(text="x"), status="survived")]
    result = asyncio.run(revise("original text", verdicts, chat_fn))
    assert result == "original text"
    assert chat_fn.calls == []


def test_revise_calls_chat_fn_with_corrections_when_refuted() -> None:
    chat_fn = _make_json_chat_fn(["original text, corrected"])
    verdicts = [
        Verdict(claim=Claim(text="wrong claim"), status="refuted", correction="right fact")
    ]
    result = asyncio.run(revise("original text", verdicts, chat_fn))
    assert result == "original text, corrected"
    user_msg = next(m["content"] for m in chat_fn.calls[0]["messages"] if m["role"] == "user")
    assert "wrong claim" in user_msg and "right fact" in user_msg


def test_revise_falls_back_to_original_on_empty_response() -> None:
    chat_fn = _make_json_chat_fn(["   "])
    verdicts = [Verdict(claim=Claim(text="x"), status="refuted", correction="y")]
    result = asyncio.run(revise("original text", verdicts, chat_fn))
    assert result == "original text"
