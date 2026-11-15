# `agent_runtime`: model and reasoning effort per role

*Module:* `agentteams/agent_runtime.py`. *Flags:* `agentteams/cli/agent_runtime_switch.py`. *Since:* 1.3.0 (agentteams #508).

A team has two roles: the **orchestrator** (`orchestrator`, `bridge-orchestrator`) and the **subagents**, which is
every other agent. The brief sets the model and the reasoning effort for each role, and can override them for a
single agent. Effort defaults to `medium` for both roles. Each framework adapter writes the values to its own
provider's settings. Where a provider has no setting for a value, the run says so; the value is never dropped silently.

```json
"agent_runtime": {
  "orchestrator": {"effort": "medium", "model": {"claude": "claude-fable-5-2"}},
  "subagents":    {"effort": "medium", "model": {"claude": "opus", "copilot": "Claude Opus 4.8 (copilot)"}},
  "agents":       {"security": {"effort": "high"}}
}
```

## Effort

The neutral levels are `low`, `medium`, `high`, `xhigh` and `max`. Each one maps to the provider's level of the same
name, so "medium" is each provider's own medium, not the same amount of reasoning everywhere. To use a level only one
provider offers, give an object: `{"default": "high", "codex": "ultra"}`. A level a provider cannot take is refused
when the team is generated.

| Framework | Where effort is written | Levels |
|---|---|---|
| `claude` | each agent's `effort:` front-matter line, plus `effortLevel` in `.claude/settings.hooks.example.json` (the main session) | `low` `medium` `high` `xhigh` `max` (`effortLevel` takes no `max`) |
| `copilot-vscode` | each agent's `reasoning-effort:` line | depends on the model; any level is passed through |
| `copilot-cli` | not written: Copilot CLI agent files take no effort key. Pass `--effort` when you start a session. | — |
| `codex` | not written yet: `model_reasoning_effort` waits for a live test against codex-cli | (`low`…`max`, `ultra`, once written) |
| `goose` | not written: Goose has no effort setting | — |
| `grok` | not written: Grok's agent keys are not in xAI's documentation | — |
| `agents-md`, `wasm` | not applicable | — |

**`max` on Claude.** An orchestrator effort of `max` goes into the orchestrator agent file only. The `effortLevel`
setting cannot take `max`, so a main session that is not started as that agent runs at its own level. Start it with
`--effort max` or `/effort max`.

## Model

`model` is an object keyed by provider: `claude`, `copilot` (both Copilot surfaces), `copilot-vscode`, `copilot-cli`,
`codex`, `goose` or `grok`. A bare string means Claude. The value must be a bare model name, never a provider, a
base URL or a profile. Endpoint routing stays in session configuration, so a brief cannot send a provider's traffic
elsewhere. With no model set, each adapter keeps its default: for Claude, `claude-fable-5-2` for the orchestrator and
`opus` for every other agent.

Claude and both Copilot surfaces write a configured model per agent. Codex, Goose and Grok report a configured model
instead of writing it: Codex and Grok for the reasons in the effort table, and Goose because a recipe model would
need a provider from the brief. Goose's model stays in its session configuration (`--goose-model`).

**The team-builder** (`team-builder`) is a meta-agent that builds teams, not a team role. It is left as it is,
with no `effort:` line.

## Precedence

On a fresh render: the agent's `agents.<slug>` entry, then its role, then the default.

On `--update --merge`:

- **`effort` is an ordinary front-matter key.** A missing line is added when the build log has a baseline. Without
  one, the change is reported. A line nobody has edited follows a changed brief. A line edited by hand is kept and
  reported. To change one agent's effort, set `agents.<slug>`.
- **`model` is a capability key and is never changed automatically.** `--update` reports the proposed line. To
  adopt it, add it by hand or run `--update --overwrite` with an `@security` clearance (operator ruling,
  2026-11-15).

A per-agent entry that lowers the effort of `@security`, `@adversarial`, `@conflict-auditor` or `@code-hygiene`, or
gives one of them its own model, is shown as a warning.

## Flags

`--orchestrator-model [PROVIDER=]MODEL`, `--orchestrator-effort LEVEL`, `--subagent-model [PROVIDER=]MODEL` and
`--subagent-effort LEVEL` override the brief **for one run**. Each may be repeated, once per provider, and
`PROVIDER=LEVEL` sets one provider's own level. Values are not kept. The build log is generated data and is never read
back as a setting. The run names each flag value and says to put it in the brief to keep it.

## Checking it

`agentteams --doctor` prints the effective model and effort for each role and each framework present, names every
value not written, and counts the Claude agent files that set no `effort:`. Two things cannot be checked from files:

- Claude Code lowers a level the model does not support to the highest level it does support. For example, `xhigh`
  runs as `high` on Opus 4.6.
- `CLAUDE_CODE_EFFORT_LEVEL` in the caller's environment outranks every file agentteams writes. `confine-run.sh`
  drops it unless `--env-allow` names it.

Per-agent `effort:` and `model:` lines live in the repository, so they hold under any HOME directory. The project
`effortLevel` is read from the session's primary working directory.

## API

| Function | Purpose |
|---|---|
| `parse(description)` | Normalise the brief block (an absent block means every default); raises `AgentRuntimeError` |
| `to_manifest(rt)` / `from_manifest(manifest)` | The manifest record (`manifest["agent_runtime"]`, carried only when the brief has the block) |
| `effort_for(rt, slug, framework)` | The level to write, or None where the framework writes none |
| `model_for(rt, slug, framework)` | The configured model, or None (the adapter default) |
| `explicit_effort(rt, slug)` | Whether the brief set this agent's effort itself |
| `session_effort(rt, framework)` | Claude's `effortLevel` (None for `max`) |
| `report_lines(rt, framework, slugs)` | One line per value not written, and per governance warning |
| `effective_table(rt, frameworks)` | `doctor`'s rows |
