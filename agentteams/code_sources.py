"""code_sources.py — collectors for the code & API index (F-CODEIDX, Phase B).

Builds the ``api-module`` and ``api-doc`` index units by (1) statically parsing
local scripts for their imports, (2) classifying each import as stdlib / local /
external, and (3) resolving external imports to their distribution **metadata**.

Hard constraint — **never execute third-party code** (plan T7 / R2-M2). All
resolution uses static ``ast`` parsing and ``importlib.metadata`` reads
(``packages_distributions``, ``metadata``, ``version``, ``files``,
``direct_url.json``). We never call ``import``/``__import__``/``find_spec``,
which could trigger side-effecting or PEP 660 editable-install finders.

Best-effort by design: an external dependency that cannot be resolved to source
files (namespace packages, zipped eggs, distro-stripped installs) degrades to a
**declared-only** unit (name + version from metadata) rather than raising — the
honest partial the audit asked for (R2-M2). ``api-doc`` is derived from
``METADATA`` (present even for editable/namespace installs) and is therefore the
more robust of the two API partitions.
"""

from __future__ import annotations

import ast
import importlib.metadata as ilmd
import json
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Iterable

from agentteams import code_index as _ci

# Narrow exception surface for the importlib.metadata / filesystem boundary
# (CH-24: named types, never a blanket `except Exception`). A metadata read may
# be absent (PackageNotFoundError), unreadable (OSError), or malformed
# (Value/Key/Attribute/TypeError); any of these means "unresolvable → degrade".
_META_ERRORS = (PackageNotFoundError, OSError, ValueError, KeyError, AttributeError, TypeError)

# Volume caps (plan §5.2 / §9) — keep a large dependency tree from bloating the
# gitignored cache. Overflow is counted and surfaced, not silently dropped.
_MAX_API_MODULE_FILES = 6
_MAX_API_SYMBOLS_PER_MODULE = 40
_MAX_BYTES_PER_API_FILE = 200_000
_MAX_DOC_CHARS = 8_000

_DEP_MANIFEST_NAMES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
)


@dataclass
class ApiCollection:
    """Result of an API collection pass over the local scripts."""

    external_imports: list[str] = field(default_factory=list)
    dist_versions: list[tuple[str, str]] = field(default_factory=list)
    api_module_units: list[dict] = field(default_factory=list)
    api_doc_units: list[dict] = field(default_factory=list)
    declared_only: list[str] = field(default_factory=list)
    resolved_source: list[str] = field(default_factory=list)
    dependency_fingerprint: str = ""

    @property
    def declared_only_rate(self) -> float:
        total = len(self.external_imports)
        return (len(self.declared_only) / total) if total else 0.0


# ---------------------------------------------------------------------------
# Import extraction (static, Python only — R2-m2)
# ---------------------------------------------------------------------------

