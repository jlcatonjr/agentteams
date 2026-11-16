"""Public-hash exemptions for :mod:`agentteams.scan`'s entropy detector (#460).

The context-free high-entropy detector in :func:`agentteams.scan._check_line` raised
``credential``/high findings on two kinds of **public** value, turning ordinary repositories
into a HALT verdict:

* npm/yarn **Subresource Integrity** hashes in lockfile ``integrity`` values (one package-lock
  produced 104 high findings);
* the full **commit SHA** in a public ``https://github.com/<owner>/<repo>/commit/<sha>`` URL
  (about one in eight 40-hex SHAs clears the entropy threshold).

:func:`check_public_hash_exemption` suppresses exactly those tokens. It is bound to the token's
span and to its position in a recognised form, never to the line and never to a path glob
alone, so every other token on the same line (a minified lockfile is a single line) is still
scanned. Split out of ``scan.py`` to keep that module under the CH-07 line ceiling; it is an
integrity-pinned enforcement module in its own right because it narrows a credential detector.
"""

from __future__ import annotations

import re
from collections.abc import Callable

#: Lockfiles whose ``integrity`` values are SRI hashes written by the package manager and
#: checked by it on install. Matched on the basename; the basename alone exempts nothing.
SRI_LOCKFILE_NAMES = frozenset({"package-lock.json", "npm-shrinkwrap.json", "yarn.lock"})

#: One SRI item, the base64 body length bound to the algorithm's digest size (sha1 20 B -> 28
#: chars, sha256 32 B -> 44, sha384 48 B -> 64, sha512 64 B -> 88, padding included). A secret
#: merely prefixed ``sha512-`` with any other length does not match.
_SRI_ITEM_RE = re.compile(
    r"(?:sha1-(?P<b1>[A-Za-z0-9+/]{27}=)"
    r"|sha256-(?P<b256>[A-Za-z0-9+/]{43}=)"
    r"|sha384-(?P<b384>[A-Za-z0-9+/]{64})"
    r"|sha512-(?P<b512>[A-Za-z0-9+/]{86}==))"
)

#: The value of a JSON ``"integrity"`` key (package-lock.json, npm-shrinkwrap.json). A value
#: holding a backslash escape does not match, so it is never exempted.
_JSON_INTEGRITY_RE = re.compile(r'"integrity"\s*:\s*"(?P<value>[^"\\]*)"')

#: A yarn.lock (v1) ``integrity`` field: the whole line is the field, quoted or bare.
_YARN_INTEGRITY_RE = re.compile(r'^\s*integrity\s+(?:"(?P<quoted>[^"\\]*)"|(?P<bare>\S+))\s*$')

#: A public GitHub commit URL whose last path segment is a full 40- or 64-hex SHA that does
#: not run on into further token characters.
_GITHUB_COMMIT_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}/commit/"
    r"(?P<sha>[0-9a-f]{64}|[0-9a-f]{40})(?![A-Za-z0-9+/=])"
)


#: A git object id or hex digest: exactly 40 (SHA-1) or 64 (SHA-256) lowercase hex characters. Git and
#: ``sha256sum``/``shasum`` print lowercase; an uppercase or mixed-case run is not exempted.
_GIT_HEX_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

#: The label that must sit immediately before the token (only spaces, ``=`` or ``:`` between) for it to be
#: read as a git object id or digest: a merge-pin flag, or a git/digest word. Case-insensitive.
_GIT_HEX_LABEL_RE = re.compile(
    r"(?:--match-head-commit|\bmerged\s+as|\b(?:commit|sha|sha1|sha-1|sha256|sha-256|digest|head|merge|tree|parent|tag|oid))"
    r"[ \t]*[=:]?[ \t]*$",
    re.IGNORECASE,
)


#: ``<label>=<hex>`` or ``<label>:<hex>`` read as one token by the sensitive-context matcher.
_JOINED_GIT_HEX_RE = re.compile(
    r"(?:--match-head-commit|commit|sha|sha1|sha-1|sha256|sha-256|digest|head|merge|tree|parent|tag|oid)"
    r"[=:](?P<hex>[0-9a-f]{40}|[0-9a-f]{64})",
    re.IGNORECASE,
)

