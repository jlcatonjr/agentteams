"""CVSS base scores from vector strings, without a dependency: v3.0/v3.1 and v4.0.

OSV advisories carry their CVSS vectors in ``severity[]`` (``{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/..."}``).
:func:`score` turns one vector into its base score; :func:`band` maps a score to the qualitative rating
both specifications share (0.1-3.9 LOW, 4.0-6.9 MODERATE, 7.0-8.9 HIGH, 9.0-10.0 CRITICAL; vulnscan says
MODERATE where CVSS says Medium, as GitHub does).

- **v3.0 / v3.1**: the base-score equations of the FIRST specification (section 7.1), with each version's
  ``Roundup`` (v3.1's integer arithmetic; v3.0's plain ceiling to one decimal).
- **v4.0**: the official FIRST scoring algorithm (macro vectors, the published look-up table and the
  severity-distance interpolation), ported from the FIRST reference calculator (BSD-2-Clause, FIRST,
  Red Hat and contributors). Threat and environmental metrics in the vector are honoured as the
  calculator does (``E:X`` counts as ``E:A``; ``CR/IR/AR:X`` as ``H``).

A vector that is malformed, misses a base metric or names a version these do not implement gives None:
the caller then falls back to the advisory's label, or reports the advisory as *unscored*.
"""

from __future__ import annotations

import itertools
import math

#: The look-up table of v4.0 macro-vector scores (EQ1..EQ6 digits -> score), FIRST reference calculator.
_V4_LOOKUP: dict[str, float] = {k: float(v) for k, v in (item.split(":") for item in (
    "000000:10 000001:9.9 000010:9.8 000011:9.5 000020:9.5 000021:9.2 000100:10 000101:9.6 000110:9.3 "
    "000111:8.7 000120:9.1 000121:8.1 000200:9.3 000201:9 000210:8.9 000211:8 000220:8.1 000221:6.8 "
    "001000:9.8 001001:9.5 001010:9.5 001011:9.2 001020:9 001021:8.4 001100:9.3 001101:9.2 001110:8.9 "
    "001111:8.1 001120:8.1 001121:6.5 001200:8.8 001201:8 001210:7.8 001211:7 001220:6.9 001221:4.8 "
    "002001:9.2 002011:8.2 002021:7.2 002101:7.9 002111:6.9 002121:5 002201:6.9 002211:5.5 002221:2.7 "
    "010000:9.9 010001:9.7 010010:9.5 010011:9.2 010020:9.2 010021:8.5 010100:9.5 010101:9.1 010110:9 "
    "010111:8.3 010120:8.4 010121:7.1 010200:9.2 010201:8.1 010210:8.2 010211:7.1 010220:7.2 010221:5.3 "
    "011000:9.5 011001:9.3 011010:9.2 011011:8.5 011020:8.5 011021:7.3 011100:9.2 011101:8.2 011110:8 "
    "011111:7.2 011120:7 011121:5.9 011200:8.4 011201:7 011210:7.1 011211:5.2 011220:5 011221:3 012001:8.6 "
    "012011:7.5 012021:5.2 012101:7.1 012111:5.2 012121:2.9 012201:6.3 012211:2.9 012221:1.7 100000:9.8 "
    "100001:9.5 100010:9.4 100011:8.7 100020:9.1 100021:8.1 100100:9.4 100101:8.9 100110:8.6 100111:7.4 "
    "100120:7.7 100121:6.4 100200:8.7 100201:7.5 100210:7.4 100211:6.3 100220:6.3 100221:4.9 101000:9.4 "
    "101001:8.9 101010:8.8 101011:7.7 101020:7.6 101021:6.7 101100:8.6 101101:7.6 101110:7.4 101111:5.8 "
    "101120:5.9 101121:5 101200:7.2 101201:5.7 101210:5.7 101211:5.2 101220:5.2 101221:2.5 102001:8.3 "
    "102011:7 102021:5.4 102101:6.5 102111:5.8 102121:2.6 102201:5.3 102211:2.1 102221:1.3 110000:9.5 "
    "110001:9 110010:8.8 110011:7.6 110020:7.6 110021:7 110100:9 110101:7.7 110110:7.5 110111:6.2 110120:6.1 "
    "110121:5.3 110200:7.7 110201:6.6 110210:6.8 110211:5.9 110220:5.2 110221:3 111000:8.9 111001:7.8 "
    "111010:7.6 111011:6.7 111020:6.2 111021:5.8 111100:7.4 111101:5.9 111110:5.7 111111:5.7 111120:4.7 "
    "111121:2.3 111200:6.1 111201:5.2 111210:5.7 111211:2.9 111220:2.4 111221:1.6 112001:7.1 112011:5.9 "
    "112021:3 112101:5.8 112111:2.6 112121:1.5 112201:2.3 112211:1.3 112221:0.6 200000:9.3 200001:8.7 "
    "200010:8.6 200011:7.2 200020:7.5 200021:5.8 200100:8.6 200101:7.4 200110:7.4 200111:6.1 200120:5.6 "
    "200121:3.4 200200:7 200201:5.4 200210:5.2 200211:4 200220:4 200221:2.2 201000:8.5 201001:7.5 201010:7.4 "
    "201011:5.5 201020:6.2 201021:5.1 201100:7.2 201101:5.7 201110:5.5 201111:4.1 201120:4.6 201121:1.9 "
    "201200:5.3 201201:3.6 201210:3.4 201211:1.9 201220:1.9 201221:0.8 202001:6.4 202011:5.1 202021:2 "
    "202101:4.7 202111:2.1 202121:1.1 202201:2.4 202211:0.9 202221:0.4 210000:8.8 210001:7.5 210010:7.3 "
    "210011:5.3 210020:6 210021:5 210100:7.3 210101:5.5 210110:5.9 210111:4 210120:4.1 210121:2 210200:5.4 "
    "210201:4.3 210210:4.5 210211:2.2 210220:2 210221:1.1 211000:7.5 211001:5.5 211010:5.8 211011:4.5 "
    "211020:4 211021:2.1 211100:6.1 211101:5.1 211110:4.8 211111:1.8 211120:2 211121:0.9 211200:4.6 "
    "211201:1.8 211210:1.7 211211:0.7 211220:0.8 211221:0.2 212001:5.3 212011:2.4 212021:1.4 212101:2.4 "
    "212111:1.2 212121:0.5 212201:1 212211:0.3 212221:0.1 "
).split())}

