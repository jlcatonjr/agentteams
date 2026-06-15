# {TOOL_NAME} — CLI Reference — {PROJECT_NAME}

> Operational reference for the **{TOOL_NAME} {TOOL_VERSION}** command-line tool in {PROJECT_NAME}.
> {TOOL_NAME} is a tool the team *uses* — not an agent. Configuration and execution
> should follow the procedures below; route security-rule changes through `@security`.

**Tool:** `{TOOL_NAME}` `{TOOL_VERSION}`
**Configuration files:** `{TOOL_CONFIG_FILES}`

---

## Official Documentation

Consult the official {TOOL_NAME} documentation at: {TOOL_DOCS_URL}

Verify CLI flags, configuration options, and rule/plugin behavior against this documentation.

## Key API Surface

<!-- AGENTTEAMS:BEGIN tool_api_surface v=1 -->
{TOOL_API_SURFACE}
<!-- AGENTTEAMS:END tool_api_surface -->

<!-- Document the primary CLI commands, configuration file format, rule/plugin system, and output formats for {TOOL_NAME}. -->

## Common Patterns & Pitfalls

{TOOL_COMMON_PATTERNS}

<!-- Document common configurations, integration patterns, and known issues for {TOOL_NAME} {TOOL_VERSION}. -->

---

## Config Management

Current configuration lives in: `{TOOL_CONFIG_FILES}`

Before any configuration change:
1. Read the current configuration file
2. Verify the proposed change is compatible with `{TOOL_VERSION}`
3. If the change disables security-related rules or checks, request clearance from `@security`
4. Back up the existing config by saving as `<filename>.backup` before writing
5. Apply the change and verify the tool runs successfully

## Command-Line Usage

1. Run `{TOOL_NAME}` with the project's standard flags and configuration
2. Capture stdout and stderr
3. Check exit code — non-zero exit indicates findings or errors
4. Parse output to identify actionable items vs informational messages

## Output Interpretation

After every execution:
- Categorise findings by severity (error, warning, info)
- Identify auto-fixable issues vs those requiring manual intervention
- For auto-fixable issues, apply the fix and re-run to verify
- Report remaining issues with file paths and line numbers

## Integration

- Pre-commit hooks: verify {TOOL_NAME} runs on staged files before commit
- CI pipeline: ensure {TOOL_NAME} runs in CI with the same configuration as local development
- Editor plugins: confirm real-time feedback is configured where supported

## When to Involve the Team

Raise with `@orchestrator` (and `@security` for rule changes) when:
- {TOOL_NAME} exits with an unexpected error (not a findings report)
- Configuration conflicts with another tool in the project
- Security-related rules need to be disabled
