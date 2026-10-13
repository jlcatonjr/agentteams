"""``--self`` without ``--output`` defaults to the selected framework's agents directory.

The directory comes from the framework registry (``agents_dir_for``), never a table in
``cli/app.py`` (CH-05). copilot-vscode — the default framework — keeps ``.github/agents``,
and the self-description is always read from ``.github/agents/_build-description.json``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.cli import app
from agentteams.frameworks.registry import FRAMEWORKS, agents_dir_for

_EXPECTED = {
    "copilot-vscode": (".github", "agents"),
    "copilot-cli": (".github", "agents"),
    "claude": (".claude", "agents"),
    "goose": (".goose", "recipes"),
    "agents-md": (".agents",),
    "codex": (".codex", "agents"),
}


def test_expected_table_covers_every_registered_framework() -> None:
    assert set(_EXPECTED) == set(FRAMEWORKS)


@pytest.mark.parametrize("framework", sorted(_EXPECTED))
def test_registry_accessor_matches_adapter(framework: str, tmp_path: Path) -> None:
    assert agents_dir_for(framework, tmp_path) == tmp_path.joinpath(*_EXPECTED[framework])
    assert agents_dir_for(framework, tmp_path) == FRAMEWORKS[framework]().get_agents_dir(tmp_path)


def test_registry_accessor_rejects_unknown_framework(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown framework"):
        agents_dir_for("not-a-framework", tmp_path)


def _run_self(monkeypatch: pytest.MonkeyPatch, root: Path, argv: list[str]) -> dict:
    desc = root / ".github" / "agents" / "_build-description.json"
    desc.parent.mkdir(parents=True, exist_ok=True)
    desc.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app, "_SCRIPT_DIR", root)
    seen: dict = {}

    def _capture(args, *_a, **_k):  # noqa: ANN001, ANN002, ANN003 - test double
        seen["output"] = args.output
        seen["description"] = args.description
        return 0

    monkeypatch.setattr(app, "run_generate", _capture)
    assert app.main(argv) == 0
    return seen


@pytest.mark.parametrize("framework", sorted(_EXPECTED))
def test_self_default_output_follows_framework(
    framework: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _run_self(monkeypatch, tmp_path, ["--self", "--framework", framework])
    assert Path(seen["output"]) == tmp_path.joinpath(*_EXPECTED[framework])
    # The description is always the copilot-directory one.
    assert Path(seen["description"]) == tmp_path / ".github" / "agents" / "_build-description.json"


def test_self_default_framework_is_github_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _run_self(monkeypatch, tmp_path, ["--self"])
    assert Path(seen["output"]) == tmp_path / ".github" / "agents"


def test_self_explicit_output_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "custom"
    seen = _run_self(
        monkeypatch, tmp_path, ["--self", "--framework", "claude", "--output", str(out)]
    )
    assert Path(seen["output"]) == out
