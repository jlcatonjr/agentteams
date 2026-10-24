"""Outbound data-loss prevention for the egress gateway (:mod:`.gateway`): bounds T6, never closes it.

Every model request the gateway relays, on every dialect, passes :meth:`Filter.apply` after the
deny-by-default policy (:func:`.gateway_dialects.check_request`) and before it is forwarded.
Decision D3 of the baseAgent hardening plan: **block secrets, redact and flag the rest.**

**Block** (HTTP 400, counted as ``refused``; the reason is ``dlp: <class>`` and never quotes the match):

| Class | What |
|---|---|
| ``known-secret`` | an exact value the gateway holds: its upstream key(s), every secret-looking value in the key files, ``--dlp-secrets-file`` files and ``~/.config/agentteams-secrets/*.env`` when present |
| ``private-key`` | a PEM or OpenSSH private-key header, a PuTTY key file |
| ``aws-access-key``, ``aws-secret-key`` | ``AKIA``/``ASIA``… key ids; a 40-character secret assigned to an ``aws…secret`` name |
| ``github-token``, ``gitlab-token`` | ``ghp_``/``gho_``/``ghu_``/``ghs_``/``ghr_`` tokens, ``github_pat_``; ``glpat-`` |
| ``anthropic-key``, ``openrouter-key``, ``openai-key`` | ``sk-ant-…``, ``sk-or-v1-…``, ``sk-…`` (``proj-``/``svcacct-``/``admin-``) |
| ``slack-token``, ``slack-webhook`` | ``xox?-…``; ``hooks.slack.com/services/…`` |
| ``google-api-key``, ``stripe-key``, ``npm-token``, ``pypi-token`` | ``AIza…``; ``sk_live_``/``rk_live_``; ``npm_…``; ``pypi-AgE…`` |
| ``jwt`` | a signed JSON Web Token (three segments) |
| ``bearer-token`` | ``Bearer <random-looking token>`` |
| ``url-credentials`` | ``scheme://user:<random-looking password>@`` |
| ``secret-assignment`` | a name containing password, secret, token, api key, credential… assigned (``=``/``:``) a random-looking value of 12+ characters, or 32+ hex digits |
| ``high-entropy-assignment`` | an ``UPPER_SNAKE=`` assignment of a 32+ character value with high entropy (not hex, not a digest) |

**Redact and flag** (replaced, counted in ``spend.json`` ``dlp_redactions``, the call continues), only
in message text (chat ``messages``; Messages ``system`` and ``messages``; Responses ``instructions``
and ``input``), never in tool-call arguments, ``tool_use`` inputs, thinking/reasoning blocks or ids:

| Class | Replaced by |
|---|---|
| ``private-package`` | ``[REDACTED:private-package]`` (names matching ``--dlp-private-names`` fnmatch patterns) |
| ``home-path`` | ``~`` (``/Users/<name>``, ``/home/<name>``, ``C:\\Users\\<name>``: the executor expands ``~`` back, so a target the agent names stays usable) |
| ``email`` | ``[REDACTED:email]`` (not ``git@…`` remotes) |
| ``hostname`` | ``[REDACTED:hostname]`` (the gateway's own hostname, ``--dlp-hostname`` names and ``--dlp-hostnames-file`` lines; a containerized gateway gets the host's from :mod:`.gateway_context`) |
| ``private-ip`` | ``[REDACTED:private-ip]`` (RFC 1918, link-local IPv4; ULA and link-local IPv6) |

**False-positive guards.** Hex digests and git SHAs, ``sha256:``/``sha512-``/``h1:`` digests, UUIDs,
CVE/GHSA ids, package versions and ``data:`` base64 URIs are never blocked: known formats need their
prefix at a token boundary and a random-looking body; generic assignments need a secret-like name or
a high-entropy non-hex value; ``data:…;base64,…`` spans and base64 image ``data`` are not scanned.

Every regex is linear (no nested quantifiers; bounded repeats), and a string seen before is not
scanned again (a conversation resends its history every turn; repeats are not recounted).
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
import re
import socket
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .gateway_dialects import CHAT, MESSAGES, RESPONSES, PolicyError

#: The agentteams secrets directory (same value as ``agent_upstreams.SECRETS_DIR``; not imported, so the
#: gateway container needs nothing beyond this package's gateway modules).
SECRETS_DIR = "~/.config/agentteams-secrets"
#: Exact secrets shorter than this are ignored (too likely to occur by chance).
MIN_EXACT = 8
#: A string longer than this is scanned in its first and last halves of this size (16 MiB, the body cap).
MAX_SCAN_CHARS = 16 * 1024 * 1024
CACHE_SIZE = 4096

BLOCK_CLASSES = ("known-secret", "private-key", "aws-access-key", "aws-secret-key", "github-token", "gitlab-token",
                 "anthropic-key", "openrouter-key", "openai-key", "slack-token", "slack-webhook", "google-api-key",
                 "stripe-key", "npm-token", "pypi-token", "jwt", "bearer-token", "url-credentials",
                 "secret-assignment", "high-entropy-assignment")
REDACT_CLASSES = ("private-package", "home-path", "email", "hostname", "private-ip")


class DlpBlocked(PolicyError):
    """A request carrying a secret or key: refused (400), never forwarded. ``cls`` is the class name only."""

    def __init__(self, cls: str) -> None:
        self.cls = cls
        super().__init__(f"dlp: {cls} (the request carries a secret or key; it was refused and nothing was forwarded)")


# ---------------------------------------------------------------- value tests

def entropy(s: str) -> float:
    """Shannon entropy of ``s`` in bits per character (0 for an empty string)."""
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values()) if n else 0.0


def _kinds(s: str) -> tuple[bool, bool, bool]:
    return (any("a" <= c <= "z" for c in s), any("A" <= c <= "Z" for c in s), any("0" <= c <= "9" for c in s))


def random_like(s: str, *, bits: float = 3.0) -> bool:
    """Whether ``s`` looks machine-generated: lower and upper case letters and digits, and ``bits`` of entropy."""
    return all(_kinds(s)) and entropy(s) >= bits


def _two_kinds(s: str) -> bool:
    """At least two of lower, upper, digit, and some entropy: a token body, not ``aaaa…`` or a word."""
    return sum(_kinds(s)) >= 2 and entropy(s) >= 3.0


_HEX = re.compile(r"[0-9a-fA-F]+")
_PLACEHOLDERS = ("example", "xxxx", "placeholder", "changeme", "change_me", "redacted", "dummy", "your_", "your-",
                 "<", "${", "{{", "****", "....", "process.env", "os.environ", "secrets.")
_SECRET_WORDS = ("password", "passwd", "passphrase", "secret", "token", "apikey", "api_key", "api-key",
                 "accesskey", "access_key", "access-key", "privatekey", "private_key", "private-key",
                 "credential", "auth_key", "authkey", "signing_key", "session_key", "client_secret")
_NOT_SECRET_PARTS = frozenset({"SHA", "SHA1", "SHA256", "SHA384", "SHA512", "MD5", "HASH", "DIGEST", "CHECKSUM",
                               "SUM", "INTEGRITY", "COMMIT", "VERSION", "REV", "REF", "ID", "UUID", "TAG", "PATH",
                               "URL", "DIR", "FILE", "HOME", "NAME", "IMAGE"})
_DIGEST_PREFIX = re.compile(r"(?i)(?:sha(?:1|224|256|384|512)|md5|h1)[-:]")


def _placeholder(value: str) -> bool:
    low = value.lower()
    return any(p in low for p in _PLACEHOLDERS)


def _secret_assignment(m: re.Match[str]) -> bool:
    key, value = m.group(1).lower(), m.group(2)
    if not any(w in key for w in _SECRET_WORDS) or _placeholder(value):
        return False
    if value.startswith(("/", "~", ".", "$", "%")) or "://" in value:
        return False                                     # a path or a reference, not a value
    return (len(value) >= 32 and bool(_HEX.fullmatch(value))) or random_like(value, bits=3.3)


def _env_assignment(m: re.Match[str]) -> bool:
    key, value = m.group(1), m.group(2).rstrip("=")
    if set(key.split("_")) & _NOT_SECRET_PARTS or _placeholder(value) or _DIGEST_PREFIX.match(value):
        return False
    return not _HEX.fullmatch(value) and random_like(value, bits=4.0)


def _url_password(m: re.Match[str]) -> bool:
    user, password = m.group(1), m.group(2)
    return password != user and not _placeholder(password) and random_like(password, bits=2.5)


def _group(n: int, test: Callable[[str], bool]) -> Callable[[re.Match[str]], bool]:
    return lambda m: test(m.group(n))


_B = r"(?<![A-Za-z0-9_-])"            # a token boundary before a prefixed credential
_E = r"(?![A-Za-z0-9_-])"
#: ``(class, regex, check)``, most specific first; ``check(match)`` confirms a candidate.
BLOCK_RULES: tuple[tuple[str, re.Pattern[str], Callable[[re.Match[str]], bool] | None], ...] = (
    ("private-key", re.compile(r"-----BEGIN[A-Z0-9 ]{0,40} PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-\d"), None),
    ("aws-access-key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])"), None),
    ("aws-secret-key", re.compile(r"(?i)aws[A-Za-z0-9_.-]{0,20}?(?:secret|sk)[A-Za-z0-9_.-]{0,20}[\"']?[ \t]{0,4}"
                                  r"[:=][ \t]{0,4}[\"']?([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"),
     _group(1, _two_kinds)),
    ("github-token", re.compile(_B + r"(?:gh[pousr]_([A-Za-z0-9]{36,255})|github_pat_([A-Za-z0-9_]{60,255}))" + _E),
     lambda m: _two_kinds(m.group(1) or m.group(2))),
    ("gitlab-token", re.compile(_B + r"glpat-([A-Za-z0-9_-]{20,64})" + _E), _group(1, _two_kinds)),
    ("anthropic-key", re.compile(_B + r"sk-ant-[a-z]{2,10}\d{2}-([A-Za-z0-9_-]{32,255})" + _E), _group(1, _two_kinds)),
    ("openrouter-key", re.compile(_B + r"sk-or-v1-[0-9a-f]{64}" + _E), None),
    ("openai-key", re.compile(_B + r"sk-(?:proj-|svcacct-|admin-)?([A-Za-z0-9_-]{32,255})" + _E),
     _group(1, random_like)),
    ("slack-token", re.compile(_B + r"xox[abposre]-([A-Za-z0-9-]{10,255})" + _E), _group(1, _two_kinds)),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]{6,12}/B[A-Z0-9]{6,12}/[A-Za-z0-9]{20,32}"),
     None),
    ("google-api-key", re.compile(_B + r"AIza([0-9A-Za-z_-]{35})" + _E), _group(1, _two_kinds)),
    ("stripe-key", re.compile(_B + r"(?:sk|rk)_live_([0-9A-Za-z]{20,255})" + _E), _group(1, _two_kinds)),
    ("npm-token", re.compile(_B + r"npm_([A-Za-z0-9]{36})" + _E), _group(1, random_like)),
    ("pypi-token", re.compile(_B + r"pypi-AgE[A-Za-z0-9_-]{50,}"), None),
    ("jwt", re.compile(_B + r"eyJ[A-Za-z0-9_-]{8,4096}\.eyJ[A-Za-z0-9_-]{8,8192}\.([A-Za-z0-9_-]{16,1024})"),
     None),   # bounded: an unbounded run of "eyJ…" made the scan quadratic (@security W2)
    ("bearer-token", re.compile(r"(?i)(?<![A-Za-z0-9])bearer[ \t]{1,4}([A-Za-z0-9._~+/-]{20,1024})"),
     _group(1, random_like)),
    ("url-credentials", re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,20}://([^\s:/@\"'<>]{1,64}):([^\s:/@\"'<>]{6,128})@"),
     _url_password),
    ("secret-assignment", re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z_][A-Za-z0-9_.-]{0,63})[\"']?[ \t]{0,4}[:=]"
                                     r"[ \t]{0,4}[\"']?([^\s\"'`<>,;(){}\[\]\\]{12,512})"), _secret_assignment),
    ("high-entropy-assignment", re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,63})=[\"']?([A-Za-z0-9+/=_.~-]{32,512})"),
     _env_assignment),
)
#: A rule runs only when one of its literal needles is in the text (``str in`` is far cheaper than a regex
#: whose leading lookbehind defeats the prefix search); ``_NEEDLES_CI`` are checked in the lower-cased text.
_NEEDLES = {"private-key": ("PRIVATE KEY", "PuTTY-User"), "aws-access-key": ("AKIA", "ASIA", "ABIA", "ACCA"),
            "github-token": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"), "gitlab-token": ("glpat-",),
            "anthropic-key": ("sk-ant-",), "openrouter-key": ("sk-or-v1-",), "openai-key": ("sk-",),
            "slack-token": ("xox",), "slack-webhook": ("hooks.slack.com",), "google-api-key": ("AIza",),
            "stripe-key": ("_live_",), "npm-token": ("npm_",), "pypi-token": ("pypi-AgE",), "jwt": ("eyJ",),
            "url-credentials": ("://",), "high-entropy-assignment": ("=",)}
_NEEDLES_CI = {"aws-secret-key": ("aws",), "bearer-token": ("bearer",)}

_DATA_URI = re.compile(r"data:[A-Za-z0-9.+/-]{1,64}(?:;[A-Za-z0-9=.+-]{1,64}){0,4};base64,[A-Za-z0-9+/=]+")
_HOME = re.compile(r"(?<![A-Za-z0-9_.~-])(?:/Users/|/home/|[A-Za-z]:\\Users\\)(?!Shared(?![A-Za-z0-9._-]))"
                   r"[A-Za-z0-9._-]{1,64}(?![A-Za-z0-9._-])")
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,64})@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
                    r"\.[A-Za-z]{2,24}(?![A-Za-z0-9-])")
_IPV4 = re.compile(r"(?<![0-9A-Za-z.@=_-])(?:10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
                   r"|192\.168(?:\.\d{1,3}){2}|169\.254(?:\.\d{1,3}){2})(?![0-9A-Za-z_-]|\.\d)")
_IPV6 = re.compile(r"(?i)(?<![0-9a-f:.])(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f]):(?:[0-9a-f]{0,4}:){1,6}[0-9a-f]{0,4}"
                   r"(?![0-9a-f:])")
_NAME_TOKEN = re.compile(r"(?<![A-Za-z0-9@._/-])@?[A-Za-z0-9][A-Za-z0-9._/@-]{0,213}")


def _redacted(cls: str) -> str:
    return f"[REDACTED:{cls}]"


# ---------------------------------------------------------------- secret sources

def _secret_name(name: str) -> bool:
    # whole words of the name only, so PATH, AUTHOR or BYPASS do not count (@security W2 false positives)
    words = set(re.split(r"[^A-Z0-9]+", name.upper()))
    return bool(words & {"KEY", "APIKEY", "TOKEN", "SECRET", "PASS", "PASSWORD", "PASSWD", "PAT", "CREDENTIAL",
                         "CREDENTIALS", "AUTH"})


def secret_values(text: str) -> set[str]:
    """Return the secret values in an env file's text (``KEY=value`` lines, ``export`` and quotes allowed).

    A value counts when it is at least :data:`MIN_EXACT` characters and either its name looks secret
    (KEY, TOKEN, SECRET, PASS, PAT, CREDENTIAL, AUTH) or the value itself is random-looking; a bare
    line of 16+ characters (a bare key file) counts too. URLs and model ids under other names do not.
    """
    out: set[str] = set()
    for line in text.splitlines():
        line = line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#"):
            continue
        name, eq, value = line.partition("=")
        if not eq:
            if len(line) >= 16 and " " not in line:
                out.add(line)
            continue
        value = value.strip().strip("'\"")
        if len(value) >= MIN_EXACT and (_secret_name(name.strip()) or random_like(value, bits=3.5)):
            out.add(value)
    return out


def load_secret_files(paths: Iterable[Path]) -> tuple[set[str], int]:
    """Read every file's secret values (:func:`secret_values`); return ``(values, files that could not be read)``."""
    values: set[str] = set()
    unreadable = 0
    for path in paths:
        try:
            values |= secret_values(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            unreadable += 1
    return values, unreadable


def secrets_dir_files(directory: str | Path = SECRETS_DIR) -> list[Path]:
    """Return the ``*.env`` files of the agentteams secrets directory, or ``[]`` when it is absent."""
    try:
        d = Path(directory).expanduser()
        return sorted(p for p in d.glob("*.env") if p.is_file()) if d.is_dir() else []
    except (RuntimeError, OSError):   # no home directory (a container uid without HOME), or not readable
        return []


def default_hostnames() -> list[str]:
    """This machine's hostname, full and short (``localhost`` and names under 3 characters are left out)."""
    full = socket.gethostname().strip().lower()
    names = {full, full.split(".", 1)[0]}
    return sorted(n for n in names if len(n) >= 3 and n not in ("localhost", "localhost.localdomain"))


def read_private_names(path: Path) -> list[str]:
    """Read fnmatch patterns, one per line (``#`` comments and blank lines ignored)."""
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]


