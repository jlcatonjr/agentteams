# Working with agentteams: branches, releases and contributions

New here? This page explains which branch changes go to and which tag to install. To start contributing,
see [Contribute to agentteams](contribute/index.md) first.

**For:** collaborators: Kevin Vallier (`@selfreferencing`), John Schuler (`@jsschuler`) and anyone else contributing.
agentteams, researchteam, mathAgents and baseAgent share one release policy, and this page summarizes it for
agentteams. Each of the other three restates it in its own `docs/release-policy.md`. Signing and verification
details are in `SECURITY.md`.

## The model in brief

- **`dev`** is where development happens, for the next minor version (today `1.3.0.dev0`). Every change except an
  urgent hotfix lands there first: contributors through a pull request into `dev`, Jim by merging locally. `dev`
  triggers no CI on push.
- **`main`** is the latest release. After each release, one **main-to-vX.Y.Z pull request** brings `main` up to that
  release's tag commit (a merge commit, never squashed). `dev` is never merged into `main` directly.
- **A hotfix** is the one exception. An urgent fix can reach `main` by a hotfix pull request, and `main` is then merged
  into `dev`. Apart from a hotfix, `main` carries only released code, so a local install of `main` (for example
  `pip install -e .`) and the docs site show released behaviour.
- **Releases are signed tags,** `v1.2.0`, `v1.2.1`, …, and they are what users install and pin.
- **`release/X.Y`** is a release line. It is cut from `dev` at the minor version's first release candidate, and it
  then takes only reviewed fixes for that line: each fix lands on `dev` first and is copied over with
  `git cherry-pick -x` (a *backport*). Its commits are unsigned between tags, so it is not for installing or pinning.
- **Each tag is cut on its release line** and signed by Jim.
- **After a final or patch release, the line is merged forward** twice: into `main` (the main-to-vX.Y.Z pull request),
  and into `dev`, so `dev` contains everything released. That second merge is a *forward-merge* pull request, which
  keeps `dev`'s own version number, as #566 and #582 did for v1.2.1 and v1.2.2.

## Branches and tags

| Ref | What it is | Who changes it |
|---|---|---|
| `dev` | Development, the next minor version | Invited contributors, through a pull request; Jim, by local merge |
| `main` | The latest signed release | Jim only, by a main-to-vX.Y.Z pull request or a hotfix pull request |
| `release/1.2` | The current release line: reviewed fixes for the next 1.2 patch; unsigned between tags; not for pinning | Jim only, by backport from `dev` (or `main`, for a hotfix) |
| `release/1.1`, `release/1.0` | Older lines: fixes only on request | Jim only, by backport |
| `vX.Y.Z`, `vX.Y.Z-rc.N` | One exact release, signed by Jim, never moved; what you install and pin | Jim only |
| Your work branch (`feat/…`, `fix/…`, `docs/…`, `chore/…`) | One change, deleted after merge | Its author |

Today `main` and `release/1.2` are at `v1.2.2`, and `dev` is `1.3.0.dev0`. The older lines' latest tags are `v1.1.4`
and `v1.0.7`. `release/1.3` will be cut from `dev` at `v1.3.0-rc.1`.

The name `release/<X.Y>` is reserved for maintained lines. Never create a `release/*` branch, locally or on GitHub.

## Using agentteams

Install the latest signed release tag: today `v1.2.2`.

```bash
pip install "agentteams @ git+https://github.com/jlcatonjr/agentteams.git@v1.2.2"
```

**Verified install,** for a governed team, an experiment or a paper. Clone the tag into a directory of its own, check its signature with repository config neutralised, then install from that checkout:

```bash
git clone --branch v1.2.2 https://github.com/jlcatonjr/agentteams.git agentteams-v1.2.2
cd agentteams-v1.2.2
git -c gpg.format=ssh -c gpg.ssh.program=/usr/bin/ssh-keygen \
    -c gpg.ssh.allowedSignersFile=$HOME/.config/agentteams/allowed_signers \
    -c gpg.program=/usr/bin/gpg -c core.fsmonitor=false -c core.hooksPath=/dev/null \
    tag -v v1.2.2 && pip install .
```

