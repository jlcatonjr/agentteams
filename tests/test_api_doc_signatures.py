"""test_api_doc_signatures.py — documented signatures must match the code.

`docs_src/api-reference/*.md` documents each module's public functions with a
`### \\`name(params)\\`` heading. Nothing kept those headings equal to the real
parameter lists, and they drifted seven ways — two of them introduced in the same
session that added drift guards for templates, ledger rows and bridge reports.

Two of the seven were worse than omissions: `compute_structural_diff` and
`build_tool_catalog` documented a keyword parameter under a name the code had
renamed, so the documented call raises `TypeError`.

**Scope is narrow by construction, not by allowlist.** Only headings naming a
*module-level function* of the module that doc covers are compared. That excludes:

* methods — `graph.md` documenting `to_dot()` rather than `to_dot(self)` is correct
  style, not drift;
* same-name functions in other modules — `parallel_plan.to_json(schedule)` must not
  be compared against `graph.ArchitectureGraph.to_json(self)`.

An allowlist would have swallowed real drift alongside that noise.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs_src/api-reference"
PKG_DIR = REPO_ROOT / "agentteams"

#: Docs that are not module references.
_NON_MODULE_DOCS = frozenset({"index", "feature-inventory"})

_HEADING_RE = re.compile(r"^#{2,4}\s+`([a-zA-Z_][a-zA-Z0-9_.]*)\(([^`]*)\)`", re.M)


def _doc_targets(stem: str) -> list[Path]:
    """Resolve a doc stem to the module file(s) it documents.

    Doc names use hyphens where modules use underscores. A stem may name a single
    module or a sub-package (``frameworks.md`` → ``agentteams/frameworks/``).
    """
    name = stem.replace("-", "_")
    module = PKG_DIR / f"{name}.py"
    if module.exists():
        return [module]
    package = PKG_DIR / name
    if package.is_dir():
        return sorted(package.glob("*.py"))
    return []


def _is_package_doc(stem: str) -> bool:
    """True when :func:`_doc_targets` resolves *stem* to a sub-package, not a single module."""
    name = stem.replace("-", "_")
    return not (PKG_DIR / f"{name}.py").exists() and (PKG_DIR / name).is_dir()


def _functions_by_module(paths: list[Path]) -> dict[str, dict[str, list[str]]]:
    """{module stem: {function name: parameter names}} for module-level defs only.

    Direct children of the module body — never methods, which live inside a
    ``ClassDef``. That is what keeps ``self`` out of the comparison.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        funcs: dict[str, list[str]] = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            params = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
            if args.vararg:
                params.append(f"*{args.vararg.arg}")
            elif args.kwonlyargs:
                params.append("*")
            params += [a.arg for a in args.kwonlyargs]
            if args.kwarg:
                params.append(f"**{args.kwarg.arg}")
            funcs[node.name] = params
        out[path.stem] = funcs
    return out


def _resolve(name: str, by_module: dict[str, dict[str, list[str]]],
             is_package: bool) -> tuple[str, list[str] | None] | None:
    """Resolve a heading's function name to ``(label, real params)``; ``None`` when not comparable.

    API-DOC-SIGNATURE-LEAF-COLLISION: a package can define one function name in several
    modules (vulnscan: ``build`` in three). When the page covers a package and the heading
    is qualified — ``grant.build(...)`` or ``agentteams.vulnscan.grant.build(...)`` — and
    its qualifier names a module of that package, the heading is compared against that
    module's function only, and a module without it is drift (real params ``None``).
    Every other heading keeps the leaf-name behaviour: across a package, a later module
    (in path order) shadows an earlier same-named function.

    Args:
        name: The dotted or bare name from the heading.
        by_module: :func:`_functions_by_module` for the page's target files.
        is_package: Whether the page documents a package rather than one module.

    Returns:
        ``(label, params)`` where *label* names the function in failure messages, or
        ``None`` when the heading names nothing comparable.
    """
    parts = name.split(".")
    if is_package and len(parts) >= 2 and parts[-2] in by_module:
        module, func = parts[-2], parts[-1]
        return f"{module}.{func}", by_module[module].get(func)
    leaf = parts[-1]
    merged: dict[str, list[str]] = {}
    for funcs in by_module.values():
        merged.update(funcs)
    if leaf not in merged:
        return None
    return leaf, merged[leaf]


