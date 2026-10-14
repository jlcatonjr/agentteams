"""``--self`` without ``--output`` defaults to the selected framework's agents directory.

The directory comes from the framework registry (``agents_dir_for``), never a table in
``cli/app.py`` (CH-05). copilot-vscode — the default framework — keeps ``.github/agents``,
and the self-description is always read from ``.github/agents/_build-description.json``.
"""

from __future__ import annotations

import json
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
    "grok": (".grok", "agents"),
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


def _seed_switch(agents_dir: Path, value: bool | None = True) -> None:
    """Write ``references/agent-privilege.json`` (``None`` writes nothing)."""
    if value is None:
        return
    refs = agents_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "agent-privilege.json").write_text(
        json.dumps({"enforce_decision_signing": value}), encoding="utf-8")


def _run_self(monkeypatch: pytest.MonkeyPatch, root: Path, argv: list[str],
              *, switch_dirs: tuple[Path, ...] | None = None, rc: int = 0) -> dict:
    desc = root / ".github" / "agents" / "_build-description.json"
    desc.parent.mkdir(parents=True, exist_ok=True)
    desc.write_text("{}", encoding="utf-8")
    for parts in _EXPECTED.values():  # every surface governed unless a test says otherwise
        if switch_dirs is None:
            _seed_switch(root.joinpath(*parts))
    for d in switch_dirs or ():
        _seed_switch(d)
    monkeypatch.setattr(app, "_SCRIPT_DIR", root)
    seen: dict = {}

    def _capture(args, *_a, **_k):  # noqa: ANN001, ANN002, ANN003 - test double
        seen["output"] = args.output
        seen["description"] = args.description
        return 0

    monkeypatch.setattr(app, "run_generate", _capture)
    assert app.main(argv) == rc
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
        monkeypatch, tmp_path, ["--self", "--framework", "claude", "--output", str(out)],
        switch_dirs=(out,),
    )
    assert Path(seen["output"]) == out


# --- fail closed: --self refuses a surface weaker than the canonical .github/agents ------------


def _canonical(root: Path) -> Path:
    return root / ".github" / "agents"


@pytest.mark.parametrize("framework", ["copilot-vscode", "claude"])
def test_self_fresh_clone_both_missing_is_allowed(framework: str, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _run_self(monkeypatch, tmp_path, ["--self", "--framework", framework], switch_dirs=())
    assert Path(seen["output"]) == tmp_path.joinpath(*_EXPECTED[framework])


@pytest.mark.parametrize("value, state", [(None, "missing"), (False, "OFF")])
@pytest.mark.parametrize("framework", ["goose", "codex", "agents-md", "claude"])
def test_self_refuses_target_weaker_than_canonical(
    framework: str, value: bool | None, state: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path.joinpath(*_EXPECTED[framework])
    _seed_switch(target, value)
    seen = _run_self(monkeypatch, tmp_path, ["--self", "--framework", framework],
                     switch_dirs=(_canonical(tmp_path),), rc=1)
    assert seen == {}  # the pipeline never ran
    err = capsys.readouterr().err
    assert "[SELF-GOVERNANCE] refused" in err
    assert f"{target} (signing switch {state})" in err
    assert f"{_canonical(tmp_path)} (signing switch ON)" in err
    assert "Render or seed that surface's governance first" in err


def test_self_dry_run_refuses_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_self(monkeypatch, tmp_path, ["--self", "--framework", "goose", "--dry-run"],
                     switch_dirs=(_canonical(tmp_path),), rc=1) == {}


@pytest.mark.parametrize("side", ["target", "canonical"])
def test_self_refuses_malformed_switch(side: str, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / ".claude" / "agents"
    _seed_switch(target)
    bad = (target if side == "target" else _canonical(tmp_path)) / "references"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "agent-privilege.json").write_text("{not json", encoding="utf-8")
    assert _run_self(monkeypatch, tmp_path, ["--self", "--framework", "claude"],
                     switch_dirs=(), rc=1) == {}
    assert "cannot read a signing switch" in capsys.readouterr().err


@pytest.mark.parametrize("framework", ["claude", "copilot-vscode", "goose"])
def test_self_allows_both_on(framework: str, tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path.joinpath(*_EXPECTED[framework])
    seen = _run_self(monkeypatch, tmp_path, ["--self", "--framework", framework],
                     switch_dirs=(target, _canonical(tmp_path)))
    assert Path(seen["output"]) == target


def test_self_allows_canonical_off_target_missing(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_switch(_canonical(tmp_path), False)
    _run_self(monkeypatch, tmp_path, ["--self", "--framework", "goose"], switch_dirs=())


@pytest.mark.parametrize("canonical, value, rc", [
    (True, None, 1), (True, False, 1), (True, True, 0), (None, None, 0), (None, True, 0),
])
def test_self_explicit_output_is_checked(canonical: bool | None, value: bool | None, rc: int,
                                         tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "custom"
    _seed_switch(out, value)
    _seed_switch(_canonical(tmp_path), canonical)
    _run_self(monkeypatch, tmp_path, ["--self", "--framework", "claude", "--output", str(out)],
              switch_dirs=(), rc=rc)


def test_help_is_unaffected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        app.main(["--self", "--help"])
    assert exc.value.code == 0
