"""``get_fix_plans`` marks a repository whose next step is ``stage-major`` (2026-11-01).

The executor serves requests after the agent's run, so the agent never saw, in its own run, the held
``stage-fix`` that makes ``stage-major`` applicable; the next run requested ``stage-fix`` again, and so on.
The host now marks such plans before the agent starts (``plan-levels.json`` ``major_next``).
Network-free: the ledger, the staged index and the plan digest are stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, executor_guard, ledger, mcp, registry, repo_alias, retry_guard  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "domain" / "vuln-remediation.template.md"


def _futile(target: str, kind: str = "failed", detail: str = retry_guard.FUTILE_DETAIL, op: str = "stage-fix") -> dict:
    return {"op": op, "target": target, "kind": kind, "detail": detail, "ts": "2026-10-31T12:00:00+00:00"}


def _plan(repo: Path, body: str) -> str:
    return f"# Fix plan (dry run)\n\nRepository (the `stage-fix` target): `{repo}`\n\n{body}"


def _npm_section(lock: str, *rows: str) -> str:
    return (f"## npm: `{lock}`\n\nnote\n\n| Package | Installed | → | Action | Notes |\n|---|---|---|---|---|\n"
            + "".join(f"{r}\n" for r in rows) + "\n")


REQ = ("| File | Package | Installed | → | Action | Notes |\n|---|---|---|---|---|---|\n"
       "| `requirements.txt` | lxml | 5.3.0 | 6.1.0 | major-held | major jump 5.3.0 → 6.1.0 |\n\n")
VITE = "| vite | 4.5.14 | ≥ 6.4.4 | major-held | dev only; semver-major |"
GLOB = "| glob | 10.5.0 | ≥ 14.0.2 | major-held | dev only; semver-major |"


def _npm_repo(root: Path, pkg: dict) -> Path:
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps(pkg))
    (root / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": pkg}}))
    return root


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A run directory, no staged fixes, an empty registry, and one unchanged plan digest."""
    state: dict = {"rows": [], "staged": [], "digest": lambda repo, day=None: "same-plan", "tmp": tmp_path}
    monkeypatch.setattr(registry, "load", list)
    monkeypatch.setattr(ledger, "read", lambda **kw: state["rows"])
    monkeypatch.setattr(executor, "staged_records", lambda: [{"repo": r} for r in state["staged"]])
    monkeypatch.setattr(retry_guard, "plan_digest", lambda repo, day=None: state["digest"](repo, day))
    state["run"] = tmp_path / "run"
    state["run"].mkdir()
    return state


def _levels(world: dict, repo: Path, plan: str) -> dict:
    (world["run"] / "fixplan-app.md").write_text(plan)
    return json.loads(executor_guard.write_plan_levels(world["run"]).read_text())["major_next"]


def test_a_held_stage_fix_marks_the_plan_for_stage_major(world: dict) -> None:
    repo = world["tmp"] / "repos" / "py"
    repo.mkdir(parents=True)
    world["rows"] = [_futile(str(repo))]
    assert _levels(world, repo, _plan(repo, REQ)) == {str(repo): {"since": "2026-10-31", "packages": ["lxml"]}}
    text = mcp.Server(world["run"], world["tmp"], world["tmp"]).call("get_fix_plans", {})
    alias = repo_alias.alias(str(repo))
    assert f"## stage-fix target `{alias}` (fixplan-app.md)\n\n**stage-fix held since 2026-10-31: nothing fixable " \
           "without a major jump → request `stage-major` for this repository now**" in text
    assert "can take: lxml" in text and str(repo) not in text


def test_a_refused_held_stage_fix_still_marks(world: dict) -> None:
    repo = world["tmp"] / "repos" / "py"
    repo.mkdir(parents=True)
    held = {**_futile(str(repo), kind="refused", detail="held: stage-fix found ..."), "ts": "2026-11-01T09:00:00+00:00"}
    world["rows"] = [_futile(str(repo)), held]
    assert str(repo) in _levels(world, repo, _plan(repo, REQ))


def test_a_changed_plan_is_not_marked(world: dict) -> None:
    repo = world["tmp"] / "repos" / "py"
    repo.mkdir(parents=True)
    world["rows"] = [_futile(str(repo))]
    world["digest"] = lambda r, day=None: "old-plan" if day else "new-plan"
    assert _levels(world, repo, _plan(repo, REQ)) == {}


