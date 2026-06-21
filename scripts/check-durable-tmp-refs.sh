#!/bin/bash
# RSR1 — CI lint guard: detect durable→tmp/ references
# Fails when tracked source files and CHANGELOG cite gitignored tmp/ paths AS IF they were durable.
# Documentation (docs_src/*.md) can cite tmp/ for educational/reference purposes.
# The key check: does this file depend on a tmp/ path existing?
#
# Usage: scripts/check-durable-tmp-refs.sh
# Exit codes:
#   0 — no violations found
#   1 — violations found

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# DURABLE ARTIFACTS that should NOT reference gitignored tmp/ paths
# (except in allowlisted contexts like "planning uses tmp/", "deprecated: see X")
DURABLE_FILES=(
    "build_team.py"
    "agentteams/*.py"
    "CHANGELOG.md"
    "schemas/*.json"
)

# Patterns exempt from the check (legitimate discussions of impermanence)
# Includes skip/exclude declarations that name tmp/ as something to AVOID — those
# are the opposite of a durable→tmp/ dependency (the rule this lint enforces):
#   _SKIP_PREFIXES — a skip-list tuple that excludes tmp/ from a scan
#   sandbox        — docstrings noting tmp/ "sandboxes" are excluded from findings
EXEMPT_PATTERN="(deprecated|gitignore|plan|tmp/remediation-plans/master-status|Mirror backup|See audit report|tmp/by-week.*audit|tmp/diffs|tmp/inject_fences|off-repo storage|Operator|rewrite-backups|_SKIP_PREFIXES|sandbox)"

cd "$REPO_ROOT"

VIOLATIONS=0
TEMP_FOUND=$(mktemp)
trap "rm -f $TEMP_FOUND" EXIT

echo "Checking durable artifacts for references to gitignored tmp/ paths..."

# Check each durable file
while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    [[ ! -f "$file" ]] && continue

    # Search for tmp/ references that are NOT exempt
    # The pattern looks for tmp/ followed by non-allowlisted context
    if grep -Hn 'tmp/' "$file" 2>/dev/null | grep -v -E "$EXEMPT_PATTERN" > "$TEMP_FOUND"; then
        echo "❌ $file: references gitignored tmp/ path (not in durable storage)" >&2
        cat "$TEMP_FOUND" | sed 's/^/    /' >&2
        # set -e safe: ((VIOLATIONS++)) returns the pre-increment value (0 → exit 1),
        # which would abort the loop on the first violation and truncate the report.
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < <(
    # Build the file list dynamically
    for pattern in "${DURABLE_FILES[@]}"; do
        git ls-files "$pattern" 2>/dev/null || true
    done | sort -u
)

if [[ $VIOLATIONS -gt 0 ]]; then
    echo "" >&2
    echo "❌ RSR1 violation: $VIOLATIONS durable artifact(s) reference non-durable tmp/ paths." >&2
    echo "   Either: (a) move the path to durable storage, (b) mark it as deprecated" >&2
    echo "   See tmp/by-week/2026-W21/infra-audit/remediation/RSR1-traceability.md" >&2
    exit 1
fi

echo "✓ RSR1 check passed: no durable→tmp/ violations"
exit 0
