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
                ids = self.vulns.get(key, [])
                # The real OSV querybatch answers a clean package with an empty object, not {"vulns": []}.
                out.append({"vulns": [{"id": i} for i in ids]} if ids else {})
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

class _NeverOpen:
    """An opener that fails the test if the transport ever tries to connect."""

    def open(self, req: object, timeout: float = 0) -> object:
        raise AssertionError(f"the transport opened a connection: {getattr(req, 'full_url', req)}")


@pytest.mark.parametrize("url", ["https://example.com/x", "http://api.osv.dev/v1/query",
                                 "https://api.osv.dev.example.com/v1/query", "https://api.osv.dev@example.com/v1/query",
                                 "ftp://api.osv.dev/v1/query"])
def test_transport_refuses_hosts_off_the_allowlist(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """V4: refused by the allowlist itself (matched message), before any connection is attempted."""
    monkeypatch.setattr(_http, "_OPENER", _NeverOpen())
    with pytest.raises(_http.FeedError, match="non-allowlisted"):
        _http.fetch_json(url)


def test_clean_packages_answered_with_empty_objects_are_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """V8: OSV's real shape for two clean packages is {"results": [{}, {}]}; that is clean, not malformed."""
    monkeypatch.setattr(_http, "fetch_json", _batch_reply({"results": [{}, {}]}))
    assert osv.vulnerable_ids([("npm", "a", "1.0.0"), ("npm", "b", "1.0.0")]) == {}
    monkeypatch.setattr(_http, "fetch_json", _batch_reply({"results": [{}, {"vulns": [{"id": "GHSA-x"}]}]}))
    assert osv.vulnerable_ids([("npm", "a", "1.0.0"), ("npm", "b", "1.0.0")]) == {("npm", "b", "1.0.0"): ["GHSA-x"]}


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


# ---------------------------------------------------------------- test matrix rev 2, area A (2026-W43)

def _batch_reply(reply: object) -> object:
    """Return a fake ``fetch_json`` that answers every querybatch with ``reply``."""
    def fake(url: str, payload: dict | None = None, **_: object) -> object:
        return reply
    return fake


@pytest.mark.parametrize("reply", [
    {"results": [{"vulns": []}]},                       # one result for two queries
    ["not", "a", "dict"],
    {"results": {"0": {"vulns": []}}},
    {"results": None},
    {},
    {"results": [{"vulns": []}, "not a dict"]},
    {"results": [{"vulns": []}, {"vulns": "nope"}]},
    {"results": [{"vulns": []}, {"vulns": []}, {"vulns": []}]},
])
def test_d1_malformed_or_short_batch_reply_fails_closed(monkeypatch: pytest.MonkeyPatch, reply: object) -> None:
    monkeypatch.setattr(_http, "fetch_json", _batch_reply(reply))
    with pytest.raises(_http.FeedError):
        osv.vulnerable_ids([("npm", "a", "1.0.0"), ("npm", "b", "1.0.0")])


def test_d1_short_reply_reaches_the_report_as_unscanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "requirements.txt").write_text("a==1.0\nb==2.0\n")
    monkeypatch.setattr(_http, "fetch_json", _batch_reply({"results": [{"vulns": []}]}))
    rep = engine.scan_path(tmp_path)
    assert rep.exit_code("none") == EXIT_UNSCANNED


class _Pager:
    """Fake querybatch that always hands back another page token; raises after ``limit`` calls."""

    def __init__(self, same_token: bool, limit: int = 500) -> None:
        self.calls = 0
        self.same_token = same_token
        self.limit = limit

    def __call__(self, url: str, payload: dict | None = None, **_: object) -> dict:
        self.calls += 1
        if self.calls > self.limit:
            raise AssertionError("pagination is unbounded")
        token = "t" if self.same_token else f"t{self.calls}"
        return {"results": [{"vulns": [{"id": f"V{self.calls}"}], "next_page_token": token}
                            for _ in payload["queries"]]}


@pytest.mark.parametrize("same_token", [True, False])
def test_d12_pagination_is_bounded(monkeypatch: pytest.MonkeyPatch, same_token: bool) -> None:
    pager = _Pager(same_token)
    monkeypatch.setattr(_http, "fetch_json", pager)
    with pytest.raises(_http.FeedError):
        osv.vulnerable_ids([("npm", "p", "1.0.0")])
    assert pager.calls <= getattr(osv, "MAX_PAGES", 0)


