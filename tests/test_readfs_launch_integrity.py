"""readfs launch integrity (references/plans/readfs-launch-integrity.design.md, R1–R3).

The Goose read-only file server ships as package data with a pinned sha256; under the switch it is installed in
the session-write-denied control plane and launched with ``python3 -I -S``; the runner refuses a mismatched copy.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import proposal_runner as R
from agentteams.cli import standalone_modes
from agentteams.frameworks import goose as goose_mod
from agentteams.frameworks import goose_tool_scoping as scoping
from agentteams.frameworks.goose import GooseAdapter

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "agentteams" / "data" / "goose-readfs-mcp.py"


def _manifest(*, switch: bool) -> dict:
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}],
            "goose_tool_scoping": "grant"}
    if switch:
        desc.update({"write_policy": "orchestrator-only", "privilege_profile": "confined"})
    return analyze.build_manifest(desc, framework="goose")


def test_pinned_hash_matches_the_shipped_server():
    assert hashlib.sha256(SERVER.read_bytes()).hexdigest() == scoping.READFS_SHA256


def test_server_is_package_data():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"data/**"' in pyproject and not (REPO / "scripts" / "goose-readfs-mcp.py").exists()


def test_launch_uses_isolated_python_and_the_right_path():
    assert scoping.readfs_extension()["args"] == ["-I", "-S", scoping.READFS_SCRIPT, "--root", "."]
    assert scoping.readfs_extension(protected=True)["args"] == [
        "-I", "-S", scoping.READFS_PROTECTED_PATH, "--root", "."]


def test_install_location_follows_the_switch():
    def shipped(switch):
        return dict(GooseAdapter().extra_output_files(_manifest(switch=switch)))
    off, on = shipped(False), shipped(True)
    assert f"../../{scoping.READFS_SCRIPT}" in off and f"../../{scoping.READFS_PROTECTED_PATH}" not in off
    assert f"../../{scoping.READFS_PROTECTED_PATH}" in on and f"../../{scoping.READFS_SCRIPT}" not in on
    assert hashlib.sha256(on[f"../../{scoping.READFS_PROTECTED_PATH}"].encode()).hexdigest() == scoping.READFS_SHA256


def test_render_fails_closed_on_a_mismatched_server(monkeypatch):
    from agentteams.frameworks import goose_docs

    tampered = lambda: "# not the shipped server\n"  # noqa: E731
    monkeypatch.setattr(goose_mod, "_readfs_mcp_content", tampered)
    monkeypatch.setattr(goose_docs, "_readfs_mcp_content", tampered)   # the pinned helper reads through goose_docs
    with pytest.raises(ValueError, match="pinned hash"):
        GooseAdapter().extra_output_files(_manifest(switch=True))
    GooseAdapter().extra_output_files(_manifest(switch=False))          # without the switch: unchanged behaviour


def test_runner_checks_the_installed_copy(tmp_path):
    R.check_installed_readfs(tmp_path)                                  # absent: no Goose readers, passes
    installed = tmp_path / scoping.READFS_PROTECTED_PATH
    installed.parent.mkdir(parents=True)
    installed.write_bytes(SERVER.read_bytes())
    R.check_installed_readfs(tmp_path)                                  # the shipped copy passes
    installed.write_text(SERVER.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    with pytest.raises(R.RunnerError, match="doesn't match"):
        R.check_installed_readfs(tmp_path)
    installed.unlink()
    installed.symlink_to(SERVER)
    with pytest.raises(R.RunnerError, match="not a regular file"):
        R.check_installed_readfs(tmp_path)
    installed.unlink()
    installed.parent.rmdir()
    elsewhere = tmp_path / "writable"
    elsewhere.mkdir()
    (elsewhere / Path(scoping.READFS_PROTECTED_PATH).name).write_bytes(SERVER.read_bytes())
    installed.parent.symlink_to(elsewhere)                              # .agentteams/bin -> agent-writable folder
    with pytest.raises(R.RunnerError, match="is a symlink"):
        R.check_installed_readfs(tmp_path)


def test_python3_inside_the_workspace_is_flagged(tmp_path, monkeypatch):
    import shutil

    system = tmp_path / "system" / "python3"
    system.parent.mkdir()
    system.write_text("")
    project = tmp_path / "project"
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(system)          # a normal virtualenv: a symlink OUT of the project
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    monkeypatch.setattr(shutil, "which", lambda name: str(venv_bin / "python3"))
    assert "inside the project" in standalone_modes.python3_in_workspace_note(project)
    # a venv whose interpreter path is outside but whose pyvenv.cfg sits in the project tree is caught too
    elsewhere = project / "tools" / "env"
    (elsewhere / "bin").mkdir(parents=True)
    (elsewhere / "pyvenv.cfg").write_text("home = /usr/bin\n")
    monkeypatch.setattr(shutil, "which", lambda name: str(elsewhere / "bin" / "python3"))
    assert standalone_modes.python3_in_workspace_note(project)
    monkeypatch.setattr(shutil, "which", lambda name: str(system))
    assert standalone_modes.python3_in_workspace_note(project) is None


def test_stale_scripts_copy_is_reported(tmp_path):
    assert standalone_modes.stale_readfs_copy_note(tmp_path) is None
    (tmp_path / scoping.READFS_SCRIPT).parent.mkdir(parents=True)
    (tmp_path / scoping.READFS_SCRIPT).write_text("x")
    assert "delete it" in standalone_modes.stale_readfs_copy_note(tmp_path)


def test_claude_edit_tools_are_denied_the_control_plane_under_the_switch():
    import json

    from agentteams.frameworks import _sandbox_emit as se

    example = json.dumps({"hooks": {}, "_comment": []})
    on = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=True))["permissions"]["deny"]
    off = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=False))["permissions"]["deny"]
    assert "Edit(/.agentteams/**)" in on and "Edit(/.agentteams/**)" not in off


def test_server_denies_the_control_plane(tmp_path):
    spec = importlib.util.spec_from_file_location("readfs_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = mod.Workspace(tmp_path)
    for rel in (".agentteams/queue-results/x.json", ".agentteams-queue/requests/y.json",
                ".agentteams/proposal-ledger.jsonl", ".agentteams/bin/goose-readfs-mcp.py"):
        assert ws.denied(rel), rel
    assert not ws.denied("src/app.py")


def test_claude_read_tools_are_denied_the_queue_and_control_plane_under_the_switch():
    """R1 (@security C4): the queue and results briefly hold raw dispatch nonces."""
    import json

    from agentteams.frameworks import _sandbox_emit as se

    example = json.dumps({"hooks": {}, "_comment": []})
    on = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=True))["permissions"]["deny"]
    off = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=False))["permissions"]["deny"]
    for rule in ("Read(/.agentteams/**)", "Read(/.agentteams-queue/**)"):
        assert rule in on and rule not in off


# --- bl48-17: an exclusive team's in-project protected_read_paths reach readfs as --deny globs ---------------

def _exclusive_manifest(protected: list[str], *, profile: str = "exclusive") -> dict:
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}],
            "goose_tool_scoping": "grant", "write_policy": "orchestrator-only", "privilege_profile": profile,
            "protected_read_paths": protected}
    return analyze.build_manifest(desc, framework="goose")


def _rendered_recipes(manifest: dict) -> dict[str, str]:
    """A reader agent's recipe, rendered through the adapter (the path that emits readfs)."""
    agent = '---\nname: Reader\ndescription: reads\ntools: ["read", "search"]\n---\n# Reader\n\nReads.\n'
    return {"reader.yaml": GooseAdapter().render_agent_file(agent, "reader", manifest)}


