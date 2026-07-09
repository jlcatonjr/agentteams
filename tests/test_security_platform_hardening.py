"""Guard for the OS-specific security hardening reference templates (2026-07-09).

`@security` gained curated platform hardening baselines for Linux, macOS, and
Windows — the systems-tier companion to the low-level code-screening block. Each
is a rendered reference template (`references/security-<os>-hardening.reference.md`).
This test pins, per platform:
  1. the template exists, is fenced, and cites its (verified) primary sources;
  2. it is REGISTERED in the output plan (the plan is a hand-maintained list, not
     a glob — an unregistered template silently never renders);
  3. it renders through the pipeline with {PROJECT_NAME} substituted;
and once for all three:
  4. the security agent template references all three docs, OS-gated, inside the
     invariant fence (so `--update --merge` propagates the pointer).

See `references/plans/security-low-level-vuln-coverage.report.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agentteams
from agentteams import analyze, ingest, render

_PKG = Path(agentteams.__file__).parent
_TEMPLATES_DIR = _PKG / "templates"
_SECURITY_TEMPLATE = _TEMPLATES_DIR / "universal" / "security.template.md"
_EXAMPLES = _PKG.parent / "examples"

# Per-platform expectations. Domain + source samples are drawn from the authored,
# web-verified content; CVEs are the landmark exploit classes each doc names.
_PLATFORMS = {
    "linux": {
        "template": "security-linux-hardening.reference.template.md",
        "fence": "linux_hardening",
        "output": "references/security-linux-hardening.reference.md",
        "title": "# Linux Security Hardening — ",
        "domains": [
            "Kernel hardening", "Privilege-escalation", "Mandatory Access Control",
            "Namespaces", "seccomp", "memory-protection",
            "systemd service", "Filesystem", "Auditing",
        ],
        "sources": [
            "https://kspp.github.io/",
            "https://man7.org/linux/man-pages/man7/capabilities.7.html",
            "https://www.cisa.gov/news-events/alerts/2022/03/15/updated-kubernetes-hardening-guide",
            "https://wiki.debian.org/Hardening",
            "https://nvd.nist.gov/",
        ],
        "cves": ["CVE-2016-5195", "CVE-2022-0847", "CVE-2019-5736", "CVE-2021-4034"],
        "render_source": "https://kspp.github.io/",
    },
    "macos": {
        "template": "security-macos-hardening.reference.template.md",
        "fence": "macos_hardening",
        "output": "references/security-macos-hardening.reference.md",
        "title": "# macOS Security Hardening — ",
        "domains": [
            "System integrity", "Privilege-escalation", "application control",
            "Application isolation", "Capability / process restriction",
            "memory-protection", "Service / daemon hardening", "Filesystem", "Auditing",
        ],
        "sources": [
            "https://support.apple.com/guide/security/system-integrity-protection-secb7ea06b49/web",
            "https://developer.apple.com/documentation/security/app-sandbox",
            "https://github.com/usnistgov/macos_security",
            "https://www.cisecurity.org/benchmark/apple_os",
            "https://nvd.nist.gov/",
        ],
        "cves": ["CVE-2021-30892", "CVE-2021-30657", "CVE-2021-30970", "CVE-2022-42821"],
        "render_source": "https://developer.apple.com/documentation/security/app-sandbox",
    },
    "windows": {
        "template": "security-windows-hardening.reference.template.md",
        "fence": "windows_hardening",
        "output": "references/security-windows-hardening.reference.md",
        "title": "# Windows Security Hardening — ",
        "domains": [
            "system-integrity", "Privilege-escalation", "application control",
            "Application isolation", "Process mitigation", "memory-protection",
            "Service / daemon hardening", "Filesystem", "Auditing",
        ],
        "sources": [
            "https://learn.microsoft.com/en-us/windows/security/hardware-security/enable-virtualization-based-protection-of-code-integrity",
            "https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard",
            "https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/",
            "https://www.cisecurity.org/benchmark/microsoft_windows_desktop",
            "https://nvd.nist.gov/",
        ],
        "cves": ["CVE-2021-34527", "CVE-2021-1732", "CVE-2021-36934", "CVE-2020-1472"],
        "render_source": "https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard",
    },
}


def _template_text(platform: dict) -> str:
    return (_TEMPLATES_DIR / "universal" / platform["template"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(_PLATFORMS))
def test_template_exists_and_is_fenced(name: str) -> None:
    p = _PLATFORMS[name]
    path = _TEMPLATES_DIR / "universal" / p["template"]
    assert path.exists(), f"{name} hardening template is missing"
    text = path.read_text(encoding="utf-8")
    begin = f"<!-- AGENTTEAMS:BEGIN {p['fence']} v=1 -->"
    end = f"<!-- AGENTTEAMS:END {p['fence']} -->"
    assert text.count(begin) == 1 and text.count(end) == 1
    assert text.index(begin) < text.index(end)
    assert "{PROJECT_NAME}" in text  # title placeholder present


@pytest.mark.parametrize("name", sorted(_PLATFORMS))
def test_template_covers_domains_and_cites_sources(name: str) -> None:
    p = _PLATFORMS[name]
    text = _template_text(p)
    missing_domains = [d for d in p["domains"] if d not in text]
    assert not missing_domains, f"{name} reference missing domains: {missing_domains}"
    missing_sources = [u for u in p["sources"] if u not in text]
    assert not missing_sources, f"{name} reference missing verified sources: {missing_sources}"
    missing_cves = [c for c in p["cves"] if c not in text]
    assert not missing_cves, f"{name} reference missing landmark CVEs: {missing_cves}"


@pytest.mark.parametrize("name", sorted(_PLATFORMS))
def test_template_registered_in_output_plan(name: str) -> None:
    brief = _EXAMPLES / "software-project" / "brief.json"
    description = ingest.load(brief, scan_project=False)
    manifest = analyze.build_manifest(description, framework="copilot-vscode")
    planned = {f["path"] for f in manifest["output_files"]}
    assert _PLATFORMS[name]["output"] in planned, f"{name} reference not in output plan"


@pytest.mark.parametrize("name", sorted(_PLATFORMS))
def test_template_renders_with_project_name(name: str) -> None:
    p = _PLATFORMS[name]
    brief = _EXAMPLES / "software-project" / "brief.json"
    description = ingest.load(brief, scan_project=False)
    manifest = analyze.build_manifest(description, framework="copilot-vscode")
    project_name = manifest["project_name"]

    rendered = dict(render.render_all(manifest, templates_dir=_TEMPLATES_DIR))
    assert p["output"] in rendered, f"{p['output']} was not rendered"
    body = rendered[p["output"]]

    assert p["title"] + project_name in body   # {PROJECT_NAME} substituted
    assert "{PROJECT_NAME}" not in body         # no literal token leaks
    assert f"<!-- AGENTTEAMS:BEGIN {p['fence']} v=1 -->" in body
    assert p["render_source"] in body


def test_security_agent_template_references_all_platform_docs() -> None:
    """The security agent template must point at all three platform references,
    inside the invariant fence, OS-gated (not unconditional)."""
    text = _SECURITY_TEMPLATE.read_text(encoding="utf-8")
    begin = text.index("<!-- AGENTTEAMS:BEGIN security_rules_invariant")
    end = text.index("<!-- AGENTTEAMS:END security_rules_invariant")
    for p in _PLATFORMS.values():
        pos = text.index(p["output"])
        assert begin < pos < end, f"{p['output']} pointer must be inside the invariant fence"
    assert "OS-specific deployment targets" in text
