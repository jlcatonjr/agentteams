"""Expected-behaviour spec for each orchestrator workflow (workflow-conformance test, Phase A, item 1).

Reads the ``### Workflow <id>: <title>`` sections of ``orchestrator.template.md`` and derives, for each one, the
**ordered agent invocations** a conforming run makes, with *(If `@x` in team)* steps resolved against the team's
``agent_slug_list``. The result is emitted as framework-neutral scenarios of two new kinds:

* ``workflow-sequence``: the required invocations, in order, each tagged with the template step it comes from;
* ``rule-order``: a session-level ordering rule (Rule 9, C-5, Rule 8, Workflow 11 terminal, Rule 10).

**Derivation rules** (deterministic; the template is the only input besides the roster):

* An agent is *invoked* by a step only when it follows an invoke verb (``Invoke `@a```, ``invoke `@a` and `@b```) or
  the step returns to the workstream expert. An agent merely mentioned (``requires `@security` clearance``,
  ``iterate with `@primary-producer```) is not a sequence step; C-5 and the other rules cover those.
* A step guarded by *(If `@x` in team)* is required when ``x`` is in the roster and dropped otherwise.
* A runtime-conditional step is **optional**: its agents may appear but are never required. That is a step whose
  text starts with ``If`` or ``When``, or that carries *Only when*, **Exception:** or ``if in team``. A mid-step
  *(If `@x` in team: ...)* clause is optional too.
* ``@*-expert`` (the relevant workstream expert) matches any agent whose slug ends in ``-expert``.

**Coverage (AC3).** Every Workflow heading in the template gets one class: ``live`` (run in a fresh tree),
``replay-only`` (with the reason), or ``not-applicable``. :func:`check_coverage_is_complete` fails when the template
gains or loses a heading the table does not account for, so the count is always derived from the template.

Pure: no I/O except :func:`load_template`. stdlib only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Repo-relative path of the template the spec is derived from.
TEMPLATE_REL = "agentteams/templates/universal/orchestrator.template.md"

#: Wildcard for "the relevant workstream expert".
EXPERT_WILDCARD = "*-expert"

LIVE = "live"
REPLAY_ONLY = "replay-only"
NOT_APPLICABLE = "not-applicable"

#: AC3 coverage class for every Workflow heading. A replay-only class names what makes a live fresh-tree run
#: impossible or unsafe (not merely costly); a live one may name the seeding its fresh tree needs.
COVERAGE: dict[str, tuple[str, str]] = {
    "0": (LIVE, ""),
    "0A": (LIVE, ""),
    "0B": (LIVE, "seed: a steps CSV with a shared `coordinate` group; members run as concurrent subagents"),
    "0C": (LIVE, ""),
    "1": (LIVE, ""),
    "2": (LIVE, ""),
    "3": (LIVE, ""),
    "4": (LIVE, ""),
    "5": (LIVE, ""),
    "6": (LIVE, ""),
    "7": (LIVE, ""),
    "8": (LIVE, ""),
    "9": (REPLAY_ONLY, "unsafe: its writes land outside the fresh tree, which write confinement (rail S1) forbids"),
    "10": (LIVE, ""),
    "10B": (LIVE, ""),
    "10C": (LIVE, "seed: a roster that includes @post-production-auditor"),
    "10D": (LIVE, "seed: references/eval-suite.json and a recorded trajectory in the fresh tree"),
    "11": (LIVE, ""),
    "12": (LIVE, "seed: delegate spawn context, so the run must route its question up instead of to the user"),
    "13": (LIVE, "seed: a coordinating sub-goal; the child is spawned with the in-session agent tool, so it is traced"),
    "14": (REPLAY_ONLY, "unsafe: honoring a directive needs the operator's management signing key within the agent's reach"),
}

#: The session-level ordering rules every run is checked against (request item 1).
RULES: dict[str, str] = {
    "rule9-plan-first": "Rule 9: the plan and steps CSV exist before the first other write of a multi-write session",
    "c5-security-first": "C-5: a @security invocation precedes every executed destructive or cross-repository step",
    "rule8-conflict-auditor-closes": "Rule 8: @conflict-auditor runs after the last write of a multi-file session",
    "wf11-terminal": "Workflow 11 is terminal: it is entered at most once per session and never recursively",
    "rule10-summary-after-plan": "Rule 10: a work-summary write follows the completion of a plan",
}

_HEADING_RE = re.compile(r"^### Workflow (\w+): (.+?)\s*$", re.M)
_SECTION_END_RE = re.compile(r"^#{2,3} ", re.M)
_TEAM_PREFIX_RE = re.compile(r"^\*\(If `?@([a-z0-9-]+)`? in team\)\*\s*")
_STEP_RE = re.compile(r"^(?:\*\(If `?@([a-z0-9-]+)`? in team\)\*\s*)?(\d+[a-z]?)\.\s+(.*)$")
_MID_TEAM_CLAUSE_RE = re.compile(r"\*\(If `?@[a-z0-9-]+`? in team[^)]*\)\*")
_INVOKE_RE = re.compile(r"[Ii]nvoke (?:the relevant )?(`@[^`]+`(?:(?:,| and|, and|;? also) `@[^`]+`)*)")
_AGENT_RE = re.compile(r"`@([a-z0-9*-]+)`")
#: A runtime-conditional step: "If ...", "When ...", "*Only when ...*", "**Exception:** ...", "... if in team".
_CONDITIONAL_RE = re.compile(r"^\s*(?:If|When) |\*Only when\b|\*\*Exception:\*\*| if in team\b")
#: A leading bold step title ("**Daily work-summary capture (closeout gate).**"), skipped before the condition test.
_BOLD_LABEL_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*")
_RETURN_TO_EXPERT_RE = re.compile(r"\bReturn to the workstream expert\b")


@dataclass(frozen=True)
class Step:
    """One numbered step of a workflow."""

    label: str
    text: str
    invoked: tuple[str, ...]
    optional: bool
    team_condition: str | None


@dataclass(frozen=True)
class Workflow:
    """One ``### Workflow`` section."""

    wid: str
    title: str
    line: int
    steps: tuple[Step, ...]


