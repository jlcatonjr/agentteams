"""checks_static.py — phase 6, checks F-1 to F-3: the ones that read source, not reports.

These three are the mechanical form of three process failures measured on 2026-08-06. Each is
stated with the failure it exists to make impossible:

* **F-1 — a verification that always passes.** A shipped payload-digest check hashed a tuple
  of five keys the producer never emits. Overlap: zero. So ``digest(payload) == digest({})``,
  a check that passes for every input, reported as "verification shipped". Every verifier now
  needs a test proving its output *changes* when its input changes, and a negative control
  proving it does not change for irrelevant input.
* **F-2 — a fix wired to one of two paths.** The capability-key sweep was attached to one of
  the two ``emit.emit_all`` call sites in ``cli/generate.py``. ``--update --merge`` — the
  command an actual fleet sweep runs — never reached it: 27 of 31 agents migrated, and the run
  reported success.
* **F-3 — hand-rolling a resolution the tool already provides.** Three times in one session; a
  hand-written ``for d in */`` missed 34 workspaces and 719 exposed agents. The handoff calls
  this *the single highest-value structural fix*.

**Every ledger row must resolve.** A rule naming a symbol that does not exist matches nothing
and passes silently — F-1 wearing the shape of whichever check it was written for. So an
unresolvable row is reported as a finding rather than skipped, in all three checks.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from agentteams import integrity
from agentteams.redteam.registry import (
    CALLPATH_PARITY_REL,
    MIN_REASON_CHARS,
    RESOLUTION_EXEMPTIONS_REL,
    VERIFIERS_REL,
    SelfAuditFinding,
    read_csv_ledger,
)

#: Name shapes that make a function a candidate verifier. Chosen to over-collect: a symbol
#: wrongly collected costs one ledger row saying why it is not a verifier, while a symbol
#: missed costs an unverified control. The asymmetry decides the threshold.
_VERIFIER_PREFIXES = ("verify", "_verify", "check", "_check", "assert", "_assert")
_VERIFIER_SUFFIXES = ("_digest", "_matches", "_is_sound")

#: The two ledger `kind` values. `verifier` owes a sensitivity test and a negative control;
#: `not-a-verifier` owes a reason. Both cost a diff, so neither can be adopted silently.
KIND_VERIFIER = "verifier"
KIND_NOT_VERIFIER = "not-a-verifier"

#: Canonical APIs that must be called rather than re-implemented (F-3), keyed by the
#: hand-rolled construct they replace.
CANONICAL_RESOLVERS: dict[str, str] = {
    "workspace-enumeration": "agentteams.fleet.discover_workspaces",
    "vcs-status": "agentteams.fleet._is_git_repo (which shells to `git rev-parse`)",
    "descriptor-lookup": "agentteams.fleet._resolve_descriptor",
}

#: Path prefixes exempt from F-3 **because they are the definition sites** — provenance, not
#: shape (CH-30). ``fleet.py`` is where the canonical resolvers live; forbidding them there
#: would forbid the API itself.
_F3_DEFINITION_SITES: tuple[str, ...] = ("agentteams/fleet.py",)

#: Directories F-3 sweeps in addition to every module that imports ``agentteams.fleet``.
_F3_ALWAYS_IN_SCOPE: tuple[str, ...] = ("agentteams/redteam/",)

#: Directory trees F-3 sweeps at all. **Tests are out of scope**, and the reason is a role
#: distinction rather than a convenience: a test that writes a descriptor fixture and then
#: asserts the resolver finds it is *exercising* the canonical API, not re-implementing it.
#: Measured before deciding: including ``tests/`` produced 16 findings, all of them fixture
#: construction in ``tests/test_fleet.py`` — a ledger of 16 permanent exemptions that nobody
#: would ever read again, which is how an exemption list stops describing the system.
_F3_ROOTS: tuple[str, ...] = ("agentteams", "scripts")

#: Glob patterns that enumerate *directory children*. ``parent.glob("*")`` is the literal
#: construct that missed 34 workspaces and 719 exposed agents. ``rglob`` is deliberately NOT
#: matched: ``root.rglob("*")`` is a recursive walk over one repository's files, which is
#: ordinary work — ``stale_detector.py`` does exactly that and is right to.
_DIRECTORY_GLOB_PATTERNS = frozenset({"*", "*/"})

#: The literals that mark a hand-rolled descriptor lookup. ``_resolve_descriptor``'s docstring
#: is literally *"Stub-trap fix: prefer the rich brief over the thin stub"* — code that names
#: either location directly has opted out of that fix.
_DESCRIPTOR_LITERALS = frozenset({"_build-description.json", ".agentteams"})


@dataclass(frozen=True)
class _Discovered:
    """A verifier-shaped function found in source."""

    module: str
    symbol: str


def _parse(path: Path) -> ast.Module:
    """Parse a Python file into an AST."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _looks_like_a_verifier(name: str) -> bool:
    """True when a function name matches the verifier shape."""
    if any(name.endswith(suffix) for suffix in _VERIFIER_SUFFIXES):
        return True
    return any(
        name == prefix or name.startswith(prefix + "_") for prefix in _VERIFIER_PREFIXES
    )


