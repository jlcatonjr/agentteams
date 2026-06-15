# Integrating continue.dev into agentteams — Feasibility, Hurdles & Gaps

**Date:** 2026-06-15
**Author:** investigation across the agentteams codebase + primary-source web research
**Status:** DRAFT → audited (see §8 Audit Findings appended after adversarial + conflict review)

---

## 1. Executive summary

agentteams currently integrates target tools in **two distinct mechanisms**, and "the same manner as claude and copilot" means both:

1. **Framework adapters** — a `FrameworkAdapter` subclass per target (`claude`, `copilot-vscode`, `copilot-cli`) that post-processes generated agent content into the target's on-disk conventions. This is the *primary build path* (`build_team.py … --framework <id>`).
2. **Bridges** — a separate, non-destructive mechanism (`--bridge-*`) that lets one framework reuse another's canonical agent infrastructure via fenced pointer files, without regenerating the team.

**Headline feasibility verdict:** Adding `continue-dev` as a **framework adapter is mechanically bounded** — the contract is 6 methods + registration in ~3 places, plus a builder template and pipeline wiring. *Audit caveat:* "well-bounded" holds **only once the per-agent file representation is settled** (a rule `.md` file vs a `config.yaml` block vs a Cloud Agent `.md`). The adapter contract assumes *one file per agent with a single extension* (`base.py` `finalize_output_path`); if continue.dev's true unit is config-blocks-in-one-file, `render_agent_file`/`get_file_extension` will not map cleanly and the estimate grows. See §4.2 hurdle 3 and §8 Conflict C5. So: **mechanically bounded, but product-degraded** (next paragraph).

