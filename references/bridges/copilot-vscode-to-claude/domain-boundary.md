# Domain Boundary — Three Retrieval Surfaces

AgentTeams exposes three **distinct** retrieval surfaces that address different questions and **must not be conflated**:

1. **Memory-index** (`memory_index`, `--query-index`) — a stdlib-only sparse tf-idf vector-space ranking over **durable prose** (work summaries, CHANGELOG, durable plans). `vector_runtime_mode: sparse-tfidf-cosine`.
2. **Code index** (`code_index`, `--query-code`) — a stdlib-only sparse tf-idf ranking over **code**: local scripts (`local-script`), the external API modules they import (`api-module`), and API documentation (`api-doc`), filterable with `--code-kind`. A **gitignored local cache** (`references/code-index/`), never committed.
3. **Project retrieval-integrator** — a project-level validation contract (e.g. `mode: relational-metadata` against project data tables). Independent of both indexes above.

The memory-index (prose) and the code-index (code) are siblings but cover disjoint content; neither participates in the single-slot project retrieval-integrator contract.

Bridge direction: `copilot-vscode` → `claude`.
