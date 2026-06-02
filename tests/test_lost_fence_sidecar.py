"""W22 data-loss recovery — sidecar capture of lost fence bodies.

Verifies that when a fence merge would silently drop hand-edited content
(detected by _detect_fence_shrink), the pre-merge fence body is preserved
both on the MergeResult (`lost_fence_bodies`) and, when emit_all is called
with a backup_path, written to a `<rel>.lost.<sid>.md` sidecar so the
operator can recover even under the default `warn` policy.

Plan: tmp/by-week/2026-W22/pr-management-agent-system-2026-05-27.* (debug
follow-up — "data loss we have been handling just now").
"""

from __future__ import annotations

from pathlib import Path

from agentteams import emit


def _fenced(sid: str, body: str) -> str:
    return (
        f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n"
        f"{body}"
        + ("" if body.endswith("\n") else "\n")
        + f"<!-- AGENTTEAMS:END {sid} -->\n"
    )


# ---------------- MergeResult carries lost_fence_bodies ---------------------

def test_merge_result_carries_lost_body_when_shrink_fires():
    existing_body = (
        "- rule a covering `collector-management`\n"
        "- rule b covering `researchteam`\n"
        "- rule c covering `vk-services-local`\n"
        "- rule d covering `tucson_data_collection`\n"
    )
    existing = _fenced("content", existing_body)
    new = _fenced("content", "- generic placeholder\n")
    mr = emit._merge_fenced_content(new, existing)
    assert mr.shrink_notices, "shrink should fire on >50% loss + lost refs"
    assert "content" in mr.lost_fence_bodies
    saved = mr.lost_fence_bodies["content"]
    # Saved body must contain the actual lost project-specific refs.
    assert "collector-management" in saved
    assert "researchteam" in saved


def test_merge_result_lost_body_empty_when_no_shrink():
    existing = _fenced("content", "- one\n- two\n")
    new = _fenced("content", "- one\n- two\n- three\n")  # grows
    mr = emit._merge_fenced_content(new, existing)
    assert mr.lost_fence_bodies == {}
    assert mr.shrink_notices == []


# ---------------- emit_all writes the sidecar file --------------------------

def _write_file(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_emit_all_writes_lost_fence_sidecar(tmp_path):
    output_dir = tmp_path / "agents"
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    rel = "git-operations.agent.md"
    target = output_dir / rel

    existing_body = (
        "- rule 1 cites `collector-management`\n"
        "- rule 2 cites `researchteam`\n"
        "- rule 3 cites `vk-api-utils`\n"
        "- rule 4 cites `tucson_data_collection`\n"
    )
    _write_file(target, _fenced("content", existing_body))
    new_render = _fenced("content", "- single generic rule\n")

    result = emit.emit_all(
        [(rel, new_render)],
        output_dir=output_dir,
        merge=True,
        backup_path=backup_dir,
        # Sidecar recovery is a "warn"-mode feature; the default "preserve"
        # policy keeps the enriched body in place so nothing is lost.
        shrink_policy="warn",
    )

    sidecar = backup_dir / f"{rel}.lost.content.md"
    assert sidecar.exists(), f"expected sidecar at {sidecar}"
    saved = sidecar.read_text(encoding="utf-8")
    assert "collector-management" in saved
    assert "researchteam" in saved

    # Notice text should mention the sidecar for operator discoverability.
    joined = "\n".join(result.notices)
    assert "recovery:" in joined
    assert "lost.content.md" in joined


def test_emit_all_no_sidecar_without_backup_path(tmp_path):
    output_dir = tmp_path / "agents"
    rel = "x.agent.md"
    target = output_dir / rel
    existing = _fenced("content", "- a\n- b\n- c\n- `path/to/foo.py`\n")
    _write_file(target, existing)
    new_render = _fenced("content", "- generic\n")

    result = emit.emit_all(
        [(rel, new_render)],
        output_dir=output_dir,
        merge=True,
        # backup_path omitted on purpose.
    )

    # Notice still fires, but no recovery suffix and no sidecar written.
    assert result.notices, "shrink notice must still be emitted"
    assert not any("recovery:" in n for n in result.notices)


def test_emit_all_no_sidecar_under_allow_policy(tmp_path):
    output_dir = tmp_path / "agents"
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    rel = "y.agent.md"
    target = output_dir / rel
    _write_file(target, _fenced("content",
        "- a `path/to/x.py`\n- b `path/to/y.py`\n- c `path/to/z.py`\n"))
    new = _fenced("content", "- placeholder\n")

    result = emit.emit_all(
        [(rel, new)],
        output_dir=output_dir,
        merge=True,
        shrink_policy="allow",
        backup_path=backup_dir,
    )

    # allow policy: no notices, no sidecar.
    assert result.notices == []
    assert not (backup_dir / f"{rel}.lost.content.md").exists()


def test_shrink_notice_sid_helper():
    assert emit._shrink_notice_sid("fence 'auth': lost concrete refs: a, b") == "auth"
    assert emit._shrink_notice_sid("not a fence notice") is None