**The hard blocker is semantic, not mechanical.** agentteams' core product is an **orchestrator that delegates to specialized agents** (the handoff model). continue.dev's *documented, supported* product is **single-assistant-at-a-time**: there is no public orchestrator→specialist delegation primitive. A real subagent mechanism exists but is **beta, CLI-only, and described by Continue itself as "internal only for now while testing"** (the `subagent` model role; PR #9128, merged 2026-01-15). So a continue.dev integration would, on the documented surface, **degrade the team to N independent assistants + rules with no native delegation** — a loss of the product's central value that is **categorically deeper than the `claude` adapter's handoff handling, not equivalent to it.** The `claude` adapter also sets `supports_handoffs() = False` and uses `manifest` delivery — but Claude Code's *runtime* then **recovers** delegation by resolving subagents from the emitted `.claude/agents/*.md` files. continue.dev's IDE documents **no equivalent runtime recovery**, so the delegation is lost outright rather than relocated. (Audit correction: an earlier draft called this "not worse in kind" — that was self-cancelling and is retracted; see §8 Conflict C2.)

**Net recommendation:** Feasible to ship as a framework adapter targeting `config.yaml` (`schema: v1`) + `.continue/rules/*.md` + `.continue/mcpServers/*.yaml`. Treat the multi-agent topology as a *known-degraded* mapping and document it. Defer or scope-flag a bridge target until the integration value is proven. **Mitigate format churn** by pinning `schema: v1`, emitting YAML (never legacy `config.json`/`.prompt`), and avoiding deprecated fields.

---

## 2. How agentteams integrates a target today (the "same manner" bar)

### 2.1 Framework adapter contract

`FrameworkAdapter` (`agentteams/frameworks/base.py`) is the abstraction. **Required** to add a target:

| Member | Purpose |
|---|---|
| `framework_id` (property) | Unique string id, e.g. `"claude"`, `"copilot-vscode"` |
| `render_agent_file(content, agent_slug, manifest)` | Reformat a rendered agent into the target's file conventions (front-matter, etc.) |
| `render_instructions_file(content, manifest)` | Reformat the top-level instructions file |
| `get_file_extension(file_type)` | Extension per file type (`agent`/`instructions`/`builder`) |
| `supports_handoffs()` | Whether the target natively understands inline YAML handoff blocks |
| `get_agents_dir(project_path)` | Default output dir (e.g. `.claude/agents`, `.github/agents`) |

**Optional / inherited:** `render_skill_file` (no-op default; only Claude emits skills), `handoff_delivery_mode` (`native`/`manifest`/`none`), `extract_handoffs`, `finalize_output_path`. Protected helpers exist for slug→name, stripping YAML front-matter, and stripping handoff sections.

Concrete examples:
- **`ClaudeAdapter`** (`frameworks/claude.py`): emits `.claude/agents/<slug>.md`, strips VS Code YAML and injects Claude front-matter (`name`, `description`, `allowed-tools`), maps instructions to `CLAUDE.md`, **`supports_handoffs() = False`**, `handoff_delivery_mode() = "manifest"`, and *does* emit skill files.
- **`CopilotVSCodeAdapter`** (`frameworks/copilot_vscode.py`): emits `.github/agents/<slug>.agent.md` with rich VS Code YAML (`user-invokable`, `tools`, `model`, `agents`, `handoffs`), instructions → `.github/copilot-instructions.md`, **`supports_handoffs() = True`** (native, inline).

### 2.2 Registration & pipeline touch points

A target id is referenced in more places than just the adapter. To add `continue-dev` you touch (verified file:line refs from codebase investigation):

- **Adapter registry (3 copies, must stay in sync):** `build_team.py` `FRAMEWORKS` dict (~L82-86), `agentteams/interop.py` `_ADAPTERS` (~L22-26), `agentteams/convert.py` `_ADAPTERS` (~L43-47).
- **CLI choices:** `build_team.py` argparse `--framework` choices (~L159-164).
- **Builder template:** new `agentteams/templates/builder/team-builder-continue-dev.template.md`, plus the selector dict in `analyze.py` (~L1331-1335).
- **Instructions path logic:** `analyze.py` (~L1319-1321) and other `instructions_path` conditionals (`render.py` ~L328, `interop.py` ~L175/244, `drift.py` ~L317, `emit.py` ~L1365).
- **Skill emission conditionals** (`analyze.py` ~L1088, ~L1184-1187, ~L1485-1488) — only relevant if continue-dev emits skills.
- **Schema enum:** `schemas/team-manifest.schema.json` framework enum (`["copilot-vscode","copilot-cli","claude"]`).
- **Tests:** `tests/test_frameworks.py` (+ `tests/test_bridge.py` if bridge support is added).
- **Docs:** `README.md` capability table & default-dir list, usage docstring, CHANGELOG.

### 2.3 Bridges (the second mechanism)

A **bridge** lets a *target* framework reuse a *source* framework's already-generated agents via small, fenced pointer files — without regenerating the team. Generation flows **source → target** (e.g. `copilot-vscode → claude`). Code lives in `agentteams/bridge.py`; CLI flags `--bridge-from`, `--bridge-source-framework`, `--bridge-check`, `--bridge-merge`, `--bridge-refresh`, `--target-host-features`.

Key facts:
- Bridge-managed regions are wrapped in `<!-- AGENTTEAMS-BRIDGE:BEGIN <id> v=N --> … <!-- AGENTTEAMS-BRIDGE:END <id> -->` fences.
- **`--bridge-merge`** updates only fenced regions, preserving user content → the safe default.
- **`--bridge-refresh`** unconditionally overwrites target entry files → **destructive**; a 2026-05-27 incident silently replaced user content in `researchteam`/`collector-management`. Governed by `references/bridge-refresh-safety.md` Pre-Flight checks.
- Source/target frameworks are **hardcoded-validated** to `{copilot-vscode, copilot-cli, claude}` (`bridge.py` ~L99-102); target entry-file rendering is a per-framework branch in `_render_target_files()` (~L527-616).

Adding `continue-dev` as a **bridge target** therefore requires: the validation set, a new `_render_target_files()` branch (which `.continue/*` files to write + which regions to fence), detection heuristics in `interop.detect_framework()`, `host_features.py` namespaces, and bridge tests.

---

## 3. continue.dev investigation

All claims primary-source-cited; confidence flagged. Research date 2026-06-15.

### 3.1 What you'd generate
- **Canonical artifact: `config.yaml`** (`schema: v1`; required keys `name`, `version`, `schema`). Optional blocks: `models`, `context`, `rules`, `prompts`, `docs`, `mcpServers`, `data`. **No top-level `agents`/`subagents`/`orchestration` key.** [docs.continue.dev/reference]
- **Sidecar Markdown/YAML** under a repo `.continue/` folder: `.continue/rules/*.md`, prompt `.md` files, `.continue/mcpServers/*.yaml`. Global config at `~/.continue/config.yaml`. [docs.continue.dev/customize/deep-dives/configuration]
- **Version control is explicitly recommended** ("treat your `config.yaml` like code — commit it"). [docs understanding-configs]

### 3.2 Mapping agentteams concepts → continue.dev
| agentteams concept | continue.dev analog | Fidelity |
|---|---|---|
| Specialized agent's instructions | **Rule** (`.continue/rules/*.md`, system message; frontmatter `name`, `globs`, `regex`, `description`, `alwaysApply`) | Good (system-prompt analog) |
| Reusable command | **Prompt** (`.md` with `invokable: true` → slash command; user message) | Good |
| Tool access | **MCP servers** (`mcpServers:` block or `.continue/mcpServers/*.yaml`) | Good |
| Named specialist agent | A separate **named assistant/config** the human selects | Partial — no auto-selection |
| **Orchestrator → specialist handoff** | **No documented primitive** | **Missing** |

### 3.3 Modes & tools
Four modes: **Chat** (no tools), **Plan** (read-only), **Agent** (full tools/MCP, autonomous), **Edit** (inline). MCP supported (`stdio`/`sse`/`streamable-http`). [docs agent/how-it-works; docs mcp]

### 3.4 CLI / headless
`cn` (`@continuedev/cli`, Node 20+); headless `cn -p "…"` with `--format json`, loads Hub agents by slug (`cn --agent org/name`). New in 2025, still **beta**. [docs guides/cli]

### 3.5 Licensing & stability
- **Apache-2.0.** [github LICENSE]
- **⚠️ The `continuedev/continue` monorepo is read-only / no longer actively maintained**; dev moved to the CLI. (Audit resolution: the README is *unambiguous* on read-only status; the same-day commits seen during research are **automated release tagging** (e.g. `v1.2.22-vscode`), not active development. Treat the monorepo as **effectively frozen** — this is settled, not an open question.)
- **Format churn:** active `config.json → config.yaml` migration; `slashCommands`/`customCommands`/legacy `.prompt` files deprecated. Pin `schema: v1`, emit YAML only.

---

## 4. Feasibility, hurdles & gaps (synthesis)

### 4.1 Feasibility — FEASIBLE as a framework adapter
The adapter contract is small and the touch-point list is enumerable (§2.2). A `ContinueDevAdapter` would:
- `framework_id = "continue-dev"`, `get_agents_dir → .continue/`, `get_file_extension → .md` (rules) / `.yaml` (config),
- `render_agent_file` → emit each specialist as a **rule** Markdown file (system message) and/or a config block,
- `render_instructions_file` → emit/extend `config.yaml` + a top-level rule (the orchestrator persona),
- `supports_handoffs() = False`, **`handoff_delivery_mode() = "none"`**. (Audit correction: do **not** copy claude's `manifest` mode here. claude is strictly `manifest` — there is no `manifest`/`none` hybrid — and `manifest` is only meaningful because Claude Code's runtime consumes it. continue.dev documents no manifest consumer, so the honest mode is `none`. See §8 Conflict C3.)

### 4.2 Hurdles
1. **No documented multi-agent delegation (the big one).** agentteams' orchestrator/specialist topology has no faithful target on continue.dev's documented surface. Mitigation: emit specialists as rules/named configs; document the degradation. The native `Subagent`/`role: subagent`/`baseSystemMessage` mechanism is **beta, CLI-only, undocumented, "internal only"** — do not build the supported path on it.
2. **Format churn & a frozen monorepo.** config.json→config.yaml migration in flight; primary repo read-only. Risk of generating against a moving/uncertain target. Mitigation: pin `schema: v1`, YAML-only, avoid deprecated fields; add a schema-version guard.
3. **Agent-file representation ambiguity.** Whether a "specialist" is a rule file, a prompt file, a named config, or a `.continue/agents/*.md` Cloud Agent is **not cleanly one-to-one**. `.continue/agents/*.md` are **Cloud Agents (Mission Control, event-triggered automations)** — NOT IDE specialists — so they are the *wrong* target for an interactive team. (See conflict note in §8 re: `.md` vs `.yaml`.)
4. **Bridge support is a separate, larger effort** with hardcoded framework sets and a per-target render branch; also inherits the `--bridge-refresh` destructive-overwrite safety burden.
5. **Editor parity:** JetBrains plugin is now community-maintained ("use the CLI instead"); VS Code + CLI are the live surfaces. Config is shared across editors (good).

### 4.3 Gaps (things agentteams would lose or have to fake)
- Native orchestrator routing / handoffs (degrades to manual assistant-switching).
- First-class "skills" (Claude-only concept; continue has rules/prompts, not skills).
- A stable, documented "named agent file" convention to write into (the supported unit is `config.yaml` blocks, not per-agent files).

---

## 5. Comparable, reputable agent systems

Retrieval date 2026-06-15. **HARD** = disclosed/read directly; **EST** = third-party estimate; **MKT** = marketing claim (often stale). Claude Code & Copilot excluded (already supported). continue.dev reported separately above.

| System | File-based repo config? | Multi-agent in files? | Primary metric | Specialist concentration |
|---|---|---|---|---|
| Cursor | `.cursor/rules/*.mdc`, `AGENTS.md` | Partial (Custom Modes are app-settings/beta) | ~$29.3B val (Nov 2025, HARD); ~1M DAU (MKT) | High |
| Windsurf (Cognition) | `.windsurf/rules/*.md` → `.devin/rules/`, workflows | Yes (committed workflows) | >1M daily devs (MKT) | High (enterprise) |
| Cline | `.clinerules/`, `AGENTS.md` | No (Plan/Act only) | 4,322,950 installs (HARD); 63.3k★ | High |
| Roo Code | `.roomodes`, `.roo/rules-{mode}/` | **Yes (best-in-class: Orchestrator/Boomerang)** | 1,738,883 installs (HARD, **frozen**) | High — **SHUT DOWN 2026-05-15** |
| Kilo Code | `.kilocodemodes`/`.kilo/rules-{slug}/` | **Yes (Orchestrator)** | 1,209,115 installs (HARD); 20.1k★ | High |
| Aider | `CONVENTIONS.md`, `.aider.conf.yml` | No (single agent) | 46,235★; ~893k PyPI/mo | **Very high** (CLI/BYO-key) |
| Sourcegraph Cody | `.sourcegraph/*.rule.md` | No | 852,590 installs (HARD, **stale/pre-shutdown**) | **Highest** — enterprise-only since 2025-07 |
| Amazon Q Developer | `.amazonq/rules/*.md`, **`.amazonq/cli-agents/*.json`** | **Yes (named custom agents)** | 1,750,773 installs (HARD) | Broad (AWS free tier) |
| JetBrains AI / Junie | `.junie/`, `AGENTS.md` | Two-tier (not arbitrary named) | 11.4M JetBrains active users (HARD) | High (paid pro IDE) |
| Gemini Code Assist | `.gemini/config.yaml`, `GEMINI.md`, `.gemini/commands/*.toml` | One assistant + agent mode | **4,530,597 installs (HARD, highest)** ⚠️ **individual/Pro/Ultra tiers stop serving 2026-06-18 → folding into "Antigravity"** | Broadest (free tier) |
| Zed | `.rules`/`AGENTS.md` (first-match-wins) | Profiles (settings); no native subagents | 85,273★ | High (Rust power users) |
| Tabnine | `.tabnine/guidelines/*.md` | No | 9,578,093 installs (HARD but **legacy/stale listing**) | High (enterprise/air-gapped) |
| OpenAI Codex CLI | `AGENTS.md`, `.codex/config.toml`, **`.codex/agents/*.toml`** | **Yes (profiles + subagents)** | 91,174★; >5M WAU (whole *family*, HARD) | Broad (ChatGPT-bundled) |
| Augment Code | `.augment/rules/*.md`, `AGENTS.md` | IDE single + multi-agent in "Intent" | 762,316 installs (HARD) | High (large-codebase) |
| Goose (Block/AAIF) | `.goosehints`, recipes/sub-recipes YAML | **Yes (recipes, subagents)** | 49,449★; 6,500+ Block weekly internal | Internal/enterprise + MCP |

### 5.1 Ranking #1 — by user-base size (largest first)
Ranked on the most comparable hard signal available; cross-unit comparisons flagged.
1. **OpenAI Codex family** — >5M WAU (HARD; whole family, not CLI alone — overstates).
2. **Gemini Code Assist** — 4,530,597 VS Code installs (HARD; cleanest large install count). ⚠️ **But Google is sunsetting the individual/Pro/Ultra Gemini Code Assist + CLI tiers on 2026-06-18, folding them into a new "Antigravity" platform** — the install base is real but the product surface a generator would target is in flux. This materially weakens its case as a "reach" target (§8 Adversarial finding, major).
3. **Cline** — 4,322,950 installs (HARD).
4. **Amazon Q Developer** — 1,750,773 installs (HARD).
5. **Roo Code** — 1,738,883 installs (HARD but **frozen/shut down**; historical scale only).
6. **Kilo Code** — 1,209,115 installs (HARD).
7. **Sourcegraph Cody** — 852,590 installs (HARD but **lifetime/stale**).
8. **Augment Code** — 762,316 installs (HARD).
9. **Cursor** — no install/star signal; ~1M DAU (MKT) + $29.3B val (HARD) → likely top-tier but soft.
10. **Windsurf** — no install/star signal; >1M daily devs (MKT).
11–16. Codex CLI repo 91.2k★, Zed 85.3k★, Goose 49.4k★, Aider 46.2k★ (star interest, not users); **Tabnine 9.58M installs (legacy/stale → deliberately ranked low)**; **JetBrains** cumulative downloads (overstate; 11.4M active users) — large but uncomparable.

*Cleanest apples-to-apples cohort (active VS Code installs):* Gemini 4.53M > Cline 4.32M > Amazon Q 1.75M > Roo 1.74M(frozen) > Kilo 1.21M > Cody 0.85M(stale) > Augment 0.76M.

### 5.2 Ranking #2 — by specialist / professional-engineer concentration (most first)
1. **Sourcegraph Cody** — enterprise-only (no consumer tier).
2. **Aider** — terminal-only, BYO-key, git-native; benchmark leaderboard credibility magnet.
3. **Augment Code** — explicitly large/complex-codebase, enterprise.
4. **Tabnine** — privacy-first, on-prem/air-gapped, regulated orgs.
5. **Windsurf** — "Enterprise AI IDE" (JPMorgan, Dell).
6. **Cursor** — strongly pro/enterprise but free tier widens the tail.
7. **Zed** — Rust/performance power users.
8. **Roo / Kilo** (tie) — "AI dev team," per-team custom modes (Roo shut down).
9. **Cline** — enterprise push but courts non-technical builders too.
10. **Goose** — concentrated where used; limited external base.
11. **JetBrains AI / Junie** — gated to paid pro IDE base (2025 free tier widened).
12. **Amazon Q** — pro/cloud devs but free tier + AWS bundling broadens.
13. **Gemini Code Assist** — least concentrated (free tier + Google bundling).

### 5.3 Cross-cutting finding
**`AGENTS.md` is the single highest-coverage artifact** — read by Cursor, Windsurf, Cline, Codex CLI, Goose, Amazon Q, JetBrains (Junie), Zed, Augment, and continue.dev; an Agentic AI Foundation (Linux Foundation) standard as of April 2026. The systems whose *file-based multi-agent* shape best matches agentteams' orchestrator+specialists model are **Kilo Code** (live heir to Roo's scheme), **Amazon Q** (`.amazonq/cli-agents/*.json`), **OpenAI Codex CLI** (`.codex/agents/*.toml`), and **Goose** (recipes/subagents).