The `&&` stops the install if the signature check fails. The output must also show the release key's full fingerprint, which begins `SHA256:IPdnLwlF`, not just "Good signature"; if it doesn't, stop and uninstall. "Verifying a release" below explains how to set up `allowed_signers` once. For a patch release, repeat this with the new tag in a new directory.

To sign decisions or grants, follow `SECURITY.md` ("Signing from a trusted install"): install with `pipx`, from that verified checkout, outside every agent write root. The [migration guide](migration-guide.md) ("Moving to a release checkout") covers rendering a governed team from a tag checkout.

**Release candidates** (`vX.Y.Z-rc.N`) are signed tags too. When you are asked to trial one, install it the same way,
from the tag. Don't install it from `main`, which stays at the previous release until the final.

**Release branches are not install sources.** `release/*` lines hold reviewed fixes queued for the next patch release. Those commits are unsigned between tags, so don't install from the branch or pin it.

**For development,** work on `dev` in a clone of your fork, and install it with `pip install -e .`. "Contributing a change" below covers setting up the fork.

**Pins between repositories** (`toolchain.lock`, pinned extras) always name the commit of a verified signed tag, never
a branch.

## Contributing a change

agentteams is public, and you have read access only. Pull requests are currently by invitation: start from an issue on which the maintainer has invited one (see [Contribute to agentteams](contribute/index.md)). Contribute from a fork; Jim reviews and merges your pull request.

1. **Set up once.** Fork the repository on GitHub, then clone your fork and add the original as `upstream`:

   ```bash
   git clone https://github.com/<your-login>/agentteams.git && cd agentteams
   git remote add upstream https://github.com/jlcatonjr/agentteams.git
   ```

2. **Start each change from an up-to-date `dev`:**

   ```bash
   git fetch upstream
   git switch dev 2>/dev/null || git switch -c dev --track upstream/dev   # the first time
   git pull --ff-only upstream dev
   ```

3. **Branch from `dev`:** `git switch -c fix/<short-topic> dev`. Use `feat/`, `fix/`, `docs/` or `chore/`. Keep one change per
   branch.
4. **Push to your fork and open a pull request** against `jlcatonjr/agentteams` `dev` (not `main`):

   ```bash
   git push -u origin fix/<short-topic>
   ```

