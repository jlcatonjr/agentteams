# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### added (vulnscan: Go, pnpm, Bun, RubyGems, Gradle and NuGet lockfiles are scanned)

- New `agentteams.vulnscan.lockfiles` (stdlib only): `go.mod` (`require` with `replace` applied; Go stdlib not
  judged, go < 1.17 and `go.work` noted), `pnpm-lock.yaml` v6/v9 (a restricted line reader, no YAML library;
  conservative dev scope), text `bun.lock` (JSONC), `Gemfile.lock` (rubygems.org gems), `gradle.lockfile` (Maven
  `group:artifact`) and NuGet `packages.lock.json`. A hostile or malformed file (an unusual name or version, an
  unknown lockfile version, over 64 MiB) is unscannable and never raises.
- Fixes for them are plan-only: `fix.plan_lockfile` writes each tool's command under
  `## <label>: <lock> — plan-only (no staged edit)`; `executor.validate_diff` refuses a change to any of them and
  `executor_guard.refuse_plan_only` refuses a `stage-fix` where they are all a repository has.
- Discovery: `go.mod` is scanned and `go.sum` beside it is no longer listed (alone it stays unsupported, `Go (go.sum
  without go.mod)`); `bun.lock` now covers a `package.json` like `pnpm-lock.yaml`; `bun.lockb` stays unsupported.
- `versions.version_key` orders Go pseudo-versions, RubyGems `.pre`, Maven qualifiers (`M`, `SNAPSHOT`, `RELEASE`,
  `FINAL`, `GA`, `SP`) and NuGet four-part versions. `project_policy.is_private` stays case-insensitive for them.

### added (vulnscan: every common lockfile is detected)

- `manifests.discover` now lists `Package.resolved` (SwiftPM), `Podfile.lock` (CocoaPods), `pubspec.lock` (Pub),
  `mix.lock` (Hex), `bun.lock` / `bun.lockb` (Bun), `deno.lock` (Deno), `renv.lock` (CRAN), `stack.yaml.lock` /
  `cabal.project.freeze` (Hackage), `conan.lock` and `vcpkg.json` (C/C++), a Julia `Manifest.toml` next to its
  `Project.toml`, and `Gemfile` / `conanfile.*` without their lockfile. Each is reported `unsupported ecosystem:
  <name>`, so a repository holding only these is in the coverage state *unsupported ecosystem* (Rule S-VM reports
  it), never *nothing to scan*. None of them hides a `package.json` or `pyproject.toml` beside it.
- `composer.json` and `Cargo.toml` without their lockfile are reported unscannable ("without a lockfile", with the
  command that writes one), as `package.json` already was; a Cargo workspace member is covered by the root's
  `Cargo.lock`.

### vulnscan (a runtime product table: more runtimes, OS releases, unknown kinds reported)

- **`runtimes.PRODUCTS`** replaces the hard-coded Node.js/PHP/Python list. Each row is a frozen `Product`: the
  endoflife.date slug, the release-line rule (a part count, or a `cycle_fn` override), the security-release feed
  and the local `Probe`s. New kinds: `ruby`, `go`, `java` (judged on Eclipse Temurin's support dates and labelled
  so; `1.8.0_x` is line `8`), `java-corretto` (`amazon-corretto`, chosen by image name), `dotnet` (one item per
  installed runtime), `nginx`, `openssl` (`1.1.1` is its own line), `postgresql`, `mysql`, `mariadb`, `redis` and
  `docker` (`docker-engine`, server version only). Products without a security feed get `eol` and MODERATE
  `behind-patch` only, and their detail says "no upstream security-release feed; judged on end-of-life and latest
  patch only". `EOL_PRODUCT` stays as a derived dict; new public `cycle_of(kind, version)`.
- **`runtimes.OS_PRODUCTS`, `OS_CODENAMES` and `evaluate_os(os_id, version_id, today=None)`** judge an OS release
  (os-release `ID` or image name; Debian and Ubuntu codenames) on its release line alone.