def test_deny_globs_keep_only_project_relative_entries():
    m = {"privilege_profile": "exclusive",
         "protected_read_paths": ["secrets/", "./notes/private", "~/x", "/abs/y", "../sib", "a/../b", ".", "q*", ""]}
    assert scoping.readfs_deny_globs(m) == ("notes/private", "notes/private/**", "secrets", "secrets/**")
    assert scoping.readfs_deny_globs({**m, "privilege_profile": "confined"}) == ()
    assert scoping.readfs_deny_globs({"privilege_profile": "exclusive"}) == ()


def test_exclusive_relative_protected_path_becomes_deny_glob(tmp_path):
    ext = scoping.readfs_extension(protected=True, deny=scoping.readfs_deny_globs(
        _exclusive_manifest(["secrets/"])))
    assert ext["args"][-4:] == ["--deny", "secrets", "--deny", "secrets/**"]
    spec = importlib.util.spec_from_file_location("readfs_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "a.txt").write_text("s")
    (tmp_path / "public.txt").write_text("p")
    deny = tuple(ext["args"][i + 1] for i, a in enumerate(ext["args"]) if a == "--deny")
    ws = mod.Workspace(tmp_path, deny)
    for rel in ("secrets", "secrets/a.txt"):
        with pytest.raises(mod.ReadfsError, match="deny list"):
            ws.resolve(rel)
    ws.resolve("public.txt")


def test_rendered_recipes_carry_the_deny_and_the_audit_requires_it():
    from agentteams.audit_agent_contract import _check_write_policy

    manifest = _exclusive_manifest(["secrets/"])
    recipes = {p: c for p, c in _rendered_recipes(manifest).items() if "agentteams_readfs" in c}
    assert recipes
    deny = scoping.readfs_deny_globs(manifest)
    for text in recipes.values():
        assert '"--deny"' in text and '"secrets/**"' in text
    name, text = next(iter(recipes.items()))
    ok = _check_write_policy({name: text}, agent_ext=".yaml", framework="goose", enabled=True, readfs_deny=deny)
    assert not [f for f in ok if "agentteams_readfs" in f.description]
    stripped = text.replace('      - "--deny"\n      - "secrets/**"\n', "")
    bad = _check_write_policy({name: stripped}, agent_ext=".yaml", framework="goose", enabled=True, readfs_deny=deny)
    assert any("doesn't launch the shipped server" in f.description for f in bad)


def test_confined_team_readfs_args_unchanged():
    # Negative control: no exclusive profile, no deny: the args are exactly the pre-bl48-17 ones.
    manifest = _exclusive_manifest(["secrets/"], profile="confined")
    assert scoping.readfs_deny_globs(manifest) == ()
    texts = _rendered_recipes(manifest).values()
    assert texts and all("agentteams_readfs" in t and "--deny" not in t for t in texts)


def test_skipped_protected_paths_are_reported_at_render(capsys):
    # Review follow-up: an entry readfs cannot deny is named on stderr, never dropped silently.
    assert scoping.readfs_deny_skipped({"privilege_profile": "exclusive", "protected_read_paths": [
        "ok", "/abs", "~/x", "a/../b", ".", "q*"]}) == ("/abs", "~/x", "a/../b", ".", "q*")
    manifest = _exclusive_manifest(["secrets/", "/abs/in/project", "~/notes"])
    GooseAdapter().extra_output_files(manifest)
    err = capsys.readouterr().err
    assert "2 protected_read_path(s) NOT denied" in err
    assert "/abs/in/project" in err and "~/notes" in err and "secrets/" not in err


def test_no_skip_notice_when_every_entry_is_passed(capsys):
    # Negative control: project-relative entries only -> no notice.
    manifest = _exclusive_manifest(["secrets/", "notes/private"])
    assert scoping.readfs_deny_skipped(manifest) == ()
    GooseAdapter().extra_output_files(manifest)
    assert "NOT denied" not in capsys.readouterr().err


def test_one_component_deny_also_blocks_a_deeper_same_named_folder(tmp_path):
    spec = importlib.util.spec_from_file_location("readfs_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for rel in ("src/secrets/a.txt", "src/secretsx/a.txt"):
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text("s")
    ws = mod.Workspace(tmp_path, scoping.readfs_deny_globs(_exclusive_manifest(["secrets"])))
    with pytest.raises(mod.ReadfsError, match="deny list"):
        ws.resolve("src/secrets/a.txt")
    ws.resolve("src/secretsx/a.txt")      # negative control: only the exact name is denied
