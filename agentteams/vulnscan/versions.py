"""Lenient, ecosystem-neutral version ordering that never raises.

The ordering is good enough to compare versions across PEP 440, semver and Composer
for advisory ranges and upgrade targets:

- numeric release segments are compared, padded with zeros;
- pre-releases sort before the final release, ordered ``dev < a/alpha < b/beta < rc/c/pre``;
- ``.postN`` sorts after the final release;
- local and build metadata after ``+`` is ignored.

Every token is typed as ``(kind, value)``, so a number is never compared with a word and
comparison cannot raise.
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"^(\d+(?:\.\d+)*)(.*)$", re.ASCII)   # ASCII digits only: int() of "²" raises
_FINAL = re.compile(r"^v?\d+(\.\d+)*$", re.ASCII)
_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}


def _num(digits: str) -> int:
    """Return the value of an ASCII digit run, capped in length (int() refuses over 4300 digits)."""
    return int(digits[:64])


def _tokens(rest: str) -> tuple:
    out = []
    for t in re.findall(r"\d+|[a-z]+", rest, re.ASCII):
        if t.isdigit():
            out.append((2, _num(t), ""))
        elif t in _PRE_RANK:
            out.append((1, _PRE_RANK[t], ""))
        else:
            out.append((0, 0, t))
    return tuple(out)


def version_key(version: str) -> tuple:
    """Return a sortable key for ``version``.

    Args:
        version: A version string such as ``"2.31.0"``, ``"v1.2.3-rc.1"`` or ``"1!2.0"``.

    Returns:
        A tuple that orders versions as described in the module docstring.
    """
    v = version.strip().lower().lstrip("v").split("+", 1)[0]
    epoch = 0
    if "!" in v:
        head, v = v.split("!", 1)
        epoch = _num(head) if head.isascii() and head.isdigit() else 0
    m = _SPLIT.match(v)
    if not m:
        return (epoch, (), -1, _tokens(v))
    nums = tuple(_num(p) for p in m.group(1).split("."))
    nums = nums + (0,) * (6 - len(nums)) if len(nums) < 6 else nums
    rest = m.group(2).lstrip(".-_")
    if not rest:
        return (epoch, nums, 1, ())
    if rest.startswith("post"):
        return (epoch, nums, 2, _tokens(rest))
    return (epoch, nums, 0, _tokens(rest))


def is_prerelease(version: str) -> bool:
    """Return True when ``version`` is a numeric release with a pre-release tag (``rc1``, ``b2``, ``-alpha.1``, ``.dev3``).

    A post-release (``1.2.post1``), a distro revision (``2.3-4ubuntu1``) or a plain release is not one.

    Args:
        version: Version string.

    Returns:
        Whether the version is a pre-release.
    """
    _epoch, nums, phase, tokens = version_key(version)
    return bool(nums) and phase == 0 and bool(tokens) and tokens[0][0] == 1


def is_final(version: str) -> bool:
    """Return True for plain numeric releases (no pre-release, dev or local tag).

    Args:
        version: Version string.

    Returns:
        Whether the version is a final release.
    """
    return bool(_FINAL.match(version.strip()))


def major(version: str) -> int | None:
    """Return the leading numeric component, or None when there is none.

    Args:
        version: Version string.

    Returns:
        The major version number.
    """
    m = _SPLIT.match(version.strip().lstrip("vV"))
    return _num(m.group(1).split(".")[0]) if m else None
