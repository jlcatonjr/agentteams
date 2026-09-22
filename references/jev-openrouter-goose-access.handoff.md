# Handoff → agentteams: accessing TypeSafe **Jev-1.13** via OpenRouter from goose

**From:** baseAgent (LLM-battery / model-integration work) · **To:** agentteams maintainers · **Date:** 2026-09-22
**Status:** informational handoff + reference adapter. **Action needed:** decide whether goose should support
Jev, and if so add a decisions-API adapter (goose's stock OpenRouter provider cannot call it — see below).

---

## TL;DR (the one thing to know)

**`typesafe/jev-1.13` is a *decisions* model, not a chat model.** It **rejects** `/chat/completions` with
HTTP 400:

> `"typesafe/jev-1.13 is a decisions model and cannot be used with the chat/completions endpoint. Use the /api/alpha/decisions endpoint instead."`

Goose's OpenRouter provider talks to `/chat/completions`, so **configuring Jev as a normal goose OpenRouter
model will not work.** Using it from goose requires a small **custom adapter** that speaks the decisions API
and maps the typed result back into goose. A working reference adapter already exists in baseAgent
(`experiments/battery/run_inloop.py:JevDecisionsBackend`).

Note it is also **absent from the default `/api/v1/models` catalog** (so model-list autodiscovery won't find
it), but it is real: `GET /api/v1/models/typesafe/jev-1.13/endpoints` returns full metadata.

---

## Model facts (verified live)

| field | value |
|---|---|
| id | `typesafe/jev-1.13` |
| name | TypeSafe: Jev 1.13 ("System One" structured-decision model) |
| modality | `text → decisions` (returns a typed choice, not free text) |
| pricing | prompt **$0.042 / M tokens**, completion **free ($0)** |
| context | 32,000 tokens (`max_completion_tokens` 28,800) |
| `supported_parameters` | **`[]`** — no `temperature`/`top_p`/etc. (nothing to sample-tune) |

Because it exposes no sampling params, it is **not a hyperparameter-tuning candidate**; treat it as a fixed,
purpose-built decision engine.

---

## The decisions API (reverse-engineered, working)

`POST https://openrouter.ai/api/alpha/decisions`
Headers: `Authorization: Bearer <OPENROUTER_API_KEY>`, `Content-Type: application/json`

**Request body**
```json
{
  "model": "typesafe/jev-1.13",
  "state": "<free-text context / observation the decision is about>",
  "questions": {
    "<name>": {
      "type": "choice",                     // discriminator ∈ {"choice","score","noul"}
      "instructions": "<what to decide>",
      "criteria": { "0": "<option 0 desc>", "1": "<option 1 desc>", "2": "<option 2 desc>" }
    }
  }
}
```
- `criteria` is a **record** (map of option-key → description), NOT an array.
- `questions` may hold several named questions; each returns its own answer.

**Response**
```json
{
  "model": "typesafe/jev-1.13-20260917",
  "answers": {
    "<name>": { "type": "choice", "choice": "2",
                "probabilities": { "0": 0.12, "1": 0.01, "2": 0.87 }, "confidence": 0.81 }
  },
  "usage": { "input_tokens": 335, "output_tokens": 38, "cost": 1.4e-05 },
  "id": "gen-dec-...", "provider": "TypeSafe"
}
```
`answers.<name>.choice` is the chosen option key (as a string). You also get a full **probability
distribution + a confidence score** — richer than a plain LLM, and useful for routing/abstention.

### Minimal curl (key off-argv, avoids `ps` leakage)
```bash
printf 'header = "Authorization: Bearer %s"\n' "$OPENROUTER_API_KEY" | \
curl -sS https://openrouter.ai/api/alpha/decisions \
  -H "Content-Type: application/json" --config - \
  -d '{"model":"typesafe/jev-1.13","state":"You may move to one candidate cell.",
       "questions":{"move":{"type":"choice","instructions":"pick the option with the most sugar",
       "criteria":{"0":"sugar 5","1":"sugar 2","2":"sugar 9"}}}}'
# → answers.move.choice == "2"
```

---

## What goose integration needs

Goose cannot use Jev through the stock OpenRouter provider (chat/completions). Two viable paths:

1. **Custom decisions provider/adapter (recommended).** A thin provider that: takes a discrete
   choice/classification request (options + instructions + context), POSTs to `/api/alpha/decisions`,
   and returns `answers.<name>.choice` (+ optionally `confidence`/`probabilities`). This is exactly what
   baseAgent's `JevDecisionsBackend` does — it parses an encoded `N: description` option list out of a
   prompt into `criteria`, calls the endpoint, and returns the chosen index. Port that logic into a goose
   provider/extension.
2. **Document Jev as decisions-only** in the agentteams OpenRouter notes and route only *structured-choice*
   steps to it (tool/option selection, classification, gating), never free-form generation.

**Fit:** Jev is excellent as the "decide among these options" / classify / route step (fast, cheap — free
completion — typed + calibrated). It is unsuitable as a general chat/agent LLM. In an agentteams pipeline,
wire it to the *decision* seam, not the *authoring* seam.

**Auth:** same `OPENROUTER_API_KEY` as any OpenRouter model; keep it off argv (curl `--config` on stdin, or
an SDK that sets the header from env) — never on the command line.

---

## Reference implementation to port

`baseAgent/experiments/battery/run_inloop.py` → class `JevDecisionsBackend` (prompt→`criteria` parse, the
POST, cost accounting via `usage.cost`, index return). Routed by a `decisions: true` flag on the model
registry entry `{"slug":"typesafe/jev-1.13","pp":0.042,"cp":0.0,"tier":"low","decisions":true}`.

---

## Delivery note

> **[Delivered 2026-09-22]** This file has since been copied into agentteams at
> `references/jev-openrouter-goose-access.handoff.md`; the switch pathway is implemented in
> `scripts/jev-decisions-adapter.py` and documented in `references/jev-goose-pathway.md`. The
> original delivery instruction below is retained for provenance and is now moot.

This handoff was authored in baseAgent because the agentteams working tree was **not writable from the
current sandbox** (`operation not permitted`). To land it in agentteams, copy this file there (e.g.
`guides/` or `references/`) from an unsandboxed shell, or grant the sandbox write access to the agentteams
repo path and I will move it.
