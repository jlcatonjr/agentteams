# Agent-provider and model documentation register

Where the answer lives when a red-team finding needs contextualizing.

A finding is only actionable once someone has decided **whose behaviour it is**: our agent's
rules, the agent framework's documented behaviour, or a property of the model. Those three lead
to completely different actions — a template edit, a workaround plus a citation, and an
argument about model selection — so an untriaged finding either gets ignored or gets "fixed" in
the wrong layer.

`references/redteam-findings.log.csv` cites into this file by `doc_id`. A
`PROVIDER-DOCUMENTED` or `MODEL-LIMITATION` triage **cannot be recorded without a citation that
resolves here**, and `tests/test_redteam_findings_ledger.py` enforces it.

## Why this is a register and not a fetcher

`agentteams/framework_research.py` already fetches upstream docs, and this deliberately does
not reuse it.

Fetched third-party content is **C-4 data**: the moment it lands in the tree it is something an
agent reads, and under the constitution it carries no instruction authority and must be treated
as inert. Making it safe would mean routing every fetch through `agentteams.scan` and deciding
what to do when a vendor's documentation page trips the injection detector. A register that
records *where the answer is*, and *when a human last looked*, gives the triage what it needs
and carries none of that.

The cost is that entries go stale silently — so they are not allowed to. Each carries a
`last_verified` date and a `window_days`, and an entry past its window **fails the suite**.
Both pre-existing provider references in this repository were last verified `2026-07-02` and
nothing said so; that is the failure mode being closed.

## How to verify an entry

Open the URL, confirm the section named in `governs` still says what the citing findings assume,
then set `last_verified` to today. If the page has changed materially, the findings citing it
need re-triage — that is the point of the window.

## Register

| doc_id | provider | url | governs | last_verified | window_days |
|---|---|---|---|---|---|
| `goose-cli-run` | Goose | https://goose-docs.ai/docs/guides/goose-cli-commands | `goose run` flags: `--no-session`, `--max-turns`, `--system` (documented 2026-11-06). The isolation contract the judgment audit depends on. **`--no-profile` is no longer on this page (2026-11-06)**; `scripts/redteam_judgment_run.py` still relies on it to remove the tool layer, so confirm it still works before trusting a judgment run (remediation log 2026-11-06) | 2026-11-06 | 90 |
| `goose-recipes` | Goose | https://goose-docs.ai/docs/guides/recipes/recipe-reference/ | Recipe and sub-recipe structure; how goose delegates via structured tool calls — the mechanism that leaks as text when a backend mishandles it. Re-verified 2026-11-07: `sub_recipes` still documented; delegation is via the `summon` extension's `delegate` tool (auto-injected for recipes with `sub_recipes`) | 2026-11-07 | 90 |
| `goose-providers` | Goose | https://goose-docs.ai/docs/getting-started/providers | Provider configuration, `OPENROUTER_HOST`, and which surfaces honour it | 2026-11-06 | 90 |
| `claude-subagents` | Anthropic | https://code.claude.com/docs/en/sub-agents | Claude Code sub-agent front matter: `name`, `description`, `tools`, `model`. The capability-declaration contract C-3 rests on (the old docs.anthropic.com URL now redirects here) | 2026-11-06 | 90 |
| `copilot-cli` | GitHub | https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli | Copilot CLI agent behaviour and configuration. Re-verified 2026-11-07: `.agent.md` in `.github/agents/` and `~/.copilot/agents/`; page now also shows `model-policy`, `strict-tools-list`, `include-custom-instructions` | 2026-11-07 | 90 |
| `copilot-chatmodes` | Microsoft | https://code.visualstudio.com/docs/copilot/customization/custom-agents | VS Code custom agents (chat modes are legacy): the `.agent.md` front-matter schema. Re-verified 2026-11-07: page no longer mentions chat modes at all; it defers the full front-matter list to the separate "Custom agent file reference" page and its own nav uses `/docs/agent-customization/custom-agents`. No finding cites this row, so no re-triage | 2026-11-07 | 90 |
| `agents-md` | agents.md | https://agents.md | The AGENTS.md cross-tool standard: plain Markdown, **no required fields** — the contract the agents-md adapter emits against (and the base the codex adapter inherits) | 2026-09-22 | 90 |
| `codex` | OpenAI | https://learn.chatgpt.com/docs/agent-configuration/agents-md | Codex AGENTS.md discovery + config keys `project_doc_max_bytes` (32 KiB default), `mcp_servers`, `CODEX_HOME` — the surface `codex_mcp_emit.py` targets | 2026-09-22 | 90 |
| `grok-build` | xAI | https://docs.x.ai/build/settings/reference | Grok Build project config (`[mcp_servers]`, `[permission]`, `[plugins]` only), sandbox profiles, agent discovery; the surface `grok.py`, `grok_config_emit.py` and `_grok_sandbox_emit.py` target (pinned in `FORMAT_SPECS["grok"].pinned_version`) | 2026-10-14 | 90 |
| `openrouter-params` | OpenRouter | https://openrouter.ai/docs/api-reference/parameters | Request (sampling) parameters. `require_parameters` and provider routing are no longer on this page (2026-11-06); see `openrouter-provider-routing` | 2026-11-06 | 90 |
| `openrouter-provider-routing` | OpenRouter | https://openrouter.ai/docs/features/provider-routing | Backend selection, `allow_fallbacks` and `require_parameters` — the mechanism the route proxy pins, after backends were measured mangling tool calls at different rates | 2026-11-06 | 90 |
| `glm-5.2-card` | Z.AI (via OpenRouter) | https://openrouter.ai/z-ai/glm-5.2 | GLM 5.2 tool-calling support (`tools`, `tool_choice` accepted) and per-provider pricing; the page no longer shows a context window (2026-11-06). The model the daily judgment audit runs on | 2026-11-06 | 90 |
| `qwen3.6-plus-card` | Alibaba (via OpenRouter) | https://openrouter.ai/qwen/qwen3.6-plus | Qwen 3.6-plus capabilities and tool-calling behaviour (`tools`, `tool_choice` accepted). Cited by the measured tool-call-in-reasoning finding | 2026-11-06 | 90 |
| `qwen3.8-max-card` | Qwen/Alibaba (via OpenRouter) | https://openrouter.ai/qwen/qwen3.8-max | Qwen 3.8 Max (canonical slug `qwen/qwen3.8-max-0902`; tool-calling supported). Cited by the @security detect-but-no-HALT under-escalation findings measured 2026-08-09. Existence + tool-calling verified against the OpenRouter model page 2026-09-29 | 2026-09-29 | 90 |

## Local measurements that are not upstream documentation

These are **our** observations, not vendor statements, and a finding citing them is *not*
`PROVIDER-DOCUMENTED` — it is at best `MODEL-LIMITATION` with local evidence. Kept here so the
distinction stays visible:

- `references/goose-backend-switcher.md` — measured tool-call leak rates per backend on
  `qwen/qwen3.6-27b` (SiliconFlow 3/12, Chutes 1/12, Phala 1/12). Explicitly **not** measured on
  GLM 5.2 or on `qwen3.6-plus` until 2026-08-07.
- `references/redteam-judgment-layer.report.md` — the first judgment-layer measurement:
  GLM 5.2 8/11, Qwen 3.6-plus 4/11, neither complying with any attack.
