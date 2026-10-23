# Automated Remediation: Step-by-Step Setup

This walkthrough takes you from nothing to an agent team that scans your repositories and servers
every day and remediates what it finds, unattended, under grants you sign. You review what it did
afterwards.

This page is the procedure. Every command, flag, file and contract is in the
**[vulnscan API reference](api-reference/vulnscan.md)**; everyday use is in the
**[user guide](vulnerability-remediation-guide.md)**.

!!! info "About the screenshots"
    vulnscan has no GUI; its screens are terminal sessions. Each screenshot is the real output of
    the command shown, captured by `scripts/gen_vulnscan_screenshots.py` in a throwaway demo
    account: two small repositories under `~/code`, one host named `web1` (an `example.com`
    address), a demo signing key, and a demo scan result (the real `daily` needs network access to
    OSV.dev). Paths are shortened to `~`, and long outputs are cut where marked. Ids, digests and
    times differ on your machine. Two demo shortcuts are disclosed where they occur; neither is a
    supported way to run vulnscan.

![Three zones: your account (scan, executor, checks, ledger, you), the agent container (goose team, MCP server, request spool, egress gateway), and outside services (GitHub, servers, the private ops repo, the model API). Requests flow from the agents through the spool to the executor, which alone reaches GitHub and the servers.](book/figures/vulnscan/vulnscan-architecture.svg)

**The shape of it.** The agents run in a container that holds no credentials. They read today's
scan and *ask* for actions by writing requests into the **spool**, the only directory they can
write besides their own session files. An **executor**, running as you, performs a request only
when:

- your signed **grant** covers that operation on that target;
- no **HALT** (a `@security` verdict recorded in your team's decision log) is open;
- no **escalation** (an agent's stop request that blocks a target until you resolve it) covers it.

Every decision is recorded in a tamper-evident **ledger**, and the review you read is built from it.
The [architecture section of the reference](api-reference/vulnscan.md#architecture) has the full
component table.

---

## Before you start

| You need | Why | Check |
|---|---|---|
| Python 3.11+ and `agentteams` | runs everything | `python -m agentteams.vulnscan --help` |
| An `agentteams` **source checkout** of the release you run | the agent image is built from it; the key helper script lives in it | `git -C <checkout> describe --tags` |
| Network access to OSV.dev, PyPI, nodejs.org, www.php.net, endoflife.date | the scan reads live advisories and release data | `curl -sI https://api.osv.dev` |
| git | scans and fixes | `git --version` |
| npm, if any repository has a `package-lock.json` | `stage-fix` updates npm lockfiles with `npm audit fix --package-lock-only` (it contacts the npm registry) | `npm --version` |
| GitHub CLI, signed in | `open-pr` pushes a branch and opens a pull request | `gh auth status` |
| ssh access to your servers, non-interactive, as root or with passwordless `sudo` | server audits run with `BatchMode=yes` and `sudo -n` | `ssh -o BatchMode=yes <host> sudo -n true` |
| Docker (Docker Desktop on macOS, Docker Engine on Linux) | the agent container and its gateway | `docker info` |
| An OpenRouter API key, or a local route proxy | the agents' model | — |
| openssl | to install your public key | `openssl version` |

Install `agentteams` somewhere an agent cannot edit, such as `pipx install` from a release you
verified. Signing refuses to run from an editable checkout or a virtual environment inside a git
work tree (see [Step 8](#step-8-sign-a-narrow-first-grant)).

The setup has three parts. Part A gives you a scheduled, deterministic scan with no agents; you can
stop there. Part B adds the agent team. Part C is the routine once it runs.

---

## Part A — Daily scanning

### Step 1: Write the config

```bash
python -m agentteams.vulnscan init --repo-root ~/code
```

`--repo-root` can be repeated. The config lives in your user config directory, never in a
repository.

![init prints the config path; the file holds every top-level default](book/figures/vulnscan/02-init.svg)

*Demo account. `init` never overwrites an existing config.*

### Step 2: Add your servers (optional)

```bash
python -m agentteams.vulnscan server-kit
```

![server-kit lists the exported scripts and config files](book/figures/vulnscan/03-server-kit.svg)

*Demo account, before its first daily run: the last line is the warning every command prints until
a daily run completes.*

Edit `~/.config/agentteams/server-kit/hosts.conf`, one line per host (the examples arrive commented
out, so no example host is ever live):

```text
web1 | deploy@web1.example.com | Ubuntu; nginx + Python app
```

Then list the host names under `"servers"` in `~/.config/agentteams/vulnscan.json`. The name
(`web1`) is what grants and escalations use. Test one audit by hand:
`~/.config/agentteams/server-kit/remote.sh web1 L1-audit.sh`.

The daily job and the executor run the scripts packaged with agentteams, reading your `hosts.conf`
and `apps.conf` from this directory. The exported copies are for running by hand; editing them
changes nothing the daily job runs. `ioc-*.txt` files you add here are embedded in each audit as
data, next to the packaged ones.

### Step 3: Run once, then schedule it

```bash
python -m agentteams.vulnscan daily
```

The run writes `~/.local/state/agentteams/vulnscan/<date>/summary.md`:

![summary.md: one table per repository with severity, installed version and the upgrade](book/figures/vulnscan/04b-summary.svg)

*Demo findings, written through the real report code. One advisory id is real
(GHSA-ffqj-6fqr-9h24, PyJWT); `DEMO-0001` is not.*

Install the daily job. Without `--apply` it only shows what it would do:

```bash
python -m agentteams.vulnscan schedule install            # dry run
python -m agentteams.vulnscan schedule install --apply
```

![schedule install prints the launchd plist and the launchctl commands it would run](book/figures/vulnscan/04-schedule.svg)

*macOS shown (launchd). Linux gets a systemd user timer, or a crontab line without systemd.
`<python>` stands for the interpreter running vulnscan.*

`python -m agentteams.vulnscan schedule status` confirms it. From now on, every command warns when
the last daily run is older than 26 hours.

---

## Part B — Agent mode

### Step 4: Generate a goose team with the remediation agent

Pick a directory for your ops team, for example `~/ops-team`, and put a brief there whose
`capabilities` include `vulnerability_remediation`:

```json
{ "capabilities": ["vulnerability_remediation"] }
```

Then render a goose team from inside that directory:

```bash
cd ~/ops-team
agentteams --description brief.json --framework goose --project .
```

The capability is opt-in; agentteams never infers it from the brief's prose. The team gets a
`vuln-remediation` sub-recipe, wired into the orchestrator:

![the recipe list includes vuln-remediation.yaml, and orchestrator.yaml references vuln_remediation](book/figures/vulnscan/05-team.svg)

*Demo team from `examples/software-project/brief.json` plus the capability. Offline, the
security-intelligence freshness gate refuses to render (correctly), so the demo stubbed that one gate
as the test suite does. Do not do this: a real run fetches live intelligence.*

You need two paths later:

- **`team_dir`**: the project root (`~/ops-team`). The container runs
  `.goose/recipes/orchestrator.yaml` from it.
- **The governance directory**: the team directory holding `references/security-decisions.log.csv`,
  for a goose team `~/ops-team/.goose/recipes`. The executor checks it for HALTs before every action.

### Step 5: Provision your operator key

Grants and resolutions are signed with your Ed25519 key. Run these in your own interactive
terminal, never in an agent session. Replace `<key-id>` with a short name, such as `op1`. The helper
is a shell script around `openssl`; it runs no agentteams code.

```bash
<agentteams checkout>/references/authorized-verify-keys/provision-operator-signing-key.sh \
    --team-dir ~/ops-team/.goose/recipes <key-id>
```

It writes the private key to `~/.config/agentteams/keys/decision-signing-<key-id>.pem` (mode 600;
every sandbox agentteams emits denies reads of that directory) and the public key into the team.
vulnscan reads public keys from a different place, so install the public key there too. It must be
yours and not group- or world-writable:

```bash
mkdir -p ~/.config/agentteams/verify-keys
openssl pkey -in ~/.config/agentteams/keys/decision-signing-<key-id>.pem -pubout \
    -out ~/.config/agentteams/verify-keys/<key-id>.pub.pem
chmod 644 ~/.config/agentteams/verify-keys/<key-id>.pub.pem
```

!!! danger "Name the private key per command, never in a profile"
    Signing reads the key file named by `AGENTTEAMS_DECISION_ED25519_KEYFILE`. Set it on the signing
    command itself, as the commands below do, so no later process in that shell (an agent
    session, a goose or claude run) inherits it. Never put it in a shell profile, and never copy the
    private key elsewhere.

### Step 6: Configure agent mode

Add the `agent` block to `~/.config/agentteams/vulnscan.json`, with `enabled` off for now:

```json
"agent": {
  "enabled": false,
  "team_dir": "~/ops-team",
  "key_file": "~/.config/agentteams/openrouter.env",
  "upstream": "https://openrouter.ai",
  "model": "z-ai/glm-5.3",
  "budget_usd": 2.0,
  "max_minutes": 45
}
```

- `key_file` holds one line, `OPENROUTER_API_KEY=...`. Only the gateway container mounts it.
- `upstream` defaults to a local route proxy at `http://host.docker.internal:8791`. Without one, set
  `https://openrouter.ai` as above.
- The gateway relays only this `model` and refuses calls once the reported spend reaches
  `budget_usd`, once `max_tokens_total` tokens (default 10 000 000) have been used, or after 20
  successful replies that carry no usage or cost (`max_unmetered_calls`; error replies such as rate
  limits are counted separately). If your route proxy does not pass usage through, either make it
  do so or set `"allow_unmetered": true` knowingly (see
  [what leaves your machine](api-reference/vulnscan.md#what-leaves-your-machine)).
- `team_dir` must be an existing directory that is not your home directory, an ancestor of it, or
  anything at, inside or above `~/.config/agentteams` or another credential directory, and it must
  not contain `key_file` (keep the OpenRouter key file outside the team directory, as above) or your
  signing key. It is mounted into the container, so the run and the self-test refuse anything else.

### Step 7: Build the agent image and prove its isolation

Take the goose version and the sha256 of its `goose-x86_64-unknown-linux-gnu.tar.bz2` (or the
`aarch64` file on Apple silicon) from the [goose releases page](https://github.com/block/goose/releases).
Pin the base image by digest.

```bash
python -m agentteams.vulnscan agent-image build --source <agentteams checkout> \
    --goose-version <X.Y.Z> --goose-sha256 <sha256> \
    --python-image python:3.12-slim@sha256:<digest>
python -m agentteams.vulnscan agent-selftest
```

A sha256 mismatch fails the build. The self-test starts the gateway and runs the container with the
agent's exact flags, then prints `{"passed": ..., "checks": {...}}`. Every check must be `true`; the
reference [lists what each one proves](api-reference/vulnscan.md#selftestcfg). Agent mode refuses to
run until a passing self-test exists **for the current image and the current container flags**:
after every image rebuild, and after an agentteams upgrade that changes how the containers are
started, re-run `agent-selftest` (the run's refusal says which).

### Step 8: Sign a narrow first grant

The first grant lets the agents stage fixes in **one** repository for **one** day, and do nothing
else. They cannot push or open pull requests yet.

```bash
AGENTTEAMS_DECISION_ED25519_KEYFILE=~/.config/agentteams/keys/decision-signing-<key-id>.pem \
python -m agentteams.vulnscan grant sign --key-id <key-id> --days 1 --max-uses 5 \
    --op stage-fix --repo-root ~/code/shop-api \
    --governance-dir ~/ops-team/.goose/recipes
```

What you see, in order:

1. any warnings from the integrity and install-location checks (signing **refuses** to run from an
   `agentteams` an agent could edit, such as an editable install in a git work tree);
2. the banner *"About to sign a STANDING vulnerability-remediation grant…"* and every field the
   signature binds;
3. the `review sha256` line, then the prompt `Sign this payload? [y/N]`.

Answer `y`. The grant is verified against your public key and installed:

![grant list prints the installed grant as JSON: stage-fix only, one repository root, one day, five uses](book/figures/vulnscan/06b-grant-list.svg)

*Demo account. The capture was made inside a development checkout, where signing correctly refuses
(the install-location check), so the demo signed an identical grant through the Python API. That
bypasses the integrity, location and confirmation checks; it is not a supported way to sign.*

!!! tip "Signing without a terminal"
    Without a terminal, signing prints everything above and stops at `no terminal to confirm on`.
    Check what it printed, then run the same command again with
    `--confirm-review-sha256 <the printed digest>`. The digest covers what you chose (operations,
    roots, hosts, uses, lifetime, key, governance directory), not the grant id and times each run
    mints, so any change to your choices is refused. A digest confirms those choices whenever it is
    used again; it is not a one-time token. Paste only a digest you read off a run you started:
    anyone who hands you a ready-made command is asking you to skip the review.

### Step 9: Run once, supervised

```bash
python -m agentteams.vulnscan agent-run
```

`agent-run` works on the latest scan. While it runs, the executor serves the agents' requests every
15 seconds: escalations first, then the agent's report, then granted operations.

![the executor's results for four requests: escalate done, report stored, server-audit refused (blocked by the escalation), stage-fix done with a vulnscan branch](book/figures/vulnscan/07-serve-requests.svg)

*Demo: the agent's requests went through the real MCP server class and were served by the real
executor with the run's directory, as `agent-run` does. The escalation on `web1` blocks the
`server-audit` request (the grant does not cover `server-audit` either; the escalation check runs
first). `stage-fix` staged a local branch; nothing was pushed.*

Then read the review. It is built from the ledger, not from the agent's account of itself:

```bash
python -m agentteams.vulnscan review
```

![the review lists open escalations, the grant, actions taken, refused requests and findings not acted on](book/figures/vulnscan/11-review.svg)

*Demo account; the output is cut where marked (the agent's stored report follows).*

Read it end to end, and look at the staged change before going on: `stage-fix` commits it in an
executor-owned worktree under `~/.local/state/agentteams/vulnscan/worktrees/`, on the branch the
review names (`git -C <that worktree> show`). Then record that you reviewed it:

```bash
python -m agentteams.vulnscan review --ack --note "first supervised run"
```

### Step 10: Widen the grant, then switch it on

Only after a run you are satisfied with, sign the grant you want for unattended runs. Name each
repository root explicitly rather than a parent of everything you have cloned: a root covers every
repository inside it, and you do not want the agents proposing changes to third-party clones, your
ops team or the agentteams checkout. The executor also refuses `stage-fix` and `open-pr` in the
repository of the agentteams installation it runs from, of `agent.team_dir`, of every grant's
governance directory and of the publish ops repository (worktrees included), whatever the grant says.

```bash
AGENTTEAMS_DECISION_ED25519_KEYFILE=~/.config/agentteams/keys/decision-signing-<key-id>.pem \
python -m agentteams.vulnscan grant sign --key-id <key-id> --days 14 --max-uses 50 \
    --op stage-fix --op open-pr --repo-root ~/code/shop-api --repo-root ~/code/billing \
    --governance-dir ~/ops-team/.goose/recipes
```

Add `--op server-audit --op server-dryrun --host web1` to let the agents audit and dry-run servers.
`merge` and `server-apply` are refused: no grant can cover them. At most two grants may be valid at
once (a third makes all of them invalid), so revoke the first one:

```bash
python -m agentteams.vulnscan grant revoke --grant-id <id of the first grant>
```

`open-pr` pushes a new `vulnscan/*` branch to the repository's `origin` and opens the pull request
on that same repository. Before pushing anything, it checks that `origin` has the form
`HOST/OWNER/REPO` (`https://`, `git@` or `ssh://`); otherwise the request fails. gh must be signed in
to that host.

Finally set `"enabled": true` in the `agent` block. From then on, every daily run ends with an agent
run.

---

## Part C — Operating the loop

### When something blocks

A notification says *"Escalation ve-… (other) opened"* or *"A @security HALT blocked
remediation"*. Ask what blocks:

```bash
python -m agentteams.vulnscan halts
```

![halts lists one open escalation with its resolve command; the HALT section says none; exit 1](book/figures/vulnscan/08-halts.svg)

*Demo account. `[exit 1]`: something blocks.*

- **An escalation of kind `other`:** check the evidence, then resolve it. You confirm at the
  terminal:

  ```bash
  python -m agentteams.vulnscan resolve ve-<id> --evidence "operator's own cron job (checked)"
  ```

- **A serious escalation** (`malicious`, `ioc`, `injection`, `integrity`, `escalation-flood`): treat
  it as an incident first. To resume, resolve it with your signature:

  ```bash
  AGENTTEAMS_DECISION_ED25519_KEYFILE=~/.config/agentteams/keys/decision-signing-<key-id>.pem \
  python -m agentteams.vulnscan resolve ve-<id> --evidence "<what you checked>" --key-id <key-id>
  ```

- **A `@security` HALT:** `halts` prints a `HALT-RETRACTED` spec. Fill in the evidence, save it, and
  sign it with `agentteams --sign-decision <spec.json> --output <governance dir>`. Nothing else
  clears a HALT.

### Talking to the agents

Leave a note for the next run. It is folded into the agents' task; it cannot widen the grant, and
the agent's instructions say a note never suppresses an escalation.

```bash
python -m agentteams.vulnscan say "skip billing this week: it is being migrated" --days 7
python -m agentteams.vulnscan say --list
```

![say prints the note id; say --list shows the note with its expiry](book/figures/vulnscan/10-say.svg)

*Demo account.*

- **From a pull request:** comment `/vulnscan note <text>` on one of the agents' pull requests, then
  import it with `say --from-gh <comment URL>`. Only your own comments are accepted, and you see the
  full text before it is stored. `review --gh-replies` lists the replies on the agents' pull
  requests as data.
- **Ask a past run why:** `agent-chat [--run <date>]` resumes that run's goose session in the
  container. The spool is read-only there, so it can explain but not act. It has its own spend, time
  and token caps (`agent.debrief_*` in the
  [configuration reference](api-reference/vulnscan.md#configuration-vulnscanjson)).

### Reports, and publishing them (optional)

The agent submits a report at the end of each run; the executor stores it, fenced, under
`<run>/agent-report/report.md` and shows it in `review`. Without a publish grant it stays local:

![publish-grant list prints nothing; publish says no active publish grant and exits 1](book/figures/vulnscan/12-publish-none.svg)

*Demo account. `[exit 1]`: no publish grant.*

To have the executor push each run's report, review and summary to a **private** repository you
own, create that repository, clone it, and sign a publish grant:

```bash
AGENTTEAMS_DECISION_ED25519_KEYFILE=~/.config/agentteams/keys/decision-signing-<key-id>.pem \
python -m agentteams.vulnscan publish-grant sign --ops-repo ~/ops-reports \
    --node-id "$(gh repo view <owner>/ops-reports --json id --jq .id)" \
    --owner-id "$(gh api users/<owner> --jq .id)" \
    --governance-dir ~/ops-team/.goose/recipes --key-id <key-id> --days 30
```

Before it signs, and again before every push, vulnscan checks that the repository is private, owned
by you, has GitHub Actions and Pages off, is the exact remote you pinned, and has no other
collaborator, no pending invitation and no deploy key. Any failure to check refuses. GitHub Apps and
tokens with access to the repository are not visible to these checks. Files go to `reports/<date>/<run id>/` on the `reports` branch, never
force-pushed. Publishing runs at the end of each daily run while agent mode is on; `publish
[--run <date>]` publishes by hand. Agent text in the files is fenced, and home paths, this machine's
hostname and private IP addresses are scrubbed.

### Your routine

| When | Do |
|---|---|
| A notification arrives | `halts` if it names an escalation or a HALT, else `review` |
| A few times a week | `review`, then `review --ack` |
| Before a grant expires | revoke the old one, then sign a new one (at most two are valid) |
| A new disclosure wave | nothing extra: the next daily run reads live advisories |

---

## Troubleshooting

Search the message for the quoted text.

| You see | Cause | Fix |
|---|---|---|
| `not authorized: ... no valid grant covers it` | no grant names that operation and target, or its uses are spent | `grant list`; sign a grant that covers it |
| `blocked by open escalation ve-...` | an open escalation covers the target | `halts`, then `resolve` |
| `HALT in force: ...` | a `@security` HALT on `vulnscan-remediation` | sign a `HALT-RETRACTED` row (see `halts`) |
| `decision log ... is missing or a symlink` | the grant's governance directory has no decision log | sign a grant naming the team directory that has `references/security-decisions.log.csv` |
| `set agent.key_file in vulnscan.json first` | `agent-selftest` before Step 6 | do Step 6, then re-run |
| agent run state `not-run`, reason mentions the self-test | no passing self-test for this image | `agent-selftest` |
| `refusing to sign from an install an agent may be able to edit` | `agentteams` runs from a git work tree | sign from a verified install outside agent write roots. `--allow-checkout-signing` overrides this, but then you sign with code an agent may have edited |
| `no terminal to confirm on` (suggesting `--confirm-review-sha256`) | signing ran without a terminal | check the printed fields, then re-run with `--confirm-review-sha256 <digest>` (see Step 8) |
| `--confirm-review-sha256 does not match` | your choices differ from the run that printed the digest | re-run without it and read the new digest |
| `agent.team_dir ... ` (run state `not-run`) | `team_dir` is unset, relative, not a directory, your home or an ancestor, a credential directory, or contains a key file | set it to the team's project directory and keep key files outside it (Step 6) |
| `the container flags or the self-test changed` / `predates container-flag binding` | agentteams changed how containers start since the last self-test | `agent-selftest` |
| `run token cap reached` / `replies carried no usable usage/cost` | the gateway's backstops (Step 6) | raise `max_tokens_total`, or fix usage reporting upstream |
| `is the same repository as` (a refused `stage-fix` / `open-pr`) | the target is the agentteams checkout, the team, a governance directory or the ops repository | leave it out of the grant |
| `no operator verify key ...` | the public key is not in `~/.config/agentteams/verify-keys/` | repeat the `openssl pkey` command in Step 5 |
| `verify key ... is not owned by you or is group/world-writable` | the public key's mode (your umask) | `chmod 644` it |
| `origin is not a GitHub repository URL` | `open-pr` on a repository whose `origin` URL is not `HOST/OWNER/REPO` | give it a GitHub `origin`, or leave it out of `open-pr` grants |
| `gh pr create failed` | gh is not signed in to `origin`'s host, or `origin` is not on GitHub | `gh auth status`; leave non-GitHub repositories out of `open-pr` grants |
| `holds the run lock` | an agent run or `agent-chat` is in progress | wait for it to finish |
| `ledger integrity failure` | the ledger was edited, truncated or deleted | see below |

**A ledger integrity failure** refuses every action until it is resolved. Treat it as an incident:
something changed a file only you and the executor should write. Do not delete anything in
`~/.config/agentteams/keys/`; your signing key lives there. To start over, revoke every grant first,
then move `~/.local/state/agentteams/vulnscan/ledger.jsonl` and
`~/.config/agentteams/keys/vulnscan-ledger.hwm` aside (keep them for investigation). A new ledger
forgets every open escalation and every grant's use count, which is why the grants must go first.
Then sign new, narrow grants.
