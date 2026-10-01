"""pin-signing-cli-modules: the operator signing path stays inside pinned code.

* **T3 (delegator lock).** ``commands.py`` / ``grant_commands.py`` hold no key handling or
  signing call, and ``_run_sign_decision`` / ``_run_sign_grant`` are a single
  ``return operator_signing.<fn>(...)``. Key or payload logic re-growing in an unpinned runner is
  a CI failure, not a silent regression.
* **T4 (generic invariant — a REGRESSION GUARD, not a boundary).** Every ``agentteams/**/*.py``
  module that references a signing or key-reading primitive (a Call, an attribute access, a
  ``getattr`` string literal, a bare name, an import of cryptography's ed25519 module) or the
  keyfile env-var string must be in ``integrity.ENFORCEMENT_MODULES``. A NEW unpinned signing
  site then fails CI wherever it appears. It is a static scan of honest code: obfuscated access
  (string concatenation, ``importlib``) defeats it, and it does not pretend otherwise.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentteams import integrity

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "agentteams"

_KEYFILE_ENV = "AGENTTEAMS_DECISION_ED25519_KEYFILE"
#: Primitives that sign, append a signed row, or load private key material.
_SIGNING_PRIMITIVES = frozenset({
    "ed25519_sign", "sign_ed25519_grant", "append_signed_decision_row", "load_pem_private_key",
    "_read_operator_private_key",
})
#: The two unpinned runners and the single pinned entry point each may call.
_DELEGATORS = {
    "agentteams/cli/commands.py": ("_run_sign_decision", "sign_decision"),
    "agentteams/cli/grant_commands.py": ("_run_sign_grant", "sign_grant"),
}


def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text(encoding="utf-8"), filename=rel)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of docstring Constant nodes (module/class/function): prose, not code."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _signing_references(tree: ast.AST) -> list[str]:
    """Every code reference to a signing primitive or the keyfile env var in ``tree``."""
    docstrings = _docstring_nodes(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SIGNING_PRIMITIVES:
            hits.append(f"attr .{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in _SIGNING_PRIMITIVES:
            hits.append(f"name {node.id} (line {node.lineno})")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if node.value in _SIGNING_PRIMITIVES:
                hits.append(f"string {node.value!r} (line {node.lineno})")
            elif _KEYFILE_ENV in node.value:
                hits.append(f"keyfile env string (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = [a.name for a in node.names]
            if "ed25519" in mod or (mod.startswith("cryptography") and "ed25519" in names):
                hits.append(f"import of cryptography ed25519 (line {node.lineno})")
            hits.extend(f"import {n} (line {node.lineno})" for n in names if n in _SIGNING_PRIMITIVES)
        elif isinstance(node, ast.Import):
            hits.extend(f"import {a.name} (line {node.lineno})" for a in node.names
                        if "ed25519" in a.name)
    return hits


def _exempt_strings(rel: str) -> bool:
    """Parser help text names the env var for the operator; it is prose, not a key read."""
    return rel == "agentteams/cli/parser.py"


def test_t4_every_signing_site_is_integrity_pinned() -> None:
    """REGRESSION GUARD: a module referencing a signing primitive must be pinned."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in integrity.ENFORCEMENT_MODULES:
            continue
        hits = _signing_references(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
        if _exempt_strings(rel):
            hits = [h for h in hits if not h.startswith("keyfile env string")]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "unpinned module(s) reference an operator signing / key-reading primitive — move the code "
        f"into agentteams/cli/operator_signing.py or pin the module: {offenders}"
    )


def test_t4_guard_is_not_vacuous() -> None:
    """The scanner must actually see the primitives in the pinned module it guards."""
    hits = _signing_references(_tree("agentteams/cli/operator_signing.py"))
    assert any("keyfile env string" in h for h in hits), hits
    assert any(".ed25519_sign" in h for h in hits), hits
    assert any(".append_signed_decision_row" in h for h in hits), hits
    assert any("_read_operator_private_key" in h for h in hits), hits
    assert _signing_references(ast.parse("getattr(sl, 'ed25519_sign')"))
    assert _signing_references(ast.parse("x = 'AGENTTEAMS_DECISION_ED25519_KEYFILE'"))
    assert _signing_references(ast.parse(
        "from cryptography.hazmat.primitives.asymmetric import ed25519"))
    assert _signing_references(ast.parse(
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey"))


def test_t3_delegators_hold_no_signing_logic() -> None:
    """The unpinned runners reference no signing primitive and print no pre-sign display."""
    for rel in _DELEGATORS:
        hits = _signing_references(_tree(rel))
        assert not hits, f"{rel} re-grew signing/key logic outside the pinned module: {hits}"
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "About to sign" not in text, f"{rel} prints the pre-sign display itself"


def test_t3_runner_bodies_are_single_return_delegations() -> None:
    for rel, (runner, target) in _DELEGATORS.items():
        funcs = [n for n in _tree(rel).body if isinstance(n, ast.FunctionDef) and n.name == runner]
        assert len(funcs) == 1, f"{rel}: {runner} not found exactly once"
        body = funcs[0].body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # docstring
        assert len(body) == 1 and isinstance(body[0], ast.Return), f"{rel}: {runner} is not one return"
        call = body[0].value
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        assert call.func.attr == target and isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "operator_signing", f"{rel}: {runner} delegates elsewhere"
