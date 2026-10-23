---
search:
  boost: 2
---

# Why agentteams

agentteams gives a software or research project a coordinated team of AI agents that works under explicit
rules. This page explains the problem it addresses, the idea behind it, and who it is for.

## The problem

A single coding agent working through a long session runs into three problems.

- **Instructions fade.** As a session grows, the project conventions and priorities stated at the start
  lose weight against the mass of recent conversation. An agent that reliably followed the rules early on
  starts to conflate tasks, skip checks or apply steps out of order, even though the rules are still
  in its context.
- **Roles blur.** One agent that drafts, reviews, approves and cleans up its own work has no one to
  catch its mistakes. Several agents with no boundaries between them do not fix this; they conflict.
- **Documentation drifts.** The files that tell agents how a project works go stale as the project
  changes. An agent that acts on stale instructions makes confident, wrong changes.

## The idea: constitutional scaffolding

agentteams treats these as problems of organization rather than of model capability. Human
organizations solve the same problems with roles, authority boundaries, ways to resolve disagreements,
and procedures for updating the rules. agentteams generates that scaffolding for a team of agents:

- **Roles.** Each agent owns one concern. One coordinates, some govern, some produce, and some hold
  deep knowledge of a single component.
- **Authority.** An explicit ordering decides which source is ground truth and whose instruction
  wins. A short, non-overridable core cannot be weakened by anything an agent reads.
- **Checks.** Destructive actions need clearance first. Plans are challenged before they run, and
  multi-file changes are checked for consistency before a session closes.
- **Maintenance.** The rules live in fenced sections that updates refresh. Your own additions sit
  outside the fences and survive.

[Constitutional design](constitutional-design.md) explains the rules. [Anatomy of a team](anatomy-of-a-team.md)
explains the roles.

## What you get

You describe your project in one brief: its goal, deliverables, tools and components. agentteams then
writes a complete team into your repository:

- an **orchestrator** that routes every request and closes every session that changed several files
  with a consistency check;
- **governance agents** for navigation, security, code hygiene, consistency, conflict resolution,
  cleanup, documentation upkeep, cross-repository coordination and git operations;
- **domain agents** chosen for your kind of project, such as drafting, auditing and conversion;
- one **workstream expert** per component of your project;
- a **team builder** agent that can regenerate or extend the team;
- the instructions file your tool reads, and maintenance commands that detect drift and update the
  team without overwriting your edits.

The same brief produces the team for several agent tools. The [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md)
lists the supported frameworks and which are in beta.

## Who it's for

- **A developer using an AI coding tool on one repository:** start with [Getting started](../getting-started.md),
  then [Working with your generated team](../working-with-your-generated-team-guide.md).
- **Maintaining agent teams across several repositories:** [Update lifecycle, drift and backups](../update-lifecycle-guide.md)
  and [Cross-repository coordination](../cross-repository-coordination-guide.md).
- **Moving a team between agent tools:** [Interoperability](../interoperability.md).
- **Reviewing the security model:** [Constitutional design](constitutional-design.md), then the
  [security handbook](../agentteams-security-guide/README.md).
- **A researcher interested in how agent teams are organized:** [Further reading](further-reading.md)
  and the [manuscript (draft)](../manuscript.md).
- **Thinking about contributing:** [Pull request process and API policy](../contributing.md).

## What agentteams is not

- **Not a hosted service.** It is a Python command-line tool that you run on your own machine, and it
  writes files into your repository.
- **Not an agent runtime.** It generates agent definitions and instructions. Your AI tool, such as
  Claude Code, GitHub Copilot, Goose, Codex or Grok Build, runs the agents.
- **Not runtime protection for the apps you build.** The generated team governs how an application is
  built. It does not run inside the application. If your application serves model output to users, see
  [Runtime security for served apps](../runtime-security-guide.md).
- **Not a model wrapper.** Core generation renders templates and calls no language model. A few
  optional features (the AI-powered audit, `--auto-correct` and AI enrichment) call the standalone
  `copilot` CLI when it is installed.
