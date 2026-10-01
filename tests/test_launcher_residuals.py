"""#11 (2026-09-30): launcher residuals — planted marker diagnosis, Codex config key warning."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import team_dir_advisories as tda

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
_linux_bwrap = pytest.mark.skipif(not (sys.platform.startswith("linux") and shutil.which("bwrap")),
                                  reason="Linux + bubblewrap only")


def _check(p: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(LAUNCHER), "--scratch", str(p), "--check", "--", "true"],
                          capture_output=True, text=True)


def _plant(p: Path, team: str = ".github/agents") -> None:
    (p / team / "references").mkdir(parents=True)
    (p / team / "references" / "build-log.json").write_text("{}")


@_linux_bwrap
def test_planted_marker_gets_the_planted_diagnosis(tmp_path):
    p = tmp_path.resolve()
    _plant(p)
    r = _check(p)
    assert r.returncode == 2 and "may have been PLANTED" in r.stderr and "OUTSIDE the sandbox" in r.stderr


@_linux_bwrap
def test_real_team_missing_its_store_keeps_the_regenerate_hint(tmp_path):
    p = tmp_path.resolve()
    _plant(p)
    (p / ".github/agents/orchestrator.agent.md").write_text("x")
    r = _check(p)
    assert r.returncode == 2 and "PLANTED" not in r.stderr and "agentteams --update" in r.stderr


@_linux_bwrap
@pytest.mark.parametrize("body,expect", [
    ('approval_policy = "never"\n', "approval_policy"),
    ('[sandbox_workspace_write]\nnetwork_access = true\n', "[sandbox_workspace_write]"),
    ('[mcp_servers.x]\ncommand = "y"\n', "[mcp_servers]"),
    ('model = "o3"\n', None),
])
def test_codex_config_security_keys_warn_but_never_die(tmp_path, body, expect):
    p = tmp_path.resolve()
    (p / ".codex").mkdir()
    (p / ".codex/config.toml").write_text(body)
    r = _check(p)
    assert r.returncode == 0, r.stderr
    if expect:
        assert "WARNING" in r.stderr and expect in r.stderr
    else:
        assert "security-relevant Codex keys" not in r.stderr


def test_python_advisories(tmp_path, capsys):
    _plant(tmp_path)
    _plant(tmp_path, ".codex/agents")
    (tmp_path / ".codex/agents/a.toml").write_text("x")  # a real codex team
    (tmp_path / ".codex/config.toml").write_text('sandbox_mode = "danger-full-access"\nnotify = ["x"]\n')
    assert tda.marker_only_team_dirs(tmp_path) == [".github/agents"]
    assert tda.codex_config_security_keys(tmp_path) == ["sandbox_mode", "notify"]
    tda.print_team_dir_advisories(tmp_path)
    err = capsys.readouterr().err
    assert "may have been PLANTED" in err and "sandbox_mode, notify" in err


def test_codex_mcp_splice_reports_carried_keys(tmp_path):
    from agentteams.codex_mcp_emit import emit_codex_mcp_config
    import inspect

    assert "preserved_security_keys" in inspect.getsource(emit_codex_mcp_config)


@_linux_bwrap
def test_symlinked_codex_config_warns(tmp_path):
    p = tmp_path.resolve()
    (p / ".codex").mkdir()
    (tmp_path / "elsewhere.toml").write_text('approval_policy = "never"\n')
    (p / ".codex/config.toml").symlink_to(tmp_path / "elsewhere.toml")
    r = _check(p)
    # the launcher already refuses a symlinked control-plane path (fail-closed) before warning
    assert r.returncode == 2 and "symlink" in r.stderr
    assert tda.codex_config_security_keys(p) == ["<symlink>"]
