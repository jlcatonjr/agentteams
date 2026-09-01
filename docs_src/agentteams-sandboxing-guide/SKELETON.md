# SKELETON — the agentteams Sandboxing map (single source of structure + facts)

> This is the **core outline** for the sandboxing subsystem: the shared spine every edition
> (R/D/S/E) projects. It is a focused deep-dive on ONE layer of the broader
> [Security Guide](../agentteams-security-guide/README.md) (its Part VI) — how *workspace
> write-confinement, read-exclusion, egress control, and the runtime deny-hook* are requested,
> decided, emitted, wired, enforced, tamper-tracked, and honestly bounded.
>
> It fixes two things the editions may **not** diverge on — the **section structure** (stable IDs
> `SB1`…) and the **canonical facts** each section asserts. How deep and in what voice each edition
> renders a section is set by `audience-profiles.md`; *what is true* is set here.
>
> **Editing rule:** change the skeleton **first**, then project into every edition. Never add a fact,
> drop a section, or reorder the spine in a book alone.

## How to read this map

- **ID** — stable (`SB3`, `SB18`). An edition marks each section it renders with the ID so the map
  and the books cross-check.
- **Canonical facts** — the invariant claims. Every edition that includes the section states these
  (adapted in depth/voice) and states nothing that contradicts them.
- **Source** — the repo file(s) the facts rest on, with line ranges (collected in `SOURCES.md`). No
  fact ships without one.
- **Status marker** — ✅ *implemented & enforced in code/tests* · ⚙ *design / procedural / operator
  action only, not a deterministic code control*. Compound (`✅/⚙`) marks a concept partly each.
  Overclaiming a ⚙ as ✅ is a fact error.
- **Dial** — per-edition depth `R/D/S/E` (Full/Core/Light/Skip; see `audience-profiles.md`).
- **The honest-ceiling doctrine (binding on every edition).** Every control is stated with what it
  *buys* and what it *cannot*. A boundary is described as "engages as tested," never "secure" or
  "unbypassable." The four load-bearing ceilings of THIS subsystem, which no edition may drop:
  1. **Opt-in.** The default profile is `cooperative`: the sandbox is **off** and the deny-hook is
     **fail-open**. Confinement engages only under `confined`/`exclusive`.
  2. **Inert until wired.** Every emitted boundary is an *example/launcher the operator must
     activate* (merge settings, set `GOOSE_SANDBOX`, or WRAP the process). agentteams never writes an
     operator's live config or auto-invokes the launcher. An emitted-but-unwired boundary confines
     nothing.
  3. **Verified only on Linux.** The Linux bwrap launcher's enforcement is proven by a live-kernel
     deny test; the macOS Seatbelt path is **enforcement-UNVERIFIED** off a mac; Windows has no
     emittable boundary.
  4. **Closes nothing absolutely.** T6 (a same-host key-holder / operator shell) and host-as-TCB stay
     bounded, never closed; seccomp/Landlock is a further layer **not yet added**.

---

## The pipeline at a glance (canonical graph G1)

Every sandbox request flows through five stages. The rest of this map details each.

```mermaid
flowchart LR
    R["REQUEST<br/>privilege_profile /<br/>*:sandbox token<br/>(SB4–SB6)"]
    D{"DECIDE<br/>is_sandbox_capable?<br/>(SB7–SB9)"}
    E["EMIT<br/>the boundary artifact<br/>(SB10–SB13)"]
    W["WIRE<br/>operator activates<br/>(SB14–SB15)"]
    EN["ENFORCE<br/>OS + PreToolUse hook<br/>(SB16–SB17)"]
    A1["advisory:<br/>unenforced-host<br/>(FATAL, SB8)"]
    A2["advisory:<br/>manual-wire<br/>(NON-FATAL, SB8)"]

    R --> D
    D -- "capable + not linux-launcher-only<br/>(claude native / goose macOS)" --> E
    D -- "linux, non-claude<br/>(launcher is the boundary)" --> A2 --> E
    D -- "not capable<br/>(Windows / other)" --> A1
    A1 -- "--allow-unenforced-confinement" --> WN["proceed with WARNING<br/>NO boundary emitted here"]
    A1 -- "default (generate)" --> X["FAIL CLOSED<br/>refuse, emit nothing"]
    E --> W --> EN
    EN -. "tamper-tracked by" .-> I["integrity manifest<br/>(SB18–SB19)"]
```

> **Reading G1's honest ceiling:** reaching **EMIT** means a boundary *artifact* exists — not that a
> boundary is *in force*. **WIRE** (operator action) and, for the Linux launcher, actually wrapping the
> process, are what make it enforce (SB14). The dotted edge to the integrity manifest is not part of the
> per-team runtime path; it is how agentteams' OWN source protects the emitters from silent edits (SB18).

