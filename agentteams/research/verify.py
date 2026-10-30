"""Claim extraction and dual-lens fact verification.

Generalizes a pattern from LingoFriend (``knowledge/team.py``/``roles.py``/``live_research.py``):
extract checkable claims from generated text, gather/filter evidence per-claim (never a pooled
blob), run a dual-lens critique with a deterministic anti-fabrication backstop, and produce a
minimal-edit revision.

Unlike the LingoFriend origin, this module has no hardcoded model client. Every function that
needs an LLM call takes a caller-supplied :data:`ChatFn` — this module never imports or assumes
any particular chat backend.

Honesty ceiling: verdict labels are always ``"survived"`` or ``"refuted"`` — never
``"verified"``/``"proven"``/``"correct"``. "Survived" means available evidence did not contradict
the claim; it does not mean the claim is true.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Protocol, Sequence


class ChatFn(Protocol):
    """The minimal chat-completion protocol a caller supplies.

    ``messages`` follows the common ``[{"role": ..., "content": ...}, ...]`` shape. When
    ``want_json`` is True, the callable SHOULD request a structured/JSON-constrained response
    from its underlying model if the backend supports it (e.g. an ``response_format``/``fmt``
    equivalent) — but every function in this module that accepts a ``ChatFn`` must also degrade
    correctly if the callable ignores ``want_json`` and returns markdown-fenced, truncated, or
    prose-wrapped JSON. Small/local models frequently do exactly that; this module's own JSON
    extraction (:func:`_extract_json`) is tolerant of it by design — do not rely on
    ``want_json`` being honored.
    """

    def __call__(
        self, messages: list[dict[str, str]], *, want_json: bool = False
    ) -> Awaitable[str]: ...


# A plain function matching the same call shape is also accepted anywhere ChatFn is — Protocol
# structural typing covers that automatically; this alias exists only for readability in type
# hints elsewhere in this module.
ChatCallable = Callable[..., Awaitable[str]]


@dataclass
class Claim:
    text: str


@dataclass
class Verdict:
    claim: Claim
    status: Literal["survived", "refuted"]
    correction: str | None = None
    lens: Literal["adversarial", "conflict"] = "adversarial"


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> dict | list | None:
    """Tolerant JSON extraction: try the raw text first, then a fenced code block, then the
    widest balanced-looking ``{...}``/``[...]`` span. Returns None on total failure — never
    raises. This tolerance is what lets every function below stay correct even when a caller's
    ``ChatFn`` doesn't honor ``want_json``."""
    for candidate in (text, _JSON_FENCE.search(text).group(1) if _JSON_FENCE.search(text) else None):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:  # CH-24: named type — json.loads's own documented failure
            pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:  # CH-24: named type
                pass
    return None


def extract_json(text: str) -> dict | list | None:
    """Public form of the tolerant JSON extraction every function here uses on a ``ChatFn`` reply:
    raw JSON, a fenced block, or the widest balanced span; ``None`` on total failure, never raises.
    For callers composing their own prompts over a ``ChatFn`` (e.g. :mod:`agentteams.research.briefing`)."""
    return _extract_json(text)


_EXTRACT_CLAIMS_INSTRUCTION = (
    "Extract every discrete, checkable factual claim from the text below. Restate ONLY what the "
    "text literally asserts — add no dates, tenses, qualifiers, or outside knowledge; do not "
    "correct, complete, or improve a claim even if you believe it is wrong or incomplete. If the "
    "text asks a question rather than asserting something, do not answer it — extract nothing for "
    'that part. Respond with a JSON object: {"claims": ["claim text", ...]}. Return an empty list '
    "if there are no checkable claims."
)


async def extract_claims(text: str, chat_fn: ChatFn) -> list[Claim]:
    """Extract discrete, checkable claims from ``text`` via ``chat_fn``.

    The "restate, don't invent" instruction above exists because an earlier, less constrained
    version of this prompt fabricated claims by applying the model's own stale training knowledge
    instead of restating the input text — a live-discovered failure mode of this pattern's
    LingoFriend origin, not a hypothetical one.
    """
    raw = await chat_fn(
        [
            {"role": "system", "content": _EXTRACT_CLAIMS_INSTRUCTION},
            {"role": "user", "content": text},
        ],
        want_json=True,
    )
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return []
    claims = parsed.get("claims", [])
    if not isinstance(claims, list):
        return []
    return [Claim(text=str(c)) for c in claims if str(c).strip()]


