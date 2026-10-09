# Part VIII — Synthesis & reference  (SB22–SB23)

<!-- skeleton:SB22 SB23 -->

The subsystem, as a reviewer should model it: **request → decide (capability + honest advisory) → emit
one of three mechanisms → operator wires/wraps → OS + hook enforce**, with the emitters tamper-tracked
and every claim bounded. No single stage is the boundary; confinement is the composition, and it engages
*as tested* only when opted-in and wired. An optional layer on top (SB24, `write_policy:
"orchestrator-only"`) removes write authority from every agent but the orchestrator and moves it to an
out-of-session runner — which, with the operator's host, becomes that layer's TCB.

```mermaid
flowchart TD
    REQ[request: profile/token] --> DEC["decide: is_sandbox_capable + advisory"]
    DEC -->|"fatal + default"| FCX[FAIL CLOSED]
    DEC --> EMIT["emit: settings / Seatbelt / launcher"]
    EMIT --> WIRE["operator WIRES/WRAPS (inert until then)"]
    WIRE --> ENF["OS + hook (fail-open coop / fail-closed confined)"]
    INT["integrity pins emitters + launcher"] -.-> EMIT
    CEIL["ceilings: opt-in · inert-until-wired · Linux-verified-only · closes-nothing-absolutely"] -.-> ENF
    WP["optional write_policy (SB24): agents propose · runner holds key"] -.->|"relies on wired sandbox"| ENF
```

The four ceilings are the reviewer's takeaways; the full tables are in
[Reference Part VIII](../reference/part-viii-synthesis-and-reference.md).
