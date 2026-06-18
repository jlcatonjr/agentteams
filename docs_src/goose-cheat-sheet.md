# Goose Cheat Sheet

Quick reference for common prompting patterns, session management, and recipe invocation.

---

## Session Modes

| Mode | Command | Use when |
|---|---|---|
| Interactive chat | `goose session` | Exploratory or conversational work |
| Resume last session | `goose session --resume` | Continue where you left off |
| Named session | `goose session --name my-session` | Work you'll return to |
| Resume named | `goose session --resume --name my-session` | Continue a specific session |
| Run a recipe | `goose run --recipe path/to/recipe.yaml` | Invoke a specific agent role |
| Run + stay interactive | `goose run --recipe orch.yaml --interactive` | Load an agent role, then chat |
| Non-interactive / CI | `goose run --recipe orch.yaml --no-session` | Scripted / automation runs |
| Pipe instructions | `echo "do the thing" \| goose run -i -` | One-shot from stdin |

---

## Referencing Files in a Prompt

### `@` file reference (inline context)

Prefix any file path with `@` and Goose reads it into the conversation:

```
@src/main.py — what does this module do?
```

```
Review @reports/draft.md and @references/style-guide.md for consistency.
```

Multiple files in one prompt:

```
Compare @.goose/recipes/orchestrator.yaml with @.goose/recipes/primary-producer.yaml
```

Glob patterns are not supported — list files individually.

### Asking Goose to read a file itself

When you want the model to search and read without pre-loading:

```
Read the file src/utils/helpers.py and explain the retry logic.
```

```
Find all files that import `fleet.py` and summarize what each one does.
```

### Passing file content via stdin

```bash
cat brief.json | goose run -i - --text "Build a team from this descriptor"
```

---

## Calling a Specific Agent (Recipe)

### Run an agent role non-interactively

```bash
goose run --recipe .goose/recipes/orchestrator.yaml
```

The `prompt:` field in the recipe (if present) is sent automatically — useful in CI.

### Run and then keep chatting

```bash
goose run --recipe .goose/recipes/primary-producer.yaml --interactive
```

### Load an agent by name (if GOOSE_RECIPE_GITHUB_REPO is set)

```bash
goose run --recipe primary-producer
```

### Inspect a recipe before running

```bash
goose recipe validate .goose/recipes/orchestrator.yaml
goose run --recipe .goose/recipes/orchestrator.yaml --explain
goose run --recipe .goose/recipes/orchestrator.yaml --render-recipe
```

### Override provider or model for one run

```bash
goose run --recipe .goose/recipes/security.yaml \
  --provider anthropic --model claude-opus-4-8
```

---

## Invoking a Sub-Agent Within a Session

When inside an orchestrator session, route work to a specialist using `summon`:

```
summon load("primary-producer")
Draft the introduction section from the brief in tmp/brief.md
```

Or reference the agent by name (the orchestrator recipe wires `sub_recipes`):

```
@primary-producer — draft the methods section from @tmp/methods-brief.md
```

Common patterns:

```
Route this to @git-operations: stage all changes in src/ and write a commit message.
```

```
Ask @security to review the credential handling in @src/auth.py before we proceed.
```

```
Hand off to @conflict-auditor — we've modified 4 files this session.
```

---

## Common Prompting Patterns

### Load context first, then ask

```
Read .goose/recipes/orchestrator.yaml. What workflows does this team support?
```

```
Read AGENTS.md, then tell me which agent handles bibliography management.
```

### Scope a task to a directory

```
Look at all files in src/agentteams/frameworks/ and identify any that don't have unit tests.
```

```
In the reports/ directory, find any Markdown files missing a References section.
```

### Chain actions explicitly

```
1. Read tmp/plan.md
2. Execute step 1 using @git-operations
3. Run @conflict-auditor after the changes
```

### Ask for a plan before execution

```
Before making any changes, write a step-by-step plan to refactor the auth module.
Wait for my approval before proceeding.
```

### Constrain output format

```
Summarize the findings in @references/audit.csv as a Markdown table with columns:
File, Issue, Severity.
```

```
List every agent in AGENTS.md as JSON: [{"slug": "...", "role": "..."}]
```

---

## Session Management

```bash
# List all sessions
goose session list

# Resume the most recent session
goose session --resume

# Fork a session (copy history, start fresh branch)
goose session --resume --fork --name my-fork

# Show prior messages when resuming
goose session --resume --history

# Remove a session
goose session remove --name old-session
```