def test_d13_transient_detail_failure_is_retried_once(tmp_path: Path, feed: Feed,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "requirements.txt").write_text("a==1.0\nb==2.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["A1"], ("PyPI", "b", "2.0"): ["B1", "B2"]}
    records = {"A1": _adv("A1", "PyPI", "a", "0", "1.1"), "B1": _adv("B1", "PyPI", "b", "0", "2.1"),
               "B2": _adv("B2", "PyPI", "b", "0", "2.2")}
    attempts: dict[str, int] = {}

    def flaky(vuln_id: str) -> dict:
        attempts[vuln_id] = attempts.get(vuln_id, 0) + 1
        if attempts[vuln_id] == 1:
            raise _http.FeedError("connection reset")
        return records[vuln_id]

    monkeypatch.setattr(osv, "details", flaky)
    rep = engine.scan_path(tmp_path)
    assert rep.feed_errors == []
    assert {(f.name, f.fix) for f in rep.findings} == {("a", "1.1"), ("b", "2.2")}
    assert set(attempts) == {"A1", "B1", "B2"} and min(attempts.values()) >= 2   # fake is uncached


def test_d13_persistent_detail_failure_is_still_reported(tmp_path: Path, feed: Feed,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "requirements.txt").write_text("a==1.0\n")
    feed.vulns = {("PyPI", "a", "1.0"): ["A1"]}
    attempts: list[str] = []

    def down(vuln_id: str) -> dict:
        attempts.append(vuln_id)
        raise _http.FeedError("down")

    monkeypatch.setattr(osv, "details", down)
    rep = engine.scan_path(tmp_path)
    assert rep.exit_code() == EXIT_UNSCANNED and "A1" in rep.feed_errors[0]
    assert attempts == ["A1", "A1"]


# E1: _http.fetch_json against a fake opener

URL = "https://api.osv.dev/v1/vulns/X"


class _Resp:
    """Minimal context-manager response for a fake ``_OPENER``."""

    def __init__(self, body: bytes, url: str = URL) -> None:
        self.body = body
        self.url = url

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, n: int = -1) -> bytes:
        return self.body if n < 0 else self.body[:n]


