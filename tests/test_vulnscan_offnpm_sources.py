"""vfr-e.E3: npm entries resolved from an allowlisted non-npm source are graded on their advisories' ranges.

No network: OSV is a canned feed. The xlsx records are shaped like GitHub's OSV export for npm ``xlsx``: an
``introduced: 0`` range with no fix event, and a ``last_known_affected_version_range`` under ``database_specific``.
The advisory ids are placeholders, not the real GHSA ids.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine, offnpm_sources, osv
from agentteams.vulnscan.report import EXIT_CLEAN, EXIT_FINDINGS, EXIT_UNSCANNED, Report

CDN = "https://cdn.sheetjs.com/xlsx-{v}/xlsx-{v}.tgz"


def _adv(vid: str, events: list[dict], last_known: str | None = None, severity: str = "HIGH",
         name: str = "xlsx") -> dict:
    aff: dict = {"package": {"ecosystem": "npm", "name": name}, "ranges": [{"type": "ECOSYSTEM", "events": events}]}
    if last_known is not None:
        aff["database_specific"] = {"last_known_affected_version_range": last_known}
    return {"id": vid, "database_specific": {"severity": severity}, "affected": [aff]}


#: Shaped like the two npm xlsx GHSA records: no npm fix, known affected below 0.19.3 and 0.20.2.
PROTO = _adv("GHSA-test-proto-0001", [{"introduced": "0"}], "< 0.19.3")
REDOS = _adv("GHSA-test-redo-0002", [{"introduced": "0"}], "< 0.20.2")


class Feed:
    """Canned OSV: answers name-only and exact-version querybatch queries; records every query."""

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
                ids = self.ids.get(q["package"]["name"], [])
                out.append({"vulns": [{"id": i} for i in ids]} if ids else {})
            return {"results": out}
        if "/v1/vulns/" in url:
            vid = url.rsplit("/", 1)[1]
            if vid not in self.records:
                raise _http.FeedError(f"no record {vid}")
            return self.records[vid]
        raise _http.FeedError(f"unexpected URL {url}")

    def add(self, name: str, record: dict) -> None:
        self.ids.setdefault(name, []).append(record["id"])
        self.records[record["id"]] = record


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    f = Feed()
    monkeypatch.setattr(_http, "fetch_json", f)
    osv._DETAILS.clear()
    return f


def _repo(tmp_path: Path, xlsx: dict) -> Path:
    """A repository with a v3 lockfile holding ``xlsx`` as given (shaped like portal's)."""
    repo = tmp_path / "portal"
    (repo / ".git").mkdir(parents=True)
    lock = {"lockfileVersion": 3, "packages": {"": {"dependencies": {"xlsx": xlsx.get("resolved", "^0.18.5")}},
                                               "node_modules/xlsx": xlsx}}
    (repo / "package-lock.json").write_text(json.dumps(lock))
    return repo


def _cdn(v: str) -> dict:
    return {"version": v, "resolved": CDN.format(v=v), "integrity": "sha512-" + "A" * 86 + "=="}


# ---------------------------------------------------------------- the allowlist match


@pytest.mark.parametrize("url", [
    CDN.format(v="0.20.3"),
    "https://CDN.SheetJS.com/xlsx-0.20.3/xlsx-0.20.3.tgz",
])
def test_the_sheetjs_cdn_tarball_matches(url: str) -> None:
    assert offnpm_sources.match("xlsx", "0.20.3", url) is offnpm_sources.SOURCES[0]


@pytest.mark.parametrize(("name", "version", "url"), [
    ("xlsx", "0.20.3", "http://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz"),          # not https
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com.evil.example/xlsx-0.20.3/xlsx-0.20.3.tgz"),
    ("xlsx", "0.20.3", "https://evil.example/xlsx-0.20.3/xlsx-0.20.3.tgz"),             # no general CDN trust
    ("xlsx", "0.20.3", "https://user@cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz"),
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com:8443/xlsx-0.20.3/xlsx-0.20.3.tgz"),
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz?x=1"),
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz#f"),
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com/other/xlsx-0.20.3.tgz"),
    ("xlsx", "0.20.2", "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz"),          # lockfile version differs
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.2.tgz"),          # path versions differ
    ("other", "0.20.3", "https://cdn.sheetjs.com/other-0.20.3/other-0.20.3.tgz"),     # not a package of the row
    ("xlsx", "0.20.3", "https://cdn.sheetjs.com:bad/xlsx-0.20.3/xlsx-0.20.3.tgz"),
    ("xlsx", "0.20.3", ""),
])
def test_anything_else_is_not_an_allowlisted_source(name: str, version: str, url: str) -> None:
    assert offnpm_sources.match(name, version, url) is None


# ---------------------------------------------------------------- one advisory


@pytest.mark.parametrize(("record", "version", "expected"), [
    (PROTO, "0.18.5", ("affected", "")),        # inside what is known affected
    (PROTO, "0.19.3", ("unknown", "names no fixed version; last known affected < 0.19.3, and 0.19.3 is above it")),
    (REDOS, "0.20.3", ("unknown", "names no fixed version; last known affected < 0.20.2, and 0.20.3 is above it")),
    (_adv("A", [{"introduced": "0"}]), "0.20.3", ("affected", "")),   # open-ended, nothing bounds it
    (_adv("A", [{"introduced": "0"}], "<= 0.20.3"), "0.20.3", ("affected", "")),
    (_adv("A", [{"introduced": "0"}], ">= 0.1, < 0.19.3"), "0.20.3", ("affected", "")),   # bound not read
    (_adv("A", [{"introduced": "0"}, {"fixed": "0.19.3"}]), "0.18.5", ("affected", "0.19.3")),
    (_adv("A", [{"introduced": "0"}, {"fixed": "0.19.3"}]), "0.20.3", ("not affected", "")),
    (_adv("A", [{"introduced": "0"}, {"last_affected": "0.18.5"}]), "0.18.5", ("affected", "")),
    (_adv("A", [{"introduced": "0"}, {"fixed": "0.19.3"}], name="other"), "0.18.5",   # names no xlsx entry
     ("unknown", "not judged (unknown range event, GIT-only or malformed range)")),
    (_adv("A", [{"introduced": "0"}, {"bogus": "1"}]), "0.18.5",
     ("unknown", "not judged (unknown range event, GIT-only or malformed range)")),
])
def test_judge(record: dict, version: str, expected: tuple[str, str]) -> None:
    assert offnpm_sources.judge(record, "xlsx", version) == expected


def test_affected_in_one_entry_wins_over_unknown_in_another() -> None:
    rec = _adv("A", [{"introduced": "0"}], "< 0.19.3")
    rec["affected"].append({"package": {"ecosystem": "npm", "name": "xlsx"},
                            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0.20.0"},
                                                                        {"fixed": "0.20.4"}]}]})
    assert offnpm_sources.judge(rec, "xlsx", "0.20.3") == ("affected", "0.20.4")