_LENS_INSTRUCTIONS = {
    "adversarial": (
        "You are an adversarial fact-checker. Given a CLAIM and EVIDENCE, decide whether the "
        "evidence CONTRADICTS the claim. Default to the claim surviving unless the evidence "
        "clearly contradicts it — absence of confirming evidence is not a contradiction."
    ),
    "conflict": (
        "You are checking for internal consistency. Given a CLAIM and EVIDENCE drawn from what "
        "was already established earlier, decide whether the evidence CONTRADICTS the claim — "
        "i.e. whether the claim conflicts with something already stated as fact. Default to the "
        "claim surviving unless the evidence clearly contradicts it."
    ),
}

_AUDIT_INSTRUCTION_SUFFIX = (
    ' Respond with a JSON object: {"contradicted": true|false, "correction": "..." or null}. '
    "If contradicted, `correction` must be a short, hedged, source-attributed correction — never "
    "invent detail beyond what the evidence states."
)


async def audit_claims(
    claims: list[Claim],
    evidence_by_claim: dict[str, str],
    chat_fn: ChatFn,
    lens: Literal["adversarial", "conflict"] = "adversarial",
) -> list[Verdict]:
    """Audit each claim against ITS OWN evidence only — never a pooled blob across all claims.

    ``evidence_by_claim`` maps claim text to that claim's own evidence string; a claim with no
    entry (or an empty one) is skipped for this lens, not audited against unrelated evidence. This
    per-claim pairing is a deliberate, audited fix in this pattern's origin: pooling all evidence
    for every claim let evidence support one claim be mistakenly treated as also relevant to an
    unrelated claim. Because the map is keyed by text, two claims with the same text share one
    evidence string; when identical texts come from different sources, use
    :func:`audit_claim_pairs`, which pairs each claim object with its own evidence.

    Every ``contradicted`` verdict is additionally checked against :func:`_supported_by_evidence`
    — a deterministic, non-LLM backstop — before being accepted; an LLM-proposed correction that
    doesn't actually derive from the claim's own evidence is downgraded back to ``"survived"``
    rather than trusted at face value.

    Args:
        claims: The claims to audit, in order.
        evidence_by_claim: Claim text to that claim's own evidence string.
        chat_fn: The caller's chat callable (see :class:`ChatFn`).
        lens: ``"adversarial"`` (fresh evidence) or ``"conflict"`` (already-established facts).

    Returns:
        One :class:`Verdict` per claim that had non-empty evidence, in input order.

    Raises:
        KeyError: If ``lens`` is not a known lens.
    """
    pairs = [(claim, evidence_by_claim.get(claim.text, "")) for claim in claims]
    return await audit_claim_pairs(pairs, chat_fn, lens=lens)


async def audit_claim_pairs(
    pairs: Sequence[tuple[Claim, str]],
    chat_fn: ChatFn,
    lens: Literal["adversarial", "conflict"] = "adversarial",
) -> list[Verdict]:
    """Audit each ``(claim, evidence)`` pair against that pair's evidence only.

    The pairing form of :func:`audit_claims`: each claim object carries its own evidence, so two
    claims with identical text from different sources are each audited against their own page,
    never one against the other's. A pair with empty evidence is skipped for this lens. Each
    returned :class:`Verdict` holds the very ``Claim`` object from its pair, so callers map
    verdicts back by identity (``verdict.claim is claim``) — ``Claim`` compares by value, so
    equality would conflate identical texts.

    Every ``contradicted`` verdict passes the deterministic :func:`_supported_by_evidence`
    backstop before it is accepted as ``"refuted"``; otherwise it is downgraded to ``"survived"``.

    Args:
        pairs: ``(claim, evidence)`` tuples, in order.
        chat_fn: The caller's chat callable (see :class:`ChatFn`).
        lens: ``"adversarial"`` (fresh evidence) or ``"conflict"`` (already-established facts).

    Returns:
        One :class:`Verdict` per pair with non-empty evidence, in input order.

    Raises:
        KeyError: If ``lens`` is not a known lens.
    """
    verdicts: list[Verdict] = []
    instruction = _LENS_INSTRUCTIONS[lens] + _AUDIT_INSTRUCTION_SUFFIX
    for claim, evidence in pairs:
        if not evidence.strip():
            continue
        raw = await chat_fn(
            [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": f"CLAIM: {claim.text}\n\nEVIDENCE:\n{evidence}",
                },
            ],
            want_json=True,
        )
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict) or not parsed.get("contradicted"):
            verdicts.append(Verdict(claim=claim, status="survived", lens=lens))
            continue
        correction = str(parsed.get("correction") or "").strip()
        if correction and _supported_by_evidence(correction, claim.text, evidence):
            verdicts.append(
                Verdict(claim=claim, status="refuted", correction=correction, lens=lens)
            )
        else:
            # An LLM claimed a contradiction but either gave no correction or one that doesn't
            # derive from the claim's own evidence — the deterministic backstop overrides it.
            verdicts.append(Verdict(claim=claim, status="survived", lens=lens))
    return verdicts


