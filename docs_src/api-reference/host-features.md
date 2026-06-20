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
Validate a single `<ns>:<feature>` token. The namespace may itself contain a colon (e.g. `bridge:copilot-vscode-to-claude:subagents`); validation splits on the last colon to separate the feature from the namespace. Raises `HostFeatureError` on empty parts, leading/trailing whitespace, or disallowed characters.

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
| `bridge:copilot-vscode-to-claude:todo-projection` | Emit `.claude/skills/todo-from-plan.md` (see [`plan_steps_todo`](plan-steps-todo.md)). |

> **Namespace scope:** this table lists only the tokens in the `bridge:copilot-vscode-to-claude:*` namespace that have a wired-up effect today. `validate` / `parse_tokens` accept any syntactically valid `<ns>:<feature>` token (the namespace may itself contain colons), so tokens in other namespaces — e.g. `claude:*` — also pass validation. They simply produce no emission unless an emitter is looking for them.

Unknown tokens are *valid syntactically* but produce no emission — emitters perform their own membership test against the active feature list and silently no-op when the flag they look for is absent.

## Example

```bash
agentteams --bridge-refresh \
  --target-host-features bridge:copilot-vscode-to-claude:subagents,bridge:copilot-vscode-to-claude:hooks \
  --bridge-from /path/to/source-team --output .claude
```
