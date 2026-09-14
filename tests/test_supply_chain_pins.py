"""Supply-chain pin consistency (Workstream B §5).

Asserts references/dependency-pins.json stays consistent with pyproject.toml's extras, and — when a
pinned library is importable — that its reported version matches the pin. Skips the import check
when the extra is absent, so base CI (which does not install `signing`) is unaffected.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PINS = _REPO / "references" / "dependency-pins.json"
_PYPROJECT = _REPO / "pyproject.toml"


def _pins() -> list[dict]:
    data = json.loads(_PINS.read_text(encoding="utf-8"))
    assert isinstance(data.get("pins"), list) and data["pins"], "dependency-pins.json has no pins"
    return data["pins"]


def _extras() -> dict[str, list[str]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def test_pins_file_is_well_formed():
    for pin in _pins():
        assert pin["name"] and pin["version"] and pin["extra"]
        assert len(pin["sdist_sha256"]) == 64
        int(pin["sdist_sha256"], 16)  # hex


def test_each_pin_matches_its_pyproject_extra_exact():
    extras = _extras()
    for pin in _pins():
        extra = pin["extra"]
        assert extra in extras, f"pyproject has no [{extra}] extra for {pin['name']}"
        expected = f"{pin['name']}=={pin['version']}"
        assert expected in extras[extra], (
            f"{extra} extra must pin {expected!r} exactly; found {extras[extra]}"
        )


def test_installed_version_matches_pin_when_present():
    import importlib

    for pin in _pins():
        try:
            mod = importlib.import_module(pin["name"])
        except ImportError:
            pytest.skip(f"{pin['name']} not installed; base CI unaffected")
        assert getattr(mod, "__version__", None) == pin["version"], (
            f"installed {pin['name']} {getattr(mod, '__version__', '?')} != pinned {pin['version']}"
        )
