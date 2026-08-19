# `redteam` — AgentTeamsModule

The `agentteams/redteam/` package: the internals of the standing constitutional red-team audit (the
`agentteams --redteam` battery) and the corpus-coverage tooling. The **model-scoring** and
**attack-generation** *scripts* that use this package live under `scripts/` and are documented in the
[Red-Team Model Scoring & Attack Generation](../redteam-model-scoring-guide.md) guide, not here.

> *Source: `agentteams/redteam/`*

---

## Package modules

| Module | Role |
|--------|------|
| `budget` | Cumulative spend ceiling for any driver that loops paid requests |
| `checks_report` | Phase-6 meta-checks F-4…F-6 (read the audit reports) |
| `checks_static` | Phase-6 meta-checks F-1…F-3 (read source, not reports) |
| `corpus` | The judgment-layer payload corpus and its guarding assertions |
| `coverage` | Diff the corpus against a taxonomy — coverage + density (detailed below) |
| `cycle` | The standing daily audit: phase order, artifacts, exit codes |
| `findings_ledger` | Durable, triaged record of what the audits found |
| `freshness` | Operator-invoked search for newly disclosed AI-adversary techniques |
| `instantiate` | Generate every framework's agent tree fresh from the canonical form |
| `kev_correlation` | Correlate the `@security` threat-intel cache against the repo |
| `realcopy` | Attack the real agent infrastructure in an isolated copy |
| `registry` | Probe data model, probe loader, ledger reader |
| `report` | Render the audit artifacts (discoveries, remediation skeleton) |
| `runner` | Phase-1 (attack) and phase-2 (review) data assembly |
| `selfaudit` | Phase-6: evaluate the red team, not the target |
| `sweep` | Red-team the whole agent infrastructure, not one agent on one framework |

The subsections below document `coverage` in full — the one submodule with a standalone consumer
(the H1 new-surface harness) and a public API worth citing directly. For the others, the source
docstrings are authoritative.

---

## `coverage` — corpus coverage & density

Diff the red-team corpus against an external taxonomy (`references/redteam-external-taxonomy.json`):
**coverage** — is each taxonomy leaf touched by *any* probe/attack? — and **density** — is a touched
leaf exercised by *enough* attacks to be a non-brittle measurement? Both **report**, neither gates.
Run via `python -m agentteams.redteam.coverage`.

> *Source: `agentteams/redteam/coverage.py`*

### Classes

#### `CoverageReport`

Result of diffing tagged probes against the taxonomy snapshot.

**Attributes:**

- `tagged_ids` (`frozenset[str]`) — Every taxonomy id tagged by at least one probe.
- `covered` (`list[tuple[str, str]]`) — `(id, name)` taxonomy entries with ≥1 tagged probe.
- `uncovered` (`list[tuple[str, str]]`) — `(id, name)` taxonomy entries with no tagged probe yet.

**Methods:** `render() -> str` — covered/total count plus the uncovered entries.

#### `DensityReport`

Result of an attacks-per-taxonomy-leaf density check (feature F2). A leaf exercised by a single
payload is a thin, brittle measurement of that surface.

**Attributes:**

- `min_per_leaf` (`int`) — Threshold below which a *touched* leaf is reported "thin".
- `per_leaf` (`dict[str, int]`) — Attack count per touched taxonomy leaf.
- `thin` (`list[tuple[str, int]]`) — `(leaf, count)` for touched leaves below `min_per_leaf`, sorted.

**Methods:** `render() -> str` — how many leaves are exercised and which are thin.

### Functions

#### `compute_coverage(external_refs_per_probe, taxonomy_path=TAXONOMY_PATH) -> CoverageReport`

Diff each probe's `external_refs` tuple against the taxonomy snapshot, splitting entries into
covered/uncovered.

- `external_refs_per_probe` (`list[tuple[str, ...]]`) — Each probe's `external_refs` (may be empty).
- `taxonomy_path` (`Path`) — Path to the taxonomy JSON snapshot.

#### `compute_density(tags_per_attack, min_per_leaf=2) -> DensityReport`

Count attacks per taxonomy leaf and flag *touched* leaves below `min_per_leaf`. Corpus-agnostic:
`tags_per_attack` is each attack's taxonomy-leaf tags (for example, a payload's `owasp_llm_2026` /
`mitre_atlas` values). An entirely untouched leaf is a *coverage* gap (see `compute_coverage`), not a
*density* one.

- `tags_per_attack` (`list[tuple[str, ...]]`) — Each attack's taxonomy-leaf tags (leaves may repeat).
- `min_per_leaf` (`int`) — Minimum attacks-per-leaf below which a touched leaf is "thin".

#### `load_taxonomy(path=TAXONOMY_PATH) -> list[tuple[str, str]]`

Return `(id, name)` pairs for every entry across the MITRE ATLAS tactics and OWASP LLM Top 10 lists in
the taxonomy snapshot, in file order.

---

## Notes

- `coverage` is deliberately **not** wired into the 7-phase cycle or the cron — corpus maintenance on
  a quarterly cadence, not a gating check.
- `compute_density` is consumed by the H1 new-surface harness (`scripts/redteam_new_surface.py`); see
  the [Red-Team Model Scoring & Attack Generation](../redteam-model-scoring-guide.md) guide.
