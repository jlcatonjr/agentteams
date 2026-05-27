"""Unit tests for agentteams.pr_management (gh subprocess fully mocked)."""

from __future__ import annotations

import datetime as _dt
import io
import json
import subprocess
from pathlib import Path

import pytest

from agentteams import pr_management as pm


# ----------------------------- registry ------------------------------------

def test_load_registry_missing_returns_empty(tmp_path):
    reg = pm.load_registry(tmp_path / "absent.json")
    assert reg.recipients == ()
    assert reg.default_reminder_interval_hours == pm.DEFAULT_INTERVAL_HOURS


def test_load_registry_seed_parses(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({
        "version": 1,
        "default_reminder_interval_hours": 12,
        "recipients": [
            {"login": "alice", "role": "owner", "default_assignee": True,
             "notifications_confirmed": True},
            {"login": "bob", "role": "reviewer", "opt_out": True},
            {"login": "carol", "role": "reviewer",
             "reminder_interval_hours": 48},
        ],
    }))
    reg = pm.load_registry(p)
    assert {r.login for r in reg.recipients} == {"alice", "bob", "carol"}
    assert reg.default_assignees() == ("alice",)
    assert {r.login for r in reg.active()} == {"alice", "carol"}
    assert reg.interval_for("carol") == 48
    assert reg.interval_for("alice") == 12
    assert reg.interval_for("unknown") == 12


def test_repo_seed_file_validates_against_schema():
    """The committed sample registry must parse and contain at least one
    active recipient — otherwise the reminder workflow has no one to nag."""
    reg = pm.load_registry(pm.DEFAULT_REGISTRY)
    assert reg.version == 1
    assert len(reg.recipients) >= 1
    assert len(reg.active()) >= 1


# ----------------------------- gh wrappers (mocked) ------------------------

def _cp(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def test_list_stale_prs_filters_by_age():
    now = _dt.datetime(2026, 5, 27, 12, 0, tzinfo=_dt.timezone.utc)
    fresh = (now - _dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    old = (now - _dt.timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    payload = [
        {"number": 1, "title": "fresh", "author": {"login": "x"},
         "assignees": [{"login": "a"}], "updatedAt": fresh,
         "url": "u", "comments": []},
        {"number": 2, "title": "old", "author": {"login": "x"},
         "assignees": [{"login": "a"}], "updatedAt": old,
         "url": "u", "comments": []},
    ]
    runner = lambda argv: _cp(stdout=json.dumps(payload))
    stale = pm.list_stale_prs(interval_hours=24, now=now, runner=runner)
    assert [p.number for p in stale] == [2]


def test_list_stale_prs_dedups_recent_reminder():
    now = _dt.datetime(2026, 5, 27, 12, 0, tzinfo=_dt.timezone.utc)
    old = (now - _dt.timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    recent_ping = (now - _dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    payload = [{
        "number": 7, "title": "with-fresh-ping", "author": {"login": "x"},
        "assignees": [{"login": "a"}], "updatedAt": old, "url": "u",
        "comments": [{"body": "[pr-reminder] hi @a",
                      "createdAt": recent_ping}],
    }]
    runner = lambda argv: _cp(stdout=json.dumps(payload))
    stale = pm.list_stale_prs(interval_hours=24, now=now, runner=runner)
    assert stale == []  # dedup'd because reminder posted 2h ago


def test_list_stale_prs_handles_gh_failure():
    runner = lambda argv: _cp(rc=1, stderr="oops")
    assert pm.list_stale_prs(runner=runner) == []


def test_post_reminder_prefixes_body():
    seen = {}
    def runner(argv):
        seen["argv"] = argv
        return _cp(stdout="ok")
    res = pm.post_reminder(42, "please review", runner=runner)
    assert res.ok and res.code == 0
    assert pm.REMINDER_PREFIX in seen["argv"][-1]
    assert "please review" in seen["argv"][-1]


def test_post_reminder_does_not_double_prefix():
    seen = {}
    def runner(argv):
        seen["argv"] = argv
        return _cp()
    pm.post_reminder(1, f"{pm.REMINDER_PREFIX} already prefixed", runner=runner)
    body = seen["argv"][-1]
    assert body.count(pm.REMINDER_PREFIX) == 1


def test_assign_recipients_empty_is_noop():
    called = []
    res = pm.assign_recipients(1, [], runner=lambda a: called.append(a) or _cp())
    assert res.ok and not called


def test_assign_recipients_dedup_and_sort():
    seen = {}
    pm.assign_recipients(
        9, ["bob", "alice", "bob"],
        runner=lambda argv: (seen.setdefault("argv", argv), _cp())[1],
    )
    assert "alice,bob" in seen["argv"]


# ----------------------------- end-of-task prompt --------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1", "continue-branch"),
    ("2", "push-main"),
    ("3", "open-pr"),
    ("open-pr", "open-pr"),
    ("CONTINUE-BRANCH", "continue-branch"),
    ("", "continue-branch"),       # empty → default
    ("nonsense", "continue-branch"),  # unknown → default
])
def test_prompt_next_action_parses(raw, expected):
    out = io.StringIO()
    got = pm.prompt_next_action(
        reader=lambda: raw,
        writer=out.write,
    )
    assert got == expected


def test_prompt_non_interactive_returns_default():
    assert pm.prompt_next_action(non_interactive=True, default="open-pr") == "open-pr"


def test_prompt_non_interactive_default_continue_branch():
    assert pm.prompt_next_action(non_interactive=True) == "continue-branch"


def test_prompt_eof_returns_default():
    def raise_eof():
        raise EOFError
    out = io.StringIO()
    assert pm.prompt_next_action(reader=raise_eof, writer=out.write) == "continue-branch"


# ----------------------------- CLI -----------------------------------------

def test_cli_remind_dry_run(capsys):
    rc = pm.main(["remind", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "dry-run" in captured.out


def test_cli_prompt_non_interactive(capsys):
    rc = pm.main(["prompt", "--non-interactive", "--default", "open-pr"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == "open-pr"


# ----------------------------- agent file presence -------------------------

def test_agent_files_present():
    root = Path(__file__).resolve().parents[1] / ".github" / "agents"
    for name in ("pr-manager", "pr-notifier", "pr-reminder"):
        f = root / f"{name}.agent.md"
        assert f.is_file(), f"missing {f}"
        txt = f.read_text(encoding="utf-8")
        assert "<!-- AGENTTEAMS:BEGIN content v=1 -->" in txt
        assert "<!-- AGENTTEAMS:END content -->" in txt
        assert "## Project-Specific Notes" in txt


def test_schema_file_present_and_parses():
    schema = (Path(__file__).resolve().parents[1] / "schemas"
              / "pr-recipient-registry.schema.json")
    assert schema.is_file()
    data = json.loads(schema.read_text(encoding="utf-8"))
    assert data.get("$schema", "").startswith("https://json-schema.org/")
    assert "recipients" in data["properties"]
