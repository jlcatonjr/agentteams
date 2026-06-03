"""Auto-fence-on-update (2026-06-03): --update --merge auto-retrofits a `content`
fence onto eligible legacy (unfenced) files so their template region becomes
mergeable, instead of skipping them. Default-on at the CLI; --yes-gated;
content-safe (pre-injection backup + shrink-guard).

Covers the adversarial-audit conditions: opt-out preserves the legacy-skip,
the --yes gate is honoured, dry-run does not mutate (and reports the would-be
retrofit), material legacy content is preserved by the shrink-guard, and
machine-managed overwrite-fenced paths are never auto-fenced.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import emit

_FENCED_NEW = (
    "---\nname: x\n---\n"
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
    "# Body\n- rule one\n- rule two\n"
    "<!-- AGENTTEAMS:END content -->\n"
)


def _legacy(out: Path, body: str = "# Old body\n- rule one\n") -> Path:
    p = out / "code-hygiene.agent.md"
    p.write_text(f"---\nname: x\n---\n{body}", encoding="utf-8")
    return p


def _emit(out: Path, **kw):
    return emit.emit_all(
        [("code-hygiene.agent.md", _FENCED_NEW)],
        output_dir=out, merge=True, **kw,
    )


def test_default_off_for_library_callers(tmp_path: Path) -> None:
    # emit_all defaults auto_fence_legacy=False so existing callers/tests keep
    # the conservative skip-legacy behaviour; the CLI flips it on.
    out = tmp_path / "a"; out.mkdir()
    legacy = _legacy(out)
    res = _emit(out, yes=True)  # no auto_fence_legacy -> default False
    assert res.fence_injected == []
    assert str(legacy) in res.skipped_legacy
    assert "AGENTTEAMS:BEGIN" not in legacy.read_text(encoding="utf-8")


def test_auto_fence_retrofits_and_merges(tmp_path: Path) -> None:
    out = tmp_path / "a"; out.mkdir()
    legacy = _legacy(out)
    res = _emit(out, yes=True, auto_fence_legacy=True)
    assert str(legacy) in res.fence_injected
    text = legacy.read_text(encoding="utf-8")
    assert "AGENTTEAMS:BEGIN content" in text       # now fenced
    assert "rule two" in text                        # additive template merged in
    assert str(legacy) in res.merged
    assert str(legacy) not in res.skipped_legacy


def test_opt_out_keeps_legacy_skip(tmp_path: Path) -> None:
    out = tmp_path / "a"; out.mkdir()
    legacy = _legacy(out)
    res = _emit(out, yes=True, auto_fence_legacy=False)
    assert res.fence_injected == []
    assert str(legacy) in res.skipped_legacy
    assert "AGENTTEAMS:BEGIN" not in legacy.read_text(encoding="utf-8")


def test_yes_gate_required(tmp_path: Path) -> None:
    out = tmp_path / "a"; out.mkdir()
    legacy = _legacy(out)
    res = _emit(out, yes=False, auto_fence_legacy=True)   # no --yes -> no mutation
    assert res.fence_injected == []
    assert str(legacy) in res.skipped_legacy
    assert "AGENTTEAMS:BEGIN" not in legacy.read_text(encoding="utf-8")


def test_dry_run_reports_but_does_not_mutate(tmp_path: Path) -> None:
    out = tmp_path / "a"; out.mkdir()
    legacy = _legacy(out)
    before = legacy.read_text(encoding="utf-8")
    res = _emit(out, yes=True, auto_fence_legacy=True, dry_run=True)
    assert any("dry-run" in p for p in res.fence_injected)
    # file on disk is untouched
    assert legacy.read_text(encoding="utf-8") == before
    assert "AGENTTEAMS:BEGIN" not in legacy.read_text(encoding="utf-8")
    # dry-run action matches the live run (MERGE), not a legacy SKIP
    actions = {e.action for e in res.dry_run_report.entries}
    assert "SKIP" not in actions


def test_material_legacy_content_preserved_by_shrink_guard(tmp_path: Path) -> None:
    # A legacy body far richer than the thin template render: after auto-fence,
    # the shrink-guard (default preserve) retains the legacy body — not deleted.
    rich = "# Rich\n" + "".join(f"- detail item {i} `path/{i}.py`\n" for i in range(12))
    out = tmp_path / "a"; out.mkdir()
    legacy = _legacy(out, body=rich)
    res = _emit(out, yes=True, auto_fence_legacy=True, shrink_policy="preserve")
    assert str(legacy) in res.fence_injected
    text = legacy.read_text(encoding="utf-8")
    assert "detail item 11" in text                  # rich content preserved
    assert any("retained existing enriched" in n or "shrank" in n or "lost" in n
               for n in res.notices)


def test_machine_managed_path_not_auto_fenced(tmp_path: Path) -> None:
    out = tmp_path / "a"; (out / "references").mkdir(parents=True)
    rel = "references/security-vulnerability-watch.json"
    target = out / rel
    target.write_text('{"legacy": true}\n', encoding="utf-8")
    res = emit.emit_all(
        [(rel, '{"new": true}\n')],
        output_dir=out, merge=True, yes=True, auto_fence_legacy=True,
    )
    # machine-managed json keeps its overwrite-fenced path; never auto-fenced.
    assert res.fence_injected == []
    assert "AGENTTEAMS:BEGIN" not in target.read_text(encoding="utf-8")
