"""Known entries in force: loading and judging the store, and grading a report (vfr-k K1).

Split from :mod:`.known`, which holds the entry format, its refusals, signing and verification. Here every
entry in the store is verified again (:func:`.known.verify`), then judged against its governance dir: the HALT
scan on :data:`.known.ACTION_ID` (voids all), a HALT on the entry's id or an ancestor's (voids it), its decision
reference, its row in the committed index, and the aggregate cap (the newest over :data:`.known.MAX_ACTIVE`
are refused). :func:`apply` then marks the findings valid entries cover, adds a HIGH ``integrity`` finding for
each void entry, and records every entry in ``report.known_entries``; :func:`markdown` renders the summary's
*Known findings* section (reasons fenced as data and capped).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan.known import (
    ACTION_ID,
    CLEARED_DECISIONS,
    DECISIONS_REL,
    INDEX_HEADER,
    INDEX_REL,
    MAX_ACTIVE,
    REASON_MAX_CHARS,
    RETRACTING_WORDS,
    SHOWN_REASON_CHARS,
    _is_trust_root,
    _now,
    body_sha256,
    finding_identity,
    member_identity,
    read_entry,
    store_dir,
    verify,
)
from agentteams.vulnscan.osv import SEVERITY_NAME
from agentteams.vulnscan.report import Finding, Report

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_TEMPLATE_RE = re.compile(r"\{(?=[{%#])")


@dataclass
class Loaded:
    """Every entry file in the store, judged: ``valid`` in force, ``void`` with a reason, ``inactive`` renewed."""

    valid: list[dict[str, Any]] = field(default_factory=list)
    void: list[tuple[dict[str, Any] | None, str, str]] = field(default_factory=list)   # (record, file name, reason)
    inactive: list[tuple[dict[str, Any], str]] = field(default_factory=list)          # (record, renewed by)
    problems: list[str] = field(default_factory=list)                                  # index rows without an entry


def committed_index(gov: Path) -> tuple[dict[str, dict[str, str]] | None, str]:
    """Return the index as committed at ``HEAD`` of the governance dir's repository.

    The working-tree file is never read: an uncommitted row counts for nothing (Rule 15(d), git-tracked).

    Args:
        gov: The governance dir.

    Returns:
        ``({entry_id: row}, "")``, or ``(None, problem)``. An id listed twice is dropped (both rows).
    """
    from agentteams.git_exec import run_git

    try:
        proc = run_git(gov, "show", f"HEAD:./{INDEX_REL}", hardened=True, timeout=30, devnull_stdin=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:   # a timeout or decode error: no index
        return None, f"cannot read the committed index: {type(exc).__name__}"
    if proc.returncode:
        return None, f"{INDEX_REL} is not committed in {gov} (git show HEAD:{INDEX_REL} failed)"
    reader = csv.reader(io.StringIO(proc.stdout))
    header = next(reader, [])
    if tuple(header) != INDEX_HEADER:
        return None, f"{INDEX_REL} does not start with {','.join(INDEX_HEADER)}"
    rows: dict[str, dict[str, str]] = {}
    dup: set[str] = set()
    for raw in reader:
        if not raw:
            continue
        row = dict(zip(INDEX_HEADER, raw + [""] * (len(INDEX_HEADER) - len(raw)), strict=False))
        eid = row["entry_id"]
        if eid in rows:
            dup.add(eid)
        rows[eid] = row
    for eid in dup:
        rows.pop(eid, None)
    return rows, ""


def _decision_problems(gov: Path, record: dict[str, Any]) -> str:
    """Return why the entry's decision reference or HALT state voids it, or ""."""
    from agentteams.cli import decision_log

    try:
        read = decision_log._read_decision_rows(gov)
    except RuntimeError as exc:
        return f"decision log unreadable: {exc}"
    if read is None:
        return f"no decision log at {gov / DECISIONS_REL}"
    rows, _cols = read
    ref = record["decision_ref"]
    matching = [r for r in rows if (r.get("plan_slug") or "").strip() == ref["plan_slug"]
                and (r.get("step") or "").strip() == ref["step"]]
    # Both columns are read: a legacy-schema HALT keeps the verdict in ``status`` (decision = the action id).
    verdicts = [" ".join((r.get(c) or "").strip().upper() for c in ("decision", "verdict", "status"))
                for r in matching]
    bad = next((v for v in verdicts if any(w in v for w in RETRACTING_WORDS)), None)
    if bad:
        return f"decision ref {ref['plan_slug']}/{ref['step']} is {bad.strip()[:80]}"
    if not any((r.get("decision") or r.get("verdict") or "").strip().upper() in CLEARED_DECISIONS for r in matching):
        return f"decision ref {ref['plan_slug']}/{ref['step']} names no cleared decision-log row"
    # Dynamic gate site (like branch-cleanup:<sha>): a HALT naming this entry or any ancestor in its lineage.
    for action in [record["entry_id"], *record["lineage"]]:
        try:
            decision_log._assert_no_unretracted_halt(gov, action=action)
        except RuntimeError as exc:
            return f"HALT on {action}{' (an ancestor)' if action != record['entry_id'] else ''}: {str(exc)[:200]}"
    return ""


