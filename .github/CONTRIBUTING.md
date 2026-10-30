# Contributing to agentteams

Thank you for your interest. The contributor guide lives on the project website, so there is one copy
to keep current:

- **[Contribute to agentteams](https://jlcatonjr.github.io/agentteams/contribute/)**: how contributions
  work today. Issues are welcome from everyone; pull requests are by invitation on an issue.
- **[Your first pull request](https://jlcatonjr.github.io/agentteams/contribute/first-pull-request/)**:
  a walkthrough from fork to open pull request.
- **[What your pull request needs](https://jlcatonjr.github.io/agentteams/contribute/what-your-pr-needs/)**:
  the review checklist.

Questions go to [Discussions](https://github.com/jlcatonjr/agentteams/discussions). Report security
vulnerabilities privately, as described in [SECURITY.md](../SECURITY.md), never in an issue. Everyone
taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## Running the tests

- **From a git worktree,** run `PYTHONPATH=$PWD python -m pytest …` from the worktree root. A venv with an
  editable install of another checkout would otherwise supply that checkout's `agentteams`.
  `tests/conftest.py` puts its own tree first on `sys.path` and `PYTHONPATH`, and stops the run with exit
  code 4 if a plugin imported a different copy first.
- **Tests do not use live DNS.** The SSRF guard resolves hostnames, so a test that passes a hostname to it
  stubs `socket.getaddrinfo` (see `_resolves_to` in `tests/test_research_search.py`). The suite then
  gives the same result offline and in a sandbox.
- **Prompt-injection payloads are built at runtime.** The constitutional gate hook refuses to write a
  source or test file that contains a literal instruction-override phrase (Rule S-5), and the scanner
  flags one in a committed file. A test that needs such a payload assembles it from fragments
  (for example `"part one " + "part two"`) so the full phrase never appears in the file.