---

## 6. Recommendation

**The decision the audits surfaced (and that this report cannot make for you):** *Do you want continue.dev **specifically**, or the most leverage for the effort?* The report's own evidence shows these point in different directions, so §6 is framed as a fork rather than a single "ship it."

### Path A — Ship continue.dev specifically (honors the explicit request)
1. **Ship a `continue-dev` framework adapter** targeting `config.yaml` (`schema: v1`) + `.continue/rules/*.md` (+ `mcpServers`). Reuses the existing adapter contract.
   - **Precondition (do this first):** decide the per-agent file unit (rule `.md` vs `config.yaml` block vs Cloud Agent `.md` — note `.continue/agents/*.md` are *event-triggered Cloud Agents, not IDE specialists* and are the wrong target). Until this is fixed, "well-bounded" is not established (§4.2 hurdle 3, §8 C5).
2. **Document the multi-agent degradation as primary, not a footnote.** Specialists → rules/named configs; **no native orchestrator delegation** and **no runtime recovery** (unlike Claude Code). Use `handoff_delivery_mode() = "none"`, not `manifest` (§8 C2/C3).
3. **Be explicit that this is *less* than how `claude` is actually used.** For this repo, claude's delegation value is delivered by the **bridge** + Claude Code runtime (`CLAUDE.md` makes the `copilot-vscode → claude` bridge the canonical entry point), not the adapter alone. "Ship adapter, defer bridge" for continue.dev therefore does **not** reproduce "the same manner as claude" (§8 C1).
4. **Defer the bridge target — and treat the deferral as a safety-coverage gap, not just effort.** A continue.dev bridge would make `config.yaml` a destructive-overwrite target, but `references/bridge-refresh-safety.md`'s Pre-Flight checks are written only for claude entry files. Since Continue *recommends committing `config.yaml` "like code,"* it is exactly the unfenced-user-content case that caused the 2026-05-27 incident. **Precondition for any future bridge:** extend the safety doc to enumerate `config.yaml`/`.continue/*` and treat `config.yaml` as never-refresh/default-unfenced (§8 C9).

