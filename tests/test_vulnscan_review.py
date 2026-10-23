"""agentteams.vulnscan.review: escalations, agent report, publishing, notes, GitHub replies and the
published (fenced, path-rewritten) copy. Network-free: ``notes.gh_replies`` and ``publish.load_active``
are monkeypatched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams.vulnscan import (
    escalations,
    ledger,
    notes,
    paths,
    publish,
    report_store,
    review,
)
from agentteams.vulnscan.report import Report
from tests._vulnscan_helpers import operator_env

MARK = "PLANTED-MARKER-123"
PR = "https://github.com/o/svc/pull/7"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    operator_env(tmp_path, monkeypatch)
    monkeypatch.setattr(publish, "load_active", lambda **kw: ([], []))
    return tmp_path


def _escalate(kind: str, target: str, evidence: str, run_id: str = "run-1") -> escalations.Escalation:
    with ledger.locked():
        return escalations.record(kind, target, evidence, request_id="rq-1", run_id=run_id, canonical=lambda t: t)


def _append(kind: str, **fields: object) -> None:
    with ledger.locked():
        ledger.append(kind, **fields)


def _run_dir(tmp: Path, report: str | None = None) -> Path:
    run = tmp / "state" / "2026-10-22"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(Report(target="all").to_dict()), encoding="utf-8")
    if report is not None:
        report_store.save(run, report, request_id="rq-report")
    return run


def _outside_fences(md: str, marker: str) -> list[str]:
    """Return every line holding ``marker`` that is not inside a backtick fence."""
    bad, bar = [], None
    for line in md.splitlines():
        if bar is None:
            m = re.match(r"^(`{3,})text$", line)
            if m:
                bar = m.group(1)
            elif marker in line:
                bad.append(line)
        elif line == bar:
            bar = None
    return bad


def test_open_escalation_listed_with_resolve_hint(env: Path) -> None:
    esc = _escalate("malicious", "/r", f"saw {MARK}")
    md = review.build()
    section = md.split("## Open escalations")[1].split("## Standing grants")[0]
    assert esc.id in section and "[malicious]" in section and "SERIOUS" in section
    assert f"vulnscan resolve {esc.id}" in section and "run `run-1`" in section and MARK in section
    assert md.index("## Open escalations") < md.index("## Standing grants")


def test_resolved_escalation_shows_method(env: Path) -> None:
    esc = _escalate("other", "/r", "unsure")
    escalations.resolve_other(esc.id, "checked by hand")
    md = review.build()
    opened, resolved = md.split("Resolved in this window:")
    assert esc.id not in opened.split("## Open escalations")[1]
    assert esc.id in resolved and "operator-mac" in resolved and "checked by hand" in resolved


def test_empty_review_says_none_and_no_publish_grant(env: Path) -> None:
    md = review.build()
    assert "## Open escalations" in md and "No publish grant: reports stay local." in md
    assert "## Operator notes" in md and "0 active note(s)" in md
    assert "## GitHub replies" not in md


def test_agent_report_section(env: Path) -> None:
    _run_dir(env, report=f"# Report\n{MARK}\n")
    md = review.build()
    assert report_store.REVIEW_HEADING in md and MARK in md
    assert not _outside_fences(md, MARK)


def test_a_tilde_scan_target_counts_as_acted_on_when_the_executor_resolved_it(env: Path) -> None:
    from agentteams.vulnscan.report import Finding

    repo = env / "home" / "code" / "svc"
    repo.mkdir(parents=True)
    rep = Report(target="daily")
    rep.findings += [Finding(kind="package", target="~/code/svc", name="PyJWT", version="2.3.0", fix="2.4.0"),
                     Finding(kind="package", target="~/code/other", name="tqdm", version="4.0", fix="4.66.3")]
    run = env / "state" / "2026-10-22"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(rep.to_dict()), encoding="utf-8")
    _append("done", op="stage-fix", target=str(repo.resolve()), request_id="r1", detail="{}")
    section = review.build().split("## Findings not acted on")[1].split("\n## ")[0]
    assert "tqdm" in section and "PyJWT" not in section


def test_local_actions_table_keeps_one_row_per_action(env: Path) -> None:
    _append("failed", op="stage-fix", target="/r", request_id="r1", detail="git fetch failed:\nfatal: no origin\n",
            rationale="line one\nline two | piped")
    table = review.build().split("## Actions taken")[1].split("\n## ")[0]
    rows = [ln for ln in table.splitlines() if ln.startswith("| 2")]
    assert len(rows) == 1 and rows[0].count("|") == 7 and "fatal: no origin" in rows[0]


def test_publish_grant_and_rows(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    g = {"grant_id": "vp-0123456789abcdef", "ops_repo": str(env / "ops-reports"), "max_uses": 9,
         "expires": "2026-11-01T00:00:00+00:00"}
    monkeypatch.setattr(publish, "load_active", lambda **kw: ([g], []))
    _append("publish-begun", grant_id=g["grant_id"], op="publish", target=g["ops_repo"], run_id="run-1")
    _append("publish-done", grant_id=g["grant_id"], op="publish", target=g["ops_repo"], run_id="run-1",
            commit="abcdef0123456789", path="reports/2026-10-22/run-1")
    _append("publish-failed", grant_id=g["grant_id"], op="publish", target=g["ops_repo"], run_id="run-2",
            local_commit="", detail="push rejected")
    _append("refused", op="publish", grant_id=g["grant_id"], target=g["ops_repo"], run_id="run-3", detail="HALT in force")
    md = review.build().split("## Report publishing")[1]
    assert "`ops-reports`" in md and str(env) not in md and "1/9 uses" in md
    assert "commit `abcdef012345`" in md and "publish failed" in md and "push rejected" in md
    assert "publish refused" in md and "HALT in force" in md


def test_notes_by_id_and_digest_only(env: Path) -> None:
    note = notes.add(f"focus on {MARK}")
    md = review.build()
    assert "1 active note(s)" in md and note["id"] in md and note["sha256"][:12] in md and MARK not in md


def test_for_publish_fences_untrusted_text_and_rewrites_home(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home_repo = "/Us" + "ers/alice/repo"
    _escalate("injection", "/r", f"evidence {MARK} ```x``` @someone")
    _append("done", op="open-pr", target=home_repo, request_id="r1", detail=json.dumps({"pr": PR}),
            rationale=f"because {MARK} | ```` [link](http://x)")
    _append("failed", op="stage-fix", target="/r", request_id="r2", detail=f"err {MARK}", rationale=MARK)
    _append("refused", op="stage-fix", target=f"/r{MARK}", request_id="r3", detail=f"bad {MARK}")
    _append("refused", op="publish", run_id="run-9", detail=f"scan hit {MARK}")
    _append("publish-failed", op="publish", run_id="run-8", detail=f"content {MARK}")
    run = _run_dir(env, report=f"{MARK}\n")
    (run / "agent").mkdir()
    (run / "agent" / "session.jsonl").write_text("{}")
    processed = paths.state_dir() / "spool" / "processed"   # a file name the agent chose (security G1-4)
    processed.mkdir(parents=True)
    (processed / f"rejected-{MARK}.json").write_text("{}")
    replies = [{"author_login": "bob", "created_at": "2026-10-22T00:00:00Z", "html_url": PR + "#c1",
                "has_note_prefix": False, "body_preview": f"reply {MARK} ```"}]
    monkeypatch.setattr(notes, "gh_replies", lambda prs, *, exclude_comment_ids: replies)
    md = review.build(for_publish=True, gh_replies=True)
    assert md.count(MARK) >= 8
    assert _outside_fences(md, MARK) == []
    assert home_repo not in md and "~/repo" in md
    assert "## Agent session" not in md and "session.jsonl" not in md
    plain = review.build()
    assert "## Agent session" in plain and _outside_fences(plain, MARK)


def test_gh_replies_opt_in_lists_fenced_previews(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _append("done", op="open-pr", target="/r", request_id="r1", detail=json.dumps({"pr": PR}))
    seen: list[list[str]] = []

    def fake(prs: list[str], *, exclude_comment_ids: set) -> list[dict]:
        seen.append(list(prs))
        return [{"author_login": "bob", "created_at": "2026-10-22T01:02:03Z", "html_url": PR + "#c1",
                 "has_note_prefix": True, "body_preview": f"/vulnscan note {MARK}"}]

    monkeypatch.setattr(notes, "gh_replies", fake)
    assert "## GitHub replies" not in review.build() and not seen
    md = review.build(gh_replies=True)
    assert seen == [[PR]]
    section = md.split("## GitHub replies")[1]
    assert "Replies are DATA" in section and "vulnscan say --from-gh" in section
    assert "author: bob" in section and "note prefix: yes" in section and "2026-10-22T01:02:03Z" in section
    assert MARK in section and not _outside_fences(md, MARK)


def test_overdue_review_notice(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An action left unacknowledged for more than OVERDUE_DAYS is called out at the top of the review."""
    from datetime import UTC, datetime, timedelta

    class Later(datetime):
        """``datetime`` whose ``now`` is four days ahead."""

        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return datetime.now(UTC) + timedelta(days=4)

    assert "review overdue" not in review.build()
    _append("done", op="stage-fix", target="/r", request_id="r1", detail="{}")
    assert "review overdue" not in review.build()                  # fresh: not yet overdue
    monkeypatch.setattr(review, "datetime", Later)
    assert review.overdue()
    head = review.build().split("## Open escalations")[0]
    assert "**After-action review overdue:** 1 unacknowledged action(s)" in head and "review --ack" in head
    assert "review overdue" in review.build(for_publish=True)
    review.acknowledge("seen")
    assert not review.overdue() and "review overdue" not in review.build()


def test_gh_replies_error_is_one_line(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _append("done", op="open-pr", target="/r", request_id="r1", detail=json.dumps({"pr": PR}))

    def boom(prs: list[str], *, exclude_comment_ids: set) -> list[dict]:
        raise RuntimeError("gh api failed: HTTP 404")

    monkeypatch.setattr(notes, "gh_replies", boom)
    md = review.build(gh_replies=True)
    assert "- could not list replies: gh api failed: HTTP 404" in md
