"""The Goose helper scripts ship inside the package, so a pip install never emits the "not found" placeholder.

researchteam's autosync installs agentteams with pip, which has no ``scripts/`` directory: every native Goose
render emitted placeholders that overwrote the working ``goose-run-resilient.py`` / ``goose-openrouter-route-proxy.py``
in the target (researchteam autosync PR #40; mathAgents restored them by hand before).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.frameworks import goose_docs
from agentteams.frameworks.goose import GooseAdapter

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = ("goose-run-resilient.py", "goose-openrouter-route-proxy.py", "goose-coordination-mcp.py")


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_packaged_copy_is_byte_identical_to_the_script(name):
    assert (REPO / "agentteams" / "data" / name).read_bytes() == (REPO / "scripts" / name).read_bytes(), (
        f"agentteams/data/{name} drifted from scripts/{name}: copy the script over it")


def test_a_pip_install_without_scripts_uses_the_packaged_copy(tmp_path):
    missing = tmp_path / "scripts" / "goose-run-resilient.py"            # no scripts/ dir, as in a pip install
    text = goose_docs._shipped_script(missing)
    assert not goose_docs.is_placeholder(text)
    assert text == (REPO / "agentteams" / "data" / "goose-run-resilient.py").read_text(encoding="utf-8")


def test_a_placeholder_is_never_emitted(monkeypatch, tmp_path):
    """If a script is in neither location, the render skips it rather than overwrite a real copy in the target."""
    import agentteams.frameworks.goose as goose_mod

    placeholder = goose_docs._shipped_script(tmp_path / "nope.py")    # in neither scripts/ nor data/
    assert goose_docs.is_placeholder(placeholder)
    monkeypatch.setattr(goose_mod, "_resilient_runner_content", lambda: placeholder)
    monkeypatch.setattr(goose_mod, "_route_proxy_content", lambda: placeholder)
    files = dict(GooseAdapter().extra_output_files({"project_name": "P"}))
    assert "../../scripts/goose-run-resilient.py" not in files
    assert "../../scripts/goose-openrouter-route-proxy.py" not in files


def test_a_normal_render_ships_the_real_scripts():
    files = dict(GooseAdapter().extra_output_files({"project_name": "P"}))
    for rel in ("../../scripts/goose-run-resilient.py", "../../scripts/goose-openrouter-route-proxy.py"):
        assert rel in files and not goose_docs.is_placeholder(files[rel])