_V3_WEIGHTS: dict[str, dict[str, float]] = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "C": {"H": 0.56, "L": 0.22, "N": 0.0},
    "I": {"H": 0.56, "L": 0.22, "N": 0.0},
    "A": {"H": 0.56, "L": 0.22, "N": 0.0},
}
_V3_PR = {"U": {"N": 0.85, "L": 0.62, "H": 0.27}, "C": {"N": 0.85, "L": 0.68, "H": 0.5}}

_V4_BASE: dict[str, tuple[str, ...]] = {
    "AV": ("N", "A", "L", "P"), "AC": ("L", "H"), "AT": ("N", "P"), "PR": ("N", "L", "H"),
    "UI": ("N", "P", "A"), "VC": ("H", "L", "N"), "VI": ("H", "L", "N"), "VA": ("H", "L", "N"),
    "SC": ("H", "L", "N"), "SI": ("H", "L", "N"), "SA": ("H", "L", "N"),
}
# Severity levels per metric (lower is more severe), for the distance from the macro vector's highest vector.
_V4_LEVELS: dict[str, dict[str, float]] = {
    "AV": {"N": 0.0, "A": 0.1, "L": 0.2, "P": 0.3}, "PR": {"N": 0.0, "L": 0.1, "H": 0.2},
    "UI": {"N": 0.0, "P": 0.1, "A": 0.2}, "AC": {"L": 0.0, "H": 0.1}, "AT": {"N": 0.0, "P": 0.1},
    "VC": {"H": 0.0, "L": 0.1, "N": 0.2}, "VI": {"H": 0.0, "L": 0.1, "N": 0.2}, "VA": {"H": 0.0, "L": 0.1, "N": 0.2},
    "SC": {"H": 0.1, "L": 0.2, "N": 0.3}, "SI": {"S": 0.0, "H": 0.1, "L": 0.2, "N": 0.3},
    "SA": {"S": 0.0, "H": 0.1, "L": 0.2, "N": 0.3},
    "CR": {"H": 0.0, "M": 0.1, "L": 0.2}, "IR": {"H": 0.0, "M": 0.1, "L": 0.2}, "AR": {"H": 0.0, "M": 0.1, "L": 0.2},
}
# Highest-severity vectors of each macro-vector level (``max_composed.js``); EQ3 is keyed by EQ3 then EQ6.
_V4_MAX_EQ1 = {0: ["AV:N/PR:N/UI:N"], 1: ["AV:A/PR:N/UI:N", "AV:N/PR:L/UI:N", "AV:N/PR:N/UI:P"],
               2: ["AV:P/PR:N/UI:N", "AV:A/PR:L/UI:P"]}
