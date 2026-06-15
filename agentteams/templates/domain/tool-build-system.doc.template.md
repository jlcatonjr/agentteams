# {TOOL_NAME} — Build System Reference — {PROJECT_NAME}

> Operational reference for the **{TOOL_NAME} {TOOL_VERSION}** build system in {PROJECT_NAME}.
> {TOOL_NAME} is infrastructure the team *uses* — not an agent. Build-config and
> dependency changes should follow the procedures below; route dependency-source or
> registry changes through `@security`.

**Build tool:** `{TOOL_NAME}` `{TOOL_VERSION}`
**Configuration files:** `{TOOL_CONFIG_FILES}`

---

## Official Documentation

Consult the official {TOOL_NAME} documentation at: {TOOL_DOCS_URL}

Verify build configuration options, dependency specifications, and plugin APIs against this documentation.

## Key API Surface

<!-- AGENTTEAMS:BEGIN tool_api_surface v=1 -->
{TOOL_API_SURFACE}
<!-- AGENTTEAMS:END tool_api_surface -->

<!-- Document the primary CLI commands, configuration directives, plugin hooks, and build lifecycle stages for {TOOL_NAME}. -->

## Common Patterns & Pitfalls

{TOOL_COMMON_PATTERNS}

<!-- Document common build configurations, dependency management patterns, and known issues for {TOOL_NAME} {TOOL_VERSION}. -->

---

## Config Management

Current configuration lives in: `{TOOL_CONFIG_FILES}`

Before any configuration change:
1. Read the current configuration file
2. Verify the proposed change is compatible with `{TOOL_VERSION}`
3. If the change modifies dependency sources or registry URLs, request clearance from `@security`
4. Back up the existing config before writing
5. Apply the change and verify a clean build completes

## Build Procedure

1. Read current config from `{TOOL_CONFIG_FILES}` to confirm expected state
2. Run the build command
3. Capture stdout and stderr
4. Check exit code — non-zero exit is a failure; log and escalate
5. Verify output artifacts exist in expected locations

## Dependency Management

1. Pin dependency versions explicitly where possible
2. Audit new dependencies for known vulnerabilities before adding
3. Document the purpose of each dependency in comments or documentation
4. Run full test suite after any dependency change

## Verification

After every build:
- All expected output artifacts are present
- Output is not empty (zero-byte output indicates silent failure)
- No new warning or error lines compared to previous successful build

## When to Involve the Team

Raise with `@orchestrator` (and `@security` for dependency sources) when:
- The build fails on two consecutive runs after config review
- Dependency resolution conflicts cannot be resolved
- Security-sensitive build settings require modification