---

## Part I — What sandboxing is and why

### SB1 — What "sandboxing" means here  ✅/⚙
**Canonical facts.**
1. In agentteams, *sandboxing* is the **runtime OS-confinement layer**: workspace **write-confinement**
   (the agent may write only inside declared roots), optional **read-exclusion** (deny reads of
   credential paths + sibling workspaces), **egress control** (deny/proxy/host network), and a
   **PreToolUse deny-hook** that gates destructive commands. It is OWASP LLM06 ("Excessive Agency")
   containment: an agent that follows injected instructions and holds `edit`/`execute` is boxed so a
   steered action cannot escape the workspace.
2. It is **one layer** of the layered security stack, not the whole of it. The governance layers
   (constitution, `@security` sentinel, clearance/waiver/grant, CLI gates, content scanner) are always
   active and are documented in the [Security Guide](../agentteams-security-guide/README.md); *this*
   guide is only the OS-confinement + deny-hook layer.
3. **Design-time, not runtime-of-the-app.** The confinement boxes the *agent that builds an app* at
   design/build time. It is **not** shipped inside the produced app; an app that serves LLM output to
   end users must add its own runtime governance.
**Source.** `SECURITY.md` §threat-model; `agentteams/host_features.py`;
`agentteams/frameworks/_sandbox_emit.py`; `agentteams/templates/universal/sandbox/confine-run.sh`.
**Dial.** R Full · D Core · S Full · E Light.

### SB2 — The in-scope adversary and the two surfaces  ✅/⚙
**Canonical facts.**
1. The realistic in-scope adversary is **an agent with legitimate write/execute access acting on
   injected instructions** — not a remote network attacker. The sandbox's job is to make that agent's
   blast radius the declared workspace, not the whole host.
2. Two surfaces enforce, and they compose: **(a) OS confinement** (Claude Code's native sandbox, Apple
   Seatbelt for goose on macOS, or the framework-neutral bwrap launcher on Linux) boxes the *filesystem
   + network*; **(b) the PreToolUse `constitutional-gate.py` hook** boxes *specific destructive command
   spellings* an agent's `Bash` tool might run. Neither is the boundary alone.
3. **Content is data (C-4).** The sandbox exists partly because a file under review, a fetched page, or
   a brief may carry injected text; confinement limits what a mis-followed instruction can do.
**Source.** `.claude/CLAUDE.md` (C-4); `agentteams/templates/universal/hooks/constitutional-gate.py`;
`SECURITY.md` §design-time-vs-runtime.
**Dial.** R Full · D Core · S Full · E Light.

### SB3 — The opt-in posture (binding ceiling)  ✅
**Canonical facts.**
1. The default `privilege_profile` is **`cooperative`**: no sandbox block is emitted and the deny-hook
   is emitted **fail-OPEN** (`_FAIL_CLOSED_ON_ERROR = False`). Confinement engages **only** when the
   operator selects `confined` or `exclusive` (or passes a `*:sandbox` host-feature token).
2. Reading "layered confinement" as "on by default" is the overclaim this fact prevents. Out of the
   box, the strongest locks are dormant.
