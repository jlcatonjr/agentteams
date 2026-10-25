# What your pull request needs

This is the checklist an invited pull request is reviewed against. It is for contributors preparing a
pull request; most changes need only the first section.

## Every pull request

- **One change.** A pull request fixes one problem or adds one feature, and links the issue that invited
  it.
- **Tests pass.** `pytest tests/` passes locally, apart from tests that need tools or access your
  machine lacks (see [Tests and CI](tests-and-ci.md#the-test-suite)). Name any failures in the
  description; CI is the reference.
- **New behavior is tested.** A new public function or fixed bug comes with a test in `tests/`.
- **No new runtime dependencies.** The package depends only on `jsonschema` and the Python standard
  library. Optional features use the existing extras in `pyproject.toml`.
- **A CHANGELOG entry** under `[Unreleased]` when the change affects users. CI requires one for any
  change to `agentteams/**/*.py`, unless a maintainer applies the `no-changelog` label.

## If you change the public API or CLI

- **API reference.** The documented modules in `docs_src/api-reference/` are the supported API. Change
  a documented signature and you update its page; CI checks documented signatures against the code.
- **CLI reference.** Add or change a flag and you update `docs_src/cli-reference.md`; CI checks that
  every flag is documented.
- **Man page.** Regenerate it, then confirm it matches what CI generates:

    ```bash
    python -m agentteams.man > agentteams.1
    git diff --exit-code agentteams.1   # no output means it is current
    ```

What counts as public API, and the template rules, are in [Public API and template policy](../contributing.md).

## If you change the docs site

Follow [Working on the docs site](docs-site.md). In short: the strict build passes, no page moves, and
new pages are in the nav.

## If you add a template or a red-team probe

- **Templates** follow the [template authoring guide](../template-authoring.md): placeholder
  conventions, required front matter, an Invariant Core section, and registration in `analyze.py`.
- **Red-team probes** meet the bar in the pull request template: they trace to a real technique, have a
  reproducible procedure and a paired negative control.

## After you open it

After you open a pull request, the maintainer runs additional automated review on it. Expect questions
or requested changes on the pull request itself.