def _module_scope(root: Path) -> list[str]:
    """Return the modules F-1 discovers verifiers in, as repo-relative POSIX paths."""
    modules = list(integrity.ENFORCEMENT_MODULES)
    redteam_dir = root / "agentteams" / "redteam"
    if redteam_dir.is_dir():
        modules.extend(
            sorted(
                str(p.relative_to(root).as_posix())
                for p in redteam_dir.rglob("*.py")
                if p.name != "__init__.py"
            )
        )
    return sorted(set(modules))


def discover_verifiers(root: Path) -> list[_Discovered]:
    """Find every verifier-shaped top-level function in the F-1 scope.

    Args:
        root: Repository root.

    Returns:
        One entry per ``(module, symbol)``, sorted. Nested functions are excluded: a helper
        closed over a parent's state is tested through its parent.
    """
    found: list[_Discovered] = []
    for rel in _module_scope(root):
        path = root / rel
        if not path.exists():
            continue
        for node in _parse(path).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _looks_like_a_verifier(
                node.name
            ):
                found.append(_Discovered(module=rel, symbol=node.name))
    return sorted(found, key=lambda d: (d.module, d.symbol))


def _test_function_exists(root: Path, reference: str) -> bool:
    """Return True when ``reference`` (``path/to/test.py::test_name``) resolves to a ``def``."""
    if "::" not in reference:
        return False
    rel, _, func = reference.partition("::")
    path = root / rel.strip()
    if not path.exists():
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func.strip()
        for node in ast.walk(_parse(path))
    )


