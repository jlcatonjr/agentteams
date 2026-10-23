# vulnscan operator scripts

Run these on your own machine, in your own interactive terminal, in order. Each one does one stage of
the [setup walkthrough](../../docs_src/vulnscan-automation-walkthrough.md), skips what already exists,
and stops for you to check. None of them runs inside an agent session.

| Script | Walkthrough | What it does |
|---|---|---|
| `01-install.sh [REF]` | Before you start | `pipx install` a committed revision with `[signing]` (from `git archive`, outside every work tree) |
| `02-sandbox-repo.sh [NAME]` | (test setup) | a private GitHub repo with a known-vulnerable pin, cloned under `~/vulnscan-sandbox-root` |
| `03-configure.sh` | Steps 1, 4, 5, 6 | config, goose ops team in `~/ops-team`, your Ed25519 key (`KEY_ID`, default `op1`), OpenRouter key file (hidden input) and the `agent` block (GLM 5.3, $2 budget, `enabled: false`) |
| `../vulnscan-agent-image.sh` | Step 7 | build the agent image (goose checked against GitHub's published digest) and run `agent-selftest`; needs Docker running |
| `05-first-run.sh` | Steps 8, 9 | sign a narrow grant (1 repo root, 1 day, 5 uses), `daily`, one supervised `agent-run`, `review`, spend |
| `../vulnscan-acceptance.sh` | Part D | read-only acceptance checks; report written outside any repository |

Your keys never pass through these scripts' output: the signing key is named only on the signing
command, and the OpenRouter key is read with hidden input straight into a mode-600 file outside the
team directory and outside `~/.config/agentteams`.

Afterwards: `gh repo delete <you>/vulnscan-sandbox`, `rm -rf ~/vulnscan-sandbox-root`, and
`python -P -m agentteams.vulnscan grant revoke <grant_id>` if the grant is still active.
