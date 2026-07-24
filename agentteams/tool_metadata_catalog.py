"""tool_metadata_catalog.py — Unified static tool-metadata catalog.

Single source of truth for known-package docs_url/api_surface/common_patterns,
consulted both unconditionally (by agentteams.analyze, every generation run) and
by the opt-in --enrich network/AI enrichment pipeline (agentteams.enrich). Prior to
this module, three catalogs covering overlapping but non-identical package sets
existed and drifted apart: analyze.py's _KNOWN_TOOL_METADATA (the only one on the
unconditional path) and enrich/_tools.py's _TOOL_CATALOG + _CANONICAL_DOCS (reachable
only via --enrich) — see tmp/by-week/2026-W30/tool-doc-catalog-remediation.plan.md.

Merge provenance: entries came from _CANONICAL_DOCS (docs_url only) as a base layer,
overlaid by the richer _TOOL_CATALOG (docs_url + api_surface + common_patterns) where
both existed for the same tool, gap-filled by _KNOWN_TOOL_METADATA for the two tools
(linearmodels, sqlite) unique to it, plus a new pytest entry (sourced verbatim from
this repo's own hand-authored self-build brief). Five tools had a conflicting docs_url
between _KNOWN_TOOL_METADATA and _TOOL_CATALOG (both were otherwise equally 'rich');
_TOOL_CATALOG's value was kept in each case — the discarded _KNOWN_TOOL_METADATA URL
is recorded in a comment next to that entry below.
"""
from __future__ import annotations

import re


