# Claude Code Cheat Sheet

Quick reference for the Claude Code CLI and VS Code extension.

---

## Part 1 — Command Line (CLI)

---

## Session Modes

| Command | Use when |
|---|---|
| `claude` | Start interactive session |
| `claude "fix the bug in auth.py"` | Start session with an initial prompt |
| `claude --continue` | Continue the most recent session |
| `claude --resume` | Open a picker to resume any past session |
| `claude --model opus` | Start with a specific model |
| `claude --print "explain this" < file.ts` | Non-interactive: print response to stdout |
| `claude --worktree feature-branch` | Start in an isolated git worktree |
| `claude --add-dir ../shared-config` | Grant access to an additional directory |
| `claude --safe-mode` | Disable all customizations (CLAUDE.md, MCP, hooks) |

### Model aliases

| Alias | Resolves to |
|---|---|
| `default` | Account default (Opus 4.8 for Max/Team; Sonnet 4.6 for Pro) |
| `best` | Fable 5 where available, else latest Opus |
| `fable` | Claude Fable 5 |
| `opus` | Claude Opus 4.8 |
| `sonnet` | Claude Sonnet 4.6 |
| `haiku` | Claude Haiku 4.5 |
| `opusplan` | Opus for planning, Sonnet for execution |

Use full model IDs for exact pinning: `--model claude-sonnet-4-6`, `--model claude-opus-4-8`.

---

## Referencing Files in a Prompt

### `@` mentions

Type `@` to autocomplete file paths directly in the CLI prompt:

```
@src/auth.py          — full file contents
@src/                 — entire folder (trailing slash)
@report.pdf pages=1-5 — specific PDF pages
@file.ts#10-30        — specific line range
```

Multiple files in one prompt:

```
Review @src/auth.py and @tests/test_auth.py for edge cases.
```

### Piping and stdin

```bash
# Pipe file content for non-interactive use
claude --print "find bugs:" < src/auth.py

# Pipe from another command
git diff | claude --print "write a commit message for this diff"

# Heredoc multi-line prompt
claude --print << 'EOF'
Look at the file below and suggest improvements:
$(cat src/main.py)
EOF
```

### Shell mode (in REPL)

Prefix with `!` to run a shell command and add its output to the session context:

```
! cat src/auth.py
! git log --oneline -10
! pytest tests/test_auth.py -v
```

---

## Slash Commands

Type `/` in the interactive session to see all available commands.

### Session control

| Command | Description |
|---|---|
| `/clear` | Reset conversation (new context, keeps history) |
| `/compact` | Summarize history to free context window space |
| `/context` | Show context window usage breakdown |
| `/recap` | Generate a summary of the current session |
| `/btw <question>` | Ask a side question (no tools, no history entry — cheap) |

### Model and effort

| Command | Description |
|---|---|
| `/model` | Open model picker |
| `/model opus` | Switch to Opus immediately |
| `/effort` | Open effort slider |
| `/effort high` | Set reasoning effort (low / medium / high / xhigh / max) |

### Memory and context

| Command | Description |
|---|---|
| `/memory` | Browse and edit CLAUDE.md and auto-memory files |
| `/init` | Auto-generate a CLAUDE.md for the current project |

### Tools and configuration

| Command | Description |
|---|---|
| `/mcp` | List, enable/disable, and authenticate MCP servers |
| `/permissions` | Set permission mode and per-tool rules |
| `/config` | Settings UI (model, effort, permission mode) |
| `/hooks` | View and manage configured hooks |
| `/settings` | Show active settings and their sources |
| `/skills` | List available skills (slash commands) |
| `/plugins` | Manage installed plugins |

### Autonomous operation

| Command | Description |
|---|---|
| `/plan` | Switch to plan mode (Claude describes changes before making them) |
| `/loop [interval]` | Re-run the current prompt on a schedule (`/loop 5m`) |
| `/goal <condition>` | Run autonomously until a condition is true (`/goal "tests pass"`) |
| `/background` | Detach session to run without blocking your terminal |
| `/batch <prompt>` | Decompose a large task and run parts in parallel worktrees |
| `/tasks` | Show background tasks and subagent progress |

### Diagnostics

| Command | Description |
|---|---|
| `/doctor` | Check MCP connections, hooks, CLAUDE.md load status |
| `/status` | Show account, model, and session info |
| `/usage` | Show token usage for account and session |
| `/debug` | Enable debug logging for this session |