# ---------------------------------------------------------------- the engine


def test_portal_cdn_xlsx_reads_unknown_never_clean(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", PROTO)
    feed.add("xlsx", REDOS)
    rep = engine.scan_path(_repo(tmp_path, _cdn("0.20.3")))
    assert [q for q in feed.queries if q["package"]["name"] == "xlsx"] == [
        {"package": {"ecosystem": "npm", "name": "xlsx"}}]   # by name: graded locally, not by exact version
    (f,) = rep.findings
    assert (f.name, f.version, f.ecosystem, f.severity, f.fix, f.unfixed) == ("xlsx", "0.20.3", "npm", 3, "", 2)
    assert f.ids == ["GHSA-test-proto-0001", "GHSA-test-redo-0002"]
    assert "resolved from cdn.sheetjs.com (allowlisted non-npm source, SheetJS)" in f.detail
    assert "graded on the npm advisories' ranges: unknown" in f.detail
    assert "last known affected < 0.20.2" in f.detail
    assert rep.sources == [".: xlsx 0.20.3 from cdn.sheetjs.com: unknown "
                           "(0 affected, 2 unknown, of 2 advisory(ies) judged on their ranges)"]
    assert rep.exit_code() == EXIT_FINDINGS
    md = rep.markdown()
    assert "## Allowlisted non-npm sources (graded on advisory ranges)" in md
    assert "xlsx 0.20.3 from cdn.sheetjs.com: unknown" in md


def test_an_old_cdn_release_inside_the_known_range_is_vulnerable(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", PROTO)
    feed.add("xlsx", REDOS)
    rep = engine.scan_path(_repo(tmp_path, _cdn("0.19.3")))
    (f,) = rep.findings
    assert "graded on the npm advisories' ranges: vulnerable" in f.detail
    assert "unknown: GHSA-test-proto-0001" in f.detail   # the other advisory's reason stays visible
    assert rep.sources[0].endswith("vulnerable (1 affected, 1 unknown, of 2 advisory(ies) judged on their ranges)")


def test_a_cdn_release_past_every_closed_range_is_fixed_and_listed(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", _adv("GHSA-test-fixd-0003", [{"introduced": "0"}, {"fixed": "0.19.3"}]))
    rep = engine.scan_path(_repo(tmp_path, _cdn("0.20.3")))
    assert rep.findings == []
    assert rep.sources == [".: xlsx 0.20.3 from cdn.sheetjs.com: fixed "
                           "(0 affected, 0 unknown, of 1 advisory(ies) judged on their ranges)"]
    assert rep.exit_code() == EXIT_CLEAN


def test_a_closed_range_gives_the_fix(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", _adv("GHSA-test-fixd-0003", [{"introduced": "0"}, {"fixed": "0.19.3"}], severity="MODERATE"))
    (f,) = engine.scan_path(_repo(tmp_path, _cdn("0.18.5"))).findings
    assert (f.fix, f.unfixed, f.severity) == ("0.19.3", 0, 2)


def test_an_osv_outage_is_unscanned_never_clean(tmp_path: Path, feed: Feed) -> None:
    feed.fail = True
    rep = engine.scan_path(_repo(tmp_path, _cdn("0.20.3")))
    assert rep.exit_code() == EXIT_UNSCANNED
    assert rep.sources == [".: xlsx 0.20.3 from cdn.sheetjs.com: unknown (the OSV query failed)"]


def test_an_unavailable_record_counts_unknown(tmp_path: Path, feed: Feed) -> None:
    feed.ids["xlsx"] = ["GHSA-test-gone-0004"]   # listed, but its record cannot be fetched
    rep = engine.scan_path(_repo(tmp_path, _cdn("0.20.3")))
    (f,) = rep.findings
    assert "advisory record unavailable" in f.detail
    assert (f.severity, f.unscored) == (engine.UNSCORED_DEFAULT, 1)
    assert rep.exit_code() == EXIT_UNSCANNED


def test_a_registry_entry_is_still_queried_by_exact_version(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", PROTO)
    rep = engine.scan_path(_repo(tmp_path, {"version": "0.18.5",
                                            "resolved": "https://registry.npmjs.org/xlsx/-/xlsx-0.18.5.tgz"}))
    assert {"package": {"ecosystem": "npm", "name": "xlsx"}, "version": "0.18.5"} in feed.queries
    assert rep.sources == []
    (f,) = rep.findings
    assert "allowlisted" not in f.detail


def test_a_non_allowlisted_off_registry_entry_is_judged_as_before(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", PROTO)
    rep = engine.scan_path(_repo(tmp_path, {"version": "0.20.3",
                                            "resolved": "https://evil.example/xlsx-0.20.3/xlsx-0.20.3.tgz"}))
    assert {"package": {"ecosystem": "npm", "name": "xlsx"}, "version": "0.20.3"} in feed.queries
    assert rep.sources == []
    assert len(rep.findings) == 1


def test_a_lockfile_v1_entry_carries_its_resolved_url(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", REDOS)
    repo = tmp_path / "portal"
    (repo / ".git").mkdir(parents=True)
    (repo / "package-lock.json").write_text(json.dumps(
        {"lockfileVersion": 1, "dependencies": {"xlsx": _cdn("0.20.3")}}))
    rep = engine.scan_path(repo)
    assert rep.sources and "unknown" in rep.sources[0]


def test_sources_survive_a_report_round_trip_and_merge() -> None:
    rep = Report(target="t", sources=["a: xlsx 0.20.3 from cdn.sheetjs.com: fixed (no advisory names the package)"])
    again = Report.from_dict(json.loads(json.dumps(rep.to_dict())))
    assert again.sources == rep.sources
    total = Report(target="all")
    total.merge(again)
    assert total.sources == rep.sources


# ---------------------------------------------------------------- @security: unreadable ranges are never clean

UNREADABLE = ("unknown", "not judged (a range bound is not a version, or a range starts above its end)")


@pytest.mark.parametrize("events", [
    [{"introduced": "0"}, {"fixed": "next"}],          # a fix bound that is not a version
    [{"introduced": "latest"}, {"fixed": "0.21.0"}],   # a start that is not a version
    [{"introduced": "2.0"}, {"fixed": "1.0"}],         # inverted: starts above its end
    [{"introduced": "0.20.0"}, {"last_affected": "0.19.0"}],
])
def test_an_unreadable_range_is_unknown_never_not_affected(events: list[dict]) -> None:
    assert offnpm_sources.judge(_adv("A", events), "xlsx", "0.20.3") == UNREADABLE


def test_an_unreadable_range_beside_a_valid_one_in_the_same_advisory_is_unknown() -> None:
    rec = _adv("A", [{"introduced": "0"}, {"fixed": "0.19.3"}])
    rec["affected"].append({"package": {"ecosystem": "npm", "name": "xlsx"},
                            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "next"}]}]})
    assert offnpm_sources.judge(rec, "xlsx", "0.20.3") == UNREADABLE


def test_an_unreadable_range_does_not_hide_an_affected_one() -> None:
    rec = _adv("A", [{"introduced": "0"}, {"fixed": "0.21.0"}])
    rec["affected"].append({"package": {"ecosystem": "npm", "name": "xlsx"},
                            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "2.0"}, {"fixed": "1.0"}]}]})
    assert offnpm_sources.judge(rec, "xlsx", "0.20.3") == ("affected", "0.21.0")


def test_a_malformed_advisory_beside_a_valid_fixed_one_grades_unknown(tmp_path: Path, feed: Feed) -> None:
    feed.add("xlsx", _adv("GHSA-test-fixd-0003", [{"introduced": "0"}, {"fixed": "0.19.3"}]))
    feed.add("xlsx", _adv("GHSA-test-next-0005", [{"introduced": "0"}, {"fixed": "next"}]))
    rep = engine.scan_path(_repo(tmp_path, _cdn("0.20.3")))
    (f,) = rep.findings
    assert f.ids == ["GHSA-test-next-0005"]
    assert "graded on the npm advisories' ranges: unknown" in f.detail
    assert rep.sources == [".: xlsx 0.20.3 from cdn.sheetjs.com: unknown "
                           "(0 affected, 1 unknown, of 2 advisory(ies) judged on their ranges)"]
    assert rep.exit_code() == EXIT_FINDINGS
