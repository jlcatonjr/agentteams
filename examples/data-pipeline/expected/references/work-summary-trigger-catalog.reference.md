<!--
SECTION MANIFEST
| section_id | designation |
|------------|-------------|
| trigger_catalog | FENCED |
-->

<!-- AGENTTEAMS:BEGIN trigger_catalog v=1 -->
# Work Summary Trigger Catalog — SalesDataPipeline

## Purpose

This file says when a governance-relevant event gets a typed entry in a work summary, and what that entry holds.
`@work-summarizer` writes the entries. Any agent whose work matches a trigger below is that trigger's initiator: it
reports the entry's fields to `@work-summarizer` or records them in the session's plan artifacts. An entry records
state that later sessions inherit. It grants the summary writer no new authority.

Governance-relevant means a constitutionally significant event, or a workflow event that changes an obligation, a
disposition, or traceability across a boundary.

## Entry Schema

Each entry is one block placed above the narrative it belongs to, with these seven fields in this order:

```markdown
#### Entry: <trigger-type> — <short title>
- **Trigger:** <trigger-type from the catalog below>
- **Timestamp:** <ISO 8601 date and time, with zone>
- **Contributing agent:** <@agent that initiated the entry>
- **Affected scope:** <files, workflows and repositories touched>
- **Disposition:** <the governing disposition, quoted from its source artifact>
- **Unresolved obligations:** <none, or one line each: obligation, owner, source artifact ID>
- **Sources:** <plan step, contradiction record, security clearance, coordination request, commit>
```

The trigger's own required fields (catalog column 3) go under **Disposition** or **Affected scope**, whichever they
describe. A field with nothing to record says `none`. It is not left out.

## Trigger Catalog

The catalog is closed: these eight types are the only values of **Trigger**.

| Trigger type | Initiator | Required fields | Destination | Closure condition |
|---|---|---|---|---|
| `workflow-step-completion` | The executing agent | Step identifier, output artifact, disposition | The current workflow's summary entry | The step is marked `done` and its evidence is linked |
| `blocker` | The blocked agent | Blocker type, dependency owner, unblock condition, affected scope | The active summary entry, plus the Open Obligations segment | The blocker is cleared, or escalated with a named decision owner |
| `contradiction-resolution` | `@conflict-auditor` or the remediating agent | Contradiction ID, resolution action, residual risk | A summary entry that points to the contradiction record | The contradiction is marked resolved and verified |
| `cross-repository-impact` | `@repo-liaison`, after its impact assessment | Affected repository, stale surfaces, approved disposition | A boundary summary entry tied to the registry changelog | The disposition is applied, rejected (including verified non-impact), deferred, or transferred to Coordination Request adjudication, and the final status is linked to the workflow's close record |
| `security-clearance` | `@security` | Clearance scope, constraints, expiry or revocation conditions | A summary entry that cites the security evidence | The operation completes within scope, or the clearance is revoked |
| `deployment-outcome` | `@orchestrator` or the release-responsible agent | Target environment, run and result identifiers, observed content state | The release summary entry | The outcome is verified against the expected deployment state |
| `boundary-decision` | `@orchestrator`, at a principal decision point | Decision artifact ID, decision statement, alternatives considered, obligations referenced from the governing artifact | A coordination summary entry linked to the impact report or coordination request | The source artifact's obligations are referenced, ownership links are validated, and tracking status is synchronized |
| `unclassified-constitutional-event` | Any agent that detects a governance-relevant change matching no other type | Event description, provisional impact scope, escalation owner, classification deadline | A provisional summary entry flagged `unclassified` | The event is reclassified into one of the other seven types, or formally retired as non-constitutional |

`security-clearance` also covers destructive-operation clearances.

## Rules

1. **No match is not a reason to omit.** A governance-relevant event that matches none of the first seven types gets
   an `unclassified-constitutional-event` entry in the same session. Work may continue meanwhile only under reversible
   dispositions, until the event is classified. A missing match is itself an auditable signal. The unclassified type
   is a provisional intake state, never a terminal class.
2. **Obligations carry forward.** Every obligation listed under **Unresolved obligations** is copied into the next
   daily summary's Open Obligations segment, and into the weekly and monthly summaries that cover it, until its
   trigger's closure condition is met. The entry that closes it names the entry that opened it.
3. **Boundary decisions are mirror records.** A `boundary-decision` entry quotes its disposition verbatim from the
   governing decision artifact and creates no new obligation. Each obligation it lists cites its source artifact ID.
   Changing an obligation's text means revising the governing artifact, not the summary.
4. **Existing summaries stay valid.** A summary written before this catalog existed, and a narrative-only summary for
   a window with no governance-relevant event, needs no typed entries and is not rewritten to add them. Typed entries
   sit above the narrative; they do not replace it.
<!-- AGENTTEAMS:END trigger_catalog -->