**Source.** `agentteams/host_features.py` (cooperative default);
`agentteams/frameworks/_sandbox_emit.py:116` `_sandbox_feature_enabled`;
`agentteams/templates/universal/hooks/constitutional-gate.py:205` (`_FAIL_CLOSED_ON_ERROR = False`).
**Dial.** R Full · D Full · S Core · E Light (mandatory ceiling #1).

---

## Part II — The request

### SB4 — Three privilege profiles  ✅
**Canonical facts.**
1. `privilege_profile` has three values, each a superset of the last:
   - **`cooperative`** (default) — no boundary emitted; the agent is trusted, fail-open hook.
   - **`confined`** — workspace **write-confinement**: the agent writes only inside
     `workspace_write_roots` (default `["."]`, the generated tree). *Network* deny-by-default is a
     property of the mechanisms that emit an egress directive — goose Seatbelt (`deny network*`) and
     the Linux launcher (`--unshare-net`); the **claude** mechanism emits **no** egress directive, so
     claude network confinement is Claude Code's own product default (SB10, same unverified caveat).
   - **`exclusive`** — `confined` **plus read-exclusion** (P3a) for the **claude** (`denyRead`) and
     **goose** (`deny file-read*`) mechanisms: OS-deny reads of a curated credential set +
     operator-supplied `protected_read_paths` (sibling workspaces), plus an operator inbound-hardening
     advisory (P3b). **Nuance for the Linux launcher (SB12):** it masks the default *credential* set
     (`~/.ssh …`) on **every** wrap regardless of profile; only the extra sibling-workspace
     `--exclude` reads are `exclusive`-specific there.
2. An **unknown** profile value **fails closed** (hard error at parse), never silently downgrades to
   `cooperative` — a typo cannot ship an unconfined team that looks confined.
**Source.** `agentteams/host_features.py` `validate_privilege_profile`;
`agentteams/frameworks/_sandbox_emit.py` `_exclusive_read_deny_paths`;
`schemas/project-description.schema.json` (`privilege_profile`, `workspace_write_roots`,
`protected_read_paths`).
**Dial.** R Full · D Full · S Core · E Light.

### SB5 — Host-feature tokens and profile expansion  ✅
**Canonical facts.**
1. The request is recorded two ways, both read: the `privilege_profile` field AND a `*:sandbox`
   host-feature token (`claude:sandbox`, `goose:sandbox`). A confined/exclusive profile **expands** to
   the framework-appropriate token (`expand_privilege_profile`): goose → `goose:sandbox`; every other
   framework → `claude:sandbox`.
2. `_sandbox_feature_enabled` / the framework-neutral `_sandbox_confinement_requested` treat **either**
   source as "confinement requested" so a confined manifest emits on the `convert`/`render` paths too,
   not only interactive `generate`.
3. A `:sandbox` token is **rejected** for a namespace with no emitter (bridge namespaces): a validating
   token that confined nothing is the silent-false-confinement failure this rejection prevents.
**Source.** `agentteams/host_features.py` `expand_privilege_profile`, `merge_profile_features`,
`validate`; `agentteams/frameworks/_linux_sandbox_emit.py` `_sandbox_confinement_requested`.
**Dial.** R Full · D Core · S Core · E Skip.

### SB6 — The manifest is the request record  ✅
**Canonical facts.**
1. `build_manifest` carries `privilege_profile`, `workspace_write_roots`, `protected_read_paths`, and
   the resolved `host_features` into the render/emit pipeline. The manifest is the single object the
   decision (SB7) and every emitter (SB10–SB13) read; there is no out-of-band sandbox state.
**Source.** `agentteams/analyze.py` `build_manifest`; `schemas/team-manifest.schema.json`.
**Dial.** R Full · D Core · S Light · E Skip.

---

## Part III — The decision

### SB7 — `is_sandbox_capable`: the capability matrix  ✅
**Canonical facts.**
1. `is_sandbox_capable(framework_id, platform)` is the single platform-aware decision function. Its
   matrix:
   - **Linux** → `True` for **any** framework (the emitted bwrap launcher wraps any process —
     framework-neutral).
   - **`claude`** → `True` **everywhere** (Claude Code configures its own Seatbelt/bubblewrap sandbox).
   - **`goose`** → `True` on **macOS** only (Apple Seatbelt).
   - anything else off Linux → `False` (no emittable boundary).
2. `SANDBOX_CAPABLE_FRAMEWORKS` is a convenience set sampling only `("claude","goose")` for the current
   host; the authoritative, framework-complete answer is `is_sandbox_capable` itself.
**Source.** `agentteams/host_features.py:222` `is_sandbox_capable`.
**Dial.** R Full · D Full · S Full · E Light.

```mermaid
flowchart TD
    Q["confinement requested?<br/>(profile confined/exclusive OR *:sandbox)"] -->|no| COOP["cooperative:<br/>no boundary (SB3)"]
    Q -->|yes| P{"platform?"}
    P -->|linux| FW{"framework == claude?"}
    FW -->|yes| CNL["claude: native settings-block sandbox (SB10),<br/>No advisory. ALSO emits the launcher (SB12/SB13),<br/>but native is claude's intended boundary — and on<br/>Linux that native arm is UNVERIFIED (SB20)"]
    FW -->|no| ML["manual-wire advisory (NON-FATAL)<br/>+ emit bwrap launcher (SB12)"]
    P -->|darwin| DF{"framework?"}
    DF -->|claude| CN["claude: native settings-block<br/>sandbox (SB10). No advisory."]
    DF -->|goose| SB["goose: Seatbelt profile +<br/>inert config example (SB11). No advisory."]
    DF -->|"other (codex/copilot/agents-md)"| UH["unenforced-host advisory (FATAL, SB8)"]
    P -->|win32/other| W2{"framework == claude?"}
    W2 -->|yes| CW["claude: native (product-arm<br/>unverified on Windows)"]
    W2 -->|no| UH
```

### SB8 — Two advisories: fatal vs non-fatal  ✅
**Canonical facts.**
1. `privilege_profile_advisory` returns one of two codes, or `None`:
   - **`privilege-profile-unenforced-host`** (**FATAL**) — no boundary is emittable here: **Windows**,
     and any **non-claude/non-goose** framework off Linux (**including on macOS** — e.g. a confined
     codex/copilot/agents-md team on a mac). `resolve_host_features_and_advise` **raises**
     `PrivilegeConfinementError` (fail-closed) unless `--allow-unenforced-confinement`, which downgrades
     it to a persisted warning **and emits no boundary on that host**.
   - **`privilege-profile-linux-launcher-manual-wire`** (**NON-FATAL**) — on **Linux, for every
     framework except claude**: enforcement IS available (the emitted launcher) but is **not
     auto-applied**; the operator must WRAP the invocation. This **never** fail-closes (enforcement is
     genuinely available); it always warns + persists.
   - **`None`** — a boundary that IS wired through the framework's own config (claude native everywhere;
     goose Seatbelt on macOS) surfaces no extra advisory. **Claude-on-Linux caveat:** claude is excluded
     from the manual-wire advisory because its *native settings-block sandbox* is its intended boundary
     — but on **Linux** that native arm is enforcement-**UNVERIFIED** (SB20), while the *verified* neutral
     launcher, which is also emitted for claude on Linux (SB13), is left un-advised. So "no advisory for
     claude" means "its intended boundary is the native one," not "claude-on-Linux is fully covered."
