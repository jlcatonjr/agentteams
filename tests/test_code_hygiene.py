"""
test_code_hygiene.py — executable guards for the repo's own code-hygiene rules.

These tests dogfood agentteams' code-hygiene agent (see
agentteams/templates/universal/code-hygiene.template.md) by enforcing a few
rules mechanically so the refactor cannot regress and new code cannot re-offend:

  * CH-07 (modular structure): no tracked non-test module exceeds a line ceiling.
  * CH-24 (exception handling is a last resort): the number of broad
    `except Exception`/`BaseException`/bare-`except` clauses only ever ratchets
    DOWN, never up.
  * CH-24 (no swallowing): the number of `except` clauses whose body is only
    `pass`/`continue` only ever ratchets DOWN.

Counts are measured by AST (not grep) so `except` inside strings/comments/docs
is never counted. Scope is pinned explicitly: tracked `*.py` from `git ls-files`,
excluding `src/` (dead duplicate, slated for removal) and `tmp/` (gitignored
scratch). The line ceiling additionally excludes `tests/` — test modules may be
long. Baselines below are the verified state on 2026-06-15; LOWER them as the
refactor removes offenders, and remove allowlist entries as files drop under the
ceiling. Raising any baseline requires an explicit, reviewed justification.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- pinned scope ----------------------------------------------------------
_EXCLUDE_PREFIXES = ("src/", "tmp/")          # excluded from every guard
_LENGTH_EXCLUDE_PREFIXES = _EXCLUDE_PREFIXES + ("tests/",)

# --- ratchets (verified 2026-06-15; only ever decrease) --------------------
MAX_MODULE_LINES = 1000
LENGTH_ALLOWLIST: frozenset[str] = frozenset({
    "build_team.py",            # 4086 — Phase 1 decomposition target
    "agentteams/analyze.py",    # 1503 — tracked debt (later phase)
    "agentteams/emit.py",       # 1389 — tracked debt (later phase)
})
BROAD_EXCEPT_BASELINE = 15      # except Exception / BaseException / bare except
SWALLOW_BASELINE = 29           # except clause whose body is only pass/continue


def _tracked_py_files() -> list[str]:
    """Return tracked + untracked-non-ignored *.py paths (relative, POSIX) via git.

    Includes untracked-but-not-gitignored files (``--others --exclude-standard``)
    so a newly-added module is checked immediately, before it is staged — a new
    oversized or broad-except-laden file must not slip past until commit. Fails
    loud if git is absent (CH-23). ``-z`` + NUL split is filename-safe.
    """
    out = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO_ROOT, text=True,
    )
    return [line for line in out.split("\0") if line]


def _in_scope(rel: str, exclude: tuple[str, ...]) -> bool:
    return not rel.startswith(exclude)


def _count_exceptions(rel_paths: list[str]) -> tuple[int, int, dict[str, int]]:
    """Return (broad_count, swallow_count, broad_by_file) measured by AST."""
    broad = 0
    swallow = 0
    by_file: dict[str, int] = {}
    for rel in rel_paths:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            handler_type = node.type
            is_broad = handler_type is None or (
                isinstance(handler_type, ast.Name)
                and handler_type.id in {"Exception", "BaseException"}
            )
            if is_broad:
                broad += 1
                by_file[rel] = by_file.get(rel, 0) + 1
            if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                swallow += 1
    return broad, swallow, by_file


def test_no_new_oversized_modules() -> None:
    """CH-07: no tracked non-test module exceeds the line ceiling (allowlist aside)."""
    offenders = {}
    for rel in _tracked_py_files():
        if not _in_scope(rel, _LENGTH_EXCLUDE_PREFIXES) or rel in LENGTH_ALLOWLIST:
            continue
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").count("\n") + 1
        if lines > MAX_MODULE_LINES:
            offenders[rel] = lines
    assert not offenders, (
        f"New module(s) exceed the {MAX_MODULE_LINES}-line CH-07 ceiling: {offenders}. "
        "Split them, or (only with justification) add to LENGTH_ALLOWLIST."
    )


def test_length_allowlist_has_no_stale_entries() -> None:
    """Keep the allowlist honest: an entry that no longer exceeds the ceiling must be removed."""
    stale = {}
    for rel in LENGTH_ALLOWLIST:
        path = REPO_ROOT / rel
        if not path.exists():
            stale[rel] = "missing"
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines <= MAX_MODULE_LINES:
            stale[rel] = lines
    assert not stale, (
        f"Stale LENGTH_ALLOWLIST entries (now under the ceiling or gone): {stale}. "
        "Remove them so the ceiling is enforced for these files again."
    )


def test_broad_except_does_not_increase() -> None:
    """CH-24: broad `except` count only ratchets down."""
    scoped = [r for r in _tracked_py_files() if _in_scope(r, _EXCLUDE_PREFIXES)]
    broad, _, by_file = _count_exceptions(scoped)
    assert broad <= BROAD_EXCEPT_BASELINE, (
        f"Broad except count rose to {broad} (baseline {BROAD_EXCEPT_BASELINE}). "
        f"CH-24 forbids new broad/blanket catches. By file: {by_file}"
    )


def test_swallowed_exceptions_do_not_increase() -> None:
    """CH-24: swallowed (`pass`/`continue`-only) `except` count only ratchets down."""
    scoped = [r for r in _tracked_py_files() if _in_scope(r, _EXCLUDE_PREFIXES)]
    _, swallow, _ = _count_exceptions(scoped)
    assert swallow <= SWALLOW_BASELINE, (
        f"Swallowed-exception count rose to {swallow} (baseline {SWALLOW_BASELINE}). "
        "CH-24 forbids new swallow-and-continue handlers."
    )


def test_framework_registry_has_single_source() -> None:
    """CH-05: the framework-id -> adapter map is defined as a dict literal in exactly one module."""
    definers = []
    for rel in _tracked_py_files():
        if not _in_scope(rel, _EXCLUDE_PREFIXES) or rel.startswith("tests/"):
            continue
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            # Module-level `FRAMEWORKS`/`_ADAPTERS` assigned a *dict literal*
            # (an `import ... as _ADAPTERS` alias is not an ast.Assign-with-Dict).
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if names & {"FRAMEWORKS", "_ADAPTERS"} and isinstance(node.value, ast.Dict):
                definers.append(rel)
    assert definers == ["agentteams/frameworks/registry.py"], (
        f"Framework registry must be a single dict literal in registry.py; found definers: {definers}"
    )
