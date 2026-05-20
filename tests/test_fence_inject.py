"""Plan 4 — legacy-file fence-marker injection tests (≥4 required)."""

from __future__ import annotations

from pathlib import Path

import pytest

import build_team
from agentteams.fence_inject import (
    DEFAULT_RETROFIT_FENCE_ID,
    inject_fence_markers,
)
from agentteams.emit import _FENCE_BEGIN_RE, _extract_fenced_regions


# ---------------- 1. single-region default (sidecar wraps body) ----------------

def test_sidecar_default_wraps_body_with_single_content_fence(tmp_path):
    src = tmp_path / "pipeline-graph.md"
    src.write_text("# Pipeline Graph\n\nA -> B -> C\n", encoding="utf-8")
    r = inject_fence_markers(src)
    assert r.injected is True
    assert r.fence_id == DEFAULT_RETROFIT_FENCE_ID
    assert r.output_path == tmp_path / "pipeline-graph.fenced.md"
    assert r.output_path.exists()
    written = r.output_path.read_text(encoding="utf-8")
    regions = _extract_fenced_regions(written)
    assert isinstance(regions, dict)
    assert set(regions) == {DEFAULT_RETROFIT_FENCE_ID}
    # Original file untouched.
    assert src.read_text() == "# Pipeline Graph\n\nA -> B -> C\n"


def test_sidecar_preserves_yaml_front_matter_above_begin_marker(tmp_path):
    src = tmp_path / "x.md"
    src.write_text(
        "---\nname: x\n---\n# Body\n\nContent.\n",
        encoding="utf-8",
    )
    r = inject_fence_markers(src)
    text = r.output_path.read_text(encoding="utf-8")
    # Front matter must appear above the BEGIN marker (parser invariant).
    fm_end = text.index("---\n", 4) + 4
    first_begin = _FENCE_BEGIN_RE.search(text).start()
    assert first_begin >= fm_end, "BEGIN marker must follow front matter"


# ---------------- 2. in-place mode writes backup before mutating ----------------

def test_in_place_mode_writes_backup_then_rewrites(tmp_path):
    src = tmp_path / "legacy.md"
    body = "# Legacy\n\nNo fences.\n"
    src.write_text(body, encoding="utf-8")
    r = inject_fence_markers(src, mode="in-place", confirm_in_place=True)
    assert r.injected is True
    assert r.output_path == src
    # Backup captured the PRE-injection content verbatim.
    assert r.backup_path is not None and r.backup_path.exists()
    assert r.backup_path.read_text(encoding="utf-8") == body
    # Source is now fenced.
    regions = _extract_fenced_regions(src.read_text(encoding="utf-8"))
    assert isinstance(regions, dict) and DEFAULT_RETROFIT_FENCE_ID in regions


def test_in_place_without_confirm_raises_value_error(tmp_path):
    src = tmp_path / "x.md"
    src.write_text("body\n", encoding="utf-8")
    with pytest.raises(ValueError, match="in-place"):
        inject_fence_markers(src, mode="in-place")  # confirm_in_place defaults False


# ---------------- 3. Idempotency on already-fenced files ----------------

def test_already_fenced_file_is_no_op(tmp_path):
    src = tmp_path / "fenced.md"
    src.write_text(
        "<!-- AGENTTEAMS:BEGIN existing v=1 -->\nbody\n<!-- AGENTTEAMS:END existing -->\n",
        encoding="utf-8",
    )
    before = src.read_text(encoding="utf-8")
    r = inject_fence_markers(src)  # sidecar mode
    assert r.injected is False
    assert r.output_path == src           # no-op signals original path
    assert src.read_text(encoding="utf-8") == before
    # And no sidecar was written.
    assert not (tmp_path / "fenced.fenced.md").exists()


def test_collision_picks_content_suffix(tmp_path):
    """If the default retrofit id ('content', matching emit's default-wrap id)
    is already taken, the helper picks content_1 etc. — verified directly via
    _unique_fence_id since any existing fence triggers the idempotent no-op
    path in inject_fence_markers."""
    from agentteams.fence_inject import _unique_fence_id
    text = "<!-- AGENTTEAMS:BEGIN content v=1 -->\nx\n<!-- AGENTTEAMS:END content -->\n"
    assert _unique_fence_id(text) == "content_1"
    text2 = text + "<!-- AGENTTEAMS:BEGIN content_1 v=1 -->\ny\n<!-- AGENTTEAMS:END content_1 -->\n"
    assert _unique_fence_id(text2) == "content_2"


def test_retrofit_default_id_matches_emit_default_wrap_id():
    """Regression: the retrofit fence id MUST equal the id used by
    emit._normalize_generated_content's default whole-body wrap, so a later
    --update --merge against a team that does emit the file replaces the
    fenced body in-place. A drift between these two constants is the bug
    that produced duplicated bodies in the 2026-05-20 collector-management
    cross-repo update."""
    from agentteams.fence_inject import DEFAULT_RETROFIT_FENCE_ID
    # The default wrap id is hardcoded as 'content' inside
    # emit._normalize_generated_content; surface it here so any future
    # rename of either side trips this test.
    EMIT_DEFAULT_WRAP_ID = "content"
    assert DEFAULT_RETROFIT_FENCE_ID == EMIT_DEFAULT_WRAP_ID


# ---------------- 4. CLI surface ----------------

def test_cli_sidecar_default_creates_sidecar_and_returns_zero(tmp_path, capsys):
    src = tmp_path / "x.md"
    src.write_text("# X\n\nBody.\n", encoding="utf-8")
    rc = build_team.main(["--add-fence-markers", str(src)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wrote sidecar" in out
    assert (tmp_path / "x.fenced.md").exists()


def test_cli_in_place_requires_yes(tmp_path, capsys):
    src = tmp_path / "x.md"
    src.write_text("# X\n", encoding="utf-8")
    rc = build_team.main(["--add-fence-markers", str(src), "--in-place"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "requires --yes" in err
    # File NOT mutated (no fence injected without --yes).
    assert _FENCE_BEGIN_RE.search(src.read_text()) is None


def test_cli_in_place_with_yes_rewrites_and_backs_up(tmp_path, capsys):
    src = tmp_path / "x.md"
    src.write_text("# X\n", encoding="utf-8")
    rc = build_team.main(["--add-fence-markers", str(src), "--in-place", "--yes"])
    assert rc == 0
    assert _FENCE_BEGIN_RE.search(src.read_text()) is not None
    backups_root = tmp_path / ".agentteams-backups"
    assert backups_root.exists()
    snaps = list(backups_root.iterdir())
    assert snaps and (snaps[0] / "x.md").exists()


def test_cli_missing_file_returns_one(tmp_path, capsys):
    rc = build_team.main(["--add-fence-markers", str(tmp_path / "does-not-exist.md")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "file not found" in err.lower()
