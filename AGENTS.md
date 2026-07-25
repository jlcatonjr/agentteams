# Agent Team (Goose bridge)

<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-entry v=1 -->
Use source framework `copilot-vscode` as canonical agent infrastructure.
Read `references/bridges/copilot-vscode-to-goose/agent-inventory.md` and `references/bridges/copilot-vscode-to-goose/quickstart-snippet.md`.
Start with orchestrator routing.

These apply to every request in this session, not just project-coordination
work routed through the orchestrator above:
- **Search before you fetch.** No builtin Goose extension does web *search*
  (query in, ranked results out) — `web_scrape` needs a URL you already know, so
  guessing one lands you on a homepage and floods context with navigation HTML.
  This project may ship `agentteams.research`, which does search, text-extracted
  fetch, and (with the `[browser]` extra) JS rendering, through the ordinary
  shell — no MCP wiring. Verify first, the same discipline as any CLI tool:
  `python -m agentteams.research --help` (install with
  `pip install agentteams[research]` if absent), then e.g.
  `python -m agentteams.research search "<query>"` and
  `python -m agentteams.research fetch "<url>"`.
- Before claiming you lack real-time or internet access, try a read-only fetch
  first (the above if available, else `web_scrape` if the `computercontroller`
  extension is active, otherwise a plain `curl`/`wget` via the shell) —
  don't default to refusal without attempting it. Prefer extracted text over
  raw HTML: a scraped homepage is mostly navigation chrome and can consume more
  than half your context while containing none of the answer.
- For "most recent / latest" questions, relevance ranking is not recency —
  confirm the date of what you found rather than trusting result order, and say
  which date you are reporting.
- When a name in the request doesn't exactly match a known entity, resolve to the
  single closest well-known match and proceed confidently — but only when one
  candidate is clearly the best fit (an obvious misspelling or variant). If
  multiple entities are genuinely comparably plausible, say so and ask instead of
  forcing a guess between real alternatives.
<!-- AGENTTEAMS-BRIDGE:END goose-bridge-entry -->
