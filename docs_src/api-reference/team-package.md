# `team_package`

Portable team package: a durable canonical directory plus its generic bridge,
zipped into one portable archive (open-items remediation OPEN-5). A repo with zero
`agentteams` integration can unpack the zip and read the canonical tree and generic
bridge prose directly; a repo with a native adapter can later get full-fidelity
native rendering via `--interop-source-framework canonical`.

CLI surface: [`--package-team`](../cli-reference.md#portable-team-package).

## Depends on the generic bridge target, not canonical-as-source

The bundled generic bridge is generated from the team's **original native
framework source**, not re-derived from the canonical directory — the recipient
repo never re-runs `--bridge-from` on unpacked contents, it only reads pre-rendered
`.md` files. `package_team` therefore refuses a canonical-directory source
(`ValueError`) — use `--bridge-from <canonical dir> --bridge-source-framework
canonical --framework generic` directly for that case instead.

The canonical snapshot and the generic bridge are both derived from the same
`source_dir` within one `package_team()` call (no intermediate write, no window
for the source to change between the two), but via two independent parsers:
[`materialize_canonical`](canonical.md) consumes [`export_to_cai`](interop.md)'s
output, while [`run_bridge`](bridge.md) re-derives its own inventory via
`bridge_sources`' separate, more lenient front-matter reader — `bridge.py`
deliberately stays independent of CAI/canonical internals. For a well-formed
source team the two agree; `tests/test_team_package.py` pins this agreement
empirically (roster parity) rather than claiming an architectural guarantee that
doesn't exist.

## Public Surface

```python
@dataclass
class PackageResult:
    success: bool
    zip_path: Path
    dry_run: bool
    source_framework: str
    agent_count: int
    errors: list[str]
    notices: list[str]
```

```python
package_team(
    *,
    source_dir: Path,
    output_zip: Path,
    source_framework: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> PackageResult
```
Package a native-framework source team as a portable zip.

Raises `FileNotFoundError` if `source_dir` does not exist, `ValueError` if
`source_dir` detects (or is declared) as canonical, `IsADirectoryError` if
`output_zip` is an existing directory, and `FileExistsError` if `output_zip`
already exists and `overwrite` is `False`.

## Zip layout

```text
.agentteams/canonical/           # team.cai.json + agents/<slug>.md, verbatim
                                  # + skills/<slug>/SKILL.md when the source
                                  #   has first-class skills (e.g. a Claude
                                  #   source's .claude/skills/)
references/bridges/<src>-to-generic/
    bridge-manifest.json
    agent-inventory.md
    quickstart-snippet.md
    entrypoint.md
    domain-boundary.md
```

## Atomicity

All intermediate writes happen inside a fresh `tempfile.TemporaryDirectory()`; the
final zip is written to a staging path inside that same temp directory, then moved
into place via `atomicio._atomic_copy` (temp-file-in-destination-dir +
`os.replace`). On any failure before that point (e.g. the bridge render itself
fails), `output_zip` is never touched.

## CLI wiring

`cli.package_switch.run_package_team` is the CLI dispatch: runs the same live
security-freshness preflight every other write-path convert/interop/bridge mode
uses (skipped only for `--dry-run`), then calls `package_team`. `--output` names
the destination zip **file** path for this mode (default `./team-package.zip`),
not a directory as it does for every other `--framework` mode.

`--package-team` is mutually exclusive with every other standalone op that
would otherwise dispatch first in `cli/app.py`'s if-chain and silently shadow
it: `--convert-from`, `--interop-from`, `--bridge-from`, `--self`,
`--fleet`, `--backup-mirror`, `--capture-baseline`/`--check-baseline`,
`--verify-waivers`, `--redteam`/`--accept-probe-baseline`,
`--write-integrity-manifest`, `--verify-integrity`, `--verify-backup`,
`--prune-backups`, `--stale-check`, `--stale-restore`,
`--add-fence-markers`, `--refresh-graph`, `--refresh-architecture`,
`--install-git-hooks`, `--revert-migration`, `--migrate`, the Goose switch
flags (`--goose-source`/`--goose-model`/`--goose-show`), and `--recipe-check`
(enforced in `agentteams/cli/parser_validate.py`).
