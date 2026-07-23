# Agent Team (Goose bridge)

<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-entry v=1 -->
Use source framework `copilot-vscode` as canonical agent infrastructure.
Read `references/bridges/copilot-vscode-to-goose/agent-inventory.md` and `references/bridges/copilot-vscode-to-goose/quickstart-snippet.md`.
Start with orchestrator routing.

These two apply to every request in this session, not just project-coordination
work routed through the orchestrator above:
- Before claiming you lack real-time or internet access, try a read-only fetch
  first (`web_scrape` if the `computercontroller` extension is active, otherwise
  a plain `curl`/`wget` via the shell) — don't default to refusal without
  attempting it.
- When a name in the request doesn't exactly match a known entity, resolve to the
  single closest well-known match and proceed confidently — but only when one
  candidate is clearly the best fit (an obvious misspelling or variant). If
  multiple entities are genuinely comparably plausible, say so and ask instead of
  forcing a guess between real alternatives.
<!-- AGENTTEAMS-BRIDGE:END goose-bridge-entry -->
