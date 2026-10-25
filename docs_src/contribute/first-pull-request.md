# Your first pull request

This tutorial takes you from an invited issue to an open pull request, using a small documentation fix.
It is for first-time contributors. Pull requests are currently by invitation, so start from an issue
where a maintainer has invited one (see [Contribute](index.md)).

You need Git, Python 3.11 or later and a GitHub account. Check your Python version first:

```bash
python3 --version
```

It should print `Python 3.11` or later.

## 1. Fork and clone

On GitHub, open <https://github.com/jlcatonjr/agentteams> and choose **Fork**. Then clone your fork,
replacing `YOUR-NAME` with your GitHub username:

```bash
git clone https://github.com/YOUR-NAME/agentteams.git
cd agentteams
```

## 2. Install the project and its test tools

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

The last line ends with `Successfully installed agentteams-…` and the test tools.

## 3. Create a branch

Branch names start with the kind of change, `docs/`, `fix/`, `feat/` or `chore/`, followed by a short
topic:

```bash
git switch -c docs/fix-typo-in-getting-started
```

Git prints `Switched to a new branch 'docs/fix-typo-in-getting-started'`.

## 4. Make the change

Edit the page under `docs_src/`, for example `docs_src/getting-started.md`. Keep the change to the one
problem the issue describes.

## 5. Build the docs the way CI does

```bash
pip install -r .github/docs-requirements.txt
mkdocs build --strict --site-dir _site
```

The build ends with a line like `INFO - Documentation built in 12.34 seconds`. Any line starting with
`WARNING` makes the strict build fail; fix the link or page it names and build again.

## 6. Run the docs checks

```bash
python scripts/check_docs_site.py pages _site
python scripts/check_docs_site.py inbound _site
pytest tests/test_docs_nav_completeness.py tests/test_docs_api_nav_groups.py tests/test_check_docs_site.py
```

The first command prints one line per check, such as `[pages] vague_link_text: 0 baselined page(s), 0
in BASELINE`, and no line containing `FAIL`. The second prints the number of site URLs it checked. The
last ends with `passed`.

## 7. Commit and push

```bash
git add docs_src/getting-started.md
git commit -m "docs: fix typo in getting started"
git push origin docs/fix-typo-in-getting-started
```

Commit messages start with the same kind of change, `docs:`, `fix:`, `feat:` or `chore:`, and may add a
scope, as in `docs(site):`. `_site/` and `.venv/` are ignored by Git, so neither is committed.

## 8. Open the pull request

GitHub prints a link after the push. Open it, set the base branch to `main`, and in the description:

- link the issue (`Closes #123`);
- say what you changed and how you checked it.

CI runs the full test suite and the docs build on your pull request. [Tests and CI](tests-and-ci.md)
explains each check and what a failure means.

## Next

- [What your pull request needs](what-your-pr-needs.md) for changes beyond the docs.
- [Releases and branches](../release-and-branching.md) for how changes reach a release.
