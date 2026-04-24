# Interoperability

AgentTeams includes a dedicated interoperability feature family for moving or exposing agent infrastructure across frameworks without losing team intent.

---

## Why This Exists

Real projects often need to:

1. keep one framework as canonical source of truth,
2. run the same team in a different runtime, or
3. maintain compatibility interfaces for adjacent systems.

The interoperability family supports those workflows through three explicit modes.

---

## Three Interoperability Modes

| Mode | CLI Entry | Primary Use | Source Canonical Docs |
|---|---|---|---|
| Format migration | `--convert-from` | Rewrite an existing team into target framework format | Preserved prose, rewritten wrappers |
| CAI interop pipeline | `--interop-from` | Normalize to canonical representation then emit target | Preserved prose + optional bundle artifacts |
| Lightweight bridge | `--bridge-from` | Expose source team to a target runtime without full regeneration | Source remains canonical |

### What Is The Difference?

Use this quick rule:

1. Choose `--convert-from` when you want a direct one-step format migration from source framework files to target framework files.
2. Choose `--interop-from` when you want normalization through the Canonical Agent Interface (CAI) and optional interop bundle artifacts before/alongside target emission.
3. Choose `--bridge-from` when you want runtime compatibility entrypoints while keeping source agent documentation canonical and not fully regenerated.

Practical distinction:

1. `--convert-from` changes wrapper format around existing authored content.
2. `--interop-from` passes through a canonical representation layer designed for transport/inspection.
3. `--bridge-from` does not replace source docs; it creates a lightweight target-facing interface over them.

---

## Mode A: Format Migration (`--convert-from`)

Use this when you want a target framework version of an existing team while preserving agent body prose.

```bash
agentteams \
  --convert-from /path/to/source/agents \
  --framework claude \
  --output /path/to/project/.claude/agents
```

Behavior:

1. Preserves agent body markdown.
2. Rewrites front matter/wrapper format for target framework.
3. Converts instructions naming (`copilot-instructions.md` <-> `CLAUDE.md`) as needed.

---

## Mode B: CAI Interop Pipeline (`--interop-from`)

Use this when you want canonical normalization and optional compatibility bundle artifacts.

```bash
agentteams \
  --interop-from /path/to/source/agents \
  --framework copilot-cli \
  --interop-mode bundle \
  --output /path/to/project/.github/copilot
```

Modes:

- `direct`: write target framework files only.
- `bundle`: write target framework files plus interoperability artifacts.

Bundle artifacts are written under `references/interop/<source>-to-<target>/` and include:

1. `team-manifest.cai.json`
2. `interop-manifest.json`
3. `routing-map.json`
4. `instructions-map.json`
5. `compatibility-report.md`

---

## Mode C: Lightweight Bridge (`--bridge-from`)

Use this when you need target runtime entrypoints that reference source canonical infrastructure without regenerating source agent docs.

```bash
agentteams \
  --bridge-from /path/to/source/agents \
  --framework claude \
  --output /path/to/project
```

Bridge artifacts are written under `references/bridges/<source>-to-<target>/`:

1. `bridge-manifest.json`
2. `agent-inventory.md`
3. `quickstart-snippet.md`
4. `entrypoint.md`

Bridge freshness checks:

```bash
agentteams \
  --bridge-from /path/to/source/agents \
  --framework claude \
  --output /path/to/project \
  --bridge-check
```

---

## Directional Coverage

All three frameworks are supported in all six directional pairings:

1. `copilot-vscode -> copilot-cli`
2. `copilot-vscode -> claude`
3. `copilot-cli -> copilot-vscode`
4. `copilot-cli -> claude`
5. `claude -> copilot-vscode`
6. `claude -> copilot-cli`

---

## Automation Support

Bridge automation is available through repository workflows:

1. `.github/workflows/bridge-maintenance.yml` for scheduled bridge refresh and check operations.
2. `.github/workflows/bridge-watchdog.yml` for stale-run monitoring and issue escalation.

---

## API Reference Links

For module-level API details, see:

1. [convert API](api-reference/convert.md)
2. [interop API](api-reference/interop.md)
3. [bridge API](api-reference/bridge.md)
4. [CLI Reference](cli-reference.md)
