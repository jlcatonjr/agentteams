"""Live vulnscan checks against OSV.dev, PyPI and an installed goose (vulnscan test campaign C10).

Skipped unless ``FEATURE_AUDIT_LIVE=1`` (the same gate as ``tests/test_live_probes.py``); the goose checks also
need ``goose`` on PATH. Every check asserts positive content — a named advisory found, a fix version planned, a
recipe accepted — never only the absence of an error, because the scanner degrades to "unscanned" rather than
raising. The network is reached only through ``python -m agentteams.vulnscan`` (the shipped allowlisted
transport). Every run uses a throwaway HOME, so nothing about this machine is read or written.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIVE = os.environ.get("FEATURE_AUDIT_LIVE") == "1"
requires_live = pytest.mark.skipif(not LIVE, reason="live vulnscan checks touch the network; set FEATURE_AUDIT_LIVE=1")
requires_goose = pytest.mark.skipif(not (LIVE and shutil.which("goose")),
                                    reason="needs FEATURE_AUDIT_LIVE=1 and goose on PATH")


def _env(home: Path) -> dict[str, str]:
    """Return an environment isolated to ``home`` (no operator config, state or goose data leaks in)."""
    keep = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTTEAMS_", "XDG_", "GOOSE_"))}
    return {**keep, "HOME": str(home), "PYTHONPATH": str(REPO), "GOOSE_DISABLE_KEYRING": "1",
            "XDG_CONFIG_HOME": str(home / ".config"), "XDG_STATE_HOME": str(home / ".local/state"),
            "XDG_DATA_HOME": str(home / ".local/share")}


def _vulnscan(home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the vulnscan CLI as an operator would, inside the isolated home."""
    return subprocess.run([sys.executable, "-m", "agentteams.vulnscan", *args], cwd=home, env=_env(home),
                          capture_output=True, text=True, timeout=900)


def _repo(path: Path, files: dict[str, str]) -> Path:
    """Create a committed git repository holding ``files``."""
    path.mkdir(parents=True)
    for name, text in files.items():
        (path / name).write_bytes(text.encode())
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                       check=True, capture_output=True)
    return path


@pytest.fixture()
def code(tmp_path: Path) -> Path:
    """A home with three fixture repos: a vulnerable Python pin (CRLF), a vulnerable npm lockfile, a clean pin."""
    home = tmp_path / "home"
    root = home / "code"
    _repo(root / "py-app", {"requirements.txt": "PyJWT==2.3.0\r\nrequests==2.31.0\r\n"})
    lock = {"name": "web-app", "lockfileVersion": 3, "requires": True,
            "packages": {"": {"name": "web-app", "dependencies": {"axios": "1.7.7"}},
                         "node_modules/axios": {"version": "1.7.7",
                                                "resolved": "https://registry.npmjs.org/axios/-/axios-1.7.7.tgz"}}}
    _repo(root / "web-app", {"package-lock.json": json.dumps(lock)})
    _repo(root / "clean-app", {"requirements.txt": "idna==3.10\n"})
    return root


@requires_live
def test_live_scan_finds_known_advisories(code: Path) -> None:
    """I1: a live scan names the known-vulnerable packages and leaves the clean pin out."""
    r = _vulnscan(code.parent, "scan", str(code), "--format", "json")
    report = json.loads(r.stdout)
    names = {f["name"] for f in report["findings"]}
    assert {"PyJWT", "requests", "axios"} <= names, (names, report.get("feed_errors"))
    assert all(f["ids"] and f["fix"] for f in report["findings"] if f["name"] == "PyJWT")


@requires_live
def test_live_fix_plans_and_applies_keeping_crlf(code: Path) -> None:
    """I1: the fix plan names PyJWT; applying it bumps the pins and keeps the file's CRLF line endings."""
    app = code / "py-app"
    plan = _vulnscan(code.parent, "fix", str(app))
    assert "PyJWT" in plan.stdout, plan.stdout + plan.stderr
    before = (app / "requirements.txt").read_bytes()
    _vulnscan(code.parent, "fix", str(app), "--apply", "--allow-dirty")
    after = (app / "requirements.txt").read_bytes()
    assert after != before and after.count(b"\r\n") == 2, after
    assert b"PyJWT==2.3.0" not in after


