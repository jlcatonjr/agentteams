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
    "ipywidgets": "ipywidgets", "widgets": "ipywidgets",
    "pandas_datareader": "pandas-datareader", "pdr": "pandas-datareader",
    "voila": "voila",
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
    "ipywidgets": "https://ipywidgets.readthedocs.io/en/stable/",
    "pandas-datareader": "https://pandas-datareader.readthedocs.io/en/latest/",
    "voila": "https://voila.readthedocs.io/en/stable/",
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
    "seaborn": {
        "docs_url": "https://seaborn.pydata.org/api.html",
        "api_surface": (
            "Figure-level functions (sns.relplot, sns.displot, sns.catplot, sns.lmplot); "
            "axes-level functions (sns.scatterplot, sns.lineplot, sns.histplot, sns.kdeplot, "
            "sns.boxplot, sns.violinplot, sns.barplot, sns.heatmap, sns.pairplot); "
            "theming (sns.set_theme, sns.set_palette, sns.set_style); "
            "FacetGrid for multi-panel layout"
        ),
        "common_patterns": (
            "Call sns.set_theme() at the top of a notebook for consistent aesthetics. "
            "Pass tidy DataFrames via data=df with x='col', y='col' keyword arguments. "
            "Use hue= for colour-encoding a grouping variable. "
            "Seaborn is built on Matplotlib — use plt.tight_layout() and plt.savefig() as usual. "
            "Pitfall: seaborn expects long/tidy data — reshape wide DataFrames with pd.melt() first."
        ),
    },
    "plotly": {
        "docs_url": "https://plotly.com/python-api-reference/",
        "api_surface": (
            "Plotly Express (px) — high-level: px.scatter, px.line, px.bar, px.histogram, "
            "px.box, px.heatmap, px.choropleth; "
            "Graph Objects (go) — low-level: go.Figure, go.Scatter, go.Bar, go.Heatmap, "
            "fig.add_trace(), fig.update_layout(), fig.update_xaxes(); "
            "Export: fig.show(), fig.write_html(), fig.write_image()"
        ),
        "common_patterns": (
            "Use px for quick interactive charts; switch to go.Figure for fine-grained control. "
            "fig.show() renders inline in Jupyter — set pio.renderers.default='notebook' if blank. "
            "fig.update_layout(title=, xaxis_title=, yaxis_title=) for clean labelling. "
            "Export interactive charts with fig.write_html('chart.html'). "
            "Pitfall: Plotly figures are JSON-serialisable — very large datasets slow the browser."
        ),
    },
    "networkx": {
        "docs_url": "https://networkx.org/documentation/stable/reference/",
        "api_surface": (
            "Graph creation: nx.Graph(), nx.DiGraph(), nx.MultiGraph(); "
            "graph manipulation: G.add_node(), G.add_edge(), G.add_nodes_from(), G.add_edges_from(); "
            "algorithms: nx.shortest_path(), nx.degree_centrality(), nx.betweenness_centrality(), "
            "nx.pagerank(), nx.connected_components(), nx.is_connected(); "
            "drawing: nx.draw(), nx.draw_networkx(), nx.spring_layout()"
        ),
        "common_patterns": (
            "Create graphs with G = nx.Graph(); G.add_edges_from(edge_list). "
            "Store node attributes: G.nodes[n]['weight'] = val. "
            "Visualise with nx.draw(G, pos=nx.spring_layout(G), with_labels=True). "
            "For weighted shortest paths pass weight='weight' to the algorithm. "
            "Pitfall: NetworkX stores graphs in memory — for >100k nodes use GraphTool or igraph."
        ),
    },
    "scikit-learn": {
        "docs_url": "https://scikit-learn.org/stable/api/index.html",
        "api_surface": (
            "Estimator API: .fit(X, y), .predict(X), .transform(X), .fit_transform(X); "
            "linear models: LinearRegression, Ridge, Lasso, LogisticRegression; "
            "preprocessing: StandardScaler, MinMaxScaler, OneHotEncoder, LabelEncoder; "
            "model selection: train_test_split, cross_val_score, GridSearchCV, KFold; "
            "metrics: mean_squared_error, r2_score, accuracy_score, classification_report; "
            "pipeline: Pipeline, make_pipeline"
        ),
        "common_patterns": (
            "Always split train/test before fitting: X_train, X_test, y_train, y_test = "
            "train_test_split(X, y, test_size=0.2, random_state=42). "
            "Use Pipeline to chain preprocessing + model: Pipeline([('scaler', StandardScaler()), ('model', LinearRegression())]). "
            "cross_val_score(model, X, y, cv=5) for robust generalisation estimates. "
            "Pitfall: fit the scaler on training data only, then transform both train and test — "
            "never call .fit_transform() on the test set."
        ),
    },
    "helipad": {
        "docs_url": "https://helipad.dev/apidocs/",
        "api_surface": (
            "Model class — main simulation container; "
            "model.addPrimitive(name, cls) — register agent type; "
            "model.addParam(name, title, type, dflt) — add adjustable parameter; "
            "model.addPlot(name, title) / model.addSeries() — define visualisation; "
            "model.start() / model.launchGUI() — run simulation; "
            "Agent base class with step() method; "
            "match() function for pairwise agent interactions"
        ),
        "common_patterns": (
            "Define agent behaviour by subclassing Agent and overriding step(). "
            "Use model.addPrimitive() to register each agent class before calling start(). "
            "Parameters added with addParam() appear as GUI sliders — set dflt for the default value. "
            "Collect time-series data via model.addPlot() and model.addSeries(). "
            "Pitfall: helipad's interactive GUI requires a Tkinter event loop — "
            "in Jupyter use model.start() rather than model.launchGUI()."
        ),
    },
    "ipywidgets": {
        "docs_url": "https://ipywidgets.readthedocs.io/en/stable/",
        "api_surface": (
            "widgets.IntSlider / FloatSlider(value, min, max, step) — numeric sliders; "
            "widgets.Dropdown(options, value) — dropdown selector; "
            "widgets.Checkbox(value) — boolean toggle; "
            "widgets.Output() — capture display output; "
            "widgets.HBox / VBox(*children) — layout containers; "
            "interact(fn, **kwargs) / interactive(fn, **kwargs) — auto-generate UI from function signature; "
            "widgets.observe(handler, names) — react to value changes; "
            "display(widget) — render widget in notebook"
        ),
        "common_patterns": (
            "Use interact() or @interact decorator for quick exploratory UIs — "
            "pass slider ranges as (min, max) or (min, max, step) tuples. "
            "For more control use interactive() and display its .widget attribute. "
            "Combine multiple widgets with HBox/VBox for layout. "
            "Use widgets.Output() context manager to capture prints/plots inside callbacks. "
            "Pitfall: widgets only render in a live Jupyter kernel — "
            "use Voilà to serve them as standalone apps or nbconvert --to html for static export. "
            "Pitfall: observe callbacks fire on every keystroke for Text widgets — "
            "debounce with a submit Button or use continuous_update=False on sliders."
        ),
    },
    "pandas-datareader": {
        "docs_url": "https://pandas-datareader.readthedocs.io/en/latest/",
        "api_surface": (
            "pdr.DataReader(name, data_source, start, end) — fetch time-series data; "
            "data_source options: 'fred' (FRED), 'yahoo' (Yahoo Finance), "
            "'famafrench' (Fama-French), 'wb' (World Bank), 'oecd', 'eurostat'; "
            "pdr.fred.FredReader(symbols, start, end).read() — direct FRED access; "
            "pdr.wb.download(indicator, country, start, end) — World Bank data; "
            "Returns pandas DataFrame indexed by date"
        ),
        "common_patterns": (
            "Use pdr.DataReader('SERIES_ID', 'fred', start='2000-01-01') to fetch FRED series — "
            "SERIES_ID examples: 'GDP', 'CPIAUCSL', 'FEDFUNDS', 'UNRATE', 'M2SL'. "
            "Chain with .pct_change() or .diff() for growth rates. "
            "Pitfall: Yahoo Finance reader is unreliable — prefer yfinance for equity data. "
            "Pitfall: some data sources require an API key set as environment variable "
            "(e.g. FRED requires FRED_API_KEY for bulk requests). "
            "Wrap reads in try/except RemoteDataError for network resilience in notebooks."
        ),
    },
    "voila": {
        "docs_url": "https://voila.readthedocs.io/en/stable/",
        "api_surface": (
            "CLI: voila notebook.ipynb — serve notebook as web app; "
            "voila --port=8866 --no-browser notebook.ipynb — specify port; "
            "voila --template=material notebook.ipynb — apply theme; "
            "voila --strip_sources=True — hide source cells in output; "
            "binder integration via postBuild + voila server extension; "
            "Python API: VoilaConfiguration for programmatic config"
        ),
        "common_patterns": (
            "Convert any ipywidgets notebook to a dashboard with `voila notebook.ipynb`. "
            "For Binder deployment add `voila` to requirements.txt and set URL path to "
            "`/voila/render/notebook.ipynb` in the Binder badge URL. "
            "Use --strip_sources=True for student-facing dashboards to hide code cells. "
            "Pitfall: Voilà re-executes the entire notebook on each page load — "
            "cache expensive computations or use @lru_cache on data-loading functions. "
            "Pitfall: widgets that depend on display() must use widgets.Output() — "
            "bare matplotlib plt.show() calls may not render correctly under Voilà."
        ),
    },
}


def _fetch_pypi_metadata(package_name: str) -> dict[str, str]:
    """Query the PyPI JSON API and return docs_url + a brief summary.

    Returns a dict with keys: docs_url, api_surface, common_patterns.
    Values are empty strings on any network/parse error.
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
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
            canonical_url = _CANONICAL_DOCS.get(pkg.lower(), "")
            if canonical_url:
                catalog[pkg] = {
                    "docs_url": canonical_url,
                    "api_surface": "",
                    "common_patterns": "",
                }
            elif fetch_pypi:
                meta = _fetch_pypi_metadata(pkg)
                catalog[pkg] = meta
            else:
                catalog[pkg] = {"docs_url": "", "api_surface": "", "common_patterns": ""}
    return catalog
