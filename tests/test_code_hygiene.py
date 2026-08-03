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
import re
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
BROAD_EXCEPT_BASELINE = 14      # except Exception/BaseException/bare. Narrowed over the sweep
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
                                # 13→14: agentteams/research/browser.py's _safe_render — the same
                                # "adversarial external content via a third-party parser" boundary
                                # as _extract_pdf_text above, one level heavier (a real browser
                                # driving an arbitrary untrusted page: navigation timeouts, missing
                                # browser binaries, protocol errors, page crashes — not enumerable
                                # in advance). browser_fetch/browser_screenshot both route through
                                # this ONE shared helper specifically so the module contributes a
                                # single new broad catch, not two — a code-hygiene-audit-driven
                                # consolidation (see tmp/by-week/2026-W30/
                                # web-browsing-playwright-cli.plan.md, "Post-implementation audit
                                # outcome" — where this exact consolidation is explained).
SWALLOW_BASELINE = 35           # except clause whose body is only pass/continue (narrow catches =
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
                                # 33→35 (this comment previously said 34, the pre-existing baseline
                                # value, but the true pre-session count measured against git HEAD
                                # with untracked files excluded — `git stash -u` — was 33; that 1-unit
                                # of slack predates this change and is unrelated to it): +2 from
                                # scripts/goose-run-resilient.py (new file, 2026-07-24) —
                                # classify_request_log skips an unparseable llm_request.jsonl
                                # line (continue; narrowed to json.JSONDecodeError/ValueError, same
                                # tolerant-parser boundary as verify.py above) and
                                # find_and_classify_latest_run skips a log candidate whose mtime
                                # can't be read (continue; OSError, e.g. a file deleted mid-glob) —
                                # both are the fail-closed contract's "can't confidently classify ->
                                # treat as alive, never as dead" boundary, not silent-failure debt.



def _tracked_files() -> list[str]:
    """Return tracked + untracked-non-ignored paths of any type (relative, POSIX).

    The ``*.py``-scoped sibling above cannot serve the path-based rules, which
    must see every file type. Same rationale otherwise: include untracked-but-
    not-ignored paths so a newly added artifact is caught before it is staged,
    and fail loud if git is absent (CH-23).
    """
    out = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT, text=True,
    )
    return [line for line in out.split("\0") if line]


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


# ---------------------------------------------------------------------------
# Path- and filename-based hygiene rules (CH-01, CH-11, CH-15)
#
# These three were selected from the mechanization classification in
# references/code-hygiene-mechanization.reference.md on one
# criterion: what a PASS establishes is unambiguous. Each is a pure statement
# about tracked paths, so a clean result means exactly what it says and nothing
# more. CH-18 was considered and rejected — a naive "version-numbered sibling"
# probe flags dated work summaries, so its semantics are not settled.
#
# Scope for all three: files tracked by git in *this* repository. They say
# nothing about a consuming project's tree, which agentteams does not scan.
# ---------------------------------------------------------------------------

_BACKUP_SUFFIX_RE = re.compile(r"(\.bak|~|\.orig|\.rej)$")
_LEGACY_DIR_RE = re.compile(r"(^|/)(oldScripts|legacy|deprecated)/")
_TEST_FILE_RE = re.compile(r"(^|/)test_[^/]+\.py$")


def test_ch01_no_backup_files_tracked() -> None:
    """CH-01: no backup or merge-reject artifacts are tracked.

    A PASS means no tracked path ends in .bak, ~, .orig or .rej. It says nothing
    about untracked working copies, which are the developer's business.
    """
    offenders = [f for f in _tracked_files() if _BACKUP_SUFFIX_RE.search(f)]
    assert not offenders, (
        f"CH-01: backup artifacts are tracked: {offenders}. "
        "Delete them or add the pattern to .gitignore."
    )


def test_ch11_tests_live_in_the_tests_directory() -> None:
    """CH-11: test modules are not scattered alongside source.

    A PASS means every tracked ``test_*.py`` is under ``tests/``. It does not
    check that the tests are good, reachable, or run by CI.
    """
    offenders = [
        f
        for f in _tracked_files()
        if _TEST_FILE_RE.search(f) and not f.startswith("tests/")
    ]
    assert not offenders, (
        f"CH-11: test modules outside tests/: {offenders}. "
        "Move them, or rename if they are not tests."
    )