---

## Keyboard Shortcuts (Interactive REPL)

### Navigation and interruption

| Key | Action |
|---|---|
| `Esc` | Interrupt Claude mid-response (keeps work done so far) |
| `Esc Esc` | Clear input draft (saves to history), or open rewind menu when empty |
| `Ctrl+C` | First press clears input; second press exits |
| `Ctrl+D` | Exit session |
| `Ctrl+L` | Redraw screen |
| `Ctrl+O` | Toggle transcript viewer (shows all tool calls) |
| `Ctrl+R` | Reverse search command history |
| `Ctrl+G` or `Ctrl+X Ctrl+E` | Open current prompt in your `$EDITOR` |
| `Ctrl+X Ctrl+K` | Stop all background subagents (press twice to confirm) |

### Permission mode cycling

| Key | Action |
|---|---|
| `Shift+Tab` or `Alt+M` | Cycle: default → acceptEdits → plan → auto |
| `Option+P` / `Alt+P` | Open model picker |
| `Option+T` / `Alt+T` | Toggle extended thinking |

### Multiline input (choose one that matches your terminal)

| Method | Works in |
|---|---|
| `\ + Enter` | All terminals |
| `Ctrl+J` | All terminals |
| `Shift+Enter` | iTerm2, Terminal.app, WezTerm, Kitty, Warp, Alacritty |
| `Option+Enter` | Terminals with Option-as-Meta configured |

Run `/terminal-setup` to install readline keybindings including `Shift+Enter`.

### Text editing (readline-style)

| Key | Action |
|---|---|
| `Ctrl+A` / `Ctrl+E` | Move to line start / end |
| `Ctrl+K` | Delete to end of line |
| `Ctrl+U` | Delete from cursor to line start |
| `Ctrl+W` | Delete previous word |
| `Alt+B` / `Alt+F` | Move back / forward one word |
| `Ctrl+Y` | Paste last deleted text |
| `Alt+Y` | Cycle paste history |

### Transcript viewer (`Ctrl+O` to toggle)

| Key | Action |
|---|---|
| `{` / `}` | Jump to previous / next user prompt |
| `Ctrl+E` | Toggle show full content |
| `v` | Open in text editor |
| `q` / `Esc` | Exit transcript view |

---

## Non-Interactive / Scripting

```bash
# Print response to stdout
claude --print "summarize this module" < src/payments.py

# JSON output for automation
claude --print --output-format json "list all exported functions" < src/api.py

# Streaming JSON (for real-time parsing)
claude --print --output-format stream-json "audit this file" < src/auth.py

# Override model for one run
claude --print --model haiku "is this valid JSON?" < data.json

# Set reasoning effort
claude --print --effort high "find subtle bugs" < src/core.py

# Quiet (no spinner, no status messages)
claude --print "fix the TODO" < src/todo.py 2>/dev/null
```

---

## MCP Server Configuration

### Adding servers

```bash
# HTTP server
claude mcp add --transport http my-server https://api.example.com/mcp

# Local stdio server
claude mcp add playwright -- npx -y @playwright/mcp@latest

# User scope (available in all projects)
claude mcp add --scope user github https://api.githubcopilot.com/mcp \
  --transport http --header "Authorization: Bearer YOUR_PAT"

# Project scope (committed to repo, shared with team)
claude mcp add --scope project my-server https://api.example.com/mcp
```

### Where config lives

| Scope | File |
|---|---|
| Local (default) | `~/.claude.json` (project-specific entry) |
| User | `~/.claude.json` (top-level `mcpServers` key) |
| Project | `.mcp.json` in repo root (committed) |

### `.mcp.json` structure

```json
{
  "mcpServers": {
    "my-http-server": {
      "type": "http",
      "url": "https://api.example.com/mcp"
    },
    "my-local-server": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@org/my-mcp@latest"]
    }
  }
}
```

### Managing and using MCP tools

```bash
claude mcp list          # check connection status
claude mcp remove <name> # remove a server
```

In a session: type `/mcp` to enable/disable servers and authenticate OAuth servers. Claude discovers and uses MCP tools automatically — no special prompt syntax needed. You can be explicit:

```
Use the github MCP server to review PR #456.
Use the playwright server to screenshot the homepage.
```

---

## Hooks