### Path B — Maximize reach/fidelity for the effort (what the evidence favors)
5. **Build an `AGENTS.md` emitter first.** A single well-formed `AGENTS.md` is read by ~10 targets at once — **including continue.dev** — and is now an Agentic AI Foundation (Linux Foundation) standard (formed Dec 2025). Highest coverage per unit effort (§5.3).
6. **Add one true file-based multi-agent target** that *preserves* the orchestrator model agentteams is built around: **Kilo Code** (`.kilocodemodes` + Orchestrator; live heir to Roo's scheme), **OpenAI Codex CLI** (`.codex/agents/*.toml`), or **Goose** (recipes/subagents). These keep delegation that continue.dev drops.
7. **For raw reach, prefer Cline** (4.32M installs, primary-verified, actively maintained, clean file config). **Do not lead with Gemini Code Assist** despite its 4.53M installs — its individual tiers sunset 2026-06-18 into "Antigravity" (§8 Adversarial, major).

**My recommendation:** if the goal is to support continue.dev *because users asked for it*, take **Path A** with the degradation documented up front and the two preconditions met. If the goal is to expand agentteams' reach generally, **Path B leads** — and because `AGENTS.md` covers continue.dev anyway, Path B partially satisfies the original request as a side effect. The two paths are sequenceable (A then B, or B then a thinner A), but they are **not** the undifferentiated "in parallel" the draft implied.

---

## 7. Confidence & open questions
- **High confidence:** agentteams adapter contract & touch points (read from code); continue.dev config.yaml schema, rules/prompts/MCP, Apache-2.0, single-assistant IDE model (primary docs).
- **Medium / verify:** exact representation of a "specialist" file in continue.dev (rule vs config vs Cloud Agent); whether `.continue/agents/*.md` (`.md`) vs `.continue/agents/*.yaml` (`.yaml`) is correct; monorepo "unmaintained" vs live-commit contradiction; subagent beta status changes.
- **Soft (MKT/stale):** Cursor/Windsurf DAU, Tabnine install/user counts, JetBrains cumulative downloads.

---

## 8. Audit Findings
*(populated after adversarial + conflict audits — see below)*
