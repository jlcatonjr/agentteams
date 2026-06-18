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
