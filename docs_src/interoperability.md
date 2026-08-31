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
4. Non-dry-run writes run a live security freshness preflight before files are written; stale-intel blocks can only be bypassed via a valid signed waiver (`references/security-waivers.log.csv` + `AGENTTEAMS_WAIVER_SIGNING_KEY`).

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
- Both write modes enforce the same live security freshness preflight used by the main render path, including the same signed-waiver exception model.

Bundle artifacts are written under `references/interop/<source>-to-<target>/` and include:

1. `team-manifest.cai.json`
2. `interop-manifest.json`
3. `routing-map.json`
4. `instructions-map.json`
5. `compatibility-report.md`

### The Durable Canonical Format (`canonical`)

`canonical` is a recognized value for `--framework` (as an interop **target**) and
`--interop-source-framework` (as a source) — an interop-only pseudo-framework, not a
rendering target. It writes/reads the durable exploded on-disk form of the CAI document
(default location `<project>/.agentteams/canonical/`):

1. `team.cai.json` — project-level data (schema version, instructions binding, MCP servers,
   framework-owned configuration, source metadata).
2. `agents/<slug>.md` — human-editable Markdown with YAML front matter (canonical capability
   vocabulary + handoffs).
3. `skills/<slug>/SKILL.md` — first-class skills with co-located files.
4. `references/**` — carried non-agent reference content.

Export to canonical:

```bash
agentteams \
  --interop-from /path/to/source/agents \
  --interop-source-framework copilot-vscode \
  --framework canonical \
  --output /path/to/project/.agentteams/canonical
```

Import from canonical:

```bash
agentteams \
  --interop-from /path/to/project/.agentteams/canonical \
  --interop-source-framework canonical \
  --framework claude \
  --output /path/to/project/.claude/agents
```

Both dispatch through the same CAI pipeline and the same live security freshness preflight
as every other interop write path. `--interop-mode` keeps its existing two values and
meanings; `bundle` mode is refused for the canonical target (bundle artifacts would land
inside the canonical directory and corrupt its `references/` tree on load). MCP servers
carried in a canonical `team.cai.json` re-validate against `mcp-server.schema.json` —
including the `security_review` hard gates — at import time, so a hand-edited weakening
fails re-import instead of silently round-tripping.

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
5. `domain-boundary.md` — clarifies the memory-index vector-mode boundary vs project-level retrieval-integrator contracts (so consumers don't conflate them).

The bridge also writes target-framework entry files (e.g. `CLAUDE.md`, `.claude/*` for claude target) at the output root. **These writes are destructive** under `--bridge-refresh` and non-destructive (fence-aware) under `--bridge-merge`.

Bridge freshness checks:

```bash
agentteams \
  --bridge-from /path/to/source/agents \
  --framework claude \
  --output /path/to/project \
  --bridge-check
```

`--bridge-check` is read-only: it verifies bridge freshness against the source manifest and does not perform the write-path security freshness preflight.

### First Run vs Subsequent Refresh

| First time generating the bridge for a project | `--bridge-refresh` (creates target entry files from scratch) |
| Subsequent refresh after source agents change, consumer entry files have been customized | **`--bridge-merge`** (fence-aware non-destructive update) |
| Verifying freshness without writing | `--bridge-check` |

`--bridge-refresh` overwrites consumer `CLAUDE.md` / `.claude/*` with terse bridge-stub content. If your team has rich entry files, use `--bridge-merge`. Consumer-managed sections should live OUTSIDE the bridge's `<!-- AGENTTEAMS-BRIDGE:BEGIN ... -->` fences so the merge logic preserves them.

### Canonical as a bridge source

`canonical` is also a recognized `--bridge-source-framework` value: point `--bridge-from` at
a canonical directory's root (the one holding `team.cai.json`, not its `agents/` subdirectory
— pointing at the subdirectory misdetects as `copilot-vscode`) and the bridge reads
`agents/*.md` directly. `--bridge-check` against a canonical source covers both `agents/*.md`
and `team.cai.json` itself, so a hand-edit to instructions/MCP/framework-extension data (which
lives inside `team.cai.json`, not as sibling files) is caught, not just agent-file changes.

### Generic bridge target — no native adapter required

`--framework generic` is a bridge-only target (pair with `--bridge-from`) for a consumer with
no `agentteams` framework adapter of its own. It emits only the framework-agnostic pair-dir
artifacts (manifest, inventory, quickstart, entrypoint, domain-boundary) — zero native consumer
entry files (no `CLAUDE.md`, no `AGENTS.md`). The generated quickstart points the consumer at
the durable canonical tree (`.agentteams/canonical/`, see above) as the fuller source of truth,
and at the exact command to generate one if it doesn't already exist alongside the bridge.

### Portable team package (`--package-team`)

A durable canonical directory plus its generic bridge can also be packaged as one portable
`.zip` — see [CLI Reference: Portable Team Package](cli-reference.md#portable-team-package).
Standalone mode (mutually exclusive with all three modes above and every other standalone CLI
op), not part of the three interoperability modes proper: it composes Mode B's canonical export
with Mode C's generic-target bridge into one distributable artifact rather than adding a fourth
mode of its own.

---

## Directional Coverage

All six registered frameworks are valid CAI interop **sources and targets**
(durable-canonical-agent-format plan, Phase F):

1. `copilot-vscode`
2. `copilot-cli`
3. `claude`
4. `goose`
5. `agents-md`
6. `codex` (thin, prep-scoped: delegates AGENTS.md rendering to the agents-md adapter)

Round-trip fidelity is exact where a target has an equivalent concept and honestly
degraded where it doesn't — surfaced via `compatibility-report.md` in bundle mode, never
silently. Notes per framework:

1. `goose` recipes capture title/description/instructions plus recipe-level configuration
   (`recipe_parameters` / `recipe_response` / `recipe_retry`, builtin extension scoping)
   through the CAI `framework_extensions.goose` bucket; handoffs round-trip as `sub_recipes`
   (true delegation) and `load(...)` context references.
2. `agents-md` and `codex` sources carry no front matter, so capabilities/handoffs land
   inferred-or-empty on export (best-effort by nature).
3. `canonical` additionally serves as the durable intermediate: any framework can export to
   it and import back from it with near-lossless fidelity (see the canonical section above).
   Modeled fields (name, description, body, capabilities/handoffs, skills, MCP servers)
   round-trip exactly; framework-specific keys not yet modeled travel through the
   `raw_front_matter` and `capabilities.raw` escape hatches so they are preserved rather
   than silently dropped. Known remaining coarseness (documented per framework in
   `capability_map.py`'s module docstring) is surfaced, not hidden.

Convert (`--convert-from`) and bridge (`--bridge-from`) coverage is narrower than interop:
`goose` is a convert/bridge target, while `agents-md`/`codex` remain generate-only for those
two paths (their interop support is the CAI path). See the
[CLI Reference](cli-reference.md#feature-support-by-framework) feature-support matrix for the
per-path detail.

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
