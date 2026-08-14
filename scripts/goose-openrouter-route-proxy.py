#!/usr/bin/env python3
"""Local proxy that injects OpenRouter provider routing into every goose request.

WHY THIS EXISTS
---------------
OpenRouter serves one model id from many upstream backends, and they are not equally
correct. Measured 2026-07-24 against ``qwen/qwen3.6-27b`` by replaying a real captured
agent payload (11 messages, 24 tools, reasoning enabled), 12 trials per backend:

    Alibaba 12/12 ok   CoreWeave 12/12 ok   Morph 12/12 ok
    Chutes  1/12 leak  Phala     1/12 leak  SiliconFlow 3/12 leak

A "leak" is the dead-turn bug: the model emits its tool call as literal
``<tool_call>`` text inside its reasoning stream instead of the structured tool-call
field, so goose sees nothing actionable, the turn ends with ``finish_reason: stop``,
and NO error is logged anywhere. See
``references/plans/goose-openrouter-tool-call-reasoning-leak-2026-07-24.report.md``.

OpenRouter can be told which backends to use -- but **goose 1.37.0 provides no way to
send that**. ``OPENROUTER_PARAMETERS`` in ``config.yaml`` is inert (verified: the key
does not appear in the 1.37.0 binary, and neither a nested ``provider`` block nor a
top-level ``transforms`` override reaches the wire, while a ``model`` change in the
same file does). ``OPENROUTER_HOST`` *is* honored, so this proxy adds the routing at
the transport layer.

Because it sits under ``OPENROUTER_HOST``, it covers **every goose surface** -- CLI,
``goose acp`` (the VS Code extension), and desktop -- unlike a ``goose run`` subprocess
wrapper, which by construction cannot see ACP traffic.

USAGE
-----
    python3 scripts/goose-openrouter-route-proxy.py --port 8791

Then in ``~/.config/goose/config.yaml``::

    OPENROUTER_HOST: http://127.0.0.1:8791

and restart goose (for the VS Code extension: reload the window, so the ``goose acp``
daemons re-read config).

Defaults to an **allowlist** of backends measured clean. Prefer ``--only`` over
``--ignore``: a denylist is only as good as your enumeration of bad backends, and an
incomplete one silently reroutes to whatever is left -- which is how an early version
of this work landed on SiliconFlow, the worst measured offender.

OPERATIONAL COST, STATED PLAINLY
--------------------------------
This is a process that must be running. If it is not, goose has no endpoint and every
request fails. That is a real tradeoff versus tolerating an intermittent dead turn;
it is why nothing here is installed or auto-started for you.

CAVEATS
-------
* "Clean" is bounded, not proven: 0/12 per backend leaves a ~22% upper bound on the
  true leak rate at 95% confidence (~10% pooled across the three). This reduces the
  failure rate; it does not guarantee zero.
* **Confirmed the hard way later the same day: the leak recurred on Morph** -- one of
  the three backends above -- with this proxy verifiably injecting the allowlist. An
  8-run end-to-end test still produced 2 silent dead turns. Treat the table as "these
  were the best of a bad set", not as a list of safe backends.
* Restricting backends can lower the effective context ceiling (Morph advertises 131k
  vs 262k for the others) and reduce availability/price competition.
* Backend behavior is not static, and **no tooling here re-measures it for you**.
  ``scripts/goose-openrouter-preflight.py --providers <model>`` lists the current
  roster (who serves the model, context, quantization) -- useful for spotting that a
  listed backend has disappeared, but it does NOT measure tool-call reliability. That
  still requires replaying a real payload N times per backend by hand; an automated
  ``--measure`` mode is logged as open work in
  ``references/agentteams-remediation-log.csv``.
* ``--only`` sets ``allow_fallbacks: false``, so if every listed backend is delisted
  upstream the request **fails loudly** rather than silently routing somewhere
  unmeasured. That is deliberate -- a silent fallback would defeat the allowlist --
  but it does mean a stale list turns into hard errors, not degraded routing.
* Secrets: the ``Authorization`` header is relayed verbatim and never logged.

IMAGE INTERCEPTION (2026-08-13)
-------------------------------
Standing operator policy: **raw image content never leaves this machine.** Every
``image_url`` content part in a relayed ``/chat/completions`` body is replaced with
locally-extracted text (tesseract OCR plus an OpenCV dominant-color / layout-block
summary) before forwarding — regardless of whether the active model supports vision.
This both enforces the policy and fixes the 404 that motivated it: the active model
(``z-ai/glm-5.2`` since 2026-08-11) is text-only on every backend that serves it, so
any image-bearing turn died with "No endpoints found that support image input".

Requires ``pytesseract``, ``Pillow``, and ``opencv-python-headless``, pinned in the
dedicated venv ``~/.config/goose/ocr-venv`` (the ambient interpreter resolving
``cv2`` via an Anaconda ``$PATH`` entry is an accident, not a dependency). Run the
proxy with that interpreter::

    ~/.config/goose/ocr-venv/bin/python3 scripts/goose-openrouter-route-proxy.py ...

The policy **fails closed** (2026-08-13 security + adversarial review): an image
part that cannot be OCR'd — decode failure, oversize, a remote http(s) URL, an
``input_image``-shaped part — is replaced with a ``[image withheld: ...]`` text
notice, never forwarded raw. Likewise at startup: if the OCR libraries are not
importable the proxy **refuses to start** rather than silently running in a mode
that forwards raw images (and 404s against text-only models anyway). The explicit
escape hatch is ``--allow-raw-images``, which restores verbatim relay of image
parts and prints a warning banner.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_UPSTREAM = "https://openrouter.ai"

#: Backends measured clean for structured tool-calling on 2026-07-24 (12/12 each).
#: Not a permanent fact -- re-verify before relying on it.
DEFAULT_ONLY = ("Alibaba", "CoreWeave", "Morph")
MEASURED_ON = "2026-07-24"

# Hop-by-hop headers must not be forwarded (RFC 7230 6.1). ``accept-encoding`` is
# dropped so the upstream does not hand back a compressed body we would relay
# without the matching header semantics.
_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "accept-encoding",
})


def build_provider_block(only: list[str], ignore: list[str], extra: dict,
                          prefer: list[str] | None = None) -> dict:
    """Assemble the OpenRouter ``provider`` routing object.

    Args:
        only: Backend names to restrict routing to (allowlist).
        ignore: Backend names to exclude (denylist).
        extra: Additional provider-object fields (e.g. ``{"sort": "throughput"}``).
        prefer: Backend names to try first, as ``provider.order``. Added 2026-08-10: an
            unordered ``only`` list lets OpenRouter's own price-sort pick whichever allowed
            backend is cheapest *today*, which silently drifts as prices move (measured:
            this exact allowlist's cheapest member changed twice in four days). "Use
            provider X" needs `order`, not `only`.

            CORRECTED 2026-08-10 (same day, live-verified against the API): OpenRouter does
            NOT treat ``order`` as a preference within ``only`` when ``allow_fallbacks`` is
            false — ``order`` alone becomes the entire candidate set, and anything in
            ``only`` but not in ``order`` is never tried. An initial version of this
            function set ``order`` to ``prefer`` verbatim, which is a hard single-backend
            pin in disguise: when the preferred backend later stops serving a model (as
            happened the same day -- CoreWeave has no endpoint for qwen/qwen3.8-max),
            every request 404s with "No endpoints found", not a fallback. Fixed by
            appending the rest of ``only`` (in its given order) after ``prefer``, so
            ``order`` always lists every allowed backend and ``prefer`` only affects which
            is tried first. Verified against the live API: with
            ``order: [CoreWeave, Alibaba, Z.AI]`` and ``allow_fallbacks: false``, a
            qwen/qwen3.8-max request (Alibaba-only today) succeeds via Alibaba. Also
            verified: bare ``order`` with no ``only`` falls back past an unreachable
            entry on its own (OpenRouter defaults ``allow_fallbacks`` to true there),
            so a ``prefer`` given without ``only`` is passed through as-is.

    Raises:
        ValueError: ``prefer`` names a backend not present in ``only`` (when ``only``
            is non-empty). Silently dropping it could mask a typo the caller believes
            is taking effect; silently keeping it would let an unvetted backend into
            the candidate set, defeating the allowlist the same way the order-only-pin
            bug defeated fallback.

    Returns:
        The provider routing dict; empty when no routing is requested.
    """
    block: dict[str, object] = {}
    if only:
        block["only"] = list(only)
        # An allowlist that silently falls back defeats the purpose.
        block["allow_fallbacks"] = False
    if ignore:
        block["ignore"] = list(ignore)
    if prefer:
        prefer = list(dict.fromkeys(prefer))  # de-dupe, preserve priority order
        if only:
            unknown = [backend for backend in prefer if backend not in only]
            if unknown:
                raise ValueError(
                    f"--prefer backend(s) not in --only allowlist: {unknown}; "
                    "add them to --only or remove them from --prefer"
                )
            # order must cover every allowed backend, or allow_fallbacks:false turns a
            # preference into a hard pin on `prefer` alone -- see docstring above.
            rest = [backend for backend in only if backend not in prefer]
            block["order"] = prefer + rest
        else:
            block["order"] = prefer
    block.update(extra)
    return block


def _ocr_available() -> bool:
    """Report whether the OCR/vision libraries are importable in this interpreter."""
    try:
        import cv2  # noqa: F401
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


#: Refuse to decode absurdly large inline images (decompression-bomb guard).
#: 20MB of base64 is ~15MB of image — far beyond any legitimate screenshot.
MAX_IMAGE_B64_BYTES = 20 * 1024 * 1024

#: Pixel-count ceiling enforced via ``Image.MAX_IMAGE_PIXELS`` before full decode.
#: PIL's default (~178M px) still admits a ~500MB-RAM decompression bomb per
#: request (2026-08-13 security review); 40M px covers any real screenshot
#: (a 5K display is ~14.7M px) at a bounded ~120MB decode.
MAX_IMAGE_PIXELS = 40_000_000

#: The ceiling we *resize down to* before OCR, for bounded CPU.
_OCR_MAX_DIM = 2200


def describe_image_bytes(raw: bytes) -> str:
    """OCR + structural summary for one decoded image, as a single text block.

    Runs tesseract for text and OpenCV for dominant colors and layout blocks,
    mirroring the collector-management 2026-08-13 script's approach so the model
    gets some non-text visual context despite never seeing pixels.

    Args:
        raw: Decoded image bytes (any format PIL can open).

    Returns:
        A formatted description block.

    Raises:
        Exception: Propagates any decode/OCR failure; callers decide fallback.
    """
    import io

    import cv2
    import numpy as np
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(raw))
    # Header-only check before the full decode: PIL's own bomb ceiling (~178M px)
    # still admits a ~500MB-RAM decode, and a degenerate aspect ratio (1 x 10M)
    # passes the base64 size cap while being useless to OCR.
    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"image too large: {img.width}x{img.height} exceeds {MAX_IMAGE_PIXELS} px"
        )
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > _OCR_MAX_DIM:
        scale = _OCR_MAX_DIM / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))

    text = pytesseract.image_to_string(img).strip()

    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    # Dominant colors: k-means over a downsampled pixel set (k=4, bounded cost).
    small = cv2.resize(arr, (64, 64), interpolation=cv2.INTER_AREA)
    pixels = small.reshape(-1, 3).astype(np.float32)
    k = 4
    _, labels, centers = cv2.kmeans(
        pixels, k, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
        3, cv2.KMEANS_PP_CENTERS,
    )
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(counts)[::-1]
    colors = []
    for i in order:
        b, g, r = (int(c) for c in centers[i])
        share = counts[i] * 100 // pixels.shape[0]
        colors.append(f"#{r:02x}{g:02x}{b:02x} ({share}%)")

    # Layout blocks: large contours on an edge map, as rough UI regions.
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = (img.width * img.height) * 0.005
    blocks = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h >= min_area:
            blocks.append((x, y, w, h))
    blocks.sort(key=lambda b: (b[1], b[0]))
    block_lines = [f"  - block at ({x},{y}) size {w}x{h}" for x, y, w, h in blocks[:12]]

    parts = [
        f"[image replaced by local OCR — {img.width}x{img.height}px; "
        "raw image content is never sent to the model]",
        "",
        "OCR text:",
        text if text else "(no text detected)",
        "",
        f"Dominant colors: {', '.join(colors)}",
    ]
    if block_lines:
        parts.append(f"Layout blocks ({len(blocks)} detected, top {len(block_lines)}):")
        parts.extend(block_lines)
    return "\n".join(parts)


def _describe_data_url(url: str) -> str | None:
    """Decode a ``data:image/...;base64,...`` URL and describe it; None on any failure."""
    try:
        _, _, b64 = url.partition(";base64,")
        if not b64 or len(b64) > MAX_IMAGE_B64_BYTES:
            return None
        return describe_image_bytes(base64.b64decode(b64, validate=True))
    except Exception as exc:  # noqa: BLE001 — any failure means withhold, never a crash
        print(f"[route-proxy] image OCR failed ({type(exc).__name__}: {exc}); "
              "withholding the image (fail-closed)", file=sys.stderr, flush=True)
        return None


#: Content-part ``type`` values treated as image content. ``image_url`` is the
#: chat-completions shape; ``input_image`` is the Responses-API shape — matched
#: too so a client speaking that dialect cannot slip an image past the policy.
_IMAGE_PART_TYPES = frozenset({"image_url", "input_image"})

_WITHHELD_OCR_FAILED = (
    "[image withheld: local OCR failed; raw image content is never sent to the model]"
)
_WITHHELD_REMOTE = (
    "[remote image withheld (URL not relayed or fetched); raw image content is "
    "never sent to the model]"
)
_WITHHELD_MALFORMED = (
    "[image withheld: unrecognized image part shape; raw image content is never "
    "sent to the model]"
)


def strip_images(body: bytes) -> tuple[bytes, int]:
    """Replace image content parts in a chat body with local OCR text.

    Standing policy (2026-08-13): raw image content never leaves this machine —
    and the policy **fails closed**. A data-URL image whose OCR succeeds becomes
    its description; anything else that is recognizably an image part (OCR
    failure, oversize, remote URL, malformed shape) becomes an explicit
    ``[image withheld: ...]`` notice, never a raw forward. The notice is visible
    to the model, so the loss is announced rather than silent. (An earlier
    draft passed failures through raw; both the security and adversarial
    reviews flagged that as quietly falsifying the policy on every error path.)

    Args:
        body: Raw request body bytes.

    Returns:
        ``(new_body, replaced_count)``; the body is unchanged when count is 0.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, 0
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return body, 0

    replaced = 0
    for message in payload["messages"]:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        content = message["content"]
        for i, part in enumerate(content):
            if not (isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES):
                continue
            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if isinstance(url, str) and url.startswith("data:image"):
                description = _describe_data_url(url) or _WITHHELD_OCR_FAILED
            elif isinstance(url, str):
                description = _WITHHELD_REMOTE
            else:
                description = _WITHHELD_MALFORMED
            content[i] = {"type": "text", "text": description}
            replaced += 1

    if not replaced:
        return body, 0
    return json.dumps(payload).encode(), replaced


def inject_provider(body: bytes, provider: dict) -> tuple[bytes, dict | None]:
    """Return ``(new_body, applied_provider)`` for one request body.

    Merging is **key-level**: a caller-supplied ``provider`` block wins on the keys it
    actually sets, but this proxy's other keys are still added. A caller sending only
    ``{"ignore": [...]}`` therefore still receives this proxy's ``only`` and
    ``allow_fallbacks``. Bodies that are not a JSON object pass through untouched
    (``applied_provider`` is ``None``).

    Args:
        body: Raw request body bytes.
        provider: Routing block to merge in.

    Returns:
        The (possibly rewritten) body and the provider block actually applied.
    """
    if not provider:
        return body, None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, None
    if not isinstance(payload, dict):
        return body, None
    existing = payload.get("provider")
    payload["provider"] = (
        {**provider, **existing} if isinstance(existing, dict) else dict(provider)
    )
    return json.dumps(payload).encode(), payload["provider"]


def make_handler(upstream: str, provider: dict, verbose: bool,
                 intercept_images: bool = True):
    """Build the request handler class bound to this proxy's configuration.

    Args:
        upstream: Base URL to relay to.
        provider: Provider routing block to inject into chat-completion bodies.
        verbose: Whether to log each injection.
        intercept_images: Replace image content parts with local OCR text
            (the fail-closed standing policy). ``False`` only via
            ``--allow-raw-images``.

    Returns:
        A ``BaseHTTPRequestHandler`` subclass.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # silence per-request stderr noise
            pass

        def _relay(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

            if method == "POST" and self.path.endswith("/chat/completions") and body:
                if intercept_images:
                    body, n_images = strip_images(body)
                    if n_images and verbose:
                        print(f"[route-proxy] replaced {n_images} image part(s) "
                              "with local OCR", flush=True)
                body, applied = inject_provider(body, provider)
                if applied and verbose:
                    print(f"[route-proxy] injected {json.dumps(applied)}", flush=True)

            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
            headers["Content-Length"] = str(len(body))
            req = urllib.request.Request(
                upstream + self.path, data=body, headers=headers, method=method,
            )
            # Tracks whether response headers are already on the wire. Once streaming
            # has started we must NOT write a second status line + headers into the
            # open chunked body -- that produces malformed framing, and on a broken
            # pipe raises again out of the handler. Drop the connection instead.
            started = False
            try:
                with urllib.request.urlopen(req, timeout=900) as resp:
                    for started in self._stream_back(resp):
                        pass
            except urllib.error.HTTPError as exc:
                if not started:
                    self._send_bytes(exc.code, exc.read(),
                                     exc.headers.get("Content-Type", "application/json"))
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                # Transport failure -> explicit 502 (never a hang). Programming errors
                # are deliberately NOT caught here: they should surface, not be
                # mislabelled as an upstream failure.
                if started:
                    self.close_connection = True
                else:
                    payload = json.dumps(
                        {"error": {"message": f"route-proxy: {type(exc).__name__}: {exc}"}}
                    ).encode()
                    self._send_bytes(502, payload, "application/json")

        def _stream_back(self, resp):
            """Relay the response chunk-by-chunk as it arrives, yielding ``True`` once
            the response headers are on the wire.

            Uses ``read1`` deliberately: ``read(n)`` blocks until it has n bytes *or*
            EOF, which would buffer an entire streamed turn and defeat the point --
            SSE tokens would arrive in one burst at the end. ``read1`` returns whatever
            is already available, keeping the stream live.

            Yields:
                ``True`` after the status line and headers have been sent, so the
                caller knows a later failure can no longer be reported as a fresh
                HTTP response.
            """
            self.send_response(resp.status)
            for key, value in resp.headers.items():
                if key.lower() not in _HOP:
                    self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            yield True
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
                yield True
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            self._relay("POST")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                # image_interception is part of health, not decoration: the
                # 2026-08-13 adversarial review found the startup banner alone
                # is lost when the proxy is hand-started/backgrounded, and a
                # raw-passthrough proxy exactly reproduces the image-404 incident.
                payload = json.dumps({
                    "service": "goose-openrouter-route-proxy",
                    "provider": provider,
                    "image_interception": "ocr" if intercept_images else "raw-passthrough",
                }).encode()
                self._send_bytes(200, payload, "application/json")
                return
            self._relay("GET")

    return Handler


def main(argv: list[str] | None = None) -> int:
    """Run the routing proxy. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8791, help="local port to listen on")
    parser.add_argument("--only", default=",".join(DEFAULT_ONLY),
                        help="comma-separated backend allowlist (default: measured-clean set); "
                             "pass '' to disable")
    parser.add_argument("--ignore", default="",
                        help="comma-separated backend denylist (prefer --only)")
    parser.add_argument("--sort", default=None, choices=["price", "throughput", "latency"],
                        help="OpenRouter routing preference among permitted backends")
    parser.add_argument("--prefer", default="",
                        help="comma-separated backend(s) to try first (provider.order), "
                             "still restricted to --only and still failing over within it — "
                             "use this for 'route through X specifically', not --sort, which "
                             "re-picks whichever allowed backend is cheapest today")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM, help="upstream base URL")
    parser.add_argument("--quiet", action="store_true", help="do not log injections")
    parser.add_argument("--allow-raw-images", action="store_true",
                        help="relay image content parts verbatim instead of replacing "
                             "them with local OCR text — explicitly abandons the "
                             "raw-images-never-leave-this-machine policy for this run, "
                             "and image-bearing requests will 404 against text-only models")
    args = parser.parse_args(argv)

    only = [p.strip() for p in args.only.split(",") if p.strip()]
    ignore = [p.strip() for p in args.ignore.split(",") if p.strip()]
    prefer = [p.strip() for p in args.prefer.split(",") if p.strip()]
    extra = {"sort": args.sort} if args.sort else {}
    try:
        provider = build_provider_block(only, ignore, extra, prefer)
    except ValueError as exc:
        parser.error(str(exc))

    if not provider:
        print("route-proxy: no routing requested (--only and --ignore both empty); "
              "this would be a plain passthrough.", file=sys.stderr)
        return 2

    intercept_images = not args.allow_raw_images
    if intercept_images and not _ocr_available():
        # Fail closed (2026-08-13 adversarial review): a banner-only warning is
        # lost when the proxy is hand-started from the config.yaml comment and
        # backgrounded, and a silently raw-passthrough proxy exactly reproduces
        # the image-404 incident this feature exists to fix.
        print("route-proxy: pytesseract/PIL/cv2 are not importable in this "
              "interpreter, so the raw-images-never-leave-this-machine policy "
              "cannot be enforced. Run under ~/.config/goose/ocr-venv/bin/python3, "
              "or pass --allow-raw-images to explicitly run without interception.",
              file=sys.stderr)
        return 2

    print(f"[route-proxy] 127.0.0.1:{args.port} -> {args.upstream}")
    print(f"[route-proxy] injecting provider={json.dumps(provider)}")
    if intercept_images:
        print("[route-proxy] image interception ON: image parts are replaced with "
              "local OCR text, failing closed to a withheld-notice (raw images "
              "never leave this machine)")
    else:
        print("[route-proxy] WARNING: --allow-raw-images — image parts relay "
              "verbatim and will 404 against text-only models.", file=sys.stderr)
    if only == list(DEFAULT_ONLY):
        # A stale allowlist is the realistic failure mode here: backend rosters and
        # behavior change, and `only` + allow_fallbacks:false hard-fails (loudly, by
        # design) if every listed backend is delisted upstream.
        print(f"[route-proxy] NOTE: built-in allowlist, measured {MEASURED_ON} and not "
              "re-verified since. Check the roster is still current with: "
              "scripts/goose-openrouter-preflight.py --providers <model> "
              "(roster only -- it does not measure reliability).")
    print(f"[route-proxy] set OPENROUTER_HOST: http://127.0.0.1:{args.port} in config.yaml, "
          "then restart goose (VS Code: reload window)")
    handler = make_handler(args.upstream, provider, verbose=not args.quiet,
                           intercept_images=intercept_images)
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[route-proxy] stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
