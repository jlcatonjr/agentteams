# `baseline`

Deterministic SHA-256 manifests of an emission output tree for regression detection. Used by the CLI flags `--capture-baseline PATH` and `--check-baseline PATH`, and consumed by `tests/test_baseline.py` to pin per-target emission contracts (`tests/baselines/<team>-<framework>.json`).

The capture skips the full generation pipeline — it walks an existing output tree, hashes every regular file (no symlinks, no excluded path components), and writes a JSON manifest. The diff is per-file SHA equality.

## Public Surface

```python
capture(
    root: Path,
    *,
    label: str,
    exclude: frozenset[str] | None = None,
) -> dict[str, Any]
```
Compute a baseline manifest for `root`. `label` is a free-form identifier (e.g. `copilot-vscode`, `bridge-overlay`). `exclude` defaults to caches and VCS metadata. The returned dict has shape `{schema_version, label, root, file_count, files: [{path, sha256}, ...]}`.

```python
write(manifest: dict[str, Any], out_path: Path) -> None
```
Write the manifest as deterministic JSON (sorted keys, 2-space indent, trailing newline). Parent dirs created as needed.

```python
load(path: Path) -> dict[str, Any]
```
Load and parse a baseline manifest from disk.

```python
diff(prior: dict[str, Any], current: dict[str, Any]) -> dict[str, list[str]]
```
Return `{added, removed, changed}` — three sorted lists of relative path strings. A file is `changed` iff present in both with differing SHA-256.

## CLI Workflow

```bash
# Capture (after a known-good emission)
agentteams --capture-baseline tests/baselines/myteam-copilot-vscode.json \
  --baseline-label copilot-vscode --output .github/agents

# Verify (CI / pre-merge)
agentteams --check-baseline tests/baselines/myteam-copilot-vscode.json \
  --output .github/agents
```
`--check-baseline` exits non-zero on any diff and lists added/removed/changed file paths to stderr; `--capture-baseline` skips the normal generation pipeline.

## Notes

- Hashing skips symlinks (no `realpath` follow).
- Excluded path components (default: `.git`, `.agentteams-backups`, `__pycache__`, `.DS_Store`) are skipped at any depth.
- The manifest's `root` is recorded as an absolute POSIX path — useful for forensics, not for portable diffing. Per-file `path` entries are root-relative.
