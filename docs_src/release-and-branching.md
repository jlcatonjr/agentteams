# Working with agentteams: branches, releases and contributions

**For:** collaborators: Kevin Vallier (`@selfreferencing`), John Schuler (`@jsschuler`) and anyone else contributing.
The binding rules are in the shared release policy of the four repositories, which this page summarizes for agentteams. Signing and verification details are in `SECURITY.md`. Each downstream repository restates the policy in its own `docs/release-policy.md`; this guide explains how to work within them. agentteams, researchteam, mathAgents
and baseAgent all follow the same model, so what you learn here applies to all four.

## The model in brief

- **`main`** is where development happens. Every change, fixes included, lands there first, through a pull request.
- **`release/1.0`** is the stable 1.0 line, and it is what users install. It changes only when Jim copies in a fix
  that is already on `main` (a *backport*).
- **Releases are tags on `release/1.0`.** Each published version (`v1.0.0`, `v1.0.1`, …) is a tag that Jim signs.
  After each tag, `release/1.0` is merged back into `main`.
- **The next minor version gets its own branch.** `release/1.1` will be cut from `main` at its first release
  candidate.

## Branches and tags

| Ref | What it is | Who changes it |
|---|---|---|
| `main` | Development, the next minor version | Anyone, through a pull request |
| `release/1.0` | The stable 1.0 line, where users install from | Jim only, by backport |
| `vX.Y.Z`, `vX.Y.Z-rc.N` | One exact release, signed by Jim, never moved | Jim only |
| Your work branch (`feat/…`, `fix/…`, `docs/…`, `chore/…`) | One change, deleted after merge | Its author |

`main` is `1.1.0.dev0`. `release/1.0` is at `1.0.0`, which is tagged `v1.0.0`.

**Older lines:** none. The 1.0 line is the first maintained line. RCs 1–9 were tags on `main`.

The name `release/<X.Y>` is reserved for maintained lines. Never create a `release/*` branch, locally or on GitHub.

## Using agentteams

```bash
# The stable 1.0 line:
pip install "agentteams @ git+https://github.com/jlcatonjr/agentteams.git@release/1.0"
# One exact, signed release:
pip install "agentteams @ git+https://github.com/jlcatonjr/agentteams.git@v1.0.0"
```

The branch install is the 1.0 line as it stands. Between releases it can include reviewed fixes that are queued for the next patch release but not yet signed, while still reporting the last release's version number. When you need exactly what was released and signed, for a paper, an experiment or a pin, use the tag. To update a branch install, run the first command again with `--force-reinstall --no-deps`.

**A governed team is different.** If agentteams renders or signs for a governed team, follow the 1.0.0 release notes: install it from a verified, detached checkout of the signed tag, as the [migration guide](migration-guide.md) explains under "Moving to a release checkout". The same applies to signing decisions or grants (`SECURITY.md`, "Signing from a trusted install").

**For development,** work on `main` in a clone of your fork, and install it with `pip install -e .`. "Contributing a change" below covers setting up the fork.

**Pins between repositories** (`toolchain.lock`, pinned extras) always name the commit of a verified signed tag, never
a branch.

## Contributing a change

agentteams is public, and you have read access only. Contribute from a fork; Jim reviews and merges your pull request.

1. **Set up once.** Fork the repository on GitHub, then clone your fork and add the original as `upstream`:

   ```bash
   git clone https://github.com/<your-login>/agentteams.git && cd agentteams
   git remote add upstream https://github.com/jlcatonjr/agentteams.git
   ```

2. **Start each change from an up-to-date `main`:**

   ```bash
   git switch main && git pull --ff-only upstream main
   ```

3. **Branch:** `git switch -c fix/<short-topic>`. Use `feat/`, `fix/`, `docs/` or `chore/`. Keep one change per
   branch.
4. **Push to your fork and open a pull request** against `jlcatonjr/agentteams` `main`:

   ```bash
   git push -u origin fix/<short-topic>
   ```

5. **CI.** `ci.yml` runs on every pull request and must pass. Before you push, run `python -m pytest tests/ -q`. If you changed a CLI flag or help text, also regenerate the man page with `python -m agentteams.man > agentteams.1` ([Contributing](contributing.md)).
6. **Merge.** Jim reviews and merges. You can't merge into this repository.
7. **Delete your branch after it merges.**

## Getting a fix into a release

1. **Land the fix on `main` first,** as above.
2. **Ask for a backport.** On the merged pull request, add the label `needs-backport-1.0`. If you can't add labels,
   leave a comment instead: "please backport to 1.0". Either way, say why. Only these qualify:
   - security fixes;
   - correctness fixes;
   - documentation fixes.

   New features wait for the next minor release.
3. **Jim does the rest.** He copies the fix to `release/1.0` in his own pull request, labelled `backport-1.0`, using
   `git cherry-pick -x` and never a squash. When he releases, he signs `v1.0.N`.
4. **Urgent?** Say so in the pull request and message Jim.

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

- Commit straight to `main` or `release/*`. Use a branch and a pull request.
- Force-push `main` or `release/*`, or delete or move a tag. A defect is fixed forward, in a new patch release.
- Put a branch name into `toolchain.lock` or a pinned extra.

## Enforcement

GitHub enforces the release rules here. Four active rulesets cover them:

- only an admin can create `release/*` branches or `v*` tags;
- `release/*` takes changes only through a pull request, with no force-push and no deletion;
- `v*` tags can't be moved or deleted.

## How a release happens

You only need this for context:

1. **Fixes accumulate.** They land on `main`, and the ones requested for backport are copied to `release/1.0`.
2. **Jim releases.** He bumps the version on `release/1.0` in a pull request, signs `vX.Y.Z` from his own terminal, publishes the GitHub release, and merges `release/1.0` forward into `main`.
3. **Repositories release in order:** agentteams → researchteam → mathAgents → baseAgent. Each one moves its pins only
   after verifying the upstream tag's signature.
4. **Next minor.** When `1.1` reaches its first release candidate, Jim cuts `release/1.1` from `main`. From then on,
   `release/1.0` takes fixes only on request, and the install instructions move to `release/1.1`.

## Verifying a release (optional)

Each tag is SSH-signed with Jim's release key. Its fingerprint begins `SHA256:IPdnLwlF`; the full fingerprint and the
`allowed_signers` line are in agentteams `SECURITY.md`, under "Release signers". Read them on GitHub, not from a
copy inside the repository you are checking, or confirm the fingerprint with Jim.

1. On your own machine, add that line to your own `~/.config/agentteams/allowed_signers`. (Agents never write this
   file; it's yours.)
2. Run this from a clone of the repository:

   ```bash
   git -c gpg.format=ssh -c gpg.ssh.program=/usr/bin/ssh-keygen \
       -c gpg.ssh.allowedSignersFile=$HOME/.config/agentteams/allowed_signers \
       -c gpg.program=/usr/bin/gpg -c core.fsmonitor=false -c core.hooksPath=/dev/null \
       tag -v v1.0.0
   ```

3. Check that the output shows the full fingerprint from `SECURITY.md`, not just "Good signature".

## Notes for agentteams

- **The docs site follows `main`.** The site at jlcatonjr.github.io/agentteams is built from `main`, so it describes the development version. For the 1.0 documentation, browse the `docs_src/` folder on the `release/1.0` branch.
- **Downstream repositories** (researchteam, mathAgents, baseAgent) pin agentteams in their `toolchain.lock` to the commit of a verified tag, never to a branch. A downstream repository may tighten these rules, never weaken them.

## Questions

Open an issue, or ask Jim directly.
