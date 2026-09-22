# Jev-via-goose Pathway

Operational reference for switching an agentteams goose instance to TypeSafe
**Jev-1.13** using `scripts/jev-decisions-adapter.py`.

## What Jev is (and is not)

`typesafe/jev-1.13` is a **decisions** model, not a chat model. It **rejects**
`/chat/completions` (HTTP 400) and only answers a *structured choice*: given a
context and an enumerated option set, it returns a chosen key plus a calibrated
probability distribution and a confidence score. Prompt is billed at
$0.042 / M tokens; **completion is free**. It exposes **no sampling parameters**.

**It cannot author code or free text.** Do not attach it as goose's chat brain —
route only tool/option selection, classification, gating, and routing steps to it.
Full model facts and the decisions API contract:
`references/jev-openrouter-goose-access.handoff.md`.

## The adapter

`scripts/jev-decisions-adapter.py` (stdlib-only) is the pathway. Three surfaces:

| Surface | Use |
|---|---|
| `JevDecisionsClient` (import) | `choose(state, instructions, options) -> ChoiceResult` and raw `decide(state, questions)`. The primitive for decision-seam callers. |
| `--serve` (decision-seam shim) | Local chat-completions server. A prompt is treated as a Jev decision **only** when it carries the `[[JEV-CHOICE]]` sentinel and enumerates options; it is then translated to the decisions API and returned chat-shaped. Prompts without the sentinel are refused with a 400 (never answered with an integer). **All other models pass through** to real OpenRouter, so it is safe to leave running. |
| `--state/--instructions/--option` (CLI) | One-off decision for testing. |

The key is read from `OPENROUTER_API_KEY` in the environment and sent only in the
`Authorization` header — never on argv.

## How goose uses Jev (decision seam — NOT a model switch)

**Do not point `GOOSE_MODEL` at Jev globally.** A goose turn is a multi-message,
tool-laden conversation; Jev can only return one option key and issues no tool
calls, so it would break the agent loop on the first authoring turn. Jev is a
*decision seam*, invoked deliberately for a single structured-choice step — not
goose's chat brain.

Two supported ways to route a decision to Jev:

**A. Import surface (preferred, programmatic):**
```python
from importlib import import_module  # or import the file directly
client = JevDecisionsClient()                      # reads OPENROUTER_API_KEY
r = client.choose(state="board state ...",
                  instructions="pick the strongest move",
                  options={"0": "e4", "1": "d4", "2": "Nf3"})
# r.index, r.confidence, r.probabilities, r.cost
```

**B. Decision-seam shim (for a goose extension that can only POST HTTP):**
```sh
python3 scripts/jev-decisions-adapter.py --serve --port 8792   # leave running
```
The extension POSTs one structured-choice step, marked with the sentinel:
```
POST http://127.0.0.1:8792/api/v1/chat/completions
{"model":"typesafe/jev-1.13","messages":[{"role":"user",
  "content":"[[JEV-CHOICE]] pick the best move\n0: e4\n1: d4\n2: Nf3"}]}
```
The shim returns a chat-shaped reply whose content is the chosen key, with
`jev.confidence`/`jev.probabilities` attached. Any prompt lacking the sentinel is
refused (HTTP 400), so an ordinary goose turn cannot be silently mis-routed.

## Scope and limits

- Jev **cannot author** — it never emits code, prose, or tool calls, only an option
  key. Keep a normal chat model configured as goose's actual model.
- The shim answers only prompts that carry the `[[JEV-CHOICE]]` sentinel **and**
  enumerate options as `N: description` lines. Everything else gets a clean 400.
- The sentinel exists to prevent an incidental numbered list in an authoring turn
  from being mis-parsed as an option slate.
- Prefer the import surface (`JevDecisionsClient.choose`); the shim exists only for
  goose extensions that can reach Jev solely over HTTP.

## Verification

```sh
# Direct decision (expects the highest-sugar option):
python3 scripts/jev-decisions-adapter.py --state "You may move to one cell." \
  --instructions "pick the option with the most sugar" \
  --option "0:sugar 5" --option "1:sugar 2" --option "2:sugar 9" --json
# -> {"index": "2", "confidence": ~0.94, "probabilities": {...}, "cost": ~1e-05}

# Shim health:
curl -s http://127.0.0.1:8792/healthz
```

## Related

- `references/jev-openrouter-goose-access.handoff.md` — inbound handoff, API contract.
- `references/goose-backend-switcher.md` — the OpenRouter/Ollama switcher this mirrors.
- `scripts/goose-openrouter-route-proxy.py` — the transport-layer proxy the shim is modeled on.
- Downstream research program: `agenticExperimentation/` (Jev as a selection oracle for a lightweight generator).
