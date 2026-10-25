# How it works

Agent Teams Module transforms a single project description into a complete, coordinated AI agent team through a four-stage pipeline.

---

## Pipeline overview

```
brief.json / brief.md
        │
        ▼
   ┌─────────┐
   │  ingest  │  Parse and validate the project description
   └────┬────┘
        │ normalized description dict
        ▼
   ┌─────────┐
   │ analyze  │  Select archetypes, detect tools, build manifest
   └────┬────┘
        │ team manifest dict
        ▼
   ┌────────┐
   │ render  │  Fill templates with resolved placeholders
   └────┬───┘
        │ (output_path, rendered_content) pairs
        ▼
   ┌──────┐
    │ emit  │  Write agent files to framework-specific target directories
   └──────┘
```

### Stage 1 — Ingest (`agentteams/ingest.py`)

Reads your project description from a `.json` or `.md` file and validates it against `agentteams/schemas/project-description.schema.json`. Returns a normalized Python dict.

### Stage 2 — Analyze (`agentteams/analyze.py`)

Builds the **team manifest** from the description dict. Key decisions made in this stage:

- **Project type classification** — determines whether the project is software, writing, data-pipeline, research, or documentation
- **Archetype selection** — picks the right domain agent mix from the template library
- **Tool importance classification** — decides whether each tool gets an operational doc (reference/skill), a lightweight reference, or no dedicated artifact (tools are never agents)
- **Authority hierarchy construction** — orders sources by rank for the agents to cite
- **Placeholder resolution** — auto-fills all `{UPPER_SNAKE_CASE}` tokens; flags `{MANUAL:*}` tokens for human review

### Stage 3 — Render (`agentteams/render.py`)

Loads each template from `templates/` and substitutes every `{PLACEHOLDER}` with its resolved value from the manifest. Returns a list of `(relative_path, content)` pairs.

### Stage 4 — Emit (`agentteams/emit.py`)

Writes all rendered files to the target framework directory:

- `copilot-vscode` -> `.github/agents/`
- `copilot-cli` -> `.github/agents/` (the same folder as VS Code; handoffs removed)
- `claude` -> `.claude/agents/`
- `goose` **(beta)** -> `.goose/recipes/`
- `agents-md` **(beta)** -> `.agents/`
- `codex` **(beta)** -> `.codex/agents/`
- `grok` **(beta)** -> `.grok/agents/`

Also writes framework instructions at the parent of the framework agents directory:

- `copilot-vscode` and `copilot-cli` -> `.github/copilot-instructions.md`
- `claude` -> `.claude/CLAUDE.md`
- `goose`, `agents-md`, `codex` and `grok` -> repo-root `AGENTS.md`

It also generates `SETUP-REQUIRED.md` for unresolved manual placeholders, and runs post-generation audit/security scan when requested.

Fresh generation is one of three build paths; see [Three build paths](#three-build-paths).

---

## 4-tier agent taxonomy

Every generated team contains agents from four hierarchical tiers.

```
Tier 1: Orchestrator
   └── Routes all work; enforces constitutional rules;
       opens and closes every multi-agent session

Tier 2: Governance Agents
   └── Navigator, Security, Code-Hygiene, Adversarial,
       Conflict-Auditor, Conflict-Resolution, Cleanup,
       Agent-Updater, Agent-Refactor, Repo-Liaison,
       Git-Operations
       Each owns a cross-cutting concern (structure, safety,
       consistency, documentation) rather than a deliverable

Tier 3: Domain Agents
   └── Work-Summarizer (always included),
       Primary-Producer, Quality-Auditor, Technical-Validator,
    Post-Production-Auditor (context-triggered),
    Format-Converter, Reference-Manager, Output-Compiler, ...
       Each owns a production workflow (drafting, auditing,
       converting, compiling)

Tier 4: Workstream Experts
   └── One per project component (e.g. @auth-module-expert,
       @tasks-api-expert)
       Each owns one deliverable unit end-to-end:
       component brief → domain agent commission → review
```

### How tiers interact

```
User Request
    │
    ▼
Orchestrator ──► Workstream Expert (prepares Component Brief)
    │                  │
    │                  ▼
    │           Domain Agent (executes production)
    │                  │
    ▼                  ▼
Governance Agents (audits, reviews, clearances)
```

The orchestrator **routes without producing**. Domain agents **produce without scoping**. Workstream experts **scope without producing**. Governance agents **audit without producing**.

### Optional outcome-verification layer

Teams that include `@post-production-auditor` can add an optional post-production workflow extension (Workflow 10C) in the orchestrator user-editable section. This workflow validates claimed completed outcomes against source-of-truth state and enforces fail-closed closure behavior on `FAIL`/`INCONCLUSIVE` verdicts.

The analyzer auto-selection for `@post-production-auditor` uses contextual cue pairing (operation/state-change + verification/proof cues) to reduce false positives from single broad keywords. Teams can always force inclusion through `selected_archetypes` in the project description.

To avoid forced propagation to teams that do not include this archetype, Workflow 10C and its routing row are added outside FENCED sections (in user-editable gaps), not inside generated fenced blocks.

### Agent knowledge updates

Generated agent teams are designed to keep their documentation current automatically as projects evolve. Two mechanisms work together:

**Automatic update triggers** — `@agent-updater` is invoked at the close of knowledge-mutating workflow steps. Specifically, it runs in Workflow 1, Workflow 2, Workflow 3 (when corrections were made), Workflow 5 (when issues were found), Workflow 6, Workflow 7, Workflow 8, and Workflow 9. Drift detected by `--check` is also an explicit trigger.

**Periodic Knowledge Re-verification** — Before any plan step executes (Workflow 10), `@technical-validator` verifies the factual claims stated in that step's inputs, outputs, and notes against current on-disk state. Steps with unverified claims are held until the user confirms. If `--check` reports template drift, `@agent-updater` re-renders affected files and calls `@technical-validator` before the next workflow step proceeds.

**Epistemic guard on audit workflows** — `@adversarial` runs at the start of Workflow 5 (Consistency Review) before any audit conclusions are surfaced, and before any deletion plan in Workflow 8 (Code Hygiene Audit). This prevents agents from auditing on the basis of unchallenged assumptions about project state.

These three mechanisms together ensure that agents do not assert stale beliefs as facts and that documentation lag — the primary cause of agent errors in long-lived teams — is detected and resolved automatically.

---

## Template library

Templates in `templates/` are Markdown files with `{PLACEHOLDER}` tokens. The library provides:

| Tier | Templates |
|------|-----------|
| Orchestrator | `universal/orchestrator.template.md` |
| Governance | `universal/` (11 tier-2 governance agent templates) |
| Domain | `domain/` (15 archetype templates + 6 tool templates, incl. `content-enricher` and `work-summarizer`) |
| Workstream Expert | `workstream-expert.template.md` (one template, rendered per component) |
| Builder | `builder/` (framework-specific team-builder templates; some frameworks share one) |

In addition to the 11 always-included tier-2 governance templates, `universal/` also contains the tier-1 Orchestrator template.

The `domain/` template library includes `content-enricher` as a selectable archetype in addition to the standard archetypes listed in the table above.

See [Template Authoring](template-authoring.md) for placeholder conventions and authoring rules.

---

## Framework adapters

The same template library targets every registered framework through adapters in `agentteams/frameworks/`. Which adapters are Stable and which are Beta is set by the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md):

