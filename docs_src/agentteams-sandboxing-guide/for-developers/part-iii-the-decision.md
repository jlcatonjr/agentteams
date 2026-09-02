# Part III — The decision  (SB7–SB9)

<!-- skeleton:SB7 SB8 SB9 -->

**Will agentteams emit a boundary for my target?** Run `is_sandbox_capable(framework, platform)`:

| Platform | claude | goose | codex / copilot / agents-md |
|---|---|---|---|
| Linux | ✅ native + launcher `bwrap` | ✅ launcher `bwrap` | ✅ launcher `bwrap` |
| macOS | ✅ native Seatbelt | ✅ native Seatbelt | ✅ launcher `build_macos` (**UNVERIFIED**) |
| Windows | ✅ native (unverified) | ❌ **fatal** | ❌ **fatal** |

*(Since 2026-W36 macOS is framework-neutral too — the SAME launcher has a `build_macos`/`sandbox-exec`
branch, so codex/copilot/agents-md on a mac are no longer fatal.)*

**Three advisories you may hit:**

- **FATAL `unenforced-host`** — no boundary emittable (**Windows**; non-claude on any non-POSIX target).
  On `generate` this **refuses** (`PrivilegeConfinementError`) unless you pass
  `--allow-unenforced-confinement` (proceeds with a warning, **emits no boundary**).
- **NON-FATAL `linux-launcher-manual-wire`** — Linux, any framework except claude. Enforcement *is*
  available; the warning reminds you the launcher must be **wrapped**. Never refuses.
- **NON-FATAL `macos-launcher-manual-wire`** — macOS, any framework except claude **and** goose (both
  have native macOS boundaries). Same "you must wrap it" reminder, plus the honest macOS residuals (mem
  uncapped, no syscall filtering, setuid denylist ≠ NoNewPrivs, loopback-only proxy) and the
  UNVERIFIED-until-`mac-escape-tests` gate. Never refuses.

```mermaid
flowchart TD
    Q["confinement requested?"] -->|yes| P{platform?}
    P -->|linux| FW{claude?}
    FW -->|yes| CN["native + launcher; no advisory<br/>(native arm UNVERIFIED on Linux)"]
    FW -->|no| ML["manual-wire (NON-FATAL) + launcher"]
    P -->|darwin| DF{framework?}
    DF -->|claude| CN2["native; no advisory"]
    DF -->|goose| SBn["native Seatbelt; no advisory"]
    DF -->|other| MM["macos manual-wire (NON-FATAL) + launcher build_macos<br/>(UNVERIFIED until mac-escape-tests)"]
    P -->|windows/other| WF{claude?}
    WF -->|yes| CW["claude native (product-arm<br/>unverified); no advisory"]
    WF -->|no| UH["unenforced-host (FATAL, SB8)"]
```

*Full detail:* [Reference Part III](../reference/part-iii-the-decision.md).
