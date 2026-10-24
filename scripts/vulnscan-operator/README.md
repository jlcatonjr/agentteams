# vulnscan operator scripts

Run these on your own machine, in your own interactive terminal, in order. Each one does one stage of
the [setup walkthrough](../../docs_src/vulnscan-automation-walkthrough.md), skips what already exists,
and stops for you to check. None of them runs inside an agent session. There is no `04-*.sh`: Step 7
is `../vulnscan-agent-image.sh` and Step 7b is `../vulnscan-tools-image.sh`, both run between `03` and `05`
(the executor refuses `stage-fix`, `open-pr` and server audits until `tools-selftest` passes).

| Script | Walkthrough | What it does |
|---|---|---|
| `01-install.sh [REF]` | Before you start | `pipx install` a committed revision with `[signing]` (from `git archive`, outside every work tree) |
| `02-sandbox-repo.sh [NAME]` | (test setup) | a private GitHub repo with a known-vulnerable pin, cloned under `~/vulnscan-sandbox-root` |
| `03-configure.sh` | Steps 1, 4, 5, 6 | config, goose ops team in `~/ops-team`, your Ed25519 key (`KEY_ID`, default `op1`), OpenRouter key file (the `OPENROUTER_API_KEY` line of the agentteams checkout's gitignored `.env`, or `AGENTTEAMS_ENV_FILE`; else `~/.config/goose/goose-backend.sh`, goose's keychain entry, or a hidden-input paste) and the `agent` block (GLM 5.3, $2 budget, `enabled: false`) |
| `../vulnscan-agent-image.sh` | Step 7 | build the agent image (goose checked against GitHub's published digest) and run `agent-selftest`; needs Docker running |
| `../vulnscan-tools-image.sh` | Step 7b | build the tools image git, npm and the server kit run in (Node.js pin checked against its signed `SHASUMS256.txt`, base image by digest) and run `tools-selftest`; executor ops refuse until it passes |
| `05-first-run.sh` | Steps 8, 9 | sign a narrow grant (1 repo root, 1 day, 5 uses), `daily`, one supervised `agent-run`, `review`, spend |
| `../vulnscan-acceptance.sh` | Part D | read-only acceptance checks; report written outside any repository |
| `06-sigterm-check.sh` | Part D #9 | starts `agent-run`, sends SIGTERM after `DELAY` s, checks exit 143, no container left, status `agent-error` |
| `07-schedule.sh [--remove]` | Step 3 | dry run of the daily job, then (after you confirm) `schedule install --apply` and `schedule status` |
| `08-enable-agent.sh` | Step 10 | sign the unattended grant (`REPO_ROOT`, `DAYS`, `USES`; with two grants already valid it first offers to revoke the one that expires first, and stops if you decline), set `agent.enabled: true`, install the daily job at `HOUR:MINUTE` |
| `09-accept-framework.sh` | Using another framework | live acceptance of one runtime driver (`FRAMEWORK=`, default `claude`; `MODEL=`): switch `agent.framework`/`agent.model` (asks; keeps a backup), build that image and run `agent-selftest` (prints the checks and the CLI's tool inventory), sign a stage-fix-only grant (1 repo root, 1 day, 5 uses), `daily`, one supervised `agent-run`, then the outcome, spend and the gateway's refusal reasons |
| `10-accept-tools-container.sh` | Subprocess confinement | live acceptance of the tools container (`KEY_ID`, `NAME`): build + `tools-selftest` (prints every check), confinement not opted out, a confined `npm ping`, a narrow stage-fix + open-pr grant if none is active (1 repo root, 1 day, 4 uses), one confined `act stage-fix` on the sandbox repo (staged files owned by you), then an open-pr dry check (credentialed `git ls-remote` from the container, the repository gh would target) and, only if you answer y, the real `act open-pr` |

Your keys never pass through these scripts' output: the signing key is named only on the signing
command, and the OpenRouter key goes straight into a mode-600 file outside the
team directory and outside `~/.config/agentteams`.

Afterwards: `gh repo delete <you>/vulnscan-sandbox`, `rm -rf ~/vulnscan-sandbox-root`, and
`python -P -m agentteams.vulnscan grant revoke --grant-id <grant_id>` if the grant is still active.