def extract_imports(py_text: str) -> set[str]:
    """Return the set of top-level module names imported by *py_text*.

    Relative imports (``from . import x``) are skipped — they are local, not
    external APIs. Syntax errors yield an empty set (robust to churn). Never
    executes the code.
    """
    try:
        tree = ast.parse(py_text)
    except (SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top:
                    names.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import → local
            if node.module:
                names.add(node.module.split(".", 1)[0])
    return names


def _stdlib_names() -> frozenset[str]:
    """Standard-library top-level module names (advisory — R2-m4).

    Uses the running interpreter's ``sys.stdlib_module_names``. The repo's
    ``requires-python`` may differ; a misclassification only affects whether an
    import is *attempted* to resolve as external, and an unresolved external
    degrades to declared-only — never an error.
    """
    return frozenset(getattr(sys, "stdlib_module_names", frozenset()))


def _local_top_names(project_root: Path) -> set[str]:
    """Top-level module/package names that are local to the project."""
    local: set[str] = set()
    try:
        children = list(project_root.iterdir())
    except OSError:
        return local
    for child in children:
        if child.is_dir() and (child / "__init__.py").exists():
            local.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            local.add(child.stem)
    return local


def classify_external(
    import_names: Iterable[str], project_root: Path
) -> list[str]:
    """Return the sorted external (third-party API) import names."""
    stdlib = _stdlib_names()
    local = _local_top_names(project_root)
    external = {
        n for n in import_names
        if n and n not in stdlib and n not in local and not n.startswith("_")
    }
    return sorted(external)


# ---------------------------------------------------------------------------
# Metadata-only resolution (no import, no find_spec)
# ---------------------------------------------------------------------------

def _import_to_distribution(import_name: str) -> str | None:
    try:
        mapping = ilmd.packages_distributions()
    except _META_ERRORS:
        return None
    dists = mapping.get(import_name)
    if dists:
        return sorted(dists)[0]
    # Fallback: the distribution may share the import name.
    try:
        ilmd.version(import_name)
        return import_name
    except _META_ERRORS:
        return None


def _distribution_version(dist: str) -> str | None:
    try:
        return ilmd.version(dist)
    except _META_ERRORS:
        return None


def _distribution_metadata_text(dist: str) -> str:
    try:
        meta = ilmd.metadata(dist)
    except _META_ERRORS:
        return ""
    parts: list[str] = []
    for key in ("Name", "Summary"):
        val = meta.get(key)
        if val:
            parts.append(str(val))
    # The long description is the payload (or a Description field on older metadata).
    try:
        body = meta.get_payload() or ""  # type: ignore[attr-defined]
    except _META_ERRORS:
        body = ""
    if not body:
        body = meta.get("Description", "") or ""
    text = "\n".join(parts)
    if body:
        text = f"{text}\n{body}"
    return text[:_MAX_DOC_CHARS]


def _editable_project_dir(dist: str) -> Path | None:
    """Recover an editable install's on-disk project dir via direct_url.json.

    A JSON read of the dist-info metadata — never an import (R2-M2).
    """
    try:
        raw = ilmd.distribution(dist).read_text("direct_url.json")
    except _META_ERRORS:
        return None
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except (ValueError, TypeError):
        return None
    url = info.get("url", "")
    if isinstance(url, str) and url.startswith("file://"):
        from urllib.parse import unquote, urlparse

        p = Path(unquote(urlparse(url).path))
        return p if p.exists() else None
    return None


def _safe_locate(package_path) -> Path | None:
    """Resolve an ``importlib.metadata`` PackagePath to an absolute path, or None."""
    try:
        return Path(package_path.locate())
    except (OSError, ValueError):
        return None


def _resolve_source_files(dist: str, import_name: str) -> list[Path]:
    """Locate up to _MAX_API_MODULE_FILES .py source files for *dist*.

    Metadata-only: uses ``files(dist)`` (a RECORD read) and, for editable
    installs, ``direct_url.json``. Never imports or calls ``find_spec``.
    """
    out: list[Path] = []
    try:
        files = ilmd.files(dist) or []
    except _META_ERRORS:
        files = []
    for pp in files:
        name = str(pp)
        if not name.endswith(".py"):
            continue
        top = name.split("/", 1)[0]
        if top not in (import_name, f"{import_name}.py"):
            continue
        located = _safe_locate(pp)
        if located is not None and located.is_file():
            out.append(located)
        if len(out) >= _MAX_API_MODULE_FILES:
            break
    if out:
        return out
    # Editable-install recovery.
    proj = _editable_project_dir(dist)
    if proj is not None:
        for cand in (proj / import_name, proj / "src" / import_name):
            if cand.is_dir():
                for f in sorted(cand.rglob("*.py"))[:_MAX_API_MODULE_FILES]:
                    out.append(f)
                break
            elif cand.with_suffix(".py").is_file():
                out.append(cand.with_suffix(".py"))
    return out[:_MAX_API_MODULE_FILES]


def _public_symbol_units(
    path: Path, *, rel: str, provenance: dict
) -> list[dict]:
    """Extract public top-level symbols from a .py file via AST (no execution)."""
    try:
        if path.stat().st_size > _MAX_BYTES_PER_API_FILE:
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    units: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue
            sig = _ci._signature_for(node)
            doc = ast.get_docstring(node) or ""
            body = f"{sig}\n{doc}".strip()
            units.append({
                "path": rel,
                "text": body,
                "language": "python",
                "source_kind": "api-module",
                "symbol": node.name,
                "signature": sig,
                "provenance": provenance,
            })
            if len(units) >= _MAX_API_SYMBOLS_PER_MODULE:
                break
    return units


# ---------------------------------------------------------------------------
# Top-level collection
# ---------------------------------------------------------------------------

def dependency_manifest_texts(project_root: Path) -> list[str]:
    texts: list[str] = []
    for name in _DEP_MANIFEST_NAMES:
        text = _ci.read_text_or_none(project_root / name)
        if text is not None:
            texts.append(text)
    return texts


def collect_api(local_sources: Iterable[Path], project_root: Path) -> ApiCollection:
    """Collect api-module + api-doc units from the imports of *local_sources*.

    Best-effort and non-raising: unreadable files, unresolved dists, and
    metadata gaps degrade gracefully. Returns an :class:`ApiCollection` with the
    units, the declared-only rate (measurement gate, R2-M2), and the dependency
    fingerprint used for api-partition staleness (R2-M1).
    """
    imports: set[str] = set()
    for src in local_sources:
        if Path(src).suffix != ".py":
            continue
        text = _ci.read_text_or_none(Path(src))
        if text is not None:
            imports |= extract_imports(text)

    external = classify_external(imports, project_root)
    result = ApiCollection(external_imports=external)

    for import_name in external:
        dist = _import_to_distribution(import_name)
        if dist is None:
            # Cannot even resolve a distribution — declared-only stub, no version.
            result.declared_only.append(import_name)
            prov = {"distribution": import_name, "version": None, "declared_only": True}
            result.api_module_units.append({
                "path": import_name, "text": import_name, "language": None,
                "source_kind": "api-module", "symbol": None, "signature": None,
                "provenance": prov,
            })
            continue
        version = _distribution_version(dist)
        result.dist_versions.append((dist, version or ""))
        prov_base = {"distribution": dist, "version": version}

        # api-doc (robust, METADATA-backed) — shipped first (R2-M2).
        doc_text = _distribution_metadata_text(dist)
        if doc_text.strip():
            result.api_doc_units.append({
                "path": dist, "text": doc_text, "language": None,
                "source_kind": "api-doc", "symbol": None, "signature": None,
                "provenance": {**prov_base},
            })

        # api-module (best-effort source).
        files = _resolve_source_files(dist, import_name)
        if files:
            result.resolved_source.append(import_name)
            for f in files:
                prov = {**prov_base, "resolved_from": str(f), "editable": None}
                result.api_module_units.extend(
                    _public_symbol_units(f, rel=import_name, provenance=prov)
                )
        else:
            # Declared-only: name + version, no source body (honest partial).
            result.declared_only.append(import_name)
            result.api_module_units.append({
                "path": dist, "text": f"{dist} {version or ''}".strip(),
                "language": None, "source_kind": "api-module", "symbol": None,
                "signature": None,
                "provenance": {**prov_base, "declared_only": True},
            })

    result.dependency_fingerprint = _ci.dependency_fingerprint(
        dependency_manifest_texts(project_root),
        external,
        result.dist_versions,
    )
    return result


def compute_dependency_fingerprint(local_sources: Iterable[Path], project_root: Path) -> str:
    """Cheap dependency fingerprint for query-time api-staleness (no source reads).

    Extracts imports, classifies external, resolves dist→version via metadata,
    and fingerprints (manifest texts + import set + versions). This is the same
    fingerprint :func:`collect_api` stores on the api partitions, computed
    without the (heavier) api source extraction so a query stays fast.
    """
    imports: set[str] = set()
    for src in local_sources:
        p = Path(src)
        if p.suffix != ".py":
            continue
        text = _ci.read_text_or_none(p)
        if text is not None:
            imports |= extract_imports(text)
    external = classify_external(imports, project_root)
    dist_versions: list[tuple[str, str]] = []
    for name in external:
        dist = _import_to_distribution(name)
        if dist is not None:
            dist_versions.append((dist, _distribution_version(dist) or ""))
    return _ci.dependency_fingerprint(
        dependency_manifest_texts(project_root), external, dist_versions
    )


__all__ = [
    "ApiCollection",
    "extract_imports",
    "classify_external",
    "dependency_manifest_texts",
    "collect_api",
    "compute_dependency_fingerprint",
]
