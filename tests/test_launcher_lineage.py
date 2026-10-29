"""The launcher lineage tells a team's ``sandbox/confine-run.sh`` copy apart: current / older / modified / missing.

* **Regeneration equality.** ``scripts/build_launcher_lineage.py`` rebuilt from git must reproduce the shipped
  ``agentteams/data/launcher_lineage.json`` byte for byte (skipped in a shallow clone unless
  ``AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1``).
* **Newest is current.** The newest version's sha256 is the template on disk, everywhere: an edit to the template
  without regenerating the lineage fails here.
* **States.** Each state, the security flag on newer versions, and the refusals (symlink, non-regular, oversized).
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from agentteams import launcher_lineage
from agentteams.launcher_lineage import LauncherState, launcher_state, load_lineage

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_launcher_lineage", REPO / "scripts" / "build_launcher_lineage.py")
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)

LINEAGE = load_lineage()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(data: bytes, commit: str, *, security: bool = False) -> dict:
    return {"sha256": _sha(data), "commit": commit * 40, "date": "2026-10-01T00:00:00+00:00", "security": security}


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_lineage_regenerates_byte_for_byte() -> None:
    if builder.is_shallow():
        if os.environ.get("AGENTTEAMS_REQUIRE_LINEAGE_HISTORY") == "1":
            pytest.fail("launcher lineage history required but this clone is shallow")
        pytest.skip("shallow clone: the launcher's history is not here")
    assert builder.serialise(builder.build()) == launcher_lineage.LINEAGE_PATH.read_text(encoding="utf-8"), (
        "agentteams/data/launcher_lineage.json is stale or hand-edited: run scripts/build_launcher_lineage.py, "
        "review the diff (and the security column), and re-pin the integrity manifest")


def test_the_newest_version_is_the_shipped_template() -> None:
    assert LINEAGE[-1]["sha256"] == _sha(launcher_lineage.TEMPLATE_PATH.read_bytes()), (
        "the launcher template changed without regenerating agentteams/data/launcher_lineage.json "
        "(run scripts/build_launcher_lineage.py)")
    assert launcher_state(launcher_lineage.TEMPLATE_PATH).state == "current"
    assert [e["date"] for e in LINEAGE] == sorted(e["date"] for e in LINEAGE)   # oldest first


def test_build_refuses_a_shallow_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if builder.is_shallow():
        pytest.skip("this clone is itself shallow")
    shallow = tmp_path / "shallow"
    proc = subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{REPO}", str(shallow)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip(f"cannot make a shallow clone here: {proc.stderr.strip()}")
    monkeypatch.setattr(builder, "REPO_ROOT", shallow)
    assert builder.is_shallow()
    with pytest.raises(RuntimeError, match="shallow"):
        builder.build()
    assert builder.main(["--check"]) == 2


def test_security_rule() -> None:
    assert builder._security("0" * 40, "fix(sandbox): close a gap")
    assert builder._security("0" * 40, "fix(launcher): parity")
    assert builder._security("0" * 40, "Fix(security)!: breaking")
    assert not builder._security("0" * 40, "feat(sandbox): a new launcher")
    assert not builder._security("0" * 40, "fix(ci): green the matrix")
    reviewed = next(iter(builder.SECURITY_REVIEWED))
    assert builder._security(reviewed, "feat(x): anything")
    assert all(len(sha) == 40 for sha in builder.SECURITY_REVIEWED)


def test_each_state(tmp_path: Path) -> None:
    v1, v2, v3 = b"#!/bin/sh\n# v1\n", b"#!/bin/sh\n# v2\n", b"#!/bin/sh\n# v3\n"
    lineage = [_entry(v1, "a"), _entry(v2, "b", security=True), _entry(v3, "c")]
    copy = tmp_path / "sandbox" / "confine-run.sh"
    assert launcher_state(copy, lineage) == LauncherState("missing", f"no launcher at {copy}")
    _write(copy, v3)
    assert launcher_state(copy, lineage).state == "current"
    _write(copy, v2)
    st = launcher_state(copy, lineage)
    assert (st.state, st.newer, st.newer_security) == ("older", 1, False)
    _write(copy, v1)
    st = launcher_state(copy, lineage)
    assert (st.state, st.newer, st.newer_security) == ("older", 2, True)
    assert "including a security fix" in st.detail and "cccccccc" in st.detail
    _write(copy, v1 + b"# local edit\n")
    st = launcher_state(copy, lineage)
    assert (st.state, st.newer, st.newer_security) == ("modified", 0, False) and "matches no released" in st.detail


def test_a_reverted_version_is_current_when_it_is_the_newest(tmp_path: Path) -> None:
    v1, v2 = b"one\n", b"two\n"
    copy = _write(tmp_path / "confine-run.sh", v1)
    assert launcher_state(copy, [_entry(v1, "a"), _entry(v2, "b"), _entry(v1, "c")]).state == "current"


def test_an_older_shipped_version_is_older(tmp_path: Path) -> None:
    if builder.is_shallow():
        pytest.skip("shallow clone: older versions are not here")
    first = LINEAGE[0]
    blob = subprocess.run(["git", "-C", str(REPO), "show", f"{first['commit']}:{first['path']}"],
                          capture_output=True, check=True).stdout
    st = launcher_state(_write(tmp_path / "confine-run.sh", blob))
    assert st.state == "older" and st.newer >= 1 and st.newer_security


def test_a_symlink_is_refused_not_followed(tmp_path: Path) -> None:
    real = _write(tmp_path / "real.sh", launcher_lineage.TEMPLATE_PATH.read_bytes())
    link = tmp_path / "confine-run.sh"
    link.symlink_to(real)
    st = launcher_state(link)
    assert st.state == "modified" and "symlink" in st.detail
    dangling = tmp_path / "dangling.sh"
    dangling.symlink_to(tmp_path / "nowhere")
    assert launcher_state(dangling).state == "modified"


def test_a_directory_fifo_or_oversized_file_is_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "dir.sh"
    d.mkdir()
    assert launcher_state(d).state == "modified"
    fifo = tmp_path / "fifo.sh"
    os.mkfifo(fifo)
    assert launcher_state(fifo).state == "modified"   # O_NONBLOCK: never waits on a writer
    big = _write(tmp_path / "big.sh", b"x" * 64)
    monkeypatch.setattr(launcher_lineage, "MAX_LAUNCHER_BYTES", 16)
    st = launcher_state(big, [_entry(b"x" * 64, "a")])
    assert st.state == "modified" and "larger than" in st.detail


def test_a_malformed_lineage_raises(tmp_path: Path) -> None:
    bad = tmp_path / "lineage.json"
    bad.write_text('{"versions": [{"sha256": "nothex", "commit": "a", "date": "d", "security": false}]}',
                   encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        load_lineage(bad)
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no 'versions'"):
        load_lineage(bad)


def test_the_lineage_and_reader_are_integrity_pinned() -> None:
    from agentteams import integrity

    source = Path(integrity.__file__).read_text(encoding="utf-8")
    assert '"agentteams/data/launcher_lineage.json"' in source and '"agentteams/launcher_lineage.py"' in source
