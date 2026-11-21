"""Expected-behaviour spec for each orchestrator workflow (workflow-conformance test, Phase A, item 1).

Reads the ``### Workflow <id>: <title>`` sections of ``orchestrator.template.md`` and derives, for each one, the
**ordered agent invocations** a conforming run makes, with *(If `@x` in team)* steps resolved against the team's
``agent_slug_list``. The result is emitted as framework-neutral scenarios of two new kinds:

* ``workflow-sequence``: the required invocations, in order, each tagged with the template step it comes from;
* ``rule-order``: a session-level ordering rule (Rule 9, C-5, Rule 8, Workflow 11 terminal, Rule 10).

**Derivation rules** (deterministic; the template is the only input besides the roster):

* A step is its numbered line plus the wrapped lines under it, up to a blank line or the next step.
* An agent is *invoked* by a step only when it follows an invoke verb (``Invoke `@a```, ``run `@a` and `@b```) or
  the step returns to the workstream expert. An agent merely mentioned (``requires `@security` clearance``,
  ``iterate with `@primary-producer```) is not a sequence step; C-5 and the other rules cover those.
* A step guarded by *(If `@x` in team)* is required when ``x`` is in the roster and dropped otherwise.
* A runtime-conditional step is **optional**: its agents may appear but are never required. That is a step whose
  text starts with ``If`` or ``When``, or that carries *Only when*, **Exception:**, ``before marking any step`` or
  ``if in team``. A mid-step *(If `@x` in team: ...)* clause is optional too.
* Steps under a ``####`` subsection whose note reads *(Skip ... if ...)* are optional.
* **Routes are expanded.** An unconditional ``→ **Standard Doc-Sync Closeout**``, ``→ **Post-Deliverable
  Retrospective**`` or ``→ **Invoke Workflow 11**`` adds the routed section's own required invocations (labelled
  ``<section>.<step>``), so a run that skips Final Check is missing agents rather than passing a rule as n/a.
  Workflow 11 is terminal and routes nowhere. The retrospective's own invocations are optional, because its step 2
  skips them when there is nothing to retrospect; its route on to the closeout is still required.
* ``@*-expert`` (the relevant workstream expert) matches any agent whose slug ends in ``-expert``.

**Coverage (AC3).** Every Workflow heading in the template gets one class: ``live`` (run in a fresh tree),
``replay-only`` (with the reason), or ``not-applicable``. :func:`check_coverage_is_complete` fails when the template
gains or loses a heading the table does not account for, so the count is always derived from the template.

Pure: no I/O except :func:`load_template`. stdlib only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
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
#: Sections a workflow step can route to, by the bold name the step uses.
CLOSEOUT = "Standard Doc-Sync Closeout"
RETROSPECTIVE = "Post-Deliverable Retrospective"
FINAL_CHECK = "Workflow 11"
#: A subsection note that makes its steps skippable: "*(Skip Part A if no plan was active ...)*".
_SKIP_NOTE_RE = re.compile(r"^\*\(Skip .+ if ")
_ROUTE_RE = re.compile(r"→ \*\*(?:Invoke )?(Standard Doc-Sync Closeout|Post-Deliverable Retrospective|Workflow 11)\b")
#: A routed section whose own invocations are optional (its step 2 skips them when there is nothing to do).
_OPTIONAL_BODY_ROUTES = frozenset({RETROSPECTIVE})
_SECTION_END_RE = re.compile(r"^#{2,3} ", re.M)
_TEAM_PREFIX_RE = re.compile(r"^\*\(If `?@([a-z0-9-]+)`? in team\)\*\s*")
_STEP_RE = re.compile(r"^(?:\*\(If `?@([a-z0-9-]+)`? in team\)\*\s*)?(\d+[a-z]?)\.\s+(.*)$")
_MID_TEAM_CLAUSE_RE = re.compile(r"\*\(If `?@[a-z0-9-]+`? in team[^)]*\)\*")
_INVOKE_RE = re.compile(r"(?:[Ii]nvoke|[Rr]un) (?:the relevant )?(`@[^`]+`(?:(?:,| and|, and|;? also) `@[^`]+`)*)")
_AGENT_RE = re.compile(r"`@([a-z0-9*-]+)`")
#: A runtime-conditional step: "If ...", "When ...", "*Only when ...*", "**Exception:** ...", "... if in team".
_CONDITIONAL_RE = re.compile(
    r"^\s*(?:If|When) |\*Only when\b|\*\*Exception:\*\*| if in team\b|\bbefore marking any step\b")
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
    routes: tuple[str, ...] = ()


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
    routes = tuple(m.group(1) for m in _ROUTE_RE.finditer(core))
    return Step(label=label, text=text, invoked=tuple(invoked), optional=optional, team_condition=team_cond,
                routes=routes)


def _steps(body: str) -> tuple[Step, ...]:
    """Parse the numbered steps of one section body, joining each step's wrapped lines."""
    raw: list[tuple[str | None, str, str]] = []
    skippable: set[int] = set()  # indexes of steps under a "*(Skip ... if ...)*" subsection note
    in_skip = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("####"):
            in_skip = False
        elif _SKIP_NOTE_RE.match(stripped):
            in_skip = True
        m = _STEP_RE.match(stripped)
        if m:
            if in_skip:
                skippable.add(len(raw))
            raw.append((m.group(1), m.group(2), m.group(3)))
        elif stripped and raw and not stripped.startswith(("#", "|", "<!--")):
            team, label, text = raw[-1]
            raw[-1] = (team, label, f"{text} {stripped}")
        elif not stripped and raw and raw[-1][1] != "":
            raw.append((None, "", ""))  # a blank line ends the step; the sentinel is dropped below
    steps = []
    for i, (team, label, text) in enumerate(raw):
        if label:
            step = _parse_step(team, label, text)
            steps.append(replace(step, optional=True) if i in skippable else step)
    return tuple(steps)


