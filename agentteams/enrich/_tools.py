"""_tools.py — Dynamic tool detection and documentation URL lookup."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentteams import tool_metadata_catalog


#: Maps common import aliases → canonical PyPI package names.
_IMPORT_TO_PACKAGE: dict[str, str] = {
    "np": "numpy", "numpy": "numpy",
    "pd": "pandas", "pandas": "pandas",
    "plt": "matplotlib", "mpl": "matplotlib", "matplotlib": "matplotlib",
    "scipy": "scipy",
    "sm": "statsmodels", "smf": "statsmodels", "statsmodels": "statsmodels",
    "sklearn": "scikit-learn", "skl": "scikit-learn",
    "tf": "tensorflow", "tensorflow": "tensorflow",
    "torch": "torch",
    "cv2": "opencv-python",
    "PIL": "Pillow", "Image": "Pillow",
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "seaborn": "seaborn", "sns": "seaborn",
    "plotly": "plotly",
    "bokeh": "bokeh",
    "altair": "altair",
    "dash": "dash",
    "streamlit": "streamlit",
    "jupyter": "jupyter", "ipython": "jupyter", "IPython": "jupyter",
    "nbformat": "nbformat",
    "sympy": "sympy",
    "networkx": "networkx",
    "nltk": "nltk",
    "spacy": "spacy",
    "transformers": "transformers",
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
    "helipad": "helipad",
    "ipywidgets": "ipywidgets", "widgets": "ipywidgets",
    "pandas_datareader": "pandas-datareader", "pdr": "pandas-datareader",
    "voila": "voila",
    "paramiko": "paramiko",
    "d3": "D3.js", "d3js": "D3.js",
    "flask": "flask",
    "fastapi": "fastapi",
    "sqlalchemy": "SQLAlchemy",
    "boto3": "boto3",
}

#: (Kept for backward compatibility with any direct references)
_TOOL_ALIASES = _IMPORT_TO_PACKAGE

def _fetch_pypi_metadata(package_name: str) -> dict[str, str]:
    """Query the PyPI JSON API and return docs_url + a brief summary.

    Returns a dict with keys: docs_url, api_surface, common_patterns.
    Values are empty strings on any network/parse error.
    """
    # PyPI package names must not contain spaces; skip lookup for multi-word names
    safe_name = package_name.strip()
    if " " in safe_name:
        return {"docs_url": "", "api_surface": "", "common_patterns": ""}
    url = f"https://pypi.org/pypi/{safe_name}/json"
    result = {"docs_url": "", "api_surface": "", "common_patterns": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agentteams-enrich/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        info = data.get("info", {})

        # -- docs_url --
        project_urls: dict = info.get("project_urls") or {}
        for key in ("Documentation", "Docs", "Doc", "API Reference", "API"):
            val = project_urls.get(key, "")
            if val and val.startswith("http"):
                result["docs_url"] = val
                break
        if not result["docs_url"]:
            for key in ("Homepage", "Source", "Repository"):
                val = project_urls.get(key, "")
                if val and val.startswith("http"):
                    result["docs_url"] = val
                    break
        if not result["docs_url"]:
            result["docs_url"] = info.get("docs_url") or info.get("home_page") or ""

        # -- api_surface from summary + first line of description --
        summary = (info.get("summary") or "").strip()
        description = (info.get("description") or "")
        # Extract first non-empty non-badge line from description as extra context
        desc_lines = [ln.strip() for ln in description.splitlines()
                      if ln.strip() and not ln.strip().startswith(("![", "#", "=", "-", "```", "|"))]
        first_desc = desc_lines[0][:200] if desc_lines else ""
        if summary:
            result["api_surface"] = summary
            if first_desc and first_desc.lower() != summary.lower():
                result["api_surface"] += f". {first_desc}"
        elif first_desc:
            result["api_surface"] = first_desc

    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        pass
    return result


def _fetch_pypi_docs_url(package_name: str) -> str:
    """Query the PyPI JSON API for a package's documentation URL.

    Returns the URL string, or empty string on any error.
    """
    return _fetch_pypi_metadata(package_name)["docs_url"]


def _fetch_npm_metadata(package_name: str) -> dict[str, str]:
    """Query the public npm registry and return docs_url + a brief summary.

    Handles scoped packages (``@scope/name``) — the slash is a genuine path
    separator in npm's registry API, not something to strip; ``urllib.parse.quote``
    keeps ``@``/``/`` literal and percent-encodes anything else, so malformed or
    unexpected characters in a brief-provided tool name can't reach the request
    unescaped. Returns a dict with keys: docs_url, api_surface, common_patterns.
    Values are empty strings on any network/parse error.
    """
    safe_name = package_name.strip()
    if not safe_name or " " in safe_name:
        return {"docs_url": "", "api_surface": "", "common_patterns": ""}
    encoded_name = urllib.parse.quote(safe_name, safe="@/")
    url = f"https://registry.npmjs.org/{encoded_name}"
    result = {"docs_url": "", "api_surface": "", "common_patterns": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agentteams-enrich/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())

        latest_version = (data.get("dist-tags") or {}).get("latest", "")
        version_info = (data.get("versions") or {}).get(latest_version, {}) or {}

        # -- docs_url: homepage first, then repository URL --
        homepage = version_info.get("homepage") or data.get("homepage") or ""
        if homepage and homepage.startswith("http"):
            result["docs_url"] = homepage
        if not result["docs_url"]:
            repo = version_info.get("repository") or data.get("repository") or {}
            repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo)
            repo_url = re.sub(r"^git\+", "", repo_url).removesuffix(".git")
            if repo_url.startswith("http"):
                result["docs_url"] = repo_url

        # -- api_surface from the package description --
        description = (version_info.get("description") or data.get("description") or "").strip()
        if description:
            result["api_surface"] = description

    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        pass
    return result


def _get_docs_url(package_name: str) -> str:
    """Return documentation URL: unified static catalog first, then PyPI, then npm."""
    known = tool_metadata_catalog.get_tool_metadata(package_name).get("docs_url", "")
    if known:
        return known
    pypi_url = _fetch_pypi_docs_url(package_name)
    if pypi_url:
        return pypi_url
    return _fetch_npm_metadata(package_name)["docs_url"]


def scan_project_imports(project_path: Path) -> dict[str, str]:
    """Scan a project directory for Python imports. Returns {package_name: alias}.

    Reads .py files and Jupyter notebook code cells.
    """
    _import_re = re.compile(
        r"^\s*import\s+([\w]+)|^\s*from\s+([\w]+)\s+import", re.MULTILINE
    )
    _stdlib = frozenset({
        "os", "sys", "re", "json", "csv", "math", "random", "time",
        "datetime", "pathlib", "typing", "collections", "itertools",
        "functools", "abc", "copy", "io", "string", "struct", "hashlib",
        "uuid", "logging", "warnings", "unittest", "contextlib",
        "dataclasses", "enum", "gc", "operator", "pprint", "subprocess",
        "shutil", "tempfile", "argparse", "textwrap", "inspect", "ast",
        "decimal", "fractions", "cmath", "statistics", "array",
        "queue", "threading", "multiprocessing", "concurrent",
        "socket", "http", "urllib", "email", "html", "xml",
        "sqlite3", "shelve", "pickle", "gzip", "zipfile", "tarfile",
        "calendar", "locale", "gettext", "tkinter", "turtle",
        "ctypes", "mmap", "platform", "signal", "traceback",
        "types", "weakref", "bisect", "heapq", "difflib",
        "doctest", "pdb", "profile", "timeit",
        "this", "antigravity",
    })
    found: dict[str, str] = {}

    def _process(src: str) -> None:
        for m in _import_re.finditer(src):
            alias = (m.group(1) or m.group(2) or "").strip()
            if not alias or alias in _stdlib:
                continue
            pkg = _IMPORT_TO_PACKAGE.get(alias, alias.lower().replace("_", "-"))
            if pkg not in found:
                found[pkg] = alias

    for py_path in project_path.rglob("*.py"):
        if ".git" in py_path.parts or "__pycache__" in py_path.parts:
            continue
        try:
            _process(py_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass

    for nb_path in project_path.rglob("*.ipynb"):
        if ".git" in nb_path.parts or ".ipynb_checkpoints" in nb_path.parts:
            continue
        try:
            nb = json.loads(nb_path.read_text(encoding="utf-8", errors="ignore"))
            for cell in nb.get("cells", []):
                if cell.get("cell_type") == "code":
                    # `or []`, not a default arg: a JSON `null` source returns None
                    # from .get (key present), and "".join(None) raises TypeError.
                    _process("".join(cell.get("source") or []))
        except (json.JSONDecodeError, OSError):
            pass

    return found


def build_tool_catalog(
    package_names: list[str],
    *,
    fetch_pypi: bool = True,
) -> dict[str, dict[str, str]]:
    """Build a metadata catalog for a list of packages.

    Resolution order per package: the unified static catalog
    (agentteams.tool_metadata_catalog, zero network) first; if unresolved and
    `fetch_pypi` is True (also gates the npm registry fetch, despite the
    parameter's PyPI-era name — kept for its one existing call site), PyPI is
    tried, then npm.
    """
    catalog: dict[str, dict[str, str]] = {}
    for pkg in package_names:
        known = tool_metadata_catalog.get_tool_metadata(pkg)
        if known:
            catalog[pkg] = known
        elif fetch_pypi:
            meta = _fetch_pypi_metadata(pkg)
            if not meta["docs_url"]:
                # Fill gaps from npm rather than replacing wholesale — PyPI can
                # return a real api_surface (from its summary) alongside an
                # empty docs_url (no Documentation/Homepage/Source/Repository
                # project URL at all); discarding that in favor of npm's
                # (likely empty, for a Python package) result would silently
                # lose real data (post-implementation audit finding).
                npm_meta = _fetch_npm_metadata(pkg)
                meta = {field: meta[field] or npm_meta[field] for field in meta}
            catalog[pkg] = meta
        else:
            catalog[pkg] = {"docs_url": "", "api_surface": "", "common_patterns": ""}
    return catalog
