# GitHub Copilot Privilege Configuration Reference

How to control what the GitHub Copilot agent in VS Code is allowed to do: tool execution, terminal commands, file system access, network access, and sandboxing.

---

## Configuration Layers

Copilot agent privileges are configured at three layers, each overriding the previous:

| Layer | File / Location | Who sets it |
|---|---|---|
| Organization policy | GitHub org settings → Copilot → Policies | GitHub org admin |
| User settings | VS Code User `settings.json` | Individual developer |
| Workspace settings | `.vscode/settings.json` (committed) | Repository |

Settings marked **ORG** in the sections below are enforced at the organization level and cannot be overridden by user or workspace settings.

---

## Permission Level (Session Scope)

The permission level is the coarsest control. It sets how the agent handles approvals for the current session.

**Configure the default for all new sessions:**

```json
// VS Code settings.json (user or workspace)
{
  "chat.permissions.default": "default"
}
```

**Change for the current session:** Click the permissions dropdown in the chat input area.

| Value | Behaviour |
|---|---|
| `default` | Uses your configured approval settings. Confirmation dialogs appear for actions that require them. |
| `bypassApprovals` | Auto-approves all tool calls without confirmation. Agent asks clarifying questions when uncertain. |
| `autopilot` | Auto-approves all tool calls. Auto-responds to clarifying questions. Keeps iterating until task complete. |

> **Warning:** `bypassApprovals` and `autopilot` bypass all per-tool, terminal, and URL approval settings. Only use them when you understand the scope of what the agent may do.

**Enable `autopilot` advanced mode** (separate model judges task completion):

```json
{
  "chat.autopilot.advanced.enabled": true
}
```

**Global auto-approve across all workspaces** (equivalent to always-on `bypassApprovals`):

```json
{
  "chat.tools.global.autoApprove": true    // ORG — can be managed at org level
}
```

Toggle from inside a chat session: `/yolo` or `/autoApprove` to enable; `/disableYolo` or `/disableAutoApprove` to disable.

---

## Tool-Level Approval

Individual tools (file editors, MCP tools, extension-contributed tools) can each be configured to run with or without a confirmation dialog.

**Manage via Command Palette:**

```
⇧⌘P → Chat: Manage Tool Approval
```

Shows all tools grouped by source. Check a tool to pre-approve it; uncheck to always require manual approval.

**Prevent a tool from ever being auto-approved** (it will always prompt):

```json
{
  "chat.tools.eligibleForAutoApproval": {
    "myTool": false,
    "github_copilot.createFile": false
  }
}
```

Setting a tool to `false` here means approval scope choices like "allow for this workspace" are not offered for that tool. It always asks.

**Reset all saved tool approvals:**

```
⇧⌘P → Chat: Reset Tool Confirmations
```

---

## Terminal Command Approval

The terminal tool runs any shell command, so approvals are per-command rather than per-tool.

**Configure auto-approve and deny rules:**

```json
{
  "chat.tools.terminal.autoApprove": {
    "mkdir": true,
    "ls": true,
    "cat": true,
    "/^git (status|log|diff|show\\b.*)$/": true,
    "npm run": true,
    "python3 -m pytest": true,
    "rm": false,
    "del": false,
    "/dangerous/": false,
    "sudo": false,
    "curl": false,
    "wget": false
  }
}
```

- `true` — auto-approve; runs without confirmation
- `false` — require manual approval under non-bypass permission levels (`default`). **Not a reliable safety boundary under `autopilot`/`bypassApprovals`:** per the warning above, those modes bypass all per-tool, terminal, and URL approval settings, so a `false` denial here cannot be relied on to block a dangerous command. To hard-block dangerous commands, do not use `autopilot`/`bypassApprovals` — use a non-bypass permission level (`default`) so the `false` rules apply.
- Patterns wrapped in `/` are treated as regular expressions matched against the command or subcommand

**Block file writes outside the workspace** (experimental):

```json
{
  "chat.tools.terminal.blockDetectedFileWrites": "outsideWorkspace"
}
```

**Ignore built-in default safe/blocked command lists** (use only your rules):

```json
{
  "chat.tools.terminal.ignoreDefaultAutoApproveRules": true
}
```

**Disable terminal auto-approval entirely** (every command prompts — ORG managed):

```json
{
  "chat.tools.terminal.enableAutoApprove": false
}
```

---

## URL and Network Approval

When the agent fetches a URL (e.g. the `#web/fetch` tool), VS Code applies a two-step approval — once for the request, once for the response content — to protect against prompt injection.

**Configure per-domain or per-URL auto-approval:**

```json
{
  "chat.tools.urls.autoApprove": {
    "https://api.github.com/*": true,
    "https://*.internal.example.com/*": true,
    "https://example.com/api/*": {
      "approveRequest": true,
      "approveResponse": false
    },
    "https://untrusted.site": false
  }
}
```

`true` — auto-approve both request and response from this domain  
`false` — always require approval for this domain  
Object form — approve request and/or response independently

Domains in VS Code's **Trusted Domains** list are auto-approved for requests but still require response review.

---

## Agent Sandboxing (Preview)

Sandboxing restricts what terminal commands can read, write, or reach on the network. When sandboxing is active, terminal commands run without a confirmation dialog because they are contained.

**Enable sandboxing** (ORG — can be set at org level):

```json
{
  "chat.agent.sandbox.enabled": "on"
}
```