#: An OpenSSH / git SSH key fingerprint as ``ssh-keygen -l`` and ``git tag -v`` print it: ``SHA256:`` then
#: exactly 43 unpadded base64 characters (a 32-byte digest).
_SSH_FINGERPRINT_BODY_RE = re.compile(r"[A-Za-z0-9+/]{43}")

def is_labelled_git_hash(line: str, start: int, end: int) -> bool:
    """True when ``line[start:end]`` is a git object id or hex digest introduced by a git/digest label.

    Security-decision logs and release records cite commits (``--match-head-commit <sha>``, ``merged as
    <sha>``) and digests (``sha256 <hex>``). When such a line also carries a secret-context word (for example
    "operator token"), the scanner's sensitive-context matcher reported the 40/64-hex value as a high-severity
    secret, and the fleet snapshot guard refused the file (v1.2.0-rc.1 soak, 2026-11-16).

    The exemption is bound to the token span, never the line: the token must be exactly 40 or 64 lowercase hex
    characters, and the text before it must end with a label from :data:`_GIT_HEX_LABEL_RE`. A bare hex run
    with no such label is still reported, because some credentials are 40-hex (older GitHub OAuth tokens).

    Accepted residue (Rule 12): a secret that is itself exactly 40 or 64 lowercase hex characters, written
    straight after one of these labels, is not reported. A label's ``\\b`` also matches after ``-`` or ``.``, so a
    label that ends a config key counts too: ``webhook-secret.digest: <64hex>`` or ``x-tag: <hex>``
    is exempt. ``digest``, ``tag``, ``head`` and ``merge`` are common words; that is the price of reading logs.

    Args:
        line: The line being scanned.
        start: Start offset of the candidate token in ``line``.
        end: End offset of the candidate token in ``line``.

    Returns:
        True to suppress the entropy or secret-context finding for this token; False to report it.
    """
    token = line[start:end]
    hex_at = start
    if _GIT_HEX_RE.fullmatch(token) is None:
        # The sensitive-context matcher reads ``--match-head-commit=<sha>`` as one token: accept it only when the
        # token is exactly a label, one ``=`` or ``:``, then the hex, so nothing else in the token is waved through.
        joined = _JOINED_GIT_HEX_RE.fullmatch(token)
        if joined is None:
            return False
        hex_at = start + joined.start("hex")
    return _GIT_HEX_LABEL_RE.search(line[:hex_at]) is not None


def sri_body_spans(line: str, filepath: str) -> list[tuple[int, int]]:
    """Return the spans of SRI base64 bodies inside a lockfile ``integrity`` value.

    Args:
        line: One line of the file.
        filepath: The file's path; only its basename is consulted.

    Returns:
        ``(start, end)`` offsets into ``line``, one per SRI item body. Empty unless the basename
        is a known lockfile and **every** space-separated item of the value is a well-formed
        SRI item: a value with any stray item exempts nothing.
    """
    name = filepath.replace("\\", "/").rsplit("/", 1)[-1]
    if name not in SRI_LOCKFILE_NAMES:
        return []
    values: list[tuple[int, str]] = []
    if name == "yarn.lock":
        match = _YARN_INTEGRITY_RE.match(line)
        if match is not None:
            group = "quoted" if match.group("quoted") is not None else "bare"
            values.append((match.start(group), match.group(group)))
    else:
        values.extend((m.start("value"), m.group("value")) for m in _JSON_INTEGRITY_RE.finditer(line))
    spans: list[tuple[int, int]] = []
    for offset, value in values:
        items: list[tuple[int, int]] = []
        position = offset
        for part in value.split(" "):
            item = _SRI_ITEM_RE.fullmatch(part)
            if item is None:
                items = []
                break
            body = next(g for g in ("b1", "b256", "b384", "b512") if item.group(g) is not None)
            items.append((position + item.start(body), position + item.end(body)))
            position += len(part) + 1
        spans.extend(items)
    return spans


