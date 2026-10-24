"""``integrity.stale_summary`` and the conftest session-start notice (INTEGRITY-MANIFEST-STALE-PER-PIN-EDIT).

Every edit to a pinned module stales the manifest; the summary names the stale pins and the exact
re-record command, and the conftest hook prints it once per session without affecting the outcome.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from agentteams import integrity

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def _tree(root: Path) -> Path:
    """Write a stand-in file for every pinned module under *root* and record its manifest."""
    for rel in integrity.ENFORCEMENT_MODULES:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"bytes of {rel}", encoding="utf-8")
    integrity.write_manifest(root)
    return root


def test_clean_tree_summarises_to_empty(tmp_path: Path) -> None:
    assert integrity.stale_summary(_tree(tmp_path)) == ""


def test_tampered_pin_is_named_with_the_re_record_command(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    edited = "agentteams/scan.py"
    (root / edited).write_text("edited", encoding="utf-8")
    summary = integrity.stale_summary(root)
    assert integrity.MANIFEST_REL_PATH in summary
    assert f"  - {edited}: modified" in summary
    assert f"agentteams --write-integrity-manifest --project {root.resolve()}" in summary
    # Only the stale pin is listed, not every module.
    assert summary.count("  - ") == 1


def test_unmanifested_pin_is_named(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    manifest = root / integrity.MANIFEST_REL_PATH
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    del payload["modules"]["agentteams/fences.py"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "  - agentteams/fences.py: unmanifested" in integrity.stale_summary(root)


def _load_conftest():
    spec = importlib.util.spec_from_file_location("_conftest_under_test", _CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Reporter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str, **_markup: bool) -> None:
        self.lines.append(line)


def _session(reporter: _Reporter | None, *, worker: bool = False) -> SimpleNamespace:
    config = SimpleNamespace(pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter))
    if worker:
        config.workerinput = {"workerid": "gw0"}
    return SimpleNamespace(config=config)


def test_conftest_prints_through_the_reporter_and_skips_workers(monkeypatch) -> None:
    hook = _load_conftest()._report_stale_integrity_manifest
    monkeypatch.setattr(integrity, "stale_summary", lambda root: "STALE-NOTICE")
    reporter = _Reporter()
    hook(_session(reporter))
    assert reporter.lines == ["STALE-NOTICE"]
    worker_reporter = _Reporter()
    hook(_session(worker_reporter, worker=True))
    assert worker_reporter.lines == []


def test_conftest_falls_back_to_stderr_and_never_raises(monkeypatch, capsys) -> None:
    hook = _load_conftest()._report_stale_integrity_manifest

    def _unreadable(root: Path) -> str:
        raise RuntimeError("unable to read enforcement integrity manifest: bad json")

    monkeypatch.setattr(integrity, "stale_summary", _unreadable)
    hook(_session(None))
    assert "could not check the enforcement manifest" in capsys.readouterr().err


def test_conftest_is_silent_when_clean(monkeypatch) -> None:
    hook = _load_conftest()._report_stale_integrity_manifest
    monkeypatch.setattr(integrity, "stale_summary", lambda root: "")
    reporter = _Reporter()
    hook(_session(reporter))
    assert reporter.lines == []
