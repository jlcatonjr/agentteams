"""The executor's spool server: reads agent requests, runs each through :func:`executor.act`, records results.

Split from :mod:`.executor` (CH-07). Every request still goes through ``executor.act``, the single
authorizing entry point; this module only reads, orders and files requests. Names it uses from the
executor are looked up on that module at call time, so ``executor.MAX_REQUESTS_PER_PASS`` and friends
stay the one source (and what tests patch).

**Batch preflight.** Before a pass's first request of a granted op, and again after any refusal of one,
:func:`batch_block` asks whether that op can run at all: the tools container (its image and passing
self-test, when confinement is on) and a valid grant with uses left that allows the op. When it cannot,
the remaining requests of that op are not attempted this pass: each gets a ``refused`` result
("not attempted: ...") and one ledger row records how many. A pass no longer fans the same systemic
refusal across every repository.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import (
    escalations,
    grant,
    ledger,
    notify,
    retry_guard,
    subproc_confine,
)
from agentteams.vulnscan import executor as ex

NOT_ATTEMPTED = "not attempted: "
STAGED_INDEX = "staged-fixes.json"   # read by the MCP server (mcp.STAGED_FILE)


def write_staged_index(run_dir: Path) -> list[str]:
    """Write ``<run_dir>/staged-fixes.json``: the repositories whose staged fix has no PR yet.

    The agent container mounts the run directory, not the spool, so this is how the agent learns to request
    ``open-pr`` rather than a second ``stage-fix``. Machine-local; never published.

    Args:
        run_dir: The run directory.

    Returns:
        The repository paths listed.
    """
    repos: list[str] = []
    staged = ex.spool_dir() / "staged"
    for path in sorted(staged.glob("*.json")) if staged.is_dir() else []:
        try:
            repo = json.loads(path.read_text(encoding="utf-8")).get("repo")
        except (OSError, ValueError, AttributeError):   # an unreadable staged state lists nothing
            repo = None
        if isinstance(repo, str) and repo.startswith("/"):
            repos.append(repo)
    ex._write_json(run_dir / STAGED_INDEX, {"repos": repos})
    return repos


def batch_block(cfg: dict[str, Any], op: str) -> str:
    """Return why no ``op`` request can run now (tools container, or no usable grant), or "".

    Args:
        cfg: Daily config.
        op: A requested operation. Internal and unknown ops return "" (``act`` handles them).

    Returns:
        The reason, or "".
    """
    if op not in grant.OPS_V1:
        return ""
    if subproc_confine.enabled(cfg):
        why = subproc_confine.unavailable_reason(subproc_confine.image_tag(subproc_confine.settings(cfg)))
        if why:
            return f"tools container unavailable: {why}"
    grants, problems = grant.load_active()
    try:
        rows = ledger.read()
    except ValueError:
        return ""   # act refuses everything on an untrusted ledger, with its own reason
    if any(op in g.get("ops", ()) and ledger.uses(g["grant_id"], rows) < int(g["max_uses"]) for g in grants):
        return ""
    return f"no valid grant with uses left allows {op}" + (f" ({'; '.join(problems)})"[:600] if problems else "")


def _read_request(path: Path) -> dict[str, Any]:
    # O_NONBLOCK: the agent can create a FIFO named <id>.json in its rw spool; a blocking open would
    # stall the serving loop, and with it the run's time cap (R8-1).
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ex.ExecError("not a regular file")
        data = os.read(fd, ex.MAX_REPORT_REQUEST_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > ex.MAX_REPORT_REQUEST_BYTES:
        raise ex.ExecError("request too large")
    req = json.loads(data.decode("utf-8"))
    if not isinstance(req, dict) or set(req) - ex.REQUEST_KEYS or not {"id", "op", "target"} <= set(req):
        raise ex.ExecError("request does not match the schema")
    if len(data) > ex.MAX_REQUEST_BYTES and req.get("op") != "report":
        raise ex.ExecError("request too large")
    if not ex.REQUEST_ID.match(str(req["id"])) or path.name != f"{req['id']}.json":
        raise ex.ExecError("bad request id")
    if not all(isinstance(req.get(k, ""), str) for k in ex.REQUEST_KEYS - {"payload"}):
        raise ex.ExecError("request fields must be strings")
    _check_payload(req)
    return req


def _check_payload(req: dict[str, Any]) -> None:
    """Validate the per-op payload: required for and only allowed with internal ops."""
    op, payload = req["op"], req.get("payload")
    if op not in ex.INTERNAL_OPS:
        if payload is not None:
            raise ex.ExecError("payload is only allowed with internal ops")
        return
    if not isinstance(payload, dict):
        raise ex.ExecError(f"op {op!r} requires a payload object")
    if op == "report":
        if set(payload) != {"markdown"} or not isinstance(payload["markdown"], str):
            raise ex.ExecError("report payload must be {\"markdown\": str}")
    else:
        if set(payload) != {"kind", "evidence"} or not all(isinstance(v, str) for v in payload.values()):
            raise ex.ExecError("escalate payload must be {\"kind\": str, \"evidence\": str}")
        if payload["kind"] not in ex.AGENT_ESCALATION_KINDS:
            raise ex.ExecError(f"escalation kind must be one of {ex.AGENT_ESCALATION_KINDS}")


def quarantine_pending(reason: str) -> int:
    """Move every pending spool request aside unprocessed (``processed/stale-<id>.json``), with one ledger row.

    Called at the start of a run, under the run lock: requests a crashed or killed run left behind
    must not be served as the new run's (its report, its escalation count, its actions).

    Args:
        reason: Why (recorded on each ``refused`` row).

    Returns:
        How many requests were moved.
    """
    req_dir, done_dir = ex.spool_dir() / "requests", ex.spool_dir() / "processed"
    pending = sorted(req_dir.glob("*.json")) if req_dir.is_dir() else []
    if not pending:
        return 0
    done_dir.mkdir(parents=True, exist_ok=True)
    for path in pending:
        shutil.move(str(path), str(done_dir / f"stale-{path.name}"))
    with ledger.locked():   # one row, however many files: a planted pile cannot stall the ledger
        ledger.append("refused", op="(stale)", target=f"{len(pending)} request files", request_id="",
                      detail=reason[:300])
    return len(pending)


def _set_aside_overflow(cfg: dict[str, Any], overflow: list[Path], done_dir: Path, run_id: str | None) -> None:
    """Move requests over the per-pass cap aside unread and open (or keep) an escalation-flood."""
    for path in overflow:
        shutil.move(str(path), str(done_dir / f"overflow-{path.name}"))
    detail = f"{len(overflow)} spool requests over the per-pass cap of {ex.MAX_REQUESTS_PER_PASS} were set aside unread"
    with ledger.locked():
        ledger.append("refused", op="(overflow)", target=f"{len(overflow)} request files", request_id="", detail=detail)
        esc, new = escalations.record_flood(detail, run_id=run_id or f"adhoc-{datetime.now(UTC).date().isoformat()}")
    if new:
        notify.send(cfg, "escalation", id=esc.id, kind=esc.kind)


def serve_requests(cfg: dict[str, Any], *, run_dir: Path | None = None, run_id: str | None = None,
                   deadline: float | None = None) -> list[dict[str, Any]]:
    """Process pending spool requests once — internal ops (escalations, reports) first.

    At most :data:`executor.MAX_REQUESTS_PER_PASS` are read; any more are moved aside unread and open an
    ``escalation-flood`` (blocks everything until the operator resolves it). Processing stops at
    ``deadline``; what is left stays queued for the next pass. An op that :func:`batch_block` stops is
    not attempted for the rest of the pass. A ``stage-fix`` held by :mod:`.retry_guard` notifies the
    operator once per held plan.

    Args:
        cfg: Daily config.
        run_dir: The current run directory (passed to ``report``).
        run_id: The current run id (passed to ``escalate``).
        deadline: ``time.monotonic()`` value after which no further request is started.

    Returns:
        Results (also written to ``<spool>/results/<id>.json`` for the agents to read). A ledger
        integrity failure stops the batch with one refused result.
    """
    req_dir = ex.spool_dir() / "requests"
    done_dir = ex.spool_dir() / "processed"
    req_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    try:
        seen = ledger.consumed_request_ids()
    except ValueError as exc:
        return [{"status": "refused", "detail": f"ledger integrity failure — nothing processed: {exc}"}]
    files = sorted(req_dir.glob("*.json"))
    if len(files) > ex.MAX_REQUESTS_PER_PASS:
        _set_aside_overflow(cfg, files[ex.MAX_REQUESTS_PER_PASS:], done_dir, run_id)
        files = files[:ex.MAX_REQUESTS_PER_PASS]
    valid: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        try:
            req = _read_request(path)
            if req["id"] in seen:
                raise ex.ExecError("request id already used (replay)")
        except (ex.ExecError, OSError, ValueError, UnicodeDecodeError) as exc:
            with ledger.locked():
                ledger.append("refused", op="(invalid)", target=path.name[:80], request_id="",
                              detail=f"malformed request: {exc}"[:500])
            shutil.move(str(path), str(done_dir / f"rejected-{path.name}"))
            continue
        valid.append((path, req))
    valid.sort(key=lambda pr: (pr[1]["op"] not in ex.INTERNAL_OPS, pr[1]["op"] != "escalate"))
    out = []
    blocked: dict[str, str] = {}      # op -> why it cannot run this pass ("" = it can)
    skipped: dict[str, int] = {}      # op -> requests not attempted
    for path, req in valid:
        if deadline is not None and time.monotonic() >= deadline:
            break   # the rest stays queued; the run's cap is enforced on the container, not here
        op = req["op"]
        if op not in blocked:
            blocked[op] = batch_block(cfg, op)
        if blocked[op]:
            res = {"id": req["id"], "op": op, "target": req["target"], "status": "refused",
                   "detail": NOT_ATTEMPTED + blocked[op]}
            skipped[op] = skipped.get(op, 0) + 1
            name = f"blocked-{path.name}"
        else:
            res = {"id": req["id"], "op": op, "target": req["target"],
                   **ex.act(op, req["target"], cfg, rationale=req.get("rationale", ""), request_id=req["id"],
                            payload=req.get("payload"), run_dir=run_dir, run_id=run_id)}
            name = path.name
            if res.get("status") == "refused":
                blocked[op] = batch_block(cfg, op)   # a systemic refusal stops the rest of this op
                if str(res.get("detail", "")).startswith(retry_guard.HELD_PREFIX) and \
                        retry_guard.mark_held(str(Path(req["target"]).expanduser().resolve())):
                    notify.send(cfg, "repo-held")
        ex._write_json(ex.spool_dir() / "results" / f"{req['id']}.json", res)
        shutil.move(str(path), str(done_dir / name))
        seen.add(req["id"])
        out.append(res)
    if skipped:
        with ledger.locked():
            for op, n in skipped.items():
                ledger.append("refused", op=op, target=f"{n} requests", request_id="",
                              detail=(NOT_ATTEMPTED + blocked[op])[:1000])
    return out