def _split_params(sig: str) -> list[str]:
    """Split a documented parameter list on top-level commas.

    Depth-aware so `dict[str, Any]` annotations and `(a, b)` defaults do not split.
    """
    params: list[str] = []
    depth = 0
    current = ""
    for ch in sig:
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
        if ch == "," and depth == 0:
            params.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        params.append(current)
    return [p.split("=")[0].split(":")[0].strip() for p in params if p.strip()]


def _documented() -> list[tuple[str, str, list[str], list[str] | None]]:
    """(doc name, func, documented params, real params) for comparable headings.

    Real params are ``None`` for a qualified heading whose module lacks the function.
    """
    rows = []
    for doc in sorted(DOCS_DIR.glob("*.md")):
        if doc.stem in _NON_MODULE_DOCS:
            continue
        by_module = _functions_by_module(_doc_targets(doc.stem))
        if not any(by_module.values()):
            continue
        is_package = _is_package_doc(doc.stem)
        for name, sig in _HEADING_RE.findall(doc.read_text(encoding="utf-8")):
            resolved = _resolve(name, by_module, is_package)
            if resolved is None:
                continue
            label, real = resolved
            rows.append((doc.name, label, _split_params(sig), real))
    return rows


def test_documented_signatures_match_the_code() -> None:
    """A documented parameter list must equal the real one, in order."""
    rows = _documented()
    assert rows, (
        "no comparable signature headings were found — the parser or the doc format "
        "changed, and this test would pass vacuously"
    )
    drift = []
    for doc, func, documented, real in rows:
        if real is None:
            module = func.split(".")[0]
            drift.append(f"{doc}: {func}() — {module}.py defines no module-level {func.split('.')[-1]}")
            continue
        if documented == real:
            continue
        missing = [p for p in real if p not in documented]
        extra = [p for p in documented if p not in real]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"documented but absent from the code {extra}")
        if not detail:
            detail.append(f"order differs: doc {documented} vs code {real}")
        drift.append(f"{doc}: {func}() — {'; '.join(detail)}")
    assert not drift, "API doc signatures have drifted from the code:\n  " + "\n  ".join(drift)


def test_qualified_heading_resolves_to_its_module_in_a_package() -> None:
    """API-DOC-SIGNATURE-LEAF-COLLISION: ``grant.build`` is grant.py's build, not the last-sorted one."""
    assert _is_package_doc("vulnscan") and not _is_package_doc("integrity")
    by_module = _functions_by_module(_doc_targets("vulnscan"))
    grant_build = _functions_by_module([PKG_DIR / "vulnscan" / "grant.py"])["grant"]["build"]
    review_build = _functions_by_module([PKG_DIR / "vulnscan" / "review.py"])["review"]["build"]
    assert grant_build != review_build, "fixture premise: the colliding builds differ"
    for name in ("grant.build", "agentteams.vulnscan.grant.build"):
        assert _resolve(name, by_module, True) == ("grant.build", grant_build)
    # Unqualified (and qualifier-not-a-module) headings keep the leaf behaviour: last module wins.
    assert _resolve("build", by_module, True) == ("build", review_build)
    assert _resolve("Grant.build", by_module, True) == ("build", review_build)
    # A qualified heading whose module lacks the function is drift, not skipped.
    assert _resolve("grant.no_such_function", by_module, True) == ("grant.no_such_function", None)
    # Outside a package, a qualifier never narrows (single-module pages are unchanged).
    single = _functions_by_module(_doc_targets("integrity"))
    assert _resolve("integrity.verify", single, False) == ("verify", single["integrity"]["verify"])
    assert _resolve("nothing_here", single, False) is None


def test_comparison_covers_a_meaningful_share_of_the_docs() -> None:
    """Guard the guard: a parser regression would silently shrink coverage to nothing.

    The count is a floor, not a target — raise it if the doc set grows, never lower it
    to make a failure go away.
    """
    rows = _documented()
    docs_covered = {doc for doc, _f, _d, _r in rows}
    assert len(rows) >= 90, f"only {len(rows)} signatures comparable; parser regression?"
    assert len(docs_covered) >= 25, f"only {len(docs_covered)} docs covered; parser regression?"


