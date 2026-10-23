# Contributing

Thank you for your interest in contributing to Agent Teams Module. This guide covers running the test suite, authoring new templates, and the pull request process.

---

## Development Setup

Agent Teams Module has one runtime dependency (`jsonschema`); otherwise it uses only the Python 3.11+ stdlib. The tools needed to run the test suite (`pytest`, `pyyaml` and the wheel-build tooling for the packaging test) are the `test` extra in `pyproject.toml`. CI's main test job also installs the `research` extra, for the tests of that optional subpackage.

```bash
git clone https://github.com/jlcatonjr/agentteams
cd agentteams
pip install -e ".[test]"
```

---

## Running the Test Suite

```bash
pytest tests/
```

All tests live in `tests/`, one `test_<module>.py` per module or feature, so the file that covers a module is usually named after it. To run only the tests for the area you changed:

```bash
pytest tests/test_render.py          # one module
pytest tests/ -k "fence"             # every test whose name matches
```

Some tests guard documentation rather than code. If you change a docs page, the API reference or a CLI flag, run these before opening a PR:

```bash
pytest tests/test_docs_nav_completeness.py tests/test_api_doc_parity.py \
       tests/test_api_doc_signatures.py tests/test_module_doc_ratchet.py \
       tests/test_cli_reference_flag_parity.py
```

---

## Authoring Templates

Templates live in `agentteams/templates/` and follow the placeholder conventions defined in [`agentteams/templates/PLACEHOLDER-CONVENTIONS.md`](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/PLACEHOLDER-CONVENTIONS.md).

See the [Template Authoring Guide](template-authoring.md) for full instructions.

**Key rules for new templates:**

1. Use `{UPPER_SNAKE_CASE}` for tokens the pipeline auto-resolves
2. Use `{MANUAL:UPPER_SNAKE_CASE}` for tokens that require human input
3. Every `.template.md` file must include a valid YAML front matter block with `name`, `description`, `user-invocable`, `tools`, and `model` fields
4. Every agent template must contain an **Invariant Core** section marked with ⛔

To register a new domain archetype, add it to the `select_archetypes()` selector in `agentteams/analyze.py` and to the team-manifest schema. The full checklist is in [Registering a new template](template-authoring.md#registering-a-new-template).

---

## Pull Request Process

Branches, releases and backports are covered in [Releases and branches](release-and-branching.md): changes go to
`main`, and `release/1.0` takes only backported fixes.

1. Fork the repository and create a feature branch
2. Make your changes — keep each PR focused on a single concern
3. Run `pytest tests/` and confirm all tests pass
4. If you changed public API signatures, update the corresponding page in `docs_src/api-reference/`
5. If you added or changed CLI flags, update `docs_src/cli-reference.md`
6. If you added or changed CLI flags/help text, regenerate `agentteams.1` before opening the PR:

```bash
python -m agentteams.man > agentteams.1
python -m agentteams.man > /tmp/agentteams-check.1 && diff /tmp/agentteams-check.1 agentteams.1
```

Stale `agentteams.1` is a common source of failed CI/deploy checks (`Check man-page is current`).
7. Open a PR against `main`; the CI workflow will run `pytest` automatically

### What gets reviewed

- Test coverage for any new public functions
- Placeholder conventions compliance for new templates
- Agent doc consistency (no stale counts, no phantom agent references)
- No external dependencies introduced

### API Documentation Boundary Policy

AgentTeamsModule uses a **curated public API** policy.

- The supported API surface is the set of modules and symbols documented under `docs_src/api-reference/`.
- Symbols not documented in API reference pages are treated as internal implementation details and may change without notice.
- Package and subpackage API pages (for example framework adapters) are allowed when the import surface is intentionally package-level.

The canonical module list is maintained in `docs_src/api-reference/index.md`. Any PR that promotes an internal symbol/module to public API must update that index and add complete API docs for the newly public surface.

---

## Reporting Issues

Open an issue at <https://github.com/jlcatonjr/agentteams/issues>. Include:

- Python version (`python --version`)
- Command run and flags used
- Error output or unexpected output
- Your brief (redact any sensitive fields)
