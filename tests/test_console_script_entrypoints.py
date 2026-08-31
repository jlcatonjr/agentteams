"""Guard: every ``[project.scripts]`` console-script target is actually shippable.

Root cause this prevents
------------------------
The ``agentteams`` console script resolves ``build_team:main``. ``build_team`` is
*not* part of the ``agentteams`` package — it is a separate top-level module that
reaches the wheel only because ``[tool.setuptools] py-modules`` lists it, while
``agentteams`` itself arrives via ``[tool.setuptools.packages.find]``. That split
means an entry point can silently reference a module the build no longer ships
(rename ``build_team.py``, drop it from ``py-modules``, or move ``main`` into a
submodule) and the breakage surfaces only after install, as a bare
``ModuleNotFoundError`` from the generated console-script wrapper.

This guard parses ``pyproject.toml`` directly and asserts, for each declared
console script, that (a) the target module is covered by the packaging config and
(b) the named attribute exists and is callable. It is environment-independent: it
does not build a wheel and does not care whether the working install is editable,
non-editable, or stale.

Deliberately *not* covered here: a stale editable install whose finder points at a
deleted source tree. That is an environment fault, not a packaging fault — no
in-repo test can observe it, because the repo tree under test is by definition
present. See ``docs_src/getting-started.md`` for the recovery procedure.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _console_scripts() -> list[tuple[str, str, str]]:
    """Return ``(script_name, module_path, attribute)`` for each console script."""
    scripts = _config()["project"]["scripts"]
    parsed = []
    for name, target in scripts.items():
        module, _, attr = target.partition(":")
        parsed.append((name, module, attr))
    return parsed


def test_console_scripts_are_declared() -> None:
    """The two supported entry points must both be present."""
    names = {name for name, _, _ in _console_scripts()}
    assert "agentteams" in names
    assert "build-team" in names, "deprecated alias; removed at 2.0 (see STABILITY.md)"


@pytest.mark.parametrize("name,module,attr", _console_scripts())
def test_console_script_target_is_well_formed(name: str, module: str, attr: str) -> None:
    """Each entry point must use the ``module:attribute`` form."""
    assert module, f"console script {name!r} declares no module"
    assert attr, f"console script {name!r} declares no attribute (expected 'module:attr')"


@pytest.mark.parametrize("name,module,attr", _console_scripts())
def test_console_script_target_module_is_shipped(name: str, module: str, attr: str) -> None:
    """The target's top-level module must be covered by the packaging config.

    A module reaches the distribution either as a standalone top-level module
    (``[tool.setuptools] py-modules``) or as part of a discovered package
    (``[tool.setuptools.packages.find] include``, whose entries are glob-ish
    prefixes such as ``agentteams*``).
    """
    setuptools_cfg = _config()["tool"]["setuptools"]
    top_level = module.split(".")[0]

    py_modules = set(setuptools_cfg.get("py-modules", []))
    if top_level in py_modules:
        return

    includes = setuptools_cfg.get("packages", {}).get("find", {}).get("include", [])
    for pattern in includes:
        if top_level == pattern.rstrip("*"):
            return

    pytest.fail(
        f"console script {name!r} targets module {module!r}, but {top_level!r} is "
        f"neither in py-modules ({sorted(py_modules)}) nor matched by "
        f"packages.find include ({includes}). The installed script would fail with "
        f"ModuleNotFoundError."
    )


@pytest.mark.parametrize("name,module,attr", _console_scripts())
def test_console_script_target_is_callable(name: str, module: str, attr: str) -> None:
    """The named attribute must exist on the target module and be callable."""
    mod = importlib.import_module(module)
    assert hasattr(mod, attr), f"console script {name!r} targets missing {module}:{attr}"
    assert callable(getattr(mod, attr)), f"{module}:{attr} is not callable"
