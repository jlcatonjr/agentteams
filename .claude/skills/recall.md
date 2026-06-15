---
name: recall
description: Memory-index retrieval via agentteams --query-index. Use BEFORE grep for broad 'where' or thematic questions about this project.
---

# /recall — Memory-Index Retrieval

For broad 'where is X' or thematic questions, query the agentteams memory-index before falling back to grep:

```
agentteams --query-index "<the user's question, quoted>" --query-strategy vector --query-k 5
```

(Some installations require `--description PATH` for read-only queries — pass the project brief if so.)

## Fallback policy

`non-blocking-file-read-then-search` (declared in the index): if vector returns no/weak hits, try `--query-strategy lexical`, then fall back to Grep / Glob. Never block on the index.

## Caveats

- Index mode is `sparse-tfidf-cosine` — keyword-aware, NOT semantic   embeddings. Synonyms and paraphrases may miss.
- Index covers durable sources (work summaries, CHANGELOG, plans),   NOT code or the gitignored `tmp/` scratch tree.
- Index is rebuilt explicitly via `--refresh-index`, not on file save.