def check_verifier_sensitivity(root: Path) -> list[SelfAuditFinding]:
    """F-1: every verifier has a sensitivity test and a negative control, or says why not.

    Args:
        root: Repository root.

    Returns:
        One finding per unregistered verifier, stale ledger row, unresolvable test reference,
        or unsubstantiated ``not-a-verifier`` claim. Empty when the ledger describes the
        source.
    """
    findings: list[SelfAuditFinding] = []
    rows = read_csv_ledger(
        root / VERIFIERS_REL,
        required_columns=("module", "symbol", "kind", "sensitivity_test",
                          "negative_control_test", "reason"),
    )
    discovered = discover_verifiers(root)
    registered = {(row["module"], row["symbol"]): row for row in rows}

    for item in discovered:
        key = (item.module, item.symbol)
        if key not in registered:
            findings.append(SelfAuditFinding(
                check="F-1",
                subject=f"{item.module}::{item.symbol}",
                detail="verifier-shaped function with no row in the verifier ledger",
                remedy=(
                    f"add a row to {VERIFIERS_REL} with kind={KIND_VERIFIER} naming a "
                    f"sensitivity test and a negative control, or kind={KIND_NOT_VERIFIER} "
                    f"with a reason of at least {MIN_REASON_CHARS} characters"
                ),
            ))

    discovered_keys = {(d.module, d.symbol) for d in discovered}
    for (module, symbol), row in sorted(registered.items()):
        if (module, symbol) not in discovered_keys:
            findings.append(SelfAuditFinding(
                check="F-1",
                subject=f"{module}::{symbol}",
                detail="ledger row names a symbol that no longer exists in the source",
                remedy=f"remove the stale row from {VERIFIERS_REL}",
            ))
            continue
        kind = row["kind"]
        if kind == KIND_NOT_VERIFIER:
            if len(row["reason"]) < MIN_REASON_CHARS:
                findings.append(SelfAuditFinding(
                    check="F-1",
                    subject=f"{module}::{symbol}",
                    detail=(
                        f"claimed {KIND_NOT_VERIFIER} with a {len(row['reason'])}-character "
                        f"reason; the bar is {MIN_REASON_CHARS}"
                    ),
                    remedy=f"state in {VERIFIERS_REL} what this function does instead",
                ))
            continue
        if kind != KIND_VERIFIER:
            findings.append(SelfAuditFinding(
                check="F-1",
                subject=f"{module}::{symbol}",
                detail=f"unknown kind {kind!r}",
                remedy=f"use {KIND_VERIFIER!r} or {KIND_NOT_VERIFIER!r}",
            ))
            continue
        for column, label in (
            ("sensitivity_test", "sensitivity test (output changes when input changes)"),
            ("negative_control_test", "negative control (output does not change for "
                                      "irrelevant input)"),
        ):
            reference = row[column]
            if not reference:
                findings.append(SelfAuditFinding(
                    check="F-1",
                    subject=f"{module}::{symbol}",
                    detail=f"no {label} declared",
                    remedy=f"name one in {VERIFIERS_REL} as path/to/test.py::test_name",
                ))
            elif not _test_function_exists(root, reference):
                findings.append(SelfAuditFinding(
                    check="F-1",
                    subject=f"{module}::{symbol}",
                    detail=f"declared {label} {reference!r} does not resolve to a test function",
                    remedy="write the test, or correct the reference",
                ))
    return findings


# ---------------------------------------------------------------------------
# F-2 — call-path parity
# ---------------------------------------------------------------------------

def _called_names(node: ast.AST) -> set[str]:
    """Return every simple or attribute call name reachable from ``node``."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _statement_calls(statement: ast.stmt) -> set[str]:
    """Return call names made by ``statement`` itself, **not** by functions defined inside it.

    Parity is measured per statement within a suite, and a nested ``def`` is not a call site —
    it is a *definition* whose own body forms a separate suite, visited separately. Counting it
    as a call site inflates the site count, which defeats the anti-vacuity branch, and lets an
    enclosing definition stand in for a guard that is not actually on the path. Both were live
    in the first version of this check and were caught by its own vacuity test.
    """
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return set()
    names: set[str] = set()
    stack: list[ast.AST] = [statement]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            stack.append(child)
    return names


def _enclosing_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every function definition in the module, innermost included."""
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _suites(tree: ast.Module) -> list[list[ast.stmt]]:
    """Return every statement suite in the module — bodies, else-branches, finally-blocks.

    Parity is measured per *suite*, not per function. Both ``emit.emit_all`` call sites in
    ``cli/generate.py`` live inside the same 860-line function, on different branches: a
    function-level rule sees one host and passes, which is precisely how W20 stayed invisible
    while 4 of 31 agents went unmigrated and the run reported success.
    """
    suites: list[list[ast.stmt]] = []
    for node in ast.walk(tree):
        for attribute in ("body", "orelse", "finalbody"):
            suite = getattr(node, attribute, None)
            if isinstance(suite, list) and suite and isinstance(suite[0], ast.stmt):
                suites.append(suite)
    return suites