def _shown_record(path: Path) -> dict[str, Any] | None:
    """Return a rejected entry file's content for display only (its target and id in the void row), or None."""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):   # unreadable: the void row names the file instead
        return None
    return rec if isinstance(rec, dict) else None


def load(*, now: datetime | None = None) -> Loaded:
    """Load and judge every entry in the store. Never raises.

    Each entry is verified (signature, purpose, bounds, refusals, derived class), then its governance dir's
    HALT state on :data:`ACTION_ID`, its decision reference and lineage, and its committed index row. The
    newest valid entries beyond :data:`MAX_ACTIVE` are void (existing ones stay in force).

    Args:
        now: Clock override (tests).

    Returns:
        The judged entries.
    """
    from agentteams.cli import decision_log
    from agentteams.vulnscan import grant

    now = now or _now()
    out = Loaded()
    d = store_dir()
    if not os.path.lexists(d):
        return out
    unsafe = grant.dir_problem(d)
    if unsafe:
        out.void.append((None, str(d), f"store {unsafe}: every entry is void"))
        return out
    verified: list[dict[str, Any]] = []
    files = sorted(d.glob("*.json"))
    in_store = {p.stem for p in files}
    for path in files:
        try:
            rec = read_entry(path)
            verify(rec, now=now)
        except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
            out.void.append((_shown_record(path), path.name, str(exc)[:300]))
            continue
        verified.append(rec)
    by_gov: dict[str, list[dict[str, Any]]] = {}
    for rec in verified:
        by_gov.setdefault(rec["governance_dir"], []).append(rec)
    survivors: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    for gov_s, recs in sorted(by_gov.items()):
        gov = Path(gov_s)
        try:
            decision_log._assert_no_unretracted_halt(gov, action=ACTION_ID)
            halt = ""
        except RuntimeError as exc:
            halt = f"HALT on {ACTION_ID} voids every entry: {str(exc)[:200]}"
        index, problem = committed_index(gov)
        for rec in recs:
            why = halt or problem
            if not why and index is not None:
                row = index.get(rec["entry_id"])
                if row is None:
                    why = f"no row for {rec['entry_id']} in the committed {INDEX_REL} (missing, or listed twice)"
                elif (row["body_sha256"], row["renews"], row["renewal_count"]) != (
                        body_sha256(rec), rec["renews"], str(rec["renewal_count"])):
                    why = f"the committed {INDEX_REL} row for {rec['entry_id']} does not match the entry"
            why = why or _decision_problems(gov, rec)
            if why:
                out.void.append((rec, f"{rec['entry_id']}.json", why))
            else:
                survivors.append(rec)
        if index is not None:
            out.problems += [f"index row {eid} has no entry in the store" for eid in sorted(index)
                             if eid not in in_store and eid not in seen_rows]
            seen_rows.update(index)
    renewed = {anc: rec["entry_id"] for rec in survivors for anc in rec["lineage"]}
    current = []
    for rec in survivors:
        if rec["entry_id"] in renewed:
            out.inactive.append((rec, renewed[rec["entry_id"]]))
        else:
            current.append(rec)
    current.sort(key=lambda r: (r["issued"], r["entry_id"]))
    out.valid = current[:MAX_ACTIVE]
    for rec in current[MAX_ACTIVE:]:
        out.void.append((rec, f"{rec['entry_id']}.json",
                         f"over the cap of {MAX_ACTIVE} entries: a new entry is refused (existing ones stay in force)"))
    return out


