"""Security-intelligence feeds returning truncated or invalid responses (#15).

A truncated body (``http.client.IncompleteRead``) or a body that is not a JSON object must surface
as a readable error naming the feed, never as a traceback out of team generation, and the
fail-closed freshness gate must still refuse the run.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from agentteams import security_refs
from agentteams.cli import security_gate

_KEV_HOST_URL = security_refs._KEV_URL


class _Resp:
    """urllib response stand-in: ``read`` returns ``body`` or raises ``exc``."""

    def __init__(self, url: str, body: bytes = b"", exc: BaseException | None = None):
        self._url, self._body, self._exc = url, body, exc

    def read(self, size: int = -1) -> bytes:
        if self._exc is not None:
            raise self._exc
        return self._body if size is None or size < 0 else self._body[:size]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


_TRUNCATED_JSON = b'{"catalogVersion": "2026.10.22", "count": 2, "vulnerabilities": [{"cveID": "CVE-2026-0'
_KEV_OK = {"catalogVersion": "2026.10.22", "count": 1, "vulnerabilities": [{
    "cveID": "CVE-2026-0001", "vendorProject": "V", "product": "P", "vulnerabilityName": "RCE",
    "dateAdded": "2026-10-21", "requiredAction": "Patch.", "knownRansomwareCampaignUse": "Unknown"}]}


def _urlopen_for(kev: _Resp | None = None, epss: _Resp | None = None):
    def _fake(req, timeout=12):
        if "known_exploited_vulnerabilities" in req.full_url and kev is not None:
            return kev
        if "api.first.org" in req.full_url and epss is not None:
            return epss
        raise OSError(f"unexpected URL: {req.full_url}")
    return _fake


@pytest.mark.parametrize("resp, detail", [
    (_Resp(_KEV_HOST_URL, exc=http.client.IncompleteRead(b"x" * 100, 5000)), "truncated"),
    (_Resp(_KEV_HOST_URL, body=_TRUNCATED_JSON), "not valid JSON"),
    (_Resp(_KEV_HOST_URL, body=b"[" + b"1, " * 40 + b"1]"), "not a JSON object"),
], ids=["incomplete-read", "invalid-json", "not-an-object"])
def test_fetch_json_names_the_feed(monkeypatch, resp, detail):
    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _urlopen_for(kev=resp))
    with pytest.raises(OSError) as info:
        security_refs._fetch_json(_KEV_HOST_URL)
    assert "CISA KEV" in str(info.value) and detail in str(info.value)


@pytest.mark.parametrize("resp", [
    _Resp(_KEV_HOST_URL, exc=http.client.IncompleteRead(b"x" * 100, 5000)),
    _Resp(_KEV_HOST_URL, body=_TRUNCATED_JSON),
], ids=["incomplete-read", "invalid-json"])
def test_bad_kev_is_reported_and_the_freshness_gate_still_refuses(monkeypatch, tmp_path: Path, capsys, resp):
    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _urlopen_for(kev=resp))
    output_dir = tmp_path / ".github" / "agents"
    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir, offline=False, max_items=2, skip_nvd=True)
    err = capsys.readouterr().err
    assert "Warning: security intelligence: CISA KEV" in err
    assert placeholders["SECURITY_DATA_FRESHNESS_STATUS"] == "stale"
    payload = json.loads(placeholders["SECURITY_VULNERABILITY_WATCH_JSON"])
    kev = next(s for s in payload["sources"] if s["name"] == "CISA KEV")
    assert kev["status"] == "unavailable"
    with pytest.raises(RuntimeError, match="security intelligence is stale"):
        security_gate._assert_security_intelligence_fresh(placeholders, output_dir=output_dir)


def test_bad_epss_is_reported_without_a_traceback(monkeypatch, tmp_path: Path, capsys):
    kev = _Resp(_KEV_HOST_URL, body=json.dumps(_KEV_OK).encode())
    epss = _Resp(security_refs._EPSS_URL + "?cve=CVE-2026-0001", body=b'{"status": "OK", "data": [')
    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _urlopen_for(kev=kev, epss=epss))
    placeholders = security_refs.build_security_placeholders(
        output_dir=tmp_path / ".github" / "agents", offline=False, max_items=2, skip_nvd=True)
    assert "Warning: security intelligence: FIRST EPSS" in capsys.readouterr().err
    payload = json.loads(placeholders["SECURITY_VULNERABILITY_WATCH_JSON"])
    epss_src = next(s for s in payload["sources"] if s["name"] == "FIRST EPSS")
    assert epss_src["status"] == "unavailable"