def test_ch15_no_legacy_directories_in_source() -> None:
    """CH-15: no oldScripts/legacy/deprecated directories are tracked.

    A PASS means no tracked path sits under a directory with one of those three
    names. Superseded code kept under any *other* name is invisible to this
    check — the rule's intent is broader than the check's reach.
    """
    offenders = [f for f in _tracked_files() if _LEGACY_DIR_RE.search(f)]
    assert not offenders, (
        f"CH-15: files under a legacy directory: {offenders}. "
        "Delete them; git history is the archive."
    )


def test_audit_ledger_makes_no_structurally_false_claims() -> None:
    """The audit ledger's rows must resolve against the tree.

    A regression guard, not a completeness check. It asserts zero DEFECTs — a row
    claiming ``absent`` while naming a surface, a surface that does not resolve,
    a template_file that does not exist. It deliberately tolerates REVIEW rows:
    ``unreviewed`` is a legitimate transient state for a newly registered
    template, and failing on it would push authors toward guessing a disposition
    rather than leaving it honest.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_verify_ledger", REPO_ROOT / "scripts" / "verify_audit_ledger.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    findings, _stats = module.verify()
    defects = [f for f in findings if f["status"] == "DEFECT"]
    assert not defects, "ledger rows make structurally false claims: " + "; ".join(
        f"{f['audit_id']}: {f['issue']}" for f in defects
    )


# ---------------------------------------------------------------------------
# The mechanization classification must describe itself accurately
#
# references/code-hygiene-mechanization.reference.md records which CH- rules are
# machine-checked and which are judgment. Its Summary counts were hand-tallied
# twice and wrong twice — once reporting eleven judgment rules where the table
# held ten, once leaving four rules filed as unwritten backlog after their tests
# had shipped. The counts are derived here so a stale tally fails the suite.
# ---------------------------------------------------------------------------

_MECHANIZATION_REF = REPO_ROOT / "references/code-hygiene-mechanization.reference.md"

# Two axes in one column, distinguished by suffix: -ed means a check exists,
# -able means one could. `partly mechanized` was split out of `partly
# mechanizable` on 2026-07-29 because the latter was carrying both meanings.
_STATUSES = (
    "mechanized",
    "partly mechanized",
    "mechanizable",
    "partly mechanizable",
    "judgment",
)

# Statuses that assert a check exists, and therefore must cite it.
_COVERAGE_STATUSES = ("mechanized", "partly mechanized")


def _mechanization_rows() -> dict[str, str]:
    """Return {rule_id: status} parsed from the classification table.

    Status is read from the second pipe-delimited cell with markdown emphasis
    stripped. Matching is longest-first so ``partly mechanizable`` is not read as
    ``mechanizable``.
    """
    rows: dict[str, str] = {}
    for line in _MECHANIZATION_REF.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*(CH-\d\d)\b[^|]*\|([^|]*)\|", line)
        if not match:
            continue
        cell = match.group(2).replace("*", "").strip().lower()
        for status in sorted(_STATUSES, key=len, reverse=True):
            if cell == status:
                rows[match.group(1)] = status
                break
        else:  # pragma: no cover - guards a malformed edit, not a code path
            raise AssertionError(
                f"{_MECHANIZATION_REF.name}: {match.group(1)} has unrecognised "
                f"status {cell!r}; expected one of {_STATUSES}"
            )
    return rows


def test_mechanization_table_covers_every_rule_exactly_once() -> None:
    """Every CH- rule in the catalogue appears in the classification, once."""
    rows = _mechanization_rows()
    catalogue = set(re.findall(r"^### (CH-\d\d) — ", _DOMAIN_CH.read_text(encoding="utf-8"), re.M))
    assert rows, f"parsed no rows from {_MECHANIZATION_REF.name}"
    missing = sorted(catalogue - set(rows))
    extra = sorted(set(rows) - catalogue)
    assert not missing, f"rules in the catalogue but absent from the classification: {missing}"
    assert not extra, f"rules classified but absent from the catalogue: {extra}"


def test_mechanization_summary_counts_match_the_table() -> None:
    """CH-20 self-consistency: the Summary must be derived from the table.

    The Summary is the figure other documents quote, so a drifted count
    propagates. This recomputes it and compares.
    """
    rows = _mechanization_rows()
    actual = {status: sum(1 for v in rows.values() if v == status) for status in _STATUSES}

    body = _MECHANIZATION_REF.read_text(encoding="utf-8")
    stated: dict[str, int] = {}
    for status in _STATUSES:
        match = re.search(rf"^\|\s*{re.escape(status)}\s*\|\s*(\d+)\s*\|", body, re.M)
        assert match, f"Summary table has no row for {status!r}"
        stated[status] = int(match.group(1))

    assert stated == actual, (
        f"Summary counts drifted from the table: stated {stated}, table {actual}. "
        "Update the Summary, and anything quoting it."
    )
    assert sum(actual.values()) == len(rows)


def test_mechanized_rows_name_a_resolvable_surface() -> None:
    """A row claiming a check exists must cite the check.

    Without this, the two statuses that assert coverage could be set with no
    implementation behind them — the failure mode the file was written to expose,
    committed inside the file itself. This is also what caught the vocabulary
    defect: five `partly mechanizable` rows tripped it, which is how the two
    senses of that phrase were found sharing one column.
    """
    text = _MECHANIZATION_REF.read_text(encoding="utf-8")
    unsupported = []
    for line in text.splitlines():
        match = re.match(r"^\|\s*(CH-\d\d)\b[^|]*\|([^|]*)\|(.*)$", line)
        if not match:
            continue
        status = match.group(2).replace("*", "").strip().lower()
        if status not in _COVERAGE_STATUSES:
            continue
        # A citation is a `module.py::name`, an `agentteams.<mod>` dotted path, or
        # a backticked test/function name.
        reason = match.group(3)
        if not re.search(r"`[^`]*(::|\bagentteams\.|_check_|test_)[^`]*`", reason):
            unsupported.append(match.group(1))
    assert not unsupported, (
        f"rows assert coverage without naming an implementing surface: {unsupported}"
    )


# ---------------------------------------------------------------------------
# No tracked file may embed an operator's absolute home path
#
# Tracked artifacts are published; an absolute path in one leaks the operator's
# username and directory layout. This was found in seven places across today's
# work: the memory index's document paths (2135), three bridge artifact types, four
# archived baseline captures, two work summaries, one documentation example and two
# test fixtures. Each was a separate emission site with its own reason, which is why
# the guard is repo-wide rather than per-subsystem.
# ---------------------------------------------------------------------------

_HOME_PATH_RE = re.compile(rb"(?:/Users/|/home/|[A-Za-z]:\\Users\\)([A-Za-z0-9._-]+)[/\\\\]")

#: Home-directory names that are documented placeholders, not real operators.
#: A generic example path is good practice — the defect is embedding a *real* one —
#: so the guard flags any name outside this set rather than any absolute path at all.
_PLACEHOLDER_HOME_NAMES: frozenset[str] = frozenset({
    "me", "you", "user", "username", "alice", "bob", "johndoe", "example", "op", "x",
})

#: Tracked paths permitted to contain an absolute home path, with the reason.
#: Not a convenience list: each entry is a case where removing the path would make
#: the file *less* truthful.
# Emptied 2026-07-30. The sole entry was `.claude/agents/references/memory-index.json`, exempted
# on the grounds that its snippets are "verbatim excerpts" and that redacting one "would make the
# index misquote its source while `source_hash` still attested to the original".
#
# That reasoning does not survive inspection. Stored snippets were never verbatim: they are
# truncated to 480 characters, have their newlines collapsed to spaces, and have leading heading
# lines stripped. `source_hash` attests to the *source document*, not to the excerpt. Redacting an
# absolute path is one more transformation of the same kind, and unlike the others it leaves a
# visible `<path>` marker rather than silently altering the text.
#
# Weighed against that: the exemption was permitting 49 absolute `/Users/...` strings in a tracked,
# already-pushed artifact, some naming an unrelated repository — and because the source documents
# are frequently local-only, the index was the *only* place those strings were committed. The leak
# was real; the fidelity it was traded for was not.
#
# `agentteams.memory_index.redact_local_paths` now removes them at snippet-construction time, so
# the file needs no exemption. Re-adding an entry here should require the same standard: name what
# is being traded away, and why the leak is worth it.
_HOME_PATH_ALLOWLIST: dict[str, str] = {}


def test_no_tracked_file_embeds_an_absolute_home_path() -> None:
    """Repo-wide guard. Read as bytes so a binary file needs no swallowed decode error."""
    offenders: dict[str, str] = {}
    for rel in _tracked_files():
        if rel in _HOME_PATH_ALLOWLIST or rel.startswith(("src/", "tmp/")):
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        for match in _HOME_PATH_RE.finditer(path.read_bytes()):
            name = match.group(1).decode("utf-8", errors="replace")
            if name not in _PLACEHOLDER_HOME_NAMES:
                offenders[rel] = match.group(0).decode("utf-8", errors="replace")
                break
    assert not offenders, (
        f"tracked file(s) embed a real absolute home path, leaking the operator's "
        f"username: {offenders}. Use a repo-relative path, `~`, Path.home(), or one of "
        f"the documented placeholder names {sorted(_PLACEHOLDER_HOME_NAMES)}."
    )


def test_home_path_allowlist_is_justified_and_current() -> None:
    """The exemption list is the part that rots, so it is checked too."""
    thin = {k: v for k, v in _HOME_PATH_ALLOWLIST.items() if len(v.split()) < 15}
    assert not thin, f"_HOME_PATH_ALLOWLIST entries need a substantive reason: {sorted(thin)}"
    for rel in _HOME_PATH_ALLOWLIST:
        path = REPO_ROOT / rel
        assert path.exists(), f"allowlisted {rel} no longer exists; remove the entry"
        assert _HOME_PATH_RE.search(path.read_bytes()), (
            f"{rel} no longer contains an absolute home path — remove it from "
            "_HOME_PATH_ALLOWLIST so the guard covers it"
        )


# ---------------------------------------------------------------------------
# CH-06 — terminal commands <=5 lines, no inline heredocs (agent instructions)
#
# Selected from the classification's `mechanizable` column on the one criterion its
# standing caution demands: the definition is complete without inventing anything.
# CH-06 names its own scope ("in agent instructions"), and audit.py already treats
# ``` fences as a first-class construct, so nothing had to be decided here.
#
# The two halves are NOT equally mechanized, and conflating them would be the exact
# hazard the classification exists to expose:
#
#   * heredocs      — 0 violations. A clean PASS means what the rule says.
#   * >5-line blocks — 10 pre-existing violations, so this half is a RATCHET. A PASS
#                      means "no NEW long command block", not "commands are short".
#
# CH-06 is therefore filed `partly mechanized`, not `mechanized`.
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^[ \t]*```([A-Za-z0-9_+-]*)\s*$")
_HEREDOC_RE = re.compile(r"<<-?\s*'?[A-Z_][A-Z0-9_]*'?")
#: Fence languages that denote a terminal command block. The empty string counts:
#: an unlabelled fence in an agent instruction is overwhelmingly a shell snippet.
_SHELL_FENCE_LANGS = frozenset({"", "bash", "sh", "shell", "zsh", "console", "terminal"})
CH06_MAX_COMMAND_LINES = 5
#: Verified 2026-07-29. Only ever decrease. Raising it requires a reviewed reason.
CH06_LONG_BLOCK_BASELINE = 10


def _shell_blocks() -> list[tuple[str, int, list[str]]]:
    """Return (relative path, 1-based fence line, body lines) per shell fence."""
    out: list[tuple[str, int, list[str]]] = []
    templates = REPO_ROOT / "agentteams/templates"
    for path in sorted(templates.rglob("*.template.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        rel = str(path.relative_to(REPO_ROOT))
        i = 0
        while i < len(lines):
            match = _FENCE_RE.match(lines[i])
            if not match:
                i += 1
                continue
            lang = match.group(1).lower()
            body: list[str] = []
            j = i + 1
            while j < len(lines) and not _FENCE_RE.match(lines[j]):
                body.append(lines[j])
                j += 1
            if lang in _SHELL_FENCE_LANGS:
                out.append((rel, i + 1, body))
            i = j + 1
    return out


def test_ch06_no_inline_heredocs_in_agent_instructions() -> None:
    """CH-06 (heredoc half): fully enforced — a clean PASS means the rule holds.

    Scope: shell fences in `agentteams/templates/**`. Says nothing about heredocs in
    prose outside a fence, or in a consumer's own edits after emission.
    """
    offenders = [
        f"{rel}:{line}"
        for rel, line, body in _shell_blocks()
        if any(_HEREDOC_RE.search(b) for b in body)
    ]
    assert not offenders, (
        f"CH-06 forbids inline heredocs in agent instructions: {offenders}. "
        "Save the script to a file and invoke it."
    )


def test_ch06_long_command_blocks_do_not_increase() -> None:
    """CH-06 (length half): a RATCHET, not a conformance check.

    10 blocks already exceed the ceiling; this only stops an 11th. It adjudicates
    none of the existing ten, several of which may be legitimately illustrative
    rather than commands an agent is meant to run.
    """
    long_blocks = [
        (rel, line, len(body))
        for rel, line, body in _shell_blocks()
        if len(body) > CH06_MAX_COMMAND_LINES
    ]
    assert len(long_blocks) <= CH06_LONG_BLOCK_BASELINE, (
        f"Command blocks over {CH06_MAX_COMMAND_LINES} lines rose to "
        f"{len(long_blocks)} (baseline {CH06_LONG_BLOCK_BASELINE}). CH-06 forbids new "
        f"ones — extract to a script file. Current: {long_blocks}"
    )


def test_ch06_baseline_is_not_stale() -> None:
    """Keep the ratchet honest: a baseline above the true count hides regressions."""
    actual = sum(1 for _r, _l, b in _shell_blocks() if len(b) > CH06_MAX_COMMAND_LINES)
    assert actual == CH06_LONG_BLOCK_BASELINE, (
        f"CH06_LONG_BLOCK_BASELINE is {CH06_LONG_BLOCK_BASELINE} but the true count is "
        f"{actual}. Lower it to {actual} so the ratchet stays tight."
    )


#: How close a module may come to MAX_MODULE_LINES before the suite says so. Sized from
#: experience: agentteams/cli/generate.py sat at 998/1000 and was pushed over TWICE in a single
#: day by ordinary three-to-eight-line additions, each time forcing an unrelated carve
#: (cli/json_mode.py, cli/exit_codes.py, cli/output_target.py) before unrelated work could land.
#: None of those authors did anything wrong; they simply had no warning until they were already
#: blocked. 25 lines is roughly "one more normal change" of runway.
CEILING_WARN_MARGIN = 25

#: Modules already inside the warning margin on 2026-07-30, with their line counts. Measured, not
#: chosen: adding the guard revealed FIVE crowded modules, three within ten lines of the ceiling,
#: so this is a systemic condition rather than one module's problem — and decomposing five
#: modules is not a change that belongs inside an unrelated remediation round.
#:
#: A RATCHET, on the CH-24 `BROAD_EXCEPT_BASELINE` precedent: a module may shrink freely, but a
#: baselined module that GROWS, or a new module entering the margin, fails. The condition
#: therefore cannot spread or worsen while the decomposition stays queued.
#: 2026-07-31: cli/artifacts.py left this set — the code-index half (a gitignored rebuildable
#: cache) was carved to cli/code_index_artifacts.py, taking it 976 -> 621. The split was already
#: latent: a section rule separated the two clusters, and they differ in kind — the memory index
#: writes a COMMITTED artifact, the code index a local cache.
#: 2026-07-31 (later): cli/generate.py left too, 991 -> 958. This one was NOT chosen — a six-line
#: comment took it to 998 and this ratchet refused the commit, which is the guard working as
#: designed. The carve it forced was the right one anyway: the inlined orphan-agent advisory
#: moved to build_team._report_orphan_agent_files, next to the reference-doc advisory it mirrors.
#: The two had been describing the same blind spot from two different files.
#: 2026-07-31 (later still): frameworks/goose.py left too, 996 -> 834. Also forced — single-
#: sourcing the research-capability guidance from capability_hints.py pushed it to 1003 and this
#: ratchet refused the commit. The seam was already there: the three document-content generators
#: (_goosehints_content, _resilient_runner_content, _goose_capabilities_content) build files the
#: adapter *emits*, while the rest of the module is adapter *behaviour*. They moved to
#: frameworks/goose_docs.py and are re-exported, so no import changed.
#:
#: Two of the four baselined modules have now been carved by an ordinary edit hitting the wall
#: rather than by a decision to decompose. That is the ratchet doing its job — but it also means
#: the remaining two will be carved the same way, at whatever moment someone is least ready.
#: 2026-08-01: audit.py left this list the way the note above predicted — an ordinary addition
#: (one new agent-contract check) hit the wall at 999/1000, and the carve happened then rather
#: than when someone planned it. The agent-contract checks moved to audit_agent_contract.py and
#: AuditFinding to audit_types.py (the carved module needed the type, audit needed the functions —
#: a cycle the shared module breaks). 999 -> 860. Three of the four originally-baselined modules
#: have now been carved this way; graph.py is the last one, and it will go the same way.
#: Empty, for the first time. `graph.py` was the last entry, at 992 lines with eight of runway,
#: and was carved to 770 on 2026-08-03 (front-matter extraction to `graph_inputs.py`).
#:
#: That carve is the sixth under CH-07 and the **first chosen rather than forced**. The other
#: five each happened when an ordinary edit pushed a module over the ceiling mid-change — the
#: pattern the remediation ledger predicted and then recorded four times over. An empty baseline
#: is the state where that stops being the only way carves happen.
#:
#: An entry here is a module allowed to sit close to the ceiling. Adding one is a decision;
#: `test_the_ceiling_baseline_is_current` fails if an entry stops being crowded, so it cannot
#: quietly outlive its reason the way `audit.py 999` did.
CEILING_MARGIN_BASELINE: dict[str, int] = {
    # Added 2026-08-03, deliberately and with the carve logged as due.
    #
    # `--reconcile-front-matter` and `--pin-templates` each pushed this module into the margin,
    # and each time the logic was moved OUT of it — into `front_matter_reconcile` and
    # `template_pins` — leaving only a dispatch stub behind. After both moves it still sits at
    # 979: the module has no room left for even a five-line call site.
    #
    # Three consecutive workarounds is the point at which working around it becomes the defect.
    # The honest options were a real carve or a recorded exception; a carve of the main pipeline
    # module at the end of a long session is the compounding pattern this repo has been bitten
    # by, so this is the exception, and the carve is logged as due in the remediation ledger.
    #
    # The seam is already visible: the standalone "do one thing and exit" modes (adopt-orphans,
    # restore-backup, scan-security, check-budget, --check, and now the pinning dispatch) are a
    # different concern from the render/emit pipeline they are interleaved with.
    "agentteams/cli/generate.py": 979,
}


def _crowded_modules() -> dict[str, int]:
    """Return in-scope modules within :data:`CEILING_WARN_MARGIN` of the ceiling."""
    out: dict[str, int] = {}
    for rel in _tracked_py_files():
        if not _in_scope(rel, _LENGTH_EXCLUDE_PREFIXES) or rel in LENGTH_ALLOWLIST:
            continue
        lines = len((REPO_ROOT / rel).read_text(encoding="utf-8").splitlines())
        if MAX_MODULE_LINES - CEILING_WARN_MARGIN <= lines <= MAX_MODULE_LINES:
            out[rel] = lines
    return out


def test_no_new_module_crowds_the_ceiling() -> None:
    """Early warning with runway, unlike `test_no_new_oversized_modules`.

    That test fires only once a module is already over and the offending change is already
    written — which is how three unrelated carves ended up inside one session. This fires while
    there is still room to decide deliberately.
    """
    newly_crowded = {
        rel: n for rel, n in _crowded_modules().items() if rel not in CEILING_MARGIN_BASELINE
    }
    assert not newly_crowded, (
        f"Module(s) newly within {CEILING_WARN_MARGIN} lines of the {MAX_MODULE_LINES}-line "
        f"CH-07 ceiling: {newly_crowded}.\n"
        f"The next ordinary change will breach it and force a carve mid-edit. Decompose now, or "
        f"add to CEILING_MARGIN_BASELINE if the size is genuinely warranted."
    )


def test_crowded_modules_do_not_grow() -> None:
    """The ratchet: a module already in the margin may shrink, never grow."""
    current = _crowded_modules()
    grown = {
        rel: (baseline, current[rel])
        for rel, baseline in CEILING_MARGIN_BASELINE.items()
        if rel in current and current[rel] > baseline
    }
    assert not grown, (
        "Module(s) already crowding the CH-07 ceiling grew further "
        f"(baseline -> now): {grown}.\n"
        "These have no runway left. Take lines out, or carve, before adding more."
    )


def test_the_ceiling_baseline_is_current() -> None:
    """A baseline listing a module that has since been decomposed is stale bookkeeping.

    Mirrors `test_home_path_allowlist_is_justified_and_current`: an exemption that no longer
    describes reality quietly stops protecting anything.
    """
    current = _crowded_modules()
    stale = sorted(set(CEILING_MARGIN_BASELINE) - set(current))
    assert not stale, (
        f"CEILING_MARGIN_BASELINE lists module(s) no longer crowding the ceiling: {stale}. "
        f"Remove them so the baseline keeps meaning what it says."
    )
