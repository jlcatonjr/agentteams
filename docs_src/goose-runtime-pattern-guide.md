# Per-User Runtime Goose Pattern

Guidance for applications that use Goose *at runtime* to stand up a background, per-user
research or learning team — and, just as important, a hard rule about where **not** to put
`goose run`.

agentteams' Goose support is **design-time**: the bridge emits thin stub recipes that point at
your canonical source agents, and the quickstart runs the *development* orchestrator. This guide
covers the separate question of running Goose *inside a produced app*, per end user, which
agentteams does not scaffold.

## Overview

This guide explains:

1. **The latency rule (read first):** do **not** route latency-critical / interactive paths
   through `goose run`.
2. **Where Goose *does* fit:** a background, per-user research/learning tier.
3. **Per-user session isolation** — named sessions, the SQLite session store, and
   context-rotation caveats.
4. **A per-user research recipe** template.
5. **Invocation hygiene** for parseable, bounded headless runs.

It is a **pattern doc, not a scaffold** — agentteams does not emit runtime Goose orchestration.
A validated (spike-level) reference is cited under [References](#references).

## The latency rule (binding for interactive paths)

> **Do NOT route a latency-critical or interactive path through `goose run`.**

`goose run` is a one-shot subprocess with a cold model-load cliff (measured at roughly **~28 s
cold-start**, ~1.5–2 s warm, against a local Ollama runtime), **no token streaming**, and
non-deterministic tool loops (an agentic `GOOSE_MODE=auto` run can spin through tool calls
before returning). Those properties are fine for background work and wrong for a live turn:

| Property | Consequence for a live turn | Verdict |
|---|---|---|
| ~28 s cold model-load | The user's first turn stalls for half a minute | ✗ live |
| Subprocess-per-call | No warm in-process state; process spawn overhead each turn | ✗ live |
| No streaming | The user watches a blank box until the whole reply lands | ✗ live |
| Non-deterministic tool loop | Turn latency and shape vary run-to-run | ✗ live |

For the **live conversation path**, call the model directly (e.g. the local Ollama HTTP
endpoint the Goose backend is configured against) so you get warm state and streaming. Reserve
`goose run` for the **background/agentic tier** below.

## Where Goose fits: a background per-user research tier

A per-user **named** Goose session genuinely accumulates history across invocations, which makes
it the right unit for a *background* per-user research or learning team — a tier that runs
between turns, proactively, or on demand, and whose latency the user does not directly wait on.
The shape:

- one **named session per user** (per language / per topic area as needed),
- invoked headless, bounded, and parsed as JSON,
- every failure returns *nothing* — the live path never depends on it.

## Per-user session isolation

Goose persists sessions in a local SQLite store (`sessions.db`). To keep users' histories from
bleeding together:

- **Name sessions per user** — e.g. `--name <app>-<account>-<lang>`, with the account/language
  reduced to path-safe identifiers first (see the
  [runtime-security guide](runtime-security-guide.md) — never interpolate raw user input into a
  session name).
- **Isolate the session store** where the threat model requires it. Sharing one `sessions.db`
  across all users is acceptable only if named sessions are strictly namespaced and you trust
  the process boundary; for real multi-user isolation, give each user (or tenant) its own store
  path.
- **Rotate context.** A long-lived named session grows unboundedly; a background tier should
  cap or rotate the accumulated context (start a fresh session generation periodically, or prune)
  so a single user's history does not eventually blow the model's context window or slow every
  background run.

> These are **caveats to design for**, not features agentteams provides. Validate your isolation
> and rotation before promoting a spike to real multi-user scale.

## A per-user research recipe

Keep the recipe minimal and extension-free so a headless, bounded run returns a structured
dossier instead of spinning an open-ended tool loop:

```yaml
# .goose/recipes/<app>-research.yaml  (illustrative)
version: "1.0.0"
title: "Per-user background research"
description: "One bounded research turn for a single user; returns a JSON dossier."
parameters:
  - key: topic
    input_type: string
    requirement: required
  - key: language
    input_type: string
    requirement: optional
    default: "en"
# No extensions: the run should reason and return, not call tools.
extensions: []
prompt: |
  Research {{ topic }} for a learner in {{ language }}.
  Return ONLY a JSON dossier: {"summary": ..., "key_facts": [...], "subtopics": [...]}.
```

## Invocation hygiene

Invoke headless with bounded, parseable flags:

```bash
goose run \
  --recipe .goose/recipes/<app>-research.yaml \
  --name "<app>-<account>-<lang>" \
  -q --output-format json \
  --provider ollama --model "<local-model>" \
  --max-turns 3 \
  --no-profile \
  --params topic="<topic>" --params language="<lang>"
```

| Flag | Why |
|---|---|
| `-q --output-format json` | Machine-parseable output; strip any banner before the first `{`. |
| `--no-profile` | Loads **only** the recipe's extensions (none), so `GOOSE_MODE=auto` cannot spin the global developer/summon tool loop — the model reasons and returns the dossier. |
| `--max-turns N` | Hard upper bound on the agentic loop; keeps a background run finite. |
| `--name <per-user>` | Per-user session accumulation + isolation (above). |
| `--provider/--model` | Pin the local runtime explicitly so the background tier matches your app's configured backend. |

Then: parse the JSON (skip any leading banner), treat a non-zero exit / timeout / empty output
as "no result," and continue — the background tier is best-effort by construction.

## Best Practices

1. **Never put the live turn behind `goose run`.** Direct-call the local model for interactive
   replies; use Goose for the background tier only.
2. **One named session per user; namespace or isolate the store.** Sanitize identifiers before
   they reach a session name or path.
3. **Bound every headless run** — `--max-turns`, a subprocess timeout, and `--no-profile` to
   suppress the tool loop.
4. **Fail open, depend on nothing.** A background research run that errors, times out, or
   returns empty must return nothing and never block or corrupt the live path.
5. **Rotate context before scale.** Cap or rotate per-user session history before promoting a
   spike to real multi-user use.

## References

- **Design-time Goose support (for contrast):** the bridge emits stub recipes pointing at
  canonical source agents; the quickstart runs the development orchestrator — see the
  [Goose privileges](goose-privileges.md) and [backend switcher](https://github.com/jlcatonjr/AgentTeamsModule/blob/main/references/goose-backend-switcher.md)
  references for backend selection (local Ollama vs OpenRouter).
- **Reference implementation (a validated spike, not a production integration):** the LingoFriend
  project's `.goose/recipes/lingofriend-research.yaml` + `knowledge/goose_research.py` run one
  background research turn through `goose run` against a local Ollama runtime, using a per-account
  named session, `--no-profile` to suppress the tool loop, and `-q --output-format json` for a
  parseable dossier. It is deliberately **not** wired into the conversation (latency-critical)
  path, and every failure returns `None`. Treat it as evidence the pattern works, not as a
  production design.
- **Related guidance:** [Runtime Security for Served Apps](runtime-security-guide.md).