def normalize_tool_key(name: str) -> str:
    """Normalize a tool name into a catalog lookup key (alnum-only, lowercase)."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


TOOL_METADATA_CATALOG: dict[str, dict[str, str]] = {
    "arpscan": {  # arp-scan
        "docs_url": "https://github.com/royhills/arp-scan/wiki",
        "api_surface": "arp-scan --localnet — scan all hosts on local subnet; arp-scan --interface=<iface> <range> — scan on specific interface; arp-scan --retry=<n> — retry count for ARP probes; arp-scan --timeout=<ms> — per-probe timeout in milliseconds; Output: IP, MAC, vendor (from OUI database)",
        "common_patterns": "Always run with sudo — ARP scanning requires raw socket access. Use --localnet flag to automatically scan the local subnet. Parse output with awk/grep: `arp-scan --localnet | grep -v 'DUP\\|starting\\|packets'`. Combine with MAC vendor lookup files for device classification. Pitfall: may not detect devices with ARP filtering or strict firewalls. Pitfall: duplicate ARP responses may indicate ARP spoofing — check for DUP lines.",
    },
    "boto3": {
        "docs_url": "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/index.html",
        "api_surface": "",
        "common_patterns": "",
    },
    "d3js": {  # D3.js
        "docs_url": "https://d3js.org/api",
        "api_surface": "d3.select() / d3.selectAll() — DOM selection and chaining; selection.data() + .enter() + .exit() — data join pattern; d3.scaleLinear(), d3.scaleBand(), d3.scaleTime() — scales; d3.axisBottom(), d3.axisLeft() — axes; d3.line(), d3.area(), d3.arc() — path generators; d3.json(), d3.csv() — async data loading; d3.zoom(), d3.drag() — interaction; d3.transition() — animated updates",
        "common_patterns": "Always use the update-enter-exit (or join()) pattern for dynamic data. Use d3.select('#chart').append('svg').attr('viewBox', ...) for responsive sizing. For real-time updates, store the selection and call .data(newData).join() on each tick. Pitfall: D3 v6+ uses event parameter in callbacks — d3.event is removed. Pitfall: axes must be appended inside a <g> and called with .call(axis).",
    },
    "fastapi": {
        "docs_url": "https://fastapi.tiangolo.com/reference/",
        "api_surface": "",
        "common_patterns": "",
    },
    "flask": {
        "docs_url": "https://flask.palletsprojects.com/en/latest/api/",
        "api_surface": "",
        "common_patterns": "",
    },
    "helipad": {
        "docs_url": "https://helipad.dev/apidocs/",
        "api_surface": "Model class — main simulation container; model.addPrimitive(name, cls) — register agent type; model.addParam(name, title, type, dflt) — add adjustable parameter; model.addPlot(name, title) / model.addSeries() — define visualisation; model.start() / model.launchGUI() — run simulation; Agent base class with step() method; match() function for pairwise agent interactions",
        "common_patterns": "Define agent behaviour by subclassing Agent and overriding step(). Use model.addPrimitive() to register each agent class before calling start(). Parameters added with addParam() appear as GUI sliders — set dflt for the default value. Collect time-series data via model.addPlot() and model.addSeries(). Pitfall: helipad's interactive GUI requires a Tkinter event loop — in Jupyter use model.start() rather than model.launchGUI().",
    },
    "ipywidgets": {
        "docs_url": "https://ipywidgets.readthedocs.io/en/stable/",
        "api_surface": "widgets.IntSlider / FloatSlider(value, min, max, step) — numeric sliders; widgets.Dropdown(options, value) — dropdown selector; widgets.Checkbox(value) — boolean toggle; widgets.Output() — capture display output; widgets.HBox / VBox(*children) — layout containers; interact(fn, **kwargs) / interactive(fn, **kwargs) — auto-generate UI from function signature; widgets.observe(handler, names) — react to value changes; display(widget) — render widget in notebook",
        "common_patterns": "Use interact() or @interact decorator for quick exploratory UIs — pass slider ranges as (min, max) or (min, max, step) tuples. For more control use interactive() and display its .widget attribute. Combine multiple widgets with HBox/VBox for layout. Use widgets.Output() context manager to capture prints/plots inside callbacks. Pitfall: widgets only render in a live Jupyter kernel — use Voilà to serve them as standalone apps or nbconvert --to html for static export. Pitfall: observe callbacks fire on every keystroke for Text widgets — debounce with a submit Button or use continuous_update=False on sliders.",
    },
    "jupyter": {
        "docs_url": "https://docs.jupyter.org/en/latest/",
        "api_surface": "IPython display API (display, HTML, Markdown, Image); magic commands (%matplotlib inline, %run, %%time, %who); Jupyter widgets (ipywidgets); nbformat for programmatic notebook I/O",
        "common_patterns": "Use %matplotlib inline or %matplotlib widget at notebook top. Cell execution order matters — restart kernel and run all before submitting. Use display(df) instead of print(df) for formatted DataFrame rendering. Pitfall: hidden state from out-of-order execution causes hard-to-reproduce bugs.",
    },
    "linearmodels": {
        "docs_url": "https://bashtage.github.io/linearmodels/",
        "api_surface": "PanelOLS, PooledOLS, RandomEffects, fit",
        "common_patterns": "Make panel indexes explicit, state fixed-effects choices clearly, and inspect fit summaries before exporting results.",
    },
    "matplotlib": {  # discarded conflicting _KNOWN_TOOL_METADATA docs_url: https://matplotlib.org/stable/contents.html
        "docs_url": "https://matplotlib.org/stable/api/",
        "api_surface": "Functional interface (plt.plot, plt.scatter, plt.hist, plt.bar, plt.show); object-oriented interface (fig, ax = plt.subplots()); axes labels/titles/legends (ax.set_xlabel, ax.set_title, ax.legend); multiple subplots (plt.subplots(nrows, ncols)); saving figures (plt.savefig)",
        "common_patterns": "Prefer the OO interface (fig, ax = plt.subplots()) for multi-panel figures. Always set fig.tight_layout() before savefig to avoid clipped labels. Use plt.style.use('seaborn-v0_8') for publication-ready aesthetics. Pitfall: plt.show() clears the figure — call savefig before show.",
    },
    "networkx": {
        "docs_url": "https://networkx.org/documentation/stable/reference/",
        "api_surface": "Graph creation: nx.Graph(), nx.DiGraph(), nx.MultiGraph(); graph manipulation: G.add_node(), G.add_edge(), G.add_nodes_from(), G.add_edges_from(); algorithms: nx.shortest_path(), nx.degree_centrality(), nx.betweenness_centrality(), nx.pagerank(), nx.connected_components(), nx.is_connected(); drawing: nx.draw(), nx.draw_networkx(), nx.spring_layout()",
        "common_patterns": "Create graphs with G = nx.Graph(); G.add_edges_from(edge_list). Store node attributes: G.nodes[n]['weight'] = val. Visualise with nx.draw(G, pos=nx.spring_layout(G), with_labels=True). For weighted shortest paths pass weight='weight' to the algorithm. Pitfall: NetworkX stores graphs in memory — for >100k nodes use GraphTool or igraph.",
    },
    "nltk": {
        "docs_url": "https://www.nltk.org/api/nltk.html",
        "api_surface": "",
        "common_patterns": "",
    },
    "nmap": {
        "docs_url": "https://nmap.org/book/man.html",
        "api_surface": "nmap -sn <range> — ping scan (host discovery, no port scan); nmap -sV <host> — service/version detection; nmap -O <host> — OS detection (requires root); nmap -p <ports> <host> — specific port scan; nmap --script <script> <host> — NSE script execution; nmap -oX output.xml — XML output for parsing; nmap -oG - — greppable output",
        "common_patterns": "Use -sn for fast host discovery without port scanning. Combine with --open to only show hosts with open ports. Parse XML output with python-nmap library for programmatic use. Pitfall: OS detection (-O) and SYN scan (-sS) require root/sudo. Pitfall: aggressive scans (-A) can trigger IDS/firewall alerts on monitored networks. Use --host-timeout to prevent hangs on unresponsive hosts.",
    },
    "numpy": {  # discarded conflicting _KNOWN_TOOL_METADATA docs_url: https://numpy.org/doc/stable/
        "docs_url": "https://numpy.org/doc/stable/reference/",
        "api_surface": "ndarray creation (np.array, np.zeros, np.ones, np.arange, np.linspace); array operations (reshape, transpose, concatenate, stack); math (np.sum, np.mean, np.std, np.dot, np.linalg); broadcasting and vectorized arithmetic",
        "common_patterns": "Prefer vectorized operations over Python loops for performance. Use dtype=float64 explicitly when storing financial/econometric data. np.nan-safe aggregates: np.nanmean, np.nanstd. For boolean indexing: arr[arr > 0]. Pitfall: integer division in older NumPy — cast dtypes explicitly.",
    },
    "pandas": {  # discarded conflicting _KNOWN_TOOL_METADATA docs_url: https://pandas.pydata.org/docs/
        "docs_url": "https://pandas.pydata.org/docs/reference/",
        "api_surface": "DataFrame/Series creation and I/O (pd.read_csv, pd.read_excel, to_csv); indexing (.loc, .iloc, boolean indexing); groupby, merge/join, pivot_table; time-series (DatetimeIndex, resample, rolling); string methods (.str.*); missing data (dropna, fillna, isna)",
        "common_patterns": "Always set index explicitly after loading CSVs when a natural key exists. Use .copy() when slicing to avoid SettingWithCopyWarning. groupby().agg() for multi-stat summaries. pd.to_datetime() + dt accessor for time-series manipulation. Pitfall: chained indexing silently creates copies — use .loc.",
    },
    "pandasdatareader": {  # pandas-datareader; discarded conflicting _KNOWN_TOOL_METADATA docs_url: https://pydata.github.io/pandas-datareader/
        "docs_url": "https://pandas-datareader.readthedocs.io/en/latest/",
        "api_surface": "pdr.DataReader(name, data_source, start, end) — fetch time-series data; data_source options: 'fred' (FRED), 'yahoo' (Yahoo Finance), 'famafrench' (Fama-French), 'wb' (World Bank), 'oecd', 'eurostat'; pdr.fred.FredReader(symbols, start, end).read() — direct FRED access; pdr.wb.download(indicator, country, start, end) — World Bank data; Returns pandas DataFrame indexed by date",
        "common_patterns": "Use pdr.DataReader('SERIES_ID', 'fred', start='2000-01-01') to fetch FRED series — SERIES_ID examples: 'GDP', 'CPIAUCSL', 'FEDFUNDS', 'UNRATE', 'M2SL'. Chain with .pct_change() or .diff() for growth rates. Pitfall: Yahoo Finance reader is unreliable — prefer yfinance for equity data. Pitfall: some data sources require an API key set as environment variable (e.g. FRED requires FRED_API_KEY for bulk requests). Wrap reads in try/except RemoteDataError for network resilience in notebooks.",
    },
    "paramiko": {
        "docs_url": "https://docs.paramiko.org/en/stable/api/",
        "api_surface": "SSHClient — connect(), exec_command(), invoke_shell(), open_sftp(); Transport — request_port_forward(), open_channel(); SFTPClient — get(), put(), listdir(), stat(); RSAKey / Ed25519Key — from_private_key_file(); AuthenticationException, SSHException for error handling",
        "common_patterns": "Use SSHClient with AutoAddPolicy only in trusted environments; prefer RejectPolicy and known_hosts in production. Always close connections with client.close() or use context manager. For port forwarding, use transport.request_port_forward() and handle incoming channels in a thread. Pitfall: exec_command() stdout is blocking — read stdout before stderr to avoid deadlocks. Pitfall: set timeout= on connect() to avoid hanging on unreachable hosts.",
    },
    "plotly": {  # discarded conflicting _KNOWN_TOOL_METADATA docs_url: https://plotly.com/python/
        "docs_url": "https://plotly.com/python-api-reference/",
        "api_surface": "Plotly Express (px) — high-level: px.scatter, px.line, px.bar, px.histogram, px.box, px.heatmap, px.choropleth; Graph Objects (go) — low-level: go.Figure, go.Scatter, go.Bar, go.Heatmap, fig.add_trace(), fig.update_layout(), fig.update_xaxes(); Export: fig.show(), fig.write_html(), fig.write_image()",
        "common_patterns": "Use px for quick interactive charts; switch to go.Figure for fine-grained control. fig.show() renders inline in Jupyter — set pio.renderers.default='notebook' if blank. fig.update_layout(title=, xaxis_title=, yaxis_title=) for clean labelling. Export interactive charts with fig.write_html('chart.html'). Pitfall: Plotly figures are JSON-serialisable — very large datasets slow the browser.",
    },
    "pytest": {
        "docs_url": "https://docs.pytest.org/en/stable/",
        "api_surface": "- **Test discovery** — files matching `test_*.py` or `*_test.py`; functions prefixed `test_`\n- **`pytest.fixture(scope=...)`** — `\"function\"` (default), `\"class\"`, `\"module\"`, `\"session\"`\n- **`pytest.mark`** — `@pytest.mark.parametrize(argnames, argvalues)`, `@pytest.mark.skip`, `@pytest.mark.skipif`, `@pytest.mark.xfail`\n- **`monkeypatch`** — `.setattr()`, `.setenv()`, `.delenv()`, `.setitem()`, `.chdir()`\n- **`tmp_path`** — `pathlib.Path` fixture scoped to the test function\n- **`capsys` / `capfd`** — capture stdout/stderr; `.readouterr()` returns `(out, err)`\n- **`conftest.py`** — project-wide fixtures; auto-used by all tests in the directory\n- **Exit codes** — 0: all passed, 1: some failed, 2: interrupted, 3: internal error, 4: bad usage, 5: no tests",
        "common_patterns": "- **Fixtures over setUp/tearDown** — pytest fixtures are composable and scopeable; prefer them over `unittest.TestCase` methods\n- **`parametrize` flattens test matrices** — `@pytest.mark.parametrize(\"a,b,expected\", [...])` generates one test per row; ID is auto-derived\n- **`conftest.py` for shared fixtures** — never duplicate fixtures across test files; put project-wide fixtures in `tests/conftest.py`\n- **`tmp_path` not `tempfile`** — `tmp_path` is auto-cleaned and returns a `pathlib.Path`; no teardown needed\n- **`monkeypatch` scope** — only valid within the test function; never use it in session-scoped fixtures unless you understand the implications\n- **`-x` stops on first failure** — use during development; CI should run without `-x` to see all failures\n- **`-q` for clean output** — `python -m pytest tests/ -q` suppresses verbose per-test output in CI logs",
    },
    "requests": {
        "docs_url": "https://requests.readthedocs.io/en/latest/api/",
        "api_surface": "",
        "common_patterns": "",
    },
    "scikitlearn": {  # scikit-learn
        "docs_url": "https://scikit-learn.org/stable/api/index.html",
        "api_surface": "Estimator API: .fit(X, y), .predict(X), .transform(X), .fit_transform(X); linear models: LinearRegression, Ridge, Lasso, LogisticRegression; preprocessing: StandardScaler, MinMaxScaler, OneHotEncoder, LabelEncoder; model selection: train_test_split, cross_val_score, GridSearchCV, KFold; metrics: mean_squared_error, r2_score, accuracy_score, classification_report; pipeline: Pipeline, make_pipeline",
        "common_patterns": "Always split train/test before fitting: X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42). Use Pipeline to chain preprocessing + model: Pipeline([('scaler', StandardScaler()), ('model', LinearRegression())]). cross_val_score(model, X, y, cv=5) for robust generalisation estimates. Pitfall: fit the scaler on training data only, then transform both train and test — never call .fit_transform() on the test set.",
    },
    "scipy": {
        "docs_url": "https://docs.scipy.org/doc/scipy/reference/",
        "api_surface": "scipy.stats — probability distributions (norm, t, f, chi2, binom), hypothesis tests (ttest_ind, ttest_rel, mannwhitneyu, chi2_contingency, f_oneway), descriptive stats (describe, skew, kurtosis); scipy.optimize — minimize, curve_fit, root_scalar; scipy.linalg — solve, inv, det, eig",
        "common_patterns": "scipy.stats.norm.cdf/ppf for z-score and critical-value lookups. ttest_ind(a, b, equal_var=False) (Welch t-test) unless variances are verified equal. f_oneway(*groups) for one-way ANOVA. Pitfall: most distribution objects use scale (not variance) as the second parameter.",
    },
    "seaborn": {
        "docs_url": "https://seaborn.pydata.org/api.html",
        "api_surface": "Figure-level functions (sns.relplot, sns.displot, sns.catplot, sns.lmplot); axes-level functions (sns.scatterplot, sns.lineplot, sns.histplot, sns.kdeplot, sns.boxplot, sns.violinplot, sns.barplot, sns.heatmap, sns.pairplot); theming (sns.set_theme, sns.set_palette, sns.set_style); FacetGrid for multi-panel layout",
        "common_patterns": "Call sns.set_theme() at the top of a notebook for consistent aesthetics. Pass tidy DataFrames via data=df with x='col', y='col' keyword arguments. Use hue= for colour-encoding a grouping variable. Seaborn is built on Matplotlib — use plt.tight_layout() and plt.savefig() as usual. Pitfall: seaborn expects long/tidy data — reshape wide DataFrames with pd.melt() first.",
    },
    "spacy": {
        "docs_url": "https://spacy.io/api",
        "api_surface": "",
        "common_patterns": "",
    },
    "sqlalchemy": {
        "docs_url": "https://docs.sqlalchemy.org/en/latest/",
        "api_surface": "",
        "common_patterns": "",
    },
    "sqlite": {
        "docs_url": "https://www.sqlite.org/docs.html",
        "api_surface": "sqlite3 CLI, CREATE TABLE, CREATE INDEX, PRAGMA, EXPLAIN QUERY PLAN",
        "common_patterns": "Use parameterized queries, explicit transactions, and indexes validated with EXPLAIN QUERY PLAN.",
    },
    "ssh": {
        "docs_url": "https://www.openssh.com/manual.html",
        "api_surface": "ssh user@host — basic connection; ssh -L local_port:remote_host:remote_port user@host — local port forward; ssh -R remote_port:local_host:local_port user@host — remote port forward; ssh -N -f — background non-interactive tunnel; ssh -o StrictHostKeyChecking=no -o BatchMode=yes — scripting options; ssh-keygen -t ed25519 -C comment — key generation; ssh-copy-id user@host — install public key; scp / sftp — secure file transfer",
        "common_patterns": "For persistent tunnels use autossh or ssh with -o ServerAliveInterval=60. Use -N -f for background tunnels that only forward ports (no shell). Check tunnel is alive: nc -z localhost <local_port> or check /proc/<pid>. Use ~/.ssh/config to define host aliases, port, IdentityFile, and tunnels. Pitfall: -o StrictHostKeyChecking=no is unsafe in production — manage known_hosts. Pitfall: tunnels drop silently on network changes — always monitor and reconnect.",
    },
    "statsmodels": {
        "docs_url": "https://www.statsmodels.org/stable/api.html",
        "api_surface": "OLS regression (sm.OLS, smf.ols); GLM (sm.GLM); time-series models (ARIMA, SARIMAX, VAR); diagnostic tests (acf/pacf, Durbin-Watson, Breusch-Pagan, White test); summary tables (.summary(), .summary2()); formula interface (smf.ols('y ~ x1 + x2', data=df).fit())",
        "common_patterns": "Always call .fit() — the model object alone is not fitted. Use smf formula interface for clean model specification with categorical dummies. model.summary() prints LaTeX-ready tables. Use HC3 robust standard errors: .fit(cov_type='HC3'). Pitfall: sm.add_constant(X) must be called explicitly when using sm.OLS with arrays.",
    },
    "sympy": {
        "docs_url": "https://docs.sympy.org/latest/reference/",
        "api_surface": "",
        "common_patterns": "",
    },
    "tensorflow": {
        "docs_url": "https://www.tensorflow.org/api_docs/python/",
        "api_surface": "",
        "common_patterns": "",
    },
    "torch": {
        "docs_url": "https://pytorch.org/docs/stable/index.html",
        "api_surface": "",
        "common_patterns": "",
    },
    "transformers": {
        "docs_url": "https://huggingface.co/docs/transformers/index",
        "api_surface": "",
        "common_patterns": "",
    },
    "voila": {
        "docs_url": "https://voila.readthedocs.io/en/stable/",
        "api_surface": "CLI: voila notebook.ipynb — serve notebook as web app; voila --port=8866 --no-browser notebook.ipynb — specify port; voila --template=material notebook.ipynb — apply theme; voila --strip_sources=True — hide source cells in output; binder integration via postBuild + voila server extension; Python API: VoilaConfiguration for programmatic config",
        "common_patterns": "Convert any ipywidgets notebook to a dashboard with `voila notebook.ipynb`. For Binder deployment add `voila` to requirements.txt and set URL path to `/voila/render/notebook.ipynb` in the Binder badge URL. Use --strip_sources=True for student-facing dashboards to hide code cells. Pitfall: Voilà re-executes the entire notebook on each page load — cache expensive computations or use @lru_cache on data-loading functions. Pitfall: widgets that depend on display() must use widgets.Output() — bare matplotlib plt.show() calls may not render correctly under Voilà.",
    },
    "xgboost": {
        "docs_url": "https://xgboost.readthedocs.io/en/stable/python/python_api.html",
        "api_surface": "",
        "common_patterns": "",
    },
}


def get_tool_metadata(name: str) -> dict[str, str]:
    """Return {docs_url, api_surface, common_patterns} for a known tool, or {} if unknown."""
    return TOOL_METADATA_CATALOG.get(normalize_tool_key(name), {})


def is_known_tool(name: str) -> bool:
    """Return True if `name` has a catalog entry (of any completeness)."""
    return normalize_tool_key(name) in TOOL_METADATA_CATALOG