| Value | Behaviour |
|---|---|
| `off` | No sandboxing (default) |
| `on` | Full isolation: file system + network restricted |
| `allowNetwork` | File system restricted; outbound network allowed freely |

**When `on` or `allowNetwork`:**
- Commands can read: workspace folders, sandbox temp folder, per-command paths
- Commands can write: current working directory and subdirectories only
- Home directory reads (`$HOME`) are denied by default
- Terminal commands run without the confirmation dialog

**Per-path file system rules** (macOS):

```json
{
  "chat.agent.sandbox.FileSystem.mac": {
    "allowRead": ["/Users/me/.config/myapp"],
    "allowWrite": ["."],
    "denyWrite": ["./secrets/"],
    "denyRead": ["/etc/passwd"]
  }
}
```

`denyWrite` and `denyRead` take precedence over `allowWrite` and `allowRead`. Globs are not supported — use exact paths.

For Linux: `chat.agent.sandbox.FileSystem.linux` (same structure).

---

## Network Filtering (Non-Sandbox)

Filter which domains agent tools (fetch, integrated browser) can contact even without full sandboxing:

```json
{
  "chat.agent.networkFilter": true,
  "chat.agent.allowedNetworkDomains": [
    "api.github.com",
    "*.internal.example.com"
  ],
  "chat.agent.deniedNetworkDomains": [
    "malicious.site"
  ]
}
```

`deniedNetworkDomains` always takes precedence over `allowedNetworkDomains`. Both support wildcards. When both lists are empty and `networkFilter` is true, all domains are blocked.

---

## Agent Enable / Disable

```json
{
  "chat.agent.enabled": false    // disables agent mode entirely
}
```

**Limit agent turns per session:**

```json
{
  "chat.agent.maxRequests": 20   // default varies; set to cap autonomous iterations
}
```

---

## GitHub Copilot Completions (per language)

Control whether Copilot provides inline completions for specific languages:

```json
{
  "github.copilot.enable": {
    "*": true,
    "plaintext": false,
    "markdown": false,
    "env": false
  }
}
```

---

## Workspace Instruction Files

These files shape agent behaviour via natural language instructions — useful for encoding privilege-like rules in plain text that the agent reads as context.

### `.github/copilot-instructions.md`

Workspace-level instructions loaded into every Copilot chat session in this repository.

```markdown
# Copilot Instructions

## Restrictions
- Do not modify files in `secrets/` or `.env` without explicit instruction.
- Do not run `git push` or `git push --force` without asking first.
- Do not install packages without confirming the package manifest change is intended.
- Read-only access only to `src/legacy/`. Do not edit these files.

## Style
- All edits must be consistent with the existing code style.
- Write tests for any new function.
```

Enable reading this file (defaults to true when the file exists):

```json
{
  "github.copilot.chat.codeGeneration.useInstructionFiles": true
}
```

### `.github/agents/*.agent.md` (agentteams pattern)

In repositories using the agentteams `copilot-vscode` framework, each agent role has its own `.agent.md` file in `.github/agents/`. These files are the instruction sets for that agent role and encode its scope of action directly. Privilege constraints belong in the agent's instruction file, not in VS Code settings.

---

## Common Privilege Profiles

### Fully autonomous (no prompts) — use with care

> **Caution — use with care.** Under `autopilot` the per-tool/terminal/URL approval settings are bypassed (see the warning above), so this profile is not a safety boundary. The blanket entries below are broad: `python3: true` auto-approves arbitrary code execution (e.g. `python3 -c '...'`), and `git: true` auto-approves `git push`, `git push --force`, and `git reset --hard`. Prefer scoped patterns (e.g. `python3 -m pytest`, `/^git (status|log|diff|show\b.*)$/`) and reserve this profile for throwaway/sandboxed workspaces.

```json
{
  "chat.permissions.default": "autopilot",
  "chat.tools.terminal.autoApprove": {
    "npm": true,
    "git": true,
    "python3": true
  }
}
```

### Require approval for all terminal commands

```json
{
  "chat.permissions.default": "default",
  "chat.tools.terminal.enableAutoApprove": false
}
```

### Sandboxed (contained, no prompts needed)

```json
{
  "chat.agent.sandbox.enabled": "on",
  "chat.agent.allowedNetworkDomains": ["api.github.com", "registry.npmjs.org"]
}
```

### Read-only analyst (no file edits, no shell)

Use `chat.permissions.default: "default"` and deny edits and terminal at the tool level via "Chat: Manage Tool Approval" (uncheck file editing and terminal tools). Complement with `.github/copilot-instructions.md` instructions prohibiting edits.

### Block specific dangerous commands, allow safe ones

> **Caution:** the `false` denials below only block under non-bypass permission levels (`chat.permissions.default: "default"`). They are bypassed by `autopilot`/`bypassApprovals` (see [Terminal Command Approval](#terminal-command-approval)), so do not rely on this profile while a bypass mode is active.

```json
{
  "chat.tools.terminal.autoApprove": {
    "/^git (status|log|diff|show\\b.*)$/": true,
    "npm test": true,
    "python3 -m pytest": true,
    "rm": false,
    "sudo": false,
    "curl": false,
    "/rm\\s+-[rf]/": false
  }
}
```

---

## Applying Changes

VS Code settings (`settings.json`) take effect immediately for the next session. Changes to the permission level dropdown apply to the current session only. Reset tool approvals via Command Palette if existing approvals conflict with new settings.