_ACRONYM_RE = re.compile(r"^[A-Z]{2,6}$")


_MIN_STEM = 4
# The past-tense and progressive forms fold to one shared verb-form key, never to the bare stem:
# "united" and "uniting" match each other, but neither matches "unit"/"units". Folding them to the
# bare stem would let an evidence word ("united") vouch for an unrelated correction word ("units").
_VERB_FORM = "~v"


def _fold(term: str) -> str:
    """Conservative English inflection folding for the anti-fabrication backstop.

    Only purely alphabetic ASCII tokens fold, and never an acronym (tested on the original case);
    digits never fold ("1990s" stays distinct from "1990"). Rules, first match wins, each applied
    only when the folded stem keeps at least ``_MIN_STEM`` characters: ``sses→ss``, ``ies→y``,
    a trailing ``s`` (not after ``s``, ``u`` or ``i``), and ``ing``/``ed`` to a shared verb-form
    key. Folding only merges inflections of one word; it adds no synonyms.
    """
    if _ACRONYM_RE.match(term) or not (term.isascii() and term.isalpha()):
        return term.lower()
    t = term.lower()
    if t.endswith("sses"):
        stem = t[:-2]
    elif t.endswith("ies"):
        stem = t[:-3] + "y"
    elif t.endswith("s") and not t.endswith(("ss", "us", "is")):
        stem = t[:-1]
    elif t.endswith("ing") and len(t) - 3 >= _MIN_STEM:
        return t[:-3] + _VERB_FORM
    elif t.endswith("ed") and len(t) - 2 >= _MIN_STEM:
        return t[:-2] + _VERB_FORM
    else:
        return t
    return stem if len(stem) >= _MIN_STEM else t


def _content_terms(text: str) -> set[str]:
    return {
        _fold(t) for t in re.findall(r"\w+", text) if len(t) >= 4 or _ACRONYM_RE.match(t)
    }


def _supported_by_evidence(correction: str, claim: str, evidence: str) -> bool:
    """Deterministic, non-LLM anti-fabrication backstop.

    Every significant content word (length >= 4, or a short all-caps acronym) in ``correction``
    must also appear in ``evidence`` — EXCLUDING words already present in ``claim`` itself (a
    correction that just repeats the claim's own vocabulary proves nothing about whether the
    evidence actually supports it). Words are compared after :func:`_fold` merges inflections
    ("models" matches "model"); a word absent from the evidence in every form still fails. A
    correction with no qualifying content words at all is treated as unsupported (returns False)
    rather than vacuously true.
    """
    correction_terms = _content_terms(correction)
    claim_terms = _content_terms(claim)
    required = correction_terms - claim_terms
    if not required:
        return False
    evidence_terms = _content_terms(evidence)
    return required.issubset(evidence_terms)


_REVISE_INSTRUCTION = (
    "You will be given an ORIGINAL text and a list of CORRECTIONS. Produce a MINIMALLY EDITED "
    "version of the original: change only the specific spans the corrections identify as wrong, "
    "and copy everything else VERBATIM. Do not rewrite, rephrase, or add any detail not already "
    "present in the original text or the corrections — this is an edit, not a rewrite."
)


async def revise(original_text: str, verdicts: list[Verdict], chat_fn: ChatFn) -> str:
    """Produce a minimal-edit revision of ``original_text`` incorporating any ``refuted``
    verdicts' corrections. Returns ``original_text`` unchanged if no verdict refuted anything.

    "Minimal edit," not "rewrite naturally," is a deliberate, audited framing: an earlier
    "rewrite naturally" version of this prompt fabricated unstated detail while "smoothing" the
    text — a live-discovered failure mode of this pattern's LingoFriend origin.
    """
    corrections = [v for v in verdicts if v.status == "refuted" and v.correction]
    if not corrections:
        return original_text
    correction_list = "\n".join(f"- {v.claim.text} -> {v.correction}" for v in corrections)
    raw = await chat_fn(
        [
            {"role": "system", "content": _REVISE_INSTRUCTION},
            {
                "role": "user",
                "content": f"ORIGINAL:\n{original_text}\n\nCORRECTIONS:\n{correction_list}",
            },
        ],
        want_json=False,
    )
    revised = raw.strip()
    return revised if revised else original_text