def check_callpath_parity(root: Path) -> list[SelfAuditFinding]:
    """F-2: a guard attached to one call site of a multi-site callee is not attached.

    Args:
        root: Repository root.

    Returns:
        One finding per unguarded call site, unresolvable ledger row, or **vacuous rule** — a
        parity rule over fewer than two call sites proves nothing, and is exactly how the
        original defect stayed invisible.
    """
    findings: list[SelfAuditFinding] = []
    rows = read_csv_ledger(
        root / CALLPATH_PARITY_REL,
        required_columns=("callee", "guard", "scope_module", "position", "note"),
    )
    for row in rows:
        callee, guard, rel = row["callee"], row["guard"], row["scope_module"]
        path = root / rel
        if not path.exists():
            findings.append(SelfAuditFinding(
                check="F-2",
                subject=f"{rel}",
                detail="parity ledger names a module that does not exist",
                remedy=f"correct or remove the row in {CALLPATH_PARITY_REL}",
            ))
            continue
        tree = _parse(path)
        functions = _enclosing_functions(tree)
        guard_defined = any(fn.name == guard for fn in functions) or guard in _called_names(tree)
        if not guard_defined:
            findings.append(SelfAuditFinding(
                check="F-2",
                subject=f"{rel}::{guard}",
                detail="parity ledger names a guard that is neither defined nor called here",
                remedy=(
                    f"correct the guard name in {CALLPATH_PARITY_REL} — a rule naming a "
                    f"nonexistent symbol matches nothing and passes for every input"
                ),
            ))
            continue
        # One call site per CALL, not per enclosing suite. `_statement_calls` walks into loops
        # and conditionals (it stops only at function boundaries), so a call inside a `for`
        # registers in the loop body AND in the function body that contains the loop. That
        # double-counts: it inflates the site count past the anti-vacuity threshold and emits
        # the same finding twice. A statement that merely CONTAINS a nested suite holding the
        # callee is skipped, because that inner suite is the one that will register it.
        position = (row.get("position") or "after").strip() or "after"
        call_sites: list[tuple[list[ast.stmt], int, int]] = []
        for suite in _suites(tree):
            for index, statement in enumerate(suite):
                if callee not in _statement_calls(statement):
                    continue
                own_suites = [
                    getattr(statement, attr, None)
                    for attr in ("body", "orelse", "finalbody")
                ]
                inner_statements = [
                    inner
                    for suite_ in own_suites
                    if isinstance(suite_, list) and suite_ and isinstance(suite_[0], ast.stmt)
                    for inner in suite_
                ]
                if any(callee in _statement_calls(inner) for inner in inner_statements):
                    continue
                call_sites.append((suite, index, statement.lineno))
        # The anti-vacuity threshold applies to POST-CONDITION parity only. "A guard must follow
        # every call" over a single call site proves nothing — that is the W20 shape, where the
        # whole risk is one path being missed. A PRECONDITION is different: "this call must be
        # preceded by preparation" is meaningful at one site, because the risk is the input not
        # being prepared at all, which is exactly the defect that motivated the `function`
        # position (the sweep read agent files raw and burned 812 calls).
        if position == "after" and len(call_sites) < 2:
            findings.append(SelfAuditFinding(
                check="F-2",
                subject=f"{rel}::{callee}",
                detail=(
                    f"parity rule covers {len(call_sites)} call site(s); a rule over fewer "
                    f"than two proves nothing and passes vacuously"
                ),
                remedy=(
                    f"if {callee} genuinely has one call site, remove the row from "
                    f"{CALLPATH_PARITY_REL}; if it has more, correct the callee name"
                ),
            ))
            continue
        # `position` declares WHERE the guard must be relative to the callee, because both
        # directions are real controls with different meanings:
        #
        #   after    — a post-condition. `_sweep_capability_key` must run AFTER `emit_all`,
        #              because it migrates what emit just wrote. A guard before it would
        #              migrate the previous state and prove nothing.
        #   function — an input precondition. `agent_system_prompt` must have run somewhere in
        #              the enclosing function before `run_payload` uses its result; it prepares
        #              the argument rather than reacting to the call, and it sits in an OUTER
        #              suite while the call sits inside a loop.
        #
        # Defaulting to `after` keeps the original row's semantics unchanged.
        for suite, index, lineno in call_sites:
            if position == "function":
                host = next(
                    (fn for fn in functions
                     if fn.lineno <= lineno <= (fn.end_lineno or fn.lineno)),
                    None,
                )
                guarded = host is not None and guard in _called_names(host)
            else:
                guarded = any(
                    guard in _statement_calls(statement) for statement in suite[index:]
                )
            if not guarded:
                findings.append(SelfAuditFinding(
                    check="F-2",
                    subject=f"{rel}:{lineno}",
                    detail=(
                        f"calls {callee} but no call to the guard {guard} appears "
                        + ("anywhere in the enclosing function"
                           if position == "function" else "after it in the same branch")
                    ),
                    remedy=(
                        f"add the {guard} call to this path — a guard on one of two paths is "
                        f"the fix that migrated 27 of 31 agents and reported success"
                    ),
                ))
    return findings


