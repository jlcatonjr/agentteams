"""Regression guard for the wheel-omitted-schemas outage (2026-09-10).

Root cause of the outage: JSON schemas lived at the repo-root ``schemas/`` dir and
``_schema_path`` resolved them via ``parents[2]/schemas`` — the repo root in a source
checkout, but ``site-packages/schemas`` (never installed) in a wheel. The rc6 wheel
therefore shipped zero schema files and every ``--query-index`` / ``--query-code`` on
an installed wheel hard-failed with "schema unavailable".

Every CI workflow installs ``pip install -e .[test]`` (editable), where source
``schemas/`` is always on the path — so no CI job exercised a built wheel and the gap
shipped undetected. This test builds an actual wheel and asserts the schemas are inside
it, which an editable/``importlib.resources`` probe cannot catch (a resources probe
passes in editable mode even with broken ``package-data``).

The build runs ``--no-isolation`` so it needs no network; ``build``/``setuptools``/
``wheel`` are declared in the ``[test]`` extra.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "agentteams" / "schemas"


def _expected_schema_arcnames() -> list[str]:
    """Every schema in the source tree, as its expected in-wheel arcname."""
    return sorted(
        f"agentteams/schemas/{p.relative_to(SCHEMAS_DIR).as_posix()}"
        for p in SCHEMAS_DIR.rglob("*.json")
    )


def test_wheel_bundles_all_schemas(tmp_path: Path) -> None:
    pytest.importorskip("build", reason="`build` backend not installed (pip install -e .[test])")
    expected = _expected_schema_arcnames()
    assert expected, "no source schemas found — SCHEMAS_DIR wrong?"

    out = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(out), str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(out.glob("agentteams-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    missing = [arc for arc in expected if arc not in names]
    assert not missing, (
        f"{len(missing)} schema file(s) missing from the built wheel "
        f"(package-data regression): {missing}"
    )
