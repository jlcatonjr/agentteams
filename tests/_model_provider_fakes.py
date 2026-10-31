"""Offline fakes shared by the ``test_model_providers_*`` modules (no test here reaches a real provider)."""

from __future__ import annotations

import json
import urllib.request
from email.message import Message
from typing import Any

import pytest

from agentteams import model_providers as mp
from agentteams.model_providers import _transport


def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every urllib opener raise, so a test that forgets to fake HTTP fails instead of calling out."""
    def _refuse(self: Any, *a: Any, **k: Any) -> Any:
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _refuse)
    monkeypatch.setattr(_transport, "_sleep", lambda s: None)
    monkeypatch.delenv("CI", raising=False)


class FakeResponse:
    """Enough of ``http.client.HTTPResponse`` for :func:`agentteams.model_providers._transport._post_json`."""

    def __init__(self, url: str, body: bytes, *, status: int = 200, content_type: str = "application/json",
                 headers: dict[str, str] | None = None) -> None:
        self._url, self._body, self.status = url, body, status
        msg = Message()
        msg["Content-Type"] = content_type
        for k, v in (headers or {}).items():
            msg[k] = v
        self.headers = msg

    def geturl(self) -> str:
        return self._url

    def read(self, n: int = -1) -> bytes:
        out, self._body = (self._body, b"") if n < 0 else (self._body[:n], self._body[n:])
        return out

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class Recorder:
    """Replaces both openers; answers each call with the next reply dict and records requests."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *replies: Any, headers: dict[str, str] | None = None) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.headers = headers or {}
        for opener in (_transport._OPENER, _transport._LOOPBACK_OPENER):
            monkeypatch.setattr(opener, "open", self._open)

    def _open(self, req: urllib.request.Request, timeout: float | None = None) -> FakeResponse:
        self.requests.append({"url": req.full_url, "headers": {k.lower(): v for k, v in req.header_items()},
                              "body": json.loads(req.data)})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(req.full_url, json.dumps(reply).encode(), headers=self.headers)

    @property
    def body(self) -> dict[str, Any]:
        return self.requests[-1]["body"]


def provider(name: str, key: str = "test-key-0123456789", base_url: str | None = None) -> mp.Provider:
    """A provider built from a synthetic key (never a real one)."""
    return mp.Provider.from_key(name, key if mp.PROVIDERS[name].key_env else "", base_url=base_url)


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


TOOL = mp.ToolSpec("run_code", "Run code in the sandbox.", {
    "type": "object", "$schema": "https://json-schema.org/draft/2020-12/schema", "additionalProperties": False,
    "properties": {"src": {"type": "string", "description": "source", "default": ""},
                   "lang": {"type": "string", "enum": ["python", "javascript"]}},
    "required": ["src"]})
