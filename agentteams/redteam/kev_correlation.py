"""kev_correlation.py — read the @security agent's threat-intel cache for report.py's advisory
correlation (improvement item I3).

Deliberately NOT one of the redteam engine's no-except execution-path modules
(``registry.py``, ``runner.py``, ``cycle.py``, ``corpus.py``, ``checks_static.py``,
``checks_report.py``, ``selfaudit.py``, ``report.py``, ``realcopy.py`` —
see ``tests/test_redteam_audit_workflow.py::_EXECUTION_PATH_MODULES``): those modules must
propagate any exception so a raising probe is classified as a broken harness, never silently
absorbed. This module's whole purpose is the opposite — degrade gracefully when an optional,
best-effort correlation source is absent or malformed — so it lives outside that boundary, the
same way ``freshness.py`` and ``coverage.py`` already do.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Where the @security agent's threat-intel fetch already writes, relative to a project root.
KEV_CACHE_REL = Path("references") / "security-vulnerability-watch.json"


def load_kev_terms(root: Path) -> frozenset[str]:
    """Read the @security agent's already-fetched threat-intel cache, if one exists.

    A NEW network call in the standing free audit would break its "network-independent and
    deterministic" property (the same constraint the freshness helper in I2 respects) — this
    only ever reads a file another command already wrote. Degrades to an empty set on any
    absence or malformation; never raises.

    Args:
        root: Repository (or generated-team) root.

    Returns:
        Product/vendor/CVE names from ``vulnerabilities`` plus package names from
        ``osv_packages`` — the terms :func:`agentteams.redteam.report.render_remediation_skeleton`
        does a best-effort substring match against. Empty when the cache is absent or unreadable.
    """
    path = root / KEV_CACHE_REL
    if not path.exists():
        return frozenset()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    terms: set[str] = set()
    for v in payload.get("vulnerabilities", []) or []:
        if isinstance(v, dict):
            terms.update(str(v[k]) for k in ("vendor", "product") if v.get(k))
    for p in payload.get("osv_packages", []) or []:
        if isinstance(p, dict) and p.get("package"):
            terms.add(str(p["package"]))
    return frozenset(terms)