def active_count(*, now: datetime | None = None) -> int:
    """Return how many entries are in force now (for the cap check before proposing or signing a new one).

    Args:
        now: Clock override (tests).

    Returns:
        The count.
    """
    return len(load(now=now).valid)


# ---------------------------------------------------------------- applying to a report

def _member_void(m: dict[str, Any], f: Finding) -> str:
    """Return why finding ``f`` (same identity as ``m``) is not what ``m`` signed, or ""."""
    if f.malicious or any(i.upper().startswith("MAL-") for i in f.ids):
        return f"{f.name}: now a malicious package (MAL-*); never known"
    if f.version != m["version"]:
        return f"{f.name}: installed version changed ({m['version'] or '-'} -> {f.version or '-'})"
    new_ids = sorted(set(f.ids) - set(m["ids"]))
    if new_ids:
        return f"{f.name}: new advisory id(s) {', '.join(new_ids[:5])}"
    if f.fix and f.fix != m["fix"]:
        return f"{f.name}: a fixed release is available ({f.fix}) that the entry did not record ({m['fix'] or 'none'})"
    if f.severity > m["severity"]:
        return (f"{f.name}: severity rose ({SEVERITY_NAME.get(m['severity'], '?')} -> "
                f"{SEVERITY_NAME.get(f.severity, '?')})")
    if f.kev and not m["kev"]:
        return f"{f.name}: now on CISA KEV (known exploited)"
    if (f.evidence or "") != m["evidence"]:
        return f"{f.name}: evidence changed (file contents or the nginx deny rule)"
    hit = next((loc for loc in f.locations if _is_trust_root(loc)), None)
    if hit:
        return f"{f.name}: location {hit!r} is a trust root; never known"
    return ""


def _match(rec: dict[str, Any], findings: list[Finding]) -> tuple[list[Finding], str]:
    """Return (the findings ``rec`` covers, why it is void or "")."""
    members: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for m in rec["members"]:
        members.setdefault(member_identity(m), []).append(m)
    covered: list[Finding] = []
    for f in findings:
        if f.target != rec["target"]:
            continue
        cands = members.get(finding_identity(f))
        if not cands:
            continue
        reasons = [_member_void(m, f) for m in cands]
        if "" not in reasons:
            same = [r for m, r in zip(cands, reasons, strict=True) if m["version"] == f.version]
            return [], (same or reasons)[0]
        covered.append(f)
    return covered, ""


def _fence(text: str, limit: int) -> str:
    """Return ``text`` as one inline code span of at most ``limit`` characters: data, never markup or template."""
    flat = _TEMPLATE_RE.sub("{ ", _CTRL_RE.sub(" ", str(text)))
    if len(flat) > limit:
        flat = flat[:limit] + "…"
    return "`" + flat.replace("`", "'").replace("|", "/") + "`"


def _row(rec: dict[str, Any] | None, name: str, status: str, why: str = "", covered: int = 0) -> dict[str, Any]:
    rec = rec if isinstance(rec, dict) else {}
    ref = rec.get("decision_ref") if isinstance(rec.get("decision_ref"), dict) else {}
    return {"entry_id": str(rec.get("entry_id") or name)[:64], "status": status, "target": str(rec.get("target", ""))[:256],
            "kind": str(rec.get("kind", ""))[:16], "members": len(rec.get("members") or []) if isinstance(
                rec.get("members"), list) else 0,
            "reason": str(rec.get("reason", ""))[:REASON_MAX_CHARS], "expires": str(rec.get("expires", ""))[:40],
            "key_id": str(rec.get("key_id", ""))[:64],
            "decision_ref": f"{str(ref.get('plan_slug', ''))[:200]}/{str(ref.get('step', ''))[:200]}" if ref else "",
            "renewal_count": rec.get("renewal_count", 0) if isinstance(rec.get("renewal_count"), int) else 0,
            "renews": str(rec.get("renews", ""))[:64], "void_reason": why[:400], "covered": covered}


