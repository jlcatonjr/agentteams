---
search:
  boost: 2
---

# Contribute to agentteams

This section is for anyone who wants to help improve agentteams. It explains how the project accepts
help today and where to start.

## How contributions work right now

agentteams is open source under the MIT license. One maintainer, [@jlcatonjr](https://github.com/jlcatonjr),
reviews changes and signs releases, so review time is limited. For now:

- **Issues are welcome from everyone.** Bug reports, documentation errors, feature ideas and proposals
  for new templates or framework adapters all start as an issue.
- **Pull requests are by invitation.** Before writing code, open an issue or comment on one. When the
  maintainer agrees the change is wanted, they will invite a pull request on that issue, usually labelled
  `good first issue` or `help wanted`. Pull requests that arrive without an invitation may be closed with
  a pointer back to an issue.
- **Questions go to [Discussions](https://github.com/jlcatonjr/agentteams/discussions)**, not issues.
- **Security problems are reported privately.** Never use an issue for a vulnerability; follow
  [SECURITY.md](https://github.com/jlcatonjr/agentteams/blob/main/SECURITY.md).

## Ways to help, from quickest to most involved

1. **Report a problem.** [Open an issue](https://github.com/jlcatonjr/agentteams/issues/new/choose) for a
   bug, a wrong or unclear page, or a broken link. Every page of this site has a view-source icon that opens
   its source file on GitHub; quote that file path in your issue.
2. **Suggest an improvement.** [Open a feature issue](https://github.com/jlcatonjr/agentteams/issues/new?template=feature.yml)
   that describes what you tried, what got in the way and what would have helped.
3. **Share an example brief.** A brief for a real kind of project, with its rough edges, shows where the
   generator falls short. The existing [examples](../examples.md) show the format.
4. **Answer and ask questions** in [Discussions](https://github.com/jlcatonjr/agentteams/discussions).
5. **Critique the research.** The [manuscript](../manuscript.md) is an early draft and invites argument.
6. **Take an invited issue.** Browse the open
   [`good first issue`](https://github.com/jlcatonjr/agentteams/issues?q=is%3Aopen+label%3A%22good+first+issue%22)
   and [`help wanted`](https://github.com/jlcatonjr/agentteams/issues?q=is%3Aopen+label%3A%22help+wanted%22)
   issues. These are the areas where invited work is most likely:
   - documentation fixes;
   - new example briefs;
   - [agent templates](../template-authoring.md);
   - the beta framework adapters (see the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md));
   - red-team probes for the security test battery.

   If both lists are empty, comment on any open issue you would like to take.

## What to expect

- **A first response within 7 days**, best effort, on issues and invited pull requests. If you have heard
  nothing after 7 days, add a comment to the thread; that is a welcome nudge, not a nuisance.
- **Review in the open.** Decisions and their reasons are written on the issue or pull request.
- **A code of conduct.** Everyone taking part follows the [code of conduct](code-of-conduct.md). Report
  conduct problems to the address it gives.

## Before your first pull request

- [Your first pull request](first-pull-request.md): a walkthrough from fork to open pull request.
- [What your pull request needs](what-your-pr-needs.md): the checklist a pull request is reviewed against.
- [Project tour](project-tour.md): where things live in the repository.
- [Tests and CI](tests-and-ci.md): what runs, and how to read a failure.
- [Working on the docs site](docs-site.md): the rules that keep the documentation and its URLs intact.