---

## Extensions (Tools)

Extensions give Goose capabilities beyond conversation. Common built-ins:

| Extension | What it does |
|---|---|
| `developer` | Read/write files, run shell commands, search code |
| `summon` | Spawn sub-agent sessions (delegation to specialist recipes) |

### Add an extension for one session

```bash
goose session --with-builtin developer
goose session --with-builtin summon
```

### Add a stdio MCP extension

```bash
goose session --with-extension "npx -y @modelcontextprotocol/server-filesystem /my/dir"
```

### Start without loading your default profile extensions

```bash
goose session --no-profile --with-builtin developer
```

---

## Automation and CI

### Run a recipe, capture output, check exit code

```bash
goose run --recipe .goose/recipes/security.yaml \
  --no-session --quiet \
  --output-format text \
  > security-report.txt
echo "exit: $?"
```

### Pass parameters into a recipe

```bash
goose run --recipe .goose/recipes/primary-producer.yaml \
  --params section=introduction \
  --params output_file=reports/intro.md
```

### Validate all recipes in a directory

```bash
for f in .goose/recipes/*.yaml; do
  goose recipe validate "$f" && echo "OK: $f" || echo "FAIL: $f"
done
```

Or use agentteams:

```bash
agentteams --framework goose --recipe-check --output .
```

---

## agentteams + Goose Integration

| Task | Command |
|---|---|
| Generate Goose recipes from copilot-vscode agents | `agentteams --convert-from .github/agents --framework goose --output .` |
| Generate bridge docs only (AGENTS.md, .goosehints) | `agentteams --bridge-from .github/agents --framework goose --bridge-refresh --output .` |
| Validate all generated recipes | `agentteams --framework goose --recipe-check --output .` |
| Update recipes after source changes | `agentteams --bridge-from .github/agents --framework goose --bridge-merge --output .` |
| Fleet update (all Goose workspaces) | `agentteams --fleet /parent/dir --fleet-frameworks goose --update --merge` |
| Fleet update (all frameworks) | `agentteams --fleet /parent/dir --fleet-frameworks all --update --merge` |

---

## Session Startup Pattern (agentteams teams)

When `.goosehints` contains a Session Startup block, Goose reads `orchestrator.yaml` at the start of every plain session automatically. You can also do it manually:

```
Read .goose/recipes/orchestrator.yaml and adopt the Orchestrator role for this session.
```

To bypass the orchestrator and work directly with a specialist:

```bash
goose run --recipe .goose/recipes/primary-producer.yaml --interactive
```

---

## Quick Diagnostics

```bash
goose doctor                    # check setup + provider connectivity
goose info                      # show active provider, model, config path
goose session list              # show all saved sessions
goose recipe list               # list recipes in configured recipe directories
goose recipe validate recipe.yaml   # validate a single recipe file
```

---

---

# Part 2 — Goose VS Code Extension

The VS Code extension wraps the same goose CLI via ACP (Agent Client Protocol), adding an inline chat panel, code-selection shortcuts, and Code Lens actions. Requires VS Code ≥ 1.95.0 and goose CLI ≥ 1.16.0 pre-installed. Install from the Marketplace: search **"VS Code Goose"** (publisher: Block).

> **Status:** Marked experimental; UI details may change across releases.

---

## Opening the Panel

| Action | How |
|---|---|
| Open Goose sidebar panel | Click the Goose icon in the Activity Bar (left sidebar) |
| Focus panel via Command Palette | `Cmd+Shift+P` → type `Goose` |
| Send selected code to Goose | Select code → `Cmd+Shift+G` (macOS) / `Ctrl+Shift+G` (Windows/Linux) |
| Ask Goose about a specific line | Click **"Ask goose"** Code Lens that appears above any line in the editor |
| Quick fix via Goose | Right-click on code → **Send to goose** (or choose a quick fix suggestion) |

The chat panel opens in the sidebar. All responses, tool calls, and history stay in that panel for the session.

---

## Attaching Files to a Message

Type `@` in the chat input to search and attach workspace files as context. Attached files appear as chips above the input and their full contents are sent to the model.

```
@ src/auth.py  →  attaches the whole file
```

Multiple attachments in one message:

```
@src/auth.py @tests/test_auth.py  Review for missing edge cases.
```

You can also attach a specific line range by appending it after the filename in the chip (exact syntax depends on extension version; check the chip UI after attaching).