def apply(report: Report, *, now: datetime | None = None, loaded: Loaded | None = None) -> list[dict[str, Any]]:
    """Grade the findings that valid entries cover as known, and record every entry in the report.

    Call after :meth:`.report.Report.apply_unscored` (entries record severities as the summary shows them) and
    before :meth:`.report.Report.exit_code`. Never raises: an unexpected error voids everything and says so.

    A covered finding gets ``known`` / ``known_until`` and stays in ``report.findings``. A void entry covers
    nothing, and the report gains a HIGH ``integrity`` finding naming it and why, unless it is merely expired
    with nothing of it left in the report. ``report.known_entries`` lists every entry with its status.

    Args:
        report: The aggregate report (modified).
        now: Clock override (tests).
        loaded: Pre-loaded entries (tests); default :func:`load`.

    Returns:
        ``report.known_entries``.
    """
    try:
        loaded = loaded or load(now=now)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:   # fail closed, said so
        loaded = Loaded(void=[(None, "(store)", f"could not load known entries: {type(exc).__name__}: {exc}"[:300])])
    present = list(report.findings)
    rows: list[dict[str, Any]] = []
    notes: list[Finding] = []

    def note(rec: dict[str, Any] | None, name: str, why: str) -> None:
        target = str(rec.get("target", "")) if isinstance(rec, dict) else ""
        notes.append(Finding(kind="integrity", target=target or "known-entries", name="known-entry", severity=3,
                             detail=f"known entry {str((rec or {}).get('entry_id') or name)[:64]} is void "
                                    f"({why[:300]}); its findings are graded normally"))

    for rec, name, why in loaded.void:
        rows.append(_row(rec, name, "void", why))
        seen = isinstance(rec, dict) and _seen(rec, present)
        if not why.startswith("expired") or seen:
            note(rec, name, why)
    for rec, by in loaded.inactive:
        rows.append(_row(rec, f"{rec['entry_id']}.json", "renewed", f"renewed by {by}"))
    for rec in loaded.valid:
        covered, why = _match(rec, present)
        if why:
            rows.append(_row(rec, f"{rec['entry_id']}.json", "void", why))
            note(rec, rec["entry_id"], why)
            continue
        until = str(rec["expires"])[:10]
        for f in covered:
            f.known, f.known_until = rec["entry_id"], until
        rows.append(_row(rec, f"{rec['entry_id']}.json", "known" if covered else "not seen", "", len(covered)))
    for p in loaded.problems:
        rows.append(_row(None, "(index)", "problem", p))
    report.findings += notes
    report.known_entries = rows
    return rows


def _seen(rec: dict[str, Any], findings: list[Finding]) -> bool:
    """Return whether any finding has the target and identity of one of ``rec``'s members."""
    try:
        ids = {member_identity(m) for m in rec["members"]}
        return any(f.target == rec["target"] and finding_identity(f) in ids for f in findings)
    except (KeyError, TypeError, AttributeError):
        return True   # a malformed record: say so loudly


def markdown(rows: list[dict[str, Any]]) -> str:
    """Render the summary's *Known findings* section: every entry, reasons fenced as data and capped.

    Args:
        rows: ``report.known_entries``.

    Returns:
        Markdown ("" with no entries).
    """
    if not rows:
        return ""
    out = ["## Known findings (operator-signed; not counted in the exit code)", "",
           "Reasons are operator text, shown as data. A void entry covers nothing: its findings are graded "
           "normally above and listed as a HIGH integrity finding.", "",
           "| Entry | Status | Target | Covers | Expires | Key | Decision | Renewals | Reason / why void |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        why = r["void_reason"] or r["reason"]
        renew = f"{r['renewal_count']}" + (f" (renews {r['renews']})" if r["renews"] else "")
        covers = f"{r['covered']} of {r['members']}" if r["status"] in ("known", "not seen") else str(r["members"])
        out.append(f"| {_fence(r['entry_id'], 64)} | {r['status']} | {_fence(r['target'], 80)} | {covers} "
                   f"| {_fence(r['expires'][:10], 12)} | {_fence(r['key_id'], 64)} | {_fence(r['decision_ref'], 120)} "
                   f"| {_fence(renew, 90)} | {_fence(why, SHOWN_REASON_CHARS)} |")
    return "\n".join(out) + "\n"