@pytest.mark.parametrize("rows", [
    pytest.param([], id="no-stage-fix-yet"),
    pytest.param([_futile("{repo}", detail=f"{retry_guard.UPSTREAM_DETAIL} (origin/main)")], id="fixed-upstream"),
    pytest.param([_futile("{repo}", kind="done", detail="staged")], id="last-stage-fix-staged"),
    pytest.param([_futile("{repo}"), _futile("{repo}", op="stage-major", kind="begun", detail="")],
                 id="stage-major-already-followed"),
])
def test_only_a_held_nothing_fixable_stage_fix_marks(world: dict, rows: list[dict]) -> None:
    repo = world["tmp"] / "repos" / "py"
    repo.mkdir(parents=True)
    world["rows"] = [{**r, "target": str(repo)} for r in rows]
    assert _levels(world, repo, _plan(repo, REQ)) == {}


def test_an_already_staged_repository_is_not_marked(world: dict) -> None:
    repo = world["tmp"] / "repos" / "py"
    repo.mkdir(parents=True)
    world["rows"], world["staged"] = [_futile(str(repo))], [str(repo)]
    assert _levels(world, repo, _plan(repo, REQ)) == {}
    # the server checks its own staged index too, whatever the levels file says
    (world["run"] / mcp.PLAN_LEVELS_FILE).write_text(json.dumps(
        {"major_next": {str(repo): {"since": "2026-10-31", "packages": ["lxml"]}}}))
    (world["run"] / mcp.STAGED_FILE).write_text(json.dumps({"repos": [str(repo)]}))
    assert "request `stage-major`" not in mcp.Server(world["run"], world["tmp"], world["tmp"]).call("get_fix_plans", {})


def test_a_plan_with_a_fix_row_is_not_marked(world: dict) -> None:
    repo = _npm_repo(world["tmp"] / "repos" / "web", {"devDependencies": {"vite": "^4.5.0"}})
    world["rows"] = [_futile(str(repo))]
    plan = _plan(repo, _npm_section("package-lock.json", VITE)
                 + _npm_section("server/package-lock.json", "| qs | 6.14.3 | ≥ 6.16.1 | fix | 4 advisories |"))
    assert _levels(world, repo, plan) == {}


def test_transitive_only_npm_majors_are_not_marked(world: dict) -> None:
    repo = _npm_repo(world["tmp"] / "repos" / "web", {"devDependencies": {"vite": "^4.5.0"}})
    world["rows"] = [_futile(str(repo))]
    assert _levels(world, repo, _plan(repo, _npm_section("package-lock.json", GLOB))) == {}   # glob: transitive
    marked = _levels(world, repo, _plan(repo, _npm_section("package-lock.json", GLOB, VITE)))
    assert marked[str(repo)]["packages"] == ["vite"]                                            # only the direct one


def test_npm_workspace_majors_are_not_marked(world: dict) -> None:
    repo = _npm_repo(world["tmp"] / "repos" / "ws", {"workspaces": ["a"], "devDependencies": {"vite": "^4.5.0"}})
    world["rows"] = [_futile(str(repo))]
    assert _levels(world, repo, _plan(repo, _npm_section("package-lock.json", VITE))) == {}


def test_an_untrusted_ledger_marks_nothing(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["tmp"] / "repos" / "py"
    repo.mkdir(parents=True)

    def broken(**kw: object) -> list:
        raise ValueError("chain broken")
    monkeypatch.setattr(ledger, "read", broken)
    assert _levels(world, repo, _plan(repo, REQ)) == {}


def test_the_recipe_requests_stage_major_for_a_marked_plan_in_the_same_run() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    fence = text[text.index("<!-- AGENTTEAMS:BEGIN daily_remediation v=7 -->"):
                 text.index("<!-- AGENTTEAMS:END daily_remediation -->")]
    flat = " ".join(fence.split())
    assert ("A plan `get_fix_plans` marks `stage-fix held since <date>: nothing fixable without a major jump` is "
            "that case already") in flat
    assert "request `stage-major` for it in this run, no `stage-fix` first" in flat
    # every existing guard stays
    assert "never request `stage-major` while that repository has a `fix` row to stage, a `stage-fix` pending, " \
           "or a fix *Already staged*" in flat
