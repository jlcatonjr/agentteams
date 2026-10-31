"""Unpinned Python requirements (N6a): specifier sets, the OSV interval walker, range judgements, the engine.

No network: OSV is a canned feed that answers name-only ``querybatch`` queries. Nothing here calls PyPI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, effectiveness, engine, fix, osv
from agentteams.vulnscan import specifiers as sp
from agentteams.vulnscan.report import EXIT_CLEAN, EXIT_COMPROMISE, EXIT_FINDINGS, EXIT_UNSCANNED, Report


def _adv(vid: str, events: list[dict], severity: str = "HIGH", name: str = "demo", **extra: object) -> dict:
    aff = {"package": {"ecosystem": "PyPI", "name": name}, "ranges": [{"type": "ECOSYSTEM", "events": events}]}
    aff.update(extra)
    return {"id": vid, "database_specific": {"severity": severity} if severity else {}, "affected": [aff]}


class NameFeed:
    """Canned OSV: name-only querybatch answers and advisory records; records every query sent."""

    def __init__(self) -> None:
        self.ids: dict[str, list[str]] = {}
        self.records: dict[str, dict] = {}
        self.queries: list[dict] = []
        self.fail = False

    def __call__(self, url: str, payload: dict | None = None, **_: object) -> object:
        if self.fail:
            raise _http.FeedError("simulated outage")
        if url.endswith("/querybatch"):
            out = []
            for q in payload["queries"]:
                self.queries.append(q)
                ids = self.ids.get(q["package"]["name"], []) if "version" not in q else []
                out.append({"vulns": [{"id": i} for i in ids]} if ids else {})
            return {"results": out}
        if "/v1/vulns/" in url:
            return self.records[url.rsplit("/", 1)[1]]
        raise _http.FeedError(f"unexpected URL {url}")   # pypi.org included: never called

    def add(self, name: str, record: dict) -> None:
        self.ids.setdefault(name, []).append(record["id"])
        self.records[record["id"]] = record


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> NameFeed:
    f = NameFeed()
    monkeypatch.setattr(_http, "fetch_json", f)
    osv._DETAILS.clear()
    return f


def _brief(policy: dict) -> str:
    return json.dumps({"project_name": "app", "project_goal": "a goal for tests", "vulnerability_scanning": policy})


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    return repo


# ---------------------------------------------------------------- specifier table

@pytest.mark.parametrize(("spec", "inside", "outside"), [
    (">=1.0", ["1.0", "5.2"], ["0.9", "2.0rc1"]),
    ("<=1.4", ["0.1", "1.4", "1.4.0"], ["1.4.1"]),
    ("<2", ["1.9.9"], ["2", "2.0.1"]),
    (">1.0", ["1.0.1"], ["1.0"]),
    ("!=1.5", ["1.4", "1.6"], ["1.5", "1.5.0"]),
    ("~=1.4.5", ["1.4.5", "1.4.9"], ["1.5.0", "1.4.4"]),
    ("~=2.2", ["2.2", "2.9"], ["3.0", "2.1"]),
    ("==1.2", ["1.2", "1.2.0"], ["1.2.1"]),
    ("==1.5.*", ["1.5", "1.5.7"], ["1.6", "1.4.9"]),
    ("!=1.5.*", ["1.4", "1.6"], ["1.5.3"]),
    (">=1.0,<2,!=1.3", ["1.2", "1.9"], ["1.3", "2.0", "0.9"]),
    ("(>=1.0, <2)", ["1.5"], ["2.1"]),
    ("", ["0.0.1", "99"], ["1.0a1"]),
    (">=2.0b1", ["2.0b2", "2.0"], ["1.9"]),
])
def test_specifier_table(spec: str, inside: list[str], outside: list[str]) -> None:
    s = sp.parse_specifier(spec)
    assert all(s.contains(v) for v in inside), spec
    assert not any(s.contains(v) for v in outside), spec


@pytest.mark.parametrize("bad", ["===1.0", "~=1", ">=1.*", "<<1", ">=abc"])
def test_unreadable_specifiers_raise(bad: str) -> None:
    with pytest.raises(ValueError):
        sp.parse_specifier(bad)


@pytest.mark.parametrize(("line", "want"), [
    ("requests>=2.0 ; python_version < '3.8'", "marker"),
    ("-e .", "unchecked"),
    ("git+https://example.invalid/x.git", "unchecked"),
    ("pkg @ https://example.invalid/pkg.whl", "unchecked"),
    ("pkg===1.0", "unchecked"),
    ("pkg>=nope", "unreadable"),
    ("--index-url https://example.invalid/simple", None),
    ("pkg[extra]>=1 --hash=sha256:00", "plain"),
])
def test_parse_requirement_kinds(line: str, want: str | None) -> None:
    got = sp.parse_requirement(line)
    if want in ("marker", "plain"):
        assert isinstance(got, sp.Requirement) and got.marker is (want == "marker")
    else:
        assert got == want


# ---------------------------------------------------------------- interval walker

def test_walker_introduced_fixed() -> None:
    ivs = osv.affected_intervals(_adv("A", [{"introduced": "0"}, {"fixed": "1.2"}]), "PyPI", "Demo")
    assert ivs == [sp.Interval(None, True, "1.2", False, True)]


def test_walker_last_affected_is_inclusive_and_not_a_fix() -> None:
    ivs = osv.affected_intervals(_adv("A", [{"introduced": "1.0"}, {"last_affected": "1.4"}]), "PyPI", "demo")
    assert ivs == [sp.Interval("1.0", True, "1.4", True, False)]


def test_walker_limit_closes_without_a_fix() -> None:
    ivs = osv.affected_intervals(_adv("A", [{"introduced": "1.0"}, {"limit": "3.0"}]), "PyPI", "demo")
    assert ivs == [sp.Interval("1.0", True, "3.0", False, False)]


def test_walker_open_ended_introduced() -> None:
    ivs = osv.affected_intervals(_adv("A", [{"introduced": "2.0"}]), "PyPI", "demo")
    assert ivs == [sp.Interval("2.0", True, None, False)]


def test_walker_versions_list() -> None:
    rec = {"id": "A", "affected": [{"package": {"ecosystem": "PyPI", "name": "demo"}, "versions": ["1.1", "1.3"]}]}
    assert osv.affected_intervals(rec, "PyPI", "demo") == [sp.Interval("1.1", True, "1.1", True),
                                                          sp.Interval("1.3", True, "1.3", True)]


@pytest.mark.parametrize("record", [
    _adv("A", [{"introduced": "0"}, {"withdrawn": "1.0"}]),            # unknown event
    _adv("A", [{"introduced": "0", "fixed": "1.0"}]),                  # two kinds in one event
    _adv("A", [{"introduced": 0}]),                                    # not a string
    {"id": "A", "affected": [{"package": {"ecosystem": "PyPI", "name": "demo"},
                              "ranges": [{"type": "GIT", "repo": "x", "events": [{"introduced": "0"}]}]}]},
    {"id": "A", "affected": "nope"},
    {"id": "A", "affected": [{"package": {"ecosystem": "PyPI", "name": "other"}}]},   # nothing for this package
])
def test_walker_not_judged(record: dict) -> None:
    assert osv.affected_intervals(record, "PyPI", "demo") is None


def test_walker_skips_git_next_to_an_ecosystem_range() -> None:
    rec = _adv("A", [{"introduced": "0"}, {"fixed": "1.2"}])
    rec["affected"][0]["ranges"].append({"type": "GIT", "repo": "x", "events": [{"introduced": "abc"}]})
    assert osv.affected_intervals(rec, "PyPI", "demo") == [sp.Interval(None, True, "1.2", False, True)]


# ---------------------------------------------------------------- judgement

def test_judge_whole_partial_none_prerelease_and_holes() -> None:
    affected = [sp.Interval(None, True, "1.4.2", False, True)]
    assert sp.judge(sp.parse_specifier(">=1.0,<1.4"), affected).verdict == "whole"
    part = sp.judge(sp.parse_specifier(">=1.0"), affected)
    assert (part.verdict, part.raise_to, [i.text() for i in part.overlap]) == ("partial", "1.4.2", [">=1.0, <1.4.2"])
    assert sp.judge(sp.parse_specifier(">=1.4.2"), affected).verdict == "none"
    pre_only = [sp.Interval("2.0a1", True, "2.0", False, True)]
    assert sp.judge(sp.parse_specifier(">=1.0,<2.0"), pre_only).verdict == "none"   # only pre-releases affected
    point = [sp.Interval("1.5", True, "1.5", True)]
    assert sp.judge(sp.parse_specifier(">=1.0,!=1.5"), point).verdict == "none"   # the != hole
    assert sp.judge(sp.parse_specifier(">=1.0"), [sp.Interval("1.0", True, "1.4", True)]).raise_to == ""


# ---------------------------------------------------------------- engine

def test_default_off_queries_nothing(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0\n"})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"fixed": "1.4.2"}]))
    rep = engine.scan_path(repo)
    assert feed.queries == [] and rep.findings == []
    assert rep.unscannable == [("requirements.txt", "no exact `==` pins")]


def test_whole_range_finding_exit_and_report(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0,<1.4\nother~=2.2\n"})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"fixed": "1.4.2"}]))
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert all("version" not in q for q in feed.queries)                      # name only, never ""
    assert {q["package"]["name"] for q in feed.queries} == {"demo", "other"}
    [f] = rep.findings
    assert (f.kind, f.version, f.spec, f.severity, f.partial, f.malicious) == ("range", "", ">=1.0,<1.4", 3, False, False)
    assert "every version `>=1.0,<1.4` allows is affected" in f.detail
    assert rep.exit_code("high") == EXIT_FINDINGS                              # whole HIGH fails the default
    [(path, reason)] = rep.unscannable
    assert path == "requirements.txt" and reason.startswith("ranges checked against advisories: 2 direct")
    md = rep.markdown()
    assert "| requirement range | demo `>=1.0,<1.4` (PyPI) |  |" in md               # Installed blank


def test_partial_range_capped_moderate_with_raise(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements-dev.txt": "demo>=1.0 ; python_version >= '3.9'\n"})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"fixed": "1.4.2"}], severity="CRITICAL"))
    rep = engine.scan_path(repo, unpinned_ranges=True)
    [f] = rep.findings
    assert (f.severity, f.partial, f.fix, f.scope) == (2, True, "1.4.2", "dev")
    assert "allows vulnerable versions `>=1.0, <1.4.2`; the installed version is unknown" in f.detail
    assert "environment marker ignored" in f.detail
    assert rep.exit_code("high", include_dev=True) == EXIT_CLEAN                # MODERATE stays below high
    md = rep.markdown()
    assert "## Requirement ranges — plan-only (no staged edit)" in md
    assert "`demo>=1.0` → `demo>=1.4.2`" in md


def test_no_overlap_no_finding(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.5\n"})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"fixed": "1.4.2"}]))
    assert engine.scan_path(repo, unpinned_ranges=True).findings == []


def test_unscored_extension_then_partial_cap(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0,<1.4\nlib>=1.0\n"})
    feed.add("demo", _adv("PYSEC-1", [{"introduced": "0"}, {"fixed": "1.4.2"}], severity=""))
    feed.add("lib", _adv("PYSEC-2", [{"introduced": "0"}, {"fixed": "1.4.2"}], severity="", name="lib"))
    rep = engine.scan_path(repo, unpinned_ranges=True)
    rep.apply_unscored("critical")
    by = {f.name: f for f in rep.findings}
    assert by["demo"].severity == 4                     # whole: unscored counts as critical
    assert by["lib"].severity == 2                      # partial: capped after unscored_as
    rep.apply_unscored("low")
    assert (by["demo"].severity, by["lib"].severity) == (1, 1)


def test_malicious_overlap_is_critical_not_compromise(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0\n"})
    feed.add("demo", _adv("MAL-2026-1", [{"introduced": "1.3"}, {"last_affected": "1.3"}], severity=""))
    rep = engine.scan_path(repo, unpinned_ranges=True)
    rep.apply_unscored("low")
    [f] = rep.findings
    assert (f.severity, f.malicious) == (4, False)
    assert f.detail.startswith("range admits known-malicious versions; check what is installed")
    assert rep.exit_code("high") == EXIT_FINDINGS != EXIT_COMPROMISE
    assert "COMPROMISE INDICATORS" not in rep.markdown()


def test_osv_failure_is_a_feed_error(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0\n"})
    feed.fail = True
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert rep.findings == [] and rep.exit_code() == EXIT_UNSCANNED
    assert any("OSV range query" in e for e in rep.feed_errors)
    assert "ranges not checked: the OSV query failed" in rep.unscannable[0][1]


def test_not_judged_advisory_is_a_coverage_note(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0\n"})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"withdrawn": "2"}]))
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert rep.findings == [] and "1 advisory(ies) not judged" in rep.unscannable[0][1]


def test_caps(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "a>=1\nb>=1\nc>=1\n"})
    for n in "abc":
        feed.add(n, _adv(f"GHSA-{n}", [{"introduced": "0"}, {"fixed": "2"}], name=n))
        feed.add(n, _adv(f"GHSA-{n}2", [{"introduced": "0"}, {"fixed": "3"}], name=n))
    m = engine.parse(repo / "requirements.txt")
    items = [engine.RangeItem(".", "requirements.txt", r)
             for r in sp.read_requirements(repo / "requirements.txt", repo).reqs]
    assert m.unpinned == 3
    rep = Report(target="x")
    notes = engine.analyze_ranges(items, rep, query_limit=2, detail_limit=3)
    assert len({q["package"]["name"] for q in feed.queries}) == 2
    assert any("over the query cap" in n for n in notes["requirements.txt"])
    assert any("over the detail cap" in n for n in notes["requirements.txt"])
    assert rep.feed_errors == []


def test_private_index_withholds_file_and_includes(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {
        "requirements.txt": "-r requirements-base.in\ndemo>=1.0\n",
        "requirements-base.in": "--extra-index-url https://pkgs.example.invalid/simple\nlib>=1\n",
        "svc/requirements.txt": "-i https://pkgs.example.invalid/simple\nsecret>=1\n",
        "web/pyproject.toml": '[project]\ndependencies = ["inhouse>=1"]\n[[tool.uv.index]]\nname = "corp"\n',
        "api/pyproject.toml": '[project]\ndependencies = ["x>=1"]\n[tool.uv.sources]\nx = { index = "corp" }\n',
        "pdm/pyproject.toml": '[project]\ndependencies = ["y>=1"]\n[[tool.pdm.source]]\nname = "corp"\n',
        "poetry/pyproject.toml": '[project]\ndependencies = ["z>=1"]\n[[tool.poetry.source]]\nname = "corp"\n',
    })
    rep = engine.scan_path(repo, unpinned_ranges=True)
    sent = {q["package"]["name"] for q in feed.queries}
    assert not sent & {"demo", "lib", "secret", "inhouse", "x", "y", "z"}
    for rel in ("requirements.txt", "requirements-base.in", "svc/requirements.txt", "web/pyproject.toml",
                "api/pyproject.toml", "pdm/pyproject.toml", "poetry/pyproject.toml"):
        assert f".: {rel} (private index)" in rep.withheld, rel


def test_private_packages_withheld(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "acme_core>=1\ndemo>=1\n",
                            ".agentteams/brief.json": _brief({"level": "scan", "private_packages": ["acme-*"]})})
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert {q["package"]["name"] for q in feed.queries} == {"demo"}
    assert ".: acme_core (PyPI)" in rep.withheld


def test_level_off_parity_with_pinned(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1.0\n", ".agentteams/brief.json": _brief({"level": "off"})})
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert rep.brief_off == ["."] and {q["package"]["name"] for q in feed.queries} == {"demo"}


def test_pyproject_alone_and_dev_group(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"pyproject.toml": '[project]\ndependencies = ["demo>=1.0,<1.4"]\n'
                                              '[project.optional-dependencies]\ntest = ["lib<1"]\n'})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"fixed": "1.4.2"}]))
    feed.add("lib", _adv("GHSA-2", [{"introduced": "0"}], name="lib"))
    rep = engine.scan_path(repo, unpinned_ranges=True)
    by = {f.name: f for f in rep.findings}
    assert by["demo"].scope == "runtime" and by["lib"].scope == "dev" and not by["lib"].partial
    assert rep.unscannable[0][1].startswith("ranges checked against advisories: 2 direct")


def test_ranges_only_file_gets_no_apply_requirements_edit(tmp_path: Path, feed: NameFeed,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """A routed repository (pinned findings elsewhere) still never has its range file edited."""
    repo = _repo(tmp_path, {"requirements.txt": "pinned==1.0\n", "requirements-ranges.txt": "demo>=1.0\n"})
    feed.add("demo", _adv("GHSA-1", [{"introduced": "0"}, {"fixed": "1.4.2"}]))
    edited: list[Path] = []
    real = fix.apply_requirements
    monkeypatch.setattr(fix, "apply_requirements", lambda p, c: (edited.append(p), real(p, c))[1])
    monkeypatch.setattr(fix.osv, "vulnerable_ids", lambda items: {("PyPI", "pinned", "1.0"): ["GHSA-P"]}
                        if ("PyPI", "pinned", "1.0") in set(items) else {})
    monkeypatch.setattr(fix, "choose_version", lambda name, cur, **_: ("1.1", ""))
    monkeypatch.setattr(fix, "git_config_problem", lambda d: "")
    monkeypatch.setattr(fix, "_add_resolved", lambda *a, **k: None)
    before = (repo / "requirements-ranges.txt").read_bytes()
    res = fix.fix_path(repo, apply=True, allow_dirty=True)
    assert (repo / "requirements-ranges.txt").read_bytes() == before
    assert [c.path for c in res.changes] == ["requirements.txt"]
    assert [p.name for p in edited] == ["requirements.txt"]


def test_effectiveness_excludes_range_findings() -> None:
    from agentteams.vulnscan.report import Finding

    prev = Report(target="daily", findings=[Finding(kind="range", target="r", name="demo", ids=["A"])])
    now = Report(target="daily", findings=[Finding(kind="range", target="r", name="lib", ids=["B"])])
    md, _ = effectiveness.section(now, prev, stats=effectiveness.PrStats())
    assert "| Findings fixed since the previous run | 0 |" in md
    assert "| Findings new since the previous run | 0 |" in md
    assert "| Requirement-range findings (not counted above: no installed version is known) | 1 |" in md


# ---------------------------------------------------------------- hostile files (code-hygiene P3)

def test_include_leaving_the_repository_is_refused_and_never_read(tmp_path: Path, feed: NameFeed,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("leaked>=1\n")
    repo = _repo(tmp_path, {"requirements.txt": "-r ../outside.txt\ndemo>=1\n",
                            "c/requirements.txt": f"-c {outside}\nother>=1\n"})
    read: list[str] = []
    real = Path.read_text

    def spy(self: Path, *a: object, **kw: object) -> str:
        read.append(str(self.resolve()))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    rf = sp.read_requirements(repo / "requirements.txt", repo)
    assert rf.private_index and rf.reqs == []
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert str(outside.resolve()) not in read
    assert not {q["package"]["name"] for q in feed.queries} & {"leaked", "demo", "other"}
    assert ".: requirements.txt (private index)" in rep.withheld
    assert ".: c/requirements.txt (private index)" in rep.withheld


def test_include_loop_terminates(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"requirements.txt": "-r requirements-b.txt\ndemo>=1\n",
                            "requirements-b.txt": "-r requirements.txt\n-c requirements-b.txt\nlib>=1\n"})
    rf = sp.read_requirements(repo / "requirements.txt", repo)
    assert not rf.private_index and [r.name for r in rf.reqs] == ["demo"]
    engine.scan_path(repo, unpinned_ranges=True)   # returns: no recursion error, no hang
    assert {q["package"]["name"] for q in feed.queries} == {"demo", "lib"}


def test_include_depth_cap_counts_as_naming_an_index(tmp_path: Path) -> None:
    depth = sp.MAX_INCLUDE_DEPTH
    files = {f"r{i}.txt": f"-r r{i + 1}.txt\n" for i in range(depth + 1)}
    files[f"r{depth + 1}.txt"] = "lib>=1\n"
    files["requirements.txt"] = "-r r0.txt\ndemo>=1\n"
    repo = _repo(tmp_path, files)
    assert sp.read_requirements(repo / "requirements.txt", repo).private_index   # too deep: cannot be checked
    (repo / f"r{depth - 1}.txt").write_text("lib>=1\n")                            # within the cap: followed
    rf = sp.read_requirements(repo / "requirements.txt", repo)
    assert not rf.private_index and [r.name for r in rf.reqs] == ["demo"]


@pytest.mark.parametrize("text", ['[project\ndependencies = ["demo>=1"]\n', '[project]\ndependencies = "demo>=1"\n',
                                  '[project]\ndependencies = 7\n[project.optional-dependencies]\ntest = 3\n'])
def test_malformed_pyproject_is_unscannable_never_raised(tmp_path: Path, feed: NameFeed, text: str) -> None:
    repo = _repo(tmp_path, {"pyproject.toml": text})
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert [p for p, _r in rep.unscannable] == ["pyproject.toml"]
    if text.startswith("[project\n"):
        with pytest.raises(sp.tomllib.TOMLDecodeError):
            sp.read_pyproject(repo / "pyproject.toml")


def test_huge_requirements_file_is_not_read(tmp_path: Path, feed: NameFeed, monkeypatch: pytest.MonkeyPatch) -> None:
    assert sp.MAX_BYTES == 64 * 1024 * 1024
    repo = _repo(tmp_path, {"requirements.txt": "demo>=1\n" * 64, "requirements-inc.txt": "-r big.txt\nlib>=1\n",
                            "big.txt": "x>=1\n" * 64})
    monkeypatch.setattr(sp, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="larger than 100 bytes"):
        sp.read_requirements(repo / "requirements.txt", repo)
    assert sp.read_requirements(repo / "requirements-inc.txt", repo).private_index   # a huge include: withheld
    rep = engine.scan_path(repo, unpinned_ranges=True)
    reasons = dict(rep.unscannable)
    assert reasons["requirements.txt"].endswith("ranges not read: ValueError")
    assert not {q["package"]["name"] for q in feed.queries} & {"demo", "x"}


# ---------------------------------------------------------------- repository-wide index (security review P-s1)

def test_a_parent_naming_the_index_withholds_its_base_file(tmp_path: Path, feed: NameFeed) -> None:
    """requirements.txt names the private index and includes requirements-base.txt, which names none itself."""
    repo = _repo(tmp_path, {"requirements.txt": "--index-url https://pkgs.example.invalid/simple\n"
                                                "-r requirements-base.txt\n",
                            "requirements-base.txt": "inhouse-lib>=1\n",
                            "tools/requirements-dev.txt": "devtool>=2\n",
                            "pinned/requirements.txt": "-i https://pkgs.example.invalid/simple\nflask==2.0.0\n"})
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert not {q["package"]["name"] for q in feed.queries} & {"inhouse-lib", "devtool"}
    for rel in ("requirements-base.txt", "tools/requirements-dev.txt"):   # the parent itself lists no names
        assert f".: {rel} (private index)" in rep.withheld, rel
    reasons = dict(rep.unscannable)
    assert reasons["requirements-base.txt"].endswith(engine.REPO_INDEX_NOTE)
    assert reasons["tools/requirements-dev.txt"].endswith(engine.REPO_INDEX_NOTE)


def test_a_pyproject_private_source_withholds_the_repositorys_requirement_ranges(tmp_path: Path,
                                                                                 feed: NameFeed) -> None:
    root = tmp_path / "ws"
    repo = _repo(root, {"pyproject.toml": '[project]\nname = "x"\n[[tool.poetry.source]]\nname = "corp"\n',
                        "requirements.txt": "inhouse>=1\n"})
    other = root / "other"
    (other / ".git").mkdir(parents=True)
    (other / "requirements.txt").write_text("demo>=1\n")
    rep = engine.scan_path(root, unpinned_ranges=True)
    sent = {q["package"]["name"] for q in feed.queries}
    assert "inhouse" not in sent and "demo" in sent            # another repository is unaffected
    assert "app: requirements.txt (private index)" in rep.withheld and repo.is_dir()


def test_an_unparsable_pyproject_counts_as_naming_an_index(tmp_path: Path, feed: NameFeed) -> None:
    repo = _repo(tmp_path, {"pyproject.toml": "[tool\n", "requirements.txt": "demo>=1\n"})
    rep = engine.scan_path(repo, unpinned_ranges=True)
    assert feed.queries == [] and ".: requirements.txt (private index)" in rep.withheld