- **`engine.local_runtimes`** runs every `PRODUCTS` probe found on `PATH` (stdout, stderr or both; 20 s timeout,
  `engine.PROBE_TIMEOUT`). `_version_of` is unchanged for its callers. A binary that is not the product
  (LibreSSL's `openssl`) or whose version cannot be read is unscannable; `mysqld` is judged as MariaDB or MySQL
  from its output.
- **An unknown `RUNTIME` kind is reported** under `unscannable` ("runtime kind <kind> not judged by this vulnscan
  version") instead of being dropped silently. An unknown release line or slug stays `unchecked`, never `ok`.
- **Fleet-wide effect:** the next `daily` and `runtimes` run judge every new product found on this machine, so new
  runtime findings can appear. A slug or release-line format endoflife.date does not use leaves that product
  `unchecked` (a feed error, exit 3) until the table row is corrected; the operator's verification script checks
  every slug before merge.
- Docs: `api-reference/vulnscan.md` (`runtimes` section with the product table, `local_runtimes`, the config and
  command tables).

### vulnscan (server kit: more runtime and server-software probes, package origin, OS release and ESM check)

- **`L1-audit.sh`** emits `RUNTIME` lines for `nginx`, `openssl` (LibreSSL is emitted as `libressl` and reported
  as not judged), `postgresql` (the server binary; `psql` only when none is found), `mysql`/`mariadb` (from
  `mysqld`/`mariadbd` output), `redis`, `ruby`, `go` (`GOTOOLCHAIN=local`), `java`/`java-corretto`, `dotnet` (one
  line per runtime version) and `docker` (server version over the local socket only; no socket access is a plain
  `INFO` line, never a `HOSTCHECK`). Each probe only prints a version, runs under `timeout 10` where `timeout`
  exists, and reads `/dev/null` as stdin; a binary is run by its found path and reported by its resolved one.
- **Package origin.** A dpkg- or rpm-owned binary's manager is `pkg:<package>` only when the installed version
  comes from an allowlisted distro archive host (`apt-cache policy`'s `***` block; `dnf -C repoquery --installed
  --qf '%{from_repo}'` or `yumdb`, resolved through `/etc/yum.repos.d`). Anything else, including a status-only
  version such as a local `.deb`, is `repo:<host>:<package>` or `repo:unknown:<package>` and is judged against
  upstream versions. Ubuntu universe/multiverse packages are `pkg:<package>:universe`. This also applies to the
  existing php/node/python lines.
- **`OSREL`** (`ID`, `VERSION_ID`, `PRETTY_NAME`; `/etc/os-release` is now parsed, never sourced) and, on Ubuntu,
  **`ESM`** (esm-infra, esm-apps from `/var/lib/ubuntu-advantage/status.json`; `pro status` is never run).
  `engine.scan_inventory` judges them: an end-of-life release is a HIGH `host` finding named `os-release`; an
  Ubuntu release past standard support is HIGH without esm-infra, LOW with it, and MODERATE with it when a
  universe runtime is present without esm-apps; an unknown `ID` is unscannable. An audit from an older kit has no
  `OSREL` line and no OS result. New `runtimes.os_extended_support()`; endoflife.date's extended-support dates are
  now read with each release line.
- **`server_fixplan`**: an `os-release` finding gets its own section before the commands, "plan an OS release
  upgrade; no kit lever applies"; non-Node runtimes in the L3 section say "upgrade through the package manager or
  the vendor (no kit lever)". The kit's `--apply` paths are unchanged.
- **Fleet-wide effect:** after the kit is re-staged, the next audit of each host can add OS release findings and
  judge third-party-repository installs (nginx.org, PGDG, NodeSource, docker-ce, PPAs) that were `distro` before.
- Workstation version probes run from `/` with `GOTOOLCHAIN=local`, an empty `GOFLAGS`, `DOCKER_HOST` pinned to the
  local socket and `DOCKER_CONTEXT` dropped, as `L1-audit.sh` does on a host (security review R-s1).
- `L1-audit.sh` runs `apt-cache policy` with its binary caches off, and its header states the one write a root run
  may still make: `dnf -C repoquery` may append to dnf's own log (security review R-s2).
- An Ubuntu release past standard support whose extended-support (ESM) data is unknown is HIGH, "ESM state
  unknown", whatever its `ESM` line says: unknown never grades as covered (code-hygiene R3).
- Tests: fake-binary L1 harness for the new probes, origins, `OSREL`/`ESM`; a read-only grep over `L1-audit.sh`
  (no service, install, `/etc` write, `pro` or network verb). Docs: `api-reference/vulnscan.md`, the remediation
  guide's kit table. The integrity manifest is re-recorded; the kit's own sha256s are computed at staging time.

### security (research hardening, follow-up to the briefing PR)

- **`fetch_text` / `fetch_text_and_date` close the DNS-rebinding window.** The host is resolved once, every
  answer must be public, and the request goes to one of those vetted addresses (up to 3, tried in order only on a
  connection failure or connect timeout) while the `Host` header, TLS SNI and certificate verification stay on
  the hostname. httpx no longer resolves the name a second time, and redirects are still not followed. Through an
  HTTP(S) proxy set in the environment the fetch now fails closed rather than resolving the name again (verified
  against httpcore 1.0.x, whose tunnel ignores the SNI hostname, so the certificate is checked against the IP; an
  `https://` proxy fails the same way, since the SNI hostname is spent on the handshake with the proxy).
  `NO_PROXY` is matched against the IP. One `UserWarning` per process says so when such a proxy applies; SOCKS
  proxies keep the hostname and are not warned about.
  `browser_fetch` keeps the residual: Chromium does its own DNS lookup after `is_public_https`.
- **The research-cache `git check-ignore` probe runs hardened** through `git_exec.run_git` (no `core.fsmonitor`,
  no hooks), with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR` and the `GIT_CONFIG_*` injection
  variables stripped, so it cannot be pointed at another repository or handed extra config.
- **The audit prompt fences evidence as data.** `research.verify` puts each claim's evidence under
  `RETRIEVAL_CONTENT_AUTHORITY` inside explicit fences, the way `briefing` fences a page for its digest.
- **The `research` extra now requires `pypdf>=6.21.2`** (was `>=4.0`). Earlier releases are affected by
  CVE-2026-115572, CVE-2026-115580 and CVE-2026-115585 (denial of service when parsing a malicious PDF) and
  CVE-2026-113786. Adopted under the S-10 scan-remediation carve-out: 6.21.2 is inside the 14-day cooldown, and
  the vulnerability scan confirmed the finding.

### added (research hardening, follow-up to the briefing PR)

- **`research.verify.audit_claim_pairs(pairs, chat_fn, lens="adversarial")`** audits each `(Claim, evidence)`
  pair against its own evidence, so identical claim texts from different sources are no longer audited against
  each other's pages. Each `Verdict` holds the pair's own `Claim` object (map back by identity). `audit_claims`
  now delegates to it, and `build_briefing` audits all claims in one `audit_claim_pairs` call (one critic request
  per claim).
- **Perspective coverage in `research.news`:** `PerspectiveSet`, `Coverage`, `gather_perspectives` and
  `perspective_summary` record whether a contested news claim has a second allowlisted news outlet behind it, and
  say "single source" when it does not. Other outlets are named "also covered by", never "reported by". Search
  is injected (`search_fn`); an `httpx.HTTPError` or `OSError` from it degrades to single-source.
  `gather_perspectives` raises `ValueError` on an unknown `kind` or a `k` that is not a non-negative int, and is
  the one `news` function that sees claim text: it sends that text to the caller's `search_fn` (a third-party
  search engine with `web_search`), so sensitive claims must not be passed. Adds
  **`ReputableSourceAllowlist.classify(hit)`**, the public per-hit allowlist filter (domain and path scope).
- **`agentteams.scan.match_injection(line, previous_line="")`**, a public per-line S-5 / C-1 matcher for
  non-agent text with no front-matter or code-span exemption. `research.screen.scan_read_content` uses it
  instead of its leading-newline `scan_content` workaround. `scan.py` is re-pinned in
  `references/enforcement-integrity.json`.
- **`memory_index.build_memory_index_from_texts(texts, *, project_name="", framework="", jsonl=None)`** builds a
  schema-identical index from in-memory id→text mappings (ids stored verbatim as `path`; `source_mtime` 0.0;
  each document marked `"origin": "text"`).
- **`research.cache.user_cache_dir()`**, the base per-user research cache directory shared by every project;
  `cache_dir()` falls back to a per-project subdirectory of it, never the base itself (see *changed*).

### fixed (research hardening, follow-up to the briefing PR)

- **The anti-fabrication backstop no longer downgrades a real refutation over an inflection** ("models" against
  "model"). Words are compared after conservative folding of ASCII alphabetic, non-acronym tokens; digits and
  acronyms never fold, and a word absent from the evidence in every form is still rejected. `-ed` and `-ing` fold
  to separate keys (`stem~ed`, `stem~ing`), distinct from each other and from the bare stem, so a correction that
  only changes tense or aspect is still downgraded to `"survived"`.
- **`feature_audit.run_proof` runs proofs with `sys.executable`**, not the `python` on `PATH`, which could belong
  to another project's venv. When that interpreter lacks pytest, the proof is reported as `HARNESS-BROKEN`
  naming the interpreter, instead of a `FAIL` per row.
- **`briefing` labels contested news claims.** A claim the digest marks `contested` from a news source gets
  `perspective_summary`'s single-source wording ("contested characterization, single source: no second outlet
  found"), capped at 120 characters (a label that would clip drops the date and keeps "contested
  characterization, single source"); no second outlet is searched for.
- **The test suite tests the tree it is run from.** `tests/conftest.py` puts its own tree first on `sys.path`
  and `PYTHONPATH` (so subprocess tests import it too); if another copy of `agentteams` was already imported,
  the run stops with exit code 4. Running from a git worktree no longer silently tests the main checkout.
- SSRF-guard tests in `test_research_search.py`, `test_research_browser.py` and `test_live_probes.py` stub
  `socket.getaddrinfo`, so they pass offline.

### changed (research hardening, follow-up to the briefing PR)

- **The research cache no longer defaults to a path git could commit.** Without `AGENTTEAMS_RESEARCH_CACHE_DIR`
  it uses `references/research-cache/` only outside a git work tree or when `git check-ignore` confirms the path
  is ignored; otherwise a per-project subdirectory of the per-user cache directory
  (`$XDG_CACHE_HOME/agentteams/research`, `~/Library/Caches/agentteams/research` on macOS, else
  `~/.cache/agentteams/research`), named by the first 16 hex digits of the SHA-256 of the resolved repository
  root (or the working directory), with one warning per process. One project's entries are never served to
  another. Any git error counts as "not ignored".
- **Memory index format v3 (`INDEX_FORMAT_VERSION = "json-bm25-postings-v3"`): `.jsonl` sources index one
  passage per record** (max 20), and a JSON-object record contributes its string values (`text`, `content`,
  `message` first) rather than its raw JSON. Their passages, tokens and lengths differ from before (JSON keys are
  no longer indexed); other sources are unchanged. `is_index_stale` skips documents marked `"origin": "text"`
  (built by `build_memory_index_from_texts`) and treats an `index_format_version` mismatch as stale, so a v2
  index is rebuilt once. The incremental updater declines a version mismatch
  (`index_format_version_mismatch`), which falls back to a full rebuild.
- Tests honour `AGENTTEAMS_TEST_INSTALLED=1` to test an installed package: `tests/conftest.py` then skips the
  source-tree path insertion and the import-location guard.
- `.github/CONTRIBUTING.md` documents running tests from a worktree, the DNS-stub convention, and building
  prompt-injection test payloads at runtime.

### added (research: topic briefings)

- **`agentteams.research.briefing.build_briefing`** (async) composes the package into a topic briefing: rated
  outlets, web and scholarly sources found from the topic alone (no link-following); `is_public_https` before
  each fetch; `scan_read_content` on every page and every digested claim; a caller `chat_fn` digest into a fixed
  claim shape under `RETRIEVAL_CONTENT_AUTHORITY`; an audit of each claim against its own page with a caller
  `critic_fn` (one `audit_claim_pairs` call, one critic request per claim, since the research hardening
  follow-up above); and cross-domain corroboration (`single_source`). It returns `Briefing` / `BriefingClaim` /
  `BriefingStats` in memory; storage and expiry are the caller's. `Retriever` injects the network for tests.
  Ported from masterTeacher's briefing tool (nx1f N21), after LingoFriend's dossier pattern.

### added (research: a read-content screen for fetched pages and retrieved passages)

- **`agentteams.research.screen.scan_read_content` and `is_inert_read_content`** screen C-4 read content before a
  model reads it, or after a model has summarised it. They are built on `agentteams.scan.match_injection` (the
  per-line matcher behind `scan_content`), so the S-5 and C-1 patterns and their normalisation are shared, but
  with no front-matter or code-span exemptions. `is_inert_read_content` returning `True` means no pattern
  matched, not that the content is safe; read content stays inert data (C-4). They add
  model-directed phrases ("system prompt", "tell the user" ...) that agent files may legitimately contain, plus
  caller `extra_patterns`. `scan.py` (a pinned enforcement module) was unchanged by this entry; the research
  hardening follow-up above adds the public per-line matcher `scan.match_injection` that the screen now uses,
  and re-pins `scan.py`. From masterTeacher's briefing tool (nx1f N21, @security condition 2).

### fixed (research: the SSRF guard checks every address a host resolves to)

- **`agentteams.research.search.is_public_https`** now resolves with `socket.getaddrinfo` over IPv4 and IPv6 and
  refuses a URL if ANY answer is not globally routable unicast. Before, it checked only the first IPv4 answer
  (`gethostbyname`), so a host with a private AAAA record or a second private A record passed, as did shared
  CGNAT space (100.64.0.0/10) and multicast. IPv4-mapped IPv6 answers, NAT64 (`64:ff9b::/96`), 6to4 and Teredo addresses are judged by the IPv4 address they carry, and an explicit denylist stands beside `is_global` so the guard does not depend on one Python release's special-range table. The
  browser's per-request route guard inherits the fix. The DNS-rebinding window between the check and the fetch
  is closed for `fetch_text` by the research hardening follow-up above (the browser keeps it, documented). Reported from masterTeacher's briefing tool (nx1f N21, @security condition 5).

### fixed (vulnscan remediation runner: the agent window was never passed)

- `vulnscan-remediate-auto.sh` built `--agent-since`/`--agent-until` with a Python snippet that used single quotes
  inside the script's single-quoted `python -c` string. Bash ended the string early, Python raised `NameError`, and
  `2>/dev/null || true` hid it, so `remediation.md` never got its "Agent run (executor rows)" section (found in the
  2026-10-30 re-run). The snippet now uses double quotes only and no longer hides its errors. A new test runs the
  tracked script's own fragment under bash. Re-run `vulnscan-remediate-auto.sh --install` after deploying.

### fixed (vulnscan: an unreadable ledger key is a clear refusal, not a crash)

- **`vulnscan.ledger`:** when the ledger key exists but cannot be read (a sandbox, a permission), `vulnscan
  review`, `halts` and every ledger read failed with a `FileExistsError` traceback: `Path.exists()` reads an
  unreachable file as missing, and the key was then created with `O_EXCL`. It now raises `LedgerKeyError`
  ("… cannot be read (…)"), which the executor already refuses on as a ledger integrity failure, and it never
  creates a second key over one it cannot see. A key another process creates between the check and the create is
  used instead of failing, and a key that is not exactly 32 bytes (empty after an interrupted create, or damaged) is
  refused instead of being used as an empty HMAC key. Integrity manifest re-recorded.
- **Filing conventions:** `references/agentteams-remediation-log.csv` is labelled *ignored here (local operational
  log)*, matching `.gitignore`, which deliberately keeps it out of the public release; it had been labelled
  *tracked*.

### added (API Impact Review: a pre-commit hook plus review, audit and revision of what a change touches)

- **`agentteams.api_impact`** detects API impact from git alone: Python modules and public symbols (functions,
  classes, public methods, `__all__`) added, removed, renamed or re-signed; `add_argument` options and
  `add_parser` subcommands; `*.schema.json` changes; and other tracked files added, removed or renamed outside
  docs, tests and work records. `status` computes each commit's review state since the branch base (merge-base
  with the default branch; the unpushed commits on the default branch; never a pass without a base). A commit
  is reviewed when its stable patch-id has a ledger file in `references/api-impact-reviews/` committed at `HEAD`;
  `record --note` writes those files (a self-attestation, visible in the PR). `status` also prints an audit
  checklist of the files that mention each changed name.
- **The `pre-commit` hook** gains an API-impact step (all teams; `git_hooks._render_hook_block`): it prints the
  staged impact and never fails the commit. It needs Python 3.11+ and runs with `-P`. In the agentteams
  repository, `git config agentteams.regenerateMan true` also makes it regenerate and stage `agentteams.1`
  when the parser changes.
- **Orchestrator:** a new **API Impact Review** sub-protocol (scope → review and update the API → audit the
  related infrastructure → revise → record) and Workflow 11 gate **6b**, which runs before the push and keeps
  the session open as *API review pending* until the branch's impactful commits are reviewed
  (`available_workflows` v10, `routing_table_rows` v6). New always-emitted `references/api-impact.reference.md`.
  `@module-doc-validator`'s Change Impact Analysis takes the reported names as its scope.
- Docs: `api-reference/api-impact.md`, the git-hooks page and figure, and the user guide.

### added (Operator Decision Query: decisions go through the interactive picker)

- **Operator Decision Query**, a new orchestrator sub-protocol (`available_workflows` fence v9, routing row in
  `routing_table_rows` v5) backed by a new always-emitted `references/operator-decision.reference.md`. A decision
  only the operator can make is put through the host's interactive selection interface (`AskUserQuestion`,
  `vscode/askQuestions`, `ask_user`, `request_user_input`). The agent offers its 2–4 best options with the
  recommended one first and a consequence line for each. Without a picker it asks with a numbered list in the
  same shape. The agent holding the operator's session asks (normally `@orchestrator`); subagents return the
  decision to whoever spawned them. An answer never clears a HALT, replaces a clearance or widens a capability.
  Non-interactive runs log the question to the escalation log, never assume an approval, and take the
  recommendation only when it is reversible. Workflows 0, 0C, 12 and 14, `@pr-manager`'s disposition prompt
  (fence v2) and `@security` Rule S-4 (fence v20) hand their operator decisions to it.
- Not in this release: a canonical `ask` capability that would grant the picker tool to a scoped orchestrator
  (`claude --agent`, a VS Code custom agent). Until then a scoped orchestrator uses the numbered-list fallback.

### added (Workflow 0C: complex-task metaplans) · changed (sandbox handoffs: running the script is the approval)

- **Workflow 0C (Complex-Task Metaplan)** in the orchestrator template (`available_workflows` fence v8, routing
  row in `routing_table_rows` v4). A request with two or more separable items gets one development or
  remediation plan per item, then a metaplan (`<meta-slug>.metaplan.md` + `.metaplan.steps.csv`, one row per
  item plan) that schedules them with `agentteams.parallel_plan`. The metaplan **and every plan it targets** are
  audited by `@adversarial` and `@conflict-auditor` and revised before anything is implemented; then the
  metaplan is implemented in rounds that keep each item plan's per-step audit. Workflow 0 step 5 hands complex
  requests to it, and the parallelization reference gains a *Metaplans* section.
- **Sandbox handoff scripts no longer prompt unless they are destructive.** `HandoffSpec.confirm` and the spec's
  `confirm` key now default to `false`: reading the script and running its command is the operator's approval.
  A destructive script still asks one `y/N`, on top of the `@security` clearance recorded before it was written.
  Because an unprompted script must not be mislabelled, destructiveness is now also **derived from the
  commands** (`sandbox_handoff.destructive_commands()`): a spec whose steps delete, force-push, reset, rewrite
  history, or run a destructive `gh`/`agentteams` mode is refused unless it is marked `destructive` with a
  clearance. Every script now prints its purpose before the first step (it used to print only inside the prompt). The
  sandbox-handoff reference (fence v2) and the orchestrator's handoff step also say a script holds **only** the
  operations the sandbox refused; everything the agent can do itself stays with the agent.

### vulnscan (remediation runner: wait for the day's agent run)

- `vulnscan-remediate-auto.sh` no longer acts on a day as soon as its folder exists. `summary.md` is written
  before the daily run's agent phase, so a tick in between ran remediation and set `remediation.done` before
  the agent ran, and fixes approved later waited a day (2026-10-30: remediation 09:01, agent 09:03–09:05). The
  job now waits while the daily run holds its lock and, with the agent on, until `agent/status.json` records
  `finished` (no longer waited for after `GIVE_UP_HOUR`). Re-run `--install` so the installed copy matches.
- `remediation-report` gains `--agent-since TS`, and the runner passes the agent run's start from
  `agent/status.json`. The agent's own executor rows before the attempt appear in a new "Agent run (executor rows)"
  section of `remediation.md`, with their refusals under "Needs you". They never count toward `infra_count`, so they
  never cause a retry. On 2026-10-30, `remediation.md` said "Needs you: nothing" while the agent's 9 requests had
  been refused.

### vulnscan (deploy safety: wait on both job locks, and say which images are missing)

- **New `scripts/vulnscan-remediation/vulnscan-deploy.sh [REF]`** (default `origin/main`) reinstalls the package
  the daily job runs, from a `git archive` of the commit extracted outside any work tree. During
  `pipx install --force` it holds the remediation lock (`remediate.flock`) and then `daily.lock`, the same order
  the runner takes them, waiting up to 30 minutes for each (`--no-wait` refuses at once). It refuses while a
  legacy `remediate.lock` directory has a live owner.
- After installing, it prints each Docker image tag the installed version needs and whether
  `docker image inspect` finds it. The tags follow the package version, so a version bump orphans the old
  images. It then runs `vulnscan ready --json` when the installed version has that command, else
  `vulnscan-ensure-docker.sh --no-start`, followed by `halts`, `handoff status` and `schedule status`.
- It rebuilds the tools image, and the agent image when agent mode is on (`--skip-images` skips both).
  `--with-scripts` re-installs the runner's scripts from the same commit. It signs, merges and pushes nothing.

### vulnscan (the unattended remediation runner is tracked; its outcome comes from the ledger)

- **`scripts/vulnscan-remediation/`** now holds the macOS remediation runner that operators had installed by
  hand: `vulnscan-remediate-auto.sh` (the 30-minute launchd job), `vulnscan-ensure-docker.sh`,
  `vulnscan-daily-wrapped.sh`, `vulnscan-target-agents.sh` and `vulnscan-handoff-lib.sh`. `--install` copies
  these from the tracked folder. Site settings (`VULNSCAN_REPOS_ROOT`, `VULNSCAN_GITHUB_ORG`, `MANAGER_TEAM`,
  `MANAGER_TEAM_DIR`) come from `~/.config/agentteams/vulnscan-remediation.conf` (read, never sourced) or the
  environment. Without `VULNSCAN_GITHUB_ORG`, no repository is handed off. The handoff steps stay in a site-local
  `vulnscan-operator-actions.sh`, which is not published.
- **Roster enrolment and goose-team creation are operator-only** (`gate`, not `ask`). The scheduled job used to
  approve both in auto mode. It now only reports a repository without a goose team or without the manager on its
  roster, because a roster is a trust root and the drafted brief comes from README text. `MANAGER_TEAM` must be a
  plain team name. `--install` backs up the scripts and plist it replaces, and XML-escapes the plist values.
- **New `python -m agentteams.vulnscan remediation-report LEDGER SINCE`** (`agentteams.vulnscan.remediation_report`)
  replaces the runner's standalone outcome script. A refused or failed ledger row counts as infrastructure, and
  is retried, only when its detail *starts with* `tools container unavailable:` or its `reason_code` starts with
  `infra.`. The match is anchored, never a substring, because details can echo repository text. An unreadable
  ledger fails closed: it counts as infrastructure. The command also writes `remediation.md`, and every refusal
  is listed under *Needs you*.
- The runner's `mkdir` lock is now `remediate.flock`, held by a holder process for exactly as long as the
  script's pid lives. A killed run frees it at once, and child processes do not inherit it. A legacy
  `remediate.lock` directory is honoured while its pid runs and removed when that owner is gone.

### vulnscan (report labels: pre-release fix targets, held count timing)

- The report's "Upgrade to" cell marks a pre-release target: `26.10.0rc1 (pre-release; the advisory names no
  stable fix)`. OSV's fixed event can be a release candidate (Twisted, 2026-10-30); `stage-fix` already skips
  pre-releases, so only the label changes.
- The effectiveness row now reads "Repositories held at scan time (nothing stageable; see the held reasons)": the
  summary is written before the remediation runner holds more repositories, so `held-repos.json` can list more later
  the same day, and a hold can now also mean "already fixed on the default branch".
- The stage-fix zero-change decision moved to `retry_guard.zero_change_detail()` / `nothing_vulnerable()` (CH-07:
  `executor.py` stays under the module ceiling); a checkout whose git query fails now shows in the Checkout freshness table as "git query failed" (unlabelled)
  instead of being dropped silently (CH-24).
- New `versions.is_prerelease()`; the `vuln-remediation` agent template says a `held:` refusal can also mean the
  default branch already has the fix and the local checkout is behind.
- Internal, behaviour unchanged: `vulnscan/daily.py` (1243 lines, over the 1000-line module ceiling) is split into
  `checkout_freshness.py`, `infra_retry.py` and `worktree_fixes.py`. `daily` re-exports the names scripts and tests use
  (`daily.infra_retry_due`, `daily.stage_worktree_fixes`, ...), and the moved code still runs git through
  `daily._git`. The three modules are pinned in the integrity manifest.

### vulnscan (stage-fix: a fix already on the default branch is not reported as a major jump)

- When `stage-fix` changes nothing and a scan of the default branch (the executor's worktree) finds nothing
  vulnerable, the refusal is now `already fixed on the default branch (<ref>): nothing vulnerable there; update
  the local checkout` instead of `nothing fixable without a major jump`. Today 7 repositories were held with the
  wrong reason: their default branches have the fix and their local checkouts are behind. The futile-retry
  guard holds the new result the same way, with a held reason that says to update the checkout. A scan that
  checks nothing or hits a feed error never counts as "nothing vulnerable".

### vulnscan (findings already fixed upstream are labelled, not counted as fixed)

- `summary.md` gets a **Checkout freshness** section after the per-repository sections: each scanned repository's
  branch, commits behind `origin/HEAD` and the age of `FETCH_HEAD`, read from the local refs (no fetch, no
  credentials).
- A package finding whose `name==version` is gone from the same file at `origin/HEAD` gets `fixed_upstream: true`
  in `summary.json`, and its Notes cell ends with `fixed upstream (checkout behind)`. It stays in the findings.
  Nested repositories and third-party clones are skipped.
- *Remediation effectiveness* has a new row for these findings, which are not counted as fixed; the other rows are
  unchanged. Scanning `origin/HEAD` instead of the working tree stays out of scope.

### vulnscan (catch-up retries a run Docker left partial; a re-run keeps the earlier summary)

- `daily --catch-up` also re-runs today's run when it exited 3 because of Docker (an `infra.*` reason code in the
  new `summary.json["infra_codes"]` or in `remediation.docker`; feed error text is never matched, because the
  `tools container unavailable` prefix also covers ssh, mount and allowlist refusals) and Docker is ready now. New config key
  `catch_up.max_retries_per_day` (default 2) bounds it; the count is kept in the run directory's
  `infra-retry.count`. A retry sends no `run-stale` notification. Until now a recent partial run was never retried.
- A same-day re-run first copies the earlier `summary.json` / `summary.md` to `summary.<HHMMSS>.{json,md}` (the
  time the earlier one was written), so it no longer overwrites a full run's summary. Readers still read only
  `summary.json`.

### vulnscan (one Docker readiness check, with reason codes)

- **New `docker_readiness` module and `vulnscan ready [--ensure] [--json]`** (exit 0 ready, 3 degraded, 2 an
  unusable config). It checks the docker CLI, the daemon, the tools and agent images and their self-test records,
  and gives each failure a stable reason code: `infra.docker_daemon`, `infra.tools_image`, `infra.selftest` or
  `infra.no_gui_session`. A stopped daemon, an image that is not built and a stale self-test no longer share one
  message.
- The four Docker probes now delegate to it, with unchanged signatures: `subproc_confine.unavailable_reason`,
  `agent_run.docker_unreachable`, the `tools_container` entry of `remediation_status.collect`, and the executor's
  batch check. A refused ledger row for an unavailable tools container gains `reason_code`; its detail keeps the
  `tools container unavailable:` prefix.
- **The daily run checks Docker once, before it scans.** The servers, the staging, the remediation readiness
  section and the agent phase use that record instead of probing again. `summary.json` records it as
  `remediation.docker`, `{layer: {state, code, message}}`.
- New `docker` config block: `autostart` (default **false**: only an operator's opt-in lets the run start Docker,
  with `open -g -a Docker` or `systemctl --user start`, never sudo), `start_timeout_s` (240) and `auto_selftest`
  (`on-change`: a self-test whose record is for another image is re-run, once per image change). It never builds
  an image.

### security (S-10: release cooldown restored, latest stable only, scan-remediation carve-out)

- **The 14-day package-release cooldown is back in force (operator ruling, 2026-10-29)**, reversing the
  2026-10-26 deprecation below, in every ecosystem. **New: only the latest stable release is adopted**: no
  pre-releases, no yanked or deprecated releases, no older release when a newer stable one has cleared the
  cooldown; holding a package back needs a recorded `@security` clearance.
- **Carve-out: neither rule ever blocks remediating a vulnerability the vulnerability scan detected.** The fix
  release may be adopted inside the cooldown and need not be the latest stable release. It must still pass
  the known-vulnerability search (no OSV `MAL-*` entry) and Rule S-9's identity check, and the carve-out covers
  only the packages the scan flagged (and the dependencies their fix pulls in). Generated `@security` agents carry it in Rule S-10 (fence v19), the S-VM
  deadline paragraph, the verdict table and the install trigger; the orchestrator's vulnscan-PR steps and the
  security feed playbook follow.
- vulnscan only ever remediates scan findings, so `fix`, `stage-fix` and the daily job keep adopting fix
  releases without an age gate. The constant is renamed `executor.REMEDIATION_COOLDOWN_DAYS` (still 0), and
  `cooldown_days` / `fix --cooldown-days` stay ignored, with notes that name the carve-out.
- Server kit: `L3-node-runtime.sh` and `L5-containers.sh` default to a 14-day cooldown again; `--allow-fresh`
  is the carve-out. `run-host.sh --allow-fresh` passes it on to L3 (never L5), and a server fix plan puts it in
  its commands when the scan found the host's Node runtime missing a security release.
- The carve-out stays per flagged host: `run-host.sh all --allow-fresh` is refused (exit 2). L5's `--allow-fresh`
  and `vulnscan-agent-image.sh --trust-download` (which lifts the script's codex age check) say they need a recorded
  `@security` clearance, since vulnscan does not scan container images or the agent CLIs. The API reference (server fix plans, npm
  fixes without `--before`), the vulnscan README and the remediation guide describe the carve-out.

### vulnscan (Rust lockfiles, honest coverage, launcher versions, a sturdier server kit)

- **Rust `Cargo.lock` files are scanned (crates.io).** Crates from the crates.io index (`registry+` and `sparse+`
  sources) are checked against OSV; path, git and other-registry crates are skipped, and a lockfile with none is
  reported unscannable. Every crate counts as runtime, since the lockfile does not record a dev or build split.
  **Fleet-wide effect:** every repository with a `Cargo.lock` gets crates.io findings and fix plans from the next
  daily run. Fixes are **plan-only**: the plan gives `cargo update -p <name>@<installed> --precise <fix>`; the
  executor never edits a `Cargo.lock` (`validate_diff` refuses it) and the agents are told not to request
  `stage-fix` for these rows. `Cargo.lock` does not join `LOCKFILES`, so a `package.json` or `pyproject.toml`
  beside one is still reported. `private_packages` match crate names with `-` and `_` alike. A lockfile is
  repository content: a crates.io entry whose name or version is not plain crate-name / version text makes the whole
  file unscannable, so no such text reaches a fix plan the agents read. `stage-fix` on a repository whose only
  manifests are plan-only is refused before a grant use is spent (`executor_guard.refuse_plan_only`).
  **Expect a one-time burst:** the first daily after upgrading reports every existing crates.io finding as new.
- **Lean's `.lake` package cache is skipped** like `node_modules`. Its vendored packages' own lockfiles are not
  the project's dependencies.
- **`status --project` and the coverage section no longer list manifests the scan never reads.** Before, files
  inside the config's `exclude` paths or matching the brief's `exclude_paths` were listed as "not scanned", and could
  make a project show as `unsupported ecosystem`. `coverage.unsupported_manifests(path, cfg)` now applies both.
- **Which launcher version a team runs.** New `agentteams.launcher_lineage` and
  `agentteams/data/launcher_lineage.json` (generated by `scripts/build_launcher_lineage.py`; a test fails when the
  template changes without regenerating). They tell a `sandbox/confine-run.sh` copy `current`, `older` (and whether a
  newer version is a security fix), `modified` (no released version) or `missing`. The launcher's bytes are
  unchanged, so consumers' sha256 pins still hold. `status --project` prints a `launcher` line; `handoff status`
  names the launcher the handoff's goose session would run under and warns unless it is current. Informational only.
- **The server kit runs from a verified copy.** `run_kit` used to mount the kit straight from the installed package.
  On 2026-10-28 a catch-up daily reported all three servers unscanned because Docker could not find that directory
  in the pipx venv (140 findings missing, the run partial). The kit is now copied, when first needed, into a 0700
  directory under the state dir: regular files only (symlinks and subdirectories refused), every required script
  present. A long-running executor re-stages when the packaged kit changes. Each run re-hashes the copy and refuses
  one that changed after staging. A missing kit fails closed with a reason instead of `docker exit 125`. Not covered:
  a kit altered on disk before staging (the integrity pins are not shipped in an installed package).
- **Integrity:** `references/enforcement-integrity.json` is regenerated for the changed pinned modules and newly pins
  `agentteams/launcher_lineage.py` and `agentteams/data/launcher_lineage.json`.

### vulnscan (the reference is checked against the code)

- **`tests/test_vulnscan_doc_conformity.py`** fails when `docs_src/api-reference/vulnscan.md` drifts from the
  code, in both directions where the doc has its own table. It checks:
  - each configuration block's keys and defaults against that block's own table (`DEFAULT_CONFIG`, `DEFAULT_AGENT`
    with the debrief defaults, the upstream defaults, the confinement defaults);
  - every notification, word for word (`notify.MESSAGES`);
  - every subcommand, in the command table and in the exit codes;
  - every long option and positional choice, in a row or paragraph about its own command;
  - every module, under its own section or named in code in the section `_DOCUMENTED_IN` gives.

  Headings inside fenced code are ignored. The 2026-10-28 audit found about 25 drifts like these that no test
  caught. The reference now lists `handoff status`'s exit codes, `act`'s operations, and which module defines
  which commands. The PR template asks for the reference to be updated with vulnscan changes.

### vulnscan (a remediation PR closed unmerged is not re-opened)

- **`open-pr` opens nothing for a change a person closed without merging.** After its open-PR claims and before
  pushing, it looks for the repository's own closed, unmerged PRs on `vulnscan/<any date>-<same digest>`, found by
  the digest in their title, at most 3. One counts only if every commit is by the executor's identity and its
  change, read from `refs/pull/<n>/head` (kept after the branch is deleted), equals the staged change byte for
  byte, so a closed PR cannot hold back a fix it does not carry. PRs that `close-superseded` closed do not count.
  When one counts, it retires the staged fix and returns `closed_unmerged`, like `already_in_base`. Before, the fix
  kept its approve-staged verdict, the same-change claim matched only open PRs, and the next run pushed it again
  (vk-data-uploader #223, closed 2026-10-28 because the coordinated upgrade #225 superseded it; data-quality-monitor
  #19 needed a hand-written reject verdict the same day). Best effort: a gh or git failure opens the PR as before.

### vulnscan (documentation audit)

- **Module guide.** New `agentteams/vulnscan/README.md` describes what vulnscan does (scanning, fixing, daily
  runs, enrolment, agent-driven remediation, operator controls, what leaves the machine), with a quick start and a
  module map. The root README links it.
- **API reference completed and corrected** (`docs_src/api-reference/vulnscan.md`). Adds sections for
  `manifests`, `versions`, `agent_settings`, `runtimes` and `project_cli`, and entries for about 30 public
  functions other modules call. Fixes about 25 inaccuracies found by checking the page against the code, among
  them a stale 14-day fix cooldown (now 0), a stale claim that runs pass no host or private names to the DLP, the
  `Report` and `FixResult` fields, a broken JSON-output table, the exit codes of `enroll`, `roots`, `status` and
  `tools-image`, missing CLI flags, and headings filed under the wrong module.
- **Guides corrected and brought up to date.** The remediation guide and the automation walkthrough now cover
  handoff delivery, the Remediation readiness and Remediation effectiveness sections, server fix plans, held
  repositories, the newer notifications and the grant-cap refusal. The walkthrough's sample self-test output shows
  the current goose checks and record fields.
- **The handoff docstring no longer names an unshipped operator script.** Writing handoff packets is operator
  tooling outside the package. The integrity manifest is re-recorded, and only `handoff.py`'s digest changes.
- **Operator scripts 05, 06 and 09** take the state directory from `agentteams.vulnscan.paths.state_dir()`, so they
  honour `AGENTTEAMS_VULNSCAN_STATE_DIR` and `XDG_STATE_HOME` instead of assuming `~/.local/state`.

### vulnscan (a staged change must resolve an advisory)

- **`stage-fix` refuses a change that resolves no advisory.** npm audit fix can move a package within its range to
  a release that is still affected: data-quality-monitor's qs went 6.14.2 → 6.14.3 inside express's `~6.14.0`
  range, while all four qs advisories need 6.16.1. The change was staged, approved and opened as PR #19
  (2026-10-28) and fixed nothing. `fix --apply` now counts each changed manifest's (ecosystem, package, advisory)
  occurrences, one per installed copy, before and after the change (OSV; `private_packages` are never looked up;
  a feed error fails closed) and records the number removed in `FixResult.resolved` (`None`: not measured). A new
  advisory on another package does not offset a fix. `stage-fix` refuses `resolved == 0` as "nothing fixable
  without a major jump", so `retry_guard` holds the repository until its fix plan changes, and the staged record
  carries `resolved`.

### vulnscan (no duplicate PR for a fix re-staged on a later day)

- **`open-pr` claims an open PR that already carries the same change under an earlier date.** The staged branch
  is `vulnscan/<YYYYMMDD>-<digest>`, and open-pr found an open PR only by that exact name. So a fix re-staged
  the next day with the same digest pushed a new branch and opened a duplicate PR (traffic-file-enrichment-app,
  2026-10-28, beside #25). It now returns the open PR on `vulnscan/<any date>-<same digest>` (`existing: true`,
  `same_change_as`) without pushing, but only if every commit on it is the executor's and its whole change equals
  the staged change byte for byte (the digest, public and 40 bits, only selects candidates). Forks' PRs are
  ignored as before; at most 3 candidates are examined; a gh or git failure opens the PR as before.
- **`close-superseded` requires the replacement to be the executor's own PR** (every commit by its git identity),
  since open-pr can now record a claimed PR as the newest.
- **`open-pr` opens nothing for a change already merged.** When every staged path already has its staged content
  on the base branch (an earlier day's PR with the same change was merged), it retires the staged fix and returns
  `already_in_base: true` instead of opening a PR that carries nothing.

### vulnscan (conditional includes in a repository's git config are refused)

- **`fix --apply` and `stage-fix` refuse a repository whose git config has an `includeIf.*` section.** Its
  condition is evaluated where git runs. So `includeIf "gitdir:**/worktrees/**"` or `"onbranch:vulnscan/**"` could
  add a filter driver that is invisible to the config check in the repository but live in the executor's worktree,
  where the checkout would run it. A plain `include.path` is still allowed; what it adds is checked as before.
- **`stage-fix` checks the config again where it is about to run git, before the checkout.** The worktree is now
  created with `--no-checkout`, the config check runs inside it, and only then are its files checked out. Config
  that changed during the fetch (or an include target that appeared) is refused before any filter could run.
- **A config query git fails is a refusal**, not "no problem" ("git cannot read this repository's config").

  This closes the follow-up in #311's security review.

### vulnscan (a reply that is all reasoning)

- **`agent.reasoning` bounds the model's reasoning.** Both rc.9 live runs (z-ai/glm-5.3) spent the whole output
  of one reply on reasoning, hit the output-token limit before any tool call, and ended `agent-error` with no
  report. The new chat-route setting (`null` by default: nothing changes) is an OpenRouter `reasoning` object
  with exactly one of `effort`, `max_tokens` (below `agent.max_output_tokens`) or `enabled: false`. The
  gateway's new `--chat-reasoning` flag writes it into every chat request, replacing the client's
  `reasoning` / `reasoning_effort` / `include_reasoning`. Both the setting and the flag are checked at load. The self-test
  fingerprint is unchanged.
- **The run state names the cut-off.** The goose driver reports goose's output-limit warning as "the model's
  reply reached its output-token limit before it called a tool". Its error-line fallback skips tool data
  (JSON and Markdown table lines): rc.9's runs were reported as `"feed_errors": [],`.
- **`list_requests` returns the 20 newest results, newest first** (was the 50 newest, oldest first). The
  request history grows all day and was part of the context the model reasoned over.

### vulnscan (superseded PRs and the merge digest)

- **New executor op `close-superseded`.** Each `open-pr` opens a new `vulnscan/*` PR (the executor never
  force-pushes), so an older PR for the same repository stayed open beside its replacement. Under a grant that
  names the op, the executor closes the repository's older PRs once the newest is open: only PRs it opened
  itself, in the same repository, with no reviews, no comments and no commit by anyone else. It posts a fixed,
  scrubbed comment naming the replacement, keeps the branch, and never merges (`vulnscan/executor_supersede.py`).
  The remediation agent requests it after a successful `open-pr` (`invariant_core` v3, `daily_remediation` v4);
  `@security`'s S-VM rules say what it may close (`security_rules_invariant` v18).
- **Daily merge digest.** Each run writes `merge-digest.sh` (0700, machine-local, never published) into its
  run directory: one block per open, mergeable remediation PR whose checks all passed, each asking before
  `gh pr merge --match-head-commit`. It runs with the operator's credentials and is not a grant use (the
  grant op `merge` stays reserved). It is built only from the validated repository, number and head SHA,
  `shlex`-quoted (`vulnscan/merge_digest.py`). The summary counts the PRs ready to merge.

### vulnscan (remediation effectiveness)

- **The daily summary says whether remediation lands.** A new **Remediation effectiveness** section
  (`effectiveness.section`) counts findings fixed and new since the previous run, the remediation PRs the
  executor opened (open, the oldest one's age, open longer than 3 days, failing CI, no CI checks, merged in
  the last 7 days), fixes staged with no PR yet, and held repositories. Counts only: the summary is
  published, so the section names no repository, PR, host or path. PR state comes from read-only
  `gh pr view` on the ledger's PR URLs.
- **New alerts.** `stale-prs` when a remediation PR is open longer than 3 days or fails CI; `agent-down` when
  the agent has not completed for two runs in a row (the 2026-10-25 outage ran three days with only
  per-run `agent-not-completed` messages); `repo-held` once per held repository plan.
- **No more futile daily retries.** `stage-fix` of a repository whose last outcome was "nothing fixable without
  a major jump" is refused (`held: …`) while its dry-run fix plan is unchanged (`retry_guard`); a changed
  plan lifts the hold. One repository had been retried and refused six times in three days.
- **Batch preflight.** Before a granted op's first request in a pass, and after any refusal, the executor
  checks the tools container and that a valid grant with uses left allows the op; when not, the op's
  remaining requests are not attempted (`not attempted: …`, one ledger row). A missing tools self-test had
  refused the same batch of nine repositories twice, minutes apart.
- The spool server (`serve_requests`, `quarantine_pending`, request reading) moved to
  `vulnscan/executor_serve.py` (CH-07; `executor.py` was at 973 of 1000 lines) and is re-exported from
  `executor`. The remediation agent's `daily_remediation` fence (v3) explains `not attempted:` and `held:`.
- **No busy-polling.** On 2026-10-27 the agent polled one pending request 69 times in two minutes until the
  10M-token run cap refused the next call. `request_status` now answers a pending request with an instruction
  not to poll, and refuses after three pending answers for one id in a run. The remediation agent's workflow
  says to queue everything and check each request once near the end.
- **Staged fixes get `open-pr`.** The host writes `staged-fixes.json` into the run directory
  (`executor_serve.write_staged_index`; the agent container mounts no spool): `get_fix_plans` lists those
  repositories first, and `request` refuses a second `stage-fix` for one.
- **New run state `cap-reached`.** A run that ends after the gateway's run budget or token cap refused a call
  is no longer a generic `agent-error`; its reason quotes the cap refusal.
- Docs: `session_id` is recorded as the one deliberate non-OpenRouter strip on the chat route.

### vulnscan (repository aliases; grants checked before git runs)

- **The remediation agent names repositories by alias.** The gateway's DLP redacts home paths, so the
  repository paths in fix plans and findings reached the model mangled, and every `stage-fix` the agent
  asked for named a guessed path that the executor refused. The MCP server now shows each repository as
  `<name>-<8 hex>` (the fix plan's file-name key) in plans, the summary and findings, and `request` and
  `escalate` map an alias named in the run's plans or findings back to the host path. `request` refuses an
  unknown alias; `escalate` passes it through (a stop condition is never refused). Executor results
  (`request_status`, `list_requests`) show aliases too. Two paths that share an alias are both left
  unmapped. An absolute path is still accepted. The fix plan key, the alias and the executor's state key
  now come from one function (`repo_alias.alias`); existing state-file names are unchanged. The
  `vuln-remediation` agent's daily steps (fence `daily_remediation` v5) give the alias as the target. The
  DLP is unchanged.
- **The executor checks grant coverage before any git runs in a requested path.** A `stage-fix` or `open-pr`
  target must be an existing directory inside an active grant's `repo_roots` (symlinks resolved) before the
  executor resolves the repository top or runs the protected-repository checks. A path no grant covers, such
  as `/root` or another user's home, is refused without a subprocess (`executor_guard.refuse_outside_grants`).
  The resolved path that was checked is the one used from then on. Refusal order for a covered path is
  unchanged. An escalation's target is canonicalised with git only inside a grant's `repo_roots`
  (`executor_guard.canonical_if_granted`); any other target blocks every target, as before for targets that
  do not canonicalise.

### security (S-10 release cooldown deprecated)

- **The 14-day package-release cooldown is deprecated (operator ruling, 2026-10-26).** A release that passes
  the known-vulnerability search may be adopted the day it is published, in every ecosystem; no cooldown
  exception or exemption is needed. The vulnerability search itself (Rule S-10) still gates every install.
  - vulnscan: the executor's 14-day floor is gone (`executor.RELEASE_COOLDOWN_DAYS = 0`). The daily
    config's `cooldown_days` and `fix --cooldown-days` are accepted but ignored, with a deprecation note,
    because existing configs carry an explicit `14`. npm runs without `--before`.
  - Server kit: `L3-node-runtime.sh` and `L5-containers.sh` default to no cooldown; `--cooldown-days N`
    still opts into one.
  - Generated `@security` agents: Rule S-10 and the S-VM deadlines no longer mention a waiting period;
    the escalation table's cooldown row now covers only an unvetted install. The security feed's
    prevention playbook says the same.
- **Trade-off:** the cooldown let registries catch and unpublish malicious releases before adoption.
  Without it, defence rests on the OSV search (including `MAL-*` entries), artifact identity (Rule S-9)
  and lockfile pinning.

### sync (sandbox artifacts for teams with a root brief)

- **Pinned sync re-renders `sandbox/` for teams whose brief is at the repository root.** `multi_sync` read the
  privilege posture only from `.agentteams/brief.json`. A team with only a root `brief.json` or the
  `.github/agents/_build-description.json` stub therefore got no boundary projection on sync, and its
  `confine-run.sh`, `mac-escape-tests.sh` and `sandbox.sb` stayed at the version that first generated them. The
  sync now finds the brief through fleet discovery (`fleet._resolve_descriptor`). A brief other than
  `.agentteams/brief.json` counts only when it sets `privilege_profile` itself, so a stub or foreign `brief.json`
  that merely inherits the confined default starts no sandbox rollout. A symlinked brief is ignored. A team with
  a regular (not symlinked) `.agentteams/brief.json` is unaffected.
- **Projection never writes outside the project.** A privilege artifact whose path resolves outside the
  repository (a linked agents directory, a `../` path) is skipped with a note.

### vulnscan (remediation pipeline: wire self-test, readiness, handoff gate, git-lfs, server plans)

- **goose's self-test proves the gateway accepts its requests (A3).** The probe runs goose headless against a
  loopback chat stub, with the command a run uses, and passes every request it sent through the gateway's own
  policy (`gateway_dialects.check_request`). Two new checks: `goose_headless_ran` and `goose_wire_compatible`. A
  goose update the gateway would refuse (as goose 1.54 was on 2026-10-24) now fails the self-test and keeps agent
  mode off, instead of every live call failing. goose's `flags_sha256` changes, so re-run `agent-selftest` once.
- **The daily summary says what blocks remediation (B1, B2, B4).** A new `## Remediation readiness` section, also
  in `summary.json` as `remediation`, lists:
  - the agentteams version;
  - an unbound tools container;
  - open escalations, with their scope;
  - an unreadable executor ledger;
  - grants expiring within 7 days;
  - configured servers no grant names.

  Two new fixed-text notifications, `remediation-blocked` and `grant-expiring`, are sent once per run. Plan-only use
  (no grant, agent mode off) is never reported as blocked. The heartbeat records `agentteams_version` and
  `remediation_blocked`.
- **Schedule drift is reported (B3).** `schedule.drift(cfg)` compares the installed job's time with
  `vulnscan.json`'s `schedule`. `vulnscan schedule status` and the readiness section print it as a warning.
- **Handoff delivery (phase H).**
  - `vulnerability_scanning.delivery: handoff` in a brief, or `"handoff_required": true` in `vulnscan.json`, makes
    the executor refuse `open-pr` until the repository's own orchestrator has approved the staged fix
    (`approve-staged` in `.vulnscan-handoff/verdicts.csv`, in the checkout or a linked worktree under
    `<repo>.worktrees/`).
  - Verdicts match by the fix's content digest, so a fix re-staged on a later day keeps its approval. The newest
    verdict wins. A verdicts file git tracks, a symlink, a FIFO or an oversized or malformed file is ignored.
  - New module `vulnscan/handoff.py`, new command `vulnscan handoff status [--json]`, and a summary section.
  - `project-description.schema.json` gains the `handoff` delivery value.
- **git-lfs repositories can be fixed (C2).** `fix.git_config_problem` refused every filter driver. It now allows
  exactly git-lfs's standard filter config (`fix.LFS_FILTER`, value for value). The allowance needs git-lfs to be
  available and none of the refused git-lfs settings: extensions, custom transfer agents and `lfs.storage`. The
  executor's git sets `GIT_LFS_SKIP_SMUDGE=1`, never runs a pager, and reads blobs with `--no-textconv`. `stage-fix`
  refuses to change any file that has a `filter` attribute. The tools image installs
  `git-lfs`, and `tools_present` checks it, so rebuild the tools image and re-run `tools-selftest`.
- **Per-host server fix plans (D1).** Each daily run writes `<state>/server-plans/<date>/server-fixplan-<host>.md`
  (outside the run directory the agent mounts), sorting each finding to
  the kit level that fixes it:
  - L2 OS security updates;
  - L3 runtimes;
  - L4 per `apps.conf` entry, with candidate `--pin` values;
  - Python packages no `apps.conf` entry covers;
  - npm trees (fixed in the app's repository);
  - host checks;
  - the dry-run and `--apply` commands.

  It changes nothing on the hosts.
- `handoff.py`, `remediation_status.py` and `server_fixplan.py` are integrity-pinned, and the manifest is
  re-recorded.

### vulnscan (daily agent: goose 1.54 wire compatibility)

- **The daily remediation agent reaches the model again.** Since the 2026-10-24 agent image (goose 1.54.0),
  every run made zero model calls. goose sends `session_id`, a session-grouping label whose value duplicates the
  allowed `user`, and the chat route refused it as an unknown key. The gateway now strips it with the other
  OpenRouter-only keys (`gateway_dialects.STRIP_KEYS`), so it is never forwarded.
- **goose's output ceiling is capped at the source.** goose 1.54 otherwise asks for the model's whole context as
  `max_tokens` (943718 for `z-ai/glm-5.3`), which the chat route refuses by design. The goose driver now sets
  `GOOSE_MAX_TOKENS` to `agent.max_output_tokens` (default 32000), never above the route's 64000 ceiling. The
  gateway's refusal of over-ceiling values is unchanged.
- **New run state `gateway-refused`.** A run with no model call and policy refusals in `spend.json` used to read
  as `agent-error`, quoting goose's "transient, please retry". It is now `gateway-refused`, and its reason lists
  the refusals and says the failure recurs on every run until the allowlist or the CLI pin changes.
- **`grant sign` refuses a grant past the aggregate cap.** When `grant.MAX_ACTIVE` (2) valid grants exist, it
  names them and asks for one to be revoked first. Previously the third grant made `load_active` refuse every
  grant, which silently stopped all remediation.

### docs (site restructure, phase 4: closeout)

- **Weekly external-link check.** `.github/workflows/docs-links.yml` runs `scripts/check_docs_site.py
  external` and fails only on definite breakage: HTTP 404/410, or a host that does not resolve. Rate
  limits and timeouts are reported as unverified. It uses the standard library, with no third-party
  action.
- **README:**
  - names all 7 frameworks, with Beta labels;
  - names the 11 governance agents its count refers to;
  - lists every framework's instructions file;
  - links the new Why agentteams and Contribute sections.
- **`references/docs-site-ux-rubric.reference.md`:** the researched UI/UX review rubric used for this
  restructure, published as a tracked reference. It covers information architecture, usability,
  WCAG 2.2 AA, contributor onboarding, Material for MkDocs and URL stability.
- **`references/documentation-refresh.procedure.md` and `references/filing-conventions.md`:** cover
  the API nav-group rule, the strict build and site checks, the removed-URL allowlist, and where new
  docs pages go.
- **Self-build brief:** `docs_src/why` and `docs_src/contribute` added to `memory_index_extra_dirs`,
  so the memory index covers the new sections.

### docs (site restructure, phase 3: Contribute)

- **New Contribute section** (`docs_src/contribute/`), with seven pages:
  - **Contribute to agentteams:** how contributions work today. Issues are welcome from everyone;
    pull requests are by invitation on an issue. It ranks ways to help from quickest to most involved
    and sets expectations: a first response within 7 days, best effort.
  - **Your first pull request:** a fork-to-PR tutorial.
  - **What your pull request needs:** the review checklist.
  - **Project tour.**
  - **Tests and CI:** every check on a pull request and how to read a failure.
  - **Working on the docs site:** the rules that keep URLs and the API reference intact.
  - **Code of conduct.**
- **Contributor Covenant 2.1** adopted as `.github/CODE_OF_CONDUCT.md`, identical to the official
  text apart from the enforcement contact. The site page includes it, so there is one copy.
- **GitHub community files:**
  - a short `.github/CONTRIBUTING.md` that points to the site;
  - issue forms for bugs, documentation problems, features, and template/adapter proposals;
  - blank issues are disabled; security reports and questions are routed to private reporting and
    Discussions;
  - a first-PR checklist at the top of the PR template.
- **`SECURITY.md` reporting channel fixed.** It said to email `maintainer@example.org`, a placeholder
  that reached nobody. It now points to GitHub private vulnerability reporting.
- **`contributing.md` becomes "Public API and template policy"** (its URL is unchanged). Its setup,
  test and pull-request sections duplicated the new Contribute pages and contradicted them in places,
  so they now live only in Contribute.
- **The page action is now "view source"** (`content.action.view`) instead of "edit this page". The edit
  flow forks the repository and offers to open a pull request, which the invitation rule asks people
  not to do.
- `scripts/check_docs_site.py` now enforces its page checks on `contribute/`.

### docs (site restructure, phase 2: Home and Why agentteams)

- **New "Why agentteams" section** (`docs_src/why/`), with four pages:
  - the problem and the constitutional approach, who the project is for, and what it is not;
  - the constitutional core in plain language, and how generated teams keep it in force;
  - why each of the four tiers of agents exists, with the adaptive plan-execution workflow
    described in words as well as the diagram;
  - further reading: the blog series, now listed on the site, plus the manuscript and the
    handbook editions.
- **Home is now a landing page:** the problem, the idea, a two-command example, routes for using,
  understanding, integrating, securing and contributing, and reasons to trust the project. The
  anchors of the old Home sections still resolve.
- **Home's framework detail moved to How it works.** The handoff and team-builder table now covers
  all 7 frameworks, and the three build paths are there too. Home's old table listed 5.
- **Dark mode** follows the reader's system setting, with a toggle. Text and links measure at least
  4.5:1 in both color schemes.
- **Link hover/focus color fixed.** Material's indigo accent (`#526cfe`) measured 4.26:1 on light
  and 3.78:1 on dark, below the WCAG AA minimum. `stylesheets/extra.css` replaces it with shades of
  the same indigo: 5.71:1 and 6.47:1.
- `scripts/check_docs_site.py` now enforces its page checks on `why/` as well as Home.
- Corrections found while reviewing these pages:
  - `copilot-cli` writes `.github/agents/*.agent.md`; `.github/copilot/` has not been emitted since
    2026-08-15. Fixed in `how-it-works.md` and `getting-started.md`.
  - The `claude` entry point is Claude Code, not "Claude Projects".
  - `agents-md` is an interop target. Only convert and bridge do not target it.
  - `getting-started.md` now lists all 7 frameworks' output folders.
  - The duplicated "Path B" subsection is merged into "Three build paths".
  - `how-it-works.md` headings are sentence case. Their anchors are unchanged.

### docs (site restructure, phase 1: navigation)

- The site nav is regrouped from 21 top-level entries into 7 tabs: Home, Guides, Why agentteams,
  Interoperability, Security, Reference and Contribute.
  - Guides is split into Get started, Run your team, Update and maintain, Cross-repo and bridges, and
    Framework notes.
  - The security guides and both handbooks get their own tab.
  - The ~120 API pages are grouped into the 9 sections of `api-reference/index.md`.
- **No URL changes.** No file moved, and the nav's page set is identical. The `docs-build` URL check
  confirms that every published file is still there.
- **Every page title is now unique.** 89 pages shared a browser-tab title, such as "Privileges",
  "Contents" or "Part I — What It Is & Why". Nav titles are now sentence case. Handbook parts put the
  topic first and their handbook and edition after it, e.g. "IV. The gates (security, developers)", so the
  topic survives tab truncation. Framework notes take a suffix, e.g. "Privileges (Goose)".
- `api-reference/index.md` headings are sentence case. Their anchors are unchanged.
- New guard `tests/test_docs_api_nav_groups.py`: the API nav groups must equal the table-bearing
  sections of `api-reference/index.md`, and every API page must sit in the group whose table lists it.
- Theme features added: section index pages, breadcrumbs, nav pruning, previous/next footer and an
  "Edit this page" button (replaced by a view-source button in phase 3). The Markdown extensions
  `attr_list` and `md_in_html` are enabled.
- Visible side effect: in the handbooks, sections whose first page is a `README.md` now open that page
  when clicked, instead of listing an "Overview"/"Contents" entry.

### docs (site restructure, phase 0: integrity baseline)

- The docs site now builds with `mkdocs build --strict`. The 4 warnings that made a strict build fail are
  fixed:
  - `cli-reference.md` linked to `api-reference/instruction-authority.md`, which never existed. The link now
    points at the shipped instruction-authority template.
  - `verification-environment.md` and `api-reference/framework-conformance.md` linked outside `docs_src/`.
    Those links now point at the files on GitHub.
- New `docs-build` job in `ci.yml` runs on every PR and push. Until now the site was built only after merge,
  in `docs.yml`. The job:
  - runs the strict build;
  - runs `scripts/check_docs_site.py`, which checks unique page titles, the skip link, image alt text,
    heading structure and link text. Pages outside the enforced set are held to a ratchet baseline.
  - checks that every site URL cited in README, the man page and `pyproject.toml` still resolves;
  - checks that every page URL of the base build still exists in the new build. Deliberate removals are
    listed in `.github/docs-removed-urls.txt`.
- The docs toolchain pins move to `.github/docs-requirements.txt`, which both workflows install from, so the
  PR check and the deploy build use the same versions.
- Corrected stale contributor docs:
  - `contributing.md` gave the template path as `templates/` and said templates are registered in
    `emit.py`; they live in `agentteams/templates/` and are registered in `analyze.py`. Its test map listed
    10 of 400+ test files and is replaced by how to run a subset and the docs guard tests. The install line
    now uses the `test` extra.
  - `template-authoring.md` had the same stale `templates/` paths.
  - `how-it-works.md` listed 5 of the 7 framework adapters, with inconsistent Beta labels. It now matches
    `STABILITY.md`.
  - `references/git-procedures.md` documented `mkdocs build --clean` into `docs/`. It now gives the strict
    `_site` build CI uses.
### Fixed

- **sandbox (macOS): gate 8 no longer scores a declined `rm` prompt as an escape.** `mac-escape-tests.sh` tried to
  delete the write-protected team marker with a plain `rm`; on a terminal BSD `rm` asks "override?", and a declined
  prompt exits 0, so the control-plane gate reported `ESCAPED: marker` although nothing was deleted (a false FAIL that
  blocked a confined-run verification). The confined `rm`/`mv` now run with `-f` and no stdin (a real denial still
  exits non-zero), and the verdict also requires the switch, verify-key store, marker and directory to be intact when
  checked from outside the sandbox; a reported success with every file intact is INDETERMINATE, not PASS.
- **vulnscan: prefixed requirements files are scanned.** A requirements file whose name has a prefix ending in `-`,
  `_` or `.` (`ci-requirements.txt`, `dev_requirements.txt`, `test.requirements.in`) was skipped silently, because the
  name test was anchored at `requirements`. Found when a `.github/ci-requirements.txt` pin with a Dependabot alert
  did not appear in the daily scan. `REQ_NAME` and `REQ_SCAN_NAME` take the prefix; a staged fix may now edit such a
  `.txt` file under the same pin-only check.

### Added (rc.5: Linux Docker Engine)

- **vulnscan: agent mode on Linux Docker Engine (R4).** New `vulnscan/docker_mode.py`: the container user
  follows the Docker mode that `docker info` reports, detected once when a self-test, a run or a debrief starts
  and passed on as a value (no subprocess while hashing or building argv). Docker Desktop (macOS or Linux): uid
  10001, as before (the same argv). Rootful Engine on a Linux host: the operator's
  `uid:gid`, so the gateway reads the 0600 key file and the DLP context and the agent writes the spool and its
  session state through plain bind mounts (an operator whose uid or primary group is root is refused). A container
  escape on a rootful Engine lands as the operator's own account, rootless Docker narrows that for the agent and
  gateway, but the tools container does not support rootless yet (its self-test fails, so confined operations are
  refused) and remediation needs a rootful Engine for now. The
  user is validated as decimal `uid:gid`, a record with any other `container_user` unlocks nothing, and the self-test
  records the identity the container reported (`observed_user`), which the CI job asserts. Rootless Docker: `0:0`, which the user namespace maps to the operator. Rootful with `userns-remap`:
  refused with an explanation (`checks.docker_mode_supported: false`). Anything unrecognised (an unreadable
  answer, a non-Desktop VM on macOS) keeps uid 10001, so a misread mode fails closed. The tmpfs `$HOME` and
  working directory are owned by the chosen user. The self-test record gains `docker_mode` and
  `container_user`; `flags_sha256` takes the user as an input; a run refuses (`not-run`) and a debrief refuses
  (exit 2) when the detected user differs from the one the self-test ran as ("the Docker mode changed since the
  last passing self-test — re-run agent-selftest"), or when the mode is refused. The debrief runs its container
  and gateway as that user (`debrief_argv(..., user=None)`, `debrief._start_gateway(..., user=None)`).
- **vulnscan: `non_root` depends on the Docker mode.** Under rootless Docker the probe checks "uid 0 inside,
  not root outside" (`/proc/self/uid_map` maps uid 0 to a non-zero uid); elsewhere it is unchanged
  (`selftest_probe.render(..., rootless)`, `NON_ROOT_ROOTLESS`).
- **CI: `.github/workflows/vulnscan-linux-containers.yml`** (ubuntu-latest, rootful Docker,
  `permissions: contents: read`): builds the tools image and the goose agent image with the repository's own
  scripts and runs `tools-selftest` and `agent-selftest` against a local stub upstream with a placeholder key,
  then asserts the self-test ran as the runner's own `uid:gid`. This job is the real Linux test.
- **vulnscan: goose's self-test runs `goose recipe validate` (R7, rc.5).** goose 1.54.0 has
  `goose recipe validate <path>` (exit 0 valid, 1 invalid). Probed offline, read-only, non-root: it rejects a
  missing required field, broken YAML and an unreadable file, but accepts unknown keys (it only parses), so the
  key allowlist and the extensions match stay the policy check. `GooseDriver.selftest_entries` adds the check
  `goose_recipe_valid` (`selftest_probe.goose_recipe_entry`) on `/work/run/agentdef/recipe.yaml`, the checked
  copy a run loads.
- **Re-run `agent-selftest` once after installing rc.5, every runtime and every Docker.** The probe now prints
  the identity the container ran as (`SELFTEST-IDENTITY`, enforced by the new check `identity_matches`), and the
  probe text is hashed, so every fingerprint changes; goose's also gains `goose_recipe_valid`, and on a Linux Engine
  the container user changes too.

### Added

- **vulnscan: the daily scan looks up repositories in parallel.** New config key `scan_workers` (default 4, 1–8;
  1 keeps the old one-after-another behaviour): the scan roots' OSV lookups, then the fix plans' registry lookups,
  run that many at a time, network-bound work that used to wait in series. Results are merged in root order on the
  calling thread, so `summary.json` and the fix plans are the same as a serial run's. Each worker runs in a copy of
  the caller's context (a `subproc_confine` scope is a ContextVar, so it applies as it would serially), and the
  a root or fix plan that fails is recorded on its own
  (`scan of <root> stopped: ...`, `fix plan for <repo> stopped: ...`; the run is partially unscanned) while the other
  roots are kept, where one failing root used to end the whole collection. `apply-worktree` staging, servers and local
  runtimes stay serial.
- **vulnscan: the remediation agent runs on the GitHub Copilot CLI and the Codex CLI (agent-runtimes plan,
  phase 4).** `agent.framework: "copilot"` (new `vulnscan/agent_drivers_copilot.py`): Copilot CLI 1.0.95 with
  bring-your-own-key through the gateway and `COPILOT_OFFLINE=true` (no GitHub sign-in or token); the chat route
  by default, the Messages route with the new `agent.copilot_provider_type: "anthropic"` (Claude models only);
  `--available-tools` = the vulnscan tools only, every built-in of the pinned release in `--excluded-tools`,
  `--disable-builtin-mcps`, `--no-custom-instructions`, the agent definition loaded as a custom agent
  (`--agent`/`--add-dir`; `agent.recipe` must be `.github/agents/<name>.agent.md`). No published checksum, so
  (revision 12) the image's `copilot-build` stage runs `npm ci --ignore-scripts` from a committed lockfile
  (`data/vulnscan_agent/copilot/`) and `npm audit signatures` on a digest-pinned Node image, and checks a hash
  over the whole installed tree. `agent.framework: "codex"` (new `vulnscan/agent_drivers_codex.py`): Codex
  0.162.1 (`codex exec --json --ephemeral --ignore-user-config --ignore-rules --strict-config -s read-only`), the
  whole configuration as `-c` overrides (a custom Responses provider at the gateway, the vulnscan MCP server
  `required`, every tool-adding feature disabled), `CODEX_HOME` the empty tmpfs home (Codex reads
  `$CODEX_HOME/AGENTS.override.md` even with project docs off: the canary caught it), OpenAI models only, new
  keys `agent.upstream_responses` / `agent.key_file_responses`. Codex keeps one built-in no switch removes,
  `apply_patch`: decision D4, it runs only with the new `agent.accept_residual_tools: ["apply_patch"]` and a
  self-test showing the read-only sandbox rejects its writes. Both refuse every `agent.command` override and have
  no debrief. Self-test (new `vulnscan/selftest_cli.py`): a headless run against the loopback stub with a canary
  planted; since neither CLI reports a tool inventory, the checks judge every tool offered in every request
  (`copilot_*`, `codex_*`, incl. `codex_home_fresh` and `codex_residual_write_blocked`); Codex's probe uses
  `agent.model`, whose catalog entry decides its tools, so the model is part of `flags_sha256`. Gateway:
  `--responses-reserve-tokens` (Codex sends no `max_output_tokens`; runs reserve `agent.max_output_tokens` per
  call instead of being refused), and the Responses route allows `client_metadata` (flat strings), `namespace`
  tools (of function/custom tools) and a client-run `tool_search`, and checks tool lists carried in `input`
  items. Every shape was observed with the pinned releases' macOS builds against the stub. The driver contract
  moved to `vulnscan/agent_driver_base.py` (re-exported by `agent_drivers`); `agent_drivers.driver_for` /
  `validate_settings`; `ImageRecipe.build_args`. `vulnscan-agent-image.sh` and `09-accept-framework.sh` take
  `FRAMEWORK=copilot|codex` (codex: sigstore check with `cosign`, Rule S-10 age check, the D4 prompt).
  Live acceptance with real keys is still to run.
- **vulnscan: severity from CVSS; unscored advisories count as HIGH (index reliability report R1).**
  New `vulnscan/cvss.py` (no dependency): CVSS v3.0/v3.1 base scores per the specification (each
  version's `Roundup`) and CVSS v4.0 by the official FIRST algorithm (macro vectors, look-up table,
  severity-distance interpolation; a port of the FIRST reference calculator, BSD-2-Clause), banded
  LOW / MODERATE / HIGH / CRITICAL. `osv.severity` is now the higher of the advisory's `CVSS_V3` /
  `CVSS_V4` vectors (top-level and `affected[].severity`) and its `database_specific.severity` label.
  An advisory with neither is **unscored**: it counts as the new config key `unscored_as` (default
  `"high"`; `--unscored-as` for `scan` / `inventory` / `runtimes`) instead of the silent MODERATE
  default (`engine.UNRATED_AS` is gone). `summary.md` marks such findings `UNSCORED (as HIGH)`, adds a
  banner and an *Unscored advisories* section; `review` counts them; `status --project` shows them as
  `UNSCORED`. New `Finding.rated` / `Finding.unscored`, `Report.unscored_as`, `Report.apply_unscored()`,
  `Report.unscored_findings()`; the MCP `list_findings` rows carry `rated` and `unscored`.
- **vulnscan: missed-run catch-up and a staleness alert (R3).** `daily --catch-up` runs the daily scan
  only when the last completed run is older than `stale_after_hours` (or there is none), after a new
  `run-stale` / `run-missing` desktop notification; otherwise it exits 0 silently. On macOS,
  `schedule install` adds a second launchd agent, `io.agentteams.vulnscan.catchup` (`RunAtLoad` plus
  every 6 h), so a run missed while the Mac was powered off is caught up at login (`schedule remove`
  removes both; `schedule status` reports it). `daily` and `schedule status` now print the staleness
  warning too. systemd already catches up (`Persistent=true`); cron is documented as not catching up.
- **vulnscan: say what we cannot see (R5).** Discovery now lists Go (`go.sum`, else `go.mod`), Maven
  (`pom.xml`), Gradle (`gradle.lockfile`, else `build.gradle*`), RubyGems (`Gemfile.lock`), NuGet
  (`packages.lock.json`, else `*.csproj`), pnpm and container images (`Dockerfile*`, `*.Dockerfile`,
  `Containerfile`, compose files), reported unscannable with the reason `unsupported ecosystem: <name>`
  (Cargo, conda and Lean now use the same wording). New coverage state **`unsupported ecosystem`** for a
  workspace whose only manifests are unsupported (it was `nothing to scan`); mixed workspaces keep their
  state and the coverage section lists their unsupported files. `status --project` reports
  `unsupported_manifests`. Rule S-VM (`security_rules_invariant` v=15) reports `unsupported ecosystem`
  as a coverage gap (CONDITIONAL); `nothing to scan` stays not a finding. Example snapshots
  regenerated.

- **vulnscan gateway: the host's DLP context reaches the containerized gateway (roadmap B2; hardening
  plan W2 residual).** New `vulnscan/gateway_context.py`; before every gateway start (run,
  `agent-selftest`, debrief) the launcher gathers, on the host: its host names (`gethostname()` full
  and short, macOS `scutil` ComputerName/LocalHostName, best effort, and the server-kit `hosts.conf`
  ssh targets' host parts, `-J` hops and `ssh -G`-resolved aliases; `hosts.conf` names themselves are
  never redacted, since the agent targets servers by them), the union of every enrolled project's
  `private_packages`, and `~/.config/agentteams-secrets/*.env`. Names go to `<gateway dir>/dlp/`
  (0700/0600, regenerated each start, symlinks refused) mounted read-only at `/run/dlp`; each secrets
  file is mounted read-only at `/run/secrets/dlp/<name>`. New gateway flag `--dlp-hostnames-file`; the
  argv carries only fixed in-container paths, never a host name, package name or secret.
  - **Intentional argv change:** the golden gateway argv gains the context mount and the
    `--dlp-hostnames-file` / `--dlp-private-names-file` (and per-file `--dlp-secrets-file`) flags.
    `flags_sha256` hashes them with fixed placeholder paths, so machine values never move it, but the
    fingerprint changes once: run `agent-selftest` again after upgrading.

- **vulnscan: the executor's git, npm and server-kit runs move into a hardened tools container
  (baseAgent hardening plan, W1; decision D1).** New `vulnscan/subproc_confine.py`,
  `vulnscan/subproc_confine_ssh.py`, `vulnscan/tools_image.py` and the image context
  `agentteams/data/vulnscan_tools/` (Dockerfile, `egress_proxy.py`, `selftest.py`,
  `vulnscan-with-token`, `git-credential-vulnscan`).
  - Every git command of a granted op (`stage-fix`, `open-pr` and the checks before them) and of
    `apply-worktree` staging, `npm audit fix`, and the server kit (`server-audit`, `server-dryrun`,
    the daily `servers` audits) run in a throwaway container: `--read-only`, `--cap-drop ALL`,
    `no-new-privileges`, your uid (never root), empty tmpfs `$HOME` and `/tmp`, and only the mounts
    the command needs (git dirs read-only for queries; the worktree writable only under a scratch
    root; never the home directory or `~/.ssh`, `~/.config/gh`, `~/.aws`, signing keys ...).
  - Network per operation: none, or an internal network whose only exit is a CONNECT proxy allowing
    exactly origin's host (git), `registry.npmjs.org:443` (npm) or the hosts.conf target's first hop
    (ssh, with a generated ssh config, the target's known_hosts lines and its identity files only).
  - Push credentials: `gh auth token` on the host, handed to the container on stdin and given by an
    in-image credential helper to origin's host only; never in an argv, a file or the container
    config. ssh origins are rewritten to https. `gh pr list` / `gh pr create` stay on the host.
  - New commands `tools-image build` and `tools-selftest`, `scripts/vulnscan-tools-image.sh`
    (Node.js 24.21.0 pinned by sha256 per architecture from its GPG-signed `SHASUMS256.txt`, base
    image by digest), and `scripts/vulnscan-operator/10-accept-tools-container.sh` (live acceptance:
    build, self-test, one confined stage-fix in the sandbox repository, one open-pr readiness check).
  - New config block `confine` (`subprocesses`, `image`, `ssh_identity`), validated at load.
- **Breaking (fail closed):** with `confine.subprocesses` unset (default true), executor ops are
  **refused** (`tools container unavailable: ...`), `apply-worktree` stages nothing and `servers`
  hosts are reported unscanned until the tools image is built and `tools-selftest` has passed for it.
  Nothing falls back to the host. `"confine": {"subprocesses": false}` restores the host behaviour as
  an explicit opt-out, announced on stderr and at the top of every `review`. The test suite turns the
  default off explicitly (`tests/conftest.py`) because it drives host git; the confined path has its
  own tests with Docker mocked.

- **vulnscan gateway: DLP on every model request, and a peer-credential Unix listener (baseAgent
  hardening plan, W2; decision D3).**
  - New `vulnscan/gateway_dlp.py`, always on, on the chat, Messages and Responses routes after the
    deny-by-default policy. **Blocks** (400, `refused`, reason `dlp: <class>`, counted in `spend.json`
    `dlp_blocks`; the matched text is never echoed or written) private keys, AWS, GitHub, GitLab,
    Anthropic, OpenRouter, OpenAI, Slack, Google, Stripe, npm and PyPI credentials, signed JWTs, bearer
    tokens, passwords in URLs, secret-named assignments of random-looking values, high-entropy
    `UPPER_SNAKE=` assignments, and by exact match the gateway's upstream keys and every secret value
    of its key files, `--dlp-secrets-file` files and `~/.config/agentteams-secrets/*.env` when present.
    **Redacts and flags** (`dlp_redactions`) home paths (to `~`, which the executor expands back),
    email addresses, private IPv4/IPv6 addresses, host names (`--dlp-hostname` plus its own) and
    private package names (`--dlp-private-names[-file]`, fnmatch) in message text only: tool-call
    arguments, `tool_use` inputs, ids and signed thinking/reasoning blocks are never changed. Digests,
    git SHAs, `sha256:`/`sha512-`/`h1:` pins, UUIDs, CVE/GHSA ids, versions and `data:` base64 media
    are not blocked; the regexes are linear and repeated history is not rescanned.
    `--dlp-responses` flags secrets in replies (`dlp_response_flags`) without altering them.
  - `vulnscan review` lists DLP blocks and reply flags of the latest run under Anomalies, with redaction
    counts beside them.
  - New `vulnscan/gateway_peer.py`: `--listen unix:PATH` binds a 0600 socket in an owner-only
    directory and admits only `--peer-uid` (default: its own), read from the kernel (`SO_PEERCRED` /
    `LOCAL_PEERCRED`), for host-side use. Runs and debriefs keep TCP on the container network.
  - Wired into runs, the self-test and debriefs by the host DLP context entry above (roadmap B2).

- **vulnscan: the remediation agent runs on Claude Code (agent-runtimes plan, phase 3).**
  - `agent.framework: "claude"`: a Claude Code 2.1.296 runtime driver. The image's `cli-claude` stage
    installs the native linux binary, sha256-pinned; `vulnscan-agent-image.sh FRAMEWORK=claude` takes the
    digest from the release's GPG-verified `manifest.json` (Anthropic's release key, fingerprint checked)
    and compares it with the pin. The run is `claude --bare -p` with `--tools ""`, `--strict-mcp-config`,
    the vulnscan MCP server inline in `--mcp-config`, `--allowedTools "mcp__vulnscan__*"`,
    `--permission-mode dontAsk` and the team's `.claude/agents/vuln-remediation.md` appended to the
    system prompt, from the empty working directory. The container holds only a dummy
    `ANTHROPIC_API_KEY`. `ANTHROPIC_BASE_URL` is the gateway's Messages route, every model alias and
    helper model is pinned to `agent.model`, and updates, telemetry, experimental betas, auto memory and
    tool search are off. `agent-chat` resumes the named session (`claude --bare --resume`), not yet
    exercised live. claude takes no `agent.command` override: the lockdown is part of the driver, and the
    self-test always runs the command a run would use.
  - Gateway wiring per driver dialect (`agent_upstreams`): new keys `upstream_messages` (default
    OpenRouter's `/api/v1`), `key_file_messages` (default `~/.config/agentteams-secrets/anthropic.env` for
    `api.anthropic.com`, else `key_file`), `models`, `prices`, `anthropic_betas`, `max_output_tokens`
    and `max_turns`. With `claude`, `agent.model` must be an Anthropic model, and `api.anthropic.com`
    needs `agent.prices`. goose's gateway command is unchanged.
  - The self-test gains `provider_apis_blocked` (Anthropic, OpenAI and xAI unreachable; an HTTP error
    status now counts as reachable for `internet_blocked` too). For claude it also runs Claude Code headless
    against a loopback stub with a canary planted (`.mcp.json`, `~/.claude.json`, settings hooks, an
    agent, a skill, `CLAUDE.md`, `AGENTS.md`). It fails unless the reported tools are only
    `mcp__vulnscan__*` (plus `EndConversation`), only the vulnscan MCP server is attached and nothing
    planted took effect (`claude_*` checks; the tool inventory is kept in the record's `detail`).
  - `scripts/vulnscan-operator/09-accept-framework.sh` (`FRAMEWORK=claude`): the operator's live
    acceptance run: image, self-test with the tool inventory, a one-day stage-fix grant, one supervised
    agent run, then the outcome, the spend and the gateway's refusal reasons.

- **researchteam integration fixes (review of 2026-10-23, items A2–A5).**
  - Scanner coverage: `requirements*.lock` and `requirements*.in` are read like `requirements*.txt`
    (scan only; a staged fix still edits only `requirements*.txt`). A `pyproject.toml` next to
    requirements files is no longer skipped: its PEP 621 `[project].dependencies` and
    `optional-dependencies` `==` pins are scanned, a pin already listed alongside is not counted twice,
    and loose entries no requirements file pins are reported unscannable. `Cargo.lock`, conda
    `environment.yml`/`.yaml` and Lean `lake-manifest.json` are reported unscannable with the reason
    ("Rust/crates.io: not supported", "conda: not supported", "Lean: not supported") instead of being
    skipped silently. Brief `exclude_paths`/`private_packages` and the operator's `exclude` apply as for
    every manifest.
  - New coverage state `nothing to scan`: a workspace with no dependency manifest at all (coverage section,
    `review` and `vulnscan status --project`, which now reports it ahead of `not enrolled`). Rule S-VM no
    longer treats it as a finding (`security_rules_invariant` v=13 → v=14).
  - `project-description.schema.json` declares the Layer-2 brief keys `layer2_profile`, `frozen_surfaces`
    (Rule 15 surface ids), `researchteam_notes`, `governance_agents`, `license` and `methodology_coverage`
    as typed optional keys, so researchteam-derived briefs and its scaffold validate strictly. Other
    unknown keys are still refused. Documented in `DESCRIPTION-FORMAT.md`.
  - The team builder (all frameworks) writes into the brief agentteams already resolves
    (`.agentteams/brief.json`, else a root `brief.json`), updates it in place keeping keys it did not ask
    about, and creates `.agentteams/brief.json` only when neither exists, so it no longer shadows a root
    `brief.json`.
  - Orchestrator `available_workflows` v=6 → v=7: rc.2 added the `vulnscan/*` dependency-fix PR review
    protocol to the fence without a version bump. There is no automated check that a fence body change
    bumps `v=` (the merge engine ignores it; see `FENCE-CONVENTIONS.md`).
- **vulnscan: enrol every project on the machine.**
  - `vulnscan enroll [DIR…] [--discover DIR] [--level L]` (operator only: a terminal, refused on CI)
    finds agentteams workspaces, groups clones and worktrees by normalised origin (`host/owner/name`,
    read from the repository config as text), asks once per owner, and writes the machine-local registry
    `~/.config/agentteams/projects.json` (mode 600). Levels come from each brief; setting one shows the
    brief diff first.
  - `daily` scans `repo_roots` plus each enrolled project's fix checkout at `scan` or `remediate`, minus
    the new `exclude` config key and duplicates. `vulnscan roots` shows each root's source.
  - The daily summary and `review` gain a Coverage section: fixable, scan-only, not covered by the grant
    yet, opted out, excluded, unanswered, duplicate checkout, missing on disk, not a git repository.
  - `vulnscan status --project DIR [--json]`: one project's level, last scan, open findings, recorded fix
    branches and PRs, and grant coverage; read-only, no network.
  - `grant sign --from-registry` proposes the fix checkout of every project enrolled at `remediate`
    (never `scan`, `off` or unanswered; never a clone of a protected repository). It shows only the
    change since the previous signed grant (additions and removals by owner, unchanged as a count) and
    asks y/N per owner for the additions; it needs a terminal and refuses on CI. The signing review is
    unchanged and its digest covers every root.
  - The executor refuses `stage-fix` and `open-pr` on a repository the registry enrols below `remediate`
    ("enrolled at `<level>`: scanning only"), and `get_fix_plans` heads those plans "scan only".
    Repositories not in the registry keep the grant as their only scope. An unreadable registry refuses
    repo operations. The registry only narrows what is scanned and proposed; it never authorizes a
    change (signed grants do), and the terminal check of `enroll` and `--from-registry` is not
    authentication.
  - Hardening from the security review. The protected-repository check also compares the push URL git
    itself would use (`remote.origin.pushurl`, `url.*.pushInsteadOf`; hardened `git remote get-url
    --push`). An enrolled checkout whose origin changed since enrolment, or an unregistered repository
    nested in one, is refused ("re-run enroll"). `registry.load` refuses a registry file that is a
    symlink, not yours or group/world-writable; the identity reader refuses a `.git` file, `commondir`
    or `config` that is not a regular file of at most 1 MiB, and the executor then refuses. `grant sign
    --from-registry` groups roots by the owner of their current origin and marks `ORIGIN CHANGED`.
  - `vulnscan status --project` adds `coverage_state` (the coverage vocabulary, plus `not enrolled`),
    `last_scan_partial`, `brief_level` and a `mismatch:` problem. `enroll` skips a project whose brief
    block is invalid and shows why, instead of treating it as unanswered.
- **Per-project vulnerability-scanning intent in the brief.**
  - New `vulnerability_scanning` block in `project-description.schema.json`: `level` (`off`, `scan`,
    `remediate`), `exclude_paths`, `private_packages`, `include_dev`, `delivery` (`pr` or `plan`) and
    `base_branch`. Unknown keys are refused. A privacy guard refuses host names, IP addresses, URLs,
    absolute paths, `..`, `~`, and catch-all `exclude_paths`/`private_packages` entries made only of
    wildcards and separators (`*`, `**`, `*/**`, `.*`). The `vulnerability_remediation` capability is
    unchanged: it still selects the ops team's vuln-remediation agent.
  - New module `agentteams.vulnscan.project_policy`: `read_policy`, `find_brief`, `is_private` and
    `set_level`. `read_policy` never raises; a missing, invalid or unreadable block counts as
    unanswered.
  - Operator choices win: a brief only narrows what leaves the machine, never what is checked. A
    repository the operator enrols at `scan`/`remediate` or lists in `repo_roots` is scanned even when
    its brief says `level: off` (`brief_off`; the Coverage section reports "brief says off; scanned by
    operator enrolment"). Only an enrolment at `off` skips it (`opted_out`). The operator's `exclude`
    paths go into the scan itself, so those manifests are never read or sent to OSV.
  - `exclude_paths` manifests are skipped (`excluded`). `private_packages` are never sent to OSV, PyPI
    or npm (PyPI names compared per PEP 503); the summary and the Coverage section only count them per
    repository (`withheld`), and `summary.json`, which the agent's MCP server reads, never names them. A
    lockfile that holds private packages is not given to `npm audit fix`. `include_dev` decides, per
    project, whether dev-only findings count (`Finding.count_dev`). The executor refuses `stage-fix` and
    `open-pr` when the brief's level is set below `remediate`, refuses `open-pr` for `delivery: plan`,
    and uses `base_branch` as the PR base (refusing when origin has no such branch).
  - `agentteams --set-vulnscan LEVEL` writes the level into the brief. It shows a diff and needs its
    own y/N on a terminal; `--yes` does not answer it. Without a terminal, it writes only with a
    matching `--set-vulnscan-confirm-sha256`.
  - `--update` and fleet runs print "vulnerability scanning: unanswered" for each project that has no
    level, and the fleet report counts them. Neither command writes machine state.
  - The team-builder intake asks for the level. `scan` is recommended, and the question explains what
    leaves the machine. Rule S-VM now reads `vulnscan status --project .` (`coverage_state`,
    `last_scan_partial`; `security_rules_invariant` bumped to v=13). The orchestrator gains a review
    protocol for `vulnscan/*` PRs.
- **`python -m agentteams.vulnscan`: known-vulnerability scanning and remediation for repositories and
  servers.**
  - It reads PyPI, npm and Packagist lockfiles and pins, and checks every exact version against OSV.dev,
    including malicious-package (`MAL-*`) records.
  - It judges Node.js, PHP and Python runtimes against live release data: security releases and
    end-of-life lines.
  - `fix` plans and makes the smallest clean upgrade. It honours the 14-day release cooldown and gates
    major jumps. npm runs lockfile-only with no install scripts.
  - `daily` and `schedule` run it every day through launchd, a systemd user timer or cron. Each run has a
    heartbeat, a new-findings diff and a notification.
  - The bundled server kit (`server-kit`) audits and patches Linux hosts over SSH. It is a dry run by
    default; app upgrades roll back automatically, and a host with compromise indicators is not patched
    in place.
  - Vulnerability knowledge is fetched live, never hardcoded. All user configuration and results live in
    XDG user directories, outside any repository; a test keeps operator data out of the shipped files.
  - See `docs_src/vulnerability-remediation-guide.md`.
- **Agent-driven, unattended remediation (opt-in).**
  - The new `vulnerability_remediation` capability adds a request-only `vuln-remediation` agent: a
    goose orchestrator sub-recipe whose grant is read and search only.
  - Agents run in a credential-free Linux container behind a key-injecting, spend-capped egress
    gateway. They queue requests through the `vulnscan` MCP server.
  - A host-side executor performs a request only under an operator Ed25519-signed **standing grant**,
    which is scoped, expires within 30 days, and is use-counted in a MAC-chained ledger. A HALT
    check runs before every use.
  - New commands: `vulnscan review` (after-action review and acknowledgement) and `agent-selftest`
    (isolation proof, a precondition for agent mode).
  - `@security` gains **Rule S-VM** (continuous vulnerability monitoring and standing grants;
    `security_rules_invariant` v=13, which includes the `status --project` rows above).
- **The vulnscan operator loop: escalations, notes, debriefs and reports.** See "When the agents
  stop" in `docs_src/vulnerability-remediation-guide.md`.
  - The agent's new MCP `escalate` tool records a ledger escalation that blocks remediation. The
    executor decides the scope: everything for `malicious`, `ioc` and `integrity`, otherwise the
    canonical target. More than 10 in one run become one global `escalation-flood`. New commands:
    `vulnscan halts` lists escalations and `@security` HALTs and prints the `HALT-RETRACTED` spec
    to sign. `vulnscan resolve` clears `other` after an interactive confirmation, and serious kinds
    only with an operator Ed25519 signature.
  - `vulnscan say` leaves operator notes. The launcher folds them into the next run's task and
    ledgers them by id and SHA-256. They never widen a grant or suppress an escalation.
    `say --from-gh` imports one `/vulnscan note` PR comment, and only on request.
  - `vulnscan agent-chat` resumes a past run's goose session for a read-only debrief: the spool is
    read-only and the debrief has its own gateway, budget, time cap and token cap. The new gateway
    option `--max-tokens-total` enforces the token cap.
  - The agent's new MCP `submit_report` tool hands its report to the executor, which stores it
    fenced under `<run>/agent-report/report.md`. With an operator-signed publish grant
    (`vulnscan publish-grant`), the executor pushes the report, review and summary without force to
    the `reports` branch of a preflighted private ops repository. `vulnscan publish` does the same
    by hand. `vulnscan-publish` is added to `GATE_ACTIONS`, so a HALT on it stops publishing.
  - `vulnscan review --gh-replies` lists replies on the agents' PRs as inert data; `say --from-gh`
    imports only your own `/vulnscan note` comment, after showing it and a terminal `y`. The
    published review fences all agent- and repository-derived text, and published files have home
    paths, this machine's hostname and private IPv4 addresses scrubbed. Content is scanned before a
    publish use is reserved; injection-pattern hits inside fences are allowed and counted.
  - From the first multi-repository run: fix plans are written per git repository
    (`fixplan-<name>-<sha8>.md`, headed by the exact `stage-fix` target) and list npm lockfile
    fixes (with `major-held`, `workspace-held` and `npm-unavailable` holds); each agent run keeps its
    own status, transcript and report (`agent/runs/<run id>/`), and a run that submits no report of
    its own is `agent-error`; the gateway counts connection failures as `upstream_errors`;
    `stage-fix` stages tracked files under ignored directories (`git add -u`, exact staged set),
    removes its own branch after a failed attempt, and disables repo-configured commit signing.
    A nested clone of another owner's project gets a plan with no target (update or remove it locally).
  - npm `stage-fix` runs npm again: its user and global config were the same empty file, which npm
    refuses ("double-loading config"), so every npm fix had found nothing. stage-fix now reports
    `npm failed: …` when npm cannot run, and a second scan the same day removes the first scan's plans.
  - The executor serves at most 200 spool requests per pass (more open an `escalation-flood`),
    sets aside requests left from a crashed run, and `serve-requests` by hand refuses while a run
    holds the run lock. Escalations on a linked worktree block its main repository.
  - **Behaviour change:** notifications carry fixed text only (ids, counts and state words). A
    `notify: "command:..."` program now receives the message as its last argument instead of a
    summary path.
  - **Behaviour change:** `open-pr` opens the pull request on `origin`'s own repository
    (`gh pr create --repo HOST/OWNER/REPO`, from `origin`'s URL); an `origin` not of that form fails
    the request before anything is pushed. Before, gh chose the base repository, and in a fork with an `upstream` remote it
    could pick the upstream project.
  - `agent-selftest` refuses without `agent.key_file`; `agent-image build` and a signed grant that
    does not verify end with a message and exit 1 or 2 instead of a traceback. The review's actions
    table keeps one row per action, `halts` always reports its HALT section, and "Findings not acted
    on" matches `~`-style repository roots.
- **Docs: Automated Remediation: Step-by-Step Setup** (`docs_src/vulnscan-automation-walkthrough.md`)
  with terminal screenshots generated from real runs (`scripts/gen_vulnscan_screenshots.py`), and the
  **`vulnscan` API reference** (`docs_src/api-reference/vulnscan.md`).
- **vulnscan hardening (remediation pass 2).**
  - `--confirm-review-sha256` now works for `grant sign`, `publish-grant sign` and `resolve`: the
    review digest covers what you chose (and the lifetime), not the id and times each run mints, and
    no longer depends on the working directory.
  - The executor refuses `stage-fix`/`open-pr` in the repository of the agentteams installation, of
    `agent.team_dir`, of every grant's governance directory and of the publish ops repository
    (compared by git common directory, so worktrees count).
  - `agent.team_dir` is validated before it is mounted (not home, an ancestor of home, or at, inside
    or above `~/.config/agentteams` or a credential directory); the self-test also checks that
    `.config/agentteams/keys` is unreachable, and its record is bound to the container flags
    (`flags_sha256`): **re-run `agent-selftest` once after upgrading.**
  - New gateway backstops: `agent.max_tokens_total` (default 10 000 000) and a refusal after
    `agent.max_unmetered_calls` (default 20) successful replies without usage, unless
    `agent.allow_unmetered`; a client that hangs up mid-stream is still metered, and upstream error
    replies are counted separately (`upstream_errors`).
  - `agent.team_dir` may not contain `agent.key_file` or the operator signing key (it is mounted into
    the container). The self-test's network probe catches only network errors, so a fault in it fails
    the test instead of reading as "internet blocked". The packaged server kit and the agent
    Dockerfile are integrity-pinned; a planted IOC terminator now stops `remote.sh` before ssh runs.
  - Publish preflight also refuses other collaborators, pending invitations and any deploy key, and
    fails closed on API errors. The fence rule that lets quoted injections through follows CommonMark
    (a longer closing run closes; an unclosed fence fences nothing), and a file with an HTML-block start
    or a non-CommonMark line break outside fences gets no exemption, closing three bypasses.
  - **Behaviour change:** the daily server audit runs the packaged server-kit scripts with your
    `hosts.conf`/`apps.conf`; the exported copies are no longer executed by the daily job. Extra
    `ioc-*.txt` files in the server-kit directory are embedded as data.
- **vulnscan hardening from the test campaign.**
  - Scanning: an OSV batch reply that is short or malformed makes the run unscanned (exit 3);
    OSV pagination is bounded; an advisory detail that fails in the parallel fetch is retried once,
    sequentially; a runtime whose version cannot be read is unscannable, never clean.
  - Fixes: npm runs on a scratch copy of `package.json` and the lockfile with empty user and global
    config, the public registry, `--strict-ssl=true` and `--ignore-scripts`, so a repository
    `.npmrc` is never read (npm workspaces fail closed). A malformed lockfile fails the request with
    a ledger row. A v1 lockfile's `resolved` URLs are checked against the registry allowlist.
  - `open-pr` is idempotent: a retry reuses an open same-repository PR whose branch holds the staged
    tree, or creates the PR without pushing when the remote ref is the staged commit; it refuses
    other content. After the PR the executor's worktree is removed and the branch kept.
  - Ledger: an incomplete final row names the recovery; appends fsync; a ledger key error refuses
    instead of crashing.
  - A SIGTERM during `agent-run` removes both containers, records `agent-error` and exits 143.
  - **Behaviour change:** the scheduled job no longer bakes in `SSH_AUTH_SOCK`. A launchd agent
    gets the login session's socket; under systemd or cron, set `IdentityAgent` in `~/.ssh/config`.
  - `schedule remove` leaves a user with no crontab without one, and reports a failed crontab write.
  - The gateway serialises upstream calls (at most one call can overshoot a cap) and validates
    `--budget-usd`, `--listen` and `--upstream`; a non-numeric `Content-Length` gets 400.
  - The MCP server bounds request lines, validates `params` and writes spool files atomically;
    `list_findings` sorts malicious first, then by severity, and returns `total` and `truncated`.
  - Each grant file must be a regular file you own, not group/world-writable and not a symlink. An
    unreadable `*.json` file in a grants directory makes repository operations refuse.
  - `~/.local/state/agentteams` is a protected write root with Claude `Edit(...)` deny rules (see
    Security). Every vulnscan module is pinned in the integrity manifest.
- **vulnscan hardening, test round 2.**
  - **Behaviour change:** `agent-selftest` needs `agent.team_dir`; it no longer falls back to a
    directory inside the state directory. `team_dir` is also refused at, inside or above the
    vulnscan state directory, and when it is not yours or is group/world-writable.
  - A grants directory, the verify-keys directory and the verify-key file are refused when they are
    a symlink, not a directory (or file), not yours, or group/world-writable.
  - `review` prints an overdue notice. `review --since` and `--run` (`publish`, `agent-chat`) must
    be `YYYY-MM-DD` (else exit 2). An unusable `vulnscan.json` exits 2 with the reason, now
    checked key by key, on every command that reads it. `scan`/`fix` on a missing path and
    `inventory` with a missing log exit 2.
  - `daily` takes `<state>/daily.lock`: a second concurrent run exits 3 with a message and writes
    nothing. The heartbeat is written atomically; a full disk exits 3 and still notifies; an
    unreadable earlier `summary.json` is skipped with a note. `fix_mode: apply-worktree` clamps the
    cooldown to at least 14 days, runs `validate_diff`, checks a reused worktree, and refuses a
    repository without `origin/HEAD`.
  - **Behaviour change:** the scheduled job bakes in only `PATH`, `HOME`, `XDG_CONFIG_HOME`,
    `XDG_STATE_HOME`, `AGENTTEAMS_VULNSCAN_CONFIG_DIR`, `AGENTTEAMS_VULNSCAN_STATE_DIR`,
    `AGENTTEAMS_SERVER_KIT_DIR` and `AGENTTEAMS_SERVER_KIT_LOGS` (`schedule.JOB_ENV_VARS`), so an
    API key in another `AGENTTEAMS_*` variable never lands in the plain-text job file. `--hour` and
    `--minute` are range-checked, `%` is escaped for cron, and `schedule remove` under systemd
    tolerates units that are not installed.
  - `stage-fix` (`executor.validate_diff`): a pin may change only its version, upward, to a final
    release; `--hash` files are never edited (`fix` holds them back as `hash-held`); renames,
    deletions, mode changes and symlinks are refused; an entry with a URL in `version` and no
    `resolved` is refused; workspace `link`/`file:` entries pass only in-repo and unchanged; a root
    `workspaces` change is refused.
  - `fix --apply` and `stage-fix` (`executor.stage_fix`, before any checkout) refuse a repository
    whose own git config defines a filter driver, or that git refuses to open (e.g.
    `safe.directory`: "git refuses to read this repository (...) — fix it by hand";
    `fix.git_config_problem`). git queries run hardened (`executor.query_git`).
  - **Behaviour change:** `run-host.sh` skips an `apps.conf` entry whose flags carry `--apply`,
    `--restart-services` or `--reboot` (split on any whitespace) and counts it as a failed step
    (exit 3): "apps.conf flags may not contain --apply, --restart-services or --reboot (found …)".
  - A spool request that is not a regular file (e.g. a FIFO) is opened with `O_NONBLOCK`, refused
    and set aside without blocking the serving loop (`executor._read_request`).
  - The schedule refuses control characters in a baked environment value
    (`schedule.job_environment`); `schedule install` reports a plan error and writes nothing.
  - `agent.debrief_max_tokens` must be an integer 1..100000000; bools, floats and strings raise
    `ValueError` (`debrief.debrief_max_tokens`).
  - A run refused for any reason while another run or debrief holds the run lock writes nothing
    (`agent_run.run_agent` returns `not-run` with `status_written: False`); every other not-run
    reason is decided under the lock.
  - **Behaviour change:** `run-host.sh` stops before any change when the first L1 audit exits 2
    (config, exit 2), 3 or prints no `RESULT` line (exit 3), or cannot reach the host (exit 1), and
    with exit 4 on compromise; otherwise it exits with the worst step result (0/1/3/4). Host names
    must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and `all` is reserved. `remote.sh` streams only
    `L1-audit.sh` to `L5-containers.sh`. L3/L4/L5 options without a value exit 2. IOC files are
    embedded with CR stripped, and a binary one exits 2. L1 reports MODERATE without apt/dnf, or
    with manifests but no `python3`.
  - Notifications: on macOS the text is passed as an AppleScript string, so non-ASCII shows
    correctly; `notify.send` never raises.
  - Gateway: `spend.json` is written atomically; `--max-tokens-total` below 0 exits 2.
    `debrief_limits` refuses NaN, infinity, booleans and strings. A SIGTERM during `agent-chat`
    removes its containers.
- **vulnscan fixes from the first live supervised runs** (goose 1.37.0, GLM 5.3 via OpenRouter; the third
  run staged and opened a correct dependency PR for $0.06).
  - goose could not create its log directory (the sessions bind mount makes Docker create `$HOME/.local`
    root-owned) and stopped before its first model call: `XDG_STATE_HOME` now points inside the writable
    sessions mount, so goose's logs also survive as `<run>/agent/goose-data/state/goose/logs`. The self-test
    checks it (`goose_dirs_writable`).
  - A run whose agent exits 0 without a single model call is `agent-error`, not `completed`.
  - **Behaviour change:** the default recipe is `.goose/recipes/vuln-remediation.yaml`; the generated
    orchestrator's prompt is a role check, so a one-shot run of it answered that and stopped.
  - **Security:** the scheduled job's `PATH` is no longer the installing shell's whole `PATH`, which put
    project virtualenv `bin` directories (agent-writable repositories) ahead of `git`/`gh` for the
    unsandboxed job. It holds only the directories the job's tools resolve to, plus the system
    directories, never one inside a git work tree under `$HOME` (`schedule.job_path`); the job runs
    Python with `-P`. Reinstall the job (`schedule install --apply`) to pick this up.
  - Local runtime findings use the target `this-machine` instead of the hostname (the agent took the
    hostname for a server); the task text limits server operations to configured server names.
  - Operator scripts sign from an empty temporary directory (a `pipx` install under `~/.local` made signing
    from `$HOME` refuse), and `03-configure.sh` takes the OpenRouter key from the checkout's `.env`.
- **vulnscan operator tooling and screenshots.**
  - Packaging: a wheel built from a working tree no longer ships `__pycache__` bytecode left by a test
    run (`[tool.setuptools.exclude-package-data]`; `MANIFEST.in` covered only the sdist). The shipped
    `.pyc` made the signing install-location check report a clean install as modified
    (`tests/test_packaging_no_bytecode.py`).
  - The walkthrough's signing and resolving commands use `python -P -m agentteams.vulnscan …`: the current
    directory stays off the import path (install-location criterion e).
  - The grant-sign and resolve screenshots are real captures from a clean install in a terminal
    (`scripts/gen_vulnscan_screenshots.py --installed`; `scripts/capture-signing-screens.sh`).
  - Operator scripts: `scripts/vulnscan-acceptance.sh` (read-only Part D checks, report written outside the
    repository) and `scripts/vulnscan-agent-image.sh` (goose download checked against the digest GitHub
    publishes, base image pinned by digest, `agent-image build`, `agent-selftest`).
  - `scripts/vulnscan-operator/` runs a first setup in order: `01-install.sh` (`pipx install` with
    `[signing]`), `02-sandbox-repo.sh` (a private sandbox repository), `03-configure.sh` (Steps 1, 4, 5,
    6), `05-first-run.sh` (a narrow `stage-fix` + `open-pr` grant on the sandbox, `daily`, one
    supervised `agent-run`, `review`), `06-sigterm-check.sh` (Part D #9), `07-schedule.sh` (the daily
    job) and `08-enable-agent.sh` (the unattended grant, `agent.enabled`, the daily job). There is no
    `04`: Step 7 is `scripts/vulnscan-agent-image.sh`. `03-configure.sh` takes the OpenRouter key from,
    in order, the `OPENROUTER_API_KEY` line of the agentteams checkout's `.env` (or
    `AGENTTEAMS_ENV_FILE`), `~/.config/goose/goose-backend.sh`, goose's keychain entry (skipped with
    `GOOSE_KEY=0`), then a hidden-input paste, and writes it to
    `~/.config/agentteams-secrets/openrouter.env` (mode 600). With two grants already valid,
    `08-enable-agent.sh` offers to revoke the one that expires first, and exits without signing if
    you decline. The walkthrough's Step 6 example `key_file` is now that path.
- **vulnscan documentation remediation.** Code fixes found while checking the docs against the code,
  and the docs that describe them.
  - Signing on Homebrew Python (`cli/signer_location.py`): the base interpreter (`sys.executable`,
    and `sys.prefix` outside a venv) may live in a git work tree when git ignores that path
    (Homebrew's `Cellar/`). The agentteams package and a venv's `sys.prefix` are still judged
    strictly, so a `pip install` into Homebrew's Python without a venv is still refused. The
    recommended install is `pipx install`, which works on Homebrew's Python.
  - `stage-fix` checks the repository before any fetch: `<repo>: git refuses to read this
    repository (<git stderr>) — fix it by hand` (ledgered `refused`, from `executor._repo_root`) and
    `<repo>: the repository's git config defines a filter driver, which git would run — fix it by
    hand` (ledgered `failed`). Before, a repository git refused to open was reported as not the top
    of a git repository, and the filter-driver check ran after the fetch.
  - `agent-chat` with an invalid debrief limit exits 2 with `debrief refused:
    agent.debrief_budget_usd must be …` (or the key at fault) instead of a traceback
    (`debrief.run_debrief`).
  - Every `agent.*` value is checked when the config loads (`agent_run.validate_agent`, from
    `daily._config_problem`), not only `agent.enabled`; a bad one exits 2 with `config <path> is
    unusable: …`. `team_dir`/`key_file`/`image` are strings (empty allowed);
    `model`/`framework`/`recipe` non-empty strings; `upstream` an http(s) URL with a host; `command` a
    non-empty list of strings; the numeric limits follow `run_limits`, `debrief_limits` and
    `debrief_max_tokens`; `allow_unmetered` is a bool.
  - Team generation names a failed security feed: `Warning: security intelligence: <feed> (<host>):
    truncated or malformed HTTP response (...)`, `response is not valid JSON, possibly truncated
    (...)` or `response is not a JSON object (got list)` (`security_refs.py`). A stale KEV snapshot
    still blocks (`[SEC-GATE/INTEL-FRESHNESS] blocked`).
  - `provision-operator-signing-key.sh` prints per-command usage
    (`AGENTTEAMS_DECISION_ED25519_KEYFILE=<path> <command>`) and "Do NOT export
    AGENTTEAMS_DECISION_ED25519_KEYFILE"; walkthrough Step 5 matches it.
  - `publish-grant sign` refuses empty or missing ids before anything else, exit 2
    (`operator_cli._grant_ids`): `refused: --owner-id is empty — run the gh lookup first; it must be
    your numeric GitHub user id`, the same for `--node-id` (the ops repository's node id),
    `is required` when a flag is omitted, and `refused: --owner-id must be your numeric GitHub user
    id (got '…')`.
  - Without Docker, `agent-selftest` exits 1 with `agent-selftest: docker is not reachable or the
    agent image <tag> is not built — start Docker (or run agent-image build) first` and
    `checks.docker_reachable: false`; `agent-image build` checks Docker before `pip wheel` (`agent
    image build failed: docker is not reachable — start Docker (or install it) first`); a docker
    failure part-way through the self-test prints `agent-selftest failed: <error>` (exit 1)
    (`agent_run.docker_unreachable`). Docker probes give up after 60 s (a hung client counts as not
    reachable); `selftest_ok` reports the same reason followed by `, then re-run agent-selftest`.
  - **Behaviour change:** signing refuses when a project `.claude/settings.json` exists but cannot be
    read or parsed, or its `allowWrite` is not a list (`<project>/.claude/settings.json cannot be read
    or parsed, so the agent-writable roots it grants are unknown`); it used to count as "no extra
    write roots". The Homebrew git-ignore lookup uses only the work tree's own ignore rules
    (inherited `GIT_*` variables and global/system git config are dropped) (`signer_location`).
  - Security-feed warnings are cleaned of control characters and terminal escapes and capped at 400
    characters; the static OWASP / LLM-reference / control-evidence tables moved to
    `agentteams/security_refs_data.py` (re-exported by `security_refs`).
  - Docs corrected: a `team_dir` at, inside or above `~/.config/agentteams/keys` is always refused
    (a protected path); a signing key kept elsewhere is checked only when
    `AGENTTEAMS_DECISION_ED25519_KEYFILE` is set (`agent_run.team_dir_problem`).
  - **Behaviour change:** `schema_version` in `scan --format json`, `summary.json` and
    `repos-*.json` is now the integer `1`, not the string `"1"` (`REPORT_SCHEMA_VERSION`). It
    increases on an incompatible change; consumers should check it.
  - Docs only: a "JSON output" section in the API reference (with `schema_version`); Python
    reference sections for `engine`, `report`, `osv`, `fix`, `ledger`, `schedule` and the gateway
    flags; prerequisites with pass conditions; goose sha256 and base-image digest instructions;
    `daily` exit codes; troubleshooting rows for each message above.
  - `scripts/gen_vulnscan_screenshots.py` captures `grant sign` confirmed by its review digest.

### Security

- **vulnscan: the PR #277 audit.** (1) A goose recipe or Copilot agent file containing a C0/C1 control, NEL
  (U+0085) or a Unicode line or paragraph separator (U+2028, U+2029) is refused when it is read: goose's YAML reader
  breaks lines there, so `title: t<U+2028>retry: ...` could hide a key from the allowlist (R2, R7). (2) A non-string
  OSV advisory `id` is ignored instead of crashing the daily run. (3) A list or other non-string `unscored_as` is a
  config error, not a traceback. (4) An absolute symlink inside a secrets directory is refused by name on the host
  (its host path does not exist inside the gateway); a relative link still works.
- **vulnscan rc.4: every operator re-runs `vulnscan agent-selftest` once after installing,** whatever the runtime.
  The R5 secrets mounts and the R6 masks below change the container flags that `flags_sha256` binds, so an rc.3
  self-test no longer vouches for an rc.4 run.
- **vulnscan: secrets the DLP check cannot use now fail closed (R5).** The gateway gets each configured secrets
  directory (`~/.config/agentteams-secrets`) as one read-only directory mount (`--mount type=bind,...,readonly`,
  CSV-quoted, at `/run/secrets/dlp/<n>`; new gateway flags `--dlp-secrets-mount` and `--dlp-skip-ok-file`), not
  one `-v` per file, so files named `.env` and paths with `,` or `:` are no longer skipped. A run ends `not-run`
  (a debrief exits 2 with `debrief not started: …`) when a configured directory exists but cannot be read, when it holds a `*.env` file the gateway
  cannot use (unreadable, not a regular file, a symlink out of the directory), or when the gateway itself counts
  an unreadable or non-UTF-8 file (it exits 2 and writes a count to `<gateway dir>/dlp-refused`). A missing
  directory is fine. New setting `agent.dlp_skip_ok` (default `[]`): `{"path", "sha256"}` entries, each
  acknowledging one exact version of one file; editing the file makes its entry stop matching; `review` lists
  them (paths on the terminal, a count in the published copy). Paths appear only on the operator's terminal;
  `dlp/notes` gains counts only.
- **vulnscan: the agent can no longer read the gateway's files (R6).** The agent, self-test and debrief
  containers mount an empty read-only tmpfs over `/work/run/gateway` and `/work/run/debrief`
  (`agent_run.MASKED_RUN_DIRS`), so the DLP host-name list, the acknowledgements and `spend.json` are hidden
  inside the read-only run mount. The mountpoints are created on the host before every `docker run`.
- **vulnscan: malformed OSV records no longer break the fix lookup (R8).** `osv.fix_for` walks a record without
  assuming any type (a malformed `affected`, package, `ranges`, range, `events`, event or non-string version is
  skipped) and never pairs a fix with a range start it could not read. A fix version must match
  `[A-Za-z0-9.+~:_-]{1,64}` (`osv.FIX_VERSION`); any other is never shown or offered: the finding notes "fix
  version not shown (unusual format)". New `Finding.malformed` / `Finding.fix_hidden` (from `osv.quirks`, passed
  by the engine), shown in the finding's notes and in a line at the top of the report; 0 in an older report.

- **vulnscan: the @security review of the rc.4 candidate, every finding fixed.** *Medium (blocked Copilot agent
  mode):* (1) Copilot reads its agent file's YAML front matter as configuration (`tools`, `model`,
  `mcp-servers` ...), and the self-test used the default file while a run loaded `agent.recipe`. Now a run refuses
  (`not-run`) and the self-test fails (`copilot_agent_file_clean`, the CLI not started) when the selected file is
  absent or its front matter cannot be parsed (a strict YAML subset); since R1 (below), other keys are removed
  from the copy instead of refusing the run; every driver's self-test runs with the selected file (`Driver.recipe` / `selected_recipe()`), and
  `flags_sha256` binds its path, plus its sha256 for copilot and goose. claude and codex read the file as prompt text only,
  so their fingerprint binds the path, not the content (a prompt edit does not invalidate the self-test); new
  module `vulnscan/agent_recipe.py`. The generated `vuln-remediation.agent.md` carries `tools`/`model`/`handoffs`
  (VS Code). Since the rc.4 residuals (R1, below), a run writes its own copy with only `name` and `description`
  and gives that to Copilot, so the generated file runs as it is. (2) Copilot's home and code cache were on the
  host-backed writable mount: the image now unpacks the CLI's package at build time into `/opt/copilot-cache`
  (root-owned, read-only at run time; verified with the linux-arm64 build as uid 10001 on a read-only rootfs),
  `COPILOT_HOME` is `/agenthome/.copilot` on the fresh tmpfs, and only logs (`--log-dir`) stay on the host mount;
  new checks `copilot_home_fresh` and `copilot_state_writable` (replacing `copilot_home_writable`). *Low:* (3) the
  Codex residual write test also targets the request spool and the state dir (and removes anything written);
  (4) a Responses call without `max_output_tokens` is forwarded with `max_output_tokens` = the reserve; (5) an
  unreadable DLP secrets file stops the gateway (exit 2, `gateway.DlpContextError`) with a hint about the
  gateway's uid 10001, and `gateway_context.prepare` explains on Linux that Docker Engine bind mounts keep the
  operator's uid and modes (the files are not made world-readable; rc.5 runs the containers as the operator on
  Linux Docker Engine, above); (6) a `hosts.conf` name that is an FQDN, an address or this machine's name (never redacted) is warned
  about at every gateway start and counted in `<gateway dir>/dlp/notes`, shown by `review` (a count, never the
  names); (7) a scalar `severity`, a non-object `database_specific` or a non-list `affected` in an OSV record is
  unscored, not a crash; (8) `daily --catch-up` checks the daily lock first and neither notifies nor runs while
  a daily run is in progress (`daily.run_in_progress`); (9) the S-VM CONDITIONAL PASS row names
  `unsupported_manifests` in an otherwise scanned project, as the rule does (`security_rules_invariant` v=16,
  example snapshots regenerated); (10) `vulnscan-agent-image.sh` prints a `NOTICE` on stderr when `cosign` is
  absent and only the codex sha256 pin was checked. Re-run `agent-selftest` once after upgrading, as the
  first bullet of this section says. New tests: `tests/test_vulnscan_security_rc4.py`.
- **vulnscan: Copilot and goose runs read a checked copy of the agent definition that vulnscan owns (rc.4
  residuals R1, R2, R3, R7, R9).** The copilot and goose fingerprints now bind the copy's sha256 (the claude and
  codex fingerprints change because of R5 and R6); the one self-test re-run above covers it.
  - **(R1, R3) Copilot no longer sees the team's `.github/agents`.** A run writes
    `<run>/agentdef/.github/agents/vuln-remediation.agent.md` from `agent.recipe` (mode 0444, alone in its
    directory): front matter `name: vuln-remediation` plus the original `description`, the body byte for byte. The
    CLI gets `--agent=vuln-remediation --add-dir=/work/run/agentdef` and is never given `/work/team`. Every other
    front-matter key (`tools`, `model`, `handoffs`, `user-invocable` ...) is removed, since each could only narrow
    the run; the removed keys are printed at run start, recorded in `status.json` under
    `agent_definition.removed_keys`, and listed by `review` under *Anomalies* (key names only). The generated
    `vuln-remediation.agent.md` therefore runs as it is. Still refused: no front matter, an unterminated or empty
    block, a missing or multi-line `description`, an unsafe file name. The self-test writes and runs the same copy;
    `copilot_canary_inert` also plants a same-name decoy agent in a team directory the CLI is not given, which must
    stay unseen, and `copilot_agent_file_clean` checks the copy. Probed with Copilot CLI 1.0.95: `--agent` matches
    the file stem or `name:`, and `--add-dir` loads no instruction, skill or other agent file into the prompt.
  - **(R2) The agent file is read strictly.** Line 1 must be exactly `---` and the block must end with exactly
    `---` (not `...`); an empty block is refused; a BOM is dropped and CRLF becomes LF before parsing. It must be
    a regular file (no symlink) under the resolved team directory (a symlinked parent is refused), at most
    256 KiB, and UTF-8.
  - **(R7) goose's recipe is copied** to `<run>/agentdef/recipe.yaml` and bound by its sha256. Allowed top-level
    keys: `version`, `title`, `description`, `instructions`, `prompt`, `parameters`, `activities` and
    `extensions`; any other (`settings`, `sub_recipes`, `retry`, `response` ...) is refused, and `extensions` must
    equal the block agentteams renders for the remediation recipe (`developer`, `summon`). A refused or missing
    recipe makes a run `not-run` and fails the self-test (`agent_definition_valid`). A goose `agent.command`
    override may not name `/work/team`, which would bypass the checked copy (`{recipe}` names it); copilot,
    claude and codex already refuse every override.
  - **(R9) Re-check after the copy.** After writing its copy, a run checks that the team's file still renders to
    it and that the self-test still vouches for it; a change in that window refuses the run (`not-run`). One gap remains:
    an edit made and reverted between the two re-checks. Once the copy exists, later edits to the team's file no
    longer change what the CLI reads.
  - `vulnscan/agent_recipe.py` gains `read_source`, `copilot_copy`, `goose_recipe_problem`, `make_copy`,
    `write_copy`, `in_container` and `AgentCopy`.
  - **Accepted, not new in rc.4:** goose's `developer` extension gives the agent a shell in its container (it can
    read `/work/team` and write request files to the spool directly), and `summon` can load the team's other
    recipes. The host executor checks every request on its own (schema, size, replay, request cap, then grant,
    HALT and protected repositories), so the MCP server is not the security boundary. A vulnscan-only goose
    recipe without `developer` is a follow-up. The goose `/work/team` command guard works on strings: `agent.command` is operator
    config and fingerprinted, so it catches mistakes, not attacks.
- **vulnscan: hostile version strings can no longer crash version ordering.** `versions.version_key` reads ASCII
  digits only and caps a digit run at 64 characters (`int()` refuses `²` and numbers over 4300 digits), so a
  malformed OSV `fixed` or `introduced` value sorts instead of raising. The goose `/work/team` command guard
  normalizes each path it finds (`/work/./team`, `//work/team`, `..`).
- **vulnscan: a repository's config can no longer make the executor's fetch, ls-remote or push run a
  program.** git keeps the first `remote.origin.uploadpack` / `receivepack` it reads (the repository's), so
  the executor now passes `--upload-pack=git-upload-pack` / `--receive-pack=git-receive-pack` explicitly. It
  also refuses any origin URL that is not https, ssh, scp-like or a local path (no `ext::` or other
  `<helper>::` remotes, which a per-protocol `protocol.<name>.allow` in repository config would otherwise
  allow), and allows only the https, ssh and file transports. Pre-existing; found by `@security` review.
- **vulnscan: protected repositories are matched by origin.** A separate clone of the agentteams
  checkout, `agent.team_dir`, a grant's `governance_dir` or a publish ops repository (same
  `host/owner/name` origin) is refused like a worktree of it; before, only the git common directory was
  compared. The checks moved into the new module `agentteams.vulnscan.executor_guard`.
- **vulnscan: PR bodies are scrubbed.** `open-pr` passes the body through the publish scrubber
  (`publish.rewrite_paths`): home paths, this host's name and private IPv4 addresses no longer reach the
  repository's pull request.
- **`--sign-mcp-direct-grant --confirm-review-sha256` could never confirm.** Each run minted a fresh
  `grant_id` and timestamps inside the digested record. The digest now covers the operator's choices
  plus the lifetime and the target project (`operator_signing.review_choices`), as `--sign-grant`
  already did; the display marks the minted fields. A digest is a standing approval of those choices,
  not a one-time token. `cli/mcp_grant_commands.py` and `vulnscan/grant.py` join `SIGNING_CLOSURE`
  and the integrity manifest.
- **The operator directory `~/.config/agentteams` is protected as a whole.** A sandbox write root may
  not be at, inside or above it (`frameworks/_write_roots.py`), and emitted Claude settings deny
  `Edit(~/.config/agentteams)` and `Edit(~/.config/agentteams/**)`. Before, only `keys/` was refused as a
  write root, and vulnscan's config (whose `notify: command:` the daily job runs), notes and server kit
  had no protection. The operator state directory `~/.local/state/agentteams` (vulnscan's ledger,
  spool and run lock; server-kit logs; doc-sync state) is protected the same way, with
  `Edit(~/.local/state/agentteams)` and `Edit(~/.local/state/agentteams/**)`.
- **macOS `~/Library/LaunchAgents` and `~/Library/Keychains` are protected write roots**
  (`frameworks/_write_roots.py`). launchd runs a LaunchAgents plist (such as the daily vulnscan job)
  unsandboxed, so emitted Claude settings also deny `Edit(~/Library/LaunchAgents)` and
  `Edit(~/Library/LaunchAgents/**)` (`frameworks/_sandbox_emit.py`).

### Developer tooling

- `integrity.stale_summary()`; the test session prints the stale pins and the re-record command once
  at start. `test_prompt_roots` assertions show the `--check` output. `test_api_doc_signatures`
  resolves a qualified heading (`grant.build(...)`) to that module in a package page.
- `svg_render.render_svg(..., direction="TB")`: an opt-in top-to-bottom layout; the default output is
  byte-identical.

### docs (site build: mkdocs-material 9.7.7)

- `.github/workflows/docs.yml` pins `mkdocs-material==9.7.7` (was 9.7.6) and `pymdown-extensions==12.1` (was
  unpinned). 9.7.6 carries GHSA-xvg9-69gf-fjrf (DOM XSS in search suggestions via a query parameter); the site
  enables `search.suggest`, and every `jlcatonjr.github.io/*` project site shares its origin. Both versions are
  past a 14-day cooldown with no OSV advisory (checked 2026-10-22).

### docs (sandbox operator handoff: verify outcomes, not exit codes)

- `references/sandbox-handoff.reference.md` §4 adds four checks learned from landing the handoff feature through
  its own protocol (PR #263):
  - A commit step compares what was committed with what it staged, because commit hooks can add files.
  - A publish step checks the pull request is mergeable, because a conflicting PR triggers no `pull_request` CI.
  - A script runs the project's pre-push checks before `git push`, including steps CI runs only on another
    operating system.
  - A step that acts on remote state converges on the desired state instead of racing a check-then-act. When a
    host auto-deleted the merged branch between `git ls-remote` and `git push --delete`, the cleanup should have
    treated that as success.
- §8's push row points at the mergeability check, and `gh run view --log-failed` covers failing logs the sandbox
  cannot fetch without credentials. Its branch-deletion row notes the remote branch may already be gone.

### feat (sandbox operator handoff: a sandboxed agent hands refused work to the operator as a script and a complete command)

- An agent inside an OS sandbox (Claude Code, Codex `workspace-write`, `confine-run.sh`) that hits a refused
  operation (a denied network host, `Operation not permitted` on a `.git` gitfile or outside its roots, an
  unreachable credential socket) now writes a reviewable `<slug>.handoff.sh` in the plan's week folder and gives the
  operator the **complete command** to run it in a terminal outside the sandbox, then reads the result back
  from `<slug>.handoff.log` and continues.
- New `agentteams/sandbox_handoff.py` (stdlib), `python -m agentteams.sandbox_handoff detect|write|status`:
  - The script carries a header (purpose, `blocked-by`, `touches`, destructive + clearance, plan), uses
    `set -euo pipefail` and an absolute `cd`, checks for required tools, echoes every command, prompts `y/N` by
    default (always when destructive), runs `verify` commands, and refuses to run while a sandbox marker is set.
  - The complete command re-checks the script's SHA-256 before running it, so an edit made after the operator
    reviewed it refuses to start.
  - `write` refuses a destructive spec without a recorded `@security` clearance (C-5), any trust root or
    capability grant (settings/hooks, rosters, `management-authority.json`, the integrity manifest, signing
    keys), credential-shaped text, and a script that fails `bash -n`.
  - `status` reads `ok` / `failed rc= step=` / `incomplete` / `not-run`, and `stale` when the log came from a
    different version of the script.
- New always-emitted `references/sandbox-handoff.reference.md`. It covers channel order (the intended in-grant
  path, then the proposal runner, whose refusal is a stop, then a handoff), the script and message contracts,
  governance, plan integration, resuming, and common handoffs.
- Orchestrator `available_workflows` v=5 → v=6:
  - A new **Sandbox Operator Handoff** sub-protocol that any workflow step can append. It is named rather than
    numbered, so it cannot collide with a project's own workflow numbers.
  - A sandbox augmentation table across Workflows 0/4/7/8/9/11 and git operations.
  - Workflow 0's capability-gap check gains a sandbox branch.
  - Plan documentation gains `operator` steps.
  - In Workflow 11, gates 7/7b run through a handoff when GitHub is unreachable; "unreachable from the sandbox" is
    no longer an "otherwise skip".
  - A new gate 7c reports pending handoffs. While work depends on a handoff, the session closes as *awaiting
    operator handoff*.
- `@git-operations` gains Invariant rule 10, a Sandbox Augmentation table and a `Sandbox handoff` output-contract
  line.
- `parallel_plan`: a step with `agent` = `operator` is always a singleton wave and is never a coordination
  candidate. The parallelization reference lists it among the carve-outs.
- The CI/CD gate's "run status is reachable" condition now counts reachability through a handoff:
  - The orchestrator's Workflow 11 step 7, `@git-operations` rule 7 and `github-workflows-merge.reference.md` are
    aligned on this.
  - The handoffs use the REST `actions/runs` query first and fall back to `gh run watch`.
  - Step 8's terminal-act ordering also admits a session that closes as awaiting operator handoff.
- Under `write_policy: "orchestrator-only"`:
  - Only `@orchestrator` writes handoff scripts.
  - "Not allowlisted" counts as a runner refusal, which is a stop and is never re-routed through a handoff.
- New API page `docs_src/api-reference/sandbox-handoff.md`.

### Changed

- **vulnscan gateway: the chat route is deny-by-default and reserved too (phase-2 parity).** After the
  OpenRouter rewrites, a chat request is refused when it carries a body key outside the allowlist,
  OpenRouter fallback `models` / `route`, `n`, a tool that is not a `function` (server tools named), a
  remote image URL, or a `max_tokens` above `--max-output-tokens`. Each call reserves its `max_tokens`
  (else `--chat-reserve-tokens`, default 8192) before it is relayed. The Messages routes accept Claude
  Code's `?beta=true` query (any other query is still 403), allow `output_config` (`effort`, `format`),
  and `spend.json` records the last 20 distinct refusal reasons (`refusal_reasons`).

## [1.0.1] - 2026-10-21

### release notes

agentteams 1.0.1 is a patch release on the 1.0 line: five fixes held until after v1.0.0, backported from `main`
by `cherry-pick -x`. It adds no dependencies and no new configuration. Decision logs, signed rows and generated teams
carry over unchanged.

- **Rule 15: unfreezing a frozen surface now needs the operator's Ed25519 signature.** An unfreeze is derived from
  any of the row's text columns, or from a write target inside a frozen surface. An author can no longer
  self-declare it away. researchteam 1.0.1 refuses every unfreeze until agentteams has this.
- **A long `run_command` returns its result instead of "queued"** (mathAgents M6-F5). The call waits up to 180 s
  for its own result.
- **The Claude hook's notice log is no longer an undeclared write** during a long confined command (M6-F7).
- **`--update --merge` draws the pipeline graph from the files it wrote**, so adopted agents appear in it.
- **Merge-adopted agents are listed as rostered, not as orphans to delete.**
- **Re-pins:** `references/enforcement-integrity.json` re-pins the changed enforcement modules. Re-render, then run
  `--verify-integrity`.
- **Install:** run from a verified, detached checkout of the signed `v1.0.1` tag, and pin its peeled commit (release
  key `SHA256:IPdnLwlF…`, as in `SECURITY.md`).

### fix (Rule 15: an unfreeze of a frozen surface derives constraint-relaxing; it is never self-declared)

- Before, a decision row that unfroze a surface was relaxing only when its author set `effect_relaxes`, which is
  the self-declaration Rule 15 forbids. researchteam 1.0.1 refuses every unfreeze until agentteams derives it.
- `cli/effect_classifier.py` now derives it on two axes:
  - **Text.** An unfreeze named in any of a row's text columns (`scope`, `action_reviewed` and `decision`, not
    only the first non-empty one) derives relaxing: `unfreeze`, `--unfreeze`, `unfrozen`, `un-freeze`,
    `un_freeze`, `thaw`. `management_directives.scope_is_allowed` refuses the same words, so no directive can
    authorise one.
  - **Target.** `derive_effect_class`, `classify_row`, `is_relaxing` and `requires_operator_signature` take an
    optional `frozen_paths`, the paths owned by a `frozen_surfaces` list (shared contract with researchteam,
    point 4). An `effect_targets` entry that is, is under, or contains one of them derives relaxing, whatever the
    action is called; `..` segments are resolved first.
- An unfreeze is in the operator-Ed25519 subset (`requires_operator_signature`), so an HMAC row can't authorise it.
- The frozen set a caller passes is the recorded freeze state before the row's change, never the working brief,
  which an agent can edit. A surface id dropped from that state is an unfreeze even when no row names it
  (`frozen_surfaces.unfrozen_ids`).
- The owned paths and the unfreeze vocabulary live in the new `cli/frozen_surfaces.py` (stdlib only; its ids are
  pinned equal to the framework registry by a test). No caller passes `frozen_paths` yet, because agentteams has
  no `frozen_surfaces` brief key; the text axis holds without it.
- `cli/effect_classifier.py`, `cli/management_directives.py`, `integrity.py` (which now lists
  `cli/frozen_surfaces.py`) and the new module change, and their pins in `references/enforcement-integrity.json`
  move; the operator's merge approval covers that.

### fix (runner MCP: a long `run_command` returns its result, not "queued")

- A granted agent's `run_command` waited only the server's call wait (20 s by default) and then returned
  `{request_id, status: queued}`. Grants are exact, so an agent without `request_status` could never read the result
  (mathAgents M6-F5: a 31 s `lake` build).
- `run_command` now waits for its own result for up to 180 s, which is the runner's 120 s MCP command cap plus time
  queued. It still ends early when the runner stops beating. Writes and deletes keep the short call wait.
- A `queued` reply now tells an agent without `request_status` to report the request id in its handoff. No grant
  changes: `request_status` is still granted only when the brief lists it.
- **Limits:**
  - The wait is not a guarantee. The runner serves one request at a time, so with several agents queued, or an
    uncapped orchestrator-channel command ahead, a result can still take longer than 180 s and come back `queued`.
  - The MCP client's own tool-call timeout must be longer than 180 s. Goose's runner extension sets 300 s; for
    Claude Code, check `MCP_TOOL_TIMEOUT`.
  - The server handles one call at a time, so while a `run_command` waits, that agent's other runner calls
    (`write_file`, `request_status`) wait behind it.
  - The server ignores cancellation, so a call the client abandons loses its output. The runner's ledger keeps the
    command's exit status and undeclared writes, not its output.
- The agent guidance (`write_policy.py`) now says this. `agentteams-runner-mcp.py`, its pin in `runner_mcp.py` and
  `write_policy.py` change, and the operator re-pins `references/enforcement-integrity.json`.

### fix (runner: the Claude hook's notice log is not a confined command's undeclared write, M6-F7)

- The generated Claude hook appends `.claude/hook-notices/<UTC date>.log` while a long confined command runs.
  `run_request` watches `.claude`, so such runs were reported ok:false with that file as an undeclared write.
- Now exempt, under the same rule as the runner's heartbeat (M6-F6), only when a sandbox ran. The exemption covers only
  `.claude/hook-notices/<date>.log` for the UTC day or days the command spanned, and only when the snapshots show it
  was appended to or created. That means the same inode and a size no smaller, still a regular file: never a
  truncation, an inode replacement, a deletion or a symlink. Every other `.claude` path stays watched,
  `.claude/settings.json` included, and nothing is exempt in an unconfined run. Rule 12: this exempts one log file
  from the escape backstop.
- `agentteams/proposal_run.py` changes. Its pin in `references/enforcement-integrity.json` moves too, and the
  operator's merge approval covers that.

### fix (`--update --merge` draws the pipeline graph from the files it wrote)

- The graph was built before the write, from freshly rendered agents laid over the disk copies. A merge keeps the
  on-disk front matter, such as an adopted agent's place in the orchestrator's `agents:` list, so the graph described
  files that were never written. baseAgent lost 13 adopted agents' edges and gained 6 that weren't in `agents:`, and
  a standalone `--refresh-graph` then flipped the graph back.
- After a successful merge-mode write, the graph is now redrawn from disk (`git_hooks.redraw_graph_after_merge`), the
  same way `--refresh-graph` and the commit hook build it. This happens before the build log is written.

### fix (orphan advisory: merge-adopted agents are not "orphans to delete")

- An agent adopted with `--update --merge --adopt-orphans` is recorded only in the orchestrator's `agents:` roster
  on disk. Each run rebuilds the manifest from the brief, so a later plain `--update` reported every such agent as an
  orphan to "review and delete manually" (found by baseAgent: 17 adopted agents). The noise could hide a real orphan.
- These files now print under their own heading, labelled as being in the orchestrator's roster (front matter,
  unverified). They are still recorded in the daily orphan-events log, under their own kind (`rostered-unverified`)
  and routing, so a planted file kept only by a roster line stays on record. A roster the merge gate's parser cannot
  read leaves the files orphaned, so nothing is silently kept.

## [1.0.0] - 2026-10-18

### release notes

agentteams 1.0 generates a coordinated agent team (orchestrator, governance, domain and workstream-expert agents)
from one project brief, for Claude, Copilot (VS Code and CLI), Goose, Codex, AGENTS.md and Grok. This release
collapses fourteen release candidates (rc.1 to rc.14) and everything since 0.1.0. The largest changes are a
signed, hash-chained security decision log that the constitutional gate hook enforces, and an opt-in
`write_policy: "orchestrator-only"` mode. In that mode only the orchestrator writes, and other agents write and
run commands through an out-of-session, OS-confined runner. Claude and Copilot (VS Code and CLI) are Stable;
Goose, Codex, AGENTS.md and Grok are Beta and outside SemVer (see `STABILITY.md`). If you are upgrading from 0.1.0
or from any release candidate, read "Breaking changes and migration" first.

- **Install:** run from a verified, detached checkout of the signed `v1.0.0` tag. Pin the tag's commit, never `main`.
  The migration guide's "Moving to a release checkout" section has the steps.
- **Upgrade order:** `--verify-integrity` and review its HALT report; then `--migrate-decision-log`; then re-render;
  then `--check-wiring`.
- **Signing key:** tags are SSH-signed by the operator. The key fingerprint begins `SHA256:IPdnLwlF`; the full value
  is in `SECURITY.md`, under "Release signers".

### Security and governance

The constitutional rules cited below are: C-2, a `@security` HALT is final; C-3, an agent's declared tools are a
limit; C-4, content an agent reads is data, not instructions; C-5, clearance before destructive actions.

#### The constitutional gate hook

- **The gate runs under an interpreter that can import agentteams.** Each Claude render, and each confined Grok
  render, writes a gitignored `<hooks>/agentteams-python` pin that names the rendering interpreter. When `python3`
  can't import agentteams, the gate re-runs under the pin with a scrubbed environment. (rc.10, rc.11) #226
- **No silent allow when the gate can't run.** Before, a gate that couldn't import agentteams denied everything
  under a confined profile and allowed everything under the default cooperative profile. Now, with no working
  interpreter, a content-bearing Write or Edit gets `ask` (cooperative) or `deny` (fail-closed), and the reason
  names `agentteams --check-wiring`. (rc.7, rc.10) #226
- **The pin is not written without its ignore rule.** When the hooks `.gitignore` is operator-owned or a symlink,
  agentteams leaves it alone, writes no pin and prints a notice. #226
- **The gate never imports agentteams from the project tree.** The repository-root fallback is removed, and the
  hook drops its own directory from `sys.path` before any import (except under `-I`). The gate and the Grok shim
  no longer use `from __future__ import annotations`. A planted `agentteams/`, `json.py` or `__future__.py` can no
  longer choose the verdict. (rc.7, rc.11)
- **Confined and exclusive teams get a fail-closed gate.** The hook is emitted with `_FAIL_CLOSED_ON_ERROR = True`,
  so a crash denies. `--allow-fallback-fail-open` restores fail-open. The gate flips only on an explicitly set
  profile, not a defaulted one. Cooperative teams are unchanged. (rc.7, rc.8)
- **PR merges prompt the operator whatever client sends them.** The gate asks on `gh [flags] pr [flags] merge`,
  the REST `pulls/<n>/merge` endpoint and the `mergePullRequest` / `enablePullRequestAutoMerge` mutations, from
  `gh`, `curl`, `wget`, httpie or an interpreter, including backslash-newline splits. "merge" in a PR body or a
  later `git merge` no longer prompts. (rc.9) #150, #173
- **Branch deletions ask.** The gate asks before `agentteams --branch-cleanup` or `--branch-post-merge … --apply`
  (see "Branch lifecycle").
- **Outbound sends ask.** Uploads, requests built from command output or naming credentials, raw sockets, remote
  copies, gists, new or repointed git remotes, pushes to a URL, DNS lookups built from output and interpreter POSTs
  get `ask`. Decoding is not gated. #211
- **Git config writes ask.** A write to any git config file is `ask`, after the content scan, so a credential in
  it is still denied. #211
- **A timed-out scan no longer allows the command.** Superlinear patterns are rewritten. Commands over 32,768
  characters, or with lines over 8,192, go to the operator unscanned. #211
- **Opt-in per-case gate decision log.** With `AGENTTEAMS_GATE_LOG` set to an absolute path outside the project
  and any work tree, the hook appends one hash-chained JSON line per call. `AGENTTEAMS_GATE_LOG_KEY` adds an input
  HMAC. Unset, the hook behaves as before. #217
- **The gate decision log holds no content.** It records the decision, reason id, scanner categories, what was
  checked and the hook's sha256, never written content, reason text or exception text. Relative, in-project,
  symlinked or non-regular log paths are refused, and a failed log write never changes the decision. #217

#### The decision log, clearances and HALTs

- **Decision signing is on by default.** The `enforce_decision_signing` brief field (default `true`), emitted in
  `references/agent-privilege.json`, makes the gate refuse unsigned authorizing rows (PASS, CONDITIONAL,
  HALT-RETRACTED). It turns on at `--update` with a notice naming the opt-out. An absent file counts as off; an
  unreadable one fails closed. (rc.7)
- **The signing switch follows the brief.** Every render and projection rewrites an owned switch file to match
  the brief. It never relaxes a signing-governed team, and prints `SIGNING SWITCH RELAXED` or `KEPT`. (rc.10) #208
- **A signature covers the whole decision.** Signed payloads cover `scope` and the `effect_*` columns, and a new
  optional `text_digest` column binds the `conditions` and `evidence` text. A row whose text was edited after
  signing stops clearing. `--sign-decision` binds the digest before signing, and refuses a constraint-relaxing
  row in a log with no `text_digest` column. A `|` in a signed axis is refused. (rc.9, rc.11)
- **An Ed25519-signed row is verified as Ed25519 for every authorizing verdict.** The gate dispatches on the
  row's signed `sig_scheme` and verifies against `references/authorized-verify-keys/<key_id>.pub.pem`, whatever
  `enforce_decision_signing` is. A failed check never falls back to HMAC. Operator Ed25519 retractions clear, and
  no HMAC secret is needed where an agent runs a render. (rc.11)
- **Signed logs chain.** `--sign-decision` fills `prev_digest`. Logs left with empty links by the old signer
  verify through a disclosed legacy prefix. `--verify-integrity` reports the prefix length and any HALT inside
  it, and exits 1 on a broken chain. #224
- **New signed logs keep the legacy columns first,** and the Goose coordination MCP server no longer rewrites the
  header and refuses a hash-chained log. #224
- **`--migrate-decision-log` upgrades a legacy log.** It appends the missing signing columns (never
  `prev_digest`) and an empty `text_digest`, pads short legacy rows, refuses torn rows, and checks that each signed
  payload is unchanged. It backs up first, writes atomically, is idempotent and needs `--yes`. (rc.10, rc.11)
  #212, #224
- **`--migrate-decision-log` reports what stops clearing.** `--dry-run` lists the unsigned PASS and retraction
  rows that will stop clearing. The report names each revoked retraction whose HALT blocks again
  (`"reactivates_halt": true` in `--json`). (rc.10, rc.11)
- **A HALT blocks every action named in its id.** A HALT matches when an action id appears as a whole token in
  its id, so `m6-overwrite-render-1a2fad6` blocks `overwrite`. Only an exact-id `HALT-RETRACTED` row clears a
  token-matched HALT. Clearing-side matching stays strict. #225
- **An unauthenticated retraction no longer wedges the HALT scan.** A `HALT-RETRACTED` row that fails
  authentication or has no evidence retracts nothing and is skipped. The HALT stays open until a valid
  retraction, and the error names each skipped row and why. (rc.12)
- **Constraint-relaxing exceptions are derived, elevated and bounded.** Whether a decision relaxes a constraint
  is derived from its effect, never self-declared. The HALT scan follows lineage. Relaxing exceptions need the
  operator's Ed25519 signature, trust-root targets are refused even when signed, and an exception registry
  enforces an aggregate cap and sunset. New: the `signing` extra, `--sign-decision`, `--audit-exceptions` and
  `--list-exceptions`. (rc.8) #46
- **A superseded clearance can't be replayed.** The newest matching decision row decides; if it is consumed, the
  gate refuses. A pre-recorded clearance yields one use. (rc.9)
- **Signing needs explicit confirmation.** `--sign-decision` and `--sign-grant` show the payload and a review
  sha256, then need `[y/N]` on a TTY or `--confirm-review-sha256 <digest>` without one, before the key is read.
  `--yes` never applies. Control, bidi and zero-width characters are refused. (rc.8) #9
- **Signing is refused from an untrusted install.** It refuses when the package, `sys.prefix` or `sys.executable`
  is inside the cwd, a git work tree or an allowWrite root. `--allow-checkout-signing` overrides, warns every time
  and is recorded in the signed row. The signing code lives in the pinned `cli/operator_signing.py`, and its
  closure is checked against the integrity manifest before the key is read. (rc.8) #10
- **`--sign-decision` checks its target.** It refuses an `--output` that is not a team directory and verifies each
  signature against the team's public key before appending. The key-provisioning helper needs `--team-dir`.
  (rc.8)
- **Governance logs are validated.** `--verify-integrity` detects malformed append-only CSV logs (field count,
  unclosed quote, swallowed rows) and exits non-zero. Appends are one locked, validated write. Security-gate and
  red-team ledger rewrites are atomic. (rc.7, rc.9) #159
- **Plain `--update` doesn't need a clearance; `--overwrite` does.** The destructive-overwrite gate fires only on
  `--overwrite`, which needs an `overwrite` clearance. `--revert-migration` needs none, so a failed `--migrate` can
  always be undone. `--from-migrate`, which let the command line skip the gate, is no longer a CLI flag. (rc.1)
- **Gate refusal codes name the gate.** Failures carry `[SEC-GATE/DESTRUCTIVE:<action>]` or
  `[SEC-GATE/INTEL-FRESHNESS]`. The stale-intel refusal states how many intel-bearing placeholders it affects.
  (rc.7)
- **Threat intel is never falsely fresh.** A vulnerability cache that is unreadable, not a JSON object,
  schema-invalid or missing `generated_at` reads `stale` (`freshness.cache_corrupt`). `--security-offline` and a
  failed fetch no longer blank cached CVEs, and invalid cache reads are rejected. (rc.7, rc.10) #224

#### Keys and grants

- **HMAC signing keys must be strong.** Signing and verifying refuse a key under 32 bytes, a known placeholder
  (`...`, `changeme`, `secret`, `password`, …) or one repeated character. This covers decision rows, waivers,
  grants, management directives and the runner's proposal-ledger key (file or environment). A weak key fails
  closed, with no bypass. (rc.12)
- **The signing key is unreadable in every sandbox.** Keys live in `~/.config/agentteams/keys/` (mode 700).
  Every sandbox read-denies it, `permissions.deny` emits `Read` rules for `keys/**` and `*.pem` (Claude's built-in
  tools ignore the sandbox), and the launcher masks `--exclude` files. Write roots inside `keys/` are refused. The
  provisioning script refuses symlinked or loose key directories. Legacy `~/.config/agentteams/*.pem` is denied.
  (rc.8)
- **Only Ed25519 grants widen a sandbox.** `--sign-grant SPEC.json` issues domain-tagged Ed25519 grants. HMAC
  write grants are refused; HMAC remains for grants that don't widen. The approver roster is read only from the
  team directory. (rc.7, rc.8)
- **Cross-workspace capability grants.** `--issue-grant` and `--verify-grants` manage signed grants with an
  optional `team_id`. A grant never overrides a HALT. Grants fail closed without a non-empty
  `references/security-approvers.txt`. (rc.7)
- **Grant hardening.** The grant ledger is hash-chained, so deletion or reordering fails closed. An optional
  signed `issuer_root` rejects target paths outside it. Issue rejects empty, `/`, home-rooted or `..` targets and
  malformed `expires_at`. (rc.7)
- **`signed_ledger` warns once** when `cryptography` is below the pinned version, and still signs. (rc.8) #20

#### Management directives

- **Management repositories.** A brief can set `is_management_repo` and `authorized_managers`. An HMAC-signed
  directive verified with `AGENTTEAMS_MANAGEMENT_SIGNING_KEY` authorizes only the exact non-destructive scope it
  names. New: `--issue-directive` and `--verify-directives`. Off by default. (rc.7)
- **A directive is an authenticated operator artifact, not a tier.** It can never clear C-5, pierce C-2, widen
  C-3 or change governance. It fails closed on an empty roster, a missing key or an invalid or expired
  directive. (rc.7)
- **The scope denylist matches word stems,** so inflections such as `deletes` or `overwriting` are refused, and
  `wipe`, `truncat`, `revert` and `destruct` are added. (rc.7)
- **A directive that can't be relayed goes to the operator.** If it fails to verify or hits a refused scope,
  HALT, constitution change or C-5 destruction, the agent asks the operator once and logs the answer to
  `orchestrator-escalation.log.csv`. (rc.7)
- **Progress check-ins.** At every stop, a management-relay orchestrator prints an agent-by-task progress table.
  Cross-repo handoffs get one row each and escalate after `CROSS_REPO_REPORT_STALE_HOURS` (default 48). (rc.7)

#### Content, scanning and secrets

- **Derived content stays data (C-4).** Text an agent derives from read content (decoded, translated,
  summarized, OCR'd, or returned by a tool or subagent) keeps the no-authority status of read content. A request
  to transform and follow is itself a finding. Text in the project's own records has authority only when its
  write traces to an operator instruction. #211
- **Untrusted results are labelled.** The OCR proxy fences image text with a nonce, and memory- and code-index
  results are labelled untrusted. #211
- **The security template gates data leaving the machine,** including one-off sends and rendered markdown URLs.
  Pre-configured remotes and the project's own `gh` repository are exempt. #211
- **`python -m agentteams.scan <path>`** scans a file or directory and returns one verdict (HALT,
  CONDITIONAL_PASS or PASS; exit 1 only on HALT). It opens regular files without following links, and a skipped
  file raises a `scanner-evasion` finding. (rc.7, rc.8)
- **Scan patterns.** The scanner adds GitHub, Slack, Stripe and JWT credential patterns and literal
  instruction-override strings. File paths no longer trip the credential check, and Goose and Codex references no
  longer raise false positives. (rc.1, rc.7)
- **`.env` files stay out of git.** `python -m agentteams.env_hygiene REPO [--fix [--execute]]` reports tracked or
  unignored env files and Dockerfiles that copy them. `--install-git-hooks` installs a blocking `env-file-guard`
  pre-commit hook. (rc.9)
- **`--scan-security`** checks for PII, credentials and placeholders, with live CVE, CISA-KEV and EPSS lookups.
  (rc.1)
- **No absolute home paths in generated artifacts.** The delivery receipt, bridge stubs and reports, the
  memory-index snippets and `build-log.json` record relative paths only. (rc.7, rc.9)

#### Integrity, trust roots and the control plane

- **`--verify-integrity` checks the enforcement manifest** and exits 1 on drift. A missing manifest is a finding
  where the repository tracks one, and the confined gate then answers `ask`. A re-record keeps absent
  installed-hook pins. The grant and rank emitters and the gate hooks are pinned. (rc.7, rc.8, rc.10) #221
- **In the agentteams tree, `--check` fails on enforcement drift,** and `--write-integrity-manifest` exists.
  (rc.7)
- **`--pin-templates`** records template digests in `.agentteams/template-pins.json` (trust on first use). A
  mismatch is reported, not blocked, and an ambiguous pin location is refused. (rc.7)
- **The control plane is write-protected.** Claude `denyWrite` covers all of `.claude` (a read-only mount, so a
  rename fails) and `.codex`, `.github/agents` and `.goose` when those teams exist. (rc.7, rc.8)
- **`permissions.deny` covers trust roots:** `.claude/settings*.json`, `.claude/hooks/**`, the signing switch,
  the verify-key store, operator rosters and `management-authority.json`. Copilot and Codex teams get roster
  stubs. (rc.7, rc.8)
- **Stale protections are named.** Advisories name `denyWrite` entries that no longer exist and sibling teams the
  live settings miss. (rc.8)
- **Opt-in prompt-root protection.** `protect_prompt_roots: true` adds `Edit` deny rules for the prompt roots
  (instruction files, agent directories, `AGENTS.md`, `CLAUDE.md`, `.mcp.json` and others). `AGENTS.override.md`
  is a prompt root. (rc.8, rc.9) #8
- **Prompt-root changes are detected.** Every render records `prompt_root_hashes`. `--update` and `--check` warn
  `PROMPT-ROOT CHANGED since last build`, and `--strict-prompt-roots` makes `--check` exit 1. (rc.8) #8
- **Planted files are named.** The launcher, generation and `--check` warn about a marker-only
  `references/build-log.json` and about a `.codex/config.toml` that sets approval, sandbox, notify or MCP keys.
  Generation and `--check` warn, and `--check-wiring` fails, when `.claude/settings.json` uses
  `sandbox.excludedCommands`. (rc.8, rc.9)
- **Backups can't be loaded as agents.** Backed-up `.md`, `.yaml`, `.yml` and `.toml` files and lost-fence
  sidecars are stored as `*.agentteams-bak`; older backups are renamed in place. A restore never revives a
  sidecar. (rc.8, rc.10) #207, #224

#### Security and governance templates

- **`@security` vets dependencies before install.** It searches advisory sources and applies a 14-day release
  cooldown to every install or update in every ecosystem. Distro security channels are exempt. (rc.7)
- **More `@security` coverage:** package-install and sudo triggers, metered-endpoint disclosure, low-level
  vulnerability screening, OS hardening references, an infrastructure-layer reference, AI-code insecure defaults
  and `.env` rules. `@git-operations` never commits a `.env` file. (rc.7, rc.9)
- **Vulnerability notes in remediation plans.** `remediation.plan.md` finding rows get advisory notes from the
  cached KEV and OSV threat intel, with no new network call. (rc.7)
- **Capability keys are never applied silently.** A change to `tools`, `model`, `agents`, `hooks`, `mcpServers`,
  `permissionMode`, `skills` or `memory` is reported as a proposal with the exact line to apply. (rc.7, rc.9)
- **Tool grants are narrower.** Claude `allowed-tools` follows `tools:`, so read-only agents get no Bash, Write or
  Edit. `conflict-auditor` is read and search only. `--post-audit` warns when a non-orchestrator agent holds both
  a write tool and dispatch (`AR_WRITER_DISPATCH`). (rc.3, rc.7, rc.9)
- **Orchestrator spawn authority and query funnel.** A delegate routes user questions up to its own orchestrator,
  and an orchestrator can spawn a scoped child orchestrator. (rc.7)
- **Bridge-refresh Pre-Flight checks** are canonical in `bridge-refresh-safety.md` and bind the orchestrator,
  `@git-operations`, `@security` and `@cleanup`. (rc.7)
- **Red-team audits.** `agentteams --redteam` runs a standing red-team audit with a self-audit phase.
  `--redteam-freshness-check` (operator-only, needs `agentteams[research]`) searches for new AI-attack techniques
  and writes candidates to `references/redteam-freshness-candidates.md`. (rc.7)

### Orchestrator-only writes and the runner

#### The `write_policy` switch

- **`write_policy: "orchestrator-only"` (opt-in).** Every non-orchestrator agent is narrowed to read, search and
  todo and told "Return Proposals, Never Write". The orchestrator keeps its tools and gains an "Applying
  Proposals" workflow. The team ships `references/write-policy.reference.md`. With the field absent or
  `"default"`, output is byte-identical. (rc.9)
- **Where the switch applies.** It needs an explicit `privilege_profile` of `confined` or `exclusive`.
  `write_policy_frameworks` limits it when one brief emits several frameworks. (rc.9)
- **Frameworks under the switch.** Claude is enforced. Codex is allowed on Linux and macOS and holds only when
  launched through the launcher (see "Codex under the launcher"). Goose is refused
  (`AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH=1` is for testing only), as are Copilot and agents-md. (rc.9, rc.10)
  #204
- **The top-level session gets the dispatch contract.** `CLAUDE.md` gets "Write Policy: You Are the
  Orchestrator", covering nonces, the staged approval loop and `--verify-proposal-ledger`. Granted agents are told
  a write receipt is final. #204
- **Existing teams adopt the switch** with `--update --merge` and `--reconcile-front-matter --reconcile-apply`,
  which narrow markdown agents and add missing capability keys. Only Codex TOML needs a fresh build, or
  `--overwrite` with a recorded clearance. (rc.9, rc.10)

#### Proposals, dispatch nonces and the ledger

- **Revoking an MCP grant binds live dispatches.** Once a grant is removed and the runner restarted, the agent's
  unexpired nonces are refused on the MCP channel. Nonces issued while it held the grant carry a signed flag, and
  the orchestrator's queue refuses them too. Nonces issued before rc.14 have no flag and stay usable for up to 24 h;
  rotating the ledger key at upgrade closes that window. (rc.14) #242
- **Proposals instead of writes.** Agents return typed change and deletion proposals and command requests. The
  orchestrator applies them with `--apply-proposal` and `--run-request`. Identity comes from a signed dispatch
  nonce (`--issue-dispatch --agent SLUG`), never from the caller. Nonce uses are counted only just before an
  action, and attempts are capped. (rc.9)
- **Policy lives in the brief.** New fields: `write_policy`, `agent_policies` (write scopes, commands with exact
  argv prefixes, cwd, stdin gates), `proposal_gates`, `protected_paths` and `confined_programs`. Lint refuses
  shells, option or parent patterns and scopes that cover the brief or the control plane. Write scopes accept
  single-segment patterns. (rc.9)
- **Requests fail closed.** A proposal outside its scopes, in the control plane, stale or failing a gate is
  refused. Commands get a scrubbed environment; an undeclared write fails the run (exit 3); a timeout kills the
  process group. An empty `stdin_gates` list is refused. (rc.9)
- **A signed proposal ledger.** `.agentteams/proposal-ledger.jsonl` logs every action and refusal, HMAC-signed and
  hash-chained with a signed head anchor. `--verify-proposal-ledger` checks it. (rc.9)
- **Where the ledger key comes from.** The runner reads it only from a 0600 file in `~/.config/agentteams/keys`,
  and refuses to start with a ledger key in its environment. Other ledger operations take the key file or the
  environment (`AGENTTEAMS_PROPOSAL_LEDGER_KEY`, falling back to `AGENTTEAMS_DECISION_SIGNING_KEY`).
  (rc.9, rc.12)

#### The runner

- **The runner's own heartbeat never fails a command.** A confined command that outlasted a heartbeat tick used to
  fail with exit 3, because the runner counted its own `.agentteams/runner.heartbeat` write as the command's
  undeclared write. That file is now exempt, but only when an OS sandbox ran. A lock keeps a snapshot from ever
  seeing a half-written beat. Unconfined runs are unchanged. (rc.14) #242
- **An out-of-session runner.** `agentteams --serve-requests` runs outside every agent session, alone holds the
  ledger key, and serves a queue in `.agentteams-queue/`. Under the switch the proposal commands queue and wait;
  `--wait-result` waits again. One runner serves a project. Command output is capped and the heartbeat has its
  own thread. Restart the runner after updating. (rc.9)
- **The sandboxed orchestrator reaches the runner.** The runner publishes its heartbeat and sealed results in the
  read-only `.agentteams-status/`. Clients without a `reply_key` are refused. (rc.10) #203
- **Staged writes by default.** Agent writes are staged under `.agentteams/staged/` after every check. The
  orchestrator uses `--list-staged`, `--show-staged`, `--apply-staged SID` (re-checked against the current file)
  and `--reject-staged`. Deletions are always staged. (rc.9)
- **Direct writes only under an operator grant.** The runner applies a write directly only under a verified
  operator Ed25519 grant that binds the agent, tools, scopes, gates, server hash, expiry (30 days at most) and a
  write cap, with an aggregate cap of 3. New: `--sign-mcp-direct-grant`, `--list-mcp-direct-grants` and
  `--revoke-mcp-direct-grant`. (rc.9) #166
- **The `agentteams_runner` MCP server.** Agents named in the new `mcp_grants` brief field get exact
  `mcp__agentteams_runner__<tool>` names and the server (`write_file`, `delete_file`, `run_command`,
  `read_file_hashed`, `request_status`). It holds no key, serves one agent, launches pinned (`python3 -I -S`) from
  `.agentteams/bin/`, and records no machine path. The runner takes the channel from the queue directory and
  binds each request to its nonce's agent. (rc.9)
- **The runner names the reinstall command** when the operator's confined file is out of date. (rc.10)

#### Confinement

- **Runner commands run in an OS sandbox.** Every `--run-request` command and pre-write gate runs confined.
  Leftover processes fail the run. With no usable sandbox the runner refuses unless `allow_unconfined_runs` is
  set, which is logged. Write roots must be real, symlink-free in-project directories. (rc.9)
- **On macOS** the sandbox is Seatbelt: writes only to declared roots and a private `TMPDIR`; the control plane,
  ledger, queue and `.git` denied; the key directory unreadable; only declared programs run. **On Linux** it is
  bwrap, with the same write rules and a PID namespace, but no program allowlist. (rc.9)
- **The confined-programs file is operator-owned.** The runner reads `confined_programs` from
  `~/.config/agentteams/confined/<project>-<hash>.json`. `--install-confined FILE` installs it only on a rerun
  with a matching `--confirm-review-sha256`. Commands take a per-entry timeout. (rc.9)
- **Gate programs are bound.** `gate_exec` entries are bound to the gate argv (`gate_argv_sha256`), and a gate
  whose programs overlap a write root is refused. (rc.9)
- **macOS: xcrun launchers reach their real tool.** For a `/usr/bin` program that is an xcrun launcher, such as
  `/usr/bin/git`, the profile also allows the real tool by literal path, and for git its `--exec-path` helpers.
  Paths come from `xcrun --find` under `xcode-select -p`, and only root-owned paths are kept. `--check-wiring`
  reports each launcher's expansion and warns on unresolved ones. (rc.13)
- **Write roots over package stores warn.** A root that covers `.lake/packages`, `node_modules`, `site-packages`,
  `.venv`, `venv`, `vendor`, `.cargo/registry`, `Pods` or `.gradle` warns at `--install-confined` and each run. A
  run that also depends on an unresolved launcher is refused unless the entry sets
  `allow_unresolved_launcher_with_package_store: true`. (rc.13)
- **Claude sandbox confinement by profile.** `privilege_profile` defaults to `confined`, which emits a sandbox
  block that confines Bash and child writes to `workspace_write_roots` once merged; `"cooperative"` opts out.
  `exclusive` also denies reads of a curated set plus `protected_read_paths`. The block sets
  `"failIfUnavailable": true`. An unknown profile fails the build. (rc.7, rc.8)
- **Write roots are checked.** Generation refuses roots at `/`, `~`, `$HOME` or their ancestors, the key
  directory, credential paths, control-plane directories and paths with shell or glob metacharacters. A new root
  outside the project needs `--accept-write-root PATH`, and each added root prints `SANDBOX WIDENING`. (rc.8)
- **Confinement fails closed only where the host has no boundary.** Claude has its own sandbox, and on Linux and
  macOS the other frameworks are confined through the launcher. On native Windows and other platforms, a
  `confined` or `exclusive` build for a non-Claude framework exits non-zero unless
  `--allow-unenforced-confinement` is passed. (rc.7)
- **Fail-open settings are named.** `--update`, `--dry-run` and `--check` print a notice for a merged Claude
  sandbox without `failIfUnavailable`, and an advisory for a confined team with no sandbox merged. (rc.8)
- **The `confine-run.sh` launcher binds the control plane read-only,** including all of `.claude`, and
  self-binds its ancestors. It refuses symlinks and hard-linked protected files, and adds `--protect PATH`. On
  macOS its Seatbelt profile matches Linux. (rc.8, rc.9) #8
- **Launcher behaviour.** It uses a default-deny environment allowlist and runs on bash 3.2. A framework's
  control-plane entries are required only when its agents directory holds a build-log marker. New:
  `--protect-prompt-roots` (Linux) and `--coord-root PATH`. (rc.7, rc.8) #8, #78
- **Goose's macOS Seatbelt profile denies all of `.claude`,** as on Linux. (rc.8) #86
- **Goose Linux runner: shell injection fixed (HIGH).** Write roots were pasted raw into an operator script. They
  are now validated and shell-quoted. (rc.8)
- **Linux setup.** `scripts/install-sandbox-deps.sh` installs bwrap and socat and diagnoses AppArmor user
  namespaces; `test-sandbox-apparmor-userns.sh` probes a profile. (rc.8)

#### Codex under the launcher

- **Codex runs under agentteams' launcher.** This is how Codex holds under the switch (see "Frameworks under the
  switch"). `confined` and `exclusive` on Codex expand to `codex:sandbox`. Linux and macOS teams get
  `.codex/confined-run.example.sh`, which runs Codex inside `confine-run.sh`. (rc.9) #160
- **A per-project `CODEX_HOME`.** The launcher validates it and makes its config, hooks, prompt and plugin files
  read-only. (rc.9) #160
- **The Codex role gate.** Under the switch, Codex teams get `.agentteams/bin/codex-role-gate.py`, the pinned
  read-only file server and `.codex/hooks.json`. Calls from a spawned agent may use only `agentteams_readfs` read
  tools; every call is denied without `AGENTTEAMS_CODEX_CONFINED=1`. (rc.9) #160, #167
- **The runner checks the gate before starting.** It refuses unless the gate, server and hooks file match their
  pinned hashes, and refuses nested Codex config. A self-probe before every confined launch checks that keys are
  unreadable and the control plane is unwritable. (rc.9) #167

#### MCP servers and checks

- **A foundational MCP server catalogue.** First-party read-only `recall` and `gitread` servers are on by default
  wherever an MCP host feature is set; `github-read`, `github-write` (no merge, delete or fork) and `fetch` are
  opt-in. `agentteams --serve-mcp {recall,gitread}` runs them over stdio, confined to the project. Nothing is
  emitted under the switch. New brief fields: `mcp_catalog`, `mcp_catalog_exclude`, `pr_management`. (rc.9) #150
- **The MCP-need protocol.** Under the switch, teams decide from runner-ledger evidence whether an agent needs an
  MCP server, keep `references/mcp-needs.csv` (orchestrator-only), and route every capability-gap plan to the
  orchestrator. `--mcp-need-report` summarizes the evidence per agent. (rc.9)
- **`AR_WRITE_POLICY` audits the switch.** Under the switch it errors on any non-orchestrator agent that can
  write, on capability front-matter keys, on a shell on any non-orchestrator agent in a team with grants, on a
  granted agent that isn't rendered exactly, and on an orchestrator without "Applying Proposals". Every render
  warns `[WRITE-POLICY]` for adopted agents that fail it. (rc.9)
- **`--check-wiring` covers the switch.** It refuses any config that defines `agentteams_runner`, checks the
  required deny rules and that the sandbox is enabled, refuses loadable files in the backup tree, and flags a
  project-local `python3`. (rc.9, rc.10)
- **`--check-wiring` refuses unsafe status-channel setups:** a Read rule on `.agentteams-status/`,
  `allowUnsandboxedCommands`, and any Linux host for a team under the switch, until the runner's status channel
  is measured under bwrap. (rc.10) #203

### Frameworks

#### Claude

- **Skills are discoverable.** Skills are emitted as `.claude/skills/<name>/SKILL.md`, so they run as `/name`
  commands. Old flat files get a notice and are never deleted. `recall` leads with lexical search. (rc.7)
- **Bridge features** (`bridge:copilot-vscode-to-claude:*`): subagent stubs, example hooks, a cache-split
  `CLAUDE.md`, `/schedule` routines and a plan-to-TodoWrite skill. (rc.7)
- **The team builder launches.** Its file has front matter, and under the switch its canonical tools map to Read,
  Grep and Glob. (rc.7, rc.9)
- **Claude Code version tripwire.** `--check-wiring` notes when the installed `claude --version` has no recorded
  sandbox test pass. (rc.8)

#### Copilot (VS Code and CLI)

- **copilot-cli emits `.github/agents/*.agent.md`,** the VS Code format, instead of `.github/copilot/<slug>.md`.
  Old files are left as orphans. Sync refuses copilot-vscode and copilot-cli together; `--frameworks` narrows the
  set. Interop into copilot-cli keeps tools, model and roster. (rc.7)
- **`user-invokable` is renamed `user-invocable`;** deployed files migrate automatically. (rc.7)
- **Body size.** A body over 30,000 characters warns on native generation and is refused on interop.
  (rc.7, rc.10) #210
- **`--update` never blanks an orchestrator's `agents:` list,** and filtering handles flow and block lists.
  (rc.1, rc.7)

#### Goose (Beta)

- **Goose support.** `--framework goose` emits recipes, orchestrator `sub_recipes`, `AGENTS.md`, `.goosehints`
  and a team builder. Goose is a convert and bridge target. `.goosehints` no longer imports `AGENTS.md`, which
  Goose reads natively. (rc.7)
- **Grant-scoped recipes.** `goose_tool_scoping: "grant"` derives each recipe's extensions from its declared
  tools (verified on Goose 1.37.0). In rc.9 the switch forced grant mode; the switch itself is now refused on
  Goose, so set `goose_tool_scoping` directly. In legacy mode an empty extension list emits `extensions: []`.
  (rc.9, rc.10)
- **`agentteams_readfs`.** Read-only agents read through a read-only stdio file server shipped as package data
  and launched `python3 -I -S`. (rc.9)
- **Recipe checks.** `--post-audit` reports `AR_GOOSE_GRANT_EXCEEDED`, `AR_GOOSE_MARKER_MISSING`,
  `AR_GOOSE_READONLY_WRITE` and `AR_GOOSE_EXTENSIONS_FAIL_OPEN`. An operator MCP server's name never excuses a
  builtin. `--check` compares the installed Goose version with 1.37.0 (advisory). (rc.9)
- **Delegation is narrowed.** In grant mode the orchestrator recipe lists the exact `extensions` argument for
  each sub-recipe; unknown targets get only `agentteams_readfs`. This stays cooperative. #206
- **Merge keeps recipes intact.** `--update --merge` reconciles `sub_recipes` and `extensions` from the render,
  keeps enriched bodies and valid YAML, puts new sections inside `instructions: |`, and adds a Project-Specific
  Notes region. It removes hand-added `team-builder` entries from `sub_recipes`. (rc.8, rc.9, rc.10) #15, #189
- **Goose leaves other owners' `AGENTS.md` alone.** It no longer overwrites a root `AGENTS.md` it didn't write,
  and marks its own. (rc.10) #222
- **Runtime helpers.** `goose-run-resilient.py` resubmits dead turns; an OpenRouter route proxy and
  `--providers` preflight help with backend routing; a coordination MCP server and `coordination_write_roots`
  support cross-repo work. (rc.7, rc.8)

#### Codex (Beta)

- **Agents are `.codex/agents/*.toml`.** Tools and handoffs go into a fenced `codex_translation` block, which is
  template-authoritative. Tool docs are `.agents/skills/<slug>/SKILL.md`. Handoffs are pruned to the roster.
  (rc.8) #25
- **Every role gets an explicit `sandbox_mode`:** `workspace-write` only when it declares edit, otherwise
  `read-only`, which means no edit, execute or retrieval. Read-only agents get a "Reading on Codex" section (the
  role-gate version under the switch); shell agents get a "Secrets on Codex" section. (rc.9) #132
- **Codex bodies update.** Agent bodies are fenced, and `--update --merge --yes` retrofits legacy files. Every
  native agent has a Project-Specific Notes region, so `--update --overwrite` carries notes. Learned blocks sync
  to and from the TOML. (rc.9, rc.12)
- **A merge never writes an agent TOML that doesn't parse.** Every merged `.toml` is parsed before writing; a
  failure is refused, the file is left as it was and the run exits non-zero. (rc.11)
- **MCP.** `codex:mcp` writes read-only servers into a managed block of `.codex/config.toml` with
  `default_tools_approval_mode = "prompt"`. (rc.7, rc.8)
- **Shared `AGENTS.md`.** A foreign or symlinked root `AGENTS.md` is never written, even with `--overwrite`, and
  even before `.codex/agents` exists. (rc.8)
- **`AGENTS.md` under the switch.** A goose-owned `AGENTS.md` gets only a `codex_write_policy` fence; a
  hand-written one gets a paste-in file under `.codex/examples/` (not when it is a symlink). (rc.11)
- **Codex is an interop source,** with a lossless round trip. (rc.8)

#### AGENTS.md

- **`--framework agents-md`** emits a neutral repository-root `AGENTS.md` plus `.agents/`. It is generate-only, and
  it still overwrites any owner's `AGENTS.md`. (rc.7, rc.10)
- **`AGENTS.md` carries the constitutional rules** in a template-sourced `constitutional_rules_baseline` fence,
  with a heading for project extensions, and a `## Code Style` section from `style_rules`. (rc.7, rc.8) #16

#### Grok (Beta)

- **Grok Build adapter.** `--framework grok` renders `.grok/agents/<slug>.md` without default tools or MCP
  inheritance, co-owns `AGENTS.md` through a `grok_runtime` fence, and protects `.grok/`. It works with interop,
  `--absorb` and fleet; convert and bridge refuse it. (rc.10) #214, #216, #218
- **The Grok hook gate is advisory.** A pinned shim runs the unmodified gate and fails open. #216
- **No widening on Grok.** Scoped `tools:` entries and `MCPTool` are dropped, and denies are applied by leaving
  the tool out. A deny list that leaves no tool is refused. `--update --merge` names any `.grok/agents` file that
  still widens. #227, #228
- **Run an agent by path:** `grok --agent .grok/agents/<slug>.md`. Grok 1.0.52 can't spawn custom subagents. #227

#### Interop, sync, convert and bridge

- **`--interop-from` never widens a grant.** Each projected agent is re-read as its target reads it and checked
  against its source grant before anything is written. Pinned sync and `--convert-from` check every agent first.
  Front-matter values are written on one line and quoted. #210
- **`--interop-allow-cooperative SLUG[,SLUG…]`.** A narrow agent projected to Codex (outside its role gate) or
  agents-md is refused unless named here. Naming it needs a decision log and records a row. #210
- **Interop carries the switch into Claude.** `--interop-from DIR --framework claude --description BRIEF` applies
  the brief's `write_policy` and `mcp_grants`, so adopted bespoke agents get the runner. A non-Goose bridge
  orchestrator is narrowed, not refused. (rc.9) #174, #178
- **Interop into Goose under the switch is refused,** because the switch is refused on Goose. Without the switch,
  interop into Goose works; a pinned sync to Goose needs the pinned `agentteams_readfs` server. (rc.10) #210
- **Convert and bridge refuse a switched team.** `--convert-from` and bridge subagent stubs refuse a team under the
  switch and point to interop. (rc.9) #183
- **Interop-projected teams are visible.** Codex, Copilot and Goose projections write a team marker, so fleet,
  `--check` and the launcher see them; the marker authorizes nothing. (rc.9)
- **Skills.** `--interop-skills-only` imports skills only. Skill paths are contained, descriptions are kept and
  safely quoted, and slugs are validated. (rc.8) #71
- **Pinned sync.** `sync_pin` keeps several framework renders in step from one canonical source. Unchanged agents
  are left byte-identical, instruction files receive only fenced regions, and projections back up first.
  `--sync-init` and `--sync` take their root from `--project`. (rc.7, rc.8)
- **Canonical format.** The CAI round trip keeps capabilities, tool scopes, names and descriptions. A canonical
  directory can be a bridge source, `--framework generic` is a bridge-only target, and `--package-team` exports a
  portable zip. (rc.7)
- **Bridges.** `--bridge-merge` leaves project-owned entry files and native agent bodies alone and says so; the
  banner names the mode; the check report records a source digest. Bridged Goose teams document
  `agentteams.research`. (rc.7, rc.8)
- **Framework watch.** Every team ships `references/framework-watch.reference.md`. `--agent-check` and
  `--framework-freshness` report provider drift and stale renders; `--stale-check` reports `PIN_UNSYNCABLE`.
  (rc.1, rc.8)
- **Scoped Bash support is tracked.** The framework watch records whether upstream docs show that Claude honours
  `Bash(...)` scopes in subagents. Only a person may mark it supported. (rc.7)

### CLI and pipeline

#### Update, merge and fences

- **`--update` merges by default.** It preserves content outside fences; a full re-render needs
  `--update --overwrite`. Byte-identical files are not rewritten. (rc.1)
- **Fenced sections.** Merges update fenced regions only, and every persona has a Project-Specific Notes region.
  Legacy files are wrapped in a `content` fence on `--update --merge --yes` (`--no-add-fence-markers` opts out),
  and a stale `content` fence is migrated to named fences. (rc.1, rc.7, rc.10) #201
- **Fence tools.** `--add-fence-markers` adds fences to a legacy file. `--migrate` tags a snapshot and re-renders
  with fences; `--revert-migration` restores the snapshot. (rc.1)
- **Merges keep your edits.** Missing sections are inserted in position. Front matter gets a three-way merge
  (skills included), keeping both-side changes as reported conflicts. Hand-written handoffs are kept.
  (rc.7, rc.9, rc.10)
- **Merges say what they skip.** Unfenced drift is reported for unedited files, and `--update` writes
  `update.report.md`. (rc.7)
- **Shrink policies.** `--shrink-policy` takes `warn`, `halt`, `allow`, `preserve` (default) and `additive`.
  Security, constitutional and `codex_translation` fences are template-authoritative. A replaced body is saved as
  a `.lost` sidecar and reported. (rc.1, rc.2, rc.7, rc.8)
- **`--shrink-allow FILE:FENCE@DIGEST`** lifts `preserve` for one reviewed section, including files above the
  agents directory. (rc.9, rc.10)
- **User regions survive `--overwrite`.** Project-Specific Notes and Rules are carried into the new render;
  `--discard-user-regions` opts out. (rc.9)
- **Project Layout region.** A USER-EDITABLE Project Layout region in the instruction files overrides the
  generated layout. (rc.10) #193
- **Fence twins.** An unfenced copy of a fenced section is dropped only on proof. A row in
  `references/fence-twin-acks.csv` acknowledges a twin kept on purpose; fleet flags new acknowledgements once.
  (rc.10) #198, #199
- **Previews.** `--update --dry-run` previews every action, including front-matter capability changes; `--json`
  prints pure JSON. `--check` uses the same pipeline. (rc.1, rc.7)
- **`--reconcile-front-matter`** reports divergence from template front matter; `--reconcile-apply` applies it
  and prints `[CAPABILITY GRANT CHANGED]`. (rc.7, rc.10)
- **Learned agent notes.** `--sync-agent-docs` propagates `AGENTTEAMS-LEARNED` blocks between framework copies.
  It reports by default; blocks with findings are quarantined. Regeneration keeps them. (rc.9)
- **Safer targets.** A relative `--output` on a foreign git-tracked directory is refused
  (`--allow-foreign-output`). `.vscode/tasks.json` is never written outside the output tree. An in-sandbox
  `--update` refuses before writing. (rc.7, rc.8)
- **`--self` without `--output`** writes to the chosen framework's agents directory (`.claude/agents` for
  Claude). It refuses when that directory's signing switch is weaker than `.github/agents`. (rc.10)
- **`--update` on a bridge target needs `--materialize-native`.** (rc.7)

#### Adoption, fleet and backups

- **Adopting bespoke agents.** `--adopt-orphans` adds agent files to the roster. With `--update` it is
  append-only and needs a once-only operator Ed25519 decision (`adopt-orphans-merge`). (rc.7, rc.9)
- **`--adopt-only SLUG[,SLUG…]`** adopts a named subset and touches only the orchestrator. Adopted agents are
  routed in every orchestrator. (rc.9, rc.10) #209
- **`--overwrite` keeps adopted routing rows.** It refuses with `[ADOPTED-ROWS] blocked` rather than drop a row
  whose agent file still exists. (rc.9)
- **`--fleet DIR`** updates many workspaces in process (dry run by default). It skips its own output, worktrees
  and submodules, refreshes bridges from their recorded source, detects USER-EDITABLE regions correctly, and
  reports shrink grants and tracked volatile churn. (rc.7, rc.9, rc.10) #181
- **Backups.** Writes are atomic and backed up to `.agentteams-backups/` with a hash manifest. New:
  `--list-backups`, `--restore-backup`, `--verify-backup`, `--prune-backups`, `--backup-mirror` and `--no-backup`.
  (rc.1, rc.7)

#### Retrieval, research and indexes

- **Memory index.** `references/memory-index.json` is a BM25 index the navigator queries first. Results carry a
  confidence label. Paths are relative and local paths are redacted. (rc.1, rc.7)
- **Memory-index shards.** The index is written compactly, and above 20 MiB it is sharded into sha256-pinned
  parts. (rc.10) #219
- **Code index.** `--refresh-code-index` and `--query-code` search code and APIs from a gitignored cache that is
  never executed. A partition path can't leave the cache. (rc.7, rc.9)
- **`agentteams[research]`.** Search with a backend fallback chain, scholarly search (OpenAlex, Crossref, arXiv),
  a TTL cache and page fetch (400 KB cap). The `retrieval` tool token grants it to two agents. (rc.7)
- **Browser rendering.** `python -m agentteams.research browser URL` renders a page with Playwright
  (`agentteams[browser]` plus `playwright install chromium`). (rc.7, rc.10)
- **News as perspective.** `agentteams.research.news` tags news sources and attributes their claims as
  perspective, with published dates. (rc.7)
- **Source allowlist presets.** `SOFTWARE_CONFIG`, `RESEARCH_CONFIG`, `DATA_CONFIG` and
  `config_for_project_type()` give per-archetype source allowlists. `DEFAULT_CONFIG` is unchanged. (rc.7)
- **Research-capability advisory.** The manifest advises, and does not enable, `research_verification` when a
  team could use it. (rc.7)
- **Every team ships an external-retrieval quality gate:** externally sourced summaries are reviewed before
  use. (rc.7)

#### Branch lifecycle

- **`--branch-inventory`, `--branch-cleanup` and `--branch-post-merge`.** Inventory classifies branches and writes a
  deletion plan. Cleanup needs a once-only `@security` PASS bound to the plan's hash; post-merge deletion needs an
  operator Ed25519 grant. Deletions are logged to a hash-chained ledger, and the gate asks before either one
  applies (see "The constitutional gate hook"). (rc.9) #102

#### Checks, audits and records

- **`--verify-integrity` passes on a clean checkout of a Claude team.** The gate's machine-local interpreter pin is
  gitignored, so it is reported as `LOCAL` when absent, not `MISSING`. Every other absence is still `MISSING`, and
  `--check-wiring` still checks the interpreter. (rc.14, found in baseAgent) #242
- **New checks:** `--check-budget`, `--check-rank` and `--check-rank-strict`, `--check-wiring`, `--stale-check`
  and `--stale-remediate`, `--capture-baseline` and `--check-baseline`, a living-document check and a dual
  descriptor advisory. `--post-audit` and the orphan advisory work for every framework. (rc.1, rc.3, rc.7, rc.9)
- **The build log records which code ran.** `build-log.json` records `agentteams_source`, and `--version` prints
  it. (rc.8, rc.9) #40, #106
- **Behavioural drift detection.** `behavioral_drift.detect_behavioral_drift` compares a run's trajectory with
  the team's eval suite and reports divergence. (rc.1)
- **`python -m agentteams.session_scan`** collects known issues, pending or blocked plan steps and git status
  anomalies. (rc.7)
- **A man page,** `agentteams.1`, is generated from the CLI. (rc.1)

#### Generation and templates

- **Tools are documents, not agents.** A tool emits a reference document (Copilot) or a skill (Claude) and is no
  longer on the roster. (rc.7)
- **The default model is Claude Opus 4.8** for every generated agent. `--cost-routing` tiers are unchanged.
  (rc.7)
- **Generated references and records:** an eval suite, a delivery receipt, a team graph and a model-routing
  contract (`--cost-routing`, off by default). (rc.1)
- **Eval adapters.** `agentteams/eval_adapters/` turns the eval suite into Inspect AI task source or an OpenAI
  Evals definition, without importing either framework. (rc.1)
- **Templates use the update features.** The orchestrator gains a behavioural-verification workflow that reads the
  eval suite. The conflict auditor checks typed handoffs. The adversarial and work-summarizer agents query the
  memory index first. The agent updater documents dry-run JSON, cost routing, receipts and backups. (rc.1)
- **Workflow templates:** governance workflow updates, work-summary capture and backfill, the Post-Deliverable
  Retrospective, a post-merge CI check and parallel plan waves. (rc.1, rc.7)
- **Code-hygiene and review rules:** new code-hygiene rules on exception handling, AI bad habits, script-first
  output and dispatch tables; technical-validator rules get their own numbering; quality and adversarial checks
  cover claims that depend on an unstated time, place or text version; read-only roles default to terse output.
  (rc.3, rc.7, rc.9) #180
- **A Unix-philosophy reference** maps the code-hygiene rules to Unix principles. (rc.1)
- **Post-production auditor** template and closure-gate schemas. (rc.1)
- **Opt-in agents:** `@pr-manager`, `@pr-notifier` and `@pr-reminder` (never merge), and `agentteams-updater`
  (proposal-only). (rc.7, rc.9)
- **`--enrich` and `--auto-correct`.** `--enrich` fills default template elements from project context and exports
  `defaults-audit.csv`. `--auto-correct` runs the `copilot` CLI to fix audit findings, then re-audits. (rc.1)
- **`--target-host-features`** takes `<namespace>:<feature>` tokens that turn on opt-in emission paths. Default
  output is unchanged. (rc.7)
- **MCP server auto-detection** is added but inert: default emission is unchanged. (rc.7)
- **Brief handling:** `{MANUAL:*}` tokens become N/A outside `--self`; archetype matching uses word boundaries;
  the module-doc pair needs packaging or docs-site signals; tool docs can follow a pinned version; scripts and
  destructive commands are never a default task; schema fixes. (rc.1, rc.7, rc.9, rc.10) #208
- **Ingest fallbacks.** A Markdown brief's `project_goal` comes from an overview heading or the first paragraph.
  With `doc_site_config_file` declared, reference and style paths are inferred from `docs/` or `docs_src/`.
  (rc.1, rc.7)
- **`devDependencies` no longer get a reference document each;** they are categorised `other`. (rc.7)
- **The `build-team` command is deprecated.** It still works through 1.x with a warning; use `agentteams`. (rc.1)

### Release and installation

- **Releases.** `v1.0.0` is a signed tag on `release/1.0`; `SECURITY.md` lists the release signing keys by tag.
  (rc.9, rc.10)
- **Install hints pin a real tag.** A release or pre-release maps to its own tag. Dev and local builds pin
  `LAST_RELEASE_TAG`, which a release PR updates with `pyproject.toml`. (rc.8, rc.9, rc.11)
- **agentteams itself is never installed from PyPI.** Every emitted install command uses the git source,
  `pip install "agentteams[<extra>] @ git+https://github.com/jlcatonjr/agentteams.git"`. Commands generated at
  runtime pin the installed tag (`…agentteams.git@<tag>`). (rc.8)
- **Dependencies.** The one runtime dependency is `jsonschema>=4.20`, installed from PyPI. Optional extras:
  `research`, `signing` (`cryptography==50.0.0`), `browser` and `test`. (rc.1, rc.2, rc.7, rc.8)
- **Packaging.** The wheel ships the JSON schemas, the Goose helper scripts and the read-only file server, and
  leaves out scratch directories. `__version__` comes from package metadata. (rc.1, rc.7, rc.9, rc.10)
- **The `signing` extra pins `cryptography==50.0.0`** (CVE-2026-69247). (rc.8)
- **Environment scripts:** `scripts/verify-env.py` (Python 3.11 or later, git 2.23 or later) and
  `scripts/install-agent-doc-sync.sh` (systemd units for learned-note sync). (rc.1, rc.9)

### Docs

- **`STABILITY.md`** lists the SemVer surface, a status table for all seven frameworks and the per-framework 1.0
  status of `write_policy`. `SECURITY.md` gives disclosure, the threat model and the signing keys. (rc.1, rc.11)
- **New:** `docs_src/known-limitations.md`; "Upgrading to 1.0" in `docs_src/migration-guide.md`; Grok privileges;
  the confinement and `proposal_run` API pages; runtime-governance guides. (rc.7, rc.9, rc.10, rc.11) #179
- **Corrected:** the claim that interop into Goose is unsupported (it is supported without the switch); the Goose
  privileges profile; the threat-model disclosure of laundering; the waiver example's placeholder key. The CLI
  reference lists every flag. (rc.7, rc.9, rc.10, rc.11, rc.12)
- **Release checkouts and signing.** The migration guide notes that a release checkout records its pin only when
  pip-installed: a source-path install (`.pth` or `PYTHONPATH`) reports `0.0.0+local` and records no pin.
  `SECURITY.md` explains why a signer installed under `$HOME` can be refused when a repository has no
  `.claude/settings.json`: install it outside `$HOME`, for example under `/opt`. (rc.14) #237
- **Architecture graph** (`references/architecture-graph.md` and its SVG) regenerated for the 1.0 module layout.
  (rc.14) #236

### Breaking changes and migration

This section is for anyone upgrading from 0.1.0 or from any release candidate. The steps, with commands, are in
`docs_src/migration-guide.md`, "Upgrading to 1.0". Run them from the 1.0 install, before re-rendering.

- **Check HALTs and the chain first.** Run `agentteams --verify-integrity`. Its report lists each unretracted HALT
  that the token rule now makes blocking, with the id to retract it by. Retract what no longer applies. (Step 1)
  #224, #225
- **Migrate the decision log.** Run `--migrate-decision-log --yes` for the signing columns, row padding and
  `text_digest`. Re-issue any revoked retraction as a signed row. (Step 2)
- **Re-sign rows** signed before rc.9 that have a non-empty `scope` or `effect_*` value, and every later row in a
  chained log. Don't clear the cells instead. (rc.9)
- **Replace weak HMAC keys.** Generate a 32-byte secret (`python -c 'import secrets; print(secrets.token_hex(32))'`),
  re-sign outstanding decisions, waivers, directives and grants, and write a new ledger key (mode 0600) before
  issuing new nonces. (rc.12)
- **Re-render, then check the gate.** Re-render, or copy the template hook into `.claude/hooks/` and
  `.github/hooks/`, then run `--check-wiring`. (Steps 3 and 4)
- **`research browser --screenshot` is removed.** Use the `browser_screenshot()` library call instead.
  (Step 5) #224
- **Scripted signing** needs `--confirm-review-sha256 <digest>`. Signing from a checkout is refused; sign from a
  trusted install, or pass `--allow-checkout-signing`. The key-provisioning helper needs `--team-dir`. (rc.8)
- **Move the signing key.** The operator runs `provision-operator-signing-key.sh --migrate` to move it to
  `~/.config/agentteams/keys/`, updates any exported keyfile path, and re-merges the sandbox block. (rc.8)
- **Grants.** HMAC write grants no longer widen a sandbox; re-issue them with `--sign-grant`. Create
  `references/security-approvers.txt` before issuing or verifying grants. (rc.7, rc.8)
- **`privilege_profile` defaults to `confined`.** To opt out, set `"privilege_profile": "cooperative"`. (rc.8)
- **Decision signing is on.** To opt out, set `enforce_decision_signing: false` in the brief. (rc.7)
- **`--update` merges.** Scripts that relied on a full re-render must pass `--update --overwrite`, which needs an
  `overwrite` clearance. (rc.1)
- **Merges wrap legacy files in fences.** To opt out, pass `--no-add-fence-markers`. (rc.7)
- **The default model is Opus 4.8.** A `model:` value you set by hand is kept: a merge reports template model
  changes but doesn't apply them. (rc.7)
- **Sandboxes.** Re-merge the Claude `sandbox` and `permissions` blocks (including `failIfUnavailable`) after
  updating. Dangerous write roots are refused; add a new external root with `--accept-write-root PATH`. (rc.8)
- **Profiles and hosts.** An unknown `privilege_profile` fails the build; fix the spelling. On native Windows and
  other platforms without a launcher, a confined non-Claude build exits non-zero; pass
  `--allow-unenforced-confinement` or use `"cooperative"`. (rc.7)
- **macOS launcher.** It refuses a team that is missing a control-plane entry; run `agentteams --update`. (rc.9)
- **The switch.** It needs an explicit `confined` or `exclusive` profile, and is refused on Goose, Copilot and
  agents-md. Interop under the switch targets Claude only. (rc.9, rc.10)
- **The confined-programs file.** Reinstall it once with `--install-confined` and `--confirm-review-sha256`, so it
  records `gate_argv_sha256`. (rc.9)
- **The runner.** Clients without a `reply_key` are refused: upgrade every agentteams install that talks to the
  runner to 1.0, then restart `--serve-requests`. (rc.10)
- **Interop with existing agents** under the switch needs `--overwrite`. (rc.9)
- **Framework output moved.** Codex agents are `.codex/agents/*.toml`; copilot-cli agents are
  `.github/agents/*.agent.md`; Claude skills are `<name>/SKILL.md`; tools are documents. Remove the old files by
  hand. (rc.7, rc.8)
- **Codex roles.** Command roles that don't declare edit are now `read-only`. Declare edit for a role that must
  write. (rc.9)
- **Goose and `AGENTS.md`.** Goose no longer overwrites an `AGENTS.md` it didn't write. To hand the file to Goose,
  delete it and re-render. (rc.10)
- **Goose and `.goosehints`.** `.goosehints` no longer imports `AGENTS.md`; Goose reads it natively, so no action
  is needed. (rc.7)
- **Grok scoped entries are dropped.** Run `--reconcile-front-matter --reconcile-apply`, or fix the files that
  `--update --merge` names. (rc.10)
- **Widening interop is refused.** Name each agent whose cooperative grant you accept in
  `--interop-allow-cooperative`. Interop also refuses a Copilot body over 30,000 characters; shorten the body
  first. (rc.10)
- **`--from-migrate` is removed.** Use `--migrate`, which sets its gate exemption internally. (rc.1)
- **`--self` writes elsewhere.** Without `--output`, it writes to the chosen framework's agents directory
  (`.claude/agents`, `.goose/recipes`, `.codex/agents`, `.agents`). Copilot (the default) still uses
  `.github/agents`. The old location was a bug that put non-Copilot agents in the Copilot directory in the wrong
  format, so do not recreate it with `--output`; remove any such stray files from `.github/agents`. (rc.10)
- **CLI.** `--update` on a bridge needs `--materialize-native`; `--sync-init` and `--sync` refuse `--output`
  (pass `--project`). Hand edits inside `codex_translation` are overwritten; move them to Project-Specific Notes.
  (rc.7, rc.8)
- **`scripts/resolve_fence_collisions.py --trust-provenance` exits 2.** Use `agentteams --update --merge`, which
  drops a fence twin only on proof. (rc.10)
- **Memory index.** Once one writer produces a sharded index, keep every writer on 1.0. (rc.10) #219
- **Rolling back:** see "Rollback A: turning `write_policy` off" and "Rollback B: reverting to rc.9" in the
  migration guide.

### Known limitations

These ship with 1.0. Those from rc.10 onward are described, with workarounds, in `docs_src/known-limitations.md`;
the rest are stated here.

- Clearances bind by id prefix; name them `<action>-<instance>` and use the `scope` column. A CONDITIONAL PASS
  needs `conditions_verified` to be exactly `verified`. A family retraction clears every prefix-matched HALT.
  (rc.10)
- Logs signed before 1.0 may verify through a legacy prefix; deletions inside it, and rows cut from the tail,
  are not detected. (rc.10)
- Grants are cooperative, not enforced, on Goose delegation, Codex outside its role gate, agents-md and Grok.
  (rc.10)
- Claude under the switch is refused on Linux. (rc.10)
- Claude Code's built-in general-purpose subagent holds Bash. (rc.10)
- The research cache still writes under `references/research-cache`. (rc.10)
- Codex: a merge doesn't narrow `sandbox_mode`; a project's own `.codex/hooks.json` keeps the role gate out; new
  teams start a legacy six-column decision log; the role gate has no codex-cli version pin. (rc.11, rc.12)
- A signed `overwrite` row can't cover a render's `CLAUDE.md` write. (rc.11)
- In an unconfined session, anything that can write the verify-key store can plant a key. (rc.11)
- Copilot: the orchestrator and security agents exceed GitHub's custom-agent body size. (rc.12)
- Repository scanning with `--project` can change the roster. (rc.12)
- macOS: only git gets a helper directory for an xcrun launcher, and an unresolved launcher alone warns but is
  not refused. (rc.13)
- Adopted agents can't be put under orchestrator-only on Codex, so a team with adopted agents keeps its Codex
  surface frozen for 1.0. (rc.14) #237
- `AGENTS.md` is not classed as a governance trust root: a clearance targeting it is classed by its other effects.
  Review such rows as you would a `CLAUDE.md` one. (rc.14) #237
- Revocation: proposals an agent staged before its MCP grant was revoked can still be applied with the
  orchestrator's approval. Reject them by hand. (rc.14)
- The prompt-injection evaluation (Experiment 1) runs after 1.0, under its approved test plan; it is not yet
  evidence for this release. Its findings ship as `v1.0.N` fixes or 1.1 changes. (1.0.0, release notes only)
- `.agentteams-backups/` is not added to `.gitignore` for you. Add it, and `git rm --cached -r` it if it was
  already committed. (rc.1)
- `--migrate` regenerates every agent file, so content added after generation is lost; recover it from the
  `pre-fencing-snapshot` tag. Use `--update --merge` for routine updates. (rc.1)
- `jsonschema` is a required runtime dependency, the one exception to the base install being stdlib-only. (rc.2)
- On `--update --merge` under the default `--shrink-policy preserve`, a fenced section whose enriched body would
  shrink keeps its old body, so a brief change may not reach it; check the shrink notices. (rc.7)
- The Tone and Style section names a fixed set of agents, some of which your team may not have. (rc.7)
- A confined command can be reported as failed (`undeclared_writes`) when the generated Claude hook writes
  `.claude/hook-notices/<date>.log` while the command runs. It is a false failure, never a false pass; re-run the
  command. Fixed in 1.0.1. (1.0.0, release notes only; M6-F7)

## [0.1.0] - 2026-04-15

### Added

- **Reference: Unix Philosophy Mapping for Code Hygiene Rules** — added `agentteams/templates/domain/unix-philosophy-mapping.template.md` and integrated into build pipeline. Each generated team includes `references/unix-philosophy-mapping.reference.md` mapping rules (CH-01 through CH-23) to Unix design principles. Three-tier classification: Tier 1 (foundational), Tier 2 (aligned), Tier 3 (project-specific). See audit report `tmp/by-week/2026-W20/unix-philosophy-mapping-audit-revisions.md`.

- `ingest` module: load project descriptions from `.json` or `.md` briefs; scan existing project directories to supplement missing fields
- `analyze` module: classify project type, select agent archetypes, detect tool agents, build team manifest
- `render` module: resolve auto and manual placeholders in agent templates; compute template hashes for drift detection
- `emit` module: write rendered agent files to disk with dry-run and overwrite-protection support
- `drift` module: detect content drift (template hash comparison) and structural drift (team composition changes) against build-log
- `scan` module: proactive security scan for PII paths, credentials, and unresolved placeholders
- `audit` module: post-generation static audit plus optional AI-powered review via `copilot` CLI
- `remediate` module: auto-correction support via standalone Copilot CLI after audit findings
- `graph` module: directed graph inference for agent team topology; outputs Mermaid, DOT, JSON, and Markdown
- `frameworks` package: `copilot-vscode`, `copilot-cli`, and `claude` framework adapters
- `build_team.py` CLI: 16-flag command-line interface wiring all pipeline stages
- Template library at the 0.1.0 release: 9 universal governance templates, 9+ domain archetype templates, 3 builder templates, 6 workstream expert-pattern templates
- JSON schemas: `project-description.schema.json` and `team-manifest.schema.json`
- Example project briefs: research, software, and data-pipeline projects
- `--self` mode: self-maintenance of the module's own agent team
- `--post-audit` mode: static + AI-powered conflict and presupposition review
- `--update` / `--prune` mode: incremental re-rendering with manual value preservation