_V4_MAX_EQ2 = {0: ["AC:L/AT:N"], 1: ["AC:H/AT:N", "AC:L/AT:P"]}
_V4_MAX_EQ3 = {
    0: {0: ["VC:H/VI:H/VA:H/CR:H/IR:H/AR:H"], 1: ["VC:H/VI:H/VA:L/CR:M/IR:M/AR:H", "VC:H/VI:H/VA:H/CR:M/IR:M/AR:M"]},
    1: {0: ["VC:L/VI:H/VA:H/CR:H/IR:H/AR:H", "VC:H/VI:L/VA:H/CR:H/IR:H/AR:H"],
        1: ["VC:L/VI:H/VA:L/CR:H/IR:M/AR:H", "VC:L/VI:H/VA:H/CR:H/IR:M/AR:M", "VC:H/VI:L/VA:H/CR:M/IR:H/AR:M",
            "VC:H/VI:L/VA:L/CR:M/IR:H/AR:H", "VC:L/VI:L/VA:H/CR:H/IR:H/AR:M"]},
    2: {1: ["VC:L/VI:L/VA:L/CR:H/IR:H/AR:H"]},
}
_V4_MAX_EQ4 = {0: ["SC:H/SI:S/SA:S"], 1: ["SC:H/SI:H/SA:H"], 2: ["SC:L/SI:L/SA:L"]}
_V4_MAX_EQ5 = {0: ["E:A"], 1: ["E:P"], 2: ["E:U"]}
# Depth of each macro vector, in severity-distance steps (``max_severity.js``).
_V4_DEPTH_EQ1 = {0: 1, 1: 4, 2: 5}
_V4_DEPTH_EQ2 = {0: 1, 1: 2}
_V4_DEPTH_EQ3EQ6 = {(0, 0): 7, (0, 1): 6, (1, 0): 8, (1, 1): 8, (2, 1): 10}
_V4_DEPTH_EQ4 = {0: 6, 1: 5, 2: 4}


def _metrics(vector: str, prefix: str) -> dict[str, str] | None:
    """Split ``CVSS:x.y/K:V/...`` into ``{K: V}``; None when malformed or a metric repeats."""
    parts = vector.strip().split("/")
    if not parts or parts[0] != prefix:
        return None
    out: dict[str, str] = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if not sep or not key or not value or key in out:
            return None
        out[key] = value
    return out


def roundup_v31(value: float) -> float:
    """Return CVSS v3.1 ``Roundup``: the smallest one-decimal number >= ``value``, in integer arithmetic.

    Args:
        value: A non-negative score.

    Returns:
        The rounded score (``4.02`` -> ``4.1``; ``4.000000000000001`` -> ``4.0``, unlike a float ceiling).
    """
    whole = round(value * 100000)
    if whole % 10000 == 0:
        return whole / 100000.0
    return (math.floor(whole / 10000) + 1) / 10.0


