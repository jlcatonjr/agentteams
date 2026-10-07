# `git_exec`

The shared git core behind agentteams' private `_git` helpers (CH-08, 2026-10-07). The modules that kept their own
helper, or a copy of the hardening, still have a wrapper with its own return shape, but each builds and runs the
command here. Other one-off git calls (`git_hooks`, `shrink_allow`, `branch_inventory`, `output_target`,
`session_scan`, `code_index_artifacts`, `redteam`) are outside this consolidation.

## Read-only hardening

A read-only git command run in a repository an agent can write should not let that repository's config run code:
`core.fsmonitor` is executed by `status` and `ls-files`, and a hooks path can point anywhere. `hardened=True` adds
command-line overrides, which beat the repository's own config. They close those two paths, not every one: a
configured filter driver (`filter.<name>.clean`) can still run during `status` on a racily-clean file.

- `-c core.fsmonitor=false -c core.hooksPath=/dev/null -c core.untrackedCache=false`
- `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`, layered over the given environment

| Caller | Hardened | Why |
|---|---|---|
| `integrity` (manifest-tracked check) | yes | runs from the gate hook, outside the sandbox |
| `cli.operator_signing` | yes | signing reads an agent-writable repository |
| `cli.signer_location` | yes | the install-location check before the key is read |
| `proposals` (the runner's change snapshot) | yes, except `core.hooksPath` | the project is agent-writable; the snapshot resolves `--git-path hooks` to watch for a planted hook, so it must see the real hooks path. Safe only while `GIT_OPTIONAL_LOCKS=0` stops `status` writing the index (which would run `post-index-change`) |
| `source_provenance` | yes | read-only; runs on every render |
| `fleet` | no | the snapshot commits, and commit hooks are part of that contract |
| `multi_sync` | no | unchanged behaviour |

The module is integrity-pinned: the pinned `proposals`, `operator_signing`, `signer_location` and `integrity` import it.

## Public surface

| Name | Purpose |
|---|---|
| `git_argv(cwd, *args, hardened=False, override_hooks=True) -> list[str]` | Build the argv (`-C cwd` unless `cwd` is `None`). |
| `run_git(cwd, *args, hardened=False, env=None, timeout=None, text=True, devnull_stdin=False, run_cwd=None, override_hooks=True)` | Run it; returns the `CompletedProcess` and never raises on a non-zero exit. |
| `HARDENING_CONFIG`, `HARDENED_ENV` | The overrides above. |
