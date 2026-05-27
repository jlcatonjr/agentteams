# `hooks_emit`

Claude hooks emission for the `copilot-vscode → claude` bridge. Writes two artifacts under `<project>/.claude/`:

1. **`settings.agentteams.example.json`** — a sample hooks block the user merges into their own `settings.json`. agentteams **never** overwrites `settings.json` or `settings.local.json`.
2. **`hook-guard.sh`** — recursion-depth-bounded notification wrapper that logs to `.claude/hook-notices/<date>.log` and refuses re-entry beyond `AGENTTEAMS_HOOK_MAX_DEPTH` (default `2`). Bounds any `agent → write → hook` cascade.

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

`hook-guard.sh` reads `AGENTTEAMS_HOOK_DEPTH` from the environment (default `0`), increments it, and exits non-zero (with a log line) if it would exceed `AGENTTEAMS_HOOK_MAX_DEPTH`. The exported depth is inherited by any downstream tool invocation the hook triggers.
