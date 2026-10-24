"""agentteams.vulnscan.notify — fixed-text notifications (R6 security M-5)."""

from __future__ import annotations

import subprocess

import pytest

from agentteams.vulnscan import notify


def test_render_accepts_only_ids_counts_and_known_words() -> None:
    assert "esc-0a1b" in notify.render("escalation", id="esc-0a1b", kind="ioc")
    assert notify.render("run-done", state="action needed", findings=3, new=1).startswith("Daily run action needed")
    for bad in ({"id": "esc 1; curl evil", "kind": "ioc"}, {"id": "esc-1", "kind": "see https://x"},
                {"id": "x" * 200, "kind": "ioc"}):
        with pytest.raises(ValueError):
            notify.render("escalation", **bad)
    with pytest.raises(ValueError):
        notify.render("run-done", state="anything the agent said", findings=1, new=0)
    with pytest.raises(KeyError):
        notify.render("free-text")


def test_send_passes_only_the_fixed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(notify.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert notify.send({"notify": "command:/usr/bin/true --flag"}, "publish-failed") is True
    assert calls[0][:2] == ["/usr/bin/true", "--flag"] and "FAILED" in calls[0][-1]
    assert notify.send({"notify": "none"}, "publish-failed") is False


def test_send_refuses_an_unsafe_field_without_raising(capsys: pytest.CaptureFixture) -> None:
    assert notify.send({"notify": "none"}, "run-done", state="some new state", findings=1, new=0) is False
    assert notify.send({"notify": "none"}, "no-such-message") is False
    assert "refused" in capsys.readouterr().err


# ---------------------------------------------------------------- Round 2 (Area R)

@pytest.mark.parametrize("bad_id", ["esc-1\n", "esc-1\r", "\nesc-1", "esc-1\x00"])
def test_an_id_with_a_trailing_newline_is_refused(bad_id: str) -> None:
    with pytest.raises(ValueError):                              # X8: re.match + "$" accepted "esc-1\n"
        notify.render("escalation", id=bad_id, kind="ioc")


@pytest.mark.parametrize("field", [{"state": ["completed"]}, {"state": {"a": 1}}, {"kind": ["ioc"]},
                                   {"id": ["esc-1"]}, {"state": None}])
def test_send_never_raises_on_an_unhashable_or_odd_field(field: dict, capsys: pytest.CaptureFixture) -> None:
    fields = {"state": "completed", **field} if "state" in field else {"id": "esc-1", "kind": "ioc", **field}
    key = "agent-not-completed" if "state" in field else "escalation"
    assert notify.send({"notify": "command:/usr/bin/true"}, key, **fields) is False     # X8
    assert "refused" in capsys.readouterr().err


def _desktop(monkeypatch: pytest.MonkeyPatch, system: str, tools: set[str]) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(notify.platform, "system", lambda: system)
    monkeypatch.setattr(notify.shutil, "which", lambda name: f"/usr/bin/{name}" if name in tools else None)
    monkeypatch.setattr(notify.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    return calls


def test_osascript_gets_a_valid_applescript_literal_with_the_em_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _desktop(monkeypatch, "Darwin", {"osascript", "notify-send"})
    assert notify.send({"notify": "desktop"}, "escalation", id="esc-1", kind="ioc") is True
    (cmd,) = calls
    message = notify.render("escalation", id="esc-1", kind="ioc")
    assert "—" in message                                   # the template carries an em dash
    assert cmd[:2] == ["osascript", "-e"]
    assert cmd[2] == f'display notification "{message}" with title "{notify.TITLE}"'   # G28: no — escape
    assert "\\u" not in cmd[2]


def test_applescript_literal_escapes_quotes_and_backslashes() -> None:
    assert notify._applescript_string('a"b\\c —') == '"a\\"b\\\\c —"'


def test_linux_falls_back_to_notify_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _desktop(monkeypatch, "Linux", {"notify-send", "osascript"})
    assert notify.send({}, "publish-done") is True               # default: desktop
    assert calls == [["notify-send", notify.TITLE, notify.render("publish-done")]]
    calls = _desktop(monkeypatch, "Darwin", {"notify-send"})     # macOS without osascript
    assert notify.send({"notify": "desktop"}, "publish-done") is True and calls[0][0] == "notify-send"


def test_no_notifier_available_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _desktop(monkeypatch, "Linux", set())
    assert notify.send({"notify": "desktop"}, "publish-done") is False and calls == []


@pytest.mark.parametrize("error", [OSError("no such program"), subprocess.TimeoutExpired(["x"], 60)])
def test_a_failing_notifier_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch, error: Exception,
                                                   capsys: pytest.CaptureFixture) -> None:
    def run(cmd: list[str], **kw: object) -> None:
        raise error
    monkeypatch.setattr(notify.subprocess, "run", run)
    assert notify.send({"notify": "command:/nonexistent/notifier"}, "publish-done") is False
    assert "notification failed" in capsys.readouterr().err
    assert notify.send({"notify": "command:'unbalanced"}, "publish-done") is False
    assert "malformed" in capsys.readouterr().err