Hooks run shell commands at lifecycle events — enforce coding standards, format on save, block dangerous commands, log tool use.

### Where hooks are configured

| File | Scope |
|---|---|
| `~/.claude/settings.json` | All projects (user) |
| `.claude/settings.json` | This project (committed) |
| `.claude/settings.local.json` | This project (not committed) |

### Key event types

| Event | Fires when |
|---|---|
| `PreToolUse` | Before any tool call (Bash, Edit, Read, MCP, etc.) |
| `PostToolUse` | After a tool succeeds |
| `UserPromptSubmit` | Before Claude processes a user prompt |
| `SessionStart` | Session begins |
| `Stop` | Session ends |
| `FileChanged` | A file is modified |
| `PreCompact` / `PostCompact` | Context compaction |

### Example hook config

```json
{
  "hooks": [
    {
      "event": "PreToolUse",
      "matcher": {
        "tool": "Bash",
        "if": "args.command matches 'rm -rf'"
      },
      "handler": {
        "type": "command",
        "command": "echo 'rm -rf is blocked' && exit 2"
      }
    },
    {
      "event": "PostToolUse",
      "matcher": { "tool": "Edit" },
      "handler": {
        "type": "command",
        "command": "prettier --write ${CLAUDE_TOOL_RESULT_VALUE.path}"
      }
    }
  ]
}
```

View configured hooks in a session with `/hooks`.

---

## CLAUDE.md and Memory

### CLAUDE.md — persistent project instructions

Claude reads these files at session start (broadest to most specific):

| Location | Scope |
|---|---|
| `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS) | Organization-wide policy |
| `~/.claude/CLAUDE.md` | All your projects |
| `./CLAUDE.md` or `./.claude/CLAUDE.md` | This project (committed) |
| `./CLAUDE.local.md` | This project, personal (not committed) |
| Subdirectory `CLAUDE.md` files | Lazy-loaded when Claude reads matching files |

**Tips:**
- Keep it under 200 lines — shorter means better adherence.
- Be specific: "Use 2-space indentation" beats "format code nicely."
- Import other files: `@README`, `@docs/style-guide.md`.
- Generate one automatically: `/init`

### Auto-memory

Claude writes project-specific memories automatically based on what's useful to recall across sessions.

| | CLAUDE.md | Auto-memory |
|---|---|---|
| Written by | You | Claude (automatically) |
| Location | Repo or `~/.claude/` | `~/.claude/projects/<hash>/memory/` |
| Shared via git | Yes (if in repo) | No — machine-local |
| Loaded at startup | Yes (all matching files) | First 200 lines of `MEMORY.md` |

Browse and edit both with `/memory`.

---

---

## Part 2 — VS Code Extension

The extension wraps the same Claude Code engine with an inline chat panel, file-attach shortcuts, and editor integration. Requires Claude Code CLI installed.

---

## Opening the Panel

| Action | How |
|---|---|
| Sidebar panel | Click the spark **✦** icon in the Activity Bar (left sidebar) |
| Editor toolbar | Click the spark **✦** icon in the top-right toolbar (requires a file open) |
| Status bar | Click **✱ Claude Code** in the bottom status bar |
| New tab | Command Palette → `Claude Code: Open in New Tab` |
| Keyboard (new tab) | `Cmd+Shift+Esc` (macOS) / `Ctrl+Shift+Esc` (Windows/Linux) |

A colored dot on the spark icon signals session state: **blue** = permission pending, **orange** = finished while the panel was hidden.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+Esc` / `Ctrl+Esc` | Toggle focus between editor and Claude panel |
| `Cmd+Shift+Esc` / `Ctrl+Shift+Esc` | Open new Claude conversation in a tab |
| `Option+K` / `Alt+K` | Insert `@file.ts#line-range` for the current editor selection |
| `Shift+Enter` | Multiline input in the chat box |
| `Cmd+Shift+T` / `Ctrl+Shift+T` | Reopen last closed Claude tab |

---

## Attaching Files and Code

### `@` mentions in the chat input

Type `@` to fuzzy-search and attach files from the workspace:

```
@src/auth.py               — full file
@src/                      — entire folder
@src/auth.py#10-30         — specific line range
@browser                   — browser content (requires Claude in Chrome extension)
```

Multiple attachments:

```
@src/auth.py @tests/test_auth.py  — do these tests cover all branches?
```

