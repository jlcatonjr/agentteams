"""sync_classifier.py — generalized three-way classifier for native↔canonical sync.

Generalizes ``front_matter_merge._merge_front_matter``'s proven three-way
decision table from two specific axes:

1. **Content scope**: flat YAML scalar keys → arbitrary content regions
   (front matter keys, body markdown, capabilities, handoffs,
   framework_extensions, instructions_binding, raw_front_matter).
2. **Comparison axis**: template↔deployed → canonical↔native.

The classifier compares three versions of an agent's data:

- **canonical** — the current state in ``.agentteams/canonical/``
- **native** — the current state in a native framework deployment
  (e.g. ``.goose/recipes/`` or ``.claude/agents/``)
- **baseline** — a real-content snapshot taken at the moment canonical was
  last materialized into (or absorbed from) this framework

and classifies each field/region as one of:

| canonical vs baseline | native vs baseline | classification          |
|----------------------|--------------------|-----------------------|
| unchanged            | unchanged          | ``unchanged``          |
| unchanged            | changed            | ``native-moved``       |
| changed              | unchanged          | ``canonical-moved``    |
| changed              | changed            | ``both-moved-conflict``|

**Capability-key carve-out (§6.1)** — the single most safety-critical rule
in the entire absorb design: capability-bearing fields are **never**
auto-applied, regardless of how clean their classification looks.  Even a
``native-moved`` (clean, one-sided) classification on a capability field
still routes to ``proposal`` (human review).  This is because a capability
grant absorbed into canonical fans back out to every other framework that
derives from it — a silently-absorbed widening doesn't stay local.

This module is deliberately **classification-only**: it produces structured
results and human-readable notices.  It never writes anything to disk.
The caller (Phase C's CLI verb) decides what to do with the results.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any

# Re-export the capability-key set from front_matter_merge so there is ONE
# definition of "which fields are capability-bearing" across the entire
# codebase.  This is the same set the plan's §6.1 names explicitly.
from agentteams.front_matter_merge import CAPABILITY_FRONT_MATTER_KEYS


class Classification(str, enum.Enum):
    """Three-way classification of a single field/region."""

    UNCHANGED = "unchanged"
    NATIVE_MOVED = "native-moved"
    CANONICAL_MOVED = "canonical-moved"
    BOTH_MOVED_CONFLICT = "both-moved-conflict"
    NO_BASELINE = "no-baseline"


class Action(str, enum.Enum):
    """What the classifier recommends the caller do with a field.

    - ``keep``       — no change needed (or no-baseline: don't touch it)
    - ``apply``      — safe to auto-apply (non-capability, clean provenance)
    - ``proposal``   — route to human review; report but never auto-apply
    """

    KEEP = "keep"
    APPLY = "apply"
    PROPOSAL = "proposal"


@dataclass
class FieldResult:
    """Classification of a single agent field."""

    field_name: str
    classification: Classification
    action: Action
    canonical_value: Any
    native_value: Any
    baseline_value: Any
    notice: str


@dataclass
class AgentSyncReport:
    """Complete classification report for one agent across all its fields."""

    agent_slug: str
    framework: str
    field_results: list[FieldResult] = field(default_factory=list)

    @property
    def applied(self) -> list[FieldResult]:
        """Fields that are safe to auto-apply (non-capability, clean)."""
        return [r for r in self.field_results if r.action == Action.APPLY]

    @property
    def proposals(self) -> list[FieldResult]:
        """Fields that require human review before applying."""
        return [r for r in self.field_results if r.action == Action.PROPOSAL]

    @property
    def unchanged(self) -> list[FieldResult]:
        """Fields with no divergence."""
        return [r for r in self.field_results if r.action == Action.KEEP]

    @property
    def has_changes(self) -> bool:
        """True if any field is not ``unchanged``."""
        return any(r.action != Action.KEEP for r in self.field_results)

    def to_text(self) -> str:
        """Human-readable report, one section per classification category."""
        lines: list[str] = []
        lines.append(f"Agent: {self.agent_slug}  (framework: {self.framework})")
        lines.append(f"  {len(self.unchanged)} unchanged, "
                      f"{len(self.applied)} apply, "
                      f"{len(self.proposals)} proposal")
        lines.append("")

        if self.applied:
            lines.append("  Auto-applicable (clean native-moved):")
            for r in self.applied:
                lines.append(f"    • {r.field_name}: {r.notice}")
            lines.append("")

        if self.proposals:
            lines.append("  Requires human review:")
            for r in self.proposals:
                lines.append(f"    • {r.field_name}: {r.notice}")
            lines.append("")

        if not self.has_changes:
            lines.append("  (no divergent fields)")
        return "\n".join(lines)


@dataclass
class SyncReport:
    """Top-level report covering all agents for one (canonical, native) pair."""

    canonical_dir: str
    native_dir: str
    framework: str
    agent_reports: list[AgentSyncReport] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(ar.has_changes for ar in self.agent_reports)

    @property
    def total_proposals(self) -> int:
        return sum(len(ar.proposals) for ar in self.agent_reports)

    @property
    def total_applied(self) -> int:
        return sum(len(ar.applied) for ar in self.agent_reports)

    def to_text(self) -> str:
        """Full human-readable report."""
        lines: list[str] = []
        lines.append("=== Sync Report ===")
        lines.append(f"  Canonical: {self.canonical_dir}")
        lines.append(f"  Native:    {self.native_dir}")
        lines.append(f"  Framework: {self.framework}")
        lines.append(f"  Agents:    {len(self.agent_reports)}")
        lines.append(f"  Applied:   {self.total_applied} fields")
        lines.append(f"  Proposals: {self.total_proposals} fields")
        lines.append("")
        for ar in self.agent_reports:
            if ar.has_changes:
                lines.append(ar.to_text())
        if not self.has_changes:
            lines.append("No divergent fields detected across any agent.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Capability-key identification
# ---------------------------------------------------------------------------

#: Fields in the CAI agent dict that carry capability grants.  These are the
#: canonical-dict equivalents of ``CAPABILITY_FRONT_MATTER_KEYS`` — the same
#: conceptual set, just named in CAI vocabulary rather than YAML-key
#: vocabulary.  ``capabilities`` covers ``tool_scopes``, ``raw``, and
#: ``model_hint`` because all three are capability-bearing sub-fields.
CAPABILITY_CAI_FIELDS: frozenset[str] = frozenset({
    "capabilities",       # tool_scopes, raw, model_hint — all capability-bearing
    "raw_front_matter",   # may contain tools/allowed-tools/disallowedTools/model
})

#: Sub-fields within ``raw_front_matter`` that are capability-bearing.  When
#: any of these keys appears in ``raw_front_matter``, the entire
#: ``raw_front_matter`` field is treated as capability-bearing for safety
#: (conservative: a subset check, not a full parse, because raw_front_matter
#: is arbitrary framework-specific JSON).
_CAPABILITY_RAW_FM_KEYS: frozenset[str] = frozenset({
    "tools",
    "allowed-tools",
    "disallowedTools",
    "model",
    "agents",
    "capabilities",
    "permissionMode",
    "user-invokable",  # copilot-vscode: controls agent visibility in VS Code UI
})


def _raw_fm_has_capability_keys(value: Any) -> bool:
    """Check whether a ``raw_front_matter`` value contains capability keys."""
    if not isinstance(value, dict):
        return False
    return any(k in _CAPABILITY_RAW_FM_KEYS for k in value)


def is_capability_field(field_name: str, value: Any) -> bool:
    """Check whether a CAI agent field is capability-bearing.

    For ``capabilities`` and ``raw_front_matter``, inspects the value's
    contents to determine if it actually carries capability data.  A
    ``raw_front_matter`` dict with only ``user-invokable: true`` is NOT
    capability-bearing (it's a metadata field); one with ``tools: [...]`` IS.

    Args:
        field_name: The CAI agent field name.
        value: The field's value (may be ``None``, dict, list, or scalar).

    Returns:
        True if the field is or contains capability-bearing data.
    """
    if field_name == "capabilities":
        return True  # entire field is capability by definition
    if field_name == "raw_front_matter":
        return _raw_fm_has_capability_keys(value)
    # Direct-name match (shouldn't happen in CAI dict, but defensive)
    if field_name in CAPABILITY_FRONT_MATTER_KEYS:
        return True
    return False


def is_capability_field_any_side(
    field_name: str,
    canonical_value: Any,
    native_value: Any,
    baseline_value: Any,
) -> bool:
    """Check whether a field is capability-bearing on ANY of the three sides.

    This is the security-critical check used by the classifier.  It must
    check all three values, not just the native value, because the native
    side may have **dropped** a capability key (e.g. removed ``tools`` from
    ``raw_front_matter``).  If only the native value were checked, a
    capability removal would be misclassified as a non-capability change
    and auto-applied — violating §6.1's rule that capability fields
    **always** route to human review regardless of classification
    cleanliness.

    The ``capabilities`` field name is always capability-bearing regardless
    of values (it is capability by definition).

    Args:
        field_name: The CAI agent field name.
        canonical_value: Current value in the canonical baseline.
        native_value: Current value in the native deployment.
        baseline_value: Value at the last sync point.

    Returns:
        True if the field is or contains capability-bearing data on any side.
    """
    if field_name == "capabilities":
        return True  # entire field is capability by definition
    if field_name == "raw_front_matter":
        return (
            _raw_fm_has_capability_keys(canonical_value)
            or _raw_fm_has_capability_keys(native_value)
            or _raw_fm_has_capability_keys(baseline_value)
        )
    # Direct-name match (shouldn't happen in CAI dict, but defensive)
    if field_name in CAPABILITY_FRONT_MATTER_KEYS:
        return True
    return False


# ---------------------------------------------------------------------------
# Field comparison
# ---------------------------------------------------------------------------

def _values_equal(a: Any, b: Any) -> bool:
    """Compare two CAI field values for equality.

    Uses JSON-serialized comparison for dicts/lists (deep equality) and
    direct comparison for scalars.  This avoids ordering issues in dict
    keys (both sides are normalized by json.dumps with sort_keys=True).
    """
    if a is None and b is None:
        return True
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        if a is None or b is None:
            return False
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    return a == b


def _classify_field(
    field_name: str,
    canonical_value: Any,
    native_value: Any,
    baseline_value: Any,
    *,
    has_baseline: bool,
) -> FieldResult:
    """Classify a single field using the three-way decision table.

    The decision table (report §5, generalizing front_matter_merge.py):

    | canonical vs baseline | native vs baseline | classification          |
    |-----------------------|--------------------|-----------------------|
    | unchanged             | unchanged          | unchanged             |
    | unchanged             | changed            | native-moved          |
    | changed               | unchanged          | canonical-moved       |
    | changed               | changed            | both-moved-conflict   |
    | (no baseline)         | (anything)         | no-baseline           |

    The capability-key carve-out (§6.1) overrides the action:
    - A ``native-moved`` classification on a NON-capability field → ``apply``
    - A ``native-moved`` classification on a capability field → ``proposal``
    - ``canonical-moved`` and ``both-moved-conflict`` → always ``proposal``
    - ``unchanged`` → ``keep``
    - ``no-baseline`` → ``keep`` (apply nothing without a baseline)

    Args:
        field_name: Name of the CAI agent field being classified.
        canonical_value: Current value in the canonical baseline.
        native_value: Current value in the native deployment.
        baseline_value: Value at the last sync point.
        has_baseline: Whether a baseline exists for this framework.

    Returns:
        A ``FieldResult`` with classification, action, and human-readable
        notice.
    """
    # B.4 security fix: check ALL three values, not just native_value.
    # If the native side DROPPED a capability key from raw_front_matter,
    # checking only native_value would miss it and auto-apply a capability
    # removal — violating §6.1.
    cap = is_capability_field_any_side(
        field_name, canonical_value, native_value, baseline_value
    )

    # No baseline → can't classify, can't apply anything
    if not has_baseline:
        if _values_equal(canonical_value, native_value):
            cls = Classification.UNCHANGED
        else:
            cls = Classification.NO_BASELINE
        return FieldResult(
            field_name=field_name,
            classification=cls,
            action=Action.KEEP,
            canonical_value=canonical_value,
            native_value=native_value,
            baseline_value=baseline_value,
            notice=(
                "No baseline recorded for this framework — "
                "nothing applied, report only"
                if cls == Classification.NO_BASELINE
                else "Unchanged (no baseline needed)"
            ),
        )

    c_changed = not _values_equal(canonical_value, baseline_value)
    n_changed = not _values_equal(native_value, baseline_value)

    # Also check if canonical and native are currently identical (even if
    # both moved identically — e.g. a sync already happened)
    if _values_equal(canonical_value, native_value):
        # They agree, so there's no divergence to absorb
        if c_changed and n_changed:
            # Both moved to the same value — effectively unchanged
            cls = Classification.UNCHANGED
        else:
            cls = Classification.UNCHANGED
        return FieldResult(
            field_name=field_name,
            classification=cls,
            action=Action.KEEP,
            canonical_value=canonical_value,
            native_value=native_value,
            baseline_value=baseline_value,
            notice="Unchanged (canonical and native agree)",
        )

    if not c_changed and not n_changed:
        cls = Classification.UNCHANGED
        return FieldResult(
            field_name=field_name,
            classification=cls,
            action=Action.KEEP,
            canonical_value=canonical_value,
            native_value=native_value,
            baseline_value=baseline_value,
            notice="Unchanged",
        )

    if not c_changed and n_changed:
        cls = Classification.NATIVE_MOVED
        if cap:
            action = Action.PROPOSAL
            notice = (
                "Native moved this capability field; canonical unchanged. "
                "CAPABILITY KEY — never auto-applied (§6.1). "
                "Review the change, then apply manually."
            )
        else:
            action = Action.APPLY
            notice = (
                "Native edited; canonical unchanged since last sync. "
                "Safe to absorb."
            )
        return FieldResult(
            field_name=field_name,
            classification=cls,
            action=action,
            canonical_value=canonical_value,
            native_value=native_value,
            baseline_value=baseline_value,
            notice=notice,
        )

    if c_changed and not n_changed:
        cls = Classification.CANONICAL_MOVED
        # Canonical moved but native didn't — the native side is stale
        # relative to canonical.  This is not an absorb case; it means
        # canonical was updated (from another framework's absorption, or
        # by hand) and the native side hasn't caught up yet.
        return FieldResult(
            field_name=field_name,
            classification=cls,
            action=Action.PROPOSAL,
            canonical_value=canonical_value,
            native_value=native_value,
            baseline_value=baseline_value,
            notice=(
                "Canonical moved since last sync; native unchanged. "
                "The native deployment is stale — re-deploy from canonical "
                "to pick up this change."
            ),
        )

    # Both moved
    cls = Classification.BOTH_MOVED_CONFLICT
    return FieldResult(
        field_name=field_name,
        classification=cls,
        action=Action.PROPOSAL,
        canonical_value=canonical_value,
        native_value=native_value,
        baseline_value=baseline_value,
        notice=(
            "Both canonical and native changed since last sync — "
            "CONFLICT.  Resolve manually."
            + (
                "  CAPABILITY FIELD (§6.1)."
                if cap else ""
            )
        ),
    )


# ---------------------------------------------------------------------------
# Agent-level classification
# ---------------------------------------------------------------------------

#: The set of CAI agent fields the classifier examines.  Order matters for
#: report readability, not for correctness.
AGENT_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "body_markdown",
    "capabilities",
    "handoffs",
    "invariant_core_markdown",
    "source_path",
    "raw_front_matter",
)


def classify_agent(
    agent_slug: str,
    framework: str,
    canonical_agent: dict[str, Any] | None,
    native_agent: dict[str, Any] | None,
    baseline_agent: dict[str, Any] | None,
) -> AgentSyncReport:
    """Classify all fields of a single agent.

    Handles agents present in canonical but not native (or vice versa) by
    reporting them as proposals — a missing agent is never auto-applied.

    Args:
        agent_slug: The agent's slug (used for reporting even if absent on
            one side).
        framework: The native framework name (e.g. ``"goose"``, ``"claude"``).
        canonical_agent: The agent dict from ``load_canonical``, or ``None``
            if the agent doesn't exist in canonical.
        native_agent: The agent dict from ``export_to_cai``, or ``None`` if
            the agent doesn't exist in the native deployment.
        baseline_agent: The agent dict from the sync baseline, or ``None``
            if no baseline exists for this framework.

    Returns:
        An ``AgentSyncReport`` with one ``FieldResult`` per field.
    """
    report = AgentSyncReport(agent_slug=agent_slug, framework=framework)
    has_baseline = baseline_agent is not None

    # Handle agent missing from one side
    if canonical_agent is None and native_agent is not None:
        report.field_results.append(FieldResult(
            field_name="__agent__",
            classification=Classification.NATIVE_MOVED,
            action=Action.PROPOSAL,
            canonical_value=None,
            native_value=native_agent.get("slug"),
            baseline_value=baseline_agent.get("slug") if baseline_agent else None,
            notice=(
                "Agent exists in native but not in canonical. "
                "New agent — review and add to canonical manually."
            ),
        ))
        return report

    if canonical_agent is not None and native_agent is None:
        report.field_results.append(FieldResult(
            field_name="__agent__",
            classification=Classification.CANONICAL_MOVED,
            action=Action.PROPOSAL,
            canonical_value=canonical_agent.get("slug"),
            native_value=None,
            baseline_value=baseline_agent.get("slug") if baseline_agent else None,
            notice=(
                "Agent exists in canonical but not in native deployment. "
                "Removed from native — review and remove from canonical "
                "if intentional."
            ),
        ))
        return report

    if canonical_agent is None and native_agent is None:
        # Shouldn't happen, but defensive
        return report

    # Classify each field
    for field_name in AGENT_FIELDS:
        c_val = canonical_agent.get(field_name)
        n_val = native_agent.get(field_name)
        b_val = baseline_agent.get(field_name) if baseline_agent else None
        result = _classify_field(
            field_name, c_val, n_val, b_val,
            has_baseline=has_baseline,
        )
        # Skip fields that are None on all three sides (nothing to report)
        if c_val is None and n_val is None and b_val is None:
            continue
        # Skip unchanged fields with no actual values
        if result.action == Action.KEEP and result.classification == Classification.UNCHANGED:
            if c_val is None and n_val is None:
                continue
        report.field_results.append(result)

    return report


# ---------------------------------------------------------------------------
# Team-level classification
# ---------------------------------------------------------------------------

def classify_sync(
    canonical_cai: dict[str, Any],
    native_cai: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    canonical_dir: str = "",
    native_dir: str = "",
    framework: str = "",
) -> SyncReport:
    """Classify all agents in a canonical↔native comparison.

    This is the top-level entry point.  It:

    1. Indexes agents by slug in canonical, native, and baseline.
    2. For each agent that appears in canonical OR native, classifies every
       field using the three-way decision table.
    3. Returns a ``SyncReport`` with structured results and human-readable
       notices.

    Args:
        canonical_cai: CAI dict from ``load_canonical``.
        native_cai: CAI dict from ``export_to_cai`` of the native deployment.
        baseline: Baseline dict from ``sync_baseline.load_baseline``, or
            ``None`` if no baseline exists for this framework.
        canonical_dir: Path string of the canonical directory (for reporting).
        native_dir: Path string of the native source directory (for reporting).
        framework: The native framework name (e.g. ``"goose"``).

    Returns:
        A ``SyncReport``.  Never writes anything to disk.
    """
    report = SyncReport(
        canonical_dir=canonical_dir,
        native_dir=native_dir,
        framework=framework,
    )

    baseline_agents: dict[str, dict[str, Any]] = {}
    if baseline and isinstance(baseline.get("agents"), list):
        for a in baseline["agents"]:
            if isinstance(a, dict) and a.get("slug"):
                baseline_agents[a["slug"]] = a

    canonical_agents: dict[str, dict[str, Any]] = {
        a["slug"]: a for a in (canonical_cai.get("agents") or [])
        if isinstance(a, dict) and a.get("slug")
    }
    native_agents: dict[str, dict[str, Any]] = {
        a["slug"]: a for a in (native_cai.get("agents") or [])
        if isinstance(a, dict) and a.get("slug")
    }

    all_slugs = sorted(set(canonical_agents) | set(native_agents))
    for slug in all_slugs:
        ar = classify_agent(
            agent_slug=slug,
            framework=framework,
            canonical_agent=canonical_agents.get(slug),
            native_agent=native_agents.get(slug),
            baseline_agent=baseline_agents.get(slug),
        )
        report.agent_reports.append(ar)

    return report