class _Opener:
    """Fake opener: each ``open`` takes the next outcome (the last one repeats)."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def open(self, req: object, timeout: float = 0) -> _Resp:
        self.calls += 1
        out = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(_http.time, "sleep", slept.append)
    return slept


def _http_error(code: int) -> Exception:
    import urllib.error
    return urllib.error.HTTPError(URL, code, "x", {}, None)   # type: ignore[arg-type]


def test_e1_redirect_is_refused_not_followed(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> None:
    import email.message
    import io
    import urllib.request
    import urllib.response

    seen: list[str] = []

    class Fake302(urllib.request.BaseHandler):
        handler_order = 100

        def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
            seen.append(req.full_url)
            hdrs = email.message.Message()
            hdrs["Location"] = "https://api.osv.dev/v1/elsewhere"
            resp = urllib.response.addinfourl(io.BytesIO(b"{}"), hdrs, req.full_url, 302)
            resp.msg = "Found"
            return resp

    monkeypatch.setattr(_http, "_OPENER", urllib.request.build_opener(_http._NoRedirect, Fake302))
    with pytest.raises(_http.FeedError, match="refusing redirect 302"):
        _http.fetch_json(URL)
    assert seen == [URL]


def test_e1_final_url_off_allowlist(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> None:
    for final in ("https://evil.example/x", "http://api.osv.dev/v1/vulns/X"):
        monkeypatch.setattr(_http, "_OPENER", _Opener(_Resp(b"{}", final)))
        with pytest.raises(_http.FeedError, match="off the allowlist"):
            _http.fetch_json(URL)


def test_e1_body_size_bounds(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> None:
    monkeypatch.setattr(_http, "_OPENER", _Opener(_Resp(b"1")))
    with pytest.raises(_http.FeedError, match="outside"):
        _http.fetch_json(URL)
    monkeypatch.setitem(_http.SIZE_BOUNDS, "api.osv.dev", (2, 10))
    monkeypatch.setattr(_http, "_OPENER", _Opener(_Resp(b'{"a": "' + b"x" * 20 + b'"}')))
    with pytest.raises(_http.FeedError, match="outside"):
        _http.fetch_json(URL)
    monkeypatch.setattr(_http, "_OPENER", _Opener(_Resp(b'{"a": 1}')))
    assert _http.fetch_json(URL) == {"a": 1}


def test_e1_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> None:
    opener = _Opener(_http_error(404))
    monkeypatch.setattr(_http, "_OPENER", opener)
    with pytest.raises(_http.FeedError, match="HTTP 404"):
        _http.fetch_json(URL, retries=3)
    assert opener.calls == 1 and no_sleep == []


def test_e1_server_error_retried_then_fails(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> None:
    opener = _Opener(_http_error(503))
    monkeypatch.setattr(_http, "_OPENER", opener)
    with pytest.raises(_http.FeedError, match="failed after 3 attempts"):
        _http.fetch_json(URL, retries=2)
    assert opener.calls == 3 and len(no_sleep) == 2


def test_e1_non_json_then_json_succeeds(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> None:
    opener = _Opener(_Resp(b"<html>oops</html>"), _Resp(b'{"ok": true}'))
    monkeypatch.setattr(_http, "_OPENER", opener)
    assert _http.fetch_json(URL, {"q": 1}) == {"ok": True}
    assert opener.calls == 2 and len(no_sleep) == 1


# E3: fix_for edge ranges and MAL ids

def test_e3_fix_for_edge_ranges() -> None:
    open_ended = _adv("O", "npm", "p", "1.0.0", None)
    assert osv.fix_for(open_ended, "npm", "p", "1.5.0") is None
    multi = {"affected": [{"package": {"ecosystem": "npm", "name": "p"}, "ranges": [
        {"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.2.0"}]},
        {"type": "SEMVER", "events": [{"introduced": "2.0.0"}, {"fixed": "2.4.1"}]},
        {"type": "GIT", "events": [{"introduced": "abc"}, {"fixed": "def"}]}]}]}
    assert osv.fix_for(multi, "npm", "p", "1.1.9") == "1.2.0"
    assert osv.fix_for(multi, "npm", "p", "2.3.0") == "2.4.1"
    assert osv.fix_for(multi, "npm", "p", "1.5.0") is None
    assert osv.fix_for(_adv("E", "npm", "p", "3.0.0", "3.0.2"), "npm", "p", "3.0.0") == "3.0.2"
    assert osv.fix_for(_adv("E", "npm", "p", "3.0.0", "3.0.2"), "npm", "p", "2.9.9") is None
    assert osv.fix_for(_adv("E", "npm", "other", "0", "9"), "npm", "p", "1.0.0") is None
    assert osv.is_malicious("MAL-2026-1") and not osv.is_malicious("GHSA-xxxx")


# E6: local runtimes with fake executables on a temporary PATH

def _exe(path: Path, output: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{output}'\n")
    path.chmod(0o755)
    return path


def _runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[tuple[str, str, str, str]]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    seen: list[tuple[str, str, str, str]] = []

    def evaluate(kind: str, version: str, path: str = "", manager: str = "",
                 today: object = None) -> runtimes.RuntimeFinding:
        seen.append((kind, version, Path(path).name, manager))
        return runtimes.RuntimeFinding(kind, version, path, manager, 0, "ok", "", "")

    monkeypatch.setattr(runtimes, "evaluate", evaluate)
    return bindir, seen


def test_e6_local_runtimes_brew_nvm_tarball(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir, seen = _runtime_env(tmp_path, monkeypatch)
    node = _exe(tmp_path / "Cellar" / "node" / "24.22.1" / "bin" / "node", "v24.22.1")
    (bindir / "node").symlink_to(node)
    _exe(bindir / "php", "PHP 8.3.30 (cli) (built: Jan 1 2026)")
    py = _exe(tmp_path / ".nvm" / "versions" / "x" / "bin" / "python3", "Python 3.12.11")
    (bindir / "python3").symlink_to(py)
    rep = engine.local_runtimes("box")
    assert sorted(seen) == [("node", "24.22.1", "node", "brew"), ("php", "8.3.30", "php", "tarball"),
                            ("python", "3.12.11", "python3", "nvm")]
    assert rep.checked == 3 and not rep.findings and not rep.unscannable


def test_e6_manager_of_system_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir, _ = _runtime_env(tmp_path, monkeypatch)
    system_py = "/usr/bin/agentteams-test-no-such-python3"
    assert engine._manager_of(system_py) == "pkg:os"
    _exe(bindir / "dpkg", "python3.12-minimal: /usr/bin/python3.12")
    assert engine._manager_of(system_py) == "pkg:python3.12-minimal"
    assert engine._manager_of("/opt/homebrew/bin/agentteams-test-node") == "brew"


def test_e6_unreadable_version_is_unscannable_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir, seen = _runtime_env(tmp_path, monkeypatch)
    _exe(bindir / "node", "segmentation fault")
    _exe(bindir / "python3", "Python 3.12.11")
    assert engine._version_of(str(bindir / "node"), ["-v"]) == ""
    assert engine._version_of(str(tmp_path / "missing"), ["-v"]) == ""
    rep = engine.local_runtimes()
    assert [s[0] for s in seen] == ["python"]
    assert [Path(p).name for p, _ in rep.unscannable] == ["node"]
