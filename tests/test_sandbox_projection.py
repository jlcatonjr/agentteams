"""P20 D2/D4/D8: the projected ``.agentteams/sandbox-state.json`` — opt-in, exact shape, ownership, no-follow writes.

* No ``model_substrate`` -> no file; a full CLI render stays free of every P20 artifact (byte-identical extras).
* Records match the JSON schema exactly (and its stdlib twin ``validate_record``); only enumerated values.
* Symlinked ``.agentteams`` or file: refused, nothing written. A foreign ``producer`` is kept with a notice.
* Unset after set: the file is left, with a notice. A second team changing a value: ``SANDBOX STATE CHANGED``.
* ``--dry-run`` writes nothing. ``--check-wiring`` / ``--verify-integrity`` report the file against the render/record.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import sandbox_projection as sp
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.wasm_config import WASM_SUBSTRATE_CLI_KEY, WASM_SUBSTRATE_KEPT_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
SCHEMA = REPO / "agentteams" / "schemas" / "sandbox-state.schema.json"
TEAM = ".claude/agents"


def _manifest(wasm: dict | None = None, **extra) -> dict:
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    if wasm is not None:
        brief["wasm_sandbox"] = wasm
    m = analyze.build_manifest(brief, framework="claude")
    m.update(extra)
    return m


def _record(value: str = "on", *, team: str = TEAM, **extra) -> dict:
    rec = sp.projection_record(_manifest({"model_substrate": value}, **extra), team=team)
    assert rec is not None
    return rec


def _file(root: Path) -> Path:
    return root / sp.PROJECTION_REL


# --- the record and its schema ------------------------------------------------------------------------------------


def test_nothing_is_projected_without_the_switch():
    assert sp.projection_record(_manifest(), team=TEAM) is None
    assert sp.projection_record(_manifest({"enabled": False}), team=TEAM) is None
    assert sp.projection_record(_manifest(None, **{WASM_SUBSTRATE_KEPT_KEY: "on"}), team=TEAM) is None  # kept


@pytest.mark.parametrize("value", ["on", "off"])
def test_records_match_the_schema_exactly(value):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    rec = _record(value)
    jsonschema.Draft7Validator(schema).validate(rec)
    assert list(rec) == list(sp.FIELDS) == schema["required"]
    assert set(schema["properties"]) == set(sp.FIELDS) and schema["additionalProperties"] is False
    assert (rec["schema"], rec["producer"], rec["require_enforced"]) == (sp.SCHEMA_ID, "agentteams", True)
    assert (rec["model_substrate_wasm"], rec["model_substrate_wasm_source"], rec["team"]) == (value, "brief", TEAM)
    assert (rec["wasm_sandbox"], rec["os_sandbox"]) == ("off", "on")  # confined profile; WASM untouched


def test_the_cli_source_is_recorded():
    rec = sp.projection_record(_manifest({"model_substrate": "off"}, **{WASM_SUBSTRATE_CLI_KEY: "on"}), team=TEAM)
    assert (rec["model_substrate_wasm"], rec["model_substrate_wasm_source"]) == ("on", "cli")


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("require_enforced"),
    lambda r: r.update(require_enforced="true"),
    lambda r: r.update(producer="someone-else"),
    lambda r: r.update(schema="agentteams-sandbox-state/v2"),
    lambda r: r.update(model_substrate_wasm="ON"),
    lambda r: r.update(model_substrate_wasm_source="env"),
    lambda r: r.update(os_sandbox_reason="Profile; rm -rf"),
    lambda r: r.update(team="/abs/path"),
    lambda r: r.update(team="../outside"),
    lambda r: r.update(extra="x"),
])
def test_the_stdlib_shape_check_refuses_every_drift(mutate):
    rec = _record()
    mutate(rec)
    with pytest.raises(sp.ProjectionError):
        sp.validate_record(rec)
    jsonschema = pytest.importorskip("jsonschema")
    assert not jsonschema.Draft7Validator(json.loads(SCHEMA.read_text())).is_valid(rec)


def test_the_text_is_canonical():
    text = sp.projection_text(_record())
    assert text.endswith("}\n") and list(json.loads(text)) == list(sp.FIELDS)


# --- writing ------------------------------------------------------------------------------------------------------


def test_write_read_and_unchanged(tmp_path):
    rec = _record()
    assert sp.write_projection(tmp_path, rec, dry_run=False).status == "written"
    assert sp.read_projection(tmp_path) == rec
    before = os.stat(_file(tmp_path)).st_mtime_ns
    assert sp.write_projection(tmp_path, rec, dry_run=False).status == "unchanged"
    assert os.stat(_file(tmp_path)).st_mtime_ns == before
    assert not [p for p in _file(tmp_path).parent.iterdir() if p.name.endswith(".tmp")]


def test_dry_run_writes_nothing(tmp_path):
    out = sp.write_projection(tmp_path, _record(), dry_run=True)
    assert out.status == "dry-run" and not (tmp_path / ".agentteams").exists()


def test_a_symlinked_control_dir_is_refused_and_nothing_written(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / ".agentteams").symlink_to(real)
    with pytest.raises(sp.ProjectionError, match="symlink"):
        sp.write_projection(tmp_path, _record(), dry_run=False)
    assert list(real.iterdir()) == []


def test_a_symlinked_file_is_refused_and_its_target_untouched(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("keep")
    (tmp_path / ".agentteams").mkdir()
    _file(tmp_path).symlink_to(target)
    with pytest.raises(sp.ProjectionError, match="symlink"):
        sp.write_projection(tmp_path, _record(), dry_run=False)
    with pytest.raises(sp.ProjectionError):
        sp.read_projection(tmp_path)
    assert target.read_text() == "keep" and _file(tmp_path).is_symlink()


@pytest.mark.parametrize("text", [json.dumps({"producer": "baseAgent", "model_substrate_wasm": "off"}), "{not json"])
def test_a_foreign_or_unreadable_file_is_kept_with_a_notice(tmp_path, text):
    (tmp_path / ".agentteams").mkdir()
    _file(tmp_path).write_text(text)
    out = sp.write_projection(tmp_path, _record(), dry_run=False)
    assert out.status == "kept-foreign" and "SANDBOX STATE KEPT" in out.notices[0]
    assert _file(tmp_path).read_text() == text


def test_only_a_team_difference_is_not_rewritten(tmp_path):
    sp.write_projection(tmp_path, _record(team=".goose/recipes"), dry_run=False)
    out = sp.write_projection(tmp_path, _record(team=TEAM), dry_run=False)
    assert out.status == "unchanged" and out.notices == []
    assert sp.read_projection(tmp_path)["team"] == ".goose/recipes"


def test_a_value_change_from_another_team_says_so(tmp_path):
    sp.write_projection(tmp_path, _record("on", team=".goose/recipes"), dry_run=False)
    out = sp.write_projection(tmp_path, _record("off", team=TEAM), dry_run=False)
    assert out.status == "written"
    assert any("SANDBOX STATE CHANGED by .claude/agents: model_substrate_wasm 'on' -> 'off'" in n
               for n in out.notices)


def test_emit_projection_leaves_the_file_when_unset(tmp_path, capsys):
    team = tmp_path / ".claude" / "agents"
    team.mkdir(parents=True)
    sp.emit_projection(_manifest({"model_substrate": "on"}), tmp_path, team)
    before = _file(tmp_path).read_bytes()
    assert sp.emit_projection(_manifest(None, **{WASM_SUBSTRATE_KEPT_KEY: "on"}), tmp_path, team) is None
    assert _file(tmp_path).read_bytes() == before
    assert "SANDBOX STATE KEPT" in capsys.readouterr().out


def test_emit_projection_never_raises(tmp_path, capsys):
    (tmp_path / ".agentteams").write_text("a file, not a dir")
    team = tmp_path / ".claude" / "agents"
    team.mkdir(parents=True)
    assert sp.emit_projection(_manifest({"model_substrate": "on"}), tmp_path, team) is None
    assert "SANDBOX STATE NOT WRITTEN" in capsys.readouterr().err


def test_kept_value_fails_toward_on(tmp_path):
    assert sp.kept_value(tmp_path) is None
    (tmp_path / ".agentteams").mkdir()
    for text, want in ((json.dumps({"model_substrate_wasm": "off"}), "off"),
                       (json.dumps({"model_substrate_wasm": "on"}), "on"), ("[]", "on"), ("{bad", "on")):
        _file(tmp_path).write_text(text)
        assert sp.kept_value(tmp_path) == want


# --- --check-wiring / --verify-integrity --------------------------------------------------------------------------


def test_wiring_lines(tmp_path):
    team = tmp_path / ".claude" / "agents"
    team.mkdir(parents=True)
    m = _manifest({"model_substrate": "on"})
    ok, lines = sp.wiring_lines(m, tmp_path, team)
    assert not ok and "exists=false parses=false matches=false" in lines[0]
    sp.emit_projection(m, tmp_path, team)
    ok, lines = sp.wiring_lines(m, tmp_path, team)
    assert ok and lines == [f"model-substrate projection {sp.PROJECTION_REL}: exists=true parses=true matches=true"]
    assert sp.wiring_lines(_manifest(), tmp_path, team) == (True, [])
    ok, _ = sp.wiring_lines(_manifest({"model_substrate": "off"}), tmp_path, team)
    assert not ok


def _build_log(team: Path, substrate: str | None) -> None:
    (team / "references").mkdir(parents=True, exist_ok=True)
    rec = {"os": "on", "os_reason": "brief", "wasm": "off", "wasm_reason": "brief", "wasm_languages": [],
           "wasm_grants": {}, "model_substrate": substrate}
    (team / "references" / "build-log.json").write_text(json.dumps({"sandbox_state": rec}))


def test_verify_projection_flags_a_lowered_floor(tmp_path):
    team = tmp_path / ".claude" / "agents"
    _build_log(team, None)
    assert sp.verify_projection(team, tmp_path) == (0, [])
    _build_log(team, "on")
    rc, lines = sp.verify_projection(team, tmp_path)
    assert rc == 1 and "LOWERED" in lines[0] and "absent" in lines[0]
    sp.write_projection(tmp_path, _record("on"), dry_run=False)
    assert sp.verify_projection(team, tmp_path) == (0, [f"Model-substrate projection: OK ({sp.PROJECTION_REL} = on)"])
    text = _file(tmp_path).read_text().replace('"model_substrate_wasm": "on"', '"model_substrate_wasm": "off"')
    _file(tmp_path).write_text(text)
    rc, lines = sp.verify_projection(team, tmp_path)
    assert rc == 1 and "'off'" in lines[0]
    _build_log(team, "off")
    rc, lines = sp.verify_projection(team, tmp_path)
    assert rc == 0 and "OK" in lines[0]


def test_verify_projection_never_skips_the_floor_when_grants_cannot_be_rebuilt(tmp_path, monkeypatch):
    # W6R F1: a record with WASM grants used to raise ImportError (no wasm_exec) and was silently treated as "no
    # record"; the floor check now reads only model_substrate, so a missing executor or a bad grant cannot hide it.
    from agentteams import sandbox_state as ss

    def _no_wasm_exec():
        raise ImportError("wasm_exec unavailable")

    monkeypatch.setattr(ss, "_wasm_exec", _no_wasm_exec)
    team = tmp_path / ".claude" / "agents"
    (team / "references").mkdir(parents=True)
    limits = {"fuel": 1_000_000, "max_memory_bytes": 64 * 1024 * 1024, "timeout_s": 5}
    rec = {"os": "on", "os_reason": "brief", "wasm": "on", "wasm_reason": "brief", "wasm_languages": [],
           "wasm_grants": {"primary-producer": {"tokens": [], "limits": limits}}, "model_substrate": "on"}
    (team / "references" / "build-log.json").write_text(json.dumps({"sandbox_state": rec}))
    with pytest.raises(ImportError):                   # the full parse still fails closed on the missing executor
        ss.verifier_sandbox_state(None, team, tmp_path)
    rc, lines = sp.verify_projection(team, tmp_path)
    assert rc == 1 and "LOWERED" in lines[0] and "absent" in lines[0]
    assert ss.recorded_model_substrate(team) == "on"
    (team / "references" / "build-log.json").write_text("not json")
    assert ss.recorded_model_substrate(team) is None and sp.verify_projection(team, tmp_path) == (0, [])


def test_an_oversized_or_non_utf8_file_is_unusable_and_kept(tmp_path):
    # W6R F3: the read is atomicio.read_regular_nofollow, bounded at 4 KiB (never reads the whole file).
    _file(tmp_path).parent.mkdir(parents=True)
    _file(tmp_path).write_text(json.dumps({"producer": "agentteams", "pad": "x" * 5000}))
    with pytest.raises(sp.ProjectionError, match="larger than 4096"):
        sp.read_projection(tmp_path)
    assert sp.kept_value(tmp_path) == "on"
    assert sp.write_projection(tmp_path, _record("on"), dry_run=False).status == "kept-foreign"
    _file(tmp_path).write_bytes(b"\xff\xfe{}")
    with pytest.raises(sp.ProjectionError, match="unusable"):
        sp.read_projection(tmp_path)


def test_project_root_for_strips_a_default_agents_dir(tmp_path):
    assert sp.project_root_for(tmp_path / ".claude" / "agents") == tmp_path.resolve()
    assert sp.project_root_for(tmp_path / ".goose" / "recipes") == tmp_path.resolve()
    assert sp.project_root_for(tmp_path) == tmp_path.resolve()


# --- opt-in: renders without the key are unchanged ----------------------------------------------------------------


@pytest.mark.parametrize("framework", ["claude", "goose", "codex", "grok", "copilot-vscode", "agents-md"])
def test_without_the_key_no_emitter_mentions_the_file(framework):
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    files = dict(FRAMEWORKS[framework]().extra_output_files(analyze.build_manifest(dict(brief), framework=framework)))
    assert not any("sandbox-state.json" in content for content in files.values())


def test_a_cli_render_without_the_key_writes_no_projection_and_one_with_it_does(tmp_path):
    import build_team
    from tests._fresh_intel import seed_offline_intel

    for name, wasm in (("plain", None), ("projected", {"model_substrate": "on"})):
        root = tmp_path / name
        root.mkdir()
        brief = json.loads(BRIEF.read_text(encoding="utf-8"))
        if wasm is not None:
            brief["wasm_sandbox"] = wasm
        (root / "brief.json").write_text(json.dumps(brief))
        out = root / ".claude" / "agents"
        extra = seed_offline_intel(out, "claude")
        assert build_team.main(["--description", str(root / "brief.json"), "--framework", "claude",
                                "--project", str(root), "--yes", "--no-scan", *extra]) == 0
        log = json.loads((out / "references" / "build-log.json").read_text())
        if wasm is None:
            assert not (root / ".agentteams" / "sandbox-state.json").exists()
            assert "sandbox_state" not in log and log["schema_version"] == "1.5"
            assert "sandbox-state.json" not in (root / ".claude" / "settings.hooks.example.json").read_text()
        else:
            rec = sp.read_projection(root)
            assert rec["model_substrate_wasm"] == "on" and rec["team"] == TEAM
            assert log["sandbox_state"]["model_substrate"] == "on"
