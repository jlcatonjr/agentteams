"""script_census classify and fragments: repository-supplied rules, a checked config, bounded matching."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from agentteams import script_census as sc
from agentteams import script_census_rules as rules

jsonschema = pytest.importorskip("jsonschema")

CONFIG = {
    "roots": ["scripts"],
    "snapshot_markers": ["old-*"],
    "family_rules": [
        {"family": "migration", "path": r"(^|/)migrations/"},
        {"family": "backfill", "content": r"^UPDATE \w+ SET"},
        {"family": "sql-report", "path": r"\.sql$", "content": r"^SELECT"},
    ],
    "fragment_rules": [
        {"fragment": "update-set", "pattern": r"^UPDATE \w+ SET"},
        {"fragment": "argparse-main", "pattern": r"argparse\.ArgumentParser\("},
    ],
}


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    files = {
        "scripts/migrations/001.sql": "UPDATE t SET a = 1;\n",
        "scripts/fix_names.sql": "-- one-off\nUPDATE people SET name = trim(name);\n",
        "scripts/report.sql": "SELECT 1;\n",
        "scripts/notes.py": "# update the plan and SET expectations\nprint('hi')\n",
        "scripts/tool.py": "import argparse\np = argparse.ArgumentParser()\n",
    }
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / rules.CONFIG_NAME).write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _inventory() -> list[dict[str, str]]:
    return sc.inventory(["scripts"])[0]


def _run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = sc.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_classify_takes_the_first_matching_rule(repo: Path) -> None:
    rows, unreadable = rules.classify(_inventory(), rules.load_config())
    family = {r["path"]: r["family"] for r in rows}
    assert family == {
        "migrations/001.sql": "migration",  # also matches 'backfill'; the earlier rule wins
        "fix_names.sql": "backfill",
        "report.sql": "sql-report",
        "notes.py": rules.UNCLASSIFIED,  # 'update the plan' is not a statement
        "tool.py": rules.UNCLASSIFIED,
    }
    assert unreadable == 0


def test_a_rule_with_path_and_content_needs_both(repo: Path) -> None:
    (repo / "scripts" / "query.py").write_text("SELECT = 1\n", encoding="utf-8")
    rows, _ = rules.classify(_inventory(), rules.load_config())
    assert {r["path"]: r["family"] for r in rows}["query.py"] == rules.UNCLASSIFIED


def test_fragments_reports_each_rule_once_per_script(repo: Path) -> None:
    (repo / "scripts" / "fix_names.sql").write_text("UPDATE a SET x=1;\nUPDATE b SET y=2;\n", encoding="utf-8")
    found, unreadable = rules.fragments(_inventory(), rules.load_config())
    assert found == [
        {"path": "scripts/fix_names.sql", "fragment": "update-set"},
        {"path": "scripts/migrations/001.sql", "fragment": "update-set"},
        {"path": "scripts/tool.py", "fragment": "argparse-main"},
    ]
    assert unreadable == 0


def test_a_script_that_is_gone_matches_only_path_rules_and_is_counted(repo: Path) -> None:
    rows = _inventory()
    (repo / "scripts" / "migrations" / "001.sql").unlink()
    (repo / "scripts" / "fix_names.sql").unlink()
    classified, unreadable = rules.classify(rows, rules.load_config())
    family = {r["path"]: r["family"] for r in classified}
    assert family["migrations/001.sql"] == "migration"
    assert family["fix_names.sql"] == rules.UNCLASSIFIED
    assert unreadable == 2


def test_a_file_reached_through_a_link_is_not_read(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.sql"
    outside.write_text("UPDATE secrets SET leaked = 1;\n", encoding="utf-8")
    rows = _inventory()
    (repo / "scripts" / "fix_names.sql").unlink()
    (repo / "scripts" / "fix_names.sql").symlink_to(outside)
    found, unreadable = rules.fragments(rows, rules.load_config())
    assert "scripts/fix_names.sql" not in {f["path"] for f in found}
    assert unreadable == 1


def test_a_row_pointing_outside_its_root_is_refused(repo: Path) -> None:
    with pytest.raises(sc.CensusError, match="not a relative path below its root"):
        rules.fragments([{"path": "../x.sql", "root": "scripts"}], rules.load_config())


def test_a_runaway_pattern_is_stopped_and_named(repo: Path) -> None:
    (repo / "scripts" / "long.py").write_text("a" * 40 + "!\n", encoding="utf-8")
    config = {"fragment_rules": [{"fragment": "runaway", "pattern": r"(a+)+$"}]}
    with pytest.raises(sc.CensusError, match=r"exceeded 0\.5s while rule 'runaway' was matching 'long\.py'"):
        rules.fragments(_inventory(), config, timeout=0.5)


def test_bad_limits_are_refused(repo: Path) -> None:
    with pytest.raises(sc.CensusError, match="timeout"):
        rules.classify(_inventory(), CONFIG, timeout=0)
    with pytest.raises(sc.CensusError, match="max_bytes"):
        rules.classify(_inventory(), CONFIG, max_bytes=-1)


@pytest.mark.parametrize(("change", "message"), [
    ({"family_rules": [{"family": "x"}]}, "does not match the schema at family_rules/0"),
    ({"family_rules": [{"family": "Bad Name", "path": "x"}]}, "does not match the schema at family_rules/0/family"),
    ({"fragment_rules": [{"fragment": "x", "pattern": "a" * 501}]}, "does not match the schema at fragment_rules/0/pattern"),
    ({"families": []}, "does not match the schema at \\(top level\\)"),
    ({"fragment_rules": [{"fragment": "x", "pattern": "(unclosed"}]}, "fragment_rules entry 1, pattern is not a regular expression"),
])
def test_a_config_the_schema_or_re_rejects_is_refused(repo: Path, change: dict[str, object], message: str) -> None:
    path = repo / "bad.json"
    path.write_text(json.dumps(change), encoding="utf-8")
    with pytest.raises(sc.CensusError, match=message):
        rules.load_config(path)


def test_config_lookup_order_and_absence(repo: Path) -> None:
    assert rules.find_config(repo) == repo / "references" / rules.CONFIG_NAME
    (repo / "references" / rules.CONFIG_NAME).unlink()
    agent_refs = repo / ".claude" / "agents" / "references"
    agent_refs.mkdir(parents=True)
    (agent_refs / rules.CONFIG_NAME).write_text("{}", encoding="utf-8")
    assert rules.find_config(repo) == agent_refs / rules.CONFIG_NAME
    (agent_refs / rules.CONFIG_NAME).unlink()
    assert rules.find_config(repo) is None
    with pytest.raises(sc.CensusError, match="no configuration"):
        rules.load_config()
    (repo / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(sc.CensusError, match="cannot be read as JSON"):
        rules.load_config(repo / "x.json")


def test_the_bundled_schema_is_a_valid_schema_and_accepts_the_documented_example() -> None:
    schema = json.loads((Path(rules.__file__).parent / "schemas" / "script-census-config.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    page = (Path(__file__).parent.parent / "docs_src" / "api-reference" / "script-census-rules.md").read_text(encoding="utf-8")
    example = page.split("```json", 1)[1].split("```", 1)[0]
    jsonschema.Draft202012Validator(schema).validate(json.loads(example))


def test_cli_pipeline_and_tsv_round_trip(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    code, inventory_csv, err = _run(capsys, monkeypatch, ["inventory", "--config", "references/script-census.json"])
    assert code == 0 and "5 script(s) under 1 root(s)" in err  # roots came from the configuration
    code, classified, err = _run(capsys, monkeypatch, ["classify"], inventory_csv)
    assert code == 0
    rows = list(csv.DictReader(io.StringIO(classified)))
    assert rows[0].keys() >= {"path", "root", "family"} and "# backfill: 1" in err and "# could not be read: 0" in err
    code, tsv, err = _run(capsys, monkeypatch, ["fragments"], classified)  # a classified table is still an inventory
    assert code == 0 and "# update-set: 2 script(s)" in err
    assert tsv.splitlines()[0] == "path\tfragment"
    back = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
    assert {"path": "scripts/tool.py", "fragment": "argparse-main"} in back and len(back) == 3


def test_cli_refusals_exit_2(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    bad = repo / "bad.json"
    bad.write_text(json.dumps({"fragment_rules": [{"fragment": "x", "pattern": "(unclosed"}]}), encoding="utf-8")
    code, out, err = _run(capsys, monkeypatch, ["fragments", "--config", str(bad)], "path,root\n")
    assert (code, out) == (2, "") and "not a regular expression" in err
    code, out, err = _run(capsys, monkeypatch, ["classify"], "name\nx\n")
    assert code == 2 and "not an inventory" in err
    (repo / "references" / rules.CONFIG_NAME).unlink()
    code, _, err = _run(capsys, monkeypatch, ["classify"], "path,root\n")
    assert code == 2 and "no configuration" in err
    code, _, err = _run(capsys, monkeypatch, ["inventory"])
    assert code == 2 and "no ROOT given" in err


def test_mark_copies_adds_the_configured_snapshot_markers(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "scripts" / "old-2024").mkdir()
    (repo / "scripts" / "old-2024" / "a.py").write_text("print(1)\n", encoding="utf-8")
    _, inventory_csv, _ = _run(capsys, monkeypatch, ["inventory", "scripts"])
    code, marked, _ = _run(capsys, monkeypatch, ["mark-copies", "--config", "references/script-census.json"], inventory_csv)
    kinds = {r["path"]: r["copy_kind"] for r in csv.DictReader(io.StringIO(marked))}
    assert code == 0 and kinds["old-2024/a.py"] == "snapshot" and kinds["tool.py"] == "authored"