| Framework | Agent Format | Entry Point |
|-----------|-------------|-------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | VS Code Copilot agent panel |
| `copilot-cli` | `.agent.md` with YAML front matter, handoffs removed | Copilot CLI (`copilot`) |
| `claude` | Claude front matter `.md` + `CLAUDE.md` | Claude Code |
| `goose` **(beta)** | Recipe YAML (`.goose/recipes/*.yaml`) + repo-root `AGENTS.md`/`.goosehints` | `goose run --recipe` |
| `agents-md` **(beta)** | Cross-tool `AGENTS.md` standard (plain `.md`) + `.agents/` | Any AGENTS.md-aware tool |
| `codex` **(beta)** | Codex custom agents (`.codex/agents/*.toml`) + repo-root `AGENTS.md` | Codex CLI |
| `grok` **(beta)** | Grok Build custom agents (`.grok/agents/*.md`) + repo-root `AGENTS.md` | `grok --agent .grok/agents/<slug>.md` |

Each adapter in `agentteams/frameworks/` knows the file naming conventions, front-matter schema, and handoff delivery mode for its target framework.

### Handoffs and the team builder

Each framework receives the team's routing (its handoffs) in the form it can use, and gets a
framework-native **team builder** agent that can regenerate or extend the team.

| Framework | Handoffs | Team builder |
|-----------|----------|--------------|
| `copilot-vscode` | Native inline YAML | `.agent.md` agent |
| `copilot-cli` | Runtime manifest (`references/runtime-handoffs.json`) when handoffs are present | `.agent.md` agent |
| `claude` | Runtime manifest (`references/runtime-handoffs.json`) when handoffs are present | Claude agent `.md` |
| `goose` **(beta)** | Native: orchestrator handoffs become `sub_recipes`, deeper edges become `summon` `load(...)` | Runnable `team-builder.yaml` recipe |
| `agents-md` **(beta)** | Routing preserved in `references/runtime-handoffs.json` | `.agents/team-builder.md` |
| `codex` **(beta)** | Native: written into each agent's `developer_instructions` | `.codex/agents/team-builder.toml` |
| `grok` **(beta)** | Runtime manifest (`references/runtime-handoffs.json`) | Grok Build custom agent |

Where handoffs go into the manifest, the visible prompt files carry no inline handoff sections, but the
routing stays available to bridge layers and other runtime tooling. Goose emits one recipe per agent
plus a team brief at the repo-root `AGENTS.md` and a `.goosehints` integrator. `agents-md` writes the
framework-neutral repo-root `AGENTS.md` read by many AI coding tools, plus per-specialist detail under
`.agents/`; `--convert-from` and `--bridge-from` do not target it (interop does). The [feature-support matrix](cli-reference.md#feature-support-by-framework)
shows which commands each framework supports.

### Three build paths

- **Path A, fresh generation:** build a team from a brief (`--description`).
- **Path B, format migration:** move an existing team to another framework (`--convert-from`).
- **Path C, lightweight bridge:** keep a source team canonical and give other tools a thin interface to
  it (`--bridge-from`).

The [Interoperability](interoperability.md) tab covers paths B and C and the CAI interop pipeline in
detail.

---

## Using a coding agent to run the pipeline

The four-stage pipeline can be driven entirely by a coding agent. Instead of running CLI commands manually, you describe what you want in plain language and the agent drafts the brief, generates the team, resolves placeholders, and audits the result.

See [Agent-Assisted Setup](agent-assisted-setup.md) for the full workflow and ready-to-use prompt templates.
