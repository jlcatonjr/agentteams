# `template_pins`

Consumer-side template pinning — the trust root for [security review §4.6](../security-hardening-guide.md). The consuming repository records the template digests it trusts in `<project>/.agentteams/template-pins.json` and **commits that file**; every later run compares the installed templates against it.

The design point is where the root of trust lives. A checksum manifest shipped *inside* the package, or a signature verified against a key pinned inside it, both put the thing being protected and the thing protecting it on the same writable surface — an attacker with package write access rewrites either. The consumer's own version control is outside that surface.

**Nothing but `--pin-templates` ever writes the pin.** A mismatch reports and never re-pins, because a pin that follows what it checks records only the last thing it saw. `tests/test_template_pins.py` asserts structurally that `write_pin` has at most one caller.

This is trust-on-first-use: it detects change *since you pinned*, not whether the first pin was good. Pair with install-time verification (`pip install --require-hashes`, attestations) if the wheel itself is in scope.

## Public Surface

```python
consumer_root(description: dict[str, Any], output_dir: Path) -> Path
```
Where the pin lives: the consuming project, never the generated output directory. `resolve_output_dir` returns `project_root == output_dir` whenever `--output` is given, so deriving the location from it would place the trust root inside a directory the tool rewrites every run. Raises `PinLocationError` when no candidate qualifies rather than falling back to a guess.

```python
pin_path(project_root: Path) -> Path
```
Where the pin file itself lives, relative to the project root: `.agentteams/template-pins.json`.

```python
load_pin(project_root: Path) -> dict[str, Any] | None
```
The consumer's committed pin, or `None` when they have not opted in. Absent is not a failure — pinning is opt-in, so an unpinned project is unprotected, not blocked. `verify` calls this internally to implement that behavior.

```python
verify(project_root: Path, installed: dict[str, str]) -> PinResult | None
```
Compare installed digests against the committed pin. Returns `None` when the project has not pinned — absent is not a failure, since pinning is opt-in. A corrupt pin also reads as absent, failing toward "unprotected" rather than "verified".

```python
write_pin(project_root: Path, installed: dict[str, str], *, pinned_at: str) -> Path
```
Record `installed` as the trusted set. Only ever called from `--pin-templates`.

```python
run_pinning(args, manifest, description, output_dir, templates_dir) -> int | None
```
CLI entry. Returns an exit code when `--pin-templates` ran, else `None` so the pipeline continues. A mismatch reports and does not block: the threat is silent propagation, which a loud report defeats, and refusing to generate would punish an intentional upgrade.

## Types

`PinResult` — `changed` / `added` / `removed` mappings plus `pinned_at`. `is_clean` ignores `added`, because a new archetype installing templates the pin predates is normal, not tampering.

`PinLocationError` (in `agentteams.errors`) — no directory outside the generated tree is available to hold the pin.

## See also

- [`front_matter_reconcile`](front-matter-reconcile.md) — reconciles deployed front matter against templates; the capability-grant complement to digest pinning.
- [`integrity`](integrity.md) — template digest computation and integrity verification underlying the pin comparison.
