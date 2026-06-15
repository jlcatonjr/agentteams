"""test_security_waiver_docs.py — P2c doc-drift guard (W2).

Pins ``docs_src/security-hardening-guide.md`` to the code's real waiver schema so the
W2 drift cannot silently recur. W2 happened *because* the schema was hand-copied into
the docs and drifted: the docs advertised a 5-column CSV (``issued_at,expires_at,
reason,approver,hmac_signature``) + a comma-joined HMAC that never matched the code's
11-column CSV + pipe-joined 9-field HMAC. The authoritative source is
``agentteams/cli/security_gate.py``; these guards fail if the doc and code disagree on
the CSV columns or the HMAC payload field order.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.cli.security_gate import (
    _SECURITY_WAIVER_REQUIRED_COLUMNS,
    _WAIVER_SIGNATURE_FIELDS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs_src" / "security-hardening-guide.md"


def _csv_block_headers(text: str) -> list[set[str]]:
    """Return the column-name set of every ```csv fenced block's first line."""
    headers: list[set[str]] = []
    for block in re.findall(r"```csv\n(.*?)```", text, flags=re.DOTALL):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if lines:
            headers.append({col.strip() for col in lines[0].split(",")})
    return headers


def test_doc_waiver_csv_header_matches_code() -> None:
    """The doc's waiver CSV header (as a set) must equal the code's required columns."""
    headers = _csv_block_headers(DOC.read_text(encoding="utf-8"))
    assert set(_SECURITY_WAIVER_REQUIRED_COLUMNS) in headers, (
        "No ```csv block in security-hardening-guide.md matches the code's waiver "
        f"columns {sorted(_SECURITY_WAIVER_REQUIRED_COLUMNS)}. Found header sets: "
        f"{[sorted(h) for h in headers]}. Update the doc to the real schema."
    )


def test_doc_hmac_payload_matches_code() -> None:
    """The doc must contain the exact pipe-joined HMAC payload field order."""
    payload = "|".join(_WAIVER_SIGNATURE_FIELDS)
    assert payload in DOC.read_text(encoding="utf-8"), (
        f"security-hardening-guide.md must document the real HMAC payload '{payload}' "
        "(pipe-joined, excluding timestamp + signature)."
    )


def test_doc_references_code_as_authoritative() -> None:
    """CH-14: the doc points to security_gate.py as the single source of truth."""
    assert "security_gate.py" in DOC.read_text(encoding="utf-8")


def test_doc_drops_the_fictional_legacy_schema() -> None:
    """Anti-regression for W2: the old 5-column header string must be gone."""
    assert (
        "issued_at,expires_at,reason,approver,hmac_signature"
        not in DOC.read_text(encoding="utf-8")
    )
