# Goose Privilege Configuration Reference

How to control what Goose is allowed to do: tool execution, file writes, shell access, and extension capabilities.

---

## Configuration Files

| File | Scope |
|---|---|
| `~/.config/goose/config.yaml` | All sessions for this user |

All privilege settings for Goose live in this single file. There is no project-level override file.

---

## Session Mode (`GOOSE_MODE`)

The mode is the coarsest privilege control. It governs whether Goose asks for confirmation before using tools.

```yaml
# ~/.config/goose/config.yaml
GOOSE_MODE: auto
```

| Value | Behaviour |
|---|---|
| `auto` | Fully autonomous. No confirmation prompts. Edits, shell commands, and extension calls run immediately. |
| `smart_approve` | Prompts before file edits, destructive shell commands, and extension calls. Read-only operations proceed silently. |
| `approve` | Prompts before every tool call without exception. |
| `chat` | Conversation only. All tool use is disabled. No file access, no shell. |

**Override for one session without changing the file:**

```bash
GOOSE_MODE=approve goose session
GOOSE_MODE=smart_approve goose run --recipe .goose/recipes/orchestrator.yaml
```

**Set interactively:**

```bash
goose configure
# → "goose mode" → choose mode
```

---

## Per-Tool Permissions

Individual tools within an extension can be configured independently of the global mode.

**Set interactively:**

```bash
goose configure
# → "Tool Permission" → choose extension → choose tool → set permission
```

| Value | Behaviour |
|---|---|
| `always_allow` | Tool runs without prompting, regardless of `GOOSE_MODE` |
| `ask_before` | Always prompts before this tool runs, regardless of `GOOSE_MODE` |
| `never_allow` | Tool is blocked entirely |

Per-tool permissions take precedence over the session mode. Setting a tool to `never_allow` blocks it even when `GOOSE_MODE: auto`.

---

## Extension Enable / Disable

Extensions are the source of all Goose tools. Disabling an extension removes all of its tools.

```yaml
# ~/.config/goose/config.yaml
extensions:
  developer:
    enabled: true        # ← set to false to disable entirely
    type: builtin
    name: developer
    timeout: 300
    bundled: true

  memory:
    enabled: false       # disabled — no memory tools available
    type: builtin
    name: memory
    bundled: true
```

To disable the developer extension (no file read/write, no shell):

```yaml
extensions:
  developer:
    enabled: false
    type: builtin
    name: developer
    bundled: true
```

---

## Restricting Which Tools an Extension Exposes

The `available_tools` key limits which tools from an extension are loaded. An empty list (default) means all tools are available.

```yaml
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
    available_tools:
      - read_file        # only allow reading files
      - list_directory   # and listing directories
      # write_file, shell, etc. are not loaded
```

This is more targeted than disabling the extension entirely — useful when you want read access but not write or shell.

---

## Common Privilege Profiles

### Read-only (no writes, no shell)

> **Caution:** with `GOOSE_MODE: auto`, read-only is enforced *solely* by the `available_tools` allowlist — there is no prompt and no deny rule, so a single missing or mistyped entry silently grants autonomous writes/shell. Prefer a non-autonomous mode (`smart_approve` or `approve`) for read-only work, and/or add `never_allow` on `text_editor`/`shell` as defense-in-depth so a typo cannot grant silent writes.

```yaml
GOOSE_MODE: auto
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
    available_tools:
      - read_file
      - list_directory
```

### Fully interactive (prompt before everything)

```yaml
GOOSE_MODE: approve
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
```

### Fully autonomous (no prompts) — use with care

> **Caution — use with care.** `GOOSE_MODE: auto` runs edits, shell commands, and extension calls immediately with no confirmation. This profile is not a safety boundary; reserve it for throwaway/sandboxed workspaces. To constrain it, add `never_allow` on dangerous tools (e.g. `shell`) or trim `available_tools`.

```yaml
GOOSE_MODE: auto
extensions:
  developer:
    enabled: true
    type: builtin
    name: developer
    timeout: 300
    bundled: true
```

### Chat only (no tools)

```yaml
GOOSE_MODE: chat
```

### Shell gated (prompts before shell), file access permitted

Set `GOOSE_MODE: smart_approve` (which *prompts* before shell commands — it does not block them; the user can still approve any command). To actually *block* shell, use `available_tools` to exclude shell tools explicitly, or set `never_allow` on `shell`.

---

## Applying Changes

Changes to `config.yaml` take effect on the next session start. Running sessions are not affected. To apply immediately:

1. Save `~/.config/goose/config.yaml`
2. Start a new session: `goose session`

The VS Code extension reads the same config file and picks up changes on the next session.

---

## Verification

```bash
goose info --verbose   # shows GOOSE_MODE and extension status
goose doctor           # connectivity and config health check
```

Inside a session: the mode is shown at session start and can be confirmed with the first tool prompt (or absence of one).
