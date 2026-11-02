"""vulnscan-install-guard.sh: the scheduled jobs refuse to run on an agentteams the deploy did not install.

Regression for vfail-1101 (2026-11-01): a "latest release" ``pipx install --force`` of v1.0.4 replaced the deployed
main in the venv the launchd jobs run; v1.0.4 has no ``agentteams.vulnscan``, and the scheduled runs failed. The
guard is sourced with a fake interpreter (a shell script answering the two probes it makes).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DIR = _ROOT / "scripts" / "vulnscan-remediation"
_GUARD = _DIR / "vulnscan-install-guard.sh"
_SHA = "dae677eac3072a9fc2dffef9e31259fc87e877a7"
_FAKE_PY = """#!/bin/bash
# answers the guard's two probes: `import agentteams.vulnscan`, and the distribution's direct_url.json url
case "$3" in
  *"import agentteams.vulnscan"*) exit "${FAKE_IMPORT_RC:-0}" ;;
  *direct_url*) printf '%s\\n' "${FAKE_URL:-}" ;;
  *) exit 9 ;;
esac
"""


def _guard(tmp_path: Path, *, import_rc: int = 0, url: str = "", deployed: object = None) -> tuple[int, str, Path]:
    py = tmp_path / "python"
    py.write_text(_FAKE_PY, encoding="utf-8")
    py.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    if deployed is not None:
        (state / "deployed.json").write_text(deployed if isinstance(deployed, str) else json.dumps(deployed),
                                             encoding="utf-8")
    proc = subprocess.run(
        ["/bin/bash", "-c", f'. "{_GUARD}"; install_guard daily "$GUARD_STATE"'],
        env={"PATH": "/usr/bin:/bin", "PY": str(py), "GUARD_STATE": str(state), "STATE": "/nonexistent-inherited", "FAKE_IMPORT_RC": str(import_rc),
             "FAKE_URL": url, "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stderr, state


def test_no_vulnscan_module_is_refused(tmp_path: Path) -> None:
    rc, err, state = _guard(tmp_path, import_rc=1)
    assert rc == 1 and "has no agentteams.vulnscan" in err
    rec = json.loads((state / "install-guard.json").read_text())
    assert rec["job"] == "daily" and "vulnscan-deploy.sh" in rec["fix"]


def test_no_deploy_record_checks_the_import_only(tmp_path: Path) -> None:
    rc, err, state = _guard(tmp_path, url="file:///anything")
    assert rc == 0 and not err and not (state / "install-guard.json").exists()


def test_the_deployed_commit_passes_and_clears_an_old_refusal(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "install-guard.json").write_text("{}", encoding="utf-8")
    url = f"file:///private/var/folders/x/T/agentteams-{_SHA[:8]}.Ab12Cd/agentteams-{_SHA[:8]}"
    rc, _err, state = _guard(tmp_path, url=url, deployed={"commit": _SHA})
    assert rc == 0 and not (state / "install-guard.json").exists()


@pytest.mark.parametrize("url", [
    "https://github.com/jlcatonjr/agentteams.git",                     # the v1.0.4 release-tag reinstall
    "file:///private/var/folders/x/T/agentteams-8f73a502.Zz/agentteams-8f73a502",   # an older deploy
    "",                                                               # no direct_url.json (PyPI wheel)
    f"file:///tmp/agentteams-{_SHA[:8]}-evil",                        # the commit only as a substring
])
def test_any_other_install_is_refused(tmp_path: Path, url: str) -> None:
    rc, err, _state = _guard(tmp_path, url=url, deployed={"commit": _SHA})
    assert rc == 1 and f"not the deployed commit {_SHA[:12]}" in err


@pytest.mark.parametrize("deployed", ["not json", {"commit": "dae677ea"}, {"commit": "x" * 40}, {}])
def test_an_unreadable_deploy_record_is_refused(tmp_path: Path, deployed: object) -> None:
    rc, err, _state = _guard(tmp_path, url=f"file:///t/agentteams-{_SHA[:8]}", deployed=deployed)
    assert rc == 1 and "unreadable or names no commit" in err


def test_both_scheduled_jobs_source_the_guard_and_the_installer_copies_it() -> None:
    daily = (_DIR / "vulnscan-daily-wrapped.sh").read_text(encoding="utf-8")
    auto = (_DIR / "vulnscan-remediate-auto.sh").read_text(encoding="utf-8")
    deploy = (_DIR / "vulnscan-deploy.sh").read_text(encoding="utf-8")
    assert 'install_guard "daily${1:+ $1}" "$STATE" || exit 4' in daily
    assert 'install_guard remediation "$STATE_DIR" || exit 4' in auto
    assert "vulnscan-install-guard.sh)" in auto.split("FILES=(", 1)[1].split("\n", 2)[1]
    assert '"$SHA" "$STATE/deployed.json"' in deploy