2. `resolve_host_features_and_advise` fail-closes on the fatal code **only** (`fatal = code ==
   "privilege-profile-unenforced-host"`). Fail-closing on the manual-wire code would wrongly refuse an
   enforceable Linux target.
3. **Why manual-wire exists (a closed gap).** Making Linux capable for every framework suppressed the
   old unenforced-host advisory for codex/copilot/agents-md, whose launcher is inert until wrapped. The
   non-fatal advisory restores the honest signal so an operator who requested `confined`, saw no error,
   and found `sandbox/confine-run.sh` is told nothing is confined until they wrap the process.
**Source.** `agentteams/host_features.py:277` `privilege_profile_advisory`, `:324`
(unenforced-host), `:352` (manual-wire); `agentteams/cli/artifacts.py:330`
`resolve_host_features_and_advise`, `:381` (fatal gate).
**Dial.** R Full · D Full · S Full · E Core (the "you must wire it / off systems it is advice not a lock").

### SB9 — Fail-closed by default on generate  ✅
**Canonical facts.**
1. On the interactive `generate` path a fatal (unenforceable) confinement request **refuses to emit an
   inert boundary** rather than ship a config that looks protective — the fail-safe-defaults principle.
   `--allow-unenforced-confinement` opts into proceeding with the advisory instead.
2. The `--convert-from`/`--fleet`/render paths keep an advisory-not-raise default (they emit via
   `_sandbox_feature_enabled` reading `privilege_profile` directly), so an unenforceable target there
   degrades to a warning rather than a non-zero exit.
**Source.** `agentteams/cli/artifacts.py:381`; `agentteams/frameworks/_sandbox_emit.py:116`.
**Dial.** R Full · D Full · S Core · E Light.

---

## Part IV — The mechanisms

### SB10 — Mechanism A: Claude native settings-block sandbox  ✅/⚙
**Canonical facts.**
1. For the `claude` framework, `_sandbox_emit._build_sandbox_block` emits a `sandbox` block into
   `.claude/settings.hooks.example.json`: `allowWrite` (the write roots), `denyWrite` (the control-plane
   files, denied even inside a write root), the `allowUnsandboxedCommands: false` escape-hatch closure,
   and — under `exclusive` — `denyRead` plus `allowRead` (which re-opens the write roots so granted paths
   stay readable). Claude Code applies it natively once the operator merges the example into their live
   `settings.json`. The block emits **no** network/egress directive — claude network confinement is
   Claude Code's own product default (unverified).
2. The block is **inert until merged** — agentteams ships an example, never writes the operator's
   `settings.json`. `verify_sandbox_wiring` (P1-3) is the read-only, output-only check that the block
   was actually merged (it reports booleans, never echoes live-settings secrets).
3. **Honest ceiling.** Claude Code's *mechanism* is verified; its Linux *product arm* on stock Ubuntu is
   **not** (nested-userns restrictions) — a separate mechanism from the bwrap launcher of SB12.
**Source.** `agentteams/frameworks/_sandbox_emit.py:176` `_build_sandbox_block`;
`agentteams/frameworks/claude.py:249` (gate), `:320` `verify_sandbox_wiring`.
**Dial.** R Full · D Full · S Core · E Light.

