# `host_features`

Parse and validate `<namespace>:<feature>` opt-in subselector tokens that gate emission of host-specific artifacts (Claude hooks, subagents, schedule routines, todo-projection skill, cache-aware CLAUDE.md, etc.). Default emission is unchanged when no subselectors are passed.

The flag is `--target-host-features TOKENS` (comma-separated). Tokens flow onto `manifest["host_features"]` and are consumed by feature-gated emitters such as `bridge_subagents`, `hooks_emit`, `schedule_emit`, and `instructions_split`.

## Public Surface

```python
parse_tokens(raw: str | None) -> list[str]
```
Parse a CSV string of subselectors into a normalized, deduped list. Empty / `None` input returns `[]`. Each surviving token is run through `validate`; the function raises `HostFeatureError` on malformed input.

```python
validate(token: str) -> None
```
Validate a single `<ns>:<feature>` token against a fixed allow-list. The namespace may itself contain a colon (e.g. `bridge:copilot-vscode-to-claude:subagents`); validation splits on the last colon to separate the feature from the namespace. Raises `HostFeatureError` when the token has no colon at all, when the namespace is not one of the enumerated `_VALID_NAMESPACES`, or when the feature is not in that namespace's `_KNOWN_FEATURES` set — e.g. `claude:not-a-real-feature` raises even though it is syntactically well-formed.

```python
is_enabled(features: Iterable[str], namespace: str, feature: str) -> bool
```
Return `True` iff `<namespace>:<feature>` is present in the active set. Provided as a convenience membership check; note that, in practice, the feature-gated emitters do **not** call `is_enabled` — they perform their own literal membership tests against the active feature list (e.g. `mcp_emit.mcp_enabled`). It is not a single enforced check point.

```python
class HostFeatureError(ValueError): ...
```
Raised by `parse_tokens` / `validate` for any malformed token.

## Currently Recognized Subselectors

| Token | Effect |
|---|---|
| `bridge:copilot-vscode-to-claude:subagents` | Emit per-agent Claude subagent stubs under `<project>/.claude/agents/` (see [`bridge_subagents`](bridge-subagents.md)). |
| `bridge:copilot-vscode-to-claude:hooks` | Emit `.claude/settings.agentteams.example.json` + `.claude/hook-guard.sh` (see [`hooks_emit`](hooks-emit.md)). |
| `bridge:copilot-vscode-to-claude:cache-split` | Render cache-aware `CLAUDE.md` (see [`instructions_split`](instructions-split.md)). |
| `bridge:copilot-vscode-to-claude:schedule` | Emit `.claude/schedules.agentteams.json` (see [`schedule_emit`](schedule-emit.md)). |
| `bridge:copilot-vscode-to-claude:todo-projection` | Emit `.claude/skills/todo-from-plan/SKILL.md` (see [`plan_steps_todo`](plan-steps-todo.md)). |
| `goose:mcp` | Wire operator-specified `mcp_servers[]` into Goose recipes as `stdio`/`streamable_http` extensions (opt-in; Goose already grants CLI via the `developer` builtin, so this is never a default). Only first-party read-only servers scoped to an agent are auto-wired; others are surfaced as recipe comments (see [`mcp_emit`](mcp-emit.md)). |
| `codex:mcp` | Splice operator-specified `mcp_servers[]` into `.codex/config.toml`'s `[mcp_servers.*]` tables — a real, live config Codex reads to launch servers, so the same first-party/read-only/no-review-required auto-wire bar as `goose:mcp` applies. Unrelated file content (comments, sandbox/profile settings) is verified unchanged before any write, refusing the write rather than risk data loss if not (see [`codex_mcp_emit`](codex-mcp-emit.md)). |

> **Namespace scope:** this table lists the tokens — spanning the `bridge:copilot-vscode-to-claude:*`, `goose`, and `codex` namespaces — that have a wired-up effect today. `validate` / `parse_tokens` enforce a fixed allow-list — `_VALID_NAMESPACES` and a per-namespace `_KNOWN_FEATURES` set — not free-form syntactic well-formedness; a namespace or feature absent from those lists raises `HostFeatureError` however well-formed the token otherwise looks (e.g. `claude:not-a-real-feature` raises). Tokens whose namespace and feature are *both* allow-listed but not in this table — e.g. bare `claude:hooks` or `claude:subagents` — pass validation and simply produce no emission unless an emitter is looking for them.

Genuinely unrecognized tokens do **not** pass through silently: a namespace or feature absent from the allow-list is rejected at `validate()` with `HostFeatureError` and never reaches `manifest["host_features"]`. "No emission" describes only tokens that clear the allow-list but whose `namespace:feature` combination no current emitter checks for — emitters perform their own membership test against the active feature list and silently no-op when the flag they look for is absent.

## Example

```bash
agentteams --bridge-refresh \
  --target-host-features bridge:copilot-vscode-to-claude:subagents,bridge:copilot-vscode-to-claude:hooks \
  --bridge-from /path/to/source-team --output .claude
```
