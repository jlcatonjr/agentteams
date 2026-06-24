# Claude Code Privilege Configuration Reference

How to control what Claude Code is allowed to do: file edits, shell commands, directory access, and tool-level allow/deny rules.

---

## Configuration Files

Settings are merged in this order — later files take precedence for overlapping keys:

| File | Scope | Committed? |
|---|---|---|
| `~/.claude/settings.json` | All projects for this user | No |
| `.claude/settings.json` | This project, all users | Yes |
| `.claude/settings.local.json` | This project, this user | No |

**All privilege settings live under the `permissions` key in these JSON files.**

---

## Permission Mode

The mode determines how Claude handles tool calls that aren't explicitly allowed or denied.

**Set for a project (persisted in VS Code):**
```json
// .vscode/settings.json
{
  "claudeCode.initialPermissionMode": "default"
}
```

**Cycle interactively in the CLI REPL:** `Shift+Tab`

**Set for one session:**
```bash
claude --permission-mode acceptEdits
```

| Mode | Behaviour |
|---|---|
| `default` | Prompts before file edits and shell commands |
| `plan` | Claude describes all changes first; you approve before execution |
| `acceptEdits` | Auto-approves file edits; still prompts for shell commands |
| `auto` | Auto-approves safe actions via classifier; minimal prompts |
| `bypassPermissions` | No prompts at all (sandboxed environments only) |

---

## Allow Rules

Rules in `permissions.allow` are **always approved without prompting**, regardless of mode.

```json
// ~/.claude/settings.json  or  .claude/settings.json
{
  "permissions": {
    "allow": [
      "Bash(git *)",
      "Bash(npm run *)",
      "Bash(python3 -m pytest *)",
      "Read(//Users/me/shared-data/**)",
      "Edit(src/**)"
    ]
  }
}
```

---

## Deny Rules

Rules in `permissions.deny` are **always blocked**, regardless of mode or allow rules. Deny takes precedence over allow.

```json
{
  "permissions": {
    "deny": [
      "Bash(rm -rf *)",
      "Bash(sudo *)",
      "Bash(git push --force *)",
      "Edit(.env)",
      "Edit(secrets/**)"
    ]
  }
}
```

---

## Pattern Syntax

Every entry is `ToolName` or `ToolName(argument-pattern)`.

### Tool names

| Tool | What it covers |
|---|---|
| `Bash` | All shell commands |
| `Read` | File and directory reads |
| `Edit` | In-place file edits |
| `Write` | Full file writes (new or overwrite) |
| `WebFetch` | HTTP/HTTPS requests |
| `WebSearch` | Web searches |
| `mcp__<server>__<tool>` | A specific MCP tool, e.g. `mcp__github__create_pr` |

### Argument patterns

Patterns use shell-style globs applied to the tool's first argument (the command string for `Bash`, the path for `Read`/`Edit`/`Write`):

| Pattern | Matches |
|---|---|
| `Bash(git *)` | Any `git` command |
| `Bash(git log*)` | `git log`, `git log --oneline`, etc. |
| `Bash(npm run *)` | Any `npm run` script |
| `Read(src/*)` | Files one level deep in `src/` |
| `Read(src/**)` | All files recursively in `src/` |
| `Read(//absolute/path/**)` | Absolute path (note double slash `//`) |
| `Edit(.env)` | Exactly the file `.env` |
| `Bash(python3 -c ' *)` | Any `python3 -c` one-liner |

**Absolute paths require a `//` prefix** (single slash is relative to the project root):

```json
"Read(//Users/jamescaton/githubrepositories/shared/**)"
```

Omitting the argument entirely matches all uses of that tool:

```json
"allow": ["WebFetch"]   // allow all web fetches
"deny": ["WebSearch"]   // deny all web searches
```

---

## Additional Directory Access

By default Claude can only read files within the current working directory. Grant access to other directories with `additionalDirectories`:

```json
{
  "permissions": {
    "additionalDirectories": [
      "/Users/me/shared-libs",
      "/Users/me/other-project/src"
    ]
  }
}
```

This is read-only access for context. It does not grant edit or bash permissions in those directories — add `Edit` or `Bash` rules for that.

---

## Common Privilege Profiles

### Read-only analyst (no writes, no shell)

```json
{
  "permissions": {
    "allow": ["Read(**)", "WebFetch"],
    "deny": ["Bash", "Edit", "Write"]
  }
}
```

### Git-only shell access

```json
{
  "permissions": {
    "allow": ["Bash(git *)"],
    "deny": ["Bash(rm *)", "Bash(sudo *)", "Bash(curl *)", "Bash(wget *)"]
  }
}
```

### Test runner (pytest only, no arbitrary shell)

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 -m pytest *)",
      "Bash(.venv/bin/python -m pytest *)",
      "Edit(src/**)",
      "Edit(tests/**)"
    ],
    "deny": ["Bash(sudo *)", "Bash(rm *)", "Bash(curl *)", "Bash(wget *)"]
  }
}
```

### Full autonomous (no prompts, use with care)

> **Caution — use with care.** Auto-approval runs actions without confirmation; reserve it for throwaway/sandboxed workspaces. Unlike `bypassPermissions`, `deny` rules still apply in `auto` mode, so keep minimal deny guardrails (e.g. `Bash(rm *)`, `Bash(sudo *)`, `Bash(git push --force *)`) even here.

Set `claudeCode.initialPermissionMode: "auto"` in VS Code settings, or use `--permission-mode auto` for a session. Keep the minimal `deny` guardrails above to block specific dangerous operations.

### Locked down (human approves everything)

```json
// .claude/settings.json — committed to repo so all users get it
{
  "permissions": {
    "deny": ["Bash(rm *)", "Bash(sudo *)", "Bash(git push *)", "WebFetch"]
  }
}
```

Combined with `claudeCode.initialPermissionMode: "plan"` to require human review before any action.

---

## Hooks as Enforcement

For enforcement logic that patterns can't express (e.g., "allow `npm install` only if `package.json` was modified"), use hooks:

```json
// .claude/settings.json — hooks are keyed by event name. `matcher` is a
// tool-name pattern (a string, e.g. "Bash" or "Edit|Write"); each match carries
// a `hooks` array of command entries. Each hook receives the event as JSON on
// stdin (fields include `tool_name`, `tool_input`, and — for PostToolUse —
// `tool_output`); read what you need from that JSON. There is no result-
// template variable.
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "cmd=$(jq -r '.tool_input.command'); case \"$cmd\" in *'npm install'*) git diff --name-only HEAD | grep -q package.json || { echo 'npm install requires a package.json change' >&2; exit 2; } ;; esac"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path' | xargs -r prettier --write 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

A hook **exiting with code 2** blocks the tool call and surfaces its stderr to Claude. Exit 0 allows the call (and may emit a JSON decision on stdout for finer control); other non-zero exits are non-blocking errors (notably, **exit 1 does not block**).

---

## Disabling All Customizations

To run with no CLAUDE.md, no MCP servers, no hooks, and no memory — useful for debugging or a clean baseline:

```bash
claude --safe-mode
# Equivalent for non-interactive/CI launches:
CLAUDE_CODE_SAFE_MODE=1 claude -p "…"
```

This does not change any config files. It applies for that session only.

---

## Applying Changes

Changes to JSON settings files take effect immediately for new sessions. The VS Code extension panel picks up user-level settings (`~/.claude/settings.json`) without restart; project settings (`.claude/settings.json`) are re-read each time a session starts.

---

## Verification

```bash
# Show active settings and their sources
claude session
> /settings

# Show what's loaded
> /doctor

# Check hooks
> /hooks
```
