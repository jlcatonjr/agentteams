# Interoperability Patterns

## When to Use This Guide

Read this guide if you:

- Have an existing agent team in one framework and need it to work in another
- Are unsure whether to use `--convert-from`, `--interop-from`, or `--bridge-from`
- Need compatibility artifacts for tooling that consumes agent manifests
- Want lightweight target-framework entrypoints that keep your source team as the single source of truth

For the concise reference on all three modes, see [Interoperability](interoperability.md). This guide adds decision guidance and worked patterns.

---

## Choosing a Mode

```
Does target runtime need fully standalone agent files?
  ├── Yes → Are you moving permanently to the new framework?
  │            ├── Yes → --convert-from  (format migration)
  │            └── No  → --interop-from  (dual-framework with CAI normalization)
  └── No  → --bridge-from  (lightweight reference artifacts; source stays canonical)
```

| Mode | Source docs stay canonical? | Target files are standalone? | CAI bundle artifacts? |
|---|---|---|---|
| `--convert-from` | No — target becomes the new canonical | Yes | No |
| `--interop-from` | Depends on usage | Yes | Optional (`--interop-mode bundle`) |
| `--bridge-from` | **Yes** | No — reference source | No |

---

## Pattern 1: One-Shot Framework Migration

**Goal:** You are moving permanently from `copilot-vscode` to `claude`. Source files are no longer maintained after the migration.

```bash
agentteams \
  --convert-from /path/to/project/.github/agents \
  --framework claude \
  --output /path/to/project/.claude/agents
```

What happens:
1. Every agent's body prose is preserved.
2. Front matter is rewritten for the Claude format.
3. Target-framework instructions are written (for Claude targets, `CLAUDE.md`).
4. Live security freshness preflight runs before writing. Stale intelligence blocks the write unless a valid signed waiver exists in `references/security-waivers.log.csv`.

After migration, validate converted output and preserve rollback history before deciding whether to archive or keep the source directory as canonical.

---

## Pattern 2: Dual-Framework Team (CAI Interop)

**Goal:** You maintain a `copilot-vscode` team as canonical source but need a `copilot-cli` version that stays in sync with re-generation.

```bash
# Full bundle (interop artifacts + target files):
agentteams \
  --interop-from /path/to/project/.github/agents \
  --interop-source-framework copilot-vscode \
  --framework copilot-cli \
  --interop-mode bundle \
  --output /path/to/project/.github/copilot
```

Bundle artifacts appear in `references/interop/copilot-vscode-to-copilot-cli/`:

| File | Contents |
|---|---|
| `team-manifest.cai.json` | Canonical Agent Interface manifest |
| `interop-manifest.json` | Source-to-target field mapping |
| `routing-map.json` | Agent slug routing equivalences |
| `instructions-map.json` | Instructions file mapping |
| `compatibility-report.md` | Human-readable translation summary |

Refresh the target team whenever the source is updated:

```bash
agentteams \
  --interop-from /path/to/project/.github/agents \
  --framework copilot-cli \
  --interop-mode direct \
  --output /path/to/project/.github/copilot \
  --overwrite
```

---

## Pattern 3: Bridge (Source Stays Canonical)

**Goal:** You want Claude users to access your `copilot-vscode` team without regenerating or duplicating source documentation.

```bash
agentteams \
  --bridge-from /path/to/project/.github/agents \
  --bridge-source-framework copilot-vscode \
  --framework claude \
  --output /path/to/project
```

Bridge artifacts appear in `references/bridges/copilot-vscode-to-claude/`:

| File | Purpose |
|---|---|
| `bridge-manifest.json` | Source-to-target agent resolution map |
| `agent-inventory.md` | Human-readable list of agents accessible through the bridge |
| `quickstart-snippet.md` | CLAUDE.md snippet for users to add to their project |
| `entrypoint.md` | Canonical entry point for Claude routing through bridge |

Source agent documentation is never modified. The bridge references it by path.

### Checking Bridge Freshness

```bash
agentteams \
  --bridge-from /path/to/project/.github/agents \
  --framework claude \
  --output /path/to/project \
  --bridge-check
```

`--bridge-check` is read-only: it compares current source-file hashes to the hashes recorded in `bridge-manifest.json` and reports stale, missing, or new source files. Use it in CI to detect when bridge artifacts need refresh.

### Refreshing a Bridge

```bash
agentteams \
  --bridge-from /path/to/project/.github/agents \
  --framework claude \
  --output /path/to/project \
  --bridge-refresh
```

`--bridge-refresh` rewrites bridge artifacts to reflect the current source manifest. It cannot be combined with `--bridge-check`.

---

## Security Preflight Behaviour

All write-path interoperability operations (convert, interop direct, interop bundle, bridge-refresh) run the same live security freshness preflight as main generation. If threat intelligence is stale and no valid signed waiver exists, the write is blocked.

`--bridge-check` is read-only and does **not** trigger the security preflight.

---

## Supported Conversion Directions

All six directional combinations are supported:

| Source → Target | `--convert-from` | `--interop-from` | `--bridge-from` |
|---|---|---|---|
| `copilot-vscode` → `copilot-cli` | ✓ | ✓ | ✓ |
| `copilot-vscode` → `claude` | ✓ | ✓ | ✓ |
| `copilot-cli` → `copilot-vscode` | ✓ | ✓ | ✓ |
| `copilot-cli` → `claude` | ✓ | ✓ | ✓ |
| `claude` → `copilot-vscode` | ✓ | ✓ | ✓ |
| `claude` → `copilot-cli` | ✓ | ✓ | ✓ |

---

## Troubleshooting

### Security preflight blocked the conversion

See the [Security Hardening Guide](security-hardening-guide.md) for waiver creation and offline mode options.

### Bridge check reports stale but source hasn't changed

Bridge check compares source file hashes and path inventory against `bridge-manifest.json`. Stale means one of the following: source file content hash changed, a source file is missing, or a new source file exists. Run `--bridge-refresh` to synchronize.

### `--interop-mode bundle` created a compatibility-report.md with warnings

The compatibility report documents any agent sections or attributes that could not be fully translated between frameworks. Review the warnings and manually adjust the target files if fidelity is required. Warnings do not block the write.
