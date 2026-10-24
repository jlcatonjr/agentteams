"""agentteams.vulnscan.gateway_dlp: block secrets, redact and flag the rest (D3), on every dialect (W2).

Every credential-shaped string here is assembled at runtime from fragments and a deterministic
pseudo-random body, so no scanner mistakes this file for one holding real keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import string
import time
from typing import Any

import pytest

from agentteams.vulnscan import agent_upstreams
from agentteams.vulnscan import gateway_dialects as d
from agentteams.vulnscan import gateway_dlp as dlp

MODEL = "anthropic/model-a"
ALNUM = string.ascii_letters + string.digits


def fake(n: int, step: int = 7, alphabet: str = ALNUM) -> str:
    """A deterministic, random-looking body of ``n`` characters (mixed case and digits)."""
    return "".join(alphabet[(i * step + i // 5) % len(alphabet)] for i in range(n))


HEX = "0123456789abcdef"
#: One fake credential per block class (built at runtime).
SECRETS: dict[str, str] = {
    "private-key": "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----\n" + fake(40) + "\n",
    "aws-access-key": "AK" + "IA" + "Q7XK2M4P9RTZ3WVB",
    "aws-secret-key": "aws_secret" + "_access_key = " + fake(40, 11),
    "github-token": "gh" + "p_" + fake(36),
    "gitlab-token": "gl" + "pat-" + fake(20),
    "anthropic-key": "sk-" + "ant-" + "api03-" + fake(80),
    "openrouter-key": "sk-" + "or-v1-" + fake(64, 5, HEX),
    "openai-key": "sk-" + "proj-" + fake(48),
    "slack-token": "xo" + "xb-" + "1234567890-" + fake(24),
    "slack-webhook": "https://hooks." + "slack.com/services/" + "T0ABCDEF1/B0ABCDEF2/" + fake(24),
    "google-api-key": "AI" + "za" + fake(35),
    "stripe-key": "sk" + "_live_" + fake(24),
    "npm-token": "np" + "m_" + fake(36),
    "pypi-token": "py" + "pi-AgE" + fake(60),
    "jwt": "ey" + "J" + fake(20) + ".ey" + "J" + fake(30, 3) + "." + fake(43, 13),
    "bearer-token": "Authorization: " + "Bearer " + fake(40, 9),
    "url-credentials": "postgres://admin:" + fake(16, 11) + "@db.internal.example/app",
    "secret-assignment": "db_password = '" + fake(20, 5) + "'",
    "high-entropy-assignment": "SERVICE_THING=" + fake(40, 17),
}


def _body(dialect: str, text: str) -> dict[str, Any]:
    if dialect == d.CHAT:
        return {"model": MODEL, "messages": [{"role": "user", "content": text}]}
    if dialect == d.MESSAGES:
        return {"model": MODEL, "max_tokens": 100, "messages": [{"role": "user",
                                                                 "content": [{"type": "text", "text": text}]}]}
    return {"model": MODEL, "max_output_tokens": 100,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}


def _check(dialect: str, body: dict[str, Any], f: dlp.Filter | None = None) -> d.Checked:
    f = f or dlp.Filter()
    return d.check_request(dialect, json.dumps(body).encode(), models={MODEL}, dlp=f.apply)


# ---------------------------------------------------------------- block classes, every dialect

@pytest.mark.parametrize("dialect", d.DIALECTS)
@pytest.mark.parametrize("cls", sorted(SECRETS))
def test_each_block_class_is_refused_on_each_dialect(cls: str, dialect: str) -> None:
    secret = SECRETS[cls]
    with pytest.raises(dlp.DlpBlocked) as exc:
        _check(dialect, _body(dialect, f"here is the file:\n{secret}\nplease summarise"))
    assert exc.value.cls == cls
    assert str(exc.value).startswith(f"dlp: {cls} ")
    assert isinstance(exc.value, d.PolicyError)
    for part in (secret, secret[-16:]):            # the matched text is never echoed
        assert part not in str(exc.value)


def test_known_secret_exact_match_beats_every_format() -> None:
    value = "fixture-" + fake(10)
    f = dlp.Filter(secrets=[value, "short"])        # values under MIN_EXACT are ignored
    assert f.secrets == (value,)
    with pytest.raises(dlp.DlpBlocked) as exc:
        _check(d.MESSAGES, _body(d.MESSAGES, f"x{value}y"), f)
    assert exc.value.cls == "known-secret"
    assert _check(d.MESSAGES, _body(d.MESSAGES, "short is fine"), f)


SECRET = SECRETS["github-token"]


@pytest.mark.parametrize(("dialect", "body"), [
    (d.CHAT, {"messages": [{"role": "system", "content": [{"type": "text", "text": SECRET}]}]}),
    (d.CHAT, {"messages": [{"role": "tool", "tool_call_id": "c1", "content": SECRET}]}),
    (d.CHAT, {"messages": [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": json.dumps({"q": SECRET})}}]}]}),
    (d.CHAT, {"messages": [{"role": "user", "content": "x"}],
              "tools": [{"type": "function", "function": {"name": "f", "description": SECRET}}]}),
    (d.MESSAGES, {"max_tokens": 9, "system": [{"type": "text", "text": SECRET}],
                  "messages": [{"role": "user", "content": "x"}]}),
    (d.MESSAGES, {"max_tokens": 9, "system": SECRET, "messages": [{"role": "user", "content": "x"}]}),
    (d.MESSAGES, {"max_tokens": 9, "messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": SECRET}]}]}]}),
    (d.MESSAGES, {"max_tokens": 9, "messages": [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "f", "input": {"nested": {SECRET: 1}}}]}]}),
    (d.MESSAGES, {"max_tokens": 9, "messages": [{"role": "user", "content": [
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": SECRET}}]}]}),
    (d.MESSAGES, {"max_tokens": 9, "metadata": {"user_id": SECRET}, "messages": [{"role": "user", "content": "x"}]}),
    (d.RESPONSES, {"max_output_tokens": 9, "instructions": SECRET, "input": "x"}),
    (d.RESPONSES, {"max_output_tokens": 9, "input": SECRET}),
    (d.RESPONSES, {"max_output_tokens": 9, "input": [{"type": "function_call_output", "call_id": "c", "output": SECRET}]}),
    (d.RESPONSES, {"max_output_tokens": 9, "input": [
        {"type": "function_call", "call_id": "c", "name": "f", "arguments": json.dumps({"a": SECRET})}]}),
])
def test_a_secret_is_found_wherever_the_request_carries_it(dialect: str, body: dict[str, Any]) -> None:
    with pytest.raises(dlp.DlpBlocked):
        _check(dialect, {"model": MODEL, **body})


# ---------------------------------------------------------------- false-positive guards

def _b64(n: int) -> str:
    return base64.b64encode(hashlib.sha512(str(n).encode()).digest() * 400).decode()


NOT_SECRETS = [
    hashlib.sha256(b"x").hexdigest(),
    "pinned sha256:" + hashlib.sha256(b"y").hexdigest(),
    "commit " + hashlib.sha1(b"z").hexdigest() + " merged",
    '"integrity": "sha512-' + base64.b64encode(hashlib.sha512(b"q").digest()).decode() + '"',
    "github.com/x/y v1.2.3 h1:" + base64.b64encode(hashlib.sha256(b"r").digest()).decode(),
    "--hash=sha256:" + hashlib.sha256(b"s").hexdigest(),
    "![img](data:image/png;base64," + _b64(1) + ")",
    "CVE-2024-12345 GHSA-abcd-efgh-ijkl GHSA-2qrg-x229-3v8q PYSEC-2023-117 lodash@4.17.21 requests==2.31.0",
    "id 123e4567-e89b-12d3-a456-426614174000 version 10.2.3 node v20.11.1",
    "max_tokens=4096 tokens_total: 120000 tokenizer=gpt2-tokenizer-v1.2",
    "token: process.env.GH_TOKEN", "password: ${{ secrets.NPM_PASSWORD }}", "api_key: <your-api-key>",
    "npm_config_cache=/tmp/x", "OLDPWD=/srv/proj2/build7",
    "SHA256SUM=" + hashlib.sha256(b"a").hexdigest(), "GIT_COMMIT=" + hashlib.sha1(b"a").hexdigest(),
    "IMAGE_DIGEST=sha256:" + hashlib.sha256(b"b").hexdigest(),
    "postgres://postgres:postgres@localhost/db", "git@github.com:owner/repo.git",
    "sk-gateway-key", "task-something-long-name-here-for-real", "the risk-assessment-for-the-model is fine",
    "Bearer $TOKEN", "Authorization: Bearer <token>", "eyJhbGciOiJIUzI1NiJ9 alone is a header, not a token",
    "AKIAEXAMPLE is too short", "ghp_short",
]


@pytest.mark.parametrize("dialect", d.DIALECTS)
def test_vulnscan_shaped_text_is_not_blocked(dialect: str) -> None:
    for text in NOT_SECRETS:
        checked = _check(dialect, _body(dialect, text))
        assert checked.redactions == {}, text


def test_base64_media_and_provider_blobs_pass_unchanged() -> None:
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(2)}}
    thinking = {"type": "thinking", "thinking": "plan: see /Users/user/x", "signature": _b64(3)[:400]}
    body = {"model": MODEL, "max_tokens": 9, "messages": [{"role": "user", "content": [image]},
                                                          {"role": "assistant", "content": [thinking]}]}
    out = json.loads(_check(d.MESSAGES, json.loads(json.dumps(body))).body)
    assert out["messages"] == body["messages"]      # thinking is signed: never redacted
    chat = {"model": MODEL, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(4)}}]}]}
    assert json.loads(_check(d.CHAT, json.loads(json.dumps(chat))).body)["messages"] == chat["messages"]


# ---------------------------------------------------------------- redact and flag

FILTER_KW: dict[str, Any] = {"hostnames": ["build-box-7"], "private_names": ["acme-*", "@corp/*"]}
PLAIN = ("checkout /Users/user/src/app and /home/op/work; mail ops.team@corp.example.org; "
         "host build-box-7.lan; ip 10.1.2.3 and 192.168.0.10:8080 and fd12:3456:789a::1; "
         "packages acme-internal@1.2.0, @corp/lib and node_modules/acme-util/index.js; public lodash")
EXPECTED = ("checkout ~/src/app and ~/work; mail [REDACTED:email]; host [REDACTED:hostname].lan; "
            "ip [REDACTED:private-ip] and [REDACTED:private-ip]:8080 and [REDACTED:private-ip]; packages "
            "[REDACTED:private-package], [REDACTED:private-package] and node_modules/[REDACTED:private-package]"
            "/index.js; public lodash")
COUNTS = {"home-path": 2, "email": 1, "hostname": 1, "private-ip": 3, "private-package": 3}


def _texts(dialect: str, body: dict[str, Any]) -> str:
    if dialect == d.CHAT:
        return body["messages"][0]["content"]
    if dialect == d.MESSAGES:
        return body["messages"][0]["content"][0]["text"]
    return body["input"][0]["content"][0]["text"]


@pytest.mark.parametrize("dialect", d.DIALECTS)
def test_lesser_classes_are_redacted_and_counted_on_each_dialect(dialect: str) -> None:
    checked = _check(dialect, _body(dialect, PLAIN), dlp.Filter(**FILTER_KW))
    assert _texts(dialect, json.loads(checked.body)) == EXPECTED
    assert dict(checked.redactions) == COUNTS


def test_redaction_keeps_json_and_tool_call_structure() -> None:
    args = json.dumps({"path": "/Users/user/repo", "who": "ops@corp.example.org"})
    tool_output = json.dumps({"files": ["/Users/user/repo/a.py"], "owner": "dev@corp.example.org",
                              "quote": 'a "quoted" \\ value'})
    body = {"model": MODEL, "messages": [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "vulnscan_read", "arguments": args}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": tool_output}]}
    out = json.loads(_check(d.CHAT, json.loads(json.dumps(body)), dlp.Filter()).body)
    assert out["messages"][1] == body["messages"][1]            # tool-call ids, names and arguments untouched
    inner = json.loads(out["messages"][2]["content"])             # still valid JSON after redaction
    assert inner == {"files": ["~/repo/a.py"], "owner": "[REDACTED:email]", "quote": 'a "quoted" \\ value'}
    assert out["messages"][2]["tool_call_id"] == "call_1"
    msgs = {"model": MODEL, "max_tokens": 9, "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "f",
                                           "input": {"path": "/Users/user/r"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                      "content": [{"type": "text", "text": "/Users/user/r ok"}]}]}]}
    out = json.loads(_check(d.MESSAGES, json.loads(json.dumps(msgs)), dlp.Filter()).body)
    assert out["messages"][0] == msgs["messages"][0]
    assert out["messages"][1]["content"][0]["content"][0]["text"] == "~/r ok"
    resp = {"model": MODEL, "max_output_tokens": 9, "instructions": "you run on 10.0.0.5", "input": [
        {"type": "function_call", "call_id": "c1", "name": "f", "arguments": args},
        {"type": "function_call_output", "call_id": "c1", "output": "owner ops@corp.example.org"},
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}]}
    out = json.loads(_check(d.RESPONSES, json.loads(json.dumps(resp)), dlp.Filter()).body)
    assert out["instructions"] == "you run on [REDACTED:private-ip]"
    assert out["input"][0] == resp["input"][0] and out["input"][2] == resp["input"][2]
    assert out["input"][1]["output"] == "owner [REDACTED:email]"


def test_redaction_guards() -> None:
    f = dlp.Filter(**FILTER_KW)
    kept = ("git@github.com:o/r.git v10.1.2.3 ==10.0.0.1 999.168.1.1 192.168.1.300 /Users/" "Shared/x "
            "acmeish lodash@4.17.21 user@1.2.3 172.32.0.1 11.0.0.1")
    checked = _check(d.CHAT, _body(d.CHAT, kept), f)
    assert json.loads(checked.body)["messages"][0]["content"] == kept and checked.redactions == {}
    off = dlp.Filter(redact=False)
    assert _check(d.CHAT, _body(d.CHAT, PLAIN), off).redactions == {}


def test_a_repeated_text_is_counted_once() -> None:
    f = dlp.Filter(**FILTER_KW)
    assert dict(_check(d.CHAT, _body(d.CHAT, PLAIN), f).redactions) == COUNTS
    again = _check(d.CHAT, _body(d.CHAT, PLAIN), f)                # the history is resent each turn
    assert again.redactions == {} and json.loads(again.body)["messages"][0]["content"] == EXPECTED


# ---------------------------------------------------------------- secrets files, sources

def test_secret_values_from_env_files() -> None:
    key = "sk-" + "or-v1-" + fake(64, 5, HEX)
    text = "\n".join([
        "# comment " + fake(30),
        f"export OPENROUTER_API_KEY='{key}'",
        "SOME_PASSWORD=hunter22hunter",          # secret-looking name
        "MISC=" + fake(24, 3),                    # random-looking value
        "MODEL=anthropic/claude-model-x",         # neither: never blocked (it is in every request)
        "UPSTREAM_URL=https://openrouter.ai/api/v1",
        "SHORT_TOKEN=abc",
        fake(32, 11),                             # a bare key line
    ])
    values = dlp.secret_values(text)
    assert values == {key, "hunter22hunter", fake(24, 3), fake(32, 11)}


def test_secret_files_and_dir(tmp_path: Any) -> None:
    (tmp_path / "a.env").write_text("X_TOKEN=" + fake(20) + "\n")
    (tmp_path / "notes.txt").write_text("Y_TOKEN=" + fake(20, 3) + "\n")
    files = dlp.secrets_dir_files(tmp_path)
    assert [p.name for p in files] == ["a.env"]
    values, bad = dlp.load_secret_files([*files, tmp_path / "missing.env"])
    assert values == {fake(20)} and bad == 1
    assert dlp.secrets_dir_files(tmp_path / "absent") == []
    assert dlp.SECRETS_DIR == agent_upstreams.SECRETS_DIR


def test_private_names_file(tmp_path: Any) -> None:
    p = tmp_path / "names.txt"
    p.write_text("# private\nacme-*\n\n@corp/*\n")
    assert dlp.read_private_names(p) == ["acme-*", "@corp/*"]


# ---------------------------------------------------------------- performance (linear time)

@pytest.mark.parametrize("evil", [
    "a" * 400_000 + "=", "A_" * 200_000 + "=", "x://" * 100_000, "@" * 200_000 + "a",
    "password=" * 50_000, "aws" * 100_000, "eyJ" * 100_000 + ".", "1.1.1." * 80_000, "fd00:" * 80_000,
    "-----BEGIN " * 40_000, "a." * 200_000 + "@", "/Users/" * 60_000, "Bearer " * 60_000,
])
def test_adversarial_inputs_scan_in_linear_time(evil: str) -> None:
    f = dlp.Filter(**FILTER_KW)
    start = time.monotonic()
    f.scan(evil, redact=True)
    assert time.monotonic() - start < 3.0


def test_a_large_request_is_scanned_quickly() -> None:
    text = ("lorem ipsum " + fake(30) + " /Users/user/y ops@corp.example.org 10.0.0.1\n") * 40_000   # ~4 MB
    start = time.monotonic()
    checked = _check(d.CHAT, _body(d.CHAT, text), dlp.Filter(**FILTER_KW))
    assert time.monotonic() - start < 10.0
    assert checked.redactions["email"] == 40_000


def test_a_long_run_of_jwt_prefixes_scans_in_linear_time() -> None:
    """The JWT rule is bounded: an unbounded run of "eyJ…" made the scan quadratic (@security W2)."""
    body = "eyJ" * 400_000
    start = time.monotonic()
    dlp.Filter().scan_block(body)
    assert time.monotonic() - start < 5.0


def test_secret_names_match_whole_words_only() -> None:
    for name in ("PATH", "AUTHOR", "BYPASS", "KEYBOARD_LAYOUT", "PASSAGE"):
        assert not dlp._secret_name(name), name
    for name in ("API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "AWS_SECRET", "GH_PAT", "auth"):
        assert dlp._secret_name(name), name
