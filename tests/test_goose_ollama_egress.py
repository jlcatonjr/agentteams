"""A Goose exclusive profile whose sanctioned egress can reach Ollama warns at render (SEC-2026-11-23-im-sealed-mail-B).

Ollama's ``/api/pull`` of a model named ``host/ns/x`` makes Ollama connect to ``host`` (measured 2026-11-23), so a
loopback egress that reaches Ollama is a channel off the machine. The filter that closes it is 1.4 scope; until then
the render warns.
"""

from __future__ import annotations

import sys

import pytest

from agentteams.frameworks import _goose_egress as E
from agentteams.frameworks import _goose_sandbox_emit as G


@pytest.fixture(autouse=True)
def _no_ollama_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


@pytest.mark.parametrize("endpoint", ["localhost:11434", "*:11434", "localhost", "*"])
def test_an_egress_that_reaches_ollama_warns(endpoint: str) -> None:
    warning = E.ollama_egress_warning(endpoint)
    assert warning and "/api/pull" in warning and "not network-isolated" in warning


def test_a_leading_zero_port_still_warns() -> None:
    assert E.ollama_egress_warning("localhost:011434")


def test_a_wildcard_host_is_not_called_loopback() -> None:
    assert "every host" in (E.ollama_egress_warning("*:11434") or "")
    assert "every port on every host" in (E.ollama_egress_warning("*") or "")
    assert "on loopback" in (E.ollama_egress_warning("localhost:11434") or "")


def test_the_warning_is_written_into_the_profile() -> None:
    """SEC-2026-11-23-im-sealed-mail-C: stderr scrolls away, so the emitted sandbox.sb carries it too."""
    prof = G._build_seatbelt_profile(["."], None, "localhost:11434", deny_network=True)
    assert ";; WARNING (Ollama relay):" in prof and '(allow network* (remote ip "localhost:11434"))' in prof
    assert ";; WARNING (Ollama relay)" not in G._build_seatbelt_profile(["."], None, "localhost:8888", deny_network=True)


@pytest.mark.parametrize("endpoint", ["localhost:8080", "*:3128", None, ""])
def test_another_port_or_no_egress_does_not_warn(endpoint: str | None) -> None:
    assert E.ollama_egress_warning(endpoint) is None


@pytest.mark.parametrize("value", ["127.0.0.1:9999", "http://localhost:9999", "http://localhost:9999/", ":9999",
                                   "http://h:9999/path", "[::1]:9999", "http://u:p@h:9999", "h:9999?x=1",
                                   "h:9999#frag"])
def test_the_port_in_ollama_host_counts_too(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OLLAMA_HOST", value)
    assert E.ollama_egress_warning("localhost:9999")
    assert E.ollama_egress_warning("localhost:11434")


@pytest.mark.parametrize("value", ["localhost", "::1", "host:abc", "", "host:\u00b2", "host:99999", "host:0"])
def test_an_ollama_host_without_a_usable_port_adds_nothing(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OLLAMA_HOST", value)
    assert E.ollama_egress_warning("localhost:8080") is None
    assert E.ollama_egress_warning("localhost:11434")


@pytest.mark.parametrize("endpoint", ["localhost:abc", "localhost:\u00b2", "localhost:99999"])
def test_an_invalid_port_returns_none_rather_than_raising(endpoint: str) -> None:
    assert E.ollama_egress_warning(endpoint) is None


def _render(profile: str, egress: str, capsys: pytest.CaptureFixture[str]) -> str:
    from agentteams import analyze

    m = analyze.build_manifest({"project_goal": "x", "privilege_profile": profile, "goose_egress_proxy": egress},
                               framework="goose")
    capsys.readouterr()
    G.goose_sandbox_output_files(m)
    return capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "darwin", reason="goose Seatbelt emits on macOS only")
def test_the_exclusive_render_warns_for_an_ollama_egress(capsys: pytest.CaptureFixture[str]) -> None:
    assert "/api/pull" in _render("exclusive", "localhost:11434", capsys)


@pytest.mark.skipif(sys.platform != "darwin", reason="goose Seatbelt emits on macOS only")
@pytest.mark.parametrize(("profile", "egress"), [("exclusive", "localhost:8888"), ("confined", "localhost:11434")])
def test_no_warning_off_ollama_or_outside_exclusive(profile: str, egress: str,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """Confined leaves egress open anyway (only a disclosure, @security); exclusive on another port is fine."""
    assert "/api/pull" not in _render(profile, egress, capsys)
