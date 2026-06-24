# `bridge_subagents`

Per-agent Claude subagent stub emitter for the `copilot-vscode → claude` bridge. Stubs delegate to the canonical copilot-vscode source agent body via a `Read` directive and carry `source_sha256` provenance for drift detection. Source files are never modified.

Opt-in via [`--target-host-features bridge:copilot-vscode-to-claude:subagents`](host-features.md).

## Layout

- Stubs land in `<project>/.claude/agents/<slug>.md`.
- Each stub is a small Markdown file with YAML front matter (`name`, `description`, `source_sha256`) and a body containing a `Read` directive pointing at the source canonical agent.
- `*-expert.agent.md` workstream-experts collapse into a single parametric `workstream-expert.md` stub that accepts a component slug at invocation.

## Public Surface

```python
@dataclass
class StubEmissionResult:
    written: list[str]            # paths actually written (or, in dry_run, would be written)
    skipped: list[str]            # paths skipped because overwrite=False and the file existed
    errors: list[str]             # error messages for any failed emission
    experts_collapsed: list[str]  # slugs of *-expert agents folded into the parametric stub
    # property: success -> len(errors) == 0
```

```python
collect_source_agents(source_dir: Path) -> list[Path]
```
Return the sorted list of `*.agent.md` files in `source_dir` (typically `<project>/.github/agents/`).

```python
emit_subagent_stubs(
    *,
    source_dir: Path,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = True,
) -> StubEmissionResult
```
Emit Claude subagent stubs delegating to copilot-vscode source agents. Stubs land in `<output_root>/.claude/agents/`. `dry_run=True` computes the action set without writing. `overwrite=True` (default) matches `--bridge-refresh` semantics; pass `False` for idempotent re-runs that preserve existing stubs.

```python
detect_stub_drift(
    *,
    source_dir: Path,
    output_root: Path,
) -> list[dict[str, str]]
```
Walk emitted stubs and compare each one's recorded `source_sha256` against the current source file's SHA-256. Returns **one record per stub whose source changed** — there is no per-stub `status` field and "ok" stubs are not included. Each record is `{slug, stub_path, source_path, recorded_sha, current_sha}`. When the source file is missing, the record is still emitted with `current_sha` set to the literal string `"<missing>"`. An empty list means no drift.

## Caveats

- The emitter writes only to `<output_root>/.claude/agents/`. It does not touch any other path under `<output_root>`, including the source canonical agent files.
- `detect_stub_drift` is a hash check, not a semantic merge — a regenerated source file with whitespace-only changes will still register as `drift`.
- The parametric workstream-expert stub does not enumerate the collapsed experts; consumers discover them by scanning `source_dir` for `*-expert.agent.md`.
