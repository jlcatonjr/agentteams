"""agentteams.vulnscan: version ordering, manifest parsing, OSV logic, engine, runtimes, reports.

Network-free: every feed call goes through ``agentteams.vulnscan._http.fetch_json``,
which the ``feed`` fixture replaces with a router over canned responses.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine, manifests, osv, runtimes
from agentteams.vulnscan.report import EXIT_CLEAN, EXIT_COMPROMISE, EXIT_FINDINGS, EXIT_UNSCANNED, Report
from agentteams.vulnscan.versions import is_final, major, version_key


def _adv(vid: str, eco: str, name: str, introduced: str, fixed: str | None, severity: str = "HIGH") -> dict:
    events = [{"introduced": introduced}] + ([{"fixed": fixed}] if fixed else [])
    return {"id": vid, "database_specific": {"severity": severity} if severity else {},
            "affected": [{"package": {"ecosystem": eco, "name": name}, "ranges": [{"type": "ECOSYSTEM", "events": events}]}]}


class Feed:
    """Canned OSV / release-data responses keyed by (ecosystem, name, version) and URL."""

    def __init__(self) -> None:
        self.vulns: dict[tuple[str, str, str], list[str]] = {}
        self.records: dict[str, dict] = {}
        self.urls: dict[str, object] = {}
        self.fail = False

    def __call__(self, url: str, payload: dict | None = None, **_: object) -> object:
        if self.fail:
            raise _http.FeedError("simulated outage")
        if url.endswith("/querybatch"):
            out = []
            for q in payload["queries"]:
                key = (q["package"]["ecosystem"], q["package"]["name"], q["version"])
                out.append({"vulns": [{"id": i} for i in self.vulns.get(key, [])]})
            return {"results": out}
        if "/v1/vulns/" in url:
            return self.records[url.rsplit("/", 1)[1]]
        if url in self.urls:
            return self.urls[url]
        raise _http.FeedError(f"unexpected URL {url}")


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    f = Feed()
    monkeypatch.setattr(_http, "fetch_json", f)
    osv._DETAILS.clear()
    runtimes._CACHE.clear()
    return f


# ---------------------------------------------------------------- versions

def test_version_ordering_handles_prerelease_post_and_padding() -> None:
    order = ["1.0.0-rc.1", "1.0", "1.0.0.post1", "1.0.1", "1.10.0", "2!0.1"]
    assert sorted(order, key=version_key) == order
    assert version_key("v24.22.1") > version_key("24.22.0")
    assert is_final("3.1.3") and not is_final("3.1.3rc1")
    assert major("v24.1.0") == 24


# ---------------------------------------------------------------- manifests

def test_discover_skips_vendored_trees_and_linked_worktrees(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "requirements.txt").write_text("a==1\n")
    (tmp_path / "app" / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "app" / "node_modules" / "x" / "package-lock.json").write_text("{}")
    wt = tmp_path / "app.worktrees" / "feature"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /repos/app/.git/worktrees/feature\n")
    (wt / "requirements.txt").write_text("a==1\n")
    sub = tmp_path / "lib"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/modules/lib\n")   # submodule: kept
    (sub / "requirements-dev.txt").write_text("b==2\n")
    found = {p.relative_to(tmp_path).as_posix() for p in manifests.discover(tmp_path)}
    assert found == {"app/requirements.txt", "lib/requirements-dev.txt"}
    with_wt = {p.relative_to(tmp_path).as_posix() for p in manifests.discover(tmp_path, include_worktrees=True)}
    assert "app.worktrees/feature/requirements.txt" in with_wt


def test_requirements_pins_extras_markers_crlf_and_unpinned(tmp_path: Path) -> None:
    p = tmp_path / "requirements.txt"
    p.write_bytes(b"# c\r\nFlask[async]==3.1.2 ; python_version>'3'\r\nrequests>=2\r\n-r other.txt\r\nPyJWT==2.3.0  # pin\r\n")
    m = manifests.parse(p)
    assert [(d.name, d.version) for d in m.deps] == [("Flask", "3.1.2"), ("PyJWT", "2.3.0")]
    assert m.unpinned == 1 and not m.unscannable
    p.write_text("requests>=2\n")
    assert "no exact" in manifests.parse(p).unscannable


def test_npm_lock_v3_v1_yarn_composer_poetry_uv_pipfile(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "root"}, "node_modules/axios": {"version": "1.7.7"},
        "node_modules/vite": {"version": "4.5.5", "dev": True},
        "node_modules/a/node_modules/@s/b": {"version": "2.0.0"}, "node_modules/l": {"link": True, "version": "0"}}}))
    deps = {(d.name, d.version, d.scope) for d in manifests.parse(tmp_path / "package-lock.json").deps}
    assert deps == {("axios", "1.7.7", "runtime"), ("vite", "4.5.5", "dev"), ("@s/b", "2.0.0", "runtime")}

    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "package-lock.json").write_text(json.dumps({"lockfileVersion": 1, "dependencies": {
        "qs": {"version": "6.14.2", "dependencies": {"side": {"version": "1.0.0", "dev": True}}}}}))
    assert {(d.name, d.scope) for d in manifests.parse(v1 / "package-lock.json").deps} == {("qs", "runtime"), ("side", "dev")}

    (tmp_path / "yarn.lock").write_text('"@babel/core@^7.0.0", "@babel/core@^7.1":\n  version "7.29.0"\n\n'
                                        'lodash@npm:^4.17.0:\n  version: 4.17.21\n')
    assert {(d.name, d.version) for d in manifests.parse(tmp_path / "yarn.lock").deps} == {
        ("@babel/core", "7.29.0"), ("lodash", "4.17.21")}

    (tmp_path / "composer.lock").write_text(json.dumps({"packages": [{"name": "guzzle/guzzle", "version": "v7.4.0"}],
                                                         "packages-dev": [{"name": "phpunit/phpunit", "version": "9.5.0"}]}))
    assert {(d.name, d.version, d.scope) for d in manifests.parse(tmp_path / "composer.lock").deps} == {
        ("guzzle/guzzle", "7.4.0", "runtime"), ("phpunit/phpunit", "9.5.0", "dev")}

    (tmp_path / "uv.lock").write_text('[[package]]\nname = "me"\nversion = "0.1"\nsource = { editable = "." }\n\n'
                                      '[[package]]\nname = "httpx"\nversion = "0.27.0"\n')
    assert [(d.name, d.version) for d in manifests.parse(tmp_path / "uv.lock").deps] == [("httpx", "0.27.0")]

    (tmp_path / "Pipfile.lock").write_text(json.dumps({"default": {"django": {"version": "==4.2.1"}},
                                                       "develop": {"pytest": {"version": "==8.0.0"}}}))
    assert {(d.name, d.scope) for d in manifests.parse(tmp_path / "Pipfile.lock").deps} == {
        ("django", "runtime"), ("pytest", "dev")}


def test_package_json_without_lock_is_unscannable_and_bad_json_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    assert [p.name for p in manifests.discover(tmp_path)] == ["package.json"]
    assert "without a lockfile" in manifests.parse(tmp_path / "package.json").unscannable
    (tmp_path / "package-lock.json").write_text("{not json")
    assert "could not parse" in manifests.parse(tmp_path / "package-lock.json").unscannable


# ---------------------------------------------------------------- osv

def test_fix_for_picks_the_range_containing_the_version(feed: Feed) -> None:
    rec = {"affected": [{"package": {"ecosystem": "PyPI", "name": "Werkzeug"}, "ranges": [{"type": "ECOSYSTEM", "events": [
        {"introduced": "0"}, {"fixed": "2.3.8"}, {"introduced": "3.0.0"}, {"fixed": "3.1.9"}]}]}]}
    assert osv.fix_for(rec, "PyPI", "werkzeug", "3.1.3") == "3.1.9"
    assert osv.fix_for(rec, "PyPI", "werkzeug", "2.0.0") == "2.3.8"
    assert osv.fix_for(rec, "PyPI", "werkzeug", "3.1.9") is None
    feed.records = {"A": _adv("A", "PyPI", "x", "0", "1.2.0"), "B": _adv("B", "PyPI", "x", "1.0", "1.4.0"),
                    "C": _adv("C", "PyPI", "x", "0", None)}
    assert osv.minimal_fix(["A", "B", "C"], "PyPI", "x", "1.1.0") == ("1.4.0", 1)


def test_vulnerable_ids_follows_page_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake(url: str, payload: dict | None = None, **_: object) -> dict:
        calls.append(payload)
        q = payload["queries"][0]
        if "page_token" in q:
            return {"results": [{"vulns": [{"id": "B"}]}]}
        return {"results": [{"vulns": [{"id": "A"}], "next_page_token": "t1"}]}

    monkeypatch.setattr(_http, "fetch_json", fake)
    assert osv.vulnerable_ids([("npm", "p", "1.0.0")]) == {("npm", "p", "1.0.0"): ["A", "B"]}
    assert len(calls) == 2


# ---------------------------------------------------------------- engine + report

def test_scan_path_reports_per_repo_with_exit_codes(tmp_path: Path, feed: Feed) -> None:
    repo = tmp_path / "svc"
    (repo / ".git").mkdir(parents=True)
    (repo / "requirements.txt").write_text("PyJWT==2.3.0\nrequests==2.33.0\n")
    (repo / "web").mkdir()
    (repo / "web" / "package-lock.json").write_text(json.dumps({"packages": {"node_modules/vite": {"version": "4.5.5", "dev": True}}}))
    feed.vulns = {("PyPI", "PyJWT", "2.3.0"): ["G1"], ("npm", "vite", "4.5.5"): ["G2"]}
    feed.records = {"G1": _adv("G1", "PyPI", "PyJWT", "0", "2.15.0", "CRITICAL"),
                    "G2": _adv("G2", "npm", "vite", "0", "8.3.3", "HIGH")}
    rep = engine.scan_path(tmp_path)
    got = {(f.target, f.name, f.fix, f.scope, f.severity) for f in rep.findings}
    assert got == {("svc", "PyJWT", "2.15.0", "runtime", 4), ("svc", "vite", "8.3.3", "dev", 3)}
    assert rep.exit_code("critical") == EXIT_FINDINGS
    assert Report(target="x", findings=[f for f in rep.findings if f.scope == "dev"]).exit_code("high") == EXIT_CLEAN
    assert Report(target="x", findings=[f for f in rep.findings if f.scope == "dev"]).exit_code("high", include_dev=True) == EXIT_FINDINGS
    md = rep.markdown()
    assert "PyJWT" in md and "dev-only" in md


def test_feed_outage_is_unscanned_never_clean(tmp_path: Path, feed: Feed) -> None:
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    feed.fail = True
    rep = engine.scan_path(tmp_path)
    assert rep.exit_code("none") == EXIT_UNSCANNED
    assert "Partially unscanned" in rep.markdown()


def test_unrated_advisories_count_as_moderate(tmp_path: Path, feed: Feed) -> None:
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["PYSEC-1"]}
    feed.records = {"PYSEC-1": _adv("PYSEC-1", "PyPI", "a", "0", "1.1", severity="")}
    rep = engine.scan_path(tmp_path)
    assert rep.findings[0].severity == 2
    assert rep.exit_code("moderate") == EXIT_FINDINGS and rep.exit_code("high") == EXIT_CLEAN


def test_inventory_distro_hostcheck_ioc_and_malicious(feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:
    feed.vulns = {("PyPI", "cryptography", "3.4.8"): ["G3"], ("PyPI", "litellm", "1.107.0"): ["MAL-2026-1"]}
    feed.records = {"G3": _adv("G3", "PyPI", "cryptography", "0", "42.0.0"),
                    "MAL-2026-1": _adv("MAL-2026-1", "PyPI", "litellm", "1.107.0", None, "")}
    monkeypatch.setattr(runtimes, "evaluate", lambda k, v, p="", m="", today=None: runtimes.RuntimeFinding(
        k, v, p, m, 3, "eol", "line is end-of-life", "24.22.1"))
    log = "\n".join([
        "host: h1", "INV\tPyPI\tcryptography\t3.4.8\t/usr/lib/python3/dist-packages",
        "INV\tPyPI\tlitellm\t1.107.0\t/opt/app/.venv/lib/python3.12/site-packages",
        "RUNTIME\tnode\t20.19.0\t/usr/local/bin/node\ttarball",
        "HOSTCHECK\tHIGH\t3 pending OS security package updates", "noise line"])
    rep = engine.scan_inventory(log, "h1")
    by = {f.name: f for f in rep.findings}
    assert by["cryptography"].distro and not by["litellm"].distro
    assert by["litellm"].malicious and by["node"].fix == "24.22.1" and by["host"].severity == 3
    assert rep.exit_code() == EXIT_COMPROMISE
    rep2 = engine.scan_inventory("IOC\tindicator path present: /tmp/x\n", "h2")
    assert rep2.exit_code() == EXIT_COMPROMISE


def test_report_round_trip_and_new_keys(feed: Feed, tmp_path: Path) -> None:
    from agentteams.vulnscan.report import Finding, new_finding_keys
    a = Report(target="t", findings=[Finding(kind="package", target="r", name="x", version="1", ecosystem="npm")])
    b = Report.from_dict(json.loads(json.dumps(a.to_dict())))
    b.findings.append(Finding(kind="package", target="r", name="y", version="2", ecosystem="npm"))
    assert new_finding_keys(b, a) == {"r|package|npm|y|2|"}
    assert "**NEW**" in b.markdown(new_keys=new_finding_keys(b, a))


# ---------------------------------------------------------------- runtimes

def _eol_feed(feed: Feed) -> None:
    feed.urls["https://endoflife.date/api/v1/products/nodejs"] = {"result": {"releases": [
        {"name": "24", "isEol": False, "latest": {"name": "24.22.1"}},
        {"name": "20", "isEol": True, "eolFrom": "2026-04-30", "latest": {"name": "20.19.9"}}]}}
    feed.urls["https://nodejs.org/dist/index.json"] = [
        {"version": "v24.22.1", "security": True}, {"version": "v24.22.0", "security": False}]
    feed.urls["https://endoflife.date/api/v1/products/python"] = {"result": {"releases": [
        {"name": "3.12", "isEol": False, "latest": {"name": "3.12.11"}}]}}


def test_runtime_verdicts(feed: Feed) -> None:
    _eol_feed(feed)
    assert runtimes.evaluate("node", "v24.22.0").verdict == "security-release-missing"
    assert runtimes.evaluate("node", "24.22.1").verdict == "ok"
    eol = runtimes.evaluate("node", "20.19.0")
    assert eol.verdict == "eol" and eol.upgrade_to == "24.22.1"
    assert runtimes.evaluate("python", "3.12.3").verdict == "behind-patch"
    assert runtimes.evaluate("node", "20.0.0", manager="pkg:nodejs").verdict == "distro"


def test_runtime_v0_api_fallback_and_unchecked(feed: Feed) -> None:
    feed.urls["https://endoflife.date/api/php.json"] = [{"cycle": "8.3", "eol": "2099-01-01", "latest": "8.3.36"},
                                                       {"cycle": "8.1", "eol": "2025-12-31", "latest": "8.1.33"}]
    feed.urls["https://www.php.net/releases/index.php?json&max=200&version=8"] = {
        "8.3.36": {"tags": ["security"]}, "8.3.35": {"tags": []}}
    today = dt.date(2026, 10, 22)
    assert runtimes.evaluate("php", "8.3.35", today=today).verdict == "security-release-missing"
    assert runtimes.evaluate("php", "8.1.2", today=today).verdict == "eol"
    feed.fail = True
    runtimes._CACHE.clear()
    assert runtimes.evaluate("python", "3.12.0").verdict == "unchecked"


# ---------------------------------------------------------------- transport

def test_transport_refuses_hosts_off_the_allowlist() -> None:
    with pytest.raises(_http.FeedError):
        _http.fetch_json("https://example.com/x")
    with pytest.raises(_http.FeedError):
        _http.fetch_json("http://api.osv.dev/v1/query")


# ---------------------------------------------------------------- review regressions (2026-10-22)

def test_version_key_never_raises_and_orders_pep440_pre_releases() -> None:
    sorted(["1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-0", "1.0.0-rc1", "weird"], key=version_key)
    assert version_key("1.0.dev1") < version_key("1.0a1") < version_key("1.0b1") < version_key("1.0rc1") < version_key("1.0")


def test_pypi_names_match_after_pep503_normalisation(feed: Feed) -> None:
    rec = _adv("G", "PyPI", "python_dotenv", "0", "1.2.2")
    assert osv.fix_for(rec, "PyPI", "Python-Dotenv", "1.0.0") == "1.2.2"


def test_new_advisory_on_known_version_is_new() -> None:
    from agentteams.vulnscan.report import Finding, new_finding_keys
    old = Report(target="t", findings=[Finding(kind="package", target="r", name="x", version="1", ids=["A"])])
    now = Report(target="t", findings=[Finding(kind="package", target="r", name="x", version="1", ids=["A", "B"])])
    assert new_finding_keys(now, old)
    h1 = Finding(kind="host", target="h", name="host", detail="reboot required")
    h2 = Finding(kind="host", target="h", name="host", detail="3 pending security updates")
    assert h1.key() != h2.key()


def test_one_unreadable_advisory_does_not_hide_the_rest(tmp_path: Path, feed: Feed,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "requirements.txt").write_text("a==1.0\nb==2.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["GOOD"], ("PyPI", "b", "2.0"): ["GONE"]}
    feed.records = {"GOOD": _adv("GOOD", "PyPI", "a", "0", "1.1", "HIGH")}

    def call(url: str, payload: dict | None = None, **kw: object) -> object:
        if url.endswith("/GONE"):
            raise _http.FeedError("HTTP 404")
        return feed(url, payload, **kw)

    monkeypatch.setattr(_http, "fetch_json", call)
    rep = engine.scan_path(tmp_path)
    assert {f.name for f in rep.findings} == {"a", "b"}
    assert rep.exit_code() == EXIT_UNSCANNED and "GONE" in rep.feed_errors[0]


def test_missing_security_flags_keep_runtime_high(feed: Feed) -> None:
    feed.urls["https://endoflife.date/api/v1/products/php"] = {"result": {"releases": [
        {"name": "8.3", "isEol": False, "latest": {"name": "8.3.36"}}]}}
    feed.urls["https://www.php.net/releases/index.php?json&max=200&version=8"] = ["unexpected", "shape"]
    v = runtimes.evaluate("php", "8.3.30")
    assert v.verdict == "behind-patch" and v.severity == 3
