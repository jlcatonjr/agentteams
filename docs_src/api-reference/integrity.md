# `integrity` — AgentTeamsModule

Hash manifest over the modules that enforce the constitution.

Every control this project relies on — the destructive-action gate, the content scanner, the fence-restore engine, the capability merge — is a Python file sitting in the same working tree the agents edit. A red-team audit on 2026-08-06 recorded that as the umbrella finding: the constitution's referee plays inside the same sandbox as the players. An agent that can write `security_gate.py` does not need to defeat the gate.

> *Source: `agentteams/integrity.py`*

---

## What this is not

A hash manifest stored beside the files it protects is a **speed bump, not a boundary**. An attacker who can edit `scan.py` can edit `enforcement-integrity.json`, and one who can edit `integrity.py` can make `verify()` return an empty list. This is documented rather than discovered later, because a control whose limits are undocumented gets trusted past them.

What it does buy:

- it detects *accidental* corruption and *unnoticed* drift, which is most real-world damage;
- it makes tampering a **two-step, recorded** act rather than a one-line edit — the attacker must also regenerate the manifest, and that regeneration appears in `git diff`;
- it gives the harness-level hook (`.claude/hooks/constitutional-gate.py`) something concrete to check before it trusts the scanner it is about to call.

The durable fix is elsewhere: move enforcement into the harness, where agents cannot write it, and keep signing keys outside the agent's environment.

---

## Constants

### `ENFORCEMENT_MODULES`

> *Source: `agentteams/integrity.py`*

Modules whose integrity the constitution depends on. Each is listed because a silent edit to it would disable or weaken a control rather than merely change behaviour: `cli/security_gate.py` (C-2, C-5), `scan.py` (C-4), `fences.py` and `unfenced.py` (C-1), `front_matter_merge.py` and `front_matter_reconcile.py` (C-3), the standing red-team audit's `redteam/checks_static.py`, `redteam/checks_report.py`, and `redteam/registry.py`, and `integrity.py` itself, so removing an entry is detectable.

### `MANIFEST_REL_PATH`

Location of the manifest, relative to the repository root: `references/enforcement-integrity.json`.

---

## Classes

### `IntegrityFinding`

> *Source: `agentteams/integrity.py`*

One enforcement module whose current digest does not match the manifest.

**Attributes:**

- `rel_path` (`str`) — Repository-relative module path.
- `expected` (`str`) — Digest recorded in the manifest.
- `actual` (`str`) — Digest computed now; empty when the file is missing.
- `reason` (`str`) — `'modified'`, `'missing'`, or `'unmanifested'`.

**Methods:**

- `describe() -> str` — One-line human-readable summary.

---

## Functions

### `compute_digests(repo_root: Path) -> dict[str, str]`

Return `{rel_path: sha256}` for every enforcement module. A module that does not exist maps to the empty string rather than being omitted, so its disappearance is a mismatch and not a silent gap in coverage.

### `write_manifest(repo_root: Path) -> Path`

Write (or refresh) the manifest and return its path. Regenerating is deliberately a separate, explicit act: it is what an operator does after an intentional change to a control, and what an attacker must additionally do after an unintentional one. An auto-refreshed manifest verifies nothing, which is why this is not wired into the normal build.

### `verify(repo_root: Path) -> list[IntegrityFinding]`

Compare enforcement modules against the manifest; empty when everything matches.

**A missing manifest returns empty.** An unmanifested repository is not a tampered one, and treating "never set up" as "compromised" would make the check fire on every fresh clone until someone muted it. Callers that require a manifest must check for the file themselves.

**Raises:** `RuntimeError` when the manifest exists but cannot be read or parsed.
