"""agentteams.vulnscan.notify — fixed-text notifications (R6 security M-5)."""

from __future__ import annotations

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
