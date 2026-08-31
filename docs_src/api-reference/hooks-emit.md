# `hooks_emit`

Claude hooks emission for the `copilot-vscode → claude` bridge. Writes two artifacts under `<project>/.claude/`:

1. **`settings.agentteams.example.json`** — a sample hooks block the user merges into their own `settings.json`. agentteams **never** overwrites `settings.json` or `settings.local.json`.
2. **`hook-guard.sh`** — recursion-depth-bounded notification wrapper that logs to `.claude/hook-notices/<date>.log` and refuses re-entry beyond `AGENTTEAMS_HOOK_MAX_DEPTH` (default `2`). Bounds any `agent → write → hook` cascade (see the [security hardening guide](../security-hardening-guide.md)).

Opt-in via [`--target-host-features bridge:copilot-vscode-to-claude:hooks`](host-features.md).

## Hook Mapping

Data-driven (canonical slug → event[, matcher]):

| Slug | Event | Matcher |
|---|---|---|
| `cleanup`, `agent-updater`, `code-hygiene`, `drift` | `PostToolUse` | `Write\|Edit` |
| `security` | `PreToolUse` | `Bash\|Write\|Edit` |
| `work-summarizer`, `post-production-auditor` | `Stop` | *(none)* |

Default mode is **notification** (hook logs; user/orchestrator decides escalation), not automatic subagent invocation. Safer first cut; the user can edit `settings.json` to upgrade specific hooks to active invocation.

## Public Surface

```python
@dataclass
class HooksEmissionResult:
    written: list[str]
    skipped: list[str]
    errors: list[str]
    success: bool  # property: len(errors) == 0
```

```python
build_settings_dict(source_dir: Path) -> dict[str, Any]
```
Build the Claude `settings.json` hooks block for active slugs. Only includes entries for slugs whose `<slug>.agent.md` exists in `source_dir`. Each entry runs `bash .claude/hook-guard.sh <event> <slug>`.

```python
emit_hooks_artifacts(
    *,
    source_dir: Path,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = True,
) -> HooksEmissionResult
```
Write `.claude/settings.agentteams.example.json` and `.claude/hook-guard.sh` under `output_root`. The guard script is chmod-ed `0755` when possible. `overwrite=True` (default) matches `--bridge-refresh` semantics.

## Recursion Guard Contract

`hook-guard.sh` reads `AGENTTEAMS_HOOK_DEPTH` from the environment (default `0`) and compares it against `AGENTTEAMS_HOOK_MAX_DEPTH` (default `2`). Every path **exits `0`** — the guard never fails a hook or blocks Claude from continuing; "refusal" means silently doing nothing, not signalling an error.

- **At or beyond max depth:** silent no-op. No log line is written and the depth is not incremented — this is the refused path.
- **Below max depth:** the guard writes a log line to `.claude/hook-notices/<YYYY-MM-DD>.log` (`event`, `slug`, and the depth it fired at), then exports `AGENTTEAMS_HOOK_DEPTH` incremented by one so any downstream tool invocation the hook triggers is inherited at the higher depth.

The log write happens only on the *allowed* path, immediately before that path's own `exit 0` — the guard is silent exactly where it refuses, and logs exactly once where it lets the hook through.

## See also

- [`schedule_emit`](schedule-emit.md) — sibling host-feature emitter (recurring `/schedule` routines).
- [`instructions_split`](instructions-split.md) — sibling host-feature emitter (cache-aware `CLAUDE.md`).
