# API Verification Session — 2026-05-20

## Task Completed
Comprehensive technical validation of the AgentTeamsModule public API surface across all 23 modules.

## Key Findings

### Stale Documentation (2 modules)
1. **emit.py** — Recent changes from 2026-05-19 not documented:
   - `DryRunReport.notices` field (Plan 3 extension point)
   - `MergeResult.shrink_notices` field
   - `result.unchanged` list behavior (mtime hygiene: byte-identical files now skip write)
   - Impact: Callers tracking `result.written` count will see different metrics

2. **frameworks.md** — Missing per-adapter documentation:
   - No constructor signatures for CopilotVSCodeAdapter, CopilotCLIAdapter, ClaudeAdapter
   - No framework-specific method/constant details

### Missing Documentation (4 modules)
- **behavioral_drift.py** (HIGH priority) — Orthogonal to drift.py; validates agent run traces vs. spec
- **memory_index.py** (HIGH priority) — BM25 lexical indexing; schema v1.2; grid-searched parameters
- **model_routing.py** (MEDIUM priority) — Optional cost-routing contract (off by default)
- **security_refs.py** (LOW priority) — Likely internal; status needs clarification

## Verification Results
- All 23 modules enumerated
- 19 documented modules verified against code ✓
- No lint/type errors found ✓
- Backward compatibility issues identified (emit.py)
- 67+ public functions verified across modules
- 30+ public classes/dataclasses verified

## Recommendations (Priority Order)

### Tier 1 (Critical)
1. Update emit.md for 2026-05-19 changes (30 min)
2. Create behavioral-drift.md (45 min)
3. Create memory-index.md (60 min)

### Tier 2 (High)
4. Expand frameworks.md with per-adapter docs (45 min)
5. Create model-routing.md (20 min)

### Tier 3 (Medium)
6. Clarify security_refs.py status (5 min)
7. Update CHANGELOG.md with emit.py changes (10 min)
8. Add "Recent Changes" section to api-reference/index.md (15 min)

## Report Location
Full structured report saved to: `tmp/API-VERIFICATION-REPORT-2026-05-20.md`