Attached files appear as chips above the input. Their full contents are sent to the model.

### Send selected code from the editor

1. Select lines in the editor
2. Press `Option+K` / `Alt+K` — inserts the `@file#range` reference automatically
3. Or: right-click → **Send to Claude Code**

The selection chip shows a line count in the corner. Click the eye icon to toggle whether the selection is visible to Claude.

You can also **Shift+drag** files from the Explorer into the prompt box.

---

## Permission Modes

The mode selector sits at the bottom of the prompt box. Cycle it or click to choose:

| Mode | Behavior |
|---|---|
| `default` | Asks permission for file edits and shell commands |
| `plan` | Claude describes all changes first; accept/reject before execution |
| `acceptEdits` | Auto-approves file edits; still asks for shell commands |
| `auto` | Auto-approves safe actions via classifier; no prompts |

In `plan` mode, diffs open in a markdown editor where you can add inline comments before accepting.

You can also cycle modes from the CLI REPL with `Shift+Tab`.

---

## Sessions and History

Sessions in the extension persist across VS Code restarts.

- **Browse history:** Click the "Session history" button in the panel header → search or select any past session.
- **Remote sessions:** The "Remote" tab shows cloud sessions from claude.ai (requires subscription).
- **Multiple tabs:** Open additional conversations with `Cmd+Shift+Esc` — each tab is independent.
- **Named sessions from CLI:** Start `goose session --name my-session` in the terminal; the extension panel picks up the active session.

Manage sessions from the integrated terminal:

```bash
claude --resume                         # resume most recent session
claude --continue <session-id>          # continue a specific session
```

---

## Slash Commands in the Extension

The extension supports a subset of CLI slash commands. Type `/` in the chat input to see what's available. Confirmed in both CLI and extension:

`/clear` `/compact` `/context` `/model` `/effort` `/memory` `/mcp` `/permissions` `/plan` `/status` `/doctor` `/recap` `/settings`

Commands available in CLI only (not in the extension panel): `!` shell mode, `/loop`, `/goal`, `/background`, `/batch`, tab completion.

---

## MCP Servers in the Extension

MCP server configuration is shared between the CLI and extension — both read `~/.claude.json` and `.mcp.json`.

**Add servers** from the terminal using `claude mcp add` (same as CLI).

**Manage servers** from within the extension: type `/mcp` in the chat input → enable, disable, or authenticate servers.

---

## Extension Settings (VS Code `settings.json`)

These settings live in VS Code workspace or user settings, not in `~/.claude/settings.json`:

| Setting | Description |
|---|---|
| `claudeCode.useTerminal` | Use CLI mode instead of the graphical panel |
| `claudeCode.initialPermissionMode` | Starting permission mode: `default` / `plan` / `acceptEdits` / `auto` |
| `claudeCode.autosave` | Auto-save files before Claude reads or writes them |
| `claudeCode.respectGitIgnore` | Exclude `.gitignore` patterns from file search |
| `claudeCode.usePythonEnvironment` | Activate the workspace Python environment for Claude |
| `claudeCode.enableNewConversationShortcut` | Enable `Cmd+N` to open a new conversation |

Behavioral settings (effort, model, hooks, MCP) live in `~/.claude/settings.json` and apply to both the CLI and extension.

---

## CLI in the VS Code Integrated Terminal

The extension bundles a private CLI for the chat panel, which is separate from the `claude` command in your PATH. To connect the CLI to VS Code (for diff viewing and IDE context):

```bash
# Inside a CLI session, link it to the open VS Code window
/ide
```

This gives the CLI access to the IDE MCP server (file open, diff view, diagnostics).

---

## Extension vs CLI — When to Use Each

| Scenario | Extension | CLI |
|---|---|---|
| Exploring code in the open editor | Preferred — Code Lens + `@` attach | Works |
| Long multi-file editing session | Preferred — persistent panel, history | Works |
| CI / scripted automation | Not applicable | Required |
| `--print` / JSON output capture | Not applicable | `--print --output-format json` |
| `!` shell mode | Not available | `!command` |
| `/loop`, `/goal` (autonomous runs) | Not available | Full support |
| `/batch` (parallel worktree tasks) | Not available | Full support |
| Reviewing diffs inline | Preferred — plan mode + diff editor | Works with `/ide` |
| MCP management | `/mcp` panel | `claude mcp add/list/remove` |