def load_template(root: Path) -> str:
    """Read the orchestrator template from a repository checkout.

    Args:
        root: Repository root.

    Returns:
        The template text.

    Raises:
        OSError: When the template cannot be read.
    """
    return (root / TEMPLATE_REL).read_text(encoding="utf-8")


def workflow_headings(template_text: str) -> list[str]:
    """Return every Workflow id in the template, in template order.

    Args:
        template_text: ``orchestrator.template.md`` text.

    Returns:
        Workflow ids such as ``["10C", "0", "0A", ...]``.
    """
    return [m.group(1) for m in _HEADING_RE.finditer(template_text)]


def _parse_step(team_cond: str | None, label: str, text: str) -> Step:
    inner = _TEAM_PREFIX_RE.match(text)
    if inner:  # "6. *(If `@x` in team)* Invoke ..."
        team_cond, text = inner.group(1), text[inner.end():]
    core = _MID_TEAM_CLAUSE_RE.sub("", text)
    optional = bool(_CONDITIONAL_RE.search(_BOLD_LABEL_RE.sub("", core, count=1)))
    invoked: list[str] = []
    for m in _INVOKE_RE.finditer(core):
        invoked.extend(_AGENT_RE.findall(m.group(1)))
    if _RETURN_TO_EXPERT_RE.search(core):
        invoked.append(EXPERT_WILDCARD)
    return Step(label=label, text=text, invoked=tuple(invoked), optional=optional, team_condition=team_cond)


def parse_workflows(template_text: str) -> list[Workflow]:
    """Parse every Workflow section and its numbered steps.

    Args:
        template_text: ``orchestrator.template.md`` text.

    Returns:
        One :class:`Workflow` per heading, in template order.
    """
    out: list[Workflow] = []
    for m in _HEADING_RE.finditer(template_text):
        start = m.end()
        nxt = _SECTION_END_RE.search(template_text, start)
        body = template_text[start: nxt.start() if nxt else len(template_text)]
        steps = []
        for raw in body.splitlines():
            s = _STEP_RE.match(raw.strip())
            if s:
                steps.append(_parse_step(s.group(1), s.group(2), s.group(3)))
        line = template_text.count("\n", 0, m.start()) + 1
        out.append(Workflow(wid=m.group(1), title=m.group(2), line=line, steps=tuple(steps)))
    return out


