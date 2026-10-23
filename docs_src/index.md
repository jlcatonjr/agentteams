---
hide:
  - navigation
  - toc
search:
  boost: 2
---

# A governed AI agent team for your project

agentteams writes a coordinated team of AI agents into your repository from one short description of
your project. It is for anyone who works with an AI coding tool and wants that work organized, checked and
kept up to date as the project changes.

<span id="what-it-does"></span>

## The problem it solves

- **One agent doing everything** drafts, reviews and approves its own work, so nothing catches its
  mistakes.
- **Long sessions erode instructions:** project rules stated early lose their hold as the conversation
  grows.
- **Agent documentation drifts:** the files that tell agents how your project works go stale, and agents
  act on them anyway.

## The idea

agentteams gives your agents what a well-run organization gives its people:

- roles with clear boundaries;
- an explicit order of authority;
- checks before risky actions;
- rules that stay current as the project changes.

The rules are a short constitution that nothing an agent reads can override.

<span id="workflow"></span><span id="install"></span><span id="quick-example"></span>

## See it

```bash
pip install "agentteams @ git+https://github.com/jlcatonjr/agentteams.git@v1.0.1"
agentteams --description brief.json --project /path/to/project --framework claude
```

The brief names your project's goal, deliverables, tools and components. The command writes a complete
team into your repository, from the orchestrator down to one expert per component. The same brief works
for VS Code Copilot, Copilot CLI and Claude Code; Goose, the cross-tool AGENTS.md standard, Codex and
Grok Build are supported in beta. Python 3.11 or later is required.

[Get started](getting-started.md){ .md-button .md-button--primary } [Why agentteams](why/index.md){ .md-button }

<span id="framework-support"></span><span id="interoperability-feature-family"></span>

## Why trust it

- **Signed releases.** Release tags are signed. You install and pin a tag; [Releases and
  branches](release-and-branching.md) lists the current one and explains how to verify it.
- **A stability policy.** [STABILITY.md](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md)
  states what semantic versioning covers, and labels the adapters that are still in beta.
- **Documentation checked against the code.** CI fails when a documented API signature or CLI flag
  stops matching the code. See the [API signature check](https://github.com/jlcatonjr/agentteams/blob/main/tests/test_api_doc_signatures.py)
  and the [CLI flag check](https://github.com/jlcatonjr/agentteams/blob/main/tests/test_cli_reference_flag_parity.py).

<span id="in-depth-guides"></span>

## Where to start

<div class="grid cards" markdown>

-   **[Use it](getting-started.md)**

    Install agentteams, write a brief and generate your first team, then keep it up to date.

-   **[Understand it](why/index.md)**

    The problem, the constitutional approach and the role of each agent, with the blog series and the
    research manuscript.

-   **[Integrate it](interoperability.md)**

    Convert a team to another agent tool, bridge one canonical team into several, or exchange teams in
    the Canonical Agent Interface format.

-   **[Secure it](agentteams-security-guide/README.md)**

    The security model: clearance, waivers, content safety, OS confinement and red-team testing.

-   **[Help build it](contribute/index.md)**

    Report problems, suggest improvements, and see how invited contributions are developed and reviewed.

</div>

<span id="blog-series-agentteams-module-roles-and-workflows"></span>

**Next:** [get started](getting-started.md), read the [blog series and manuscript](why/further-reading.md),
or see [how you can help](contribute/index.md).
