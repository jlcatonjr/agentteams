"""WASM plan P17 §1: the 2x2 OS x WASM render matrix over every framework id, through the real CLI.

Each cell renders ``examples/software-project`` (ungoverned, so the D7 gate prints notices instead of refusing; the
governed path is ``tests/test_sandbox_off_governance.py``) with ``--os-sandbox`` x ``--wasm-sandbox`` into the
framework's canonical agents dir, once per module (renders are cached; the asserts are cheap). Per-plan unit tests
own each emitter; this file proves the plans compose:

* the render succeeds, or (``--framework wasm`` with WASM off) refuses with the documented message, writing nothing;
* the ``sandbox_state`` record in the team manifest (build-log) is exactly the requested switches;
* OS artifacts exist iff the OS switch is on; the shared launcher is byte-identical to the template in every cell;
* ``agentteams_wasm`` launch wiring (and the installed server) exists iff WASM is on and the framework's wiring row
  says it is wired or an inert example;
* with WASM on, the R1 control-plane denies sit in the emitted OS boundary (Claude settings, Goose Seatbelt, Codex
  runner), covering every MCP config path P11 lists, and are absent with WASM off;
* a not-enforced notice is printed iff the framework cannot enforce, the OS sandbox is off, or the R1 rules are not
  merged (a fresh render never claims ``enforced``); merging the emitted Claude examples turns it into ``enforced``;
* the recorded states classify as D7 relaxations exactly on on->off.

The renders run on the real ``sys.platform`` (macOS here): Linux bwrap emission is a per-plan unit test and an
operator step (P17 plan notes).
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import build_team
from agentteams import wasm_enforcement as we
from agentteams.cli.sandbox_effects import NATIVE_EXEC_KEEP, SANDBOX_OS_OFF, SANDBOX_WASM_OFF
from agentteams.cli.sandbox_relaxation_gate import plan_relaxations
from agentteams.frameworks import _wasm_wiring as ww
from agentteams.frameworks.registry import agents_dir_for
from agentteams.sandbox_state import previous_sandbox_state
from agentteams.wasm_mcp import PROTECTED_PATH
from tests._fresh_intel import OFFLINE_FLAG, seed_offline_intel
from tests._wasm_framework_helpers import _INTEL_AT

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
WASM_BRIEF = json.loads((REPO / "examples" / "software-project" / "wasm-runtime.brief.json").read_text(encoding="utf-8"))
LAUNCHER = (REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh").read_bytes()

#: Cell ids: the eight framework ids, plus Goose with ``goose_tool_scoping: grant`` (its MCP-capable mode).
CELLS = ("claude", "goose", "goose-grant", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md", "wasm")
SWITCHES = (("on", "on"), ("on", "off"), ("off", "on"), ("off", "off"))
#: P11's wiring row per cell while WASM is on (the acceptance table; ``wasm`` runs guests itself, no MCP).
WIRING = {"claude": "wired-per-agent", "goose": "not-wired", "goose-grant": "wired-per-agent", "codex": "wired-shared",
          "grok": "unverified-format", "copilot-vscode": "example-only", "copilot-cli": "example-only",
          "agents-md": "instructions-only", "wasm": "not-wired"}
_LAUNCHING = frozenset({"wired-per-agent", "wired-shared", "example-only"})
_BOUNDARY = frozenset({"confine-run.sh", "mac-escape-tests.sh", "sandbox.sb", "config.yaml.agentteams.example",
                       "confined-run.example.sh", "sandbox.toml"})
_WASM_OFF_REFUSAL = "--framework wasm needs WASM confinement, but it is switched off"
_CLAUDE_R1 = ("Edit(/.agentteams/**)", "Edit(/.claude/agents/**)")
#: A real launch of the server: its path immediately followed by ``--agent`` in an argv list (JSON, TOML or YAML).
_LAUNCH = re.compile(r'\.agentteams/bin/agentteams-wasm-mcp\.py"\s*(?:,|\n\s*-)\s*"--agent"')


def _fw(cell: str) -> str:
    return "goose" if cell == "goose-grant" else cell


@dataclass
class Render:
    cell: str
    os: str
    wasm: str
    root: Path
    team: Path
    rc: int
    out: str
    err: str
    rows: dict[str, Any] = field(default_factory=dict)       # wasm_enforcement rows recorded by this render
    wiring: dict[str, Any] = field(default_factory=dict)     # manifest["wasm_wiring"] at that point

    @property
    def text(self) -> str:
        return self.out + self.err

    def files(self) -> dict[str, str]:
        """Every rendered file outside ``references/``, as text (binary files decode with replacement)."""
        found = {}
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root).as_posix()
            if p.is_file() and "/references/" not in f"/{rel}" and not rel.startswith(("docs/", "src/")):
                found[rel] = p.read_bytes().decode("utf-8", "replace")
        return found

    def state(self) -> dict[str, Any] | None:
        log = self.team / "references" / "build-log.json"
        return json.loads(log.read_text(encoding="utf-8")).get("sandbox_state") if log.is_file() else None


def _render(base: Path, cell: str, os_: str, wasm: str) -> Render:
    fw = _fw(cell)
    root = base / f"{cell}-os{os_}-wasm{wasm}"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("doc\n")
    brief = {**(WASM_BRIEF if fw == "wasm" else BRIEF), "enforce_decision_signing": False}
    if cell == "goose-grant":
        brief["goose_tool_scoping"] = "grant"
    path = base / f"{root.name}.brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    team = root / ".agentteams-wasm" / "agents" if fw == "wasm" else agents_dir_for(fw, root)
    if fw == "wasm":
        extra = ["--output", str(team), OFFLINE_FLAG]
        (team / "references").mkdir(parents=True)
        (team / "references" / "security-vulnerability-watch.json").write_text(json.dumps(   # as the P08 helper
            {"generated_at": _INTEL_AT, "sources": [], "vulnerabilities": [], "osv_packages": []}), encoding="utf-8")
    else:
        extra = seed_offline_intel(team, fw)
    rec = Render(cell, os_, wasm, root, team, 0, "", "")
    real = we.report

    def spy(manifest: dict, framework: str, **kw: Any) -> None:
        real(manifest, framework, **kw)
        rec.rows = json.loads(json.dumps(manifest.get(we.KEY) or {}))
        rec.wiring = json.loads(json.dumps(manifest.get(ww.WIRING_KEY) or {}))

    out, err = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as mp, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        mp.setattr(we, "report", spy)
        try:
            rec.rc = build_team.main(["--description", str(path), "--framework", fw, "--project", str(root), "--yes",
                                      "--no-scan", "--os-sandbox", os_, "--wasm-sandbox", wasm, *extra])
        except SystemExit as exc:   # a usage error would be a test bug; keep the code for the assert message
            rec.rc = int(exc.code or 0)
    rec.out, rec.err = out.getvalue(), err.getvalue()
    return rec


@pytest.fixture(scope="module")
def matrix(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str, str], Render]:
    base = Path(str(tmp_path_factory.mktemp("switch-matrix"))).resolve()
    return {(c, o, w): _render(base, c, o, w) for c in CELLS for o, w in SWITCHES}


_ALL = [pytest.param(c, o, w, id=f"{c}-os{o}-wasm{w}") for c in CELLS for o, w in SWITCHES]
#: BUG (P13/R1, routed to the orchestrator): a fresh Goose grant-mode render with the OS sandbox on claims
#: ``subagents ENFORCED``. ``wasm_mcp.control_plane_advisory`` counts the emitted ``.goose/sandbox.sb`` as merged, yet
#: that profile says it is "INERT until you wire it in" (operator merge of ``config.yaml.agentteams.example`` /
#: a ``sandbox-exec`` wrapper, neither checked), so a narrowed agent's recipe is not proven write-protected. Claude
#: needs the live ``settings.json``; Codex says ``conditional`` ("not verifiable at render").
_GOOSE_ENFORCED_UNMERGED = pytest.mark.xfail(strict=True, reason=(
    "P13/R1: fresh goose grant render claims subagents ENFORCED while .goose/sandbox.sb is inert (not wired into "
    "the live Goose config); control_plane_advisory treats the emitted profile as merged"))
_WASM_ON = [pytest.param(c, o, id=f"{c}-os{o}",
                         marks=[_GOOSE_ENFORCED_UNMERGED] if (c, o) == ("goose-grant", "on") else [])
            for c in CELLS if c != "wasm" for o in ("on", "off")]


def _ok(r: Render) -> None:
    assert r.rc == 0, r.text[-1500:]


# --- render outcome and recorded state ------------------------------------------------------------------------------

@pytest.mark.parametrize("cell, os_, wasm", _ALL)
def test_render_succeeds_or_refuses_with_the_documented_message(matrix, cell, os_, wasm):
    r = matrix[(cell, os_, wasm)]
    if cell == "wasm" and wasm == "off":
        assert r.rc == 1 and _WASM_OFF_REFUSAL in r.err
        assert not (r.root / ".agentteams-wasm" / "TEAM.md").exists() and r.state() is None
        return
    _ok(r)


@pytest.mark.parametrize("cell, os_, wasm", _ALL)
def test_recorded_state_is_exactly_the_requested_switches(matrix, cell, os_, wasm):
    r = matrix[(cell, os_, wasm)]
    if r.rc:
        return
    state = r.state()
    assert state is not None and (state["os"], state["os_reason"]) == (os_, "cli")
    assert (state["wasm"], state["wasm_reason"]) == (wasm, "cli")
    assert bool(state["wasm_languages"]) is (wasm == "on")
    # The read-back state (what the next render's gate compares against) agrees with the record.
    prev = previous_sandbox_state(r.team, r.root, _fw(cell))
    assert prev is not None and (prev.os, prev.wasm) == (os_ == "on", wasm == "on")


# --- OS artifacts and the launcher ----------------------------------------------------------------------------------

def _os_artifacts(r: Render, files: dict[str, str]) -> set[str]:
    found = {p for p in files if p.rsplit("/", 1)[-1] in _BOUNDARY}
    settings = files.get(".claude/settings.hooks.example.json")
    if settings is not None and "sandbox" in json.loads(settings):
        found.add("claude sandbox block")
    if _fw(r.cell) == "wasm":
        runtime = json.loads(files[".agentteams-wasm/runtime.json"])
        if runtime["host_os_confine"] != "off":
            found.add(f"wasm host_os_confine={runtime['host_os_confine']}")
    return found


@pytest.mark.parametrize("cell, os_, wasm", _ALL)
def test_os_artifacts_present_iff_the_os_switch_is_on(matrix, cell, os_, wasm):
    r = matrix[(cell, os_, wasm)]
    if r.rc:
        return
    files = r.files()
    assert bool(_os_artifacts(r, files)) is (os_ == "on"), sorted(files)
    launchers = [p for p in files if p.endswith("/confine-run.sh") or p == "sandbox/confine-run.sh"]
    for rel in launchers:   # METAPLAN §5: launcher bytes unchanged
        assert (r.root / rel).read_bytes() == LAUNCHER, rel


# --- agentteams_wasm wiring -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell, os_, wasm", _ALL)
def test_wasm_wiring_present_iff_wasm_on_and_the_framework_wires_mcp(matrix, cell, os_, wasm):
    r = matrix[(cell, os_, wasm)]
    if r.rc:
        return
    files = r.files()
    launching = sorted(p for p, t in files.items() if _LAUNCH.search(t))
    scan = ww.scan_launching_configs(r.root)   # P11's R1 truth source: a fail-closed superset (YAML searched whole)
    assert scan.unsafe == () and set(launching) <= set(scan.launching)
    installed = (r.root / PROTECTED_PATH).is_file()
    if wasm == "off":
        assert launching == [] and not installed and not (r.root / ".agentteams" / "bin").exists()
        assert "[WASM-WIRING]" not in r.text and "[WASM-ENFORCEMENT]" not in r.text
        return
    status = WIRING[cell]
    row = r.wiring.get("frameworks", {}).get(_fw(cell)) if r.wiring else None
    if _fw(cell) != "wasm":
        assert row is not None and row["status"] == status, row
    assert f"[WASM-WIRING] {_fw(cell)}: {status}" in r.text
    assert bool(launching) is (status in _LAUNCHING), launching
    assert installed is (status in _LAUNCHING), "the server is installed iff something launches it"
    # Every file that launches the server is one P11 lists for R1 protection (relative to the project root).
    protected = set(r.wiring.get("protected_config_paths") or [])
    for rel in launching:
        assert ww.covered(rel, protected), (rel, sorted(protected))


# --- R1 control-plane denies ----------------------------------------------------------------------------------------

def _claude_settings(r: Render) -> dict[str, Any] | None:
    path = r.root / ".claude" / "settings.hooks.example.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


@pytest.mark.parametrize("os_, wasm", SWITCHES)
def test_claude_r1_denies_follow_wasm_and_cover_every_launching_config(matrix, os_, wasm):
    r = matrix[("claude", os_, wasm)]
    _ok(r)
    data = _claude_settings(r) or {}
    deny_write = (data.get("sandbox") or {}).get("filesystem", {}).get("denyWrite", [])
    deny = (data.get("permissions") or {}).get("deny", [])
    if os_ == "off":   # no boundary at all: the enforcement row must say advisory (asserted below)
        assert "sandbox" not in data and not any(rule in deny for rule in _CLAUDE_R1)
        return
    assert (".agentteams" in deny_write) is (wasm == "on")
    assert all((rule in deny) is (wasm == "on") for rule in _CLAUDE_R1), deny
    for rel in r.wiring.get("protected_config_paths") or []:
        assert rel in deny_write and f"Edit(/{rel})" in deny, rel


@pytest.mark.parametrize("cell", ["goose", "goose-grant"])
@pytest.mark.parametrize("wasm", ["on", "off"])
def test_goose_seatbelt_denies_the_control_plane_iff_wasm_on(matrix, cell, wasm):
    r = matrix[(cell, "on", wasm)]
    _ok(r)
    profile = (r.root / ".goose" / "sandbox.sb").read_text(encoding="utf-8")
    assert ('/.agentteams"' in profile) is (wasm == "on")
    for rel in r.wiring.get("protected_config_paths") or []:   # the grant-mode recipes that launch the server
        assert rel.startswith(".goose/recipes/") and ".goose/recipes" in profile


@pytest.mark.parametrize("wasm", ["on", "off"])
def test_codex_runner_protects_the_wasm_config_iff_wasm_on(matrix, wasm):
    r = matrix[("codex", "on", wasm)]
    _ok(r)
    runner = (r.root / ".codex" / "confined-run.example.sh").read_text(encoding="utf-8")
    assert "$ROOT/.agentteams" in runner   # unconditional
    assert (ww.CODEX_CONFIG in runner) is (wasm == "on")


# --- D2 notices: never a silent enforcement claim -------------------------------------------------------------------

def _not_enforced(line: str) -> bool:
    return "ADVISORY ONLY" in line or "not enforced" in line


@pytest.mark.parametrize("cell, os_", _WASM_ON)
def test_not_enforced_notice_iff_cannot_enforce_or_os_off_or_r1_unmerged(matrix, cell, os_):
    r = matrix[(cell, os_, "on")]
    _ok(r)
    fw = _fw(cell)
    row = r.rows[fw]
    [line] = [ln.strip() for ln in r.err.splitlines() if ln.strip().startswith(f"[WASM-ENFORCEMENT] {fw}: ")]
    can_enforce = row["subagents"] in ("enforced", "conditional")
    assert row["subagents"] != "enforced", "a fresh render never has the R1 rules merged into live config"
    if can_enforce:   # only the Codex launcher-gated cell: OS on, its runner applies R1 at each launch
        assert (fw, os_) == ("codex", "on") and row["os_sandbox"] and row["control_plane_merged"]
    assert _not_enforced(line) is (not can_enforce), line
    assert ("ADVISORY ONLY" in line) is (row["subagents"] == "advisory")
    if os_ == "off":
        assert row["subagents"] in ("advisory", "declared") and not row["os_sandbox"]
        assert "[WASM-ENFORCEMENT] OS sandbox off:" in r.err


def test_merging_the_emitted_claude_examples_makes_subagents_enforced(matrix, tmp_path, capsys):
    """The operator step the advisory names: merge the emitted settings + MCP examples, then ``--check-wiring``."""
    r = matrix[("claude", "on", "on")]
    _ok(r)
    root = tmp_path / "merged"
    shutil.copytree(r.root, root, symlinks=True)
    brief = root.parent / "merged.brief.json"
    brief.write_text(json.dumps({**BRIEF, "enforce_decision_signing": False}), encoding="utf-8")
    argv = ["--description", str(brief), "--framework", "claude", "--project", str(root), "--os-sandbox", "on",
            "--wasm-sandbox", "on", "--check-wiring"]

    def enforcement_line() -> str:
        capsys.readouterr()
        build_team.main(argv)
        cap = capsys.readouterr()
        return next(ln for ln in (cap.out + cap.err).splitlines() if "[WASM-ENFORCEMENT] claude:" in ln)

    assert "ADVISORY ONLY" in enforcement_line()
    shutil.copy(root / ".claude" / "settings.hooks.example.json", root / ".claude" / "settings.json")
    line = enforcement_line()
    assert "subagents ENFORCED" in line and "ADVISORY ONLY" not in line, line
    # The top-level merge the wiring line documents (example -> .mcp.json) adds a launching config the emitted
    # settings do not deny: the scan finds it and the claim drops back to advisory, naming it (never silent).
    example = json.loads((root / ww.CLAUDE_EXAMPLE).read_text(encoding="utf-8"))
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": example["mcpServers"]}), encoding="utf-8")
    line = enforcement_line()
    assert "ADVISORY ONLY" in line and "denyWrite .mcp.json" in line and "Edit(/.mcp.json)" in line, line


# --- D7: on -> off is classified relaxing ---------------------------------------------------------------------------

@pytest.mark.parametrize("cell", [c for c in CELLS if c != "wasm"])
@pytest.mark.parametrize("old", SWITCHES, ids=lambda s: f"was-os{s[0]}-wasm{s[1]}")
@pytest.mark.parametrize("new", SWITCHES, ids=lambda s: f"now-os{s[0]}-wasm{s[1]}")
def test_recorded_states_classify_relaxations_exactly_on_on_to_off(matrix, cell, old, new):
    fw = _fw(cell)
    a, b = matrix[(cell, *old)], matrix[(cell, *new)]
    prev = previous_sandbox_state(a.team, a.root, fw)
    now = previous_sandbox_state(b.team, b.root, fw)
    assert prev is not None and now is not None
    kinds = {x.kind for x in plan_relaxations(prev, now, framework_id=fw, profile_would_emit=True)
             if not x.continuing}
    expected = {SANDBOX_OS_OFF} if old[0] == "on" and new[0] == "off" else set()
    expected |= {SANDBOX_WASM_OFF} if old[1] == "on" and new[1] == "off" else set()
    assert kinds - {NATIVE_EXEC_KEEP} == expected
