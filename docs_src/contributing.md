# Contributing

Thank you for your interest in contributing to Agent Teams Module. This guide covers running the test suite, authoring new templates, and the pull request process.

---

## Development Setup

No external runtime dependencies are required — Agent Teams Module uses only the Python 3.11+ stdlib. The only dev dependency is `pytest`.

```bash
git clone https://github.com/jlcatonjr/agentteams
cd agentteams
pip install -e . pytest
```

---

## Running the Test Suite

```bash
pytest tests/
```

All tests live in `tests/`. The suite runs in under 5 seconds with no network access and no filesystem side effects (all I/O is patched or uses `tmp_path`).

### Test coverage map

| Test file | What it tests |
|-----------|--------------|
| `test_ingest.py` | JSON/YAML loading, schema validation, normalization |
| `test_analyze.py` | Manifest building, archetype selection, tool classification |
| `test_render.py` | Placeholder resolution, template loading, cross-reference validation |
| `test_emit.py` | File write, overwrite protection, SETUP-REQUIRED generation |
| `test_drift.py` | Hash comparison, drift detection, impact classification |
| `test_scan.py` | Security findings, severity classification, pattern detection |
| `test_audit.py` | Static audit checks, AI audit availability detection |
| `test_remediate.py` | Remediation plan generation and application |
| `test_graph.py` | Graph construction, serialization (Mermaid/DOT/JSON/Markdown) |
| `test_integration.py` | End-to-end pipeline smoke tests against example briefs |

---

## Authoring Templates

Templates live in `templates/` and follow the placeholder conventions defined in [`templates/PLACEHOLDER-CONVENTIONS.md`](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/PLACEHOLDER-CONVENTIONS.md).

See the [Template Authoring Guide](template-authoring.md) for full instructions.

**Key rules for new templates:**

1. Use `{UPPER_SNAKE_CASE}` for tokens the pipeline auto-resolves
2. Use `{MANUAL:UPPER_SNAKE_CASE}` for tokens that require human input
3. Every `.template.md` file must include a valid YAML front matter block with `name`, `description`, `user-invokable`, `tools`, and `model` fields
4. Every agent template must contain an **Invariant Core** section marked with ⛔

To register a new template for use in the rendering pipeline, add it to the appropriate section in `src/emit.py`'s template routing logic.

---

## Pull Request Process

1. Fork the repository and create a feature branch
2. Make your changes — keep each PR focused on a single concern
3. Run `pytest tests/` and confirm all tests pass
4. If you changed public API signatures, update the corresponding page in `docs_src/api-reference/`
5. If you added or changed CLI flags, update `docs/cli-reference.md`
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
