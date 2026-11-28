# `script_census_rules` — AgentTeamsModule

Sorts inventoried scripts into families and finds repeated fragments, using rules the repository writes.

> *Source: `agentteams/script_census_rules.py`*

---

**Why it exists.** An [inventory](script-census.md) says which scripts exist. A review then asks two
questions the tool cannot answer alone: which scripts do the same kind of job, and which pieces of code
recur across them. Both depend on the repository, so the rules live in a file the repository owns.
No rule ships with the tool.

**What it does.**

- `classify` adds a `family` column: the first family rule that matches a script names its family.
- `fragments` writes one `path<TAB>fragment` row for each fragment rule found in a script.
- Both read an inventory on stdin, are read-only, and write only to stdout and stderr.

**What it never does.** It never follows a symbolic link: a row whose path is absolute or has a `..`
component is refused, and a file now reached through a link is not read. The configuration file is
read the same way, and one larger than 1,000,000 bytes is refused.

**The `root` column is not a fence.** The inventory is text on stdin and `root` is whatever a row
says, so a hand-made row can name any regular file you can read; what comes back is only which rules
matched it. Feed these commands an inventory you made, and give relative roots so that no home path
lands in a saved table.

**Slow patterns are stopped.** The patterns are regular expressions you wrote, and Python's `re` has
no time limit; a pattern such as `(a+)+$` can run without end. All matching therefore runs in one
worker process that is stopped after `--timeout` seconds (default 60). The refusal names the rule and
the script it was on.

## Commands

```
python -m agentteams.script_census classify  [--config FILE] [--max-bytes N] [--timeout SECONDS] < inventory.csv
python -m agentteams.script_census fragments [--config FILE] [--max-bytes N] [--timeout SECONDS] < inventory.csv
```

`inventory` and `mark-copies` also accept `--config FILE`: `inventory` then walks the configured
`roots` when no root is named, and `mark-copies` adds the configured `snapshot_markers`.

A whole pass, with relative roots so that no home path lands in a saved table:

```
python -m agentteams.script_census inventory --config references/script-census.json \
  | python -m agentteams.script_census mark-copies \
  | python -m agentteams.script_census classify > census.csv
python -m agentteams.script_census fragments < census.csv > fragments.tsv
```

Exit status: 0 when the table was written; 2 when there is no configuration, it does not match the
schema, a pattern is not a regular expression, the input is not an inventory, a limit is not valid,
or matching did not finish in time.

## The configuration file

Without `--config`, `classify` and `fragments` read `references/script-census.json` in the current
directory, or the same file name under the agent directory's `references/` folder there. Run them from
the repository root. `inventory` and
`mark-copies` read a configuration only when `--config` names one.

```json
{
  "roots": ["tmp", "scripts"],
  "snapshot_markers": ["old-*"],
  "family_rules": [
    {"family": "migration", "path": "(^|/)migrations/"},
    {"family": "backfill", "content": "^UPDATE \\w+ SET"},
    {"family": "sql-report", "path": "\\.sql$", "content": "^SELECT"}
  ],
  "fragment_rules": [
    {"fragment": "update-set", "pattern": "^UPDATE \\w+ SET"},
    {"fragment": "argparse-main", "pattern": "argparse\\.ArgumentParser\\("}
  ]
}
```

| Key | Meaning |
|---|---|
| `roots` | Folders `inventory` walks when none is named. At most 50 |
| `snapshot_markers` | Folder-name patterns that `mark-copies` adds to its built-in ones. At most 50 |
| `family_rules` | Tried in order. Each has a `family` and a `path` expression, a `content` expression, or both. At most 200 |
| `fragment_rules` | Each has a `fragment` name and a `pattern`. At most 200 |

- A family or fragment name is lower-case letters, digits, `-` and `_`, at most 64 characters.
- An expression is at most 500 characters. `path` is searched in the script's path below its root.
  `content` and `pattern` are searched in the script's text, with `^` and `$` matching at each line.
- Matching is case-sensitive, so `^UPDATE \w+ SET` finds a statement and not the words "update the plan".
- Any other key is refused. The schema is `agentteams/schemas/script-census-config.schema.json`.

## Output

| Command | Output |
|---|---|
| `classify` | The input rows with `family` added: a rule's family, or `unclassified` |
| `fragments` | A tab-separated table with the header `path`, `fragment`. `path` is the root and the path joined with `/`, so it is unique across roots. A rule is reported once per script |

Both print a count per family or fragment on stderr, and how many scripts could not be read. A script
that cannot be read can match only a family rule that has no `content`.

## Key surface

### find_config(repo)

The configuration file under `references/` at the root, else under the first agent directory's
`references/` that has it, else `None`.

### load_config(path, *, repo)

Reads the file and checks it against the schema; every pattern compiles. Raises `CensusError`
when no file is found, it is a symbolic link or larger than `MAX_CONFIG_BYTES`, it is not JSON, it
does not match the schema, a pattern does not compile, or `jsonschema` is not installed.

### classify(rows, config, *, max_bytes, timeout)

Returns `(rows, unreadable)`. Raises `CensusError` for a row that is not an inventory row, a limit
that is not valid, or matching that did not finish in `timeout` seconds.

### fragments(rows, config, *, max_bytes, timeout)

Returns `(found, unreadable)`, where `found` holds dicts keyed by `FRAGMENT_COLUMNS` in inventory
order and then rule order. Raises as `classify`.

### Constants

`CONFIG_NAME`, `MATCH_TIMEOUT`, `MAX_CONFIG_BYTES`, `UNCLASSIFIED`, `FRAGMENT_COLUMNS`.
