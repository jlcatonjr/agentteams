# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.x     | Yes       |
| < 1.0   | No        |

## Reporting a Vulnerability

Please report suspected vulnerabilities privately by opening a GitHub Security Advisory draft for this repository.

If private advisory flow is unavailable, open a private coordination issue with maintainers and include:

1. A clear vulnerability description.
2. Affected files/versions.
3. Reproduction steps or proof of concept.
4. Impact and suggested remediation.

## Response Targets

| Severity | Initial Triage Target | Remediation Target |
| -------- | --------------------- | ------------------ |
| Critical | 24 hours              | 7 days             |
| High     | 2 business days       | 14 days            |
| Medium   | 5 business days       | 30 days            |
| Low      | 10 business days      | 60 days            |

## Security Maintenance Cadence

This repository attempts daily security maintenance through the scheduled bridge workflow (`.github/workflows/bridge-maintenance.yml`), which invokes `scripts/run_daily_security_maintenance.sh` as its first integrated step.

The maintenance path is warn-and-continue; operators should review `tmp/bridge-maintenance/summary.md` for step-level outcomes.

The standalone security workflow (`.github/workflows/security-maintenance.yml`) is retained as a manual fallback (`workflow_dispatch`) for ad-hoc reruns and incident response.
