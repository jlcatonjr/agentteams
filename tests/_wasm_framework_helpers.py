"""Shared helpers for the ``--framework wasm`` tests (WASM plan P08): render a small team into a tmp project."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import build_team
from tests._fresh_intel import OFFLINE_FLAG

REPO = Path(__file__).resolve().parents[1]
BASE_BRIEF = REPO / "examples" / "software-project" / "brief.json"
WASM_BRIEF = REPO / "examples" / "software-project" / "wasm-runtime.brief.json"


def project(tmp_path: Path, name: str = "proj") -> Path:
    """A real-path project root with ``docs/`` and ``src/`` (the example brief's read paths)."""
    root = Path(os.path.realpath(tmp_path)) / name
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("granted doc with a needle\n")
    (root / "src").mkdir()
    (root / "src" / "m.py").write_text("x = 1\n")
    return root


def write_brief(root: Path, *, base: Path = WASM_BRIEF, **overrides: Any) -> Path:
    """Write a brief (the example plus top-level *overrides*; a value of None deletes the key) outside the project."""
    data = json.loads(base.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path = root.parent / f"{root.name}.brief.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


#: One intel timestamp for every render of a test session: the security agent embeds it, so two renders compare
#: byte-identical only when they read the same snapshot (``tests/_fresh_intel.seed_offline_intel`` uses "now").
_INTEL_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def render(root: Path, brief: Path, *extra: str) -> int:
    """Run ``--framework wasm`` for *root* (a fixed offline intel snapshot seeded); return the exit code."""
    out = root / ".agentteams-wasm" / "agents"
    (out / "references").mkdir(parents=True, exist_ok=True)
    (out / "references" / "security-vulnerability-watch.json").write_text(json.dumps(
        {"generated_at": _INTEL_AT, "sources": [], "vulnerabilities": [], "osv_packages": []}), encoding="utf-8")
    return build_team.main(["--description", str(brief), "--framework", "wasm", "--project", str(root),
                            "--output", str(out), "--yes", "--no-scan", OFFLINE_FLAG, *extra])


def team_files(root: Path) -> dict[str, bytes]:
    """Every file of the rendered team (``.agentteams-wasm/`` minus ``references/`` and the build log)."""
    base = root / ".agentteams-wasm"
    out = {}
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and "/references/" not in rel and not rel.endswith("build-log.json"):
            out[rel] = path.read_bytes()
    return out
