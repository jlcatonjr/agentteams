"""findings_ledger.py — a durable, triaged record of what the red-team audits found.

**The gap this closes, measured rather than assumed.** The deterministic battery's findings
reach tracked ledgers and 24 of 29 have been worked off. The judgment layer's findings reached
*nothing*: they landed in a gitignored `tmp/` directory, were read by no script but the two
that wrote them, and triggered no notification of any kind. The daily cadence was justified on
**trend detection**, and the artifacts that would show which payload moved were being discarded
every day. That is a claim whose denominator was never built.

**One row per finding, not per run.** The upsert key is
``(layer, finding_id, model, observed)``, so a payload that fails identically every day is
*one row whose ``last_seen`` advances*, and a payload whose verdict changes updates the row and
records the transition. Appending per run would produce 365 rows a year for a stable failure
and bury the only signal that matters — which is how a ledger becomes something people stop
opening.

**Triage is the contextualization.** A finding nobody has classified cannot be acted on,
because the action differs completely by class: our rules being inadequate is a template edit,
a provider's documented behaviour is a citation and a workaround, and a model limitation is an
argument about *model selection* rather than a prompt to tune. Tuning a prompt until a model
passes is fitting the test to the model.

**Ageing is what makes it a control rather than a list.** An ``UNTRIAGED`` row older than
:data:`UNTRIAGED_MAX_AGE_DAYS` fails the suite. This repository has a remediation log that
reached 28 open rows, several of them describing work that had already shipped, which is the
demonstration that ledgers without an ageing rule stop describing the system.
"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from agentteams.atomicio import atomic_rewrite_csv_rows

#: Where the ledger lives. **Tracked**, unlike the run artifacts it is promoted from: a finding
#: that only exists in a gitignored directory is a finding that did not survive the week.
FINDINGS_LEDGER_REL = "references/redteam-findings.log.csv"

#: The provider/model documentation register that citations resolve into.
PROVIDER_DOCS_REL = "references/agent-provider-docs.reference.md"

FIELDNAMES: tuple[str, ...] = (
    "first_seen", "last_seen", "layer", "framework", "agent", "finding_id", "interface", "model",
    "expected", "observed", "triage", "citation", "remediation_target", "status", "note",
)

# --- triage vocabulary ---------------------------------------------------------------------

UNTRIAGED = "UNTRIAGED"
OUR_DEFECT = "OUR-DEFECT"
PROVIDER_DOCUMENTED = "PROVIDER-DOCUMENTED"
MODEL_LIMITATION = "MODEL-LIMITATION"
HARNESS_DEFECT = "HARNESS-DEFECT"

TRIAGE_CLASSES: frozenset[str] = frozenset({
    UNTRIAGED, OUR_DEFECT, PROVIDER_DOCUMENTED, MODEL_LIMITATION, HARNESS_DEFECT,
})

#: What each class must carry to be a closed question rather than a label.
TRIAGE_EVIDENCE: dict[str, str] = {
    OUR_DEFECT: "remediation_target",
    PROVIDER_DOCUMENTED: "citation",
    MODEL_LIMITATION: "citation",
    HARNESS_DEFECT: "remediation_target",
    UNTRIAGED: "",
}

#: How long a finding may sit unclassified before the suite fails. Two weeks: long enough that
#: a holiday does not trip it, short enough that it is still the same context when someone
#: looks.
UNTRIAGED_MAX_AGE_DAYS = 14

#: An `OUR-DEFECT` fix must land in the MODULE, never in a generated instance. The audited
#: agent file is a derived artifact (Constitutional Rule 4): a fix written into it is
#: overwritten in fenced regions on the next `--update --merge`, silently diverges in unfenced
#: ones, and reaches no other generated team.
MODULE_REMEDIATION_PREFIXES: tuple[str, ...] = ("agentteams/",)

#: A `HARNESS-DEFECT` fix lands in the measuring apparatus.
HARNESS_REMEDIATION_PREFIXES: tuple[str, ...] = ("scripts/", "tests/", "agentteams/redteam/")

_DOC_ID_RE = re.compile(r"^\|\s*`([a-z0-9][a-z0-9._-]*)`\s*\|")


@dataclass
class Finding:
    """One measured failure, with everything needed to decide what to do about it."""

    layer: str
    finding_id: str
    interface: str
    model: str
    expected: str
    observed: str
    # Defaulted for backward compatibility with the 15 rows written before the sweep existed,
    # which were all @security on claude. New rows always set them explicitly.
    framework: str = "claude"
    agent: str = "security"
    first_seen: str = ""
    last_seen: str = ""
    triage: str = UNTRIAGED
    citation: str = ""
    remediation_target: str = ""
    status: str = "open"
    note: str = ""

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        """The upsert identity.

        ``framework`` and ``agent`` are part of it, and leaving them out was a real defect
        caught in the plan audit: with 30 agents across 3 frameworks, ``auth-01`` on
        ``@orchestrator/goose`` and ``auth-01`` on ``@security/claude`` would have upserted into
        the same row. Ninety findings would have collapsed into one, and the ledger would have
        under-reported by that factor while looking perfectly tidy.
        """
        return (self.layer, self.framework, self.agent, self.finding_id, self.model,
                self.observed)


def read_ledger(root: Path) -> list[Finding]:
    """Load the ledger, or return ``[]`` when it does not exist yet."""
    path = root / FINDINGS_LEDGER_REL
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in FIELDNAMES if c not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"{path}: ledger missing column(s) {missing}")
        return [
            Finding(**{k: (row.get(k) or "").strip() for k in FIELDNAMES})
            for row in reader
        ]


def write_ledger(root: Path, findings: list[Finding]) -> Path:
    """Write the ledger, sorted so a diff shows a change rather than a reshuffle."""
    path = root / FINDINGS_LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(findings, key=lambda f: (f.layer, f.framework, f.agent, f.finding_id,
                                              f.model, f.observed))
    rows = [
        {k: str(v) for k, v in asdict(finding).items() if k in FIELDNAMES}
        for finding in ordered
    ]
    atomic_rewrite_csv_rows(path, rows, list(FIELDNAMES))
    return path


@dataclass
class PromotionResult:
    """What a promotion changed, so the caller can say something specific."""

    added: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    transitioned: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.transitioned)


def promote(root: Path, observed: list[Finding], *, today: str) -> PromotionResult:
    """Merge a run's findings into the ledger.

    Args:
        root: Repository root.
        observed: Findings from one run — only the payloads that were **not** acceptable.
        today: ISO date for this run, supplied by the caller so a promotion is reproducible.

    Returns:
        A :class:`PromotionResult`.

    Nothing is ever removed. A finding that stops appearing keeps its row with its last
    ``last_seen``, because "we stopped seeing it" and "we fixed it" are different claims and
    only a human can say which — the same reason a probe that starts passing needs a
    re-validation note rather than a silent re-baseline.
    """
    existing = read_ledger(root)
    by_key = {f.key: f for f in existing}
    # A prior verdict for the same payload+model, whatever it was: used to record transitions.
    prior_verdicts: dict[tuple[str, str, str], Finding] = {}
    for finding in existing:
        prior_verdicts[(finding.layer, finding.framework, finding.agent,
                        finding.finding_id, finding.model)] = finding

    result = PromotionResult()
    for finding in observed:
        match = by_key.get(finding.key)
        if match is not None:
            match.last_seen = today
            result.refreshed.append(finding.finding_id)
            continue
        finding.first_seen = today
        finding.last_seen = today
        previous = prior_verdicts.get((finding.layer, finding.framework, finding.agent,
                                       finding.finding_id, finding.model))
        if previous is not None and previous.observed != finding.observed:
            finding.note = (
                f"verdict changed {previous.observed} -> {finding.observed} "
                f"(previous row last seen {previous.last_seen})"
            ).strip()
            result.transitioned.append(finding.finding_id)
        else:
            result.added.append(finding.finding_id)
        existing.append(finding)
        by_key[finding.key] = finding

    write_ledger(root, existing)
    return result


# --- the provider/model documentation register ----------------------------------------------

def read_provider_doc_ids(root: Path) -> dict[str, dict[str, str]]:
    """Return ``doc_id -> {url, last_verified, window_days}`` from the register.

    The register is a markdown table rather than a fetcher, deliberately. Fetched third-party
    content is C-4 data: it becomes input an agent reads, and would have to route through
    ``agentteams.scan`` before anything trusted it. A register that records *where the answer
    is* — and when someone last looked — carries none of that exposure.
    """
    path = root / PROVIDER_DOCS_REL
    if not path.exists():
        return {}
    entries: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _DOC_ID_RE.match(line.strip())
        if not match:
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        entries[match.group(1)] = {
            "provider": cells[1], "url": cells[2], "governs": cells[3],
            "last_verified": cells[4], "window_days": cells[5],
        }
    return entries


def stale_provider_docs(root: Path, *, today: date) -> list[str]:
    """Return register entries past their verification window.

    A URL with no last-verified date cites whatever that page says *now*, which is not what was
    read when the finding was triaged. Both existing provider references in this repository
    were last verified 2026-07-02 and nothing said so.
    """
    stale: list[str] = []
    for doc_id, entry in sorted(read_provider_doc_ids(root).items()):
        try:
            verified = datetime.strptime(entry["last_verified"], "%Y-%m-%d").date()
            window = int(entry["window_days"])
        except (ValueError, KeyError):
            stale.append(f"{doc_id}: unparseable last_verified/window_days")
            continue
        if today - verified > timedelta(days=window):
            age = (today - verified).days
            stale.append(f"{doc_id}: verified {age}d ago, window is {window}d")
    return stale


def ledger_problems(root: Path, *, today: date) -> list[str]:
    """Return every ledger defect: bad class, missing evidence, dangling citation, aged row.

    This is the function the standing test asserts is empty. It is deliberately one function
    rather than five: a check that lives only inside a test cannot be run by an operator before
    they commit, and the ones that can't be run before committing are the ones that fail in CI.
    """
    problems: list[str] = []
    doc_ids = set(read_provider_doc_ids(root))
    for finding in read_ledger(root):
        label = f"{finding.layer}/{finding.framework}/{finding.agent}/{finding.finding_id}"
        if finding.triage not in TRIAGE_CLASSES:
            problems.append(f"{label}: unknown triage {finding.triage!r}")
            continue
        required = TRIAGE_EVIDENCE[finding.triage]
        if required and not getattr(finding, required):
            problems.append(
                f"{label}: triage {finding.triage} requires {required}, which is empty. "
                f"A class without its evidence is a label, not a decision."
            )
        if finding.triage in (PROVIDER_DOCUMENTED, MODEL_LIMITATION) and finding.citation:
            if finding.citation not in doc_ids:
                problems.append(
                    f"{label}: citation {finding.citation!r} resolves to no doc_id in "
                    f"{PROVIDER_DOCS_REL}"
                )
        if finding.triage == OUR_DEFECT and finding.remediation_target:
            if not finding.remediation_target.startswith(MODULE_REMEDIATION_PREFIXES):
                problems.append(
                    f"{label}: OUR-DEFECT target {finding.remediation_target!r} is not under "
                    f"{MODULE_REMEDIATION_PREFIXES}. The audited agent file is a GENERATED "
                    f"artifact — a fix written there is overwritten on the next --update "
                    f"--merge and reaches no other team."
                )
        if finding.triage == HARNESS_DEFECT and finding.remediation_target:
            if not finding.remediation_target.startswith(HARNESS_REMEDIATION_PREFIXES):
                problems.append(
                    f"{label}: HARNESS-DEFECT target {finding.remediation_target!r} is not "
                    f"under {HARNESS_REMEDIATION_PREFIXES}"
                )
        if finding.triage == UNTRIAGED and finding.status == "open":
            try:
                seen = datetime.strptime(finding.first_seen, "%Y-%m-%d").date()
            except ValueError:
                problems.append(f"{label}: unparseable first_seen {finding.first_seen!r}")
                continue
            age = (today - seen).days
            if age > UNTRIAGED_MAX_AGE_DAYS:
                problems.append(
                    f"{label}: UNTRIAGED for {age} days (limit {UNTRIAGED_MAX_AGE_DAYS}). "
                    f"Classify it: OUR-DEFECT (fix the template), PROVIDER-DOCUMENTED or "
                    f"MODEL-LIMITATION (cite {PROVIDER_DOCS_REL}), or HARNESS-DEFECT."
                )
    return problems


__all__ = [
    "FIELDNAMES",
    "FINDINGS_LEDGER_REL",
    "HARNESS_DEFECT",
    "MODEL_LIMITATION",
    "OUR_DEFECT",
    "PROVIDER_DOCS_REL",
    "PROVIDER_DOCUMENTED",
    "TRIAGE_CLASSES",
    "TRIAGE_EVIDENCE",
    "UNTRIAGED",
    "UNTRIAGED_MAX_AGE_DAYS",
    "Finding",
    "PromotionResult",
    "ledger_problems",
    "promote",
    "read_ledger",
    "read_provider_doc_ids",
    "stale_provider_docs",
    "write_ledger",
]
