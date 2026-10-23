"""security_refs_data.py — Static security reference tables used by :mod:`agentteams.security_refs`.

Data only (no fetching): the OWASP LLM Top 10, the LLM security reference list and the control-evidence
rows. Split out of ``security_refs.py`` to keep that module under the file-size limit (CH-07); updated in
source when the upstream lists change.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# OWASP LLM Top 10 – 2025 edition  (static; updated in source on major releases)
# Reference: https://owasp.org/www-project-top-10-for-large-language-model-applications/
# ---------------------------------------------------------------------------
_OWASP_LLM_TOP10: list[dict[str, str]] = [
    {
        "id": "LLM01:2025",
        "name": "Prompt Injection",
        "summary": (
            "Attacker-controlled input overrides or hijacks LLM instructions, "
            "causing unintended actions including data exfiltration and privilege escalation."
        ),
    },
    {
        "id": "LLM02:2025",
        "name": "Sensitive Information Disclosure",
        "summary": (
            "LLM inadvertently reveals PII, credentials, or proprietary data from training "
            "or context when prompted directly or through side-channel extraction."
        ),
    },
    {
        "id": "LLM03:2025",
        "name": "Supply Chain Vulnerabilities",
        "summary": (
            "Compromised models, datasets, plugins, or integrations introduce malicious "
            "behaviour that bypasses standard code review and testing pipelines."
        ),
    },
    {
        "id": "LLM04:2025",
        "name": "Data and Model Poisoning",
        "summary": (
            "Adversarial manipulation of training or fine-tuning data degrades model "
            "integrity, introduces backdoors, or embeds biased responses."
        ),
    },
    {
        "id": "LLM05:2025",
        "name": "Improper Output Handling",
        "summary": (
            "LLM-generated content passed unsanitised to downstream systems causes "
            "XSS, SSRF, code injection, or command execution."
        ),
    },
    {
        "id": "LLM06:2025",
        "name": "Excessive Agency",
        "summary": (
            "An LLM agent operates with overly broad permissions or autonomy, amplifying "
            "the blast radius of prompt injection or logic errors to destructive real-world actions."
        ),
    },
    {
        "id": "LLM07:2025",
        "name": "System Prompt Leakage",
        "summary": (
            "The system prompt (including confidential instructions and secrets) is "
            "extracted through adversarial queries, revealing business logic or credentials."
        ),
    },
    {
        "id": "LLM08:2025",
        "name": "Vector and Embedding Weaknesses",
        "summary": (
            "Poisoned embeddings or RAG data stores cause the model to retrieve and "
            "act on attacker-controlled content, enabling indirect prompt injection at scale."
        ),
    },
    {
        "id": "LLM09:2025",
        "name": "Misinformation",
        "summary": (
            "Hallucinated or factually incorrect LLM outputs are acted upon without "
            "verification, leading to flawed decisions, compliance violations, or reputational harm."
        ),
    },
    {
        "id": "LLM10:2025",
        "name": "Unbounded Consumption",
        "summary": (
            "Uncontrolled LLM inference requests exhaust computational resources, "
            "enabling denial-of-service or cost-exhaustion attacks."
        ),
    },
]

#: Authoritative LLM/AI security reference URLs
_LLM_SECURITY_REFERENCES: list[dict[str, str]] = [
    {
        "name": "OWASP LLM Top 10 (2025)",
        "url": "https://owasp.org/www-project-top-10-for-large-language-model-applications/",
        "description": "Canonical taxonomy of the ten most critical LLM application security risks.",
    },
    {
        "name": "MITRE ATLAS",
        "url": "https://atlas.mitre.org/",
        "description": "Adversarial Threat Landscape for AI Systems — ML-specific attack techniques and mitigations.",
    },
    {
        "name": "Claude Security (Anthropic)",
        "url": "https://code.claude.com/docs/en/security",
        "description": "Anthropic-published security controls and guidance for Claude deployments.",
    },
    {
        "name": "NIST AI Risk Management Framework",
        "url": "https://airc.nist.gov/",
        "description": "NIST AI RMF — governance framework for trustworthy and responsible AI systems.",
    },
    {
        "name": "ENISA Multilayer Framework for Good Cybersecurity Practices for AI",
        "url": "https://www.enisa.europa.eu/publications/multilayer-framework-for-good-cybersecurity-practices-for-ai",
        "description": "EU guidance on securing AI systems across design, development, and deployment.",
    },
]

#: Control-to-test evidence matrix used to keep control claims executable.
_CONTROL_EVIDENCE_ROWS: list[dict[str, str]] = [
    {
        "control_id": "CTRL-01",
        "layer": "operational-fail-safe",
        "test_id": "tests/test_build_team_security_gates.py::test_gate_blocks_missing_required_header_columns",
        "enforcement_point": "build_team.py security decision gate",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-02",
        "layer": "secrets-and-data-loss-prevention",
        "test_id": "tests/test_scan.py::test_detect_high_entropy_secret_like_token",
        "enforcement_point": "agentteams.scan layered detector",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-03",
        "layer": "change-control-gates",
        "test_id": "tests/test_build_team_security_gates.py::test_gate_accepts_current_repository_schema",
        "enforcement_point": "build_team.py schema compatibility gate",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-04",
        "layer": "threat-intel-artifact-integrity",
        "test_id": "tests/test_security_refs.py::test_build_security_placeholders_offline_from_cache",
        "enforcement_point": "security_refs threat-intel cache path",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-05",
        "layer": "prompt-injection-defense",
        "test_id": "tests/test_security_refs.py::test_build_security_placeholders_online",
        "enforcement_point": "security_refs LLM threat baseline",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-06",
        "layer": "agentic-permission-boundaries",
        "test_id": "tests/test_build_team_security_gates.py::test_action_matches_tokenized_action_names",
        "enforcement_point": "build_team.py action matching boundary",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-07",
        "layer": "threat-intel-freshness-governance",
        "test_id": "tests/test_security_refs.py::test_build_security_placeholders_nvd_enrichment",
        "enforcement_point": "security_refs live CVSS/EPSS enrichment",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-08",
        "layer": "scoring-completeness",
        "test_id": "tests/test_security_refs.py::test_build_security_placeholders_osv_packages",
        "enforcement_point": "security_refs OSV enrichment",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-09",
        "layer": "stale-data-signaling",
        "test_id": "tests/test_security_refs.py::test_build_security_placeholders_offline_from_cache",
        "enforcement_point": "security_refs stale-cache warning",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-10",
        "layer": "continuous control drift",
        "test_id": "scripts/run_daily_security_maintenance.sh",
        "enforcement_point": "daily security maintenance pipeline (.github/workflows/security-maintenance.yml; scheduled 09:00 EDT + workflow_dispatch fallback)",
        "status": "implemented",
    },
    {
        "control_id": "CTRL-11",
        "layer": "low-level-vuln-awareness",
        "test_id": "tests/test_security_lowlevel_coverage.py::test_template_lists_low_level_classes",
        "enforcement_point": "security.template.md low-level vulnerability screening block",
        "status": "implemented",
    },
]
