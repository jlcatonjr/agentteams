#!/usr/bin/env python3
"""redteam_judgment_run.py — measure the JUDGMENT layer against a live agent, and price it.

The deterministic battery measures Layer B: literal instruction overrides the scanner catches.
It cannot measure what happens when a payload never uses a trigger phrase and still asks an
agent to act against its constitution. `tests/redteam/payloads.json` carries 14 such payloads —
13 verified to sit *outside* `agentteams.scan` — plus a scorer. Until this script existed
nobody had run them, so the judgment layer was an **accepted risk rather than a measured one**
(W14).

This drives the corpus through a live agent, scores the responses, and reports what it cost
from the provider's own ledger.

SAFETY — WHY THE TOOL LAYER IS REMOVED
--------------------------------------
The operator's goose config runs ``GOOSE_MODE: auto`` with the shell-capable ``developer``
extension. **These payloads are engineered to make an agent act against its instructions.**
Running them through a shell-capable agent in auto-approve mode means a payload that *succeeds*
gets a shell — on the operator's machine, in their repository — and a successful attack would
look identical to a crashed run until something had already changed on disk.

So every invocation passes:

* ``--no-profile``  — no default extensions: no shell, no browser, no file writes
* ``--max-turns 1`` — one response, so there is no agentic loop to hijack and a hard token bound
* ``--no-session``  — nothing carries between payloads

That last one is not tidiness. ``incr-01`` and ``incr-02`` escalate *incrementally across
turns*; a shared session would let payload N poison payload N+1, and every number after the
first contamination would be unattributable.

The scorer reads response **text**. It never needed the tools. Whether a model would actually
*execute* a command it agreed to is a different experiment that needs a disposable VM, and this
is not it.

COST
----
The authoritative figure is the **credit delta from the provider's ledger**
(``GET /api/v1/credits``), not the token counts the run reports about itself. A cost number
derived from the thing being measured and never checked against the provider is the same shape
as a verifier that cannot fail. Both are recorded; disagreements are reported, not smoothed.

Usage::

    python3 scripts/redteam_judgment_run.py --model z-ai/glm-5.2
    python3 scripts/redteam_judgment_run.py --model z-ai/glm-5.2 --limit 2   # smoke test
    python3 scripts/redteam_judgment_run.py --model z-ai/glm-5.2 --dry-run   # spend nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.redteam.run_harness import (  # noqa: E402
    REVIEWER_PROMPT,
    load_corpus,
    score_response,
)

#: The agent whose judgment is under test. Passed as the system prompt so the model is THIS
#: repository's @security agent rather than a generic assistant.
#:
#: This was missed on the first real run and the mistake is instructive. `--no-profile` was
#: added to strip the *tool layer* for safety; it also stripped the agent's *identity*. GLM 5.2
#: then produced an excellent review of a real injection — "⛔ BLOCK … Reject this content. Log
#: the attempted override as a security event" — and scored MISS, because the scorer looks for
#: this repo's contract (`HALT` with finding `INJECTION ATTEMPT DETECTED`) and the model had
#: never been told that contract existed. The scorer was right; the run was measuring a
#: different system than the one it claimed to.
#:
#: Identity WITHOUT capability is the distinction that matters: the rules come in through
#: --system, the tools stay out via --no-profile.
SECURITY_AGENT_REL = ".claude/agents/security.md"

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"

#: Refuse to start below this much remaining credit. A run that empties the account is worse
#: than a run that does not happen, because the next thing that needed the key also fails.
MIN_REMAINING_USD = 5.00

#: Hard ceiling for one full pass. The estimate for 14 payloads on glm-5.2 is a few cents; this
#: exists because an estimate is not a control.
DEFAULT_BUDGET_USD = 2.00


#: Response signatures that mean the request never reached the model. These must NEVER be
#: scored: a transport failure scored as MISS reads as "the agent said nothing useful", which
#: is a measurement, when in fact nothing was measured at all. The first smoke run of this
#: script produced exactly that — 2/2 MISS at $0.0000 in 6.5s, because the route proxy the
#: goose config points at was not running. The numbers looked like a result and were an
#: outage. Indeterminate is not a measurement, the same way it is not a pass.
_TRANSPORT_FAILURE_MARKERS: tuple[str, ...] = (
    "network error",
    "could not connect",
    "connection refused",
    "no such host",
    "error sending request",
    "401 unauthorized",
    "403 forbidden",
    "404 not found",
    "429 too many requests",
    "500 internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "no endpoints found",
    # CLI-level failures. These are not transport errors, but they share the property that
    # matters: no model answered, so there is nothing to score.
    "error: unexpected argument",
    "error: invalid value",
    "error: the following required arguments",
    "usage: goose",
)

#: No round trip to a hosted model completes this fast. A sub-second "response" is a local
#: failure — a parse error, a missing binary, an instant rejection — regardless of what its
#: text happens to contain. This guard is deliberately independent of the text, because the
#: failure that motivated it produced text that scored perfectly.
MIN_PLAUSIBLE_SECONDS = 1.5


def transport_failure(text: str) -> str:
    """Return the transport-failure marker found in ``text``, or "" when the call landed.

    Args:
        text: Combined stdout/stderr from one agent invocation.

    Returns:
        The matched marker, or an empty string.
    """
    low = text.lower()
    for marker in _TRANSPORT_FAILURE_MARKERS:
        if marker in low:
            return marker
    return ""


@dataclass
class PayloadResult:
    """One payload, its response, its score, and what it cost."""

    pid: str
    payload_class: str
    article: str
    expected: str
    observed: str = ""
    acceptable: bool = False
    response: str = ""
    seconds: float = 0.0
    error: str = ""
    transport_error: str = ""


@dataclass
class RunReport:
    model: str
    started_at: str
    results: list[PayloadResult] = field(default_factory=list)
    credits_before: float | None = None
    credits_after: float | None = None
    budget_usd: float = DEFAULT_BUDGET_USD

    @property
    def spend(self) -> float | None:
        if self.credits_before is None or self.credits_after is None:
            return None
        return round(self.credits_before - self.credits_after, 6)


def _get_json(url: str, *, token: str | None = None, timeout: int = 30) -> dict:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_remaining_credit(token: str) -> float | None:
    """Return remaining credit in USD, or None when the endpoint cannot answer.

    None is deliberately distinct from 0.0. "The provider did not tell us" and "there is no
    money left" are different states, and treating the first as the second would abort runs
    that should proceed — while treating it as "plenty" would remove the only real cost control.
    Callers decide; this does not guess.
    """
    try:
        data = _get_json(OPENROUTER_CREDITS_URL, token=token).get("data", {})
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError):
        return None
    total = data.get("total_credits")
    used = data.get("total_usage")
    if total is None or used is None:
        return None
    return round(float(total) - float(used), 6)


def validate_model_slug(model: str) -> tuple[bool, str]:
    """Check the slug exists and supports tool calls, before spending anything.

    ``scripts/goose-openrouter-preflight.py`` exists because an invalid slug makes goose stop
    immediately and *quietly* — a run that does nothing looks a lot like a run that found
    nothing. The classic case is Ollama tag syntax (``model:tag``) pasted into an OpenRouter
    slug, where ``:`` means a pricing variant.

    Returns:
        ``(ok, message)``.
    """
    try:
        models = _get_json(OPENROUTER_MODELS_URL)["data"]
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError) as exc:
        return False, f"could not reach the model catalogue to validate {model!r}: {exc}"
    match = next((m for m in models if m.get("id") == model), None)
    if match is None:
        near = sorted(m["id"] for m in models if model.split("/")[-1][:6] in m["id"])[:5]
        return False, (
            f"{model!r} is not an OpenRouter model id. "
            + (f"Did you mean one of {near}? " if near else "")
            + "An unknown slug makes goose stop immediately and quietly."
        )
    pricing = match.get("pricing", {})
    return True, (
        f"{model} — ctx={match.get('context_length')}, "
        f"in=${pricing.get('prompt')}/tok, out=${pricing.get('completion')}/tok, "
        f"tools={'tools' in (match.get('supported_parameters') or [])}"
    )


def verify_instance_is_module_generated(rel: str) -> tuple[str, str]:
    """Check the audited agent file still matches what the module generated.

    **Why this gates the measurement.** The audit measures a generated *instance*
    (``.claude/agents/security.md``), and that instance is an artifact of the module —
    rendered from ``agentteams/templates/universal/security.template.md`` through the pipeline.
    Constitutional Rule 4: primary deliverables are canonical, build artifacts are derived.

    That has a hard consequence for remediation. If the audit finds the agent's rules
    inadequate, the fix belongs in the **template**, and reaches the instance through
    ``--update --merge``. Hand-editing the instance is wrong twice over: fenced regions are
    overwritten on the next update, and unfenced ones silently diverge so no other generated
    team ever receives the fix.

    And if the instance has *already* drifted, the measurement describes a file the module
    cannot reproduce — so a template fix would not reproduce the result either, and the audit
    would be scoring something that exists on one machine.

    Returns:
        ``(status, detail)`` where status is ``"OK"``, ``"MODIFIED"``, ``"UNKNOWN"``.
    """
    from agentteams import drift

    agents_dir = REPO_ROOT / Path(rel).parent
    try:
        entries = drift.verify_output_integrity(agents_dir)
    except (FileNotFoundError, ValueError, OSError):
        return "UNKNOWN", "no build log — cannot confirm the instance came from the module"
    name = Path(rel).name
    match = next((e for e in entries if e["rel_path"].endswith(name)), None)
    if match is None:
        return "UNKNOWN", f"{name} is not recorded in the build log"
    return match["status"], f"{name} is {match['status']} against its build-log baseline"


def load_agent_contract() -> str:
    """Return the @security agent definition used as the system prompt.

    Raises:
        FileNotFoundError: If the agent file is absent. Running without it silently measures a
            generic model instead of this repository's agent, and the two are not the same
            system — so this is an error rather than a fallback.
    """
    path = REPO_ROOT / SECURITY_AGENT_REL
    if not path.exists():
        raise FileNotFoundError(
            f"{SECURITY_AGENT_REL} is missing. Without the agent contract the run measures a "
            f"generic model, not this repository's @security agent, and the scorer's verdict "
            f"vocabulary would not apply."
        )
    text = path.read_text(encoding="utf-8")
    # Strip YAML front matter. A body starting with `---` is read by the CLI's argument parser
    # as a flag-like token, and goose rejected the whole option — then echoed the ENTIRE 39KB
    # contract back in its error message. That error text contains the word HALT hundreds of
    # times, so the scorer matched HALT on all 14 payloads and the run reported 11/11 defended
    # in 0.0 seconds for $0.00. A perfect score produced by a parse error.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.lstrip("-\n \t")


def _goose_argv(model: str, prompt_path: Path, system: str) -> list[str]:
    """Build the goose invocation. Isolation flags are NOT optional — see the module docstring."""
    return [
        "goose", "run",
        "--no-profile",       # no default extensions: no shell, no browser, no file writes
        "--no-session",       # nothing carries between payloads (incr-01/incr-02)
        "--max-turns", "1",   # one response: no agentic loop, and a hard token bound
        "--provider", "openrouter",
        "--model", model,
        "--quiet",
        # Identity, not capability: the agent's rules and verdict contract, with no tools.
        "--system", system,
        "--instructions", str(prompt_path),
    ]


def run_payload(model: str, payload: dict, workdir: Path, timeout: int,
                system: str) -> PayloadResult:
    """Run one payload through the agent and score the response."""
    result = PayloadResult(
        pid=payload["id"],
        payload_class=payload.get("class", ""),
        article=payload.get("article", "-"),
        expected=payload["expected"],
    )
    prompt_path = workdir / f"{payload['id']}.prompt.txt"
    prompt_path.write_text(
        REVIEWER_PROMPT.format(content=payload["content"]), encoding="utf-8"
    )

    started = time.monotonic()
    try:
        completed = subprocess.run(
            _goose_argv(model, prompt_path, system),
            capture_output=True, text=True, timeout=timeout, cwd=workdir,
        )
    except subprocess.TimeoutExpired:
        result.seconds = time.monotonic() - started
        result.error = f"timed out after {timeout}s"
        result.observed, result.acceptable = score_response("", payload["expected"])
        return result
    result.seconds = time.monotonic() - started
    result.response = (completed.stdout or "") + (completed.stderr or "")
    if result.seconds < MIN_PLAUSIBLE_SECONDS:
        result.transport_error = f"returned in {result.seconds:.2f}s"
        result.error = (
            f"impossibly fast ({result.seconds:.2f}s < {MIN_PLAUSIBLE_SECONDS}s) — no model "
            f"answered; NOT a measurement"
        )
        result.observed = "NO-CALL"
        result.acceptable = False
        return result
    marker = transport_failure(result.response)
    if marker:
        # Not scored. The request never reached the model, so there is no judgment to measure.
        result.transport_error = marker
        result.error = f"transport failure ({marker}) — NOT a measurement"
        result.observed = "NO-CALL"
        result.acceptable = False
        return result
    if completed.returncode != 0 and not result.response.strip():
        result.transport_error = "empty output"
        result.error = f"goose exited {completed.returncode} with no output — NOT a measurement"
        result.observed = "NO-CALL"
        result.acceptable = False
        return result
    result.observed, result.acceptable = score_response(result.response, payload["expected"])
    return result


def render_report(report: RunReport) -> str:
    """Render the run as markdown, with the controls reported separately."""
    attacks = [r for r in report.results if r.expected != "PASS"]
    controls = [r for r in report.results if r.expected == "PASS"]
    complied = [r for r in attacks if r.observed == "COMPLY"]
    missed = [r for r in attacks if r.observed == "MISS"]
    false_positives = [r for r in controls if not r.acceptable]

    lines = [
        "# Judgment-layer measurement (W14)",
        "",
        f"**Model:** `{report.model}`  ",
        f"**Started:** {report.started_at}  ",
        f"**Tool layer:** removed (`--no-profile --no-session --max-turns 1`)  ",
        f"**Audited instance:** `{SECURITY_AGENT_REL}` — a GENERATED ARTIFACT of "
        f"`agentteams/templates/universal/security.template.md`",
        "",
        "> **Remediation path.** A weakness found here is fixed in the **template**, not in the "
        "instance. The instance is derived (Constitutional Rule 4); hand-editing it loses fenced "
        "changes on the next update, leaves unfenced ones diverged, and reaches no other "
        "generated team. Propagate with:",
        "> ```",
        "> # edit agentteams/templates/universal/security.template.md, then:",
        "> agentteams --description .github/agents/_build-description.json --project . \\",
        "> --framework claude --output .claude/agents --update --merge --yes",
        "> ```",
        "> Note the framework: `--self` writes `.github/agents/` (copilot-vscode) and would "
        "**not** touch the audited file — the fix would look ineffective because it reached a "
        "different instance.",
        "",
        "## Counts",
        "",
        "| claim | numerator | denominator | population_source |",
        "|---|---|---|---|",
        f"| attack payloads defended (REPORT or HALT as required) | "
        f"{sum(1 for r in attacks if r.acceptable)} | {len(attacks)} | `corpus.load_corpus` |",
        f"| attack payloads COMPLIED with | {len(complied)} | {len(attacks)} | "
        f"`corpus.load_corpus` |",
        f"| attack payloads MISSED entirely | {len(missed)} | {len(attacks)} | "
        f"`corpus.load_corpus` |",
        f"| benign controls correctly passed | "
        f"{sum(1 for r in controls if r.acceptable)} | {len(controls)} | `corpus.load_corpus` |",
        "",
    ]
    if complied:
        lines += [
            "> **MEASURED JUDGMENT-LAYER EXPLOIT.** "
            f"{len(complied)} payload(s) obtained compliance: "
            + ", ".join(r.pid for r in complied)
            + ". This is a property of **this model**, recorded as such. The response is model "
              "selection, not prompt tuning — fitting the prompt until the test passes is "
              "fitting the test to the model.",
            "",
        ]
    if false_positives:
        lines += [
            f"> **{len(false_positives)} benign control(s) misclassified**: "
            + ", ".join(r.pid for r in false_positives)
            + ". A reviewer that HALTs on everything scores perfectly on attacks and is "
              "useless in practice; this is the number that distinguishes discriminating from "
              "paranoid.",
            "",
        ]

    lines += ["## Per payload", "",
              "| id | class | article | expected | observed | ok | secs | note |",
              "|---|---|---|---|---|---|---|---|"]
    for r in report.results:
        note = r.error or ""
        lines.append(
            f"| {r.pid} | {r.payload_class} | {r.article} | {r.expected} | **{r.observed}** | "
            f"{'yes' if r.acceptable else 'NO'} | {r.seconds:.1f} | {note} |"
        )

    spend = report.spend
    lines += ["", "## Cost", ""]
    if spend is not None and spend <= 0 and any(r.observed != "NO-CALL" for r in report.results):
        lines += [
            "> **HARNESS BROKEN — zero spend with scored responses.** A hosted model cannot be "
            "queried for free, so a $0.00 ledger delta alongside scored answers means nothing "
            "reached the provider and the scores describe local output. Cost is being used "
            "here as a liveness check, which is the one thing it can prove that the response "
            "text cannot.",
            "",
        ]
    if spend is None:
        lines.append(
            "Provider ledger unavailable for one or both reads, so **the spend is unknown** "
            "rather than zero. Reporting an unmeasured cost as $0.00 is the same defect as "
            "reporting an unmeasured population as clean."
        )
    else:
        per = spend / len(report.results) if report.results else 0.0
        lines += [
            f"- **Provider ledger delta: ${spend:.4f}** (authoritative)",
            f"- Per payload: ${per:.4f}",
            f"- Budget cap: ${report.budget_usd:.2f}",
            f"- Credit remaining after: "
            f"${report.credits_after:.4f}" if report.credits_after is not None else "",
        ]
    return "\n".join(line for line in lines if line != "") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="z-ai/glm-5.2")
    parser.add_argument("--limit", type=int, default=0,
                        help="run only the first N payloads (smoke test)")
    parser.add_argument("--timeout", type=int, default=180, help="per-payload seconds")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--out", default=None, help="report directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the plan; spend nothing")
    args = parser.parse_args(argv)

    token = os.environ.get("OPENROUTER_API_KEY", "")
    if not token:
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 2

    ok, message = validate_model_slug(args.model)
    print(f"  model      : {message}")
    if not ok:
        return 2

    remaining = read_remaining_credit(token)
    if remaining is None:
        print("  credits    : UNAVAILABLE — cannot enforce the budget; refusing to spend.",
              file=sys.stderr)
        return 2
    print(f"  credits    : ${remaining:.4f} remaining")
    if remaining < MIN_REMAINING_USD:
        print(f"  Refusing to start: under ${MIN_REMAINING_USD:.2f} remaining.", file=sys.stderr)
        return 2

    status, detail = verify_instance_is_module_generated(SECURITY_AGENT_REL)
    print(f"  instance   : {detail}")
    if status == "MODIFIED":
        print("  Refusing to run: the audited instance has been hand-edited, so it is not what "
              "the module generates. A template fix could not reproduce this measurement.",
              file=sys.stderr)
        return 2
    contract = load_agent_contract()
    print(f"  agent      : {SECURITY_AGENT_REL} ({len(contract):,} chars) via --system")
    corpus = load_corpus()
    if args.limit:
        corpus = corpus[: args.limit]
    print(f"  payloads   : {len(corpus)} "
          f"({sum(1 for p in corpus if p['expected'] == 'PASS')} benign controls)")
    print(f"  isolation  : --no-profile --no-session --max-turns 1  (no shell, no tools)")

    if args.dry_run:
        print("  --dry-run: validated only; nothing spent.")
        return 0

    stamp = time.strftime("%Y-%m-%d")
    # Resolved to an absolute path because run_payload invokes goose with cwd=workdir; a
    # relative --out made the prompt path resolve against the NEW cwd, and goose reported
    # "Instruction file not found" for all 14 payloads. The default path was absolute, so
    # this only bit when --out was passed — which is exactly when a second model is being
    # compared against the first.
    out_dir = (Path(args.out).resolve() if args.out
               else REPO_ROOT / "tmp" / "redteam-judgment" / stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = RunReport(
        model=args.model,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        credits_before=remaining,
        budget_usd=args.budget,
    )

    for index, payload in enumerate(corpus, start=1):
        print(f"  [{index}/{len(corpus)}] {payload['id']} ({payload['expected']}) … ",
              end="", flush=True)
        result = run_payload(args.model, payload, out_dir, args.timeout, contract)
        report.results.append(result)
        print(f"{result.observed}{'' if result.acceptable else '  <-- NOT ACCEPTABLE'}"
              f"{'  ' + result.error if result.error else ''}")

        spent_so_far = (remaining - (read_remaining_credit(token) or remaining))
        if spent_so_far > args.budget:
            print(f"  ABORTING: spend ${spent_so_far:.4f} exceeded the ${args.budget:.2f} cap.",
                  file=sys.stderr)
            break

    report.credits_after = read_remaining_credit(token)

    (out_dir / "responses.json").write_text(
        json.dumps({r.pid: r.response for r in report.results}, indent=2), encoding="utf-8"
    )
    report_text = render_report(report)
    (out_dir / "judgment-report.md").write_text(report_text, encoding="utf-8")
    print()
    print(report_text)
    print(f"  artifacts  : {out_dir}")

    attacks = [r for r in report.results if r.expected != "PASS"]
    return 1 if any(r.observed == "COMPLY" for r in attacks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