# ---------------------------------------------------------------------------
# The feature inventory guides the API-doc review, so its own arithmetic must hold
#
# The summary table declares a per-category count and a total. The total read `~126`
# while its addends summed to 125 — the same hand-tally defect the inventory's own
# note warns readers about, committed in the summary itself. The `~` hedged a number
# that is exactly computable from the rows above it.
# ---------------------------------------------------------------------------

SUMMARY_TABLE = REPO_ROOT / "docs_src/assets/feature-summary-table.md"
INVENTORY = DOCS_DIR / "feature-inventory.md"

_CATEGORY_ROW_RE = re.compile(r"\|\s*\*\*[^|]+\*\*\s*\|\s*(\d+)\s*\|")
_TOTAL_RE = re.compile(r"\*\*Total:\*\*\s*~?(\d+)\s+documented features across\s+(\d+)")


def test_feature_summary_total_equals_its_addends() -> None:
    """A stated total must equal the column it summarises."""
    text = SUMMARY_TABLE.read_text(encoding="utf-8")
    counts = [int(n) for n in _CATEGORY_ROW_RE.findall(text)]
    assert counts, "no category rows parsed — the summary table format changed"
    match = _TOTAL_RE.search(text)
    assert match, "summary table states no total"
    total, categories = int(match.group(1)), int(match.group(2))
    assert total == sum(counts), (
        f"stated total {total} != sum of addends {sum(counts)} ({counts}). "
        "Correct the total, not this test."
    )
    assert categories == len(counts), (
        f"stated {categories} capability areas but the table has {len(counts)} rows"
    )


_VERSION_BASELINE_RE = re.compile(r"\*\*Version baseline:\*\*\s*([0-9][^\s·]*)")


def test_inventory_version_baseline_matches_the_installed_version() -> None:
    """The drift that actually caused harm was the baseline, not the per-category counts.

    The inventory's own note explains why the counts are hand-maintained and cannot be derived
    without a machine-readable feature set — that reasoning stands. But the *version baseline*
    beside them was never a judgement call: it is `agentteams.__version__`, and it sat at
    `0.1.0 (2026-04-15)` while the module was at `1.0.0rc6` — four months and a major version
    stale. A reader checking whether a capability had shipped was reading a document that
    described a different release.

    Pinned here rather than hand-reconciled, because "reconcile it by hand next time" is what
    produced the four-month gap.
    """
    import agentteams

    if agentteams.__version__ == "0.0.0+local":
        # agentteams/__init__.py's fallback when the package isn't installed: there is no installed version to
        # compare against. CI and the dev venv install it, so the check still runs there.
        pytest.skip("agentteams is not installed in this interpreter (version 0.0.0+local)")
    text = INVENTORY.read_text(encoding="utf-8")
    match = _VERSION_BASELINE_RE.search(text)
    assert match, "feature-inventory.md states no **Version baseline:** — format changed"
    assert match.group(1) == agentteams.__version__, (
        f"feature-inventory.md claims version baseline {match.group(1)}, but the module is at "
        f"{agentteams.__version__}. Update the document, not this test."
    )


def test_inventory_governance_agent_list_matches_the_code() -> None:
    """The one category whose members are enumerable from code must agree with it."""
    from agentteams import analyze

    text = SUMMARY_TABLE.read_text(encoding="utf-8")
    row = re.search(r"\*\*Governance Agents\*\*\s*\|\s*(\d+)\s*\|\s*([^|]+)\|", text)
    assert row, "no Governance Agents row in the summary table"
    listed = {n.strip() for n in row.group(2).split(",")}
    real = set(analyze.GOVERNANCE_AGENTS)
    assert listed == real, (
        f"governance list drifted — only in doc: {sorted(listed - real)}; "
        f"only in code: {sorted(real - listed)}"
    )
    assert int(row.group(1)) == len(real), (
        f"row claims {row.group(1)} governance agents; code declares {len(real)}"
    )
