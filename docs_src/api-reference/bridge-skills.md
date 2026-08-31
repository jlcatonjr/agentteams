# `bridge_skills`

Claude skill bodies emitted by the bridge. Carved out of [`bridge`](bridge.md) for the
CH-07 1000-line module ceiling; the renderers are re-exported from `agentteams.bridge`, so
existing imports keep working.

## The layout contract

Each renderer returns the **body** of a skill. The caller writes it to
`.claude/skills/<name>/SKILL.md`.

Claude Code discovers a project-level skill only as a **directory containing `SKILL.md`**
([docs](https://code.claude.com/docs/en/skills.md)). Three consequences shape this module and
its callers:

- A flat `.claude/skills/<name>.md` is **never loaded**. It is inert, not an error — nothing
  warns, and the retrieval layer simply degrades to grep. This was the 2026-08-07 finding:
  the skills had been emitted flat since introduction and had never once been reachable.
- The **directory name is the invocable command name** (`/recall`), not the `name:` key in
  front matter. Renaming the directory renames the command.
- `name` and `description` are both *optional* front matter (`name` defaults to the directory
  name; `description` falls back to the first paragraph). The renderers emit both anyway,
  because the description is what the model reads when deciding whether to invoke the skill.

## Public Surface

```python
def _render_recall_skill() -> str
def _render_code_recall_skill() -> str
```

Both are module-private by name but re-exported through `agentteams.bridge` for the emitter.

## Strategy guidance in the emitted bodies

Both skills instruct **lexical-first, vector-as-retry** — matching the agent-side
`memory_index_consultation` protocol. `--query-strategy` and `--code-query-strategy` both
default to `lexical` in the CLI, so the skills document the shipped default rather than
overriding it.

`vector` here means cosine similarity over **sparse tf-idf term vectors** — there is no
embedding model (`vector_model_id` is `null`), consistent with the stdlib-only constraint.
Both bodies carry that caveat so a reader does not expect semantic paraphrase matching.

## Related

- [`bridge`](bridge.md) — the emitter that writes these bodies to disk
- [`memory_index`](memory-index.md) / [`code_index`](code-index.md) — what the skills query
- [`plan_steps_todo`](plan-steps-todo.md), [`parallel_plan`](parallel-plan.md) — the other two
  host-feature-gated skills, emitted under the same directory contract
