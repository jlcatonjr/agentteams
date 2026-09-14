"""effect_classifier.py — derive whether an authorizing row RELAXES a constraint.

Workstream D (the keystone) of the exception-governance hardening. It closes the P1
defect: *the relaxing class must not be a self-declared flag.* A self-flag is Gap A one
layer up — an author (or a compromised runner) that sets ``relaxes_constraint=no`` would
route a genuinely relaxing authorization down the ordinary path, exactly as renaming an
action-id evaded the HALT scan.

**What "relaxing" means here.** A row is *relaxing* when its **effect** widens authority
rather than exercising authority already held: it grants a capability (widens C-3), targets
a governance or trust root (the constitution, an invariant, a grant, the roster, a signing
key, the security-decision log, a waiver, the integrity manifest, or the Ed25519
verify-key store), clears a destructive / bulk / cross-repository action (C-5), or pierces
/ retracts an existing constraint such as a ``@security`` HALT (C-2). Everything else —
an authorizing row that merely exercises an ordinary, benign, already-bounded authority —
is *non-relaxing*, and the ~100 existing HMAC-signed decision rows stay non-relaxing with
zero migration.

**Derived, never trusted.** The class is derived from the **union** of two independent axes —
the structured effect fields AND the row's scope/action/verdict text — combined fail-closed so
neither can suppress the other (a benign structured cell can never silence a relaxing verdict).
A row MAY *also* carry a self-declared ``effect_class`` column, but it is only ever **checked
against** the derived class, never used in its place, and only a *dangerous* divergence refuses
(:class:`EffectClassifierError`): declared non-relaxing on a row not proven benign, or declared
relaxing on a row the effect proves benign. A merely conservative declaration (declared
relaxing, derived indeterminate) is accepted — both take the elevated path. When no axis decides,
the class is :data:`INDETERMINATE`, which every caller treats **as relaxing**.

**The fail-closed ceiling is coverage-bounded, not universal (re-review R1).** The classifier
*proves* RELAXING from the denylist + governance vocabulary and the structured axes; it concludes
NON_RELAXING for a non-empty scope by the ABSENCE of a relaxing signal, and defaults to
INDETERMINATE only for an empty/undecidable scope. So a relaxing effect stated in
out-of-vocabulary text with no structured ``effect_*`` fields is NOT detected on the text axis —
a deliberate tradeoff that keeps the ~100 legacy HMAC rows migration-free, safe only under the
keyless threat model (writing any authorizing row already needs signed-write capability). The
durable closure is the WS-B/C requirement that new authorizing rows declare structured effect.
See :func:`_text_signal`.

**Not a denylist extension.** This is a *separate marker*. Its destructive/governance vocabulary
is reused, not re-listed: verb scopes go through :func:`management_directives.scope_is_allowed`
and trust-root paths through :func:`governance_targets.target_is_governance_root` — the latter
the single shared source both this classifier and the directive denylist import, so the two
paths can never drift (audit m5/Q1). It never itself refuses an ordinary action; it only tells
the caller which class a row is in, so the caller can require the elevated (asymmetric-signed)
path for a relaxing row and leave a non-relaxing row on its current path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentteams.cli.governance_targets import target_is_governance_root
from agentteams.cli.management_directives import scope_is_allowed

#: The three derived classes. ``INDETERMINATE`` is not a third policy outcome — every caller
#: collapses it to "treat as relaxing" (fail-closed) — but it is tracked distinctly so the
#: caller can log *why* a row landed in the elevated path (a derivable relaxing effect vs an
#: undecidable one that defaulted there).
RELAXING = "relaxing"
NON_RELAXING = "non-relaxing"
INDETERMINATE = "indeterminate"

#: Verdict (decision-log) that retracts a HALT. Mirrors ``decision_log._HALT_RETRACTION_VERDICT``
#: (kept as a literal here rather than imported to avoid a cli→cli import cycle; the two are
#: pinned equal by ``tests/test_effect_classifier.py``). A retraction pierces a constraint by
#: definition, so it is relaxing regardless of any other field.
_HALT_RETRACTION_VERDICT = "HALT-RETRACTED"

class EffectClassifierError(RuntimeError):
    """Raised when a row's self-declared ``effect_class`` disagrees with its derived class.

    A hard refusal, by design: the derived class is authoritative, and a disagreeing
    self-declaration is either an evasion attempt (declared benign, derived relaxing) or a
    defect in the author's model (declared relaxing, derived benign). Either way the row must
    not proceed on an unresolved contradiction about whether it relaxes a constraint.
    """


@dataclass(frozen=True)
class EffectProfile:
    """The structured effect a row asserts, from which its class is derived.

    Every field is optional; a row that declares none yields an all-empty profile, which forces
    the derivation to fall back to the row's scope/verdict text (and, failing that, to
    :data:`INDETERMINATE`). When a field IS present it is authoritative for its axis.
    """

    grants_capabilities: tuple[str, ...] = ()
    write_targets: tuple[str, ...] = ()
    relaxes: tuple[str, ...] = ()
    destructive: bool = False
    cross_repo: bool = False
    bulk: bool = False
    #: True when the row declared at least one structured effect axis (so the derivation knows
    #: whether an all-empty profile means "declared benign" vs "declared nothing").
    declared: bool = field(default=False)

    def is_empty(self) -> bool:
        """Return True when no structured effect axis was declared."""
        return not (
            self.grants_capabilities
            or self.write_targets
            or self.relaxes
            or self.destructive
            or self.cross_repo
            or self.bulk
        )


def _split_list(raw: str) -> tuple[str, ...]:
    """Split a semicolon/comma-separated cell into a tuple of non-empty tokens."""
    if not raw:
        return ()
    parts = re.split(r"[;,]", raw)
    return tuple(p.strip() for p in parts if p.strip())


def _truthy(raw: str) -> bool:
    """Return True for the affirmative spellings used in effect flag cells."""
    return (raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def effect_profile_from_row(row: dict[str, str]) -> EffectProfile:
    """Read a row's declared structured effect fields into an :class:`EffectProfile`.

    Recognized optional columns (all absent ⇒ an empty, undeclared profile):

    * ``effect_grants`` — semicolon/comma list of capabilities the row grants;
    * ``effect_targets`` — semicolon/comma list of write-target paths the row authorizes;
    * ``effect_relaxes`` — semicolon/comma list of constraints the row relaxes/waives/retracts;
    * ``effect_destructive`` / ``effect_cross_repo`` / ``effect_bulk`` — boolean flags.

    Args:
        row: The authorizing row (decision-log or directive), as a string-valued dict.

    Returns:
        The declared effect profile; ``declared`` is True iff any effect column was present
        and non-empty.
    """
    grants = _split_list(row.get("effect_grants", ""))
    targets = _split_list(row.get("effect_targets", ""))
    relaxes = _split_list(row.get("effect_relaxes", ""))
    destructive = _truthy(row.get("effect_destructive", ""))
    cross_repo = _truthy(row.get("effect_cross_repo", ""))
    bulk = _truthy(row.get("effect_bulk", ""))
    declared = bool(
        grants or targets or relaxes or destructive or cross_repo or bulk
    )
    return EffectProfile(
        grants_capabilities=grants,
        write_targets=targets,
        relaxes=relaxes,
        destructive=destructive,
        cross_repo=cross_repo,
        bulk=bulk,
        declared=declared,
    )


def _profile_signal(profile: EffectProfile) -> str:
    """Return this row's relaxing signal from its STRUCTURED effect axes alone.

    * an empty (undeclared) profile decides nothing ⇒ :data:`INDETERMINATE`;
    * a granted capability, a relaxed constraint, a destructive/cross-repo/bulk flag, or a
      governance-root / destructive-scope target ⇒ :data:`RELAXING`;
    * a profile that declares only benign, non-governance targets ⇒ :data:`NON_RELAXING`.

    This is ONE axis of the union in :func:`derive_effect_class`; it never suppresses the text
    axis (adversarial F-1). A structured profile can only *add* a relaxing signal, never subtract
    the verdict/scope-text signals for axes it is silent on.
    """
    if profile.is_empty():
        return INDETERMINATE
    if profile.grants_capabilities or profile.relaxes:
        return RELAXING
    if profile.destructive or profile.cross_repo or profile.bulk:
        return RELAXING
    for target in profile.write_targets:
        # A governance-root target, OR a target whose text the shared denylist already reads as
        # destructive/governance (reuse — m5), is relaxing.
        if target_is_governance_root(target) or not scope_is_allowed(target):
            return RELAXING
    return NON_RELAXING


def _text_signal(row: dict[str, str], *, kind: str) -> str:
    """Return this row's relaxing signal from its scope/verdict TEXT alone.

    * A ``HALT-RETRACTED`` decision verdict is relaxing by definition (it pierces C-2);
    * a governance-root or destructive/bulk/cross-repo scope ⇒ :data:`RELAXING`;
    * a non-empty scope that clears both those vocabularies ⇒ :data:`NON_RELAXING`;
    * an empty scope ⇒ :data:`INDETERMINATE`.

    **Coverage ceiling (re-review R1).** The text axis proves *relaxing* from the denylist +
    governance vocabulary; it proves *non-relaxing* only by the ABSENCE of a hit, which is not the
    same as proof. So a relaxing effect phrased in out-of-vocabulary text (``authorize-autonomous-
    key-rotation``) with no structured ``effect_*`` fields classifies NON_RELAXING here. Only an
    *empty* scope defaults to INDETERMINATE. This under-coverage is a deliberate, bounded tradeoff
    (it keeps the ~100 legacy HMAC rows non-relaxing with zero migration) and is safe only under
    the keyless threat model: producing any authorizing row already requires signed-write
    capability. The durable closure is the WS-B/C acceptance condition that **new** authorizing
    rows in a governed workspace declare structured effect (so this text fallback governs only
    pre-existing legacy rows). Do not read "relaxing-until-proven" as universal — it is
    vocabulary-bounded on the text axis.

    Always evaluated (never gated on the absence of a structured profile), so a benign structured
    cell can no longer silence a relaxing verdict/scope (adversarial F-1/F-3).

    Args:
        row: The authorizing row.
        kind: ``"decision"`` or ``"directive"`` — selects which columns hold the operative scope.

    Returns:
        One of :data:`RELAXING`, :data:`NON_RELAXING`, :data:`INDETERMINATE`.
    """
    if kind == "decision":
        verdict = (row.get("verdict") or row.get("status") or "").strip().upper()
        if verdict == _HALT_RETRACTION_VERDICT.upper():
            return RELAXING
        scope_text = (
            row.get("scope")
            or row.get("action_reviewed")
            or row.get("decision")
            or ""
        ).strip()
    else:  # directive
        scope_text = (row.get("task_scope") or "").strip()

    if not scope_text:
        return INDETERMINATE
    if target_is_governance_root(scope_text):
        return RELAXING
    # scope_is_allowed==False ⇒ the text names a destructive/governance/bulk/cross-repo effect.
    return NON_RELAXING if scope_is_allowed(scope_text) else RELAXING


def _combine(*signals: str) -> str:
    """Combine per-axis signals fail-closed: RELAXING > NON_RELAXING > INDETERMINATE.

    A relaxing signal on ANY axis makes the row relaxing (no benign axis can cancel it). The row
    is non-relaxing only when at least one axis proves it benign and none flags it relaxing. When
    no axis decides, the row is indeterminate (which callers collapse to relaxing).
    """
    if RELAXING in signals:
        return RELAXING
    if NON_RELAXING in signals:
        return NON_RELAXING
    return INDETERMINATE


def derive_effect_class(row: dict[str, str], *, kind: str) -> str:
    """Return the derived effect class for a row, ignoring any self-declared ``effect_class``.

    Computed as the fail-closed UNION (:func:`_combine`) of two independent axes — the structured
    effect profile (:func:`_profile_signal`) and the scope/verdict text (:func:`_text_signal`).
    Neither axis can suppress the other: declaring one benign structured field cannot silence a
    relaxing verdict or scope (the adversarial F-1 keystone hole). Never consults ``effect_class``
    — that column is only ever *checked against* this result by :func:`classify_row`.

    Args:
        row: The authorizing row (decision-log or directive).
        kind: ``"decision"`` or ``"directive"``.

    Returns:
        One of :data:`RELAXING`, :data:`NON_RELAXING`, :data:`INDETERMINATE`.
    """
    if kind not in {"decision", "directive"}:
        raise ValueError(f"kind must be 'decision' or 'directive', not {kind!r}")
    profile = effect_profile_from_row(row)
    return _combine(_profile_signal(profile), _text_signal(row, kind=kind))


def classify_row(row: dict[str, str], *, kind: str) -> str:
    """Return a row's effect class, refusing when a self-declaration disagrees with the derivation.

    This is the module's single public policy entry point. It:

    1. derives the class from the row's effect (:func:`derive_effect_class`);
    2. if the row carries a self-declared ``effect_class``, refuses only a **dangerous**
       divergence — never a merely conservative one (audit Q4):

       * declared ``non-relaxing`` but derived is *not* ``non-relaxing`` (relaxing OR
         indeterminate) ⇒ **raise** — the author under-claims a row that is not proven benign;
       * declared ``relaxing`` but derived ``non-relaxing`` ⇒ **raise** — the author's model
         diverges from a row the effect proves benign, worth surfacing loudly;
       * declared ``relaxing`` and derived ``indeterminate`` ⇒ **accept** (both take the elevated
         path — an honest conservative declaration must not be refused);
    3. returns the derived class (``INDETERMINATE`` included — callers collapse it to relaxing).

    A self-declared ``effect_class`` of ``indeterminate`` (or an unrecognized value) is treated as
    no self-declaration, and is never accepted as a *benign* assertion — an author cannot
    self-assert away the elevated path.

    Args:
        row: The authorizing row.
        kind: ``"decision"`` or ``"directive"``.

    Returns:
        The derived class.

    Raises:
        EffectClassifierError: The self-declared class dangerously diverges from the derived class.
    """
    derived = derive_effect_class(row, kind=kind)
    declared = (row.get("effect_class") or "").strip().lower()
    dangerous = (declared == NON_RELAXING and derived != NON_RELAXING) or (
        declared == RELAXING and derived == NON_RELAXING
    )
    if dangerous:
        raise EffectClassifierError(
            f"row declares effect_class={declared!r} but its derived effect is {derived!r}: "
            "the relaxing class is derived from effect, never self-declared, and a disagreement "
            "is refused (fail-closed). Correct the declaration or the effect fields."
        )
    return derived


def non_eligibility_reason(row: dict[str, str], *, kind: str) -> str | None:
    """Return why a row is CATEGORICALLY non-eligible (refused even when validly signed), or None.

    Workstream E. Some effects must not be authorizable through a decision-log / directive exception
    **at all** — not even by the operator's Ed25519 signature — because they would rewrite the trust
    base the whole scheme rests on, or combine into a sweeping grant. Those changes have their own
    privileged process (governance edit + integrity re-pin under out-of-chain review); a per-row
    exception is never the right instrument. This runs on every authorizing row, ahead of the
    signature checks, so a valid signature cannot buy an ineligible effect.

    Non-eligible:

    * an effect that **writes/targets a governance or trust root** — the constitution, a roster, a
      signing key, the integrity manifest, or (R1) the Ed25519 verify-key store: authorizing a write
      there would let the grantee self-bootstrap trust;
    * a **granted capability that names a governance/trust root**;
    * the **sweeping combination** cross-repo + destructive + bulk in one grant.

    It reuses the shared governance-target vocabulary (no denylist double-impl — m5); its NEW
    coverage over the C-4 directive denylist is the decision_log path (which has no scope denylist)
    and the capability-combination rule.

    Args:
        row: The authorizing row (decision-log or directive).
        kind: ``"decision"`` or ``"directive"``.

    Returns:
        A human-readable reason string when the row is non-eligible, else None.
    """
    profile = effect_profile_from_row(row)
    for target in profile.write_targets:
        if target_is_governance_root(target):
            return f"authorizes a write to governance/trust root {target!r}"
    for capability in profile.grants_capabilities:
        if target_is_governance_root(capability):
            return f"grants a governance/trust capability {capability!r}"
    if profile.cross_repo and profile.destructive and profile.bulk:
        return "combines cross-repo + destructive + bulk into one sweeping grant"
    if kind == "decision":
        scope_text = (
            row.get("scope") or row.get("action_reviewed") or row.get("decision") or ""
        ).strip()
    else:
        scope_text = (row.get("task_scope") or "").strip()
    if scope_text and target_is_governance_root(scope_text):
        return f"scope {scope_text!r} targets a governance/trust root"
    return None


def requires_operator_signature(row: dict[str, str], *, kind: str) -> bool:
    """Return True for the NARROW constraint-relaxing EXCEPTION subset that needs operator Ed25519.

    Distinct from :func:`is_relaxing` (which is broad — it includes any destructive action so the
    HALT/lineage machinery covers renames). The elevated *asymmetric-signing* requirement (WS-B) is
    for **exceptions that relax a constraint**, not routine destructive operations: a routine
    ``prune``/``overwrite`` still clears through the established ``@security`` C-5 HMAC decision, but
    a row that **grants a capability, targets a governance/trust root, explicitly relaxes a
    constraint, or reaches across repositories/in bulk** requires the operator's private key.

    Making this narrower than ``is_relaxing`` is deliberate: applying operator-only Ed25519 to every
    destructive action would replace the normal destructive-clearance workflow, which is not the
    intent — the operator asked to retain a signable path for ADX-like *exceptions*.

    Args:
        row: The authorizing row.
        kind: ``"decision"`` or ``"directive"``.

    Returns:
        Whether the row belongs to the exception subset that needs an operator Ed25519 signature.

    Raises:
        EffectClassifierError: A dangerous self-declared ``effect_class`` divergence (via
            :func:`is_relaxing`), so the caller fails closed.
    """
    if not is_relaxing(row, kind=kind):  # also raises on a dangerous self-declaration divergence
        return False
    profile = effect_profile_from_row(row)
    if profile.grants_capabilities or profile.relaxes or profile.cross_repo or profile.bulk:
        return True
    if any(target_is_governance_root(target) for target in profile.write_targets):
        return True
    if kind == "decision":
        scope_text = (
            row.get("scope") or row.get("action_reviewed") or row.get("decision") or ""
        ).strip()
    else:
        scope_text = (row.get("task_scope") or "").strip()
    return bool(scope_text and target_is_governance_root(scope_text))


def is_relaxing(row: dict[str, str], *, kind: str) -> bool:
    """Return True when a row must take the elevated path (relaxing OR indeterminate).

    The fail-closed collapse every caller applies: a derivably-relaxing row and an
    undecidable (:data:`INDETERMINATE`) row both require the elevated (asymmetric-signed)
    authorization; only a provably :data:`NON_RELAXING` row stays on the ordinary path.

    Args:
        row: The authorizing row.
        kind: ``"decision"`` or ``"directive"``.

    Returns:
        Whether the row is treated as relaxing (relaxing-until-proven).

    Raises:
        EffectClassifierError: A self-declaration disagrees with the derivation.
    """
    return classify_row(row, kind=kind) != NON_RELAXING


__all__ = [
    "RELAXING",
    "NON_RELAXING",
    "INDETERMINATE",
    "EffectClassifierError",
    "EffectProfile",
    "classify_row",
    "derive_effect_class",
    "effect_profile_from_row",
    "is_relaxing",
    "non_eligibility_reason",
    "requires_operator_signature",
    "target_is_governance_root",
]
