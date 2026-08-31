# Verification environment

This page declares the minimum tooling AgentTeams' CI gate and local
development workflow assume. The preflight script
[`scripts/verify-env.py`](../scripts/verify-env.py) asserts these
preconditions and is run as the first step in CI.

## Preconditions

| Tool   | Minimum version | Reason |
|--------|-----------------|--------|
| Python | **3.11**        | Pattern-matching, `typing.Self`, the `tomllib` stdlib module — all used across `agentteams/`. Declared in `pyproject.toml` (`requires-python = ">=3.11"`). |
| git    | **2.23**        | Stable interaction between `-z` and `--literal-pathspecs`; introduces `git switch` / `git restore` which the docs and operator procedures assume. |

These minimums are deliberate floors, not exact pins; newer versions are
welcome. The CI matrix currently exercises Python 3.11 and 3.12 on both
`ubuntu-latest` and `macos-latest`.

## Platform notes

- **macOS filenames** use NFD unicode normalization by default while Linux
  uses NFC. The `agentteams.scan` module treats both forms as equivalent;
  the macOS leg of the CI matrix exists to keep this guarantee honest.
- **Path quoting** in `git ls-files` differs across versions: the
  `--literal-pathspecs` + `-z` combination is the only contract treated as
  stable. See `agentteams/_utils.py` for the wrapper.
- **No external runtime dependencies** beyond `jsonschema` (declared in
  `pyproject.toml`). The preflight does not check Python *packages* — those
  are resolved by `pip install -e .`.

## Running the preflight

Local:

```bash
python scripts/verify-env.py             # human-readable
python scripts/verify-env.py --quiet     # suppress success line
python scripts/verify-env.py --json      # machine-readable
```

Exit codes:

- `0` — all preconditions met
- `1` — one or more preconditions unmet (each failing check prints a
  remediation hint)
- `2` — unexpected error (subprocess failure, parse failure)

CI invokes the preflight before any test or build step; a non-zero exit
fails the run fast.

## Extending the preflight

Add a new check by:

1. Implementing a `_check_<tool>()` function in `scripts/verify-env.py`
   that returns the canonical `{name, ok, required, found, hint}` dict.
2. Appending it to `run_checks()`.
3. Adding a unit test in `tests/test_verify_env.py` covering pass + failure
   modes.
4. Updating the table on this page.

Keep checks orthogonal: the preflight is for *environment* preconditions
(interpreters, OS-level tools, platform constraints). Behavioral
correctness lives in the test suite.

## Local verification environment (dev dependencies)

The runtime is stdlib-only, but the **test suite** needs a few dev-only
packages that CI installs and a bare system Python typically lacks. On a
PEP 668 "externally managed" interpreter (Homebrew/macOS system Python),
`pip install --user` is refused — use a venv:

```bash
python3 -m venv .venv-test
.venv-test/bin/pip install -e '.[test,research]'
.venv-test/bin/python -m pytest tests/
```

The extras are the canonical dep lists (`pyproject.toml`): base supplies
`jsonschema`, `[test]` supplies `pytest` + `PyYAML`, `[research]` supplies
`httpx` + `pypdf`. What breaks without each (observed 2026-08-16):

| Package | Absent → |
|---|---|
| `jsonschema` (base) | `agentteams.canonical` fails at import; schema-validation tests and every test importing `canonical`/`absorb` error at collection |
| `httpx` (`[research]`) | all `tests/test_research_*.py` error at collection |
| `pypdf` (`[research]`) | the two PDF-extraction tests skip (guarded with `pytest.importorskip`) |
| `PyYAML` (`[test]`) | `canonical._load_yaml_block` uses the built-in minimal-subset parser instead — covered by its own tests either way |

## Out of scope

- Containerisation (Dockerfile / devcontainer) — separate future work.
- Network reachability checks — CI runners have their own contract.
- Python package presence for the *preflight* — handled by `pip install -e .`
  and the venv recipe above; the preflight itself must remain runnable on a
  bare interpreter to deliver a useful error.
