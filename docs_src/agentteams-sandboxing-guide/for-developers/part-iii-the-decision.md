# Part III — The decision  (SB7–SB9)

<!-- skeleton:SB7 SB8 SB9 -->

**Will agentteams emit a boundary for my target?** Run `is_sandbox_capable(framework, platform)`:

| Platform | claude | goose | codex / copilot / agents-md |
|---|---|---|---|
| Linux | ✅ native + launcher | ✅ launcher | ✅ launcher |
| macOS | ✅ native | ✅ Seatbelt | ❌ **fatal** |
| Windows | ✅ native (unverified) | ❌ **fatal** | ❌ **fatal** |

**Two advisories you may hit:**

- **FATAL `unenforced-host`** — no boundary emittable (Windows; non-claude/non-goose off Linux, incl.
  macOS). On `generate` this **refuses** (`PrivilegeConfinementError`) unless you pass
  `--allow-unenforced-confinement` (which proceeds with a warning and **emits no boundary**).
- **NON-FATAL `linux-launcher-manual-wire`** — Linux, any framework except claude. Enforcement *is*
  available; the warning reminds you the launcher must be **wrapped**. It never refuses.

```mermaid
flowchart TD
    Q["confinement requested?"] -->|yes| P{platform?}
    P -->|linux| FW{claude?}
    FW -->|yes| CN["native + launcher; no advisory<br/>(native arm UNVERIFIED on Linux)"]
    FW -->|no| ML["manual-wire (NON-FATAL) + launcher"]
    P -->|darwin| DF{framework?}
    DF -->|claude| CN2["native; no advisory"]
    DF -->|goose| SBn["Seatbelt; no advisory"]
    DF -->|other| UH["unenforced-host (FATAL)"]
    P -->|windows/other| WF{claude?}
    WF -->|yes| CW["claude native (product-arm<br/>unverified); no advisory"]
    WF -->|no| UH
```

*Full detail:* [Reference Part III](../reference/part-iii-the-decision.md).
