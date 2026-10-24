"""Hardened JSON-over-HTTPS transport for the vulnerability feeds.

Same integrity rules as ``agentteams.security_refs``: HTTPS only, an explicit host
allowlist, and size-bounded bodies. Redirects are refused outright.
This module keeps its own allowlist because it needs a different set of hosts.
Transient failures (connection resets, truncated bodies, invalid JSON) are retried. A
4xx answer is not retried.

Tests replace :func:`fetch_json` through monkeypatching. Callers therefore reach it as
``_http.fetch_json`` rather than importing the name.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

#: Hosts whose responses are accepted.
ALLOWED_HOSTS: frozenset[str] = frozenset({
    "api.osv.dev",      # package advisories
    "pypi.org",         # release lists + upload dates for pin remediation
    "nodejs.org",       # dist/index.json: per-release `security` flag
    "endoflife.date",   # runtime release cycles and EOL dates
    "www.php.net",      # releases JSON: per-release `security` tag
})

#: (min_bytes, max_bytes) per host.
SIZE_BOUNDS: dict[str, tuple[int, int]] = {
    "api.osv.dev": (2, 32 * 1024 * 1024),
    "pypi.org": (2, 64 * 1024 * 1024),
    "nodejs.org": (64, 8 * 1024 * 1024),
    "endoflife.date": (2, 4 * 1024 * 1024),
    "www.php.net": (2, 4 * 1024 * 1024),
}

USER_AGENT = "agentteams-vulnscan/1"


class FeedError(OSError):
    """A vulnerability feed could not be read; results that depend on it are unknown."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: the feeds are fixed endpoints, so a redirect is a fault."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        """Raise instead of following (urllib hook).

        Raises:
            FeedError: Always.
        """
        raise FeedError(f"refusing redirect {code} from {req.full_url} to {newurl}")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def fetch_json(url: str, payload: dict | None = None, *, timeout: float = 60.0,
               retries: int = 4) -> dict | list:
    """GET (or POST ``payload`` as JSON to) ``url`` and decode the JSON body.

    Args:
        url: HTTPS URL on an allowlisted host.
        payload: JSON body to POST; ``None`` sends a GET.
        timeout: Per-attempt timeout in seconds.
        retries: Extra attempts after a transient failure.

    Returns:
        The decoded JSON value.

    Raises:
        FeedError: Host not allowlisted, non-HTTPS, size out of bounds, a 4xx answer,
            or every attempt failed.
    """
    if _host(url) not in ALLOWED_HOSTS or not url.startswith("https://"):
        raise FeedError(f"refusing non-allowlisted feed URL: {url}")
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(2 ** attempt, 10))
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                final = resp.geturl()
                host = _host(final)
                if not final.startswith("https://") or host not in ALLOWED_HOSTS:
                    raise FeedError(f"redirected off the allowlist: {final}")
                low, high = SIZE_BOUNDS[host]
                body = resp.read(high + 1)
            if not low <= len(body) <= high:
                raise FeedError(f"{url}: response size {len(body)} outside [{low}, {high}]")
            return json.loads(body.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise FeedError(f"{url}: HTTP {exc.code}") from exc
            last = exc
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                ConnectionError, json.JSONDecodeError) as exc:
            last = exc
    raise FeedError(f"{url}: failed after {retries + 1} attempts: {last}")