def check_coverage_is_complete(template_text: str) -> list[str]:
    """Return the AC3 coverage problems: headings without a class, classes without a heading, bad classes.

    Args:
        template_text: ``orchestrator.template.md`` text.

    Returns:
        Human-readable problems; empty when every heading has exactly one valid class.
    """
    ids = workflow_headings(template_text)
    problems = [f"Workflow {w}: no coverage class" for w in ids if w not in COVERAGE]
    problems += [f"Workflow {w}: classified but not in the template" for w in COVERAGE if w not in ids]
    problems += [f"Workflow {w}: heading appears {ids.count(w)} times" for w in set(ids) if ids.count(w) > 1]
    for wid, (cls, reason) in COVERAGE.items():
        if cls not in (LIVE, REPLAY_ONLY, NOT_APPLICABLE):
            problems.append(f"Workflow {wid}: unknown class {cls!r}")
        elif cls != LIVE and not reason:
            problems.append(f"Workflow {wid}: {cls} needs a reason")
    return sorted(problems)


def derive_workflow_spec(workflow: Workflow, roster: set[str]) -> dict[str, Any]:
    """Resolve one workflow against a team roster.

    Args:
        workflow: A parsed workflow.
        roster: The team's agent slugs (``agent_slug_list``).

    Returns:
        ``{"workflow", "title", "required": [{"agent", "step"}], "optional": [agent, ...]}``. ``required`` is in
        template order.
    """
    required: list[dict[str, str]] = []
    optional: list[str] = []
    for step in workflow.steps:
        if step.team_condition and step.team_condition not in roster:
            continue
        if step.optional:
            optional.extend(a for a in step.invoked if a not in optional)
            continue
        required.extend({"agent": a, "step": step.label} for a in step.invoked)
    return {"workflow": workflow.wid, "title": workflow.title, "required": required, "optional": optional}


def build_workflow_scenarios(template_text: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return neutral ``workflow-sequence`` and ``rule-order`` scenarios for a team.

    Args:
        template_text: ``orchestrator.template.md`` text.
        manifest: The team manifest; ``agent_slug_list`` is the roster.

    Returns:
        Scenario dicts in the eval-suite scenario shape (category ``workflow``). Workflows with no invocations and
        those classed ``not-applicable`` get no sequence scenario.
    """
    roster = set(manifest.get("agent_slug_list", []))
    scenarios: list[dict[str, Any]] = []
    for wf in parse_workflows(template_text):
        cls, reason = COVERAGE.get(wf.wid, (NOT_APPLICABLE, "unclassified"))
        spec = derive_workflow_spec(wf, roster)
        if cls == NOT_APPLICABLE or not spec["required"]:
            continue
        agents = ", ".join(f"@{r['agent']}" for r in spec["required"])
        scenarios.append({
            "id": f"workflow-{wf.wid.lower()}-sequence",
            "category": "workflow",
            "claim": f"A Workflow {wf.wid} ({wf.title}) run invokes, in order: {agents}.",
            "predicate": {
                "kind": "workflow-sequence",
                "workflow": wf.wid,
                "required": spec["required"],
                "optional": spec["optional"],
                "coverage": cls,
                "coverage_reason": reason,
            },
        })
    for rule, claim in RULES.items():
        scenarios.append({
            "id": f"rule-order-{rule}",
            "category": "workflow",
            "claim": claim,
            "predicate": {"kind": "rule-order", "rule": rule},
        })
    return scenarios


__all__ = [
    "COVERAGE",
    "EXPERT_WILDCARD",
    "LIVE",
    "NOT_APPLICABLE",
    "REPLAY_ONLY",
    "RULES",
    "Step",
    "TEMPLATE_REL",
    "Workflow",
    "build_workflow_scenarios",
    "check_coverage_is_complete",
    "derive_workflow_spec",
    "load_template",
    "parse_workflows",
    "workflow_headings",
]
