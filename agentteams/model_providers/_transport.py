"""Hardened HTTP transport for :mod:`agentteams.model_providers`: no redirects, size caps, bounded retries.

Redirect refusal follows ``agentteams/vulnscan/_http.py``'s ``_NoRedirect``. Tests replace :data:`_sleep` and the
openers' ``open`` so nothing waits or reaches a real host.
"""

from __future__ import annotations

import http.client
import json
import math
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentteams.model_providers._common import ModelProviderError, _is_loopback, _scrub, _strict_loads

if TYPE_CHECKING:
    from agentteams.model_providers._config import Provider

MAX_RESPONSE_BYTES = 16 * 1024 * 1024

MAX_ERROR_BODY_BYTES = 64 * 1024

MAX_RETRIES = 2

BACKOFF_CAP_S = 30.0

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})

USER_AGENT = "agentteams-model-providers/1"



class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a redirect could carry the key header to another host."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        """Raise instead of following (urllib hook).

        Raises:
            ModelProviderError: ``protocol``, always.
        """
        raise ModelProviderError("protocol", f"refusing HTTP {code} redirect", status=code)



#: Remote presets honour the environment's proxy settings (this environment's egress proxy needs them).
_OPENER = urllib.request.build_opener(_NoRedirect)

#: Loopback presets never go through a proxy.
_LOOPBACK_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))

#: Replaced in tests so backoff does not wait.
_sleep: Callable[[float], None] = time.sleep



@dataclass
class _Response:
    status: int
    headers: Mapping[str, str]
    payload: Any
    attempts: int



def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None  # the HTTP-date form is not honoured; the backoff applies instead
    return seconds if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None



def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "not_found"
    if status == 413:
        return "too_large"
    if status == 429:
        return "rate_limited"
    if status in (503, 529):
        return "overloaded"
    if status == 408:
        return "timeout"
    if status >= 500:
        return "server"
    return "bad_request"



# UNVERIFIED: a heuristic. No provider doc read here names a fixed error code for a content-policy 4xx, so a
# 4xx whose error text carries one of these markers maps to "refused" rather than "bad_request".
_REFUSAL_MARKERS = ("content_filter", "content_policy", "safety", "responsibleai", "prohibited")



def _error_text(raw: bytes) -> str:
    try:
        body = _strict_loads(raw)
    except ValueError:
        return raw.decode("utf-8", errors="replace")
    err = body.get("error") if isinstance(body, dict) else None
    # Anthropic's nested {"error": {type, message}} is verified (api/errors); OpenAI's and Google's are
    # UNVERIFIED here (same nesting assumed). Anything else falls back to the scrubbed, capped raw text.
    if isinstance(err, dict):
        parts = [str(err.get(k)) for k in ("type", "code", "status") if isinstance(err.get(k), (str, int))]
        return " ".join(parts + [str(err.get("message", ""))]).strip()
    if isinstance(err, str):
        return err
    return raw.decode("utf-8", errors="replace")



def _read_capped(resp: Any, cap: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(min(1 << 16, cap + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            raise ModelProviderError("protocol", f"response larger than {cap} bytes (discarded)")



def _post_json(provider: Provider, url: str, auth: dict[str, str], body: Mapping[str, Any],
               timeout: float) -> _Response:
    """POST ``body`` as JSON to ``url`` with bounded retries; return the decoded JSON reply.

    ``auth`` (the key-bearing headers) and every header dict built from it are cleared before this returns or an
    error leaves it, so a traceback rendered with locals, here or in the caller, does not show the key.

    Args:
        provider: The provider (name for errors, key for scrubbing).
        url: The request URL (a fixed path on the vetted base URL).
        auth: Auth headers; emptied in place on exit.
        body: JSON request body.
        timeout: Per-attempt socket timeout in seconds.

    Returns:
        Status, headers, decoded payload and the number of attempts.

    Raises:
        ModelProviderError: Any transport, HTTP or decoding failure, mapped to the closed kinds, key scrubbed.
    """
    all_headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT,
                   **auth}
    try:
        return _send(provider, url, all_headers, body, timeout)
    finally:
        auth.clear()          # the caller's dict
        all_headers.clear()   # the same dict _send's frame holds



def _send(provider: Provider, url: str, all_headers: dict[str, str], body: Mapping[str, Any],
          timeout: float) -> _Response:
    """The retry loop behind :func:`_post_json`; each attempt's request headers are cleared when it ends."""
    name = provider.preset.name
    data = json.dumps(body, allow_nan=False, ensure_ascii=False).encode("utf-8")
    opener = _LOOPBACK_OPENER if _is_loopback(url) else _OPENER
    deadline = time.monotonic() + timeout * (1 + MAX_RETRIES) + BACKOFF_CAP_S
    attempts = 0
    last: ModelProviderError | None = None
    while True:
        attempts += 1
        retry_after: float | None = None
        req: urllib.request.Request | None = None
        try:
            req = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
            # A timeout here (no response byte yet) is an OSError, retried below like a connection failure.
            resp = opener.open(req, timeout=timeout)
            with resp:
                if resp.geturl() != url:
                    raise ModelProviderError("protocol", "response came from an unexpected URL")
                ctype = resp.headers.get("Content-Type", "")
                try:
                    raw = _read_capped(resp, MAX_RESPONSE_BYTES)
                except TimeoutError as exc:       # mid-body: may be billed, never retried
                    raise ModelProviderError("timeout", "response body timed out") from exc
                except (OSError, http.client.HTTPException) as exc:  # likewise: a reply had started
                    raise ModelProviderError("network", f"response body read failed: {type(exc).__name__}") from exc
                if "json" not in ctype.lower():
                    raise ModelProviderError("protocol", f"response is not JSON (Content-Type {_scrub(ctype)!r})")
                try:
                    payload = _strict_loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ModelProviderError("protocol", "response is not valid JSON") from exc
                return _Response(resp.status, dict(resp.headers.items()), payload, attempts)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = _retry_after(exc.headers)
            try:
                text = _scrub(_error_text(exc.read(MAX_ERROR_BODY_BYTES)), provider._key)
            except (OSError, http.client.HTTPException):
                text = ""
            finally:
                exc.close()
            kind = _status_kind(status)
            if kind == "bad_request" and any(m in text.lower() for m in _REFUSAL_MARKERS):
                kind = "refused"
            err = ModelProviderError(kind, f"HTTP {status}: {text}" if text else f"HTTP {status}", status=status,
                                     provider=name, attempts=attempts, retry_after=retry_after)
            if status not in RETRY_STATUSES:
                raise err from None
            last = err
        except ModelProviderError as exc:
            exc.provider, exc.attempts = name, attempts
            raise
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                last = ModelProviderError("timeout", f"no response within {timeout}s")
            else:
                last = ModelProviderError("network", _scrub(f"{type(exc).__name__}: {reason}", provider._key))
        finally:
            if req is not None:     # urllib's frames in a chained traceback hold this same object
                req.headers.clear()
                req.unredirected_hdrs.clear()
        last.provider, last.attempts = name, attempts
        if attempts > MAX_RETRIES:
            raise last
        delay = min(BACKOFF_CAP_S, retry_after if retry_after is not None else 2 ** attempts + random.random())
        if time.monotonic() + delay > deadline:
            raise last
        _sleep(delay)
