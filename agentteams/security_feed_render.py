"""security_feed_render.py — render UNTRUSTED external feed text into agent markdown.

Carved out of ``security_refs`` when an ordinary addition pushed that module against the CH-07
ceiling. The seam is not "all the formatters" — it is the trust boundary. Everything here renders
text authored *outside this project* (CISA KEV, MITRE CVE, OSV.dev advisory ids), so every value
passes :func:`_sanitize_feed_text` first. The formatters left behind in ``security_refs``
(``_format_llm_threats``, ``_format_source_registry``, ``_format_control_evidence_matrix``)
render data this project itself authors and need no such treatment.

Splitting on that line means "did this string come from outside?" is answered by which module a
function lives in, rather than by remembering to check.

``security_refs`` re-exports these names, so no existing import changed.
"""

from __future__ import annotations

_FEED_FIELD_MAX_CHARS = 400


def _sanitize_feed_text(value: object, *, limit: int = _FEED_FIELD_MAX_CHARS) -> str:
    """Neutralise one externally-authored feed value before it is rendered into an agent file.

    KEV/CVE/OSV free-text fields (``vulnerabilityName``, ``requiredAction``, ``vendorProject``, …)
    are written by vulnerability reporters and vendors, not by this project, and they are
    interpolated into the ``threat_intelligence`` fence of ``security.template.md`` — the file
    that defines the highest-privilege agent in the team. That makes them an indirect
    prompt-injection channel (OWASP LLM01/LLM03, both of which this module's own payload
    enumerates), so they are treated as untrusted input rather than as data this project authored.

    Three neutralisations, each closing a specific hole:

    - **Whitespace collapses to single spaces.** A newline would let a value open a markdown
      heading, start a list item, or place text at column 0 — i.e. escape the list item it was
      supposed to occupy and address the agent directly.
    - **HTML-comment delimiters are defanged.** This is the sharp one:
      :data:`agentteams.fences._FENCE_END_RE` matches with ``.search()`` against each line, so an
      *inline* ``<!-- AGENTTEAMS:END threat_intelligence -->`` inside a feed value would close the
      fence early and silently restructure the emitted file. Collapsing newlines alone does not
      prevent this.
    - **Backticks become apostrophes.** The formatters wrap CVE ids in inline code spans; a
      backtick in a value would terminate one and let the remainder render as prose.

    Length is bounded last so a pathological field cannot dominate the rendered section.

    Args:
        value: Raw feed value. Non-string input is coerced; ``None`` becomes ``""``.
        limit: Maximum characters retained before an ellipsis is appended.

    Returns:
        A single-line string safe to interpolate into fenced markdown.
    """
    if value is None:
        return ""
    text = " ".join(str(value).split())
    text = text.replace("<!--", "<!––").replace("-->", "––>")
    text = text.replace("`", "'")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _format_threat_summary(vulns: list[dict]) -> str:
    """Render the threat summary from canonical-shape vulnerability records.

    Enrichment (EPSS/CVSS) is embedded inline per-record (``epss``, ``epss_percentile``,
    ``cvss_score``, ``cvss_severity``) rather than passed as separate lookup maps — this is what
    lets an offline cache read render identically to a live fetch: the enrichment travels with the
    record instead of needing to be separately restored (see
    references/plans/security-vuln-cache-normalization.plan.md).
    """
    if not vulns:
        return "- No live vulnerability data was available; consult cached reference file."

    lines: list[str] = []
    for vuln in vulns:
        # Every field below originates in an external feed — see _sanitize_feed_text.
        cve = _sanitize_feed_text(vuln.get("cve")) or "UNKNOWN-CVE"
        epss_score = _sanitize_feed_text(vuln.get("epss"), limit=32)
        pct = _sanitize_feed_text(vuln.get("epss_percentile"), limit=32)
        epss_text = ""
        if epss_score:
            pct_text = f", percentile {pct}" if pct else ""
            epss_text = f" | EPSS {epss_score}{pct_text}"
        cvss_text = ""
        cvss_score = _sanitize_feed_text(vuln.get("cvss_score"), limit=32)
        if cvss_score:
            cvss_severity = _sanitize_feed_text(vuln.get("cvss_severity"), limit=32)
            cvss_text = f" | CVSS {cvss_score}" + (f" {cvss_severity}" if cvss_severity else "")
        vendor = _sanitize_feed_text(vuln.get("vendor")) or "Unknown vendor"
        product = _sanitize_feed_text(vuln.get("product"))
        name = _sanitize_feed_text(vuln.get("name")) or "Known exploited vulnerability"
        date_added = _sanitize_feed_text(vuln.get("date_added"), limit=32) or "n/a"
        lines.append(
            f"- `{cve}` | {vendor} {product} | {name} | added {date_added}{epss_text}{cvss_text}"
        )
    return "\n".join(lines)


def _format_prevention_playbook(vulns: list[dict]) -> str:
    """Render the prevention playbook from canonical-shape vulnerability records (see
    _format_threat_summary's docstring for why these are canonical, not live-API, shape)."""
    actions: list[str] = []
    for vuln in vulns:
        # Vendor/CISA-authored free text, rendered into the security agent's own file.
        action = _sanitize_feed_text(vuln.get("required_action"))
        if action and action not in actions:
            actions.append(action)
        if len(actions) >= 4:
            break

    base = [
        "- Prioritize remediation for KEV-listed CVEs as actively exploited threats.",
        "- Triage by exploitability (EPSS) and internet exposure before lower-risk backlog items.",
        "- Enforce patch windows with owner, SLA, and verification evidence for each critical CVE.",
        "- When patching is blocked, define compensating controls (WAF rules, ACL tightening, feature disablement).",
        "- Add detections for exploitation attempts and verify telemetry coverage for affected assets.",
    ]
    if actions:
        base.append("- Vendor/CISA required actions:")
        base.extend([f"  - {a}" for a in actions])
    return "\n".join(base)



def _format_osv_summary(findings: list[dict]) -> str:
    """Return a markdown-formatted OSV package vulnerability summary."""
    if not findings:
        return "- No package-level vulnerabilities found in OSV.dev for the declared project dependencies."
    lines: list[str] = []
    for f in findings:
        # Advisory ids come from OSV.dev; package/ecosystem echo the project's own manifest but
        # are sanitized alongside them so one code path governs everything feed-derived.
        ids = ", ".join(_sanitize_feed_text(i, limit=64) for i in f["top_ids"]) if f["top_ids"] else "n/a"
        plural = "y" if f["vuln_count"] == 1 else "ies"
        package = _sanitize_feed_text(f["package"], limit=128)
        ecosystem = _sanitize_feed_text(f["ecosystem"], limit=64)
        lines.append(
            f"- **{package}** ({ecosystem}): "
            f"{f['vuln_count']} known vulnerabilit{plural} — top IDs: {ids}"
        )
    return "\n".join(lines)


