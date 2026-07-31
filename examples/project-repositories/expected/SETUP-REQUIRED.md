<!-- AGENTTEAMS:BEGIN content v=1 -->
# SETUP-REQUIRED.md

The following **2 placeholder(s)** could not be automatically resolved
for project **ProjectRepositories** and require manual attention.

---

## 1. `{PIP_PACKAGE_NAME}`

**Found in:** `multiple`
**Context:** The placeholder {PIP_PACKAGE_NAME} could not be auto-resolved.

**Action required:** Search for `{MANUAL:PIP_PACKAGE_NAME}` across all generated
agent files and replace with the correct value.

---

## 2. `{DOC_SITE_CONFIG_FILE}`

**Found in:** `multiple`
**Context:** The placeholder {DOC_SITE_CONFIG_FILE} could not be auto-resolved.

**Action required:** Search for `{MANUAL:DOC_SITE_CONFIG_FILE}` across all generated
agent files and replace with the correct value.

---


Once all items above are resolved, invoke `@conflict-auditor` to verify consistency.

## Recommended `.gitignore` entries

Your generated team writes two machine-local caches that should **not** be committed. Add these
to this repository's `.gitignore`:

```gitignore
# agentteams retrieval validation-cache sidecars — machine-local, rebuilt on demand
**/references/memory-index.vcache
**/references/code-index/
```

Already committed one by mistake? `.gitignore` does not untrack an existing file — use
`git rm --cached <path>` first, then confirm with `git ls-files | grep vcache`.
<!-- AGENTTEAMS:END content -->
