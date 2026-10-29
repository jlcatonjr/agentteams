"""Known-vulnerability scanning and remediation for repositories and servers.

``python -m agentteams.vulnscan`` finds dependencies and runtimes with published
vulnerabilities and drives their remediation:

- ``scan PATH`` reads lockfiles and pinned manifests (PyPI, npm, Packagist, crates.io) under a
  repository or a parent directory and checks every exact version against OSV.dev.
- ``inventory LOG`` judges what a server reported. The bundled server kit's audit script
  prints the packages and runtimes installed on a host; this command applies the same
  engine to them.
- ``fix PATH`` turns findings into edits: the smallest clean release (no release-age
  cooldown since 2026-10-26), with major-version jumps gated. It is a dry run unless ``--apply``.
- ``daily`` runs all of it on a schedule against the repos and servers named in the
  user's own config file, which lives outside every repository.
- ``schedule`` installs that daily run as a launchd agent, a systemd user timer, or a
  cron entry. ``server-kit`` exports the shell scripts used on Linux hosts.
- Agent mode (opt-in): ``agent-run`` runs the team in a credential-free container behind a
  spend-capped gateway (:mod:`.agent_run`, :mod:`.gateway`). The agents queue requests through
  the ``vulnscan`` MCP server (:mod:`.mcp`). The host-side executor (:mod:`.executor`) acts only
  under the operator's signed standing grant (:mod:`.grant`) and records every action in a
  MAC-chained ledger (:mod:`.ledger`). ``review`` is the after-action review.

Vulnerability knowledge is never hardcoded. Package advisories come from OSV.dev, the
Node.js security-release flags from nodejs.org, and runtime end-of-life dates from
endoflife.date, so the same code covers the next disclosure wave without edits.

Stdlib only. Nothing here records anything about the user's machines in the package.
Configuration and results live under the XDG config and state directories.
"""

from __future__ import annotations

REPORT_SCHEMA_VERSION = 1   # integer; readers ignore unknown top-level keys
