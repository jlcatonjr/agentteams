"""The wheel never ships bytecode left in the source tree (vulnscan screenshot capture, 2026-10-23).

``[tool.setuptools.package-data]`` uses ``templates/**`` / ``data/**`` globs, which also match ``__pycache__``
directories a test run leaves behind; MANIFEST.in's ``global-exclude`` covers only the sdist. A shipped
``.pyc`` is listed (with a hash) in the wheel's RECORD, Python rewrites it on first use, and the signing
install-location check then reports the clean install as modified. Built from a copy of the source tree
(without any stale ``build/`` directory) with a planted ``__pycache__``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wheel_excludes_source_tree_bytecode(tmp_path: Path) -> None:
    pytest.importorskip("build.__main__", reason="`build` backend not installed (pip install -e .[test])")
    src = tmp_path / "src"
    shutil.copytree(REPO_ROOT, src, ignore=shutil.ignore_patterns(
        ".git", "build", "dist", "tmp", "workSummaries", ".claude", "*.egg-info", "docs_src", "tests", "site"))
    planted = src / "agentteams" / "data" / "__pycache__" / "planted.cpython-311.pyc"
    planted.parent.mkdir(exist_ok=True)
    planted.write_bytes(b"\x00stale")
    out = tmp_path / "dist"
    proc = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out), str(src)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    (wheel,) = out.glob("agentteams-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    bytecode = [n for n in names if "__pycache__" in n or n.endswith((".pyc", ".pyo"))]
    assert not bytecode, f"the wheel ships bytecode from the source tree: {bytecode[:10]}"
    assert "agentteams/data/server_kit/remote.sh" in names          # package data itself is still shipped
