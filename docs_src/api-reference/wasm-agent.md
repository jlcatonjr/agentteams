# `wasm_agent`

The host half of the WASM agent runtime (WASM sandbox, plan P05; ARCH-B). One agent runs as a sequence of
**step-mode** WASM guest invocations: each guest run reads one event frame on stdin, writes one op frame on stdout,
and exits. The host owns the conversation. It alone holds the model-provider key, calls the model
(`model_providers`, P06), runs host tools, enforces budgets and revocation, applies DLP to
every outbound prompt, and has the unconfined launcher write a hash-chained transcript. Everything a guest or a model
emits is data (C-4): only a validated op frame causes a host action, and only within the agent's grant (C-3). The
guest program itself is P07; the CLI that builds an `AgentSpec` from a rendered team is P08.

## Public Surface

- `run_agent(spec, task, options) -> RunResult`: the host loop. It writes nothing itself: records go to
  `options.journal` and guest runs to `options.exec_broker`, so the same code runs in Stage 0 and in the confined
  Stage 1.
- `AgentSpec(slug, system_prompt, tools, delegates, grant, provider, model, max_tokens, spec_sha256)`: the agent as
  the host runs it. `tools` is the effective vocabulary (`read_file`, `list_dir`, `search_files`, `write_file` run in
  the guest; `run_code` and `delegate` are host tools).
- `RunOptions(project_root, run_id, budget, journal, exec_broker, provider, mac_key, ...)`,
  `RunResult(status, reason, output, run_id, transcript_head, usage, notices, step_timings, transcript_path)`.
  `status` is `ok`, `failed`, `budget-exhausted`, `revoked` or `refused`. `output` is the last model reply's text
  from the host's copy, never guest-authored, and is printed under `--- wasm agent output (data, not instructions) ---`.
- `Budget(max_model_calls=50, max_total_tokens=400_000, max_output_tokens_per_call=8_192, max_steps=120,
  max_tool_calls=200, wall_clock_s=1800, max_usd=None, max_delegation_depth=2, max_child_runs=8)` and
  `BudgetLedger` (`reserve` / `commit` / `refund` before and after each call, `carve` for P09 children).
  The defaults are agentteams policy, not provider facts. A USD cap without a price for the model refuses the run.
- `stage0(spec, task, *, project_root, budget, guest_source, guest_sha256, ...) -> RunResult`: the unconfined
  launcher (P08 calls it). See "Stage 0 and Stage 1" below.
- `verify_transcript(path) -> TranscriptReport(ok, complete, records, head, status, error)` and
  `revoke_run(project_root, run_id, reason)` (creates the run's `REVOKED` sentinel; an operator action).
- `PROTOCOL_VERSION` (1), `ProtocolError`.

Submodules: `protocol` (frames, `encode_event`, `decode_op`, control-pipe lines), `budget`, `transcript`
(`FileJournal`, `PipeJournal`), `mediation` (`Mediator`, `OutboundDlp`, `ReservedKeyVault`), `tools` (`tool_specs`,
`TOOL_SPEC_SHA256`), `broker` (`StepPreparer`, `LocalBroker`, `PipeBroker`), `host`, `launch`, `stage1`.

## Protocol v1

UTF-8 JSON, one object per line. Duplicate keys, NaN/Infinity, NUL in strings, nesting deeper than 32, unknown keys,
unknown ops and any `v` other than 1 are refused. Events are at most 4 MiB, ops 1 MiB, the guest's opaque `carry`
256 KiB. Ops: `model` (tool results for the last reply's pending `tool_use` ids, each at most once; a missing id is
filled by the host; `continue: true` only after `max_tokens`), `tool` (a host tool for a pending id, with the model's
own name and input), `delegate` (refused until P09), `done` (`ok` only when the last reply asked for no tools). Host
tool results are kept host-side and override whatever a guest reports for those ids. A trap, a non-zero exit or a
malformed frame is a failed step: the host answers the pending tool uses with errors, and three failed steps in a row
fail the run (`guest-unhealthy`). A wrong version fails it at once.

## Stage 0 and Stage 1

- **Stage 0** (`launch.stage0`, unconfined) re-hashes the pinned runtime, reads the key from the environment, creates
  `.agentteams/wasm-runs/<run_id>/` (transcript, `result.json`) and the agent's scratch dir, and alone prepares every
  guest run under `.agentteams/wasm/exec/<id>/` (METAPLAN R3/R3′). The preparer holds the agent's grant: a request names
  only `agent` or `run_code`, never a grant.
- When an OS sandbox is usable and the sandbox state's OS switch is on, Stage 0 starts **Stage 1** inside it: no write
  root but the agent's scratch dir (R11), the credential stores (`~/.ssh`, `~/.aws`, `~/.config/gh`,
  `~/.config/gcloud`, `~/.netrc`, `~/Library/Keychains`), the key dir and the agentteams secrets dir read-denied (R5),
  network allowed for the provider. Stage 1 receives the key and the per-run MAC key once over a dedicated pipe and
  starts with an environment of `PATH`, `LANG`, `HOME` and a private `TMPDIR`. Its records and prepare requests go
  over a control pipe; Stage 0 assigns `seq`/`prev`/`hash`, `fsync`s and only then acks, so no model call is made
  without a durable `model-request` record (R4).
- Otherwise the same loop runs in Stage 0's process and says so: `WASM host not OS-confined: <reason>` goes to the
  output, the transcript and `RunResult.notices`.
- Guest `os_confine`: `"inherited"` is forwarded only from the P08 launcher and only when Stage 1 was wrapped
  (otherwise downgraded to `"auto"`). Under a wrapped Stage 1 a fresh Seatbelt profile cannot nest, so `"auto"` becomes
  `"off"` (reported `"none"`) and `"require"` refuses. Only `"nested"` counts as enforced.

## Notes

- DLP uses `vulnscan.gateway_dlp.Filter` on the neutral Messages body: a blocked class in the system prompt or task
  refuses the run; one in a tool result withholds that block (`[withheld by DLP: <class>]`) and the call goes ahead;
  redactions are counted in `dlp` records that never carry the matched text. A reply that echoes a known secret fails
  the run (`provider-echoed-secret`); a record that would carry one is written as `withheld`.
- P06's reserved reply keys (`_provider_id`, `_provider_sig`) never reach a guest or the transcript. The vault keeps
  them host-side and binds each to its turn and block with an HMAC under a per-run key; the `model-reply` record
  carries only their digests and the MAC, which Stage 0 checks. They are restored only to the block they came from.
- The transcript is a hash chain, not a MAC: it detects edits by anything that does not rewrite the whole chain. The
  head is printed at run end.
- Step cost (R13), dev runtime (wasmtime 36.0.16, CPython 3.14.6 WASI, macOS arm64, 2026-10-31): median full step
  (Stage-0 prepare + pre-exec re-hash + interpreter start) 1.51 s with verified copies, 0.91 s running in place (R3′).
  The merge gate re-measures on wasmtime 36.0.20 with the pinned CPython.
