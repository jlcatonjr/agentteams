"""Tests for agentteams.baseline (Phase 0)."""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import baseline


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_capture_empty_dir(tmp_path: Path):
    manifest = baseline.capture(tmp_path, label="empty")
    assert manifest["file_count"] == 0
    assert manifest["files"] == []
    assert manifest["schema_version"] == baseline.BASELINE_SCHEMA_VERSION
    assert manifest["label"] == "empty"


def test_capture_hashes_files_deterministically(tmp_path: Path):
    _write(tmp_path / "a.txt", "alpha")
    _write(tmp_path / "sub" / "b.txt", "beta")
    m1 = baseline.capture(tmp_path, label="t")
    m2 = baseline.capture(tmp_path, label="t")
    assert m1 == m2
    paths = {f["path"] for f in m1["files"]}
    assert paths == {"a.txt", "sub/b.txt"}


def test_capture_excludes_caches(tmp_path: Path):
    _write(tmp_path / "real.txt", "x")
    _write(tmp_path / "__pycache__" / "junk.pyc", "y")
    _write(tmp_path / ".git" / "config", "z")
    m = baseline.capture(tmp_path, label="t")
    paths = {f["path"] for f in m["files"]}
    assert paths == {"real.txt"}


def test_write_and_load_roundtrip(tmp_path: Path):
    _write(tmp_path / "a.txt", "alpha")
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "x.md", "content")
    m = baseline.capture(src, label="r")
    out = tmp_path / "baselines" / "r.json"
    baseline.write(m, out)
    loaded = baseline.load(out)
    assert loaded == m
    # Deterministic format: sorted keys, trailing newline.
    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert parsed["file_count"] == 1


def test_diff_added_removed_changed(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "keep.txt", "same")
    _write(a / "gone.txt", "x")
    _write(a / "mod.txt", "old")
    _write(b / "keep.txt", "same")
    _write(b / "mod.txt", "new")
    _write(b / "new.txt", "fresh")
    prior = baseline.capture(a, label="prior")
    curr = baseline.capture(b, label="curr")
    d = baseline.diff(prior, curr)
    assert d["added"] == ["new.txt"]
    assert d["removed"] == ["gone.txt"]
    assert d["changed"] == ["mod.txt"]


def test_diff_no_changes(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a / "x.txt", "same")
    _write(b / "x.txt", "same")
    d = baseline.diff(baseline.capture(a, label="p"), baseline.capture(b, label="c"))
    assert d == {"added": [], "removed": [], "changed": []}
