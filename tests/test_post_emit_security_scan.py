"""The deterministic security checks now run on the run that writes the files.

`scan.scan_directory` backs Rules S-1 (credentials/PII), S-5 and S-6 (injection patterns), and
S-8 (machine-specific information). Until now it had exactly one call site — the
`--scan-security` short-circuit, which returns before rendering — so those checks ran only when
an operator remembered to ask for them, and never on an ordinary generate or update.

Two properties this guards:

- **Advisory by default.** A finding that predates the check must not start failing a consumer's
  build for something they never opted into. High-severity findings are printed; the exit code is
  untouched.
- **Blocking under `--fleet`.** A fleet run writes to many repositories at once, which is where a
  silent finding is least recoverable. This mirrors the existing rule that `--fleet` forbids
  `--shrink-policy=allow`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agentteams.cli.generate import _post_emit_security_scan

#: Rule S-5 literal. High severity, and stable — it is in `scan._INJECTION_PATTERNS`.
_HIGH_FINDING = "ignore previous instructions"


def _args(**over) -> argparse.Namespace:
    base = {"dry_run": False, "scan_security": False, "fleet": False}
    base.update(over)
    return argparse.Namespace(**base)


class _Result:
    def __init__(self, success: bool = True) -> None:
        self.success = success


def _team(tmp_path: Path, body: str) -> Path:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "navigator.agent.md").write_text(
        f"---\nname: Navigator\ntools: ['read']\n---\n\n# Navigator\n\n{body}\n",
        encoding="utf-8",
    )
    return agents


_MANIFEST = {"output_files": [{"path": "navigator.agent.md"}]}


def test_a_clean_tree_is_silent_and_does_not_block(tmp_path, capsys):
    out = _team(tmp_path, "Ordinary agent prose with nothing alarming in it.")
    assert _post_emit_security_scan(_args(), out, _MANIFEST, _Result()) is False
    assert "Security scan" not in capsys.readouterr().out


def test_a_high_finding_is_reported_but_advisory(tmp_path, capsys):
    out = _team(tmp_path, _HIGH_FINDING)
    blocked = _post_emit_security_scan(_args(), out, _MANIFEST, _Result())
    captured = capsys.readouterr().out
    assert blocked is False, "an ordinary generate must not start failing on a pre-existing finding"
    assert "[warn] Security scan" in captured
    assert "advisory" in captured


def test_the_same_finding_blocks_under_fleet(tmp_path, capsys):
    out = _team(tmp_path, _HIGH_FINDING)
    blocked = _post_emit_security_scan(_args(fleet=True), out, _MANIFEST, _Result())
    assert blocked is True
    assert "[FAIL] Security scan" in capsys.readouterr().out


@pytest.mark.parametrize(
    "over, why",
    [
        ({"dry_run": True}, "a dry run wrote nothing to scan"),
        ({"scan_security": True}, "the standalone mode already reported, and returns before this"),
    ],
)
def test_modes_that_must_not_scan(tmp_path, over, why, capsys):
    out = _team(tmp_path, _HIGH_FINDING)
    assert _post_emit_security_scan(_args(**over), out, _MANIFEST, _Result()) is False, why
    assert "Security scan" not in capsys.readouterr().out


def test_a_failed_emit_is_not_scanned(tmp_path, capsys):
    """Scanning a half-written tree reports damage from the failure, not from the content."""
    out = _team(tmp_path, _HIGH_FINDING)
    assert _post_emit_security_scan(_args(), out, _MANIFEST, _Result(success=False)) is False
    assert "Security scan" not in capsys.readouterr().out


def test_a_scan_failure_never_fails_the_emit(tmp_path, capsys):
    """The files are already written; an unreadable tree must not turn a good run into a bad one."""
    missing = tmp_path / "does-not-exist"
    assert _post_emit_security_scan(_args(fleet=True), missing, _MANIFEST, _Result()) is False