def check_public_hash_exemption(
    line: str,
    filepath: str,
    start: int,
    end: int,
    is_opaque_segment: Callable[[str], bool],
) -> bool:
    """True when the high-entropy token at ``line[start:end]`` is a known public hash (#460).

    * **SRI:** the token is exactly the base64 body of an SRI item in a lockfile ``integrity``
      value (see :func:`sri_body_spans`).
    * **Commit URL:** the token ends exactly at the 40/64-hex SHA that is the last segment of
      ``https://github.com/<owner>/<repo>/commit/<sha>``. The scanner reads
      ``com/<owner>/<repo>/commit/<sha>`` as one token, so every other ``/`` segment is
      re-tested with ``is_opaque_segment``; a secret in the owner or repo segment still fires.

    Accepted residue (Rule 12): a secret that is itself shaped exactly like one of these public
    hashes, in exactly that position, is not reported. And a secret of 23 characters or fewer in a
    commit URL's owner or repo segment is not re-flagged, because ``is_opaque_segment`` (the same
    test :func:`agentteams.scan._looks_like_filesystem_path` applies) needs 24 or more.

    Args:
        line: The line being scanned.
        filepath: The file's path (the SRI form consults its basename).
        start: Start offset of the candidate token in ``line``.
        end: End offset of the candidate token in ``line``.
        is_opaque_segment: The scanner's own test for whether one segment, alone, is a
            high-entropy opaque token (passed in so the threshold has one source of truth).

    Returns:
        True to suppress the entropy finding for this token; False to report it.
    """
    if (start, end) in sri_body_spans(line, filepath):
        return True
    for url in _GITHUB_COMMIT_URL_RE.finditer(line):
        if url.end("sha") != end or not url.start() <= start <= url.start("sha"):
            continue
        return not any(is_opaque_segment(s) for s in line[start:url.start("sha")].split("/"))
    return False


def is_ssh_key_fingerprint(line: str, start: int, end: int) -> bool:
    """True when ``line[start:end]`` is the body of an SSH key fingerprint, ``SHA256:<43 base64>``.

    Security logs record release-signature checks (``Good "git" signature … key SHA256:IPdnLw…``); the 43-character
    base64 body clears the entropy bar (about 5 bits) and was reported as a high-severity secret, so the fleet
    snapshot guard refused every governed team's log (v1.2.0-rc.1 soak, 2026-11-16). A fingerprint is a public
    digest of a public key.

    Bound to the token span: the token must be exactly 43 characters of unpadded base64, the 7 characters before it
    must be ``SHA256:`` (as ssh-keygen and git print it, case-sensitive), and the next character must not continue a
    base64 run. A bare 43-character base64 value with no ``SHA256:`` label is still reported.

    Accepted residue (Rule 12): a secret that is itself 43 unpadded base64 characters, written straight after
    ``SHA256:``, is not reported.

    Args:
        line: The line being scanned.
        start: Start offset of the candidate token in ``line``.
        end: End offset of the candidate token in ``line``.

    Returns:
        True to suppress the finding for this token; False to report it.
    """
    if _SSH_FINGERPRINT_BODY_RE.fullmatch(line[start:end]) is None:
        return False
    if end < len(line) and (line[end].isalnum() or line[end] in "+/="):
        return False
    return start >= 7 and line[start - 7:start] == "SHA256:"


def is_labelled_public_identifier(line: str, start: int, end: int) -> bool:
    """True when ``line[start:end]`` is a labelled git object id / digest or an SSH key fingerprint.

    The scanner's one call for both public-identifier exemptions (v1.2.0-rc.1 soak), in both matcher branches: a
    security-log line that says "operator token" is still citing a commit or a release-key fingerprint.

    Args:
        line: The line being scanned.
        start: Start offset of the candidate token in ``line``.
        end: End offset of the candidate token in ``line``.

    Returns:
        True to suppress the finding for this token; False to report it.
    """
    return is_labelled_git_hash(line, start, end) or is_ssh_key_fingerprint(line, start, end)
