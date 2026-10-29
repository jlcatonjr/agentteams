"""``status`` and the coverage section leave out the manifests a scan never reads (plan vba-p4).

Fixtures use ``environment.yml`` and ``lake-manifest.json`` outside any vendored directory, so the result does not
depend on which ecosystems are supported or which directories discovery skips.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams.vulnscan import coverage


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "proj"
    (ws / "condaenv").mkdir(parents=True)
    (ws / "condaenv" / "environment.yml").write_text("name: x\n", encoding="utf-8")
    (ws / "proofs").mkdir()
    (ws / "proofs" / "lake-manifest.json").write_text("{}", encoding="utf-8")
    (ws / "requirements.txt").write_text("requests==2.32.3\n", encoding="utf-8")
    return ws


def test_without_a_config_every_unsupported_manifest_is_listed(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    labels, only = coverage.unsupported_manifests(ws)
    assert labels == ["condaenv/environment.yml (conda)", "proofs/lake-manifest.json (Lean)"]
    assert not only


def test_config_exclude_inside_the_workspace_drops_its_manifests(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    labels, _only = coverage.unsupported_manifests(ws, {"exclude": [str(ws / "proofs")]})
    assert labels == ["condaenv/environment.yml (conda)"]


def test_brief_exclude_paths_are_honoured(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / "brief.json").write_text(json.dumps({"vulnerability_scanning": {"level": "scan",
                                                                        "exclude_paths": ["condaenv/*"]}}),
                                   encoding="utf-8")
    labels, _only = coverage.unsupported_manifests(ws, {})
    assert labels == ["proofs/lake-manifest.json (Lean)"]


def test_excluded_unsupported_manifests_do_not_make_the_workspace_unsupported_only(tmp_path: Path) -> None:
    ws = tmp_path / "proj"
    (ws / "proofs").mkdir(parents=True)
    (ws / "proofs" / "lake-manifest.json").write_text("{}", encoding="utf-8")
    (ws / "requirements.txt").write_text("requests==2.32.3\n", encoding="utf-8")
    (ws / "condaenv").mkdir()
    (ws / "condaenv" / "environment.yml").write_text("name: x\n", encoding="utf-8")
    cfg = {"exclude": [str(ws / "proofs"), str(ws / "condaenv")]}
    assert coverage.unsupported_only(ws) is False   # requirements.txt is supported either way
    (ws / "requirements.txt").unlink()
    assert coverage.unsupported_only(ws) is True
    assert coverage.unsupported_only(ws, cfg) is False   # nothing the scan would read is unsupported
    assert coverage.project_state(cfg, None, ws, []) != "unsupported ecosystem"


def test_an_exclude_outside_the_workspace_changes_nothing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert coverage.unsupported_manifests(ws, {"exclude": [str(other)]}) == coverage.unsupported_manifests(ws)


def test_a_workspace_whose_manifests_are_all_excluded_has_nothing_to_scan(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    cfg = {"exclude": [str(ws / "proofs"), str(ws / "condaenv"), str(ws / "requirements.txt")]}
    assert coverage.nothing_to_scan(ws) is False
    assert coverage.nothing_to_scan(ws, cfg) is True
    assert coverage.project_state(cfg, None, ws, []) == "nothing to scan"
