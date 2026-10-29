# `launcher_lineage`

Tells a derived copy of the sandbox launcher (`sandbox/confine-run.sh`) apart by its bytes: the current
agentteams release, an older one, or none at all. The launcher carries no version stamp, because consuming projects
pin its sha256 and a stamp would change every copy's bytes. The version is read from a lineage of its released bytes
instead.

```python
from pathlib import Path
from agentteams.launcher_lineage import launcher_state

st = launcher_state(Path("sandbox/confine-run.sh"))
print(st.state, st.detail)        # current released version 761d9440 (2026-10-14)
```

## `launcher_state(path, lineage=None) -> LauncherState`

| `state` | Meaning |
|---|---|
| `current` | the bytes equal the newest released version, which is the template in this install |
| `older` | the bytes equal an earlier released version; `newer` counts the later ones and `newer_security` says whether any of them is a security fix |
| `modified` | the bytes match no released version (a local edit or a fork), or the path is a symlink, not a regular file, or larger than `MAX_LAUNCHER_BYTES`. Review it: this is never folded into a benign label |
| `missing` | no file at `path` |

`LauncherState` is frozen: `state`, `detail` (one line for a person), `newer`, `newer_security`; `to_dict()`.
`lineage` defaults to `load_lineage()`. The file is read once, bounded, and opened without following a symlink.

## `load_lineage(path=LINEAGE_PATH) -> list[dict]`

Reads `agentteams/data/launcher_lineage.json`: one entry per commit that changed the template, oldest first, each
`{sha256, commit, date, security}`. Raises `ValueError` on a malformed file.

## Regenerating the lineage

```
python scripts/build_launcher_lineage.py          # rewrite agentteams/data/launcher_lineage.json
python scripts/build_launcher_lineage.py --check  # exit 1 when it is stale
```

The script walks the template's history from `HEAD` and refuses a shallow clone, so no released version is missing.
`security` is true when the commit subject is a `fix(sandbox|launcher|security…)` or the commit is on the
hand-reviewed list in the script (changes that tighten a deny rule without saying so in the subject). A test fails
when the newest entry does not equal the current template, so an edit to the launcher must regenerate the lineage
in the same change. Both files are integrity-pinned.

## Where it is shown

`python -m agentteams.vulnscan status --project DIR` prints a `launcher` line for `DIR/sandbox/confine-run.sh`, and
`handoff status` reports the launcher a handoff's goose session would run under, with a warning unless it is
`current` ([`vulnscan`](vulnscan.md)). Both are informational: the launcher's own fail-closed checks are the gate.
