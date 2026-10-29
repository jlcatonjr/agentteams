<!--
Delete any section that doesn't apply to this PR. Most PRs need only the top section.
-->

## Summary

<!-- What changed and why. Link the issue on which this pull request was invited: Closes #123 -->

<!--
First pull request? The checklist this is reviewed against:
https://jlcatonjr.github.io/agentteams/contribute/what-your-pr-needs/
- One change, linked to its issue.
- `pytest tests/` passes (or say which failures you could not resolve).
- New behavior has a test; no new runtime dependency.
- CHANGELOG entry under [Unreleased] if users are affected.
- Public API/CLI changed? Update docs_src/api-reference/ or docs_src/cli-reference.md and regenerate agentteams.1.
- vulnscan changed (a config key, notification, command, option or module)? Update
  docs_src/api-reference/vulnscan.md; tests/test_vulnscan_doc_conformity.py checks it.
- Docs changed? `mkdocs build --strict` and `python scripts/check_docs_site.py pages _site` pass.
-->

## Test plan

<!-- How this was verified. -->

---

## Adding a new red-team probe? (item I10)

Delete this section if not applicable. A new probe in
`tests/constitutional_redteam_battery.py` should satisfy the same signal-over-noise bar
CISA's KEV catalog uses for what it lists (full rationale:
`references/redteam-audit.procedure.md` § "Before revising the corpus"):

- [ ] **Traces to a real technique** — a real observed incident, or an external taxonomy
      entry (MITRE ATLAS technique id / OWASP LLM Top-10 risk id). Tag it via the probe's
      `external_refs` field where a defensible mapping exists — see
      `agentteams/redteam/coverage.py`.
- [ ] **Has a clear, reproducible test procedure** — another maintainer can read the probe
      and understand what it attacks and why the outcome means what it claims.
- [ ] **Has a paired negative control**, or a recorded exemption in
      `references/redteam-uncontrolled-probes.csv` (enforced mechanically by
      `registry.validate_registration()` — CI will refuse an unpaired probe either way).
