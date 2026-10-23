# `security_refs_data` — AgentTeamsModule

Static security reference tables used by [`security_refs`](security-refs.md).

Data only: nothing here fetches, parses or renders. The tables were split out of `security_refs.py`
to keep that module under the file-size limit, and `security_refs` re-exports them under the same
names, so `security_refs._OWASP_LLM_TOP10` and the other two still work for existing callers.

> *Source: `agentteams/security_refs_data.py`*

---

## Tables

| Name | Type | Content |
|---|---|---|
| `_OWASP_LLM_TOP10` | `list[dict[str, str]]` | The OWASP Top 10 for LLM applications (2025 edition): one entry per risk |
| `_LLM_SECURITY_REFERENCES` | `list[dict[str, str]]` | Curated LLM security references rendered with the threat summary |
| `_CONTROL_EVIDENCE_ROWS` | `list[dict[str, str]]` | Control-to-evidence rows: each control, its status and the test that evidences it |

`tests/test_security_lowlevel_coverage.py` checks that every `_CONTROL_EVIDENCE_ROWS` test id points
at a real test, and that the OWASP list keeps exactly 10 entries.

## Updating

Edit the tables in source when an upstream list changes (a new OWASP edition, a new reference, a new
control). They are not refreshed at run time; the live feeds are fetched by `security_refs`.
