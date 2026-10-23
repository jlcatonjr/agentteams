# Tests and CI

This page is a reference for contributors: what the test suite covers, how to run part of it, and what
each CI check on a pull request means.

## The test suite

All tests live in `tests/`, one file per module or feature, named `test_<topic>.py`. The suite covers:

| Area | Examples |
|---|---|
| The generation pipeline | ingest, analyze, render, emit, drift and update |
| Framework adapters | one or more files per supported agent tool |
| Security | the scanner, gates, waivers, confinement and the red-team battery |
| Documentation guards | API and CLI reference parity, nav completeness, the docs-site checks |
| Packaging | that the built wheel ships its JSON schemas |

Run all of it, or only what you changed:

```bash
pytest tests/                       # everything
pytest tests/test_render.py         # one file
pytest tests/ -k fence              # tests whose names match
```

Some tests need tools or access a local machine may lack: the `goose` CLI, a browser, network access
for live probes, or the operating-system sandbox used by the confinement tests. They skip or fail
locally for that reason. Mention such failures in your pull request; CI is the reference environment.

There is no separate lint or formatting command. Match the style of the surrounding code.

## Checks on a pull request

| Check | Workflow | Fails when |
|---|---|---|
| Tests (Ubuntu and macOS, Python 3.11 and 3.12) | `ci.yml`, job `test` | the environment check fails, any test fails, the wheel is missing its schemas, `agentteams.1` is out of date, or a durable file cites a gitignored `tmp/` path |
| Template lineage | `ci.yml`, job `template-lineage` | a recorded history of template sections disagrees with Git; you rarely touch this, so ask on the pull request if it fails |
| macOS Seatbelt | `ci.yml`, job `macos-seatbelt` | advisory: shown, but does not block |
| Docs build | `ci.yml`, job `docs-build` | the strict build warns, a page check fails, a cited URL no longer resolves, or a published URL disappears |
| Changelog | `changelog-link.yml` | `agentteams/**/*.py` changed without a `CHANGELOG.md` entry |

The other workflows run on a schedule, on push to `main`, or by hand: for example the daily feature
audit, the weekly red-team audit, the security maintenance runs and the site deploy. They do not run on
pull requests.

**Your first pull request may wait for approval.** GitHub does not run workflows on a first-time
contributor's pull request from a fork until the maintainer approves them, so the checks may show as
waiting at first. To see why a check failed, open its **Details** link on the pull request.

## Fixing common failures

- **Changelog:** add an entry under `[Unreleased]` in `CHANGELOG.md`, or ask the maintainer to apply the
  `no-changelog` label if the change needs none.
- **Man page out of date:** regenerate it as described in
  [What your pull request needs](what-your-pr-needs.md#if-you-change-the-public-api-or-cli).

## Reading a docs-build failure

- **A `WARNING` line in the build** names a page and a link or nav entry that is broken. Fix the link.
- **`[pages] FAIL (enforced page)`** names a page and a check, such as a missing heading level or vague
  link text, on a page that must have none.
- **`[pages] FAIL ratchet … newly violates`** means a page that used to pass a check now fails it.
- **`[pages] FAIL ratchet … no longer violates`** means you fixed a known problem: remove that page from
  `BASELINE` in `scripts/check_docs_site.py`.
- **`[urls] FAIL`** means a page that is published today would disappear. Keep the page, or follow
  [Working on the docs site](docs-site.md) to remove it on purpose.
- **`[inbound] FAIL`** means a site URL cited in the README, man page or `pyproject.toml` no longer
  resolves.