5. **CI.** `ci.yml` runs on every pull request into `dev`, `main` or a `release/*` line and must pass. macOS jobs run on pull
   requests, not again on the push that merges one. Before you push, run `python -m pytest tests/ -q`. If you changed a CLI flag or help text, also regenerate the man page with `python -m agentteams.man > agentteams.1` ([What your pull request needs](contribute/what-your-pr-needs.md#if-you-change-the-public-api-or-cli)).
6. **Merge.** Jim reviews and merges. You can't merge into this repository.
7. **Delete your branch after it merges.**

## Getting a fix into a release

1. **Land the fix on `dev` first,** as above. It reaches the next minor release with the rest of `dev`.
2. **Ask for a backport** if it should ship sooner, in a patch on a release line. Comment on the merged pull request,
   naming the line and the reason, for example "please backport to 1.2: fixes a refused install". Jim adds the
   label (`needs-backport-1.1` or `needs-backport-1.0`, where the line has one). Only these qualify:
   - security fixes;
   - correctness fixes;
   - documentation fixes.

   New features wait for the next minor release.
3. **Jim does the rest.** He copies the fix to the release line in his own pull request, using `git cherry-pick -x`
   and never a squash. When he releases, he signs `vX.Y.Z`, and the line is merged forward into `main` and `dev`.
4. **A fix that applies only to a release line** (the code is already gone from `dev`): open an issue describing it,
   and Jim fixes it on the line directly.
5. **Urgent, for example a security fix?** Say so in the pull request and message Jim. He decides whether it ships as a
   patch or a hotfix.

## What only Jim does

- Pushing or merging to any `release/*` branch.
- Creating a `release/*` branch.
- Creating, moving or deleting tags.
- Signing a release.
- Moving a cross-repository pin.
- Signed decision rows and the security-decision logs.
- Re-enabling autosync.

If you work through this repository's agent team (Claude, Copilot, Codex or Goose), the agents will stop and ask
before any of these actions. In your own session, they are asking *you*: answer no, and contact Jim.

## Please don't

- Commit straight to `dev`, `main` or `release/*`. Use a branch and a pull request into `dev`.
- Open a pull request into `main`. Only the main-to-vX.Y.Z and hotfix pull requests go there.
- Force-push `main` or `release/*`, or delete or move a tag. A defect is fixed forward, in a new patch release.
- Put a branch name into `toolchain.lock` or a pinned extra.

## Enforcement

GitHub enforces the release rules with five active rulesets:

1. only an admin can create `release/*` branches;
2. `release/*` lines take changes only through a pull request with passing checks, with no force-push and no deletion;
3. `release/1.2`, the current line, has its own copy of rule 2;
4. only an admin can create `v*` tags;
5. `v*` tags can't be moved or deleted.

`main` is also protected: its merges need the required checks to pass, for administrators too.

## How a release happens

You only need this for context:

1. **Fixes accumulate on `dev`.** The ones requested for a patch are copied to the current release line with
   `git cherry-pick -x`.
2. **A patch release.** Jim bumps the version on the release line in a pull request, signs `vX.Y.Z` from his own
   terminal, and publishes the GitHub release. The line is then merged forward into `main` and `dev`.
3. **A new minor version:**
   - **Freeze.** New features stop landing for that version, and `dev` passes the full test and docs checks.
   - **Cut.** Jim creates `release/X.Y` from that `dev` commit, and `dev` moves on to the next development version.
   - **Release candidates.** Each `vX.Y.0-rc.N` is tagged on the line through its own release-commit pull request.
   - **Soak.** Downstream repositories trial each candidate, installed from its signed tag. Fixes found there land
     on `dev` first and are copied to the line.
   - **Final.** `vX.Y.0` is a version-and-CHANGELOG-only commit on the line, signed like any release. Then the line is
     merged forward into `main` and `dev`, as for a patch.
4. **Repositories release in order:** agentteams → researchteam → mathAgents → baseAgent. Each one moves its pins only
   after verifying the upstream tag's signature.
5. **Older lines** take fixes only on request once the next line has its final release.

## Verifying a release

Each tag is SSH-signed with Jim's release key. Its fingerprint begins `SHA256:IPdnLwlF`; the full fingerprint and the
`allowed_signers` line are in agentteams `SECURITY.md`, under "Release signers". Read them on GitHub, not from a
copy inside the repository you are checking, or confirm the fingerprint with Jim.

1. On your own machine, add that line to your own `~/.config/agentteams/allowed_signers`. (Agents never write this
   file; it's yours.)
2. Run this from a checkout of the tag (the verified install above does this for you):

   ```bash
   git -c gpg.format=ssh -c gpg.ssh.program=/usr/bin/ssh-keygen \
       -c gpg.ssh.allowedSignersFile=$HOME/.config/agentteams/allowed_signers \
       -c gpg.program=/usr/bin/gpg -c core.fsmonitor=false -c core.hooksPath=/dev/null \
       tag -v v1.2.2
   ```

3. Check that the output shows the full fingerprint from `SECURITY.md`, not just "Good signature".

## Notes for agentteams

- **The docs site follows `main`,** so it describes the latest release. The site at jlcatonjr.github.io/agentteams is
  built from `main`. For a release candidate's documentation, or `dev`'s, browse the `docs_src/` folder at that tag
  or branch.
- **Only agentteams uses `dev` so far.** researchteam, mathAgents and baseAgent still develop on `main`. Each can opt in
  through its brief (`git_workflow.branch_model: "integration"`), which ships the same model to its agent team.
- **Downstream repositories** (researchteam, mathAgents, baseAgent) pin agentteams in their `toolchain.lock` to the commit of a verified tag, never to a branch. A downstream repository may tighten these rules, never weaken them.
- **What a release may change** (patch, minor, major, and the security-fix allowance) is set out in `STABILITY.md`,
  "Release cadence".

## Questions

Open an issue, or ask Jim directly.
