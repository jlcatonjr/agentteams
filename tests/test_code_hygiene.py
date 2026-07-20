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
    # build_team.py left at Step D (now a 833-line shim); cli/app.py left at Step D2
    # (1174 -> 263 after the generate pipeline moved to cli/generate.py, 939 lines).
    # Both were reduced by CH-07 carves: emit.py 1584 -> 1080 (backup subsystem ->
    # agentteams/backup.py + atomicio.py); analyze.py 1507 -> 1276 (_plan_output_files ->
    # agentteams/output_plan.py). Getting under the 1000 ceiling would need a second carve
    # each (emit: fence/merge -> fences.py; analyze: _format_*/_default_* -> manifest_format.py);
    # deferred — proven to work but a larger blast radius.
})
BROAD_EXCEPT_BASELINE = 13      # except Exception/BaseException/bare. Narrowed over the sweep
                                # (Steps E + remaining-items I6: commands, render_pipeline, ingest,
                                # mcp_emit). The remaining 11 are justified external/isolation/
                                # never-block/cleanup-reraise boundaries, each annotated with a
                                # CH-24 rationale (visible WARN or re-raise, not silent swallow).
                                # 11→13: agentteams/research/ (the research + fact-verification
                                # baseline capability) added two — search.py's _extract_pdf_text
                                # (third-party pypdf parsing of adversarial external PDF bytes; the
                                # exception surface for arbitrary untrusted input isn't enumerable
                                # in advance) and reputable.py's ThreadPoolExecutor future-result
                                # loop (thread-isolation boundary — one worker's unexpected failure
                                # must not abort the whole batch, matching architecture.py's
                                # existing best-effort-mapper precedent below). Every OTHER
                                # exception site this package added was narrowed to named types
                                # (httpx.HTTPError, ValueError, json.JSONDecodeError, etc.) instead
                                # of raising this baseline further — see the CH-24 comments inline.
SWALLOW_BASELINE = 34           # except clause whose body is only pass/continue (narrow catches =
                                # known-recoverable external boundaries; the ratchet blocks new ones).
                                # 30→31: architecture.py skips files that fail ast.parse (SyntaxError/
                                # ValueError) — a best-effort module mapper must tolerate an
                                # unparseable source file rather than abort the whole map.
                                # 31→34: agentteams/research/ added three — reputable.py's
                                # ThreadPoolExecutor loop (continue; same isolation boundary as
                                # above) and verify.py's two JSON-extraction attempts (pass; a
                                # tolerant multi-strategy JSON parser trying progressively looser
                                # extraction strategies, each individually allowed to fail and fall
                                # through to the next — narrowed to json.JSONDecodeError, not a
                                # blanket catch, per the CH-24 comments inline).


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


def test_artifacts_schema_anchor_resolves_to_repo_schemas() -> None:
    """Step C re-anchor guard: cli/artifacts.py uses Path(__file__).parents[2]/schemas
    after the move; assert that resolves to the real repo-root schemas dir with the
    four artifact schemas present (a wrong anchor would silently misvalidate)."""
    from agentteams.cli import artifacts
    schema_dir = Path(artifacts.__file__).resolve().parents[2] / "schemas"
    assert schema_dir == (REPO_ROOT / "schemas").resolve(), schema_dir
    for name in (
        "delivery-receipt.schema.json", "eval-suite.schema.json",
        "model-routing.schema.json", "memory-index.schema.json",
    ):
        assert (schema_dir / name).exists(), f"missing {name} at re-anchored path"


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


# Modules produced by the build_team decomposition refactor. CH-22 requires their
# function signatures to stay fully type-annotated (the guard below enforces it).
_REFACTOR_MODULES = (
    "agentteams/cli/app.py",
    "agentteams/cli/generate.py",
    "agentteams/cli/parser.py",
    "agentteams/cli/render_pipeline.py",
    "agentteams/cli/commands.py",
    "agentteams/cli/artifacts.py",
    "agentteams/cli/security_gate.py",
    "agentteams/frameworks/registry.py",
    "agentteams/errors.py",
    # CH-07 module extractions (keep the annotation ratchet honest on new modules)
    "agentteams/atomicio.py",
    "agentteams/backup.py",
    "agentteams/output_plan.py",
    "agentteams/cli/schema_cache.py",
)


_UNIVERSAL_CH = REPO_ROOT / "agentteams/templates/universal/code-hygiene.template.md"
_DOMAIN_CH = REPO_ROOT / "agentteams/templates/domain/code-hygiene-rules-reference.template.md"


def test_extension_rules_present_in_both_templates() -> None:
    """Parity guard: CH-26/CH-27/CH-28 must appear in BOTH the universal agent
    template and the domain enforcement reference, so the agent summary and the
    enforcement catalog never drift apart (the same CH-20 hazard the rules guard)."""
    universal = _UNIVERSAL_CH.read_text(encoding="utf-8")
    domain = _DOMAIN_CH.read_text(encoding="utf-8")
    for rule in ("CH-26", "CH-27", "CH-28"):
        assert rule in universal, f"{rule} missing from {_UNIVERSAL_CH.name}"
        assert rule in domain, f"{rule} missing from {_DOMAIN_CH.name}"


def test_ch28_constraints_sentence_present() -> None:
    """CH-28 is only safe (no CH-20 contradiction with CH-10/CH-22/CH-23/CH-24 or
    the refactor agents) because it front-loads the constraint that required
    changes and sanctioned refactors override it. Guard that sentence so a future
    trim cannot silently reintroduce the contradiction."""
    universal = _UNIVERSAL_CH.read_text(encoding="utf-8")
    domain = _DOMAIN_CH.read_text(encoding="utf-8")
    # The "required changes still apply even when they add lines" constraint must
    # be stated verbatim somewhere; the universal template carries it un-wrapped.
    assert "even when they add lines" in " ".join(universal.split())
    # Both templates must cite the rules CH-28 defers to, so the exemption is explicit.
    for ref in ("CH-10", "CH-22", "CH-23", "CH-24", "CH-07", "CH-08"):
        assert ref in domain, f"CH-28 constraint must reference {ref} in {_DOMAIN_CH.name}"


def test_refactor_modules_are_fully_type_annotated() -> None:
    """CH-22: every module-level function in the refactor's cli/* + registry + errors
    modules must annotate its parameters (except self/cls) and its return type.
    Coverage is currently 100%; this ratchet keeps new code from regressing it.
    (Runtime CH-22 guards are used where misuse is plausible, e.g.
    security_gate.set_migrate_exemption raising TypeError on a non-bool.)"""
    gaps = []
    for rel in _REFACTOR_MODULES:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                unannotated = [
                    a.arg for a in node.args.args
                    if a.annotation is None and a.arg not in ("self", "cls")
                ]
                if unannotated or node.returns is None:
                    gaps.append(
                        f"{rel}:{node.lineno} {node.name} "
                        f"(unannotated params={unannotated}, has_return={node.returns is not None})"
                    )
    assert not gaps, f"CH-22: refactor modules have unannotated signatures: {gaps}"
