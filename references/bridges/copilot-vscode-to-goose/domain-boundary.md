# Domain Boundary — Memory-Index Vector vs Retrieval-Integrator

The agentteams `memory_index` module ships a `--query-strategy vector` mode that is a **local sparse tf-idf vector-space ranking for memory-history retrieval only**. It is stdlib-only, deterministic, and scoped to durable text sources (work summaries, CHANGELOG, durable plans).

It is **separate** from any project-level retrieval-integrator validation contract (e.g., relational metadata retrieval against project data tables). When a project's retrieval contract has `mode: relational-metadata`, that is independent from the memory-index's `vector_runtime_mode: sparse-tfidf-cosine` — the two retrieval surfaces address different questions and must not be conflated.

Bridge direction: `copilot-vscode` → `goose`.
