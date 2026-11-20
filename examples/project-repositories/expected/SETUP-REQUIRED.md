<!-- AGENTTEAMS:BEGIN content v=1 -->
# SETUP-REQUIRED.md

The following **5 placeholder(s)** could not be automatically resolved
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

## 3. `{ADJACENT_REPO_AGENTS_PATH}`

**Found in:** `references/adjacent-repos.md`
**Context:** Written in the template (line 33): | **Agent infra path** | `{MANUAL:ADJACENT_REPO_AGENTS_PATH}` |

**Action required:** Search for `{MANUAL:ADJACENT_REPO_AGENTS_PATH}` across all generated
agent files and replace with the correct value.

---

## 4. `{ADJACENT_REPO_PATH}`

**Found in:** `references/adjacent-repos.md`
**Context:** Written in the template (line 32): | **Local path**       | `{MANUAL:ADJACENT_REPO_PATH}` (use `~/` notation, not an absolute home path) |

**Action required:** Search for `{MANUAL:ADJACENT_REPO_PATH}` across all generated
agent files and replace with the correct value.

---

## 5. `{PROJECT_REDTEAM_NOTES}`

**Found in:** `references/redteam-methodology.reference.md`
**Context:** Written in the template (line 237): {MANUAL:PROJECT_REDTEAM_NOTES}

**Action required:** Search for `{MANUAL:PROJECT_REDTEAM_NOTES}` across all generated
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