def score_v3(vector: str) -> float | None:
    """Return the CVSS v3.0 / v3.1 base score of ``vector``.

    Args:
        vector: ``CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H`` (temporal and environmental metrics ignored).

    Returns:
        The base score 0.0-10.0, or None when the vector is malformed.
    """
    version = vector.strip().split("/", 1)[0]
    if version not in ("CVSS:3.0", "CVSS:3.1"):
        return None
    m = _metrics(vector, version)
    if m is None or m.get("S") not in ("U", "C"):
        return None
    try:
        w = {k: _V3_WEIGHTS[k][m[k]] for k in _V3_WEIGHTS}
        pr = _V3_PR[m["S"]][m["PR"]]
    except KeyError:
        return None
    iss = 1 - (1 - w["C"]) * (1 - w["I"]) * (1 - w["A"])
    changed = m["S"] == "C"
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
    exploitability = 8.22 * w["AV"] * w["AC"] * pr * w["UI"]
    if impact <= 0:
        return 0.0
    raw = min((1.08 if changed else 1.0) * (impact + exploitability), 10.0)
    return roundup_v31(raw) if version == "CVSS:3.1" else math.ceil(raw * 10 - 1e-9) / 10.0


def _v4_value(m: dict[str, str], metric: str) -> str:
    """Return the effective value of a v4 metric (threat/environmental defaults, modified metrics win)."""
    selected = m.get(metric, "X")
    if metric == "E" and selected == "X":
        return "A"
    if metric in ("CR", "IR", "AR") and selected == "X":
        return "H"
    modified = m.get("M" + metric, "X")
    return modified if modified != "X" else selected


def _v4_macro(m: dict[str, str]) -> tuple[int, int, int, int, int, int]:
    v = lambda k: _v4_value(m, k)   # noqa: E731
    if v("AV") == "N" and v("PR") == "N" and v("UI") == "N":
        eq1 = 0
    elif (v("AV") == "N" or v("PR") == "N" or v("UI") == "N") and v("AV") != "P":
        eq1 = 1
    else:
        eq1 = 2
    eq2 = 0 if v("AC") == "L" and v("AT") == "N" else 1
    if v("VC") == "H" and v("VI") == "H":
        eq3 = 0
    elif v("VC") == "H" or v("VI") == "H" or v("VA") == "H":
        eq3 = 1
    else:
        eq3 = 2
    if v("MSI") == "S" or v("MSA") == "S":
        eq4 = 0
    elif v("SC") == "H" or v("SI") == "H" or v("SA") == "H":
        eq4 = 1
    else:
        eq4 = 2
    eq5 = {"A": 0, "P": 1, "U": 2}[v("E")]
    eq6 = 0 if ((v("CR") == "H" and v("VC") == "H") or (v("IR") == "H" and v("VI") == "H")
                or (v("AR") == "H" and v("VA") == "H")) else 1
    return eq1, eq2, eq3, eq4, eq5, eq6


def _lookup(*eqs: int) -> float | None:
    return _V4_LOOKUP.get("".join(map(str, eqs)))


