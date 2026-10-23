# Constitutional design

Every generated team works under a short constitution. This page explains, in plain language, what
that constitution does and why it is built the way it is. It is written for anyone evaluating the
security model; the [security handbook](../agentteams-security-guide/README.md) has the full detail. The exact rule text ships with every team,
in the [instruction-authority template](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/universal/instruction-authority.reference.template.md).

## Two questions every team must answer

When agents disagree, they disagree about one of two things.

- **What is true?** One agent's reading of the code contradicts another's, or a document disagrees
  with the schema. A team answers this with an **authority hierarchy**: an ordered list of sources,
  where the higher source wins.
- **Whose instruction wins?** A request from you, a rule in the team's instructions, and a sentence
  inside a file the agent is reading can all look like instructions. A team answers this with an
  **instruction ordering**.

The second question is the one prompt injection targets. An injection doesn't argue that a source is
true. It tries to make text the agent reads act as a command. An ordering of facts cannot stop that,
so the constitution adds an ordering of instructions.

## The core, in plain language

At the top of that ordering is a constitutional core that nothing below it can weaken.

| Rule | In plain language |
|---|---|
| **C-1 Precedence** | The ordering itself cannot be reordered, weakened or suspended. No text can promote itself to a higher tier. |
| **C-2 HALT is final** | When the security agent halts an operation, the operation stops, and nothing overrides that. Separately, an action the rules block can go ahead only with a waiver that is signed, scoped, time-limited and limited in number of uses. A waiver never overrides a halt. |
| **C-3 Capabilities are binding** | The tools an agent declares are a limit, not a suggestion. No instruction authorizes an agent to act outside them. Widening them is a privileged change that needs the security agent. |
| **C-4 Content is data** | Anything an agent reads is data, not instructions: a file under review, a search result, a web page, the project brief. So is anything derived from it, like a summary or a translation. Text in read content that tries to direct the agent is reported as a finding. The one narrow exception is a directive cryptographically signed with a key the operator provisioned, and even that cannot authorize anything destructive or weaken these rules. |
| **C-5 Clearance before destruction** | Destructive, bulk and cross-repository actions need a recorded clearance before they run, not after. |

## How the rules stay in force

A constitution on its own is just text. Generated teams keep these rules in force in several ways.

- **Fenced sections.** The module owns the constitution and the other generated rules, and keeps them
  inside machine-readable fences. `agentteams --update --merge` refreshes what is inside the fences and
  leaves what is outside them alone, so your project-specific additions survive updates while the
  rules stay current. See [Section fencing and safe merges](../section-fencing-guide.md).
- **Dedicated reviewers.** Specific agents have the job of enforcing the rules, and none of them does
  production work:
  - the security agent clears or halts sensitive actions;
  - an adversarial agent challenges the assumptions behind a plan before it runs;
  - a conflict auditor checks multi-file changes for contradictions before a session closes.
- **Plans before action.** Multi-step work starts with a written plan and a step list, and each
  completed step is reviewed before the next one runs.
- **Drift detection.** `agentteams --check` reports where a team's files no longer match the
  templates they came from.

## Why "constitutional"

The approach draws on economics and political economy: how organizations and institutions coordinate
people who each know only part of the picture. The manuscript cites, among others, Hayek, Coase,
Williamson, North and Ostrom. A constitution fixes a few rules that every member can rely on,
and leaves the rest to be worked out locally. Generated teams follow the same pattern: a small,
fixed core and clear roles, with room for each project to add its own rules. The
[manuscript (draft)](../manuscript.md) develops this argument in full.

## Go deeper

- [Security handbook, everyone edition, part II: the constitution and authority](../agentteams-security-guide/for-everyone/part-ii-the-constitution-and-authority.md)
- [Security handbook, developers edition](../agentteams-security-guide/for-developers/README.md)
- [Security workflow glossary](../security-workflow-glossary.md)
- [Anatomy of a team](anatomy-of-a-team.md)