# ---------------------------------------------------------------- the filter

_SKIP_REDACT_KEYS = frozenset({"type", "role", "id", "tool_use_id", "tool_call_id", "call_id", "name", "model",
                               "signature", "encrypted_content", "media_type", "cache_control", "status", "index",
                               "object", "tool_calls", "function_call", "arguments", "image_url", "file_data",
                               "input_audio"})
_NO_REDACT_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning", "tool_use", "server_tool_use",
                              "function_call", "custom_tool_call"})
#: Top-level body keys whose message text is redacted, per dialect (every string anywhere is block-scanned).
REDACT_ROOTS = {CHAT: frozenset({"messages"}), MESSAGES: frozenset({"system", "messages"}),
                RESPONSES: frozenset({"instructions", "input"})}


class Filter:
    """The gateway's DLP filter: exact secrets, block rules, redaction rules, and a scan cache."""

    def __init__(self, *, secrets: Iterable[str] = (), hostnames: Iterable[str] = (),
                 private_names: Iterable[str] = (), redact: bool = True) -> None:
        """Build a filter.

        Args:
            secrets: Exact values to block (the upstream keys, the secrets files' values); never logged.
            hostnames: Host names to redact (case-insensitive, whole names).
            private_names: fnmatch patterns of private package names to redact (case-insensitive).
            redact: Apply the redact-and-flag classes (blocking always applies).
        """
        self.secrets = tuple(sorted({s for s in secrets if len(s) >= MIN_EXACT}, key=len, reverse=True))
        self.hostnames = tuple(sorted({h.lower() for h in hostnames if len(h) >= 3}, key=len, reverse=True))
        self.private_names = tuple(p.strip().lower() for p in private_names if p.strip())
        # the longest literal run of each pattern: names are tokenized only when one occurs ("" : always)
        self._anchors = tuple(max(re.split(r"[*?\[\]]", p), key=len) for p in self.private_names) \
            if self.private_names else None
        self.redact = redact
        self._host = re.compile(r"(?i)(?<![A-Za-z0-9-])(?:" + "|".join(map(re.escape, self.hostnames)) +
                                r")(?![A-Za-z0-9-])") if self.hostnames else None
        self._cache: OrderedDict[tuple[bytes, bool], tuple[str, str | None]] = OrderedDict()
        self._lock = threading.Lock()   # requests are checked concurrently (outside the gateway's upstream gate)

    # -- scanning one string

    def scan_block(self, text: str) -> str:
        """Return the first block class found in ``text``, or ``""``. ``data:`` base64 URIs are not scanned."""
        if len(text) > 2 * MAX_SCAN_CHARS:
            text = text[:MAX_SCAN_CHARS] + "\n" + text[-MAX_SCAN_CHARS:]
        for secret in self.secrets:
            if secret in text:
                return "known-secret"
        if ";base64," in text:
            text = _DATA_URI.sub(" ", text)
        low = ""
        for cls, rx, check in BLOCK_RULES:
            if cls in _NEEDLES_CI:
                low = low or text.lower()
                if not any(n in low for n in _NEEDLES_CI[cls]):
                    continue
            elif cls in _NEEDLES and not any(n in text for n in _NEEDLES[cls]):
                continue
            for m in rx.finditer(text):
                if check is None or check(m):
                    return cls
        return ""

    def _redact(self, text: str, counts: Counter[str]) -> str:
        """Redact the lesser classes outside ``data:`` URIs; count each replacement in ``counts``."""
        if ";base64," not in text:
            return self._redact_plain(text, counts)
        out, last = [], 0
        for m in _DATA_URI.finditer(text):
            out += [self._redact_plain(text[last:m.start()], counts), m.group(0)]
            last = m.end()
        return "".join([*out, self._redact_plain(text[last:], counts)])

    def _redact_plain(self, text: str, counts: Counter[str]) -> str:
        if self._anchors is not None and (not all(self._anchors) or any(a in text.lower() for a in self._anchors)):
            text = _NAME_TOKEN.sub(lambda m: self._private_token(m.group(0), counts), text)

        def sub(cls: str, rx: re.Pattern[str], text: str, keep: Callable[[re.Match[str]], bool] | None = None,
                repl: str | None = None) -> str:
            def one(m: re.Match[str]) -> str:
                if keep is not None and keep(m):
                    return m.group(0)
                counts[cls] += 1
                return _redacted(cls) if repl is None else repl
            return rx.sub(one, text)

        if "/Users/" in text or "/home/" in text or ":\\Users\\" in text:
            text = sub("home-path", _HOME, text, repl="~")
        if "@" in text:
            text = sub("email", _EMAIL, text, keep=lambda m: m.group(1).lower() == "git")
        if self._host is not None:
            text = sub("hostname", self._host, text)
        if any(n in text for n in ("10.", "172.", "192.168.", "169.254.")):
            text = sub("private-ip", _IPV4, text, keep=lambda m: any(int(o) > 255 for o in m.group(0).split(".")))
        if "::" in text or text.count(":") >= 2:
            text = sub("private-ip", _IPV6, text, keep=lambda m: m.group(0).count(":") < 2)
        return text

    def _private_token(self, token: str, counts: Counter[str]) -> str:
        """Redact a name token, or its ``/``-separated parts (``@scope/name`` kept together), that is private."""
        core = token.rstrip(".")
        tail = token[len(core):]
        if self._private(core):
            counts["private-package"] += 1
            return _redacted("private-package") + tail
        parts = core.split("/")
        if len(parts) == 1:
            return token
        out: list[str] = []
        i = 0
        while i < len(parts):
            pair = "/".join(parts[i:i + 2]) if i + 1 < len(parts) and parts[i].startswith("@") else ""
            if pair and self._private(pair):
                out.append(_redacted("private-package"))
                counts["private-package"] += 1
                i += 2
                continue
            hit = bool(parts[i]) and self._private(parts[i].split("@", 1)[0] if not parts[i].startswith("@")
                                                  else parts[i])
            counts["private-package"] += int(hit)
            out.append(_redacted("private-package") if hit else parts[i])
            i += 1
        return "/".join(out) + tail

    def _private(self, name: str) -> bool:
        low = name.lower()
        bare = low.split("@", 1)[0] if not low.startswith("@") else "@" + low[1:].split("@", 1)[0]
        return any(fnmatch.fnmatchcase(bare, p) or fnmatch.fnmatchcase(low, p) for p in self.private_names)

    def scan(self, text: str, *, redact: bool) -> tuple[str, str | None, Counter[str]]:
        """Scan one string: ``(block class or "", the redacted text or None when unchanged, counts)``.

        A string scanned before returns the cached result with empty counts (counted once).
        """
        key = (hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=20).digest(), redact)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
        if hit is not None:
            return hit[0], hit[1], Counter()
        counts: Counter[str] = Counter()
        cls = self.scan_block(text)
        new = None
        if not cls and redact and self.redact:
            red = self._redact(text, counts)
            new = red if red != text else None
        with self._lock:
            self._cache[key] = (cls, new)
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return cls, new, counts

    # -- a whole request

    def apply(self, dialect: str, body: dict[str, Any]) -> dict[str, int]:
        """Scan a validated request body in place: raise on a secret, redact the lesser classes.

        Every string value and object key in the body is block-scanned (except base64 media ``data``);
        message text under :data:`REDACT_ROOTS` is redacted, but never ids, names, tool-call arguments,
        ``tool_use`` inputs or thinking/reasoning blocks, so the tool-call structure is unchanged.

        Args:
            dialect: ``chat``, ``messages`` or ``responses``.
            body: The decoded request (mutated: redacted strings are replaced).

        Returns:
            Redactions made, per class (only for text not seen before).

        Raises:
            DlpBlocked: A block class was found (its ``cls`` names it; the match is never kept).
        """
        roots = REDACT_ROOTS.get(dialect, frozenset())
        counts: Counter[str] = Counter()
        for key in list(body):
            if key == "model":
                continue
            body[key] = self._walk(body[key], key in roots, counts)
            self._key(key)
        return dict(counts)

    def _key(self, key: str) -> None:
        cls = self.scan(key, redact=False)[0]
        if cls:
            raise DlpBlocked(cls)

    def _walk(self, node: Any, redact: bool, counts: Counter[str]) -> Any:
        if isinstance(node, str):
            cls, new, found = self.scan(node, redact=redact)
            if cls:
                raise DlpBlocked(cls)
            counts.update(found)
            return node if new is None else new
        if isinstance(node, list):
            for i, item in enumerate(node):
                node[i] = self._walk(item, redact, counts)
            return node
        if isinstance(node, dict):
            inner = redact and node.get("type") not in _NO_REDACT_TYPES
            for k in list(node):
                self._key(k)
                if k == "data" and node.get("type") == "base64":
                    continue                              # base64 media: not text, not scanned
                node[k] = self._walk(node[k], inner and k not in _SKIP_REDACT_KEYS
                                     and not (k == "input" and isinstance(node[k], dict)), counts)
            return node
        return node

    def scan_response(self, payload: bytes) -> str:
        """Return the first block class in an upstream reply (JSON or SSE, as received), or ``""``."""
        return self.scan_block(payload.decode("utf-8", errors="replace"))


def from_mapping(counts: Mapping[str, int]) -> dict[str, int]:
    """Return ``counts`` without zero entries, sorted by class (stable ``spend.json`` output)."""
    return {k: int(v) for k, v in sorted(counts.items()) if v}
