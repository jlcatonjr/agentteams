"""Offline unit tests for scripts/goose-openrouter-route-proxy.py.

Network-free: only the pure body-rewriting and routing-block helpers are exercised
(no server is bound, no upstream is contacted). Mirrors the importlib loader pattern
used by test_goose_openrouter_preflight.py, since scripts/ is excluded from the
packaged distribution and cannot be imported normally.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "goose-openrouter-route-proxy.py"


def _load():
    spec = importlib.util.spec_from_file_location("goose_route_proxy", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not bind a socket or perform network I/O
    return module


grp = _load()


# --- build_provider_block ----------------------------------------------------

def test_allowlist_disables_fallbacks():
    # An allowlist that silently falls back to an excluded backend is not an allowlist.
    block = grp.build_provider_block(["Alibaba", "Morph"], [], {})
    assert block["only"] == ["Alibaba", "Morph"]
    assert block["allow_fallbacks"] is False


def test_denylist_does_not_force_allow_fallbacks_false():
    # A denylist intentionally keeps the rest of the routing pool available.
    block = grp.build_provider_block([], ["Chutes"], {})
    assert block["ignore"] == ["Chutes"]
    assert "allow_fallbacks" not in block


def test_extra_fields_merge():
    block = grp.build_provider_block(["Alibaba"], [], {"sort": "throughput"})
    assert block["sort"] == "throughput"
    assert block["only"] == ["Alibaba"]


def test_empty_block_when_no_routing_requested():
    assert grp.build_provider_block([], [], {}) == {}


def test_prefer_sets_order_and_composes_with_only():
    # 2026-08-10: "route through X" needs provider.order, not --sort, which re-picks
    # whichever allowed backend is cheapest today and silently drifts as prices move.
    #
    # order must list every `only` backend, not just `prefer`: OpenRouter treats `order`
    # + allow_fallbacks:false as the ENTIRE candidate set (live-verified), so an order of
    # just ["CoreWeave"] silently drops Z.AI/Alibaba as fallback candidates -- a hard pin
    # on CoreWeave alone, not a preference. That caused a same-day outage: CoreWeave has
    # no endpoint for qwen/qwen3.8-max, so every request 404'd instead of falling over to
    # Alibaba.
    block = grp.build_provider_block(["Z.AI", "Alibaba", "CoreWeave"], [], {},
                                     prefer=["CoreWeave"])
    assert block["order"] == ["CoreWeave", "Z.AI", "Alibaba"]
    assert block["only"] == ["Z.AI", "Alibaba", "CoreWeave"]
    assert block["allow_fallbacks"] is False  # still hard-restricted to the allowlist


def test_prefer_alone_sets_order_without_restricting():
    # Live-verified: bare `order` with no `only` falls back past an unreachable entry
    # on its own (OpenRouter defaults allow_fallbacks to true there), so passing
    # `prefer` through unchanged is safe -- unlike the `only`-present case above.
    block = grp.build_provider_block([], [], {}, prefer=["CoreWeave"])
    assert block == {"order": ["CoreWeave"]}


def test_no_prefer_omits_order():
    assert "order" not in grp.build_provider_block(["Alibaba"], [], {})


def test_prefer_outside_only_is_refused():
    # A `--prefer` backend absent from `--only` would otherwise ride into `order` and,
    # since `only` + allow_fallbacks:false makes `order` the entire candidate set, could
    # let an unvetted backend serve requests -- silently defeating the allowlist.
    with pytest.raises(ValueError, match="not in --only allowlist"):
        grp.build_provider_block(["Alibaba"], [], {}, prefer=["CoreWeave"])


def test_prefer_duplicates_are_deduped():
    block = grp.build_provider_block(["Z.AI", "Alibaba", "CoreWeave"], [], {},
                                     prefer=["CoreWeave", "CoreWeave"])
    assert block["order"] == ["CoreWeave", "Z.AI", "Alibaba"]


def test_default_allowlist_is_the_measured_clean_set():
    # Guards against silently drifting the shipped default away from what was measured
    # (2026-07-24: these three were 12/12 clean; Chutes/Phala/SiliconFlow leaked).
    assert set(grp.DEFAULT_ONLY) == {"Alibaba", "CoreWeave", "Morph"}


# --- strip_images (image interception, 2026-08-13) ---------------------------
#
# Policy under test: raw image content never leaves the machine — image_url parts
# are replaced with local OCR text before relay. These tests are offline: real OCR
# needs the ~/.config/goose/ocr-venv interpreter (pytesseract/PIL/cv2), so the
# describe step is monkeypatched here and only the pure rewriting logic is pinned.
# The one test that exercises real OCR skips when the deps are absent, matching
# the repo's existing environment-dependent-skip pattern.

def _image_body(url, extra_part=None):
    parts = [{"type": "text", "text": "what does this show?"},
             {"type": "image_url", "image_url": {"url": url}}]
    if extra_part:
        parts.append(extra_part)
    return json.dumps({"model": "m", "messages": [
        {"role": "user", "content": parts},
    ]}).encode()


_TINY_PNG_B64 = (  # 1x1 white pixel, a valid PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//8/AwAI/AL+"
    "hc2rNAAAAABJRU5ErkJggg=="
)


def test_strip_images_replaces_data_url_with_description(monkeypatch):
    monkeypatch.setattr(grp, "_describe_data_url", lambda url: "OCR SAYS HELLO")
    body, n = grp.strip_images(_image_body("data:image/png;base64," + _TINY_PNG_B64))
    assert n == 1
    payload = json.loads(body)
    parts = payload["messages"][0]["content"]
    assert parts[1] == {"type": "text", "text": "OCR SAYS HELLO"}
    assert parts[0]["type"] == "text"  # sibling part untouched
    assert not any(p.get("type") == "image_url" for p in parts)


def test_strip_images_fails_closed_on_describe_failure(monkeypatch):
    # OCR failure must NOT forward the raw image (2026-08-13 security +
    # adversarial reviews: pass-through quietly falsified the never-send-raw-
    # images policy on every error path). The withheld notice keeps the loss
    # visible to the model instead of silent.
    monkeypatch.setattr(grp, "_describe_data_url", lambda url: None)
    body, n = grp.strip_images(_image_body("data:image/png;base64," + _TINY_PNG_B64))
    assert n == 1
    part = json.loads(body)["messages"][0]["content"][1]
    assert part["type"] == "text"
    assert "image withheld" in part["text"]
    assert _TINY_PNG_B64 not in body.decode()


def test_strip_images_withholds_remote_urls(monkeypatch):
    # A remote URL is image content by reference — relaying it hands the
    # upstream something to fetch. Fail closed: withhold, never relay.
    called = []
    monkeypatch.setattr(grp, "_describe_data_url", lambda url: called.append(url))
    body, n = grp.strip_images(_image_body("https://example.com/x.png"))
    assert n == 1 and called == []
    part = json.loads(body)["messages"][0]["content"][1]
    assert part["type"] == "text" and "withheld" in part["text"]
    assert "example.com" not in body.decode()


def test_strip_images_matches_input_image_parts(monkeypatch):
    # Responses-API dialect: {"type": "input_image", "image_url": "<data-url>"} —
    # a client speaking this shape must not slip an image past the policy.
    monkeypatch.setattr(grp, "_describe_data_url", lambda url: "DESC")
    raw = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64," + _TINY_PNG_B64},
    ]}]}).encode()
    body, n = grp.strip_images(raw)
    assert n == 1
    assert json.loads(body)["messages"][0]["content"][0] == {"type": "text", "text": "DESC"}


def test_strip_images_handles_string_form_image_url(monkeypatch):
    # Some clients send image_url as a bare string, not {"url": ...}.
    monkeypatch.setattr(grp, "_describe_data_url", lambda url: "DESC")
    raw = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": "data:image/png;base64," + _TINY_PNG_B64},
    ]}]}).encode()
    body, n = grp.strip_images(raw)
    assert n == 1
    assert json.loads(body)["messages"][0]["content"][0]["type"] == "text"


@pytest.mark.parametrize("raw", [
    b"not json",
    b"[1,2,3]",
    b'{"messages": "not a list"}',
    b'{"messages": [{"role": "user", "content": "plain string"}]}',
    b'{"no_messages": true}',
])
def test_strip_images_passes_through_non_image_bodies(raw, monkeypatch):
    monkeypatch.setattr(grp, "_describe_data_url", lambda url: "DESC")
    body, n = grp.strip_images(raw)
    assert body == raw and n == 0


def test_describe_data_url_refuses_oversized_payload(monkeypatch):
    # Decompression-bomb guard: refuse before decoding, not after.
    big = "data:image/png;base64," + "A" * (grp.MAX_IMAGE_B64_BYTES + 1)
    assert grp._describe_data_url(big) is None


def test_describe_data_url_rejects_invalid_base64(capsys):
    assert grp._describe_data_url("data:image/png;base64,!!!not-base64!!!") is None
    assert "image OCR failed" in capsys.readouterr().err


@pytest.mark.skipif(not grp._ocr_available(), reason="pytesseract/PIL/cv2 not installed "
                    "(the live proxy runs under ~/.config/goose/ocr-venv, which has them)")
def test_describe_image_bytes_real_ocr_end_to_end():
    # Renders text into a PNG and reads it back through the real OCR path,
    # certifying the venv contract (tesseract binary + PIL + cv2 all working).
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 80), "white")
    ImageDraw.Draw(img).text((10, 25), "ROUTE PROXY OCR TEST 99", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = grp.describe_image_bytes(buf.getvalue())
    # PIL's default bitmap font is tiny and tesseract garbles some glyphs
    # (measured: "TEST 99" -> "TESTSS"), so assert on a reliably-read word,
    # not exact transcription — the test certifies the pipeline, not accuracy.
    assert "PROXY" in out                 # OCR reached the rendered text
    assert "Dominant colors:" in out      # cv2 color pass ran
    assert "never sent to the model" in out


# --- inject_provider ---------------------------------------------------------

def test_inject_adds_provider_to_json_body():
    body, applied = grp.inject_provider(b'{"model":"m","stream":true}', {"only": ["Alibaba"]})
    payload = json.loads(body)
    assert payload["provider"] == {"only": ["Alibaba"]}
    assert payload["model"] == "m" and payload["stream"] is True  # untouched
    assert applied == {"only": ["Alibaba"]}


def test_caller_supplied_provider_wins_on_conflict():
    # Never override explicit routing a caller already chose.
    body, applied = grp.inject_provider(
        b'{"provider":{"only":["Morph"]}}', {"only": ["Alibaba"], "sort": "price"},
    )
    payload = json.loads(body)
    assert payload["provider"]["only"] == ["Morph"]   # caller's value survives
    assert payload["provider"]["sort"] == "price"     # non-conflicting key still merged
    assert applied["only"] == ["Morph"]


@pytest.mark.parametrize("raw", [b"not json", b"[1,2,3]", b'"a string"', b""])
def test_non_object_bodies_pass_through_untouched(raw):
    body, applied = grp.inject_provider(raw, {"only": ["Alibaba"]})
    assert body == raw
    assert applied is None


def test_empty_provider_block_is_a_noop():
    raw = b'{"model":"m"}'
    body, applied = grp.inject_provider(raw, {})
    assert body == raw and applied is None


# --- hop-by-hop header hygiene ----------------------------------------------

def test_hop_by_hop_headers_are_not_forwarded():
    # Relaying these breaks framing (RFC 7230 6.1); accept-encoding is dropped so we
    # never relay a compressed body without matching header semantics.
    for h in ("connection", "transfer-encoding", "host", "content-length", "accept-encoding"):
        assert h in grp._HOP


def test_authorization_is_not_hop_by_hop():
    # The caller's key must reach the upstream; it is relayed, never logged.
    assert "authorization" not in grp._HOP


# --- transport: streaming + header relay (offline, fake response) ------------

class _FakeHeaders:
    def __init__(self, pairs):
        self._pairs = pairs

    def items(self):
        return list(self._pairs)


class _FakeResp:
    """Minimal urllib-response stand-in that hands back one chunk per read1()."""

    def __init__(self, chunks, status=200, headers=()):
        self._chunks = list(chunks)
        self.status = status
        self.headers = _FakeHeaders(headers)
        self.read1_calls = 0
        self.read_calls = 0

    def read1(self, _n):
        self.read1_calls += 1
        return self._chunks.pop(0) if self._chunks else b""

    def read(self, _n):  # must NOT be used: read() blocks until n bytes or EOF
        self.read_calls += 1
        return b"".join(self._chunks) if self._chunks else b""


class _Recorder:
    """Captures what the handler writes to the client."""

    def __init__(self):
        self.buf = bytearray()
        self.flushes = 0

    def write(self, b):
        self.buf.extend(b)

    def flush(self):
        self.flushes += 1


def _handler_for(provider=None):
    """Build a handler instance for offline use, without binding a socket."""
    cls = grp.make_handler("https://upstream.invalid", provider or {"only": ["Alibaba"]}, False)
    h = cls.__new__(cls)  # bypass BaseHTTPRequestHandler.__init__ (which would do I/O)
    h.wfile = _Recorder()
    h.sent = []
    h.headers_out = []
    h.send_response = lambda code: h.sent.append(code)
    h.send_header = lambda k, v: h.headers_out.append((k, v))
    h.end_headers = lambda: None
    return h


def test_stream_back_uses_read1_not_read():
    # Regression guard for the real defect: read(8192) blocks until 8192 bytes OR EOF,
    # which buffers an entire streamed turn and delivers SSE in one burst at the end.
    resp = _FakeResp([b"a", b"b", b"c"])
    h = _handler_for()
    list(h._stream_back(resp))
    assert resp.read1_calls > 0
    assert resp.read_calls == 0, "must not use blocking read() on a streamed body"


def test_stream_back_emits_each_chunk_separately_and_terminates():
    resp = _FakeResp([b"one", b"two"])
    h = _handler_for()
    list(h._stream_back(resp))
    body = bytes(h.wfile.buf)
    # chunked framing: <hex-len>\r\n<data>\r\n per chunk, then the 0-length terminator
    assert b"3\r\none\r\n" in body
    assert b"3\r\ntwo\r\n" in body
    assert body.endswith(b"0\r\n\r\n")


def test_stream_back_flushes_per_chunk_so_output_is_not_buffered():
    resp = _FakeResp([b"x", b"y", b"z"])
    h = _handler_for()
    list(h._stream_back(resp))
    assert h.wfile.flushes >= 3  # one per chunk, else the client sees nothing until EOF


def test_stream_back_signals_started_before_any_body_is_written():
    # The caller relies on this to know a later failure can no longer be reported
    # as a fresh HTTP response (otherwise it corrupts the open chunked body).
    resp = _FakeResp([b"data"])
    h = _handler_for()
    gen = h._stream_back(resp)
    first = next(gen)
    assert first is True
    assert bytes(h.wfile.buf) == b"", "headers must be sent before any body bytes"


def test_stream_back_drops_hop_by_hop_headers_from_upstream():
    resp = _FakeResp([b"x"], headers=[("Content-Type", "text/event-stream"),
                                      ("Transfer-Encoding", "chunked"),
                                      ("Connection", "keep-alive")])
    h = _handler_for()
    list(h._stream_back(resp))
    names = {k.lower() for k, _ in h.headers_out}
    assert "content-type" in names
    # relaying upstream's transfer-encoding on top of ours would double-encode
    assert "connection" not in names
    assert sum(1 for k, _ in h.headers_out if k.lower() == "transfer-encoding") == 1


def test_relay_does_not_write_a_second_response_after_streaming_started(monkeypatch):
    # S1 regression guard: once headers + body bytes are on the wire, a later upstream
    # failure must NOT emit a fresh status line + headers into the open chunked body
    # (malformed framing, and on a broken pipe it raises out of the handler). It must
    # drop the connection instead.
    import urllib.request as _u

    class _DyingResp(_FakeResp):
        def read1(self, _n):
            self.read1_calls += 1
            if self.read1_calls == 1:
                return b"partial"
            raise OSError("upstream vanished mid-stream")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    h = _handler_for()
    h.headers = {"Content-Length": "0"}
    h.rfile = None
    h.path = "/api/v1/chat/completions"
    h.close_connection = False
    sent_after = []
    h._send_bytes = lambda *a: sent_after.append(a)
    monkeypatch.setattr(_u, "urlopen", lambda *a, **k: _DyingResp([b"x"]))

    h._relay("GET")  # GET so no body is read from rfile

    assert sent_after == [], "must not send a second HTTP response mid-stream"
    assert h.close_connection is True
    assert b"partial" in bytes(h.wfile.buf)  # the bytes already streamed are kept


def test_relay_sends_502_when_upstream_fails_before_streaming(monkeypatch):
    # Contrast with the above: pre-stream failures SHOULD produce a clean 502.
    import urllib.request as _u

    def _boom(*a, **k):
        raise OSError("connection refused")

    h = _handler_for()
    h.headers = {"Content-Length": "0"}
    h.rfile = None
    h.path = "/api/v1/chat/completions"
    h.close_connection = False
    sent = []
    h._send_bytes = lambda status, payload, ctype: sent.append((status, payload))
    monkeypatch.setattr(_u, "urlopen", _boom)

    h._relay("GET")

    assert len(sent) == 1 and sent[0][0] == 502
    assert b"connection refused" in sent[0][1]


# --- local health endpoint --------------------------------------------------

def test_healthz_identifies_proxy_without_upstream_relay():
    h = _handler_for({"only": ["Alibaba"], "allow_fallbacks": False})
    h.path = "/healthz"
    relayed = []
    sent = []
    h._relay = lambda method: relayed.append(method)
    h._send_bytes = lambda status, payload, content_type: sent.append(
        (status, json.loads(payload), content_type)
    )

    h.do_GET()

    assert relayed == []
    assert sent == [(200, {
        "service": "goose-openrouter-route-proxy",
        "provider": {"only": ["Alibaba"], "allow_fallbacks": False},
        # Health must expose interception mode: the startup banner is lost when
        # the proxy is hand-started and backgrounded, and a raw-passthrough
        # proxy exactly reproduces the image-404 incident.
        "image_interception": "ocr",
    }, "application/json")]


# --- main() argument handling ------------------------------------------------

def test_main_refuses_when_no_routing_requested(capsys):
    # A pure passthrough would silently provide none of the protection the user
    # believes they configured -- refuse loudly instead.
    assert grp.main(["--only", "", "--ignore", ""]) == 2
    assert "no routing requested" in capsys.readouterr().err


def test_main_refuses_prefer_outside_only(capsys):
    with pytest.raises(SystemExit) as excinfo:
        grp.main(["--only", "Alibaba", "--prefer", "CoreWeave"])
    assert excinfo.value.code == 2
    assert "not in --only allowlist" in capsys.readouterr().err


class _FakeServer:
    def __init__(self, *_a, **_k): pass
    def serve_forever(self): raise SystemExit(0)


def _capture_handler_call(monkeypatch, captured):
    def _fake_handler(upstream, provider, verbose, intercept_images=True):
        del upstream, verbose  # matches make_handler's keyword-arg call site exactly
        captured["provider"] = provider
        captured["intercept_images"] = intercept_images
        return object

    monkeypatch.setattr(grp, "make_handler", _fake_handler)
    monkeypatch.setattr(grp, "ThreadingHTTPServer", _FakeServer)


def test_prefer_flag_reaches_the_provider_block(monkeypatch):
    # End-to-end through argument parsing, not just the helper -- pins the standing job's
    # actual invocation shape (`--only "Z.AI,Alibaba,CoreWeave" --prefer "CoreWeave"`).
    captured = {}
    _capture_handler_call(monkeypatch, captured)
    # This test's subject is routing, not OCR; don't let the host env decide it.
    monkeypatch.setattr(grp, "_ocr_available", lambda: True)
    with pytest.raises(SystemExit):
        grp.main(["--only", "Z.AI,Alibaba,CoreWeave", "--prefer", "CoreWeave"])
    assert captured["provider"]["order"] == ["CoreWeave", "Z.AI", "Alibaba"]
    assert captured["provider"]["only"] == ["Z.AI", "Alibaba", "CoreWeave"]
    assert captured["intercept_images"] is True


def test_main_refuses_to_start_without_ocr_deps(monkeypatch, capsys):
    # Fail closed: a banner-only warning is lost when the proxy is hand-started
    # and backgrounded; running raw-passthrough silently reproduces the
    # image-404 incident the interception exists to fix.
    monkeypatch.setattr(grp, "_ocr_available", lambda: False)
    assert grp.main(["--only", "Alibaba"]) == 2
    err = capsys.readouterr().err
    assert "ocr-venv" in err and "--allow-raw-images" in err


def test_main_allow_raw_images_is_the_explicit_escape_hatch(monkeypatch):
    captured = {}
    _capture_handler_call(monkeypatch, captured)
    monkeypatch.setattr(grp, "_ocr_available", lambda: False)
    with pytest.raises(SystemExit):
        grp.main(["--only", "Alibaba", "--allow-raw-images"])
    assert captured["intercept_images"] is False
