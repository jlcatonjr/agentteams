"""``write_manifest`` keeps the pins of installed hook copies it cannot see (GROK-RETRO-R2).

The two installed constitutional-gate copies are gitignored, so a re-record from a worktree or a
fresh clone found them absent and wrote ``""``, blanking the main checkout's pins. ``verify``
tolerates that absence, which is why it went unnoticed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import integrity

CLAUDE_COPY, GITHUB_COPY = sorted(integrity.INSTALLED_COPIES)
TEMPLATE = integrity._GATE_TEMPLATE


def _tree(root: Path, *, skip: tuple[str, ...] = ()) -> Path:
    for rel in integrity.ENFORCEMENT_MODULES:
        if rel in skip:
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"bytes of {rel}", encoding="utf-8")
    return root


def _modules(root: Path) -> dict[str, str]:
    return json.loads((root / integrity.MANIFEST_REL_PATH).read_text(encoding="utf-8"))["modules"]


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    """A tree whose manifest was recorded with both installed copies present."""
    integrity.write_manifest(_tree(tmp_path))
    return tmp_path


def test_absent_copies_keep_their_recorded_digest(recorded: Path, capsys) -> None:
    before = _modules(recorded)
    for rel in integrity.INSTALLED_COPIES:
        (recorded / rel).unlink()
    integrity.write_manifest(recorded)
    after = _modules(recorded)
    assert after[CLAUDE_COPY] == before[CLAUDE_COPY] != ""
    assert after[GITHUB_COPY] == before[GITHUB_COPY] != ""
    assert capsys.readouterr().err.count("kept its recorded digest") == 2


def test_present_copies_are_rehashed(recorded: Path, capsys) -> None:
    (recorded / CLAUDE_COPY).write_text("edited hook", encoding="utf-8")
    before = _modules(recorded)
    integrity.write_manifest(recorded)
    assert _modules(recorded)[CLAUDE_COPY] not in ("", before[CLAUDE_COPY])
    assert "[INTEGRITY]" not in capsys.readouterr().err


def test_a_missing_ordinary_module_still_records_empty(recorded: Path) -> None:
    # The carry-over is for the two gitignored copies only; an absent control is not kept alive.
    rel = "agentteams/cli/security_gate.py"
    (recorded / rel).unlink()
    integrity.write_manifest(recorded)
    assert _modules(recorded)[rel] == ""


def test_first_manifest_records_empty_with_a_notice(tmp_path: Path, capsys) -> None:
    integrity.write_manifest(_tree(tmp_path, skip=tuple(integrity.INSTALLED_COPIES)))
    modules = _modules(tmp_path)
    assert modules[CLAUDE_COPY] == modules[GITHUB_COPY] == ""
    assert capsys.readouterr().err.count("has no well-formed prior digest") == 2


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """A recorded tree whose copies are byte-identical to the template, as a real install is."""
    _tree(tmp_path)
    for rel in integrity.INSTALLED_COPIES:
        (tmp_path / rel).write_bytes((tmp_path / TEMPLATE).read_bytes())
    integrity.write_manifest(tmp_path)
    for rel in integrity.INSTALLED_COPIES:
        (tmp_path / rel).unlink()
    return tmp_path


def test_a_kept_digest_matching_the_template_is_quiet(installed: Path, capsys) -> None:
    integrity.write_manifest(installed)
    err = capsys.readouterr().err
    assert err.count("kept its recorded digest") == 2 and "WARNING" not in err


def test_a_changed_template_warns_for_each_kept_digest(installed: Path, capsys) -> None:
    (installed / TEMPLATE).write_text("new gate template", encoding="utf-8")
    integrity.write_manifest(installed)
    err = capsys.readouterr().err
    assert err.count("probably stale") == 2 and CLAUDE_COPY in err and GITHUB_COPY in err


def test_a_planted_digest_is_kept_but_warned_about(installed: Path, capsys) -> None:
    path = installed / integrity.MANIFEST_REL_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["modules"][CLAUDE_COPY] = "ab" * 32
    path.write_text(json.dumps(payload), encoding="utf-8")
    integrity.write_manifest(installed)
    err = capsys.readouterr().err
    assert _modules(installed)[CLAUDE_COPY] == "ab" * 32
    assert "WARNING" in err and CLAUDE_COPY in err.split("WARNING")[1]


@pytest.mark.parametrize("junk", ["not-a-digest", "AB" * 32, "ab" * 31])
def test_a_malformed_prior_digest_is_not_carried(installed: Path, junk: str) -> None:
    path = installed / integrity.MANIFEST_REL_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["modules"][CLAUDE_COPY] = junk
    path.write_text(json.dumps(payload), encoding="utf-8")
    integrity.write_manifest(installed)
    assert _modules(installed)[CLAUDE_COPY] == ""


def test_the_template_constant_is_a_pinned_module() -> None:
    assert TEMPLATE in integrity.ENFORCEMENT_MODULES


@pytest.mark.parametrize("corrupt", ["{not json", '{"modules": ["a"]}', '["x"]', '{"modules": {"a": 1}}'])
def test_a_corrupt_manifest_preserves_nothing_and_says_so(recorded: Path, corrupt: str, capsys) -> None:
    for rel in integrity.INSTALLED_COPIES:
        (recorded / rel).unlink()
    (recorded / integrity.MANIFEST_REL_PATH).write_text(corrupt, encoding="utf-8")
    integrity.write_manifest(recorded)
    modules = _modules(recorded)
    assert modules[CLAUDE_COPY] == modules[GITHUB_COPY] == ""
    assert "unreadable" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files regardless of mode")
def test_a_present_but_unreadable_copy_does_not_inherit_the_old_digest(recorded: Path) -> None:
    copy = recorded / CLAUDE_COPY
    copy.chmod(0)
    try:
        integrity.write_manifest(recorded)
    finally:
        copy.chmod(0o644)
    assert _modules(recorded)[CLAUDE_COPY] == ""
