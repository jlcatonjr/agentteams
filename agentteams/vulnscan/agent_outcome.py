"""What an agent run's end says, and the one nudge a goose run gets after a reply cut off at the output limit.

Split out of :mod:`.agent_run` (CH-07). :func:`read_spend` and :func:`cap_reason` read the gateway's totals;
:func:`transcript_error` names the CLI's own error line (:meth:`.agent_drivers.Driver.parse_outcome`).

**The nudge (rc.12).** rc.9 and rc.10 live runs (z-ai/glm-5.3) spent a whole reply on reasoning: goose logged
"Response reached the model's output-token limit", the reply carried no tool call, goose exited, and the run
ended ``agent-error`` with no report. When the goose driver's outcome is exactly that
(:data:`.agent_drivers.OUTPUT_LIMIT_ERROR`) and the run submitted no report, the launcher resumes the same goose
session once (``goose run --resume --name <session>``, its sessions persist in ``<run>/agent/goose-data``) with
the fixed operator message :data:`NUDGE_MESSAGE`. It does this only within the run's own deadline (at least
:data:`MIN_NUDGE_SECONDS` left) and while the gateway's budget and token cap have room; the gateway still
enforces both. Never for another driver, never with an ``agent.command`` override, never twice. ``status.json``
records ``nudged: true``, or ``nudge_skipped`` with the reason when the outcome called for one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentteams.vulnscan import agent_drivers, report_store
from agentteams.vulnscan.agent_settings import driver_of

#: Substrings of the gateway's refusal reasons that mean a run cap stopped the run (``cap-reached``).
CAP_REFUSAL = ("run token cap", "-token run cap", "run budget", "reservation refused")
#: The operator message a nudged goose session is resumed with (fixed text: nothing from the run is folded in).
NUDGE_MESSAGE = ("Operator: your last reply reached the output-token limit before it called any tool, so nothing "
                 "was done. Stop deliberating. Within the remaining budget, act now: queue any requests you have "
                 "already decided on, then call submit_report with what you have. Keep each reply short.")
#: The least time left before the run's deadline for a nudge to be worth starting.
MIN_NUDGE_SECONDS = 120.0


def read_spend(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read the gateway's ``spend.json`` tolerantly (G12): a bad file never stops the status being written.

    Args:
        path: ``<run>/gateway/spend.json``.

    Returns:
        ``(spend, problem)``: the totals and ``""``; ``(None, "")`` when the file is absent; or
        ``(None, reason)`` when it is unreadable, not JSON or not an object.
    """
    if not path.is_file():
        return None, ""
    try:
        spend = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:   # ValueError includes UnicodeDecodeError and JSONDecodeError
        return None, f"spend.json is unreadable ({type(exc).__name__}: {str(exc)[:200]})"
    if not isinstance(spend, dict):
        return None, f"spend.json is not an object ({type(spend).__name__})"
    return spend, ""


def cap_reason(spend: dict[str, Any] | None) -> str:
    """Return why the gateway's run budget or token cap refused calls, from ``spend.json``, or "".

    Args:
        spend: The gateway totals (:func:`read_spend`), or None.

    Returns:
        ``"the gateway's run cap refused further calls: <reason>"``, or "" when no cap refusal is recorded.
    """
    hits = [str(r) for r in (spend or {}).get("refusal_reasons") or [] if any(k in str(r) for k in CAP_REFUSAL)]
    return f"the gateway's run cap refused further calls: {hits[-1]}"[:300] if hits else ""


def transcript_error(path: Path, a: dict[str, Any]) -> str:
    """Return ``": <the CLI's error line>"`` from a run transcript (:meth:`Driver.parse_outcome`), or ""."""
    try:
        hit = driver_of(a).parse_outcome(path.read_text(encoding="utf-8", errors="replace")).error
    except (OSError, ValueError):
        return ""
    return f": {hit}" if hit else ""


def _cap_used_up(spend: dict[str, Any]) -> str:
    """Return why the gateway would refuse the next call outright (a cap refusal, or a cap used up), or ""."""
    refused = cap_reason(spend)
    if refused:
        return refused
    keys = ("spent_usd", "budget_usd", "prompt_tokens", "completion_tokens", "max_tokens")
    if not all(isinstance(spend.get(k, 0), (int, float)) and not isinstance(spend.get(k, 0), bool) for k in keys):
        return "spend.json has no usable totals"
    spent, budget = float(spend.get("spent_usd", 0)), float(spend.get("budget_usd", 0))
    tokens = int(spend.get("prompt_tokens", 0)) + int(spend.get("completion_tokens", 0))
    cap = int(spend.get("max_tokens", 0))
    if budget and spent >= budget:
        return f"the run budget is used up (${spent:.2f} of ${budget:.2f})"
    if cap and tokens >= cap:
        return f"the run token cap is used up ({tokens} of {cap})"
    return ""


def nudge_skip_reason(a: dict[str, Any], run_dir: Path, seconds_left: float) -> str | None:
    """Decide whether a finished agent run gets its one nudge (:data:`NUDGE_MESSAGE`).

    Args:
        a: Agent settings.
        run_dir: The run directory (``agent/transcript.log``, ``gateway/spend.json``, the report store).
        seconds_left: Seconds before the run's deadline.

    Returns:
        None when the outcome does not call for a nudge (not goose, or not cut off at the output limit, or a
        report was submitted); ``""`` to nudge; else why a nudge the outcome called for is not given.
    """
    driver = driver_of(a)
    if driver.name != agent_drivers.GOOSE.name:
        return None
    try:
        transcript = (run_dir / "agent" / "transcript.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if driver.parse_outcome(transcript).error != agent_drivers.OUTPUT_LIMIT_ERROR:
        return None
    try:
        if report_store.load(run_dir) is not None:   # an earlier run's report was archived under the lock
            return None
    except ValueError:   # its text is not copied: status.json carries no report content
        return "the stored report could not be checked"
    if a.get("command"):
        return "agent.command overrides the goose command, so the session cannot be resumed safely"
    if seconds_left < MIN_NUDGE_SECONDS:
        return f"only {max(seconds_left, 0):.0f} s of the run's time were left"
    spend, problem = read_spend(run_dir / "gateway" / "spend.json")
    if spend is None:   # fail closed: without the gateway's totals the room left is unknown
        return problem or "spend.json is missing, so the budget left is unknown"
    return _cap_used_up(spend)


def nudge_failure_reason(reason: str) -> str:
    """Return a failed nudged run's ``reason``: the original one plus what the nudge did."""
    return (f"{reason}; " if reason else "") + ("the goose session was resumed once with an operator nudge to call "
                                                "submit_report, and the resumed session also ended without a report")
