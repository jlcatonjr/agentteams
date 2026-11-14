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