### SB11 — Mechanism B: goose macOS Seatbelt profile  ✅/⚙
**Canonical facts.**
1. For `goose` on **macOS**, `goose_sandbox_output_files` emits a `sandbox.sb` Seatbelt profile
   (`sandbox-exec`) that `deny file-write*` outside the write roots, `deny network*` by default (an
   egress-proxy allow for ONE endpoint is behind an explicit flag), and — for `exclusive` — `deny
   file-read*` of the protected set. It ships an **inert** `config.yaml.agentteams.example` carrying
   `GOOSE_SANDBOX`; the operator merges it.
2. An **explicit `sys.platform == "darwin"` guard** ensures this path emits ONLY on macOS. Off macOS it
   returns `[]` — but that is NOT "no boundary": on Linux the neutral launcher (SB12) is the boundary;
   only Windows has none.
3. **Honest ceiling.** The Seatbelt path is **enforcement- and profile-syntax-UNVERIFIED** off a mac
   host — a green emission test means "the profile is shaped right," never "it denies on a real mac."
**Source.** `agentteams/frameworks/_goose_sandbox_emit.py:333` `goose_sandbox_output_files`, `:350`
(darwin guard); `_seatbelt_path_expr`.
**Dial.** R Full · D Core · S Core · E Light (mandatory ceiling #3 — macOS UNVERIFIED).

### SB12 — Mechanism C: the framework-neutral Linux bwrap launcher  ✅/⚙
**Canonical facts.**
1. `_linux_sandbox_emit.linux_sandbox_output_files` emits a provider-agnostic **`bwrap` launcher** to
   the generated project's repo-root **`sandbox/confine-run.sh`** — deliberately NOT under `.goose/` and
   NOT framework-gated (it wraps any process). It is emitted for a confined/exclusive team of **any**
   framework, platform-guarded to Linux.
2. The launcher confines **once the operator WRAPs the invocation with it**
   (`sandbox/confine-run.sh --scratch DIR --egress deny -- <agent cmd>`); the emit step only *writes the
   file* — an emitted-but-unwrapped launcher confines nothing (this is why SB12 is ✅/⚙: ✅ as emitted, ⚙
   for the operator-action that activates it; the honest ceiling is SB14). When wrapped it enforces:
   `--ro-bind / /` (read-only root), `--bind $SCRATCH` (the only writable path), `--unshare-net` on
   `--egress deny` (network isolation), and `--tmpfs` masks over the credential set
   (`~/.ssh ~/.aws ~/.gnupg ~/.kube ~/.config/gcloud ~/.azure`, applied on **every** wrap regardless of
   profile) plus any `--exclude` sibling paths (exclusive). **NoNewPrivs** is bubblewrap's *default*
   behavior here, **not an explicit emitted flag** — so, unlike the textual flags, it is **not** covered
   by the SB18 content pin (there is no line to diff). `--egress proxy` runs inside a pre-created netns;
   `other OS → FAIL CLOSED`.
3. The launcher content is **emitted verbatim** (byte-for-byte) from a shipped template asset
   (`templates/universal/sandbox/confine-run.sh`) — it is generic, taking `--scratch`/`--exclude`/
   `--egress` at RUN time, so no manifest values are baked in. The emit path lands it repo-root-relative
   at the correct `../` depth per framework (`sandbox_launcher_rel_path`) and marks it executable.
**Source.** `agentteams/frameworks/_linux_sandbox_emit.py:52,94` `linux_sandbox_output_files`;
`agentteams/frameworks/base.py:125` `sandbox_launcher_rel_path`, `:139` `extra_output_files`;
`agentteams/templates/universal/sandbox/confine-run.sh`; `agentteams/atomicio.py` (shebang → +x).
**Dial.** R Full · D Full · S Full · E Light.

```mermaid
flowchart TD
    REQ["confined / exclusive team"] --> M{"which mechanism<br/>(by SB7 decision)"}
    M -->|"claude (any OS)"| A["A: settings-block sandbox"]
    M -->|"goose (macOS)"| B["B: Seatbelt sandbox.sb"]
    M -->|"any framework (Linux) —<br/>STACKS with A on claude"| C["C: bwrap launcher<br/>sandbox/confine-run.sh"]
    A --> WD["WRITE-confine: ALL three<br/>(allowWrite / ro-bind)"]
    B --> WD
    C --> WD
    A --> NA["NETWORK: A emits NO egress directive —<br/>it is Claude Code's product default (unverified, SB10)"]
    B --> NBC["NETWORK: B & C deny by default<br/>(deny network* / --unshare-net)"]
    C --> NBC
    A --> RAB["READ-exclude: A & B only under EXCLUSIVE<br/>(denyRead / deny file-read*)"]
    B --> RAB
    C --> RC["READ-exclude: C masks the credential set ALWAYS<br/>(~/.ssh … tmpfs, any profile); --exclude adds<br/>siblings only under exclusive"]
    WD --> INERT["INERT until the operator activates it:<br/>merge settings / set GOOSE_SANDBOX / WRAP the process (SB14)"]
    NA --> INERT
    NBC --> INERT
    RAB --> INERT
    RC --> INERT
```

### SB13 — Framework-neutral wiring, no harness preference  ✅
**Canonical facts.**
1. The Linux launcher is emitted from `base.extra_output_files` so **every** framework adapter emits it
   (claude, codex, copilot-vscode/-cli, agents-md, goose) — no harness is preferred. Only `claude.py`
   and `goose.py` override `extra_output_files`, and both call `super()`; there is no double-emit.
2. The neutral path resolves to repo-root `sandbox/confine-run.sh` via `sandbox_launcher_rel_path()`:
   `../../` for 2-deep agents dirs (claude/copilot/goose), `../` for 1-deep (codex/agents-md).
**Source.** `agentteams/frameworks/base.py:139`; `agentteams/frameworks/codex.py`,
`agentteams/frameworks/agents_md.py` (rel-path overrides); `agentteams/frameworks/claude.py`,
`agentteams/frameworks/goose.py` (`super()`).
**Dial.** R Full · D Core · S Light · E Skip.

---

## Part V — Wiring & runtime enforcement

### SB14 — Inert until wired (binding ceiling)  ⚙
**Canonical facts.**
1. Every emitted boundary is **inert until the operator activates it** — the standing "ship an example,
   never clobber the operator's live config" convention. Activation differs by mechanism: **merge** the
   settings block (claude); **set `GOOSE_SANDBOX`** (goose macOS); **WRAP** the invocation
   (`sandbox/confine-run.sh --scratch DIR --egress deny -- <agent cmd>`, Linux).
2. For the Linux launcher there is no framework config that references it — the operator must change how
   they LAUNCH the agent. This is why SB8's non-fatal manual-wire advisory exists: to say so at
   generation time.
**Source.** `agentteams/frameworks/hooks_emit.py` (example-not-settings convention);
`agentteams/templates/universal/sandbox/confine-run.sh` (usage header);
`agentteams/host_features.py:352` (manual-wire).
**Dial.** R Full · D Full · S Core · E Core (mandatory ceiling #2).

### SB15 — Verifying the wiring took effect  ✅
**Canonical facts.**
1. Read-only, output-only verifiers close the "looks confined, enforces nothing" gap without echoing
   live-config secrets: `claude.verify_sandbox_wiring` (settings merged?) and
   `_goose_sandbox_emit.verify_goose_sandbox_wiring` — the latter is **platform-honest**: on **Linux**
   it verifies `sandbox/confine-run.sh` is present and returns `ENFORCEABLE` (noting it must still be
   wrapped); on **Windows** it is exit-neutral "NOT ENFORCEABLE HERE"; on **macOS** it checks the
   Seatbelt `.goose/sandbox.sb`.
**Source.** `agentteams/frameworks/claude.py:320` `verify_sandbox_wiring`;
`agentteams/frameworks/_goose_sandbox_emit.py:392` `verify_goose_sandbox_wiring`.
**Dial.** R Full · D Full · S Light · E Skip.

### SB16 — The PreToolUse constitutional-gate hook  ✅/⚙
**Canonical facts.**
1. A second surface — the emitted `constitutional-gate.py` PreToolUse hook — routes destructive **Bash**
   command spellings (repo/ref/worktree/filesystem/infrastructure/database deletion; the
   "delete-authorization gate") to the operator for authorization BEFORE they run (C-5).
2. **It is a best-effort, cooperative speed-bump, not a boundary.** It does NOT gate `Write`/`Edit`
   content deletion, MCP/non-Bash deletes, interpreter-mediated deletion it does not pattern-match,
   alias/quote/variable obfuscation, or harnesses that do not honor PreToolUse (or auto-approve under
   headless). A green delete-gate test means "these spellings are gated," never "deletion is prevented."
**Source.** `agentteams/templates/universal/hooks/constitutional-gate.py:63` (delete gate);
`agentteams/templates/universal/security.template.md` (scope + limits).
**Dial.** R Full · D Full · S Core · E Light.

### SB17 — Fail-open default, fail-closed under confinement  ✅
**Canonical facts.**
1. The hook defaults **fail-OPEN** (`_FAIL_CLOSED_ON_ERROR = False`): a gate crash is a harness *allow*,
   so a buggy gate never bricks a cooperative session. Under `confined`/`exclusive`, emission flips the
   sentinel to `_FAIL_CLOSED_ON_ERROR = True` (unless `--allow-fallback-fail-open`), so a crash emits a
   `deny` rather than a silent allow — the operator opted into a boundary a crash must not drop.
**Source.** `agentteams/templates/universal/hooks/constitutional-gate.py:205`
(`_FAIL_CLOSED_ON_ERROR = False`); `agentteams/frameworks/claude.py` `_apply_fail_closed_policy`.
**Dial.** R Full · D Full · S Core · E Light.

```mermaid
flowchart TD
    CMD["agent Bash command"] --> H["constitutional-gate.py<br/>PreToolUse hook"]
    H --> MATCH{"matches a delete<br/>idiom / S-9 pattern?"}
    MATCH -->|no| ALLOW["allow"]
    MATCH -->|yes| ASK["ask — route to operator (C-5)"]
    H -.->|"hook itself crashes"| FC{"_FAIL_CLOSED_ON_ERROR?"}
    FC -->|"False (cooperative default)"| ALLOW2["fail-OPEN → allow<br/>(never brick a trusted session)"]
    FC -->|"True (confined/exclusive)"| DENY["fail-CLOSED → deny<br/>(operator opted into a boundary)"]
```

---

## Part VI — Integrity, provenance & drift

### SB18 — The boundary content is tamper-tracked  ✅
**Canonical facts.**
1. `enforcement-integrity.json` pins a sha256 of every enforcement module. For sandboxing it pins **both**
   the emitters (`_sandbox_emit.py`, `_linux_sandbox_emit.py`) **and** the launcher **asset**
   (`templates/universal/sandbox/confine-run.sh`) — because the bwrap flags that ARE the boundary live in
   the `.sh`, so pinning the `.py` alone would leave the boundary content untracked. A silent edit
   dropping `--unshare-net` trips `--verify-integrity` (red-team probe E4) instead of passing unnoticed.
2. The manifest is regenerated deliberately (`--write-integrity-manifest`) only after an INTENDED control
   change; the diff IS the control. `integrity.py` pins itself, so removing an entry is detectable.
**Source.** `agentteams/integrity.py:65` (`_sandbox_emit.py`), `:71` (`_linux_sandbox_emit.py`), `:77`
(`confine-run.sh`); `references/enforcement-integrity.json`.
**Dial.** R Full · D Full · S Core · E Light (mandatory ceiling: "tamper-evident, not tamper-proof").

### SB19 — Cross-repo single-source-of-truth & drift protocol  ✅/⚙
**Canonical facts.**
1. The launcher is emitted **verbatim** so a consuming project (e.g. baseAgent) keeps a byte-identical
   copy guarded by a **sha256 pin**; agentteams is the single source of truth. A byte change is a
   coordinated event: agentteams pings the consumer, both re-pin, and the consumer re-runs its live-kernel
   deny test. The launcher header is consumer-**neutral** (no consumer-specific paths or netns baked in);
   its `--netns` default is a neutral token a consumer overrides *explicitly at invocation* rather than
   by editing the file.
**Source.** `agentteams/templates/universal/sandbox/confine-run.sh` (provenance header);
`agentteams/frameworks/_linux_sandbox_emit.py` (verbatim emission).
**Dial.** R Full · D Core · S Light · E Skip.

```mermaid
flowchart LR
    SRC["agentteams template asset<br/>confine-run.sh (source of truth)"] -->|"emit verbatim"| ART["emitted<br/>sandbox/confine-run.sh"]
    SRC -->|"sha256 pinned in"| MAN["enforcement-integrity.json (SB18)"]
    ART -->|"consumer keeps<br/>byte-identical copy"| CON["consumer repo<br/>(sha-pin drift test)"]
    EDIT["intended byte change"] --> SRC
    EDIT -.->|"ping + both re-pin +<br/>consumer re-runs deny test"| CON
    MAN -.->|"--verify-integrity trips on<br/>an UNINTENDED edit (probe E4)"| STOP["fail-closed / flagged"]
```

---

## Part VII — Honest ceilings, verification & red-team

### SB20 — What is verified, and what is not  ✅/⚙
**Canonical facts.**
1. **Linux launcher: enforcement-VERIFIED.** A live-kernel deny test proves write-outside-`--scratch`,
   credential/sibling read, and raw egress are all denied for a real process (incl. a real `goose`
   process), reproduced on stock `bwrap`.
2. **macOS Seatbelt: enforcement- and profile-syntax-UNVERIFIED** off a mac host.
3. **Claude native Linux product arm: unverified** on stock Ubuntu (nested-userns); the mechanism is
   verified. These three are distinct mechanisms with distinct verdicts — never conflated.
4. **Reconciliation note (2026-09-01).** The sibling [Security Guide's](../agentteams-security-guide/README.md)
   Part VI predates the framework-neutral Linux launcher and still frames confinement as *"verified on
   macOS only."* Per current source (`host_features.py`, `confine-run.sh` status header) that framing is
   **stale**: the **Linux launcher is the enforcement-verified path** and macOS Seatbelt is the
   **unverified** one. This guide states the current facts; the security-guide skeleton + editions and
   its `audience-profiles.md` ceiling #4 are pending a matching correction (tracked as a remediation
   item). A reader comparing the two should treat *this* guide as current on the verification verdict.
**Source.** `agentteams/templates/universal/sandbox/confine-run.sh` (status header);
`agentteams/host_features.py` (Linux VERIFIED comment);
`docs_src/api-reference/workspace-privilege-scoping.md` (Linux verification verdict).
**Dial.** R Full · D Core · S Full · E Core (mandatory ceiling #3).

### SB21 — What sandboxing does NOT close  ✅/⚙
**Canonical facts.**
1. **T6 / host-as-TCB stay bounded, never closed.** A same-host operator shell or a key-holding peer is
   out of scope for these controls; the sandbox boxes a mis-steered *agent*, not a determined local
   principal.
2. **seccomp/Landlock is a further layer NOT yet added** — the bwrap launcher is filesystem + netns +
   NoNewPrivs confinement, not syscall filtering.
3. The PreToolUse hook's uncovered surfaces (SB16.2) remain the operator's responsibility.
**Source.** `agentteams/templates/universal/sandbox/confine-run.sh` (policy header);
`agentteams/templates/universal/security.template.md` (delete-gate limits).
**Dial.** R Full · D Core · S Full · E Core (mandatory ceiling #4 restated).

---

## Part VIII — Synthesis & reference matter

### SB22 — The end-to-end synthesis  ✅/⚙
**Canonical facts.**
1. A confined team's life: **request** (profile/token, SB4–SB6) → **decide** (`is_sandbox_capable` +
   advisory, SB7–SB9) → **emit** the mechanism artifact (SB10–SB13) → **wire** (operator activation,
   SB14–SB15) → **enforce** (OS + fail-open/closed hook, SB16–SB17), with the emitters + launcher asset
   **tamper-tracked** (SB18–SB19) and every claim **honestly bounded** (SB20–SB21). No single stage is
   the boundary; confinement is the composition, and it engages *as tested* only when opted-in and wired.
**Source.** all of the above.
**Dial.** R Full · D Core · S Full · E Light.

```mermaid
flowchart TD
    subgraph REQUEST
        r1["privilege_profile:<br/>cooperative/confined/exclusive"] --> r2["+ workspace_write_roots,<br/>protected_read_paths, *:sandbox token"]
    end
    subgraph DECIDE
        d1["is_sandbox_capable(framework, platform)"] --> d2["advisory: none / manual-wire / unenforced-host"]
    end
    subgraph EMIT
        e1["claude settings block"]:::m
        e2["goose Seatbelt .sb"]:::m
        e3["Linux bwrap confine-run.sh"]:::m
    end
    subgraph WIRE_ENFORCE["WIRE + ENFORCE"]
        w1["operator activates<br/>(merge / GOOSE_SANDBOX / WRAP)"] --> w2["OS confinement in force"]
        w3["PreToolUse hook:<br/>fail-open (coop) / fail-closed (confined)"]
    end
    REQUEST --> DECIDE
    d2 -->|"fatal + default"| FCX["FAIL CLOSED (refuse)"]
    DECIDE --> EMIT
    EMIT --> WIRE_ENFORCE
    INT["enforcement-integrity.json:<br/>pins emitters + launcher asset"] -.->|tamper-track| EMIT
    CEIL["honest ceilings:<br/>opt-in · inert-until-wired · Linux-verified-only · closes-nothing-absolutely"] -.-> WIRE_ENFORCE
    classDef m fill:#eef,stroke:#557;
```

### SB23 — Reference tables & glossary  ✅
**Canonical facts.**
1. The capability matrix (SB7), the two advisory codes (SB8), the three mechanisms and their emit paths
   (SB10–SB12), the pinned modules (SB18), and the four load-bearing ceilings (top of this map) are the
   quick-reference surface. Editions R and D carry the full tables; S carries the matrix + ceilings; E
   carries the four ceilings in plain words.
**Source.** this SKELETON.
**Dial.** R Full · D Full · S Core · E Light.