def _section_body(template_text: str, start: int) -> str:
    nxt = _SECTION_END_RE.search(template_text, start)
    return template_text[start: nxt.start() if nxt else len(template_text)]


def parse_routes(template_text: str) -> dict[str, Workflow]:
    """Parse the sections a step can route to (the closeout, the retrospective and Workflow 11).

    Args:
        template_text: ``orchestrator.template.md`` text.

    Returns:
        ``{route name: Workflow}`` for each route present in the template.
    """
    out: dict[str, Workflow] = {}
    for name in (CLOSEOUT, RETROSPECTIVE):
        m = re.search(rf"^### {re.escape(name)}\s*$", template_text, re.M)
        if m:
            out[name] = Workflow(wid=name, title=name, line=template_text.count("\n", 0, m.start()) + 1,
                                 steps=_steps(_section_body(template_text, m.end())))
    final = next((w for w in parse_workflows(template_text) if w.wid == "11"), None)
    if final:
        out[FINAL_CHECK] = final
    return out


def parse_workflows(template_text: str) -> list[Workflow]:
    """Parse every Workflow section and its numbered steps.

    Args:
        template_text: ``orchestrator.template.md`` text.

    Returns:
        One :class:`Workflow` per heading, in template order.
    """
    out: list[Workflow] = []
    for m in _HEADING_RE.finditer(template_text):
        line = template_text.count("\n", 0, m.start()) + 1
        out.append(Workflow(wid=m.group(1), title=m.group(2), line=line,
                            steps=_steps(_section_body(template_text, m.end()))))
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


def _route_label(route: str) -> str:
    return {CLOSEOUT: "closeout", RETROSPECTIVE: "retrospective", FINAL_CHECK: "11"}[route]


def derive_workflow_spec(workflow: Workflow, roster: set[str],
                         routes: dict[str, Workflow] | None = None) -> dict[str, Any]:
    """Resolve one workflow against a team roster, expanding unconditional routes.

    Args:
        workflow: A parsed workflow.
        roster: The team's agent slugs (``agent_slug_list``).
        routes: From :func:`parse_routes`; without it, routes are not expanded.

    Returns:
        ``{"workflow", "title", "required": [{"agent", "step"}], "optional": [agent, ...], "routes": [...]}``.
        ``required`` is in template order; a routed section's entries are labelled ``<section>.<step>``.
    """
    required: list[dict[str, str]] = []
    optional: list[str] = []
    followed: list[str] = []

    def walk(wf: Workflow, prefix: str, body_optional: bool, depth: int) -> None:
        for step in wf.steps:
            if step.team_condition and step.team_condition not in roster:
                continue
            label = f"{prefix}{step.label}"
            if step.optional or body_optional:
                optional.extend(a for a in step.invoked if a not in optional)
            else:
                required.extend({"agent": a, "step": label} for a in step.invoked)
            if step.optional or not routes or depth > 3:
                continue
            for route in step.routes:
                target = routes.get(route)
                if target is None or target.wid == wf.wid or (route == FINAL_CHECK and workflow.wid == "11"):
                    continue
                followed.append(route)
                walk(target, f"{_route_label(route)}.", route in _OPTIONAL_BODY_ROUTES, depth + 1)

    walk(workflow, "", False, 0)
    return {"workflow": workflow.wid, "title": workflow.title, "required": required, "optional": optional,
            "routes": followed}


def build_workflow_scenarios(template_text: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return neutral ``workflow-sequence`` and ``rule-order`` scenarios for a team.

    Args:
        template_text: ``orchestrator.template.md`` text.
        manifest: The team manifest; ``agent_slug_list`` is the roster.

    Returns:
        Scenario dicts in the eval-suite scenario shape (category ``workflow``). Every workflow not classed
        ``not-applicable`` gets a sequence scenario; one with no required invocation is checked by the rules only.
    """
    roster = set(manifest.get("agent_slug_list", []))
    routes = parse_routes(template_text)
    scenarios: list[dict[str, Any]] = []
    for wf in parse_workflows(template_text):
        cls, reason = COVERAGE.get(wf.wid, (NOT_APPLICABLE, "unclassified"))
        spec = derive_workflow_spec(wf, roster, routes)
        if cls == NOT_APPLICABLE:
            continue
        agents = ", ".join(f"@{r['agent']}" for r in spec["required"]) or "no required invocation (rules only)"
        scenarios.append({
            "id": f"workflow-{wf.wid.lower()}-sequence",
            "category": "workflow",
            "claim": f"A Workflow {wf.wid} ({wf.title}) run invokes, in order: {agents}.",
            "predicate": {
                "kind": "workflow-sequence",
                "workflow": wf.wid,
                "required": spec["required"],
                "optional": spec["optional"],
                "routes": spec["routes"],
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
    "CLOSEOUT",
    "FINAL_CHECK",
    "RETROSPECTIVE",
    "build_workflow_scenarios",
    "check_coverage_is_complete",
    "derive_workflow_spec",
    "load_template",
    "parse_routes",
    "parse_workflows",
    "workflow_headings",
]