def _v4_score(m: dict[str, str]) -> float:
    """The FIRST v4.0 algorithm (``cvss_score.js``) on validated metrics."""
    if all(_v4_value(m, k) == "N" for k in ("VC", "VI", "VA", "SC", "SI", "SA")):
        return 0.0
    eq1, eq2, eq3, eq4, eq5, eq6 = _v4_macro(m)
    value = _lookup(eq1, eq2, eq3, eq4, eq5, eq6)
    if value is None:   # not reachable for a valid vector; fail towards the higher neighbour, never below
        return 10.0
    lower1 = _lookup(eq1 + 1, eq2, eq3, eq4, eq5, eq6)
    lower2 = _lookup(eq1, eq2 + 1, eq3, eq4, eq5, eq6)
    if eq3 == 0 and eq6 == 0:
        left, right = _lookup(eq1, eq2, eq3, eq4, eq5, eq6 + 1), _lookup(eq1, eq2, eq3 + 1, eq4, eq5, eq6)
        # as the reference: the left neighbour only when both exist and it scores higher
        lower36 = left if left is not None and right is not None and left > right else right
    elif eq3 == 1 and eq6 == 0:
        lower36 = _lookup(eq1, eq2, eq3, eq4, eq5, eq6 + 1)
    elif eq3 == 2 and eq6 == 1:
        lower36 = _lookup(eq1, eq2, eq3 + 1, eq4, eq5, eq6 + 1)   # does not exist
    else:
        lower36 = _lookup(eq1, eq2, eq3 + 1, eq4, eq5, eq6)
    lower4 = _lookup(eq1, eq2, eq3, eq4 + 1, eq5, eq6)
    lower5 = _lookup(eq1, eq2, eq3, eq4, eq5 + 1, eq6)

    cur = {k: _V4_LEVELS[k][_v4_value(m, k)] for k in _V4_LEVELS}
    dist: dict[str, float] = {}
    for combo in itertools.product(_V4_MAX_EQ1[eq1], _V4_MAX_EQ2[eq2], _V4_MAX_EQ3[eq3][eq6], _V4_MAX_EQ4[eq4],
                                   _V4_MAX_EQ5[eq5]):
        top = dict(p.split(":") for p in "/".join(combo).split("/"))
        dist = {k: cur[k] - _V4_LEVELS[k][top[k]] for k in _V4_LEVELS}
        if all(x >= 0 for x in dist.values()):
            break   # the first highest vector at or above this one (none: the last one tried, as the reference)
    step = 0.1
    parts = (
        (lower1, dist["AV"] + dist["PR"] + dist["UI"], _V4_DEPTH_EQ1[eq1]),
        (lower2, dist["AC"] + dist["AT"], _V4_DEPTH_EQ2[eq2]),
        (lower36, sum(dist[k] for k in ("VC", "VI", "VA", "CR", "IR", "AR")), _V4_DEPTH_EQ3EQ6.get((eq3, eq6), 1)),
        (lower4, dist["SC"] + dist["SI"] + dist["SA"], _V4_DEPTH_EQ4[eq4]),
        (lower5, 0.0, 1),   # EQ5: the proportion is always 0
    )
    existing = [(value - lower) * (d / (depth * step)) for lower, d, depth in parts if lower is not None]
    mean = sum(existing) / len(existing) if existing else 0.0
    result = min(max(value - mean, 0.0), 10.0)
    return math.floor(result * 10 + 0.5) / 10.0   # JavaScript Math.round, half up


def score_v4(vector: str) -> float | None:
    """Return the CVSS v4.0 score of ``vector`` (CVSS-B, or CVSS-BT/BE/BTE when those metrics are present).

    Args:
        vector: ``CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N`` (further metrics allowed).

    Returns:
        The score 0.0-10.0, or None when the vector is malformed or misses a base metric.
    """
    m = _metrics(vector, "CVSS:4.0")
    if m is None:
        return None
    if any(m.get(k) not in allowed for k, allowed in _V4_BASE.items()):
        return None
    checks = {"E": "XAPU", "CR": "XHML", "IR": "XHML", "AR": "XHML", "MSI": "XSHLN", "MSA": "XSHLN"}
    for key, value in m.items():
        if key in checks and value not in checks[key]:
            return None
        if key.startswith("M") and key[1:] in _V4_BASE and value != "X" and value not in _V4_LEVELS.get(key[1:], {}):
            return None
    return _v4_score(m)


def score(vector: str) -> float | None:
    """Return the base score of a CVSS v3.x or v4.0 vector.

    Args:
        vector: The vector string, as in OSV ``severity[].score``.

    Returns:
        The score, or None for another version or a malformed vector.
    """
    text = str(vector or "").strip()
    if text.startswith("CVSS:4.0/"):
        return score_v4(text)
    if text.startswith(("CVSS:3.0/", "CVSS:3.1/")):
        return score_v3(text)
    return None


def band(value: float) -> str:
    """Return the qualitative rating of a CVSS score.

    Args:
        value: 0.0-10.0.

    Returns:
        ``CRITICAL`` (>= 9.0), ``HIGH`` (>= 7.0), ``MODERATE`` (>= 4.0) or ``LOW`` (below; a 0.0 "None"
        score is still a published advisory, so it is LOW rather than unscored).
    """
    if value >= 9.0:
        return "CRITICAL"
    if value >= 7.0:
        return "HIGH"
    if value >= 4.0:
        return "MODERATE"
    return "LOW"
