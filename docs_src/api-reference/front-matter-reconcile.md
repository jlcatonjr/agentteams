# `front_matter_reconcile`

Reports where a deployed team's YAML front matter diverges from its templates, and applies the template's values only on explicit instruction. Backs the CLI flags `--reconcile-front-matter` and `--reconcile-apply`.

Front matter **cannot be fenced**: YAML must occupy the first bytes of a file and a fence marker is an HTML comment, so there is nowhere to put one. The merge therefore uses a three-way rule — the template's value on a file unmodified since generation, the on-disk value on an edited one. That second branch is deliberate, since an edit may be a project's own choice, but it means a capability fix expressed as a `tools:` grant stops silently at every edited file with only a notice buried in a full update run.

Applying is privileged and treated that way: `allowed-tools` is a [capability grant](capability-map.md), C-3 makes widening one a privileged change, so the apply path is a separate flag never implied by the report, and every applied change is announced with `[CAPABILITY GRANT CHANGED]`. See the [security hardening guide](../security-hardening-guide.md) for the privileged-change rules this path enforces.

## Public Surface

```python
find_divergences(rendered: list[tuple[str, str]], output_dir: Path) -> list[Divergence]
```
Every front-matter key where the deployed file and the fresh render disagree. Only keys the **template declares** are compared — a key the deployed file adds is a project choice, not drift, and reporting it would train operators to ignore the report. A file with no front matter at all is skipped rather than given one, since inventing a block means inventing a capability grant.

```python
apply_divergences(divergences: list[Divergence], output_dir: Path) -> list[str]
```
Rewrite each diverging `key:` line to the template's value. The body is untouched, as is every key not in `divergences`. Returns one line per change, capability keys marked.

```python
format_report(divergences: list[Divergence]) -> str
```
Human-readable report. Says so explicitly when the team is already reconciled, and names the flag that would apply the changes.

```python
run_reconcile(args, rendered: list[tuple[str, str]], output_dir: Path) -> int
```
CLI entry. Always returns 0: divergence is a finding to read, not an error.

```python
migrate_capability_key(agents_dir: Path, *, dry_run: bool = True) -> list[CapabilityKeyMigration]
```
Rename a superseded capability key (`allowed-tools` → `tools`) in every agent file under `agents_dir`, in place. Existing Claude teams generated before 2026-08-06 declare `allowed-tools:` — a key Claude Code's subagent schema does not define — so they grant every agent every tool regardless of what the file's body claims; this is the standalone migration for teams `--update --merge` cannot reach, since front matter is preserved verbatim. Only the key name is renamed — the value is carried across byte-for-byte. `dry_run=True` by default: writing must be asked for, so with it nothing is written and the result describes what would change.

```python
survey_capability_keys(agents_dir: Path) -> dict[str, int]
```
Read-only census of which capability key (`tools` vs. the superseded `allowed-tools`) a deployed team's agent files use. Separate from `migrate_capability_key` so a survey across other people's repositories is provably incapable of writing. Returns `{"agents": n, "superseded": n, "current": n, "none": n}`.

## Types

`Divergence` — `rel_path`, `key`, `deployed`, `template`. `is_capability` is true for `tools`, `allowed-tools`, `capabilities`, `disallowedTools`, `model`, and `agents`, because a diverging grant is a different kind of finding from a diverging description.

## See also

- [`template_pins`](template-pins.md) — consumer-side pinning of the template digests this module reconciles against.
- [`drift`](drift.md) — content and structural drift detection for fenced regions; front matter is the unfenceable complement this module handles.
