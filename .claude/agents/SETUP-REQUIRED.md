<!-- AGENTTEAMS:BEGIN content v=1 -->
# SETUP-REQUIRED.md

All placeholders were automatically resolved. No manual setup required.

Agent team successfully generated for **AgentTeamsModule**.

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
