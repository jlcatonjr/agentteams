---
name: recall
description: Memory-index retrieval via agentteams --query-index. Use BEFORE grep for broad 'where' or thematic questions about this project.
---

# /recall — Memory-Index Retrieval

For broad 'where is X' or thematic questions, query the agentteams memory-index before falling back to grep. **Lexical first** — it is the default and the shipped agent protocol:

```
agentteams --query-index "<the user's question, quoted>" --query-k 5
```

Retry with `--query-strategy vector` when **either** (a) lexical returns zero hits, **or** (b) the lexical top-1 has no content-word overlap with the query, **or** (c) the question is purely thematic with no concrete term to match on.

(Some installations require `--description PATH` for read-only queries — pass the project brief if so; use `--self` when maintaining agentteams itself.)

## Fallback policy

`non-blocking-file-read-then-search` (declared in the index): if lexical returns no/weak hits, try `--query-strategy vector`, then fall back to Grep / Glob. Never block on the index.

Each hit carries a `confidence` field — treat `reliable` as actionable, `candidate` as worth opening before relying on it, and `weak` as noise.

## Caveats

- Index mode is `sparse-tfidf-cosine` — keyword-aware, NOT semantic   embeddings. There is no embedding model (`vector_model_id` is null);   `vector` means cosine over sparse tf-idf term vectors. Synonyms and   paraphrases may miss.
- Index covers durable sources (work summaries, CHANGELOG, plans),   NOT code or the gitignored `tmp/` scratch tree.
- Index is rebuilt explicitly via `--refresh-index`, not on file save.
- For **code / API** questions, use `/code-recall` instead.
