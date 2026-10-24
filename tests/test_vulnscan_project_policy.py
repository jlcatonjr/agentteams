"""The brief's ``vulnerability_scanning`` block: reading, privacy guard, writing, and the scanner honouring it.

No network: OSV and release lookups are replaced by a recorder that fails on anything unexpected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine, fix, osv
from agentteams.vulnscan import project_policy as pp
from agentteams.vulnscan.report import EXIT_CLEAN, EXIT_FINDINGS, Report

SCHEMA = Path(__file__).resolve().parents[1] / "agentteams" / "schemas" / "project-description.schema.json"


def _brief(repo: Path, data: dict, *, rich: bool = True) -> Path:
    path = repo / ".agentteams" / "brief.json" if rich else repo / "brief.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


class Recorder:
    """Stands in for every HTTP call; records each package name sent anywhere."""

    def __init__(self, vulns: dict | None = None, records: dict | None = None) -> None:
        self.sent: list[str] = []
        self.vulns = vulns or {}
        self.records = records or {}

    def __call__(self, url: str, payload: dict | None = None, **_: object) -> object:
        if url.endswith("/querybatch"):
            out = []
            for q in payload["queries"]:
                self.sent.append(q["package"]["name"])
                ids = self.vulns.get(q["package"]["name"], [])
                out.append({"vulns": [{"id": i} for i in ids]} if ids else {})
            return {"results": out}
        if "/v1/vulns/" in url:
            return self.records[url.rsplit("/", 1)[1]]
        raise _http.FeedError(f"unexpected URL {url}")


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(_http, "fetch_json", r)
    osv._DETAILS.clear()
    return r


# ------------------------------------------------------------------ reading

def test_no_brief_or_no_block_is_unanswered(tmp_path: Path) -> None:
    assert pp.read_policy(tmp_path) == pp.ProjectPolicy(level=None)
    b = _brief(tmp_path, {"project_goal": "a goal for tests"})
    pol = pp.read_policy(tmp_path)
    assert pol.level is None and pol.brief_path == b and pol.problem == ""
    assert "unanswered" in pp.unanswered_line(tmp_path, pol)


def test_full_block_reads(tmp_path: Path) -> None:
    _brief(tmp_path, {"project_goal": "a goal for tests", pp.POLICY_KEY: {
        "level": "remediate", "exclude_paths": ["vendor/"], "private_packages": ["@acme/*"],
        "include_dev": True, "delivery": "plan", "base_branch": "release/2.x"}})
    pol = pp.read_policy(tmp_path)
    assert (pol.level, pol.exclude_paths, pol.private_packages, pol.include_dev, pol.delivery, pol.base_branch) == (
        "remediate", ("vendor/",), ("@acme/*",), True, "plan", "release/2.x")
    assert pp.unanswered_line(tmp_path, pol) == ""


def test_brief_resolution_prefers_the_rich_brief(tmp_path: Path) -> None:
    _brief(tmp_path, {pp.POLICY_KEY: {"level": "off"}}, rich=False)
    _brief(tmp_path, {pp.POLICY_KEY: {"level": "scan"}})
    assert pp.read_policy(tmp_path).level == "scan"


@pytest.mark.parametrize("block", [
    "scan", {"level": "everything"}, {"exclude_paths": []}, {"level": "scan", "extra": 1},
    {"level": "scan", "exclude_paths": ["/etc"]}, {"level": "scan", "exclude_paths": ["../other"]},
    {"level": "scan", "exclude_paths": ["~/x"]}, {"level": "scan", "exclude_paths": ["https://example/x"]},
    {"level": "scan", "private_packages": ["10.1.2.3"]}, {"level": "scan", "private_packages": ["build.example.com"]},
    {"level": "scan", "private_packages": ["user@host"]}, {"level": "scan", "private_packages": ["localhost"]},
    {"level": "scan", "base_branch": "a..b"}, {"level": "scan", "include_dev": "yes"},
    {"level": "scan", "delivery": "push"}, {"level": "scan", "exclude_paths": "vendor"},
])
def test_invalid_block_is_unanswered_with_a_reason(tmp_path: Path, block: object) -> None:
    _brief(tmp_path, {pp.POLICY_KEY: block})
    pol = pp.read_policy(tmp_path)
    assert pol.level is None and pol.problem
    assert "/etc" not in pol.problem and "10.1.2.3" not in pol.problem   # never echoes the value


def test_unreadable_brief_never_raises(tmp_path: Path) -> None:
    (tmp_path / "brief.json").write_text("{not json", encoding="utf-8")
    pol = pp.read_policy(tmp_path)
    assert pol.level is None and "JSON" in pol.problem


def test_is_private_and_is_excluded() -> None:
    assert pp.is_private("@Acme/Widget", ["@acme/*"]) and pp.is_private("ACME-core", ["acme-*"])
    assert not pp.is_private("requests", ["acme-*"])
    assert pp.is_excluded("vendor/lib/requirements.txt", ["vendor/"])
    assert pp.is_excluded("examples/demo/package-lock.json", ["examples/*"])
    assert not pp.is_excluded("src/requirements.txt", ["vendor"])


# ------------------------------------------------------------------ schema

def test_schema_block_matches_the_module() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    blk = schema["properties"][pp.POLICY_KEY]
    props = blk["properties"]
    assert blk["additionalProperties"] is False and blk["required"] == ["level"]
    assert set(props) == set(pp.KEYS)
    assert props["level"]["enum"] == list(pp.LEVELS) and props["delivery"]["enum"] == list(pp.DELIVERIES)
    assert props["exclude_paths"]["items"]["pattern"] == pp.EXCLUDE_PATH_PATTERN
    assert props["private_packages"]["items"]["pattern"] == pp.PRIVATE_PACKAGE_PATTERN
    assert props["base_branch"]["pattern"] == pp.BASE_BRANCH_PATTERN
    assert "vulnerability_remediation" in schema["properties"]["capabilities"]["items"]["enum"]


def test_schema_validates_the_block() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    ok = {"project_goal": "a goal for tests", pp.POLICY_KEY: {"level": "scan", "private_packages": ["@acme/*"]}}
    jsonschema.validate(ok, schema)
    for bad in ({"level": "scan", "exclude_paths": ["/abs"]}, {"level": "scan", "host": "x"},
                {"level": "scan", "private_packages": ["192.168.0.1"]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"project_goal": "a goal for tests", pp.POLICY_KEY: bad}, schema)


# ------------------------------------------------------------------ writing

def test_set_level_creates_the_block_and_keeps_the_rest(tmp_path: Path) -> None:
    text = '{\n    "project_goal": "a goal for tests",\n    "tools": [1, 2],\n    "zeta": {"a": 1}\n}\n'
    b = tmp_path / "brief.json"
    b.write_text(text, encoding="utf-8")
    seen: list[str] = []
    assert pp.set_level(tmp_path, "scan", confirm=lambda d: seen.append(d) or True)
    new = b.read_text(encoding="utf-8")
    assert new.startswith(text[:-3])                       # every earlier line untouched, order kept
    assert '"tools": [1, 2]' in new and json.loads(new)[pp.POLICY_KEY] == {"level": "scan"}
    assert '\n    "vulnerability_scanning": {\n        "level": "scan"\n    }\n}' in new
    assert seen and seen[0].startswith("--- a/brief.json") and '+    "vulnerability_scanning"' in seen[0]


def test_set_level_updates_an_existing_block(tmp_path: Path) -> None:
    b = _brief(tmp_path, {"project_goal": "a goal for tests",
                          pp.POLICY_KEY: {"level": "scan", "private_packages": ["acme-*"]}, "after": True})
    assert pp.set_level(tmp_path, "remediate", confirm=lambda d: True)
    data = json.loads(b.read_text(encoding="utf-8"))
    assert data[pp.POLICY_KEY] == {"level": "remediate", "private_packages": ["acme-*"]}
    assert list(data) == ["project_goal", pp.POLICY_KEY, "after"]


def test_set_level_needs_confirmation_and_a_brief(tmp_path: Path) -> None:
    said: list[str] = []
    assert not pp.set_level(tmp_path, "scan", confirm=lambda d: True, say=said.append)
    assert "no brief" in said[-1] and not any(tmp_path.iterdir())
    b = _brief(tmp_path, {"project_goal": "a goal for tests"})
    before = b.read_text(encoding="utf-8")
    assert not pp.set_level(tmp_path, "scan", confirm=lambda d: False, say=said.append)
    assert b.read_text(encoding="utf-8") == before
    assert not pp.set_level(tmp_path, "everything", confirm=lambda d: True, say=said.append)
    assert pp.set_level(tmp_path, "off", confirm=lambda d: True)
    assert not pp.set_level(tmp_path, "off", confirm=lambda d: True, say=said.append)
    assert "already" in said[-1]


def test_cli_set_vulnscan_needs_a_terminal_or_the_reviewed_sha(tmp_path: Path, monkeypatch, capsys) -> None:
    from agentteams.cli.app import main

    b = _brief(tmp_path, {"project_goal": "a goal for tests"})
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["--set-vulnscan", "scan", "--project", str(tmp_path), "--yes"]) == 1   # --yes is not an answer
    assert pp.POLICY_KEY not in json.loads(b.read_text(encoding="utf-8"))
    out = capsys.readouterr()
    sha = out.out.split("diff sha256: ")[1].split()[0]
    assert main(["--set-vulnscan", "scan", "--project", str(tmp_path),
                 "--set-vulnscan-confirm-sha256", "0" * 64]) == 1
    assert main(["--set-vulnscan", "scan", "--project", str(tmp_path), "--set-vulnscan-confirm-sha256", sha]) == 0
    assert json.loads(b.read_text(encoding="utf-8"))[pp.POLICY_KEY] == {"level": "scan"}


# ------------------------------------------------------------------ scanning

def _repo(root: Path, name: str, reqs: str, policy: dict | None = None) -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    (repo / "requirements.txt").write_text(reqs, encoding="utf-8")
    if policy is not None:
        _brief(repo, {"project_goal": "a goal for tests", pp.POLICY_KEY: policy})
    return repo


def test_scan_honours_exclude_and_private_but_a_brief_off_is_still_scanned(tmp_path: Path, rec: Recorder) -> None:
    _repo(tmp_path, "optout", "secretlib==1.0\n", {"level": "off"})
    a = _repo(tmp_path, "app", "requests==2.0\nacme-core==3.1\n",
              {"level": "scan", "private_packages": ["ACME-*"], "exclude_paths": ["examples"]})
    (a / "examples").mkdir()
    (a / "examples" / "requirements.txt").write_text("vendoredlib==0.1\n", encoding="utf-8")
    _repo(tmp_path, "plain", "flask==1.0\n")                       # unanswered: scanned as before
    rep = engine.scan_path(tmp_path)
    # S1: the operator asked to scan this root, so a brief's off narrows nothing; it is a reported mismatch
    assert sorted(rec.sent) == ["flask", "requests", "secretlib"]
    assert rep.brief_off == ["optout"] and rep.opted_out == []
    assert rep.excluded == ["app: examples/requirements.txt"]
    assert rep.withheld == ["app: acme-core (PyPI)"]
    assert rep.policy_counts() == {"app": (1, 1)}
    md = rep.markdown()
    assert "Brief says off; scanned by operator enrolment" in md and "Excluded by the project brief" in md
    assert "`app`: 1 package(s)" in md and "acme-core" not in md   # S5: names never in the markdown
    back = Report.from_dict(json.loads(json.dumps(rep.to_dict())))
    assert (back.brief_off, back.excluded, back.withheld) == (rep.brief_off, rep.excluded, rep.withheld)
    redacted = rep.to_dict(private_names=False)
    assert redacted["withheld"] == ["app: (private)"] and "acme" not in json.dumps(redacted)


def test_operator_off_and_exclude_are_never_read_or_sent(tmp_path: Path, rec: Recorder,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    off = _repo(tmp_path, "optout", "secretlib==1.0\n", {"level": "scan"})
    keep = _repo(tmp_path, "app", "requests==2.0\n")
    (keep / "legacy").mkdir()
    (keep / "legacy" / "requirements.txt").write_text("oldlib==0.1\n", encoding="utf-8")
    read: list[Path] = []
    real_parse = engine.parse
    monkeypatch.setattr(engine, "parse", lambda p: read.append(p) or real_parse(p))
    rep = engine.scan_path(tmp_path, exclude=[keep / "legacy"], opted_out=[off])
    assert rec.sent == ["requests"]                                    # S6: never queried
    assert all("legacy" not in str(p) and "optout" not in str(p) for p in read)   # S6: never read
    assert rep.opted_out == ["optout"] and rep.excluded == [] and rep.brief_off == []


@pytest.mark.parametrize("pattern", ["*", "**", "*/**", "?*", ".*", "[a-z]*", "*-*"])
def test_catch_all_patterns_are_refused(tmp_path: Path, pattern: str) -> None:
    for key in ("exclude_paths", "private_packages"):
        _brief(tmp_path, {pp.POLICY_KEY: {"level": "scan", key: [pattern]}})
        pol = pp.read_policy(tmp_path)
        assert pol.level is None and "catch-all" in pol.problem, (key, pattern)


def test_schema_refuses_catch_all_patterns() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for key, value in (("exclude_paths", "*/**"), ("private_packages", "*"), ("private_packages", "@*/*")):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"project_goal": "a goal for tests", pp.POLICY_KEY: {"level": "scan", key: [value]}},
                                schema)
    jsonschema.validate({"project_goal": "a goal for tests",
                         pp.POLICY_KEY: {"level": "scan", "exclude_paths": ["vendor/*"], "private_packages": ["acme-*"]}},
                        schema)


def test_deeply_nested_brief_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "brief.json").write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    pol = pp.read_policy(tmp_path)
    assert pol.level is None and "RecursionError" in pol.problem
    said: list[str] = []
    assert not pp.set_level(tmp_path, "scan", confirm=lambda d: True, say=said.append)


def test_set_level_refuses_a_symlinked_brief_or_one_outside_the_project(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    target = _brief(outside, {"project_goal": "a goal for tests"})
    before = target.read_text(encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "brief.json").symlink_to(target)
    said: list[str] = []
    assert not pp.set_level(proj, "scan", confirm=lambda d: True, say=said.append)
    assert "symlink" in said[-1] and target.read_text(encoding="utf-8") == before
    (proj / "brief.json").unlink()
    (proj / ".agentteams").symlink_to(outside / ".agentteams")      # a symlinked directory leads outside
    assert not pp.set_level(proj, "scan", confirm=lambda d: True, say=said.append)
    assert "outside the project" in said[-1] and target.read_text(encoding="utf-8") == before


def test_is_private_normalises_pypi_names_per_pep503() -> None:
    assert pp.is_private("Acme_Core", ["acme-core"], "PyPI") and pp.is_private("acme.core", ["ACME__core"], "PyPI")
    assert pp.is_private("acme--core-utils", ["acme-core-*"], "PyPI")
    assert not pp.is_private("acme_core", ["acme-core"], "npm")       # npm names are not normalised


def test_private_pypi_spelling_variants_are_never_sent(tmp_path: Path, rec: Recorder) -> None:
    _repo(tmp_path, "app", "Acme_Core==3.1\nacme.utils==1.0\nrequests==2.0\n",
          {"level": "scan", "private_packages": ["acme-*"]})
    rep = engine.scan_path(tmp_path)
    assert rec.sent == ["requests"] and rep.policy_counts() == {"app": (0, 2)}
    rec.sent.clear()
    fix.fix_path(tmp_path / "app", npm=False)
    assert rec.sent == ["requests"]


def test_include_dev_override_per_project(tmp_path: Path, rec: Recorder) -> None:
    for name, flag in (("counted", True), ("default", None)):
        repo = tmp_path / name
        (repo / ".git").mkdir(parents=True)
        (repo / "requirements-dev.txt").write_text("devtool==1.0\n", encoding="utf-8")
        if flag is not None:
            _brief(repo, {pp.POLICY_KEY: {"level": "scan", "include_dev": flag}})
    rec.vulns = {"devtool": ["G1"]}
    rec.records = {"G1": {"id": "G1", "database_specific": {"severity": "HIGH"}, "affected": []}}
    rep = engine.scan_path(tmp_path)
    by = {f.target: f for f in rep.findings}
    assert by["counted"].count_dev is True and by["default"].count_dev is None
    assert Report(target="x", findings=[by["counted"]]).exit_code("high") == EXIT_FINDINGS
    assert Report(target="x", findings=[by["default"]]).exit_code("high") == EXIT_CLEAN


def test_fix_plan_never_looks_up_private_or_excluded(tmp_path: Path, rec: Recorder) -> None:
    repo = _repo(tmp_path, "app", "acme-core==3.1\nrequests==2.0\n",
                 {"level": "scan", "private_packages": ["acme-*"], "exclude_paths": ["examples"]})
    (repo / "examples").mkdir()
    (repo / "examples" / "requirements.txt").write_text("vendoredlib==0.1\n", encoding="utf-8")
    res = fix.fix_path(repo, npm=False)
    assert rec.sent == ["requests"]
    assert ("examples/requirements.txt", res.refused[0][1]) == res.refused[0]


# ------------------------------------------------------------------ reporting

def test_update_notice_prints_only(capsys, tmp_path: Path) -> None:
    pp.report_unanswered({"project_goal": "x", "existing_project_path": str(tmp_path)})
    assert f"enroll {tmp_path}" in capsys.readouterr().out
    pp.report_unanswered({pp.POLICY_KEY: {"level": "scan"}})
    assert capsys.readouterr().out == ""


def test_fleet_report_counts_unanswered(tmp_path: Path, capsys) -> None:
    from agentteams._fleet_report import write_report
    from agentteams.fleet import WorkspaceResult

    results = [WorkspaceResult(path="a", is_git=True, vulnscan_unanswered=True), WorkspaceResult(path="b", is_git=True)]
    write_report(results, tmp_path / "r", tmp_path, False, "both")
    assert json.loads((tmp_path / "r" / "report.json").read_text())["vulnscan_unanswered"] == 1
    assert "1 project(s) unanswered" in capsys.readouterr().out