@requires_live
def test_live_daily_writes_summary_and_heartbeat(code: Path) -> None:
    """I5: init + daily produce a summary with findings and a heartbeat (exit 0, 1 or 3 when a feed is blocked)."""
    home = code.parent
    assert _vulnscan(home, "init", "--repo-root", str(code)).returncode == 0
    cfg_path = home / ".config/agentteams/vulnscan.json"
    cfg = json.loads(cfg_path.read_text())
    cfg.update({"notify": "none"})
    cfg_path.write_text(json.dumps(cfg))
    r = _vulnscan(home, "daily")
    state = home / ".local/state/agentteams/vulnscan"
    runs = sorted(p for p in state.iterdir() if (p / "summary.json").is_file())
    assert runs, r.stdout + r.stderr
    summary = json.loads((runs[-1] / "summary.json").read_text())
    assert {"PyJWT", "axios"} <= {f["name"] for f in summary["findings"]}
    assert (state / "heartbeat.json").is_file()
    assert r.returncode in (0, 1, 3), r.stdout + r.stderr


def _goose_team(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Generate a goose team with the vulnerability-remediation capability in an isolated home."""
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    team = tmp_path / "ops-team"
    team.mkdir()
    brief = json.loads((REPO / "examples/software-project/brief.json").read_text())
    brief["capabilities"] = sorted(set(brief.get("capabilities", [])) | {"vulnerability_remediation"})
    (team / "brief.json").write_text(json.dumps(brief))
    gen = ("import sys; sys.path.insert(0, %r)\n"
           "from agentteams.cli import security_gate\n"
           "security_gate._assert_security_intelligence_fresh = lambda *a, **k: None\n"
           "import build_team\n"
           "sys.exit(build_team.main(['--description', 'brief.json', '--framework', 'goose', '--project', '.', "
           "'--yes', '--no-scan', '--security-offline']))\n") % str(REPO)
    r = subprocess.run([sys.executable, "-c", gen], cwd=team, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    return team, env


@requires_goose
def test_live_goose_validates_generated_recipes(tmp_path: Path) -> None:
    """I14: the installed goose accepts both generated recipes, and the orchestrator declares the task param."""
    team, env = _goose_team(tmp_path)
    recipes = team / ".goose" / "recipes"
    for name in ("vuln-remediation.yaml", "orchestrator.yaml"):
        v = subprocess.run(["goose", "recipe", "validate", str(recipes / name)], cwd=team, env=env,
                           capture_output=True, text=True, timeout=120)
        assert v.returncode == 0, (name, v.stdout + v.stderr)
    # vulnscan runs vuln-remediation.yaml one-shot: its prompt must carry the run's task.
    assert "{{ task }}" in (recipes / "vuln-remediation.yaml").read_text().split("instructions:")[0]
    orch = (recipes / "orchestrator.yaml").read_text().replace('"', "")
    assert "key: task" in orch and "vuln-remediation.yaml" in orch


@requires_goose
def test_live_goose_accepts_every_flag_vulnscan_passes(tmp_path: Path) -> None:
    """I14: every ``--flag`` in the container command and the debrief resume exists in the installed goose."""
    from agentteams.vulnscan import agent_run, debrief
    env = _env(tmp_path)

    def help_of(*sub: str) -> str:
        r = subprocess.run(["goose", *sub, "--help"], env=env, capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr

    def missing(flags: list[str], help_text: str) -> list[str]:
        # Whole flags only: ``--name`` must not be satisfied by ``--name-x`` in the help text.
        return [f for f in flags if f.startswith("--")
                and not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", help_text)]

    assert missing(agent_run.agent_drivers.GOOSE.command_template(), help_of("run")) == []
    resume = debrief.resume_command("vulnscan-2026-01-01")
    assert missing(resume, help_of(*[a for a in resume[1:] if not a.startswith("-")][:1])) == []


@requires_goose
def test_live_goose_honours_xdg_data_home(tmp_path: Path) -> None:
    """The container relies on goose keeping its session store under ``$XDG_DATA_HOME/goose``."""
    env = _env(tmp_path)
    subprocess.run(["goose", "session", "list"], env=env, capture_output=True, text=True, timeout=120)
    stores = list((tmp_path / ".local/share/goose").rglob("*.db"))
    assert stores, sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
