"""_tools.py — Dynamic tool detection and documentation URL lookup."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path


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
    "flask": "flask",
    "fastapi": "fastapi",
    "sqlalchemy": "SQLAlchemy",
    "boto3": "boto3",
}

#: (Kept for backward compatibility with any direct references)
_TOOL_ALIASES = _IMPORT_TO_PACKAGE

#: Canonical documentation URLs for known packages (no network needed).
_CANONICAL_DOCS: dict[str, str] = {
    "numpy": "https://numpy.org/doc/stable/reference/",
    "pandas": "https://pandas.pydata.org/docs/reference/",
    "matplotlib": "https://matplotlib.org/stable/api/",
    "scipy": "https://docs.scipy.org/doc/scipy/reference/",
    "statsmodels": "https://www.statsmodels.org/stable/api.html",
    "scikit-learn": "https://scikit-learn.org/stable/api/index.html",
    "tensorflow": "https://www.tensorflow.org/api_docs/python/",
    "torch": "https://pytorch.org/docs/stable/index.html",
    "jupyter": "https://docs.jupyter.org/en/latest/",
    "seaborn": "https://seaborn.pydata.org/api.html",
    "plotly": "https://plotly.com/python-api-reference/",
    "sympy": "https://docs.sympy.org/latest/reference/",
    "networkx": "https://networkx.org/documentation/stable/reference/",
    "nltk": "https://www.nltk.org/api/nltk.html",
    "spacy": "https://spacy.io/api",
    "transformers": "https://huggingface.co/docs/transformers/index",
    "helipad": "https://helipad.dev/apidocs/",
    "requests": "https://requests.readthedocs.io/en/latest/api/",
    "flask": "https://flask.palletsprojects.com/en/latest/api/",
    "fastapi": "https://fastapi.tiangolo.com/reference/",
    "sqlalchemy": "https://docs.sqlalchemy.org/en/latest/",
    "boto3": "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/index.html",
    "xgboost": "https://xgboost.readthedocs.io/en/stable/python/python_api.html",
}

# Keep a small legacy inline catalog for the three most-used packages so that
# api_surface and common_patterns are available without a network call.
_TOOL_CATALOG: dict[str, dict[str, str]] = {
    "numpy": {
        "docs_url": "https://numpy.org/doc/stable/reference/",
        "api_surface": (
            "ndarray creation (np.array, np.zeros, np.ones, np.arange, np.linspace); "
            "array operations (reshape, transpose, concatenate, stack); "
            "math (np.sum, np.mean, np.std, np.dot, np.linalg); "
            "broadcasting and vectorized arithmetic"
        ),
        "common_patterns": (
            "Prefer vectorized operations over Python loops for performance. "
            "Use dtype=float64 explicitly when storing financial/econometric data. "
            "np.nan-safe aggregates: np.nanmean, np.nanstd. "
            "For boolean indexing: arr[arr > 0]. "
            "Pitfall: integer division in older NumPy — cast dtypes explicitly."
        ),
    },
    "pandas": {
        "docs_url": "https://pandas.pydata.org/docs/reference/",
        "api_surface": (
            "DataFrame/Series creation and I/O (pd.read_csv, pd.read_excel, to_csv); "
            "indexing (.loc, .iloc, boolean indexing); "
            "groupby, merge/join, pivot_table; "
            "time-series (DatetimeIndex, resample, rolling); "
            "string methods (.str.*); "
            "missing data (dropna, fillna, isna)"
        ),
        "common_patterns": (
            "Always set index explicitly after loading CSVs when a natural key exists. "
            "Use .copy() when slicing to avoid SettingWithCopyWarning. "
            "groupby().agg() for multi-stat summaries. "
            "pd.to_datetime() + dt accessor for time-series manipulation. "
            "Pitfall: chained indexing silently creates copies — use .loc."
        ),
    },
    "matplotlib": {
        "docs_url": "https://matplotlib.org/stable/api/",
        "api_surface": (
            "Functional interface (plt.plot, plt.scatter, plt.hist, plt.bar, plt.show); "
            "object-oriented interface (fig, ax = plt.subplots()); "
            "axes labels/titles/legends (ax.set_xlabel, ax.set_title, ax.legend); "
            "multiple subplots (plt.subplots(nrows, ncols)); "
            "saving figures (plt.savefig)"
        ),
        "common_patterns": (
            "Prefer the OO interface (fig, ax = plt.subplots()) for multi-panel figures. "
            "Always set fig.tight_layout() before savefig to avoid clipped labels. "
            "Use plt.style.use('seaborn-v0_8') for publication-ready aesthetics. "
            "Pitfall: plt.show() clears the figure — call savefig before show."
        ),
    },
    "scipy": {
        "docs_url": "https://docs.scipy.org/doc/scipy/reference/",
        "api_surface": (
            "scipy.stats — probability distributions (norm, t, f, chi2, binom), "
            "hypothesis tests (ttest_ind, ttest_rel, mannwhitneyu, chi2_contingency, f_oneway), "
            "descriptive stats (describe, skew, kurtosis); "
            "scipy.optimize — minimize, curve_fit, root_scalar; "
            "scipy.linalg — solve, inv, det, eig"
        ),
        "common_patterns": (
            "scipy.stats.norm.cdf/ppf for z-score and critical-value lookups. "
            "ttest_ind(a, b, equal_var=False) (Welch t-test) unless variances are verified equal. "
            "f_oneway(*groups) for one-way ANOVA. "
            "Pitfall: most distribution objects use scale (not variance) as the second parameter."
        ),
    },
    "statsmodels": {
        "docs_url": "https://www.statsmodels.org/stable/api.html",
        "api_surface": (
            "OLS regression (sm.OLS, smf.ols); "
            "GLM (sm.GLM); "
            "time-series models (ARIMA, SARIMAX, VAR); "
            "diagnostic tests (acf/pacf, Durbin-Watson, Breusch-Pagan, White test); "
            "summary tables (.summary(), .summary2()); "
            "formula interface (smf.ols('y ~ x1 + x2', data=df).fit())"
        ),
        "common_patterns": (
            "Always call .fit() — the model object alone is not fitted. "
            "Use smf formula interface for clean model specification with categorical dummies. "
            "model.summary() prints LaTeX-ready tables. "
            "Use HC3 robust standard errors: .fit(cov_type='HC3'). "
            "Pitfall: sm.add_constant(X) must be called explicitly when using sm.OLS with arrays."
        ),
    },
    "jupyter": {
        "docs_url": "https://docs.jupyter.org/en/latest/",
        "api_surface": (
            "IPython display API (display, HTML, Markdown, Image); "
            "magic commands (%matplotlib inline, %run, %%time, %who); "
            "Jupyter widgets (ipywidgets); "
            "nbformat for programmatic notebook I/O"
        ),
        "common_patterns": (
            "Use %matplotlib inline or %matplotlib widget at notebook top. "
            "Cell execution order matters — restart kernel and run all before submitting. "
            "Use display(df) instead of print(df) for formatted DataFrame rendering. "
            "Pitfall: hidden state from out-of-order execution causes hard-to-reproduce bugs."
        ),
    },
}


def _fetch_pypi_docs_url(package_name: str) -> str:
    """Query the PyPI JSON API for a package's documentation URL.

    Returns the URL string, or empty string on any error.
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agentteams-enrich/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        info = data.get("info", {})
        project_urls: dict = info.get("project_urls") or {}
        for key in ("Documentation", "Docs", "Homepage"):
            val = project_urls.get(key, "")
            if val and val.startswith("http"):
                return val
        docs = info.get("docs_url") or ""
        if docs:
            return docs
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        pass
    return ""


def _get_docs_url(package_name: str) -> str:
    """Return documentation URL: canonical dict first, then PyPI lookup."""
    canonical = _CANONICAL_DOCS.get(package_name.lower(), "")
    if canonical:
        return canonical
    return _fetch_pypi_docs_url(package_name)


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
                    _process("".join(cell.get("source", [])))
        except (json.JSONDecodeError, OSError):
            pass

    return found


def build_tool_catalog(
    package_names: list[str],
    *,
    fetch_pypi: bool = True,
) -> dict[str, dict[str, str]]:
    """Build a metadata catalog for a list of packages.

    Uses the legacy _TOOL_CATALOG for rich entries; fills docs_url from
    _CANONICAL_DOCS or PyPI for everything else.
    """
    catalog: dict[str, dict[str, str]] = {}
    for pkg in package_names:
        if pkg in _TOOL_CATALOG:
            catalog[pkg] = _TOOL_CATALOG[pkg]
        else:
            docs_url = _CANONICAL_DOCS.get(pkg.lower(), "")
            if not docs_url and fetch_pypi:
                docs_url = _fetch_pypi_docs_url(pkg)
            catalog[pkg] = {"docs_url": docs_url, "api_surface": "", "common_patterns": ""}
    return catalog
