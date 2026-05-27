# Bridge-Refresh Safety Precautions

**Date Effective:** 2026-05-27
**Authority Level:** Orchestrator + Git Operations
**Scope:** Any invocation of `agentteams --bridge-from … --bridge-refresh` against an external project (test team, consumer repo, or any directory not owned by AgentTeamsModule itself).

**Origin:** 2026-05-27 incident — a Phase-2-through-Phase-4 development cycle used `--bridge-refresh` against `researchteam` and `collector-management` test teams. `--bridge-refresh` is **explicitly destructive** at the target: it overwrites `CLAUDE.md`, `.claude/README.md`, `.claude/agent-team.md`, `.claude/quickstart-snippet.md`, and `.claude/skills/recall.md` unconditionally. Both test teams had user-authored content in those files (including `collector-management`'s `Retrieval-First Rule (MANDATORY)` and the user's full `sync_claude_agent_team.py`-generated agent inventory). The destructive overwrite silently replaced that content. Recovery was possible via `git checkout HEAD --` only because the files were tracked. The same operation against an untracked or recently-edited unstaged file would have been unrecoverable.

---

## I. The Three Bridge Modes — Choosing Correctly

| Mode | Flag | Behavior at target entry files (`CLAUDE.md`, `.claude/*.md`) | When to use |
|---|---|---|---|
| **Check** | `--bridge-check` | Read-only; produces a freshness report | Always safe; use to inspect state before any write |
| **Merge** | `--bridge-merge` | Re-renders **only** content inside `<!-- AGENTTEAMS-BRIDGE:BEGIN region v=N -->…<!-- AGENTTEAMS-BRIDGE:END region -->` fences; files lacking any bridge fence are **skipped** with a notice in `bridge-merge.report.md` | The correct default for any project that already has user-authored content in CLAUDE.md or .claude/ |
| **Refresh** | `--bridge-refresh` | **Overwrites** target entry files unconditionally | Only on first-time bridge generation, or when consumer entry files are known-disposable |

**Default presumption:** for any external project, treat `--bridge-refresh` as destructive until proven otherwise. The "known-disposable" case is rare in practice — most projects accumulate user-authored guidance in CLAUDE.md within hours of first contact.

---

## II. Mandatory Pre-Flight (before any `--bridge-refresh`)

The following checks **must all pass** before invoking `--bridge-refresh` against an external project. If any check fails, switch to `--bridge-merge` or run `--bridge-check` first and surface the result to the user.

### Check 1 — Existing target entry files

```bash
for f in CLAUDE.md .claude/README.md .claude/agent-team.md .claude/quickstart-snippet.md .claude/skills/recall.md; do
  [ -f "$target_project/$f" ] && echo "PRESENT: $f"
done
```

If any file is PRESENT, proceed to Check 2.

### Check 2 — Fence presence

For every PRESENT file from Check 1:

```bash
grep -l "AGENTTEAMS-BRIDGE:BEGIN" "$target_project/$f" || echo "UNFENCED: $f"
```

If any file is UNFENCED, it contains user-authored content and `--bridge-refresh` will destroy it. **Stop. Switch to `--bridge-merge`.**

### Check 3 — Working-tree cleanliness

```bash
cd "$target_project" && git status --short
```

If the target has uncommitted changes to any file under `CLAUDE.md` or `.claude/`, **do not run `--bridge-refresh`**. Either commit the changes first (so they are recoverable via `git checkout HEAD --`) or use `--bridge-merge`.

### Check 4 — Tracked-vs-untracked classification

For each PRESENT file from Check 1:

```bash
cd "$target_project" && git ls-files --error-unmatch "$f" 2>/dev/null && echo "TRACKED" || echo "UNTRACKED"
```

UNTRACKED files have no git safety net. Their loss is permanent. **Do not overwrite UNTRACKED files via `--bridge-refresh`.**

---

## III. Safe Invocation Patterns

### First-time bridge generation (no prior CLAUDE.md or .claude/)

```bash
agentteams --bridge-from <project>/.github/agents \
  --framework claude --bridge-refresh \
  --target-host-features "<features>" --yes
```

Safe because there is nothing to overwrite.

### Subsequent bridge updates (CLAUDE.md or .claude/ already present)

```bash
agentteams --bridge-from <project>/.github/agents \
  --framework claude --bridge-merge \
  --target-host-features "<features>" --yes
```

Files lacking the bridge fence are skipped with a notice; user content is preserved.

### Forced refresh after Pre-Flight all-pass

If Checks 1–4 all pass AND the user has explicitly authorized destruction in the current session, `--bridge-refresh` is permitted. Record the authorization in the closeout work summary with the specific files acknowledged as disposable.

---

## IV. Recovery Procedure (when destruction already occurred)

1. **Stop further bridge runs** until recovery is complete.
2. Inspect the working tree at the target: `git status --short`.
3. For each tracked-modified file you did not intend to alter: `git checkout HEAD -- <file>`.
4. For untracked files newly created by the bridge that you want to keep: leave in place.
5. For untracked files newly created by the bridge that you want to discard: `rm <file>`.
6. Confirm content recovery by spot-reading the restored files for the user's original sections (e.g., critical operational rules, security guidance).
7. Record the incident in a work summary with: which files were affected, the exact bridge command run, what was restored, what was preserved.

---

## V. Test-Team Special Case

The two designated test teams (`researchteam`, `collector-management`) are **not disposable**. They are real working repositories with user-authored documentation and active project history. Treat them like any other external consumer repository:

- Default to `--bridge-merge`.
- Use `--bridge-refresh` only after Pre-Flight all-pass.
- Always run against a clean working tree.
- After any bridge invocation, immediately run `git status --short` in the target and surface any unexpected modifications.

---

## VI. Bound Operators

This precaution binds:

- **@orchestrator** — must not delegate bridge operations without surfacing this policy.
- **@git-operations** — must verify Pre-Flight checks before any bridge command that includes `--bridge-refresh`.
- **@security** — flags any bridge-refresh invocation against a working tree with uncommitted CLAUDE.md or .claude/ changes as a destructive risk requiring explicit user authorization.
- **@cleanup** — must not delete bridge-emitted artifacts without first verifying they are not the only copy of user content (this is rare but possible after a forced refresh).

---

## VII. Audit Anchor

If a future bridge-refresh causes information loss, the audit MUST link back to this document and identify which Pre-Flight check failed (or was skipped). A check that was skipped without recorded justification is a process failure separate from the loss itself.