# ---------------------------------------------------------------------------
# F-3 — canonical resolution
# ---------------------------------------------------------------------------

def _imports_fleet(tree: ast.Module) -> bool:
    """True when the module imports ``agentteams.fleet`` in any spelling."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "agentteams.fleet" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "agentteams.fleet":
                return True
            if node.module == "agentteams" and any(a.name == "fleet" for a in node.names):
                return True
    return False


def _f3_candidate_files(root: Path) -> list[Path]:
    """Return the Python files F-3 sweeps."""
    candidates: list[Path] = []
    for directory in _F3_ROOTS:
        base = root / directory
        if base.is_dir():
            candidates.extend(sorted(base.rglob("*.py")))
    return candidates


def _f3_in_scope(root: Path, path: Path, tree: ast.Module) -> bool:
    """True when ``path`` is fleet-consuming code, and is not a canonical definition site."""
    rel = path.relative_to(root).as_posix()
    if rel.startswith(_F3_DEFINITION_SITES):
        return False
    if rel.startswith(_F3_ALWAYS_IN_SCOPE):
        return True
    return _imports_fleet(tree)


def _enclosing_name(tree: ast.Module, lineno: int) -> str:
    """Return the innermost function enclosing ``lineno``, or ``"<module>"``.

    Findings anchor on the enclosing function rather than on a line number, because a line
    number changes with every edit above it — an exemption ledger keyed on line numbers goes
    stale the first time anyone touches the file, and a stale ledger reads as a clean one.
    """
    best = "<module>"
    best_span = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= lineno <= end:
            span = end - node.lineno
            if best_span is None or span < best_span:
                best, best_span = node.name, span
    return best


def _flag(rel: str, function: str, line: int, detail: str, key: str) -> SelfAuditFinding:
    return SelfAuditFinding(
        check="F-3",
        subject=f"{rel}::{function}::{key}",
        detail=f"line {line}: {detail}",
        remedy=(
            f"call {CANONICAL_RESOLVERS[key]} instead, or record the row "
            f"{rel},{function},{key} in {RESOLUTION_EXEMPTIONS_REL} with a reason"
        ),
    )


def _collect_resolution_flags(root: Path) -> list[SelfAuditFinding]:
    """Return every hand-rolled resolution construct in the F-3 scope, before exemptions."""
    flags: list[SelfAuditFinding] = []
    for path in _f3_candidate_files(root):
        tree = _parse(path)
        if not _f3_in_scope(root, path, tree):
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                where = _enclosing_name(tree, node.lineno)
                if attr == "iterdir":
                    flags.append(_flag(
                        rel, where, node.lineno,
                        "enumerates directory children by hand where fleet work needs the "
                        "canonical workspace enumerator",
                        "workspace-enumeration",
                    ))
                elif attr == "glob" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and first.value in _DIRECTORY_GLOB_PATTERNS:
                        flags.append(_flag(
                            rel, where, node.lineno,
                            f"globs {first.value!r} over a parent directory — the construct "
                            f"that missed 34 workspaces and 719 exposed agents",
                            "workspace-enumeration",
                        ))
                elif attr in {"exists", "is_dir"} and _is_dot_git_expression(node.func.value):
                    flags.append(_flag(
                        rel, where, node.lineno,
                        "tests for a `.git` directory to decide whether a path is a "
                        "repository, which misclassifies git worktrees (whose .git is a file)",
                        "vcs-status",
                    ))
            elif isinstance(node, ast.Constant) and node.value in _DESCRIPTOR_LITERALS:
                flags.append(_flag(
                    rel, _enclosing_name(tree, node.lineno), node.lineno,
                    f"hard-codes the descriptor location {node.value!r} instead of resolving "
                    f"it; the canonical resolver prefers the rich brief over the thin stub",
                    "descriptor-lookup",
                ))
    return flags


def check_canonical_resolution(root: Path) -> list[SelfAuditFinding]:
    """F-3: target, descriptor and VCS resolution go through the module's own APIs.

    Scope is fleet-consuming non-test code — every module under ``agentteams/`` or
    ``scripts/`` that imports ``agentteams.fleet``, plus ``agentteams/redteam/``.
    ``agentteams/fleet.py`` is exempt **by path**, because it is where the canonical resolvers
    are defined; the exemption keys on provenance rather than on shape, per CH-30.

    Args:
        root: Repository root.

    Returns:
        One finding per unexempted hand-rolled construct, plus one per **stale exemption** —
        a row that no longer matches anything, which is how an exemption ledger stops
        describing the system.
    """
    exemptions = {
        (row["file"], row["function"], row["construct"]): row["reason"]
        for row in read_csv_ledger(
            root / RESOLUTION_EXEMPTIONS_REL,
            required_columns=("file", "function", "construct", "reason"),
        )
    }
    findings: list[SelfAuditFinding] = []
    matched: set[tuple[str, str, str]] = set()
    for flag in _collect_resolution_flags(root):
        rel, function, construct = flag.subject.split("::", 2)
        key = (rel, function, construct)
        reason = exemptions.get(key)
        if reason is None:
            findings.append(flag)
        elif len(reason) < MIN_REASON_CHARS:
            findings.append(SelfAuditFinding(
                check="F-3",
                subject=flag.subject,
                detail=(
                    f"exempted with a {len(reason)}-character reason; the bar is "
                    f"{MIN_REASON_CHARS}"
                ),
                remedy=f"state why the canonical API does not apply here, in {RESOLUTION_EXEMPTIONS_REL}",
            ))
            matched.add(key)
        else:
            matched.add(key)
    for key in sorted(set(exemptions) - matched):
        findings.append(SelfAuditFinding(
            check="F-3",
            subject="::".join(key),
            detail="exemption matches no construct in the source — stale row",
            remedy=f"remove it from {RESOLUTION_EXEMPTIONS_REL}",
        ))
    return findings


def _is_dot_git_expression(node: ast.AST) -> bool:
    """True when ``node`` is a path expression ending in the literal ``.git``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        return isinstance(right, ast.Constant) and right.value == ".git"
    if isinstance(node, ast.Call) and node.args:
        last = node.args[-1]
        return isinstance(last, ast.Constant) and last.value == ".git"
    return False


__all__ = [
    "CANONICAL_RESOLVERS",
    "KIND_NOT_VERIFIER",
    "KIND_VERIFIER",
    "check_callpath_parity",
    "check_canonical_resolution",
    "check_verifier_sensitivity",
    "discover_verifiers",
]