**Alternative — send selected code directly:**

1. Select lines in the editor
2. Press `Cmd+Shift+G` / `Ctrl+Shift+G`
3. Goose receives the selection as inline context with its file path and line range

---

## Loading an Agent Role (Recipe)

There is no "Load Recipe" button in the extension UI. The recommended approach is to start the session from the terminal with a recipe pre-loaded, then continue in the extension panel:

```bash
# Start a recipe session — the VS Code extension panel picks up the active session
goose run --recipe .goose/recipes/orchestrator.yaml --interactive
```

Alternatively, instruct Goose to read the recipe file itself at the start of your chat:

```
Read .goose/recipes/orchestrator.yaml and adopt the Orchestrator role for this session.
```

This is exactly what the **Session Startup block** in `.goosehints` automates — see below.

---

## .goosehints and the VS Code Extension

`.goosehints` (placed at the project root) is **automatically read on every Goose interaction**, including in the VS Code extension. It provides persistent project context without any manual action.

It differs from sessions:

| | `.goosehints` | Session history |
|---|---|---|
| Scope | Every interaction, all sessions | One named conversation |
| Contents | Static instructions, project context | Full message history |
| Loaded automatically? | Yes — always | Yes — when resumed |
| Survives new sessions? | Yes | Only if session is named and resumed |

**Tip for agentteams projects:** The `.goosehints` Session Startup block tells Goose to self-read `orchestrator.yaml` at the top of every session — including VS Code sessions. You get Orchestrator behavior in the extension panel without any setup prompt.

Example `.goosehints` pattern agentteams generates:

```markdown
@AGENTS.md

### Session Startup (Mandatory)

At the start of every session in MyProject, before responding to any user request:

1. Read `.goose/recipes/orchestrator.yaml` using your file tool
2. Adopt the Orchestrator identity and routing logic from that file
```

---

## Routing to Sub-Agents in the Extension

The same routing prompts that work in `goose session` work in the VS Code chat panel:

```
Route this to @git-operations: commit all staged changes with a descriptive message.
```

```
Ask @security to review @src/payments.py before we proceed.
```

```
Hand off to @conflict-auditor — we've modified 3 files this session.
```

To explicitly summon a sub-recipe (requires the `summon` extension to be active):

```
summon load("primary-producer")
Draft the introduction from the brief at @tmp/intro-brief.md
```

If the orchestrator recipe was loaded (via `.goosehints` or a CLI `--recipe` start), sub-recipe routing happens through its `sub_recipes` delegation table automatically.

---

## Session Management in VS Code

Sessions created in the extension panel persist across VS Code restarts. You can manage them from the integrated terminal:

```bash
goose session list                        # see all sessions with timestamps
goose session --resume                    # resume the most recent session
goose session --resume --name my-session  # resume a specific named session
goose session --resume --fork             # fork session (keep history, fresh branch)
goose session remove --name old-session   # delete a session
```

Start a named session directly in the extension by opening a terminal and running:

```bash
goose session --name research-2026-06-18
```

The extension panel will reflect the active session.

---

## Provider and Extension Configuration

Provider selection is done once via CLI:

```bash
goose configure   # interactive setup: choose provider (anthropic, openai, etc.) and model
```

This writes `~/.config/goose/config.yaml`, which the VS Code extension reads automatically.

To add tool extensions for an extension-panel session, start from the terminal with flags:

```bash
goose session --with-builtin developer    # file read/write + shell access
goose session --with-builtin summon       # sub-agent delegation
goose session --with-extension "npx -y @modelcontextprotocol/server-filesystem /my/dir"
```

To override the model for a one-off VS Code session:

```bash
goose session --name fast-check \
  --with-builtin developer \
  -- --model claude-haiku-4-5-20251001    # model override (CLI passthrough)
```

---

## VS Code Extension vs CLI — When to Use Each

| Scenario | VS Code Extension | CLI |
|---|---|---|
| Exploring code in the open editor | Preferred — Code Lens + `@` attach | Possible |
| Long multi-file editing session | Preferred — persistent panel, history | Possible |
| CI / scripted automation | Not applicable | Required |
| Loading a specific recipe role | Use CLI to start, continue in panel | Full support |
| Named sessions with history | Both (manage via CLI) | Full support |
| Passing `--params` to a recipe | Not available in panel | `--params KEY=VAL` |
| Quiet/JSON output capture | Not applicable | `--quiet --output-format json` |
