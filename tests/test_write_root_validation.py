"""Follow-up #2 (brief-json-privilege-tamper): brief write roots cannot silently widen the sandbox.

Covers the pinned chokepoint (``frameworks/_write_roots.py``) in every emitter, the goose Linux
runner's shell-injection fix, the operator acceptance gate (``cli/write_root_policy.py``), the
widening notice, and the argv-only ``--accept-write-root`` flag.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks._goose_sandbox_emit import _build_linux_goose_runner, _runner_root_expr
from agentteams.frameworks._sandbox_emit import (
    OPERATOR_EXAMPLE_PATHS,
    SIGNING_KEY_DIR,
    _build_sandbox_block,
    permission_deny_rules,
)
from agentteams.frameworks._write_roots import (
    PROJECT_ROOT_KEY,
    PROVENANCE_KEY,
    _SIGNING_KEY_DIR,
    is_external_root,
    project_root_of,
    provenance_of,
    root_problem,
    unaccepted_external_roots,
    validate_write_roots,
)
from agentteams.cli import write_root_policy as policy

REPO = Path(__file__).resolve().parents[1]
HOME = os.path.expanduser("~")

REFUSED = [
    "", " ", "/", "//", "~", "~/", HOME, os.path.dirname(HOME),
    "..", "../..", "./a/../../..",
    "~/.config", "~/.config/agentteams", "~/.config/agentteams/keys/x", "~/.ssh", "~/.ssh/k",
    "~/.bashrc", "~/.local/bin", "~/.config/systemd/user", "~/.claude",
    ".claude", ".claude/agents/references", "./.claude/", "src/../.claude",
    ".github/agents/references", ".github/hooks", ".codex/config.toml", ".goose", ".git",
    ".git/hooks", "sandbox", ".agentteams", "~bob/x",
    'x"y', "x$(id)", "x`id`", "x\ny", "x\\y", "a;b", "a|b", "a*", "a?", "a[1]", "{a,b}", " src",
]
ALLOWED = [".", "./src", "docs", "src/sub", "/srv/repos/x", "~/scratch", "~/.config/mytool",
           "../sibling", ".github/workflows"]


@pytest.mark.parametrize("root", REFUSED)
def test_hard_bans(root, tmp_path):
    proj = str(tmp_path / "proj") if root not in ("..", "../..", "./a/../../..") else HOME + "/x/proj"
    assert root_problem(root, proj, platform="linux") is not None, root


@pytest.mark.parametrize("root", ALLOWED)
def test_allowed_shapes(root, tmp_path):
    assert root_problem(root, str(tmp_path / "w" / "proj"), platform="linux") is None, root


def test_project_root_itself_and_its_ancestor(tmp_path):
    proj = str(tmp_path / "proj")
    assert root_problem(proj, proj) is None  # absolute == project == "."
    assert root_problem(str(tmp_path), proj) == "an ancestor of the project"
    assert root_problem(proj + "/.claude", proj).startswith("inside the project control plane")


def test_darwin_is_case_insensitive(tmp_path):
    assert root_problem(".Claude", str(tmp_path), platform="darwin")
    assert root_problem(".Claude", str(tmp_path), platform="linux") is None


def test_symlinked_root_is_judged_by_its_target(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "link").symlink_to(HOME)
    assert root_problem("link", str(proj)) == "the home directory or an ancestor of it"


def test_signing_key_constant_is_single_sourced():
    assert _SIGNING_KEY_DIR == SIGNING_KEY_DIR


def test_project_root_key_is_honoured_only_as_a_path(tmp_path):
    assert project_root_of({PROJECT_ROOT_KEY: str(tmp_path)}) is None  # JSON input: ignored
    assert project_root_of({PROJECT_ROOT_KEY: tmp_path}) == str(tmp_path)
    assert provenance_of({PROVENANCE_KEY: [["~/x", "signed capability grant"]]}) == {}


# --- every emitter refuses -----------------------------------------------------------------------

@pytest.mark.parametrize("root", ["~", "x$(touch /tmp/pwn)", ".claude", "~/.ssh"])
def test_claude_block_refuses(root):
    with pytest.raises(ValueError, match="refused sandbox write root"):
        _build_sandbox_block([root], platform="linux")


def test_claude_block_refuses_a_project_ancestor_only_with_the_project_root(tmp_path):
    proj = tmp_path / "proj"
    _build_sandbox_block([str(tmp_path)], platform="linux")  # no project root: check skipped
    with pytest.raises(ValueError, match="ancestor of the project"):
        _build_sandbox_block([str(tmp_path)], platform="linux", project_root=str(proj))


@pytest.mark.parametrize("root", ["~", "x$(touch /tmp/pwn)", ".git/hooks"])
def test_goose_linux_runner_refuses(root):
    with pytest.raises(ValueError, match="refused sandbox write root"):
        _build_linux_goose_runner({"workspace_write_roots": [root]})


def test_goose_seatbelt_refuses(monkeypatch):
    from agentteams.frameworks import _goose_sandbox_emit as g
    from agentteams.frameworks.registry import FRAMEWORKS

    monkeypatch.setattr(g.sys, "platform", "darwin")
    monkeypatch.setattr(g, "is_sandbox_capable", lambda fw: True)
    manifest = {"framework": "goose", "privilege_profile": "confined", "workspace_write_roots": ["~/.ssh"]}
    with pytest.raises(ValueError, match="refused sandbox write root"):
        FRAMEWORKS["goose"]().extra_output_files(manifest)


def test_goose_runner_renders_and_quotes_every_root_shape():
    assert _runner_root_expr(".") == '"$REPO_ROOT"'
    assert _runner_root_expr("src") == '"$REPO_ROOT"/src'
    assert _runner_root_expr("/srv/repos/x") == "/srv/repos/x"  # never $REPO_ROOT//srv/...
    assert _runner_root_expr("~/scratch") == '"$HOME"/scratch'
    assert _runner_root_expr("a b") == '"$REPO_ROOT"/\'a b\''
    text = _build_linux_goose_runner({"workspace_write_roots": ["src", "/srv/repos/x"]})
    assert '--writable "$REPO_ROOT"/src --writable /srv/repos/x' in text


def test_goose_runner_exclude_paths_are_quoted_and_unsafe_ones_skipped():
    text = _build_linux_goose_runner({"privilege_profile": "exclusive",
                                      "protected_read_paths": ["/a b", "x$(id)", "/ok"]})
    assert "--exclude '/a b' --exclude /ok" in text and "x$(id)" not in text
    assert "1 protected_read_path(s) SKIPPED" in text


def test_goose_runner_never_executes_an_injected_root(tmp_path):
    """Even if validation were bypassed, the quoted expression is inert in bash."""
    marker = tmp_path / "pwned"
    expr = '"$REPO_ROOT"/' + shlex.quote(f"x$(touch {marker})")
    subprocess.run(["bash", "-c", f'REPO_ROOT=/nowhere; printf "%s\\n" {expr}'], check=True,
                   capture_output=True)
    assert not marker.exists()


def test_multi_sync_projection_refuses_a_banned_root(tmp_path):
    from agentteams import multi_sync

    with pytest.raises(ValueError, match="refused sandbox write root"):
        multi_sync._check_projected_write_roots(
            tmp_path, {"privilege_profile": "confined", "workspace_write_roots": ["~"]},
            ["claude"], None)


# --- external roots and acceptance ---------------------------------------------------------------

def test_external_classification(tmp_path):
    proj = str(tmp_path / "proj")
    assert is_external_root("~/scratch", proj) and is_external_root("../sib", proj)
    assert is_external_root("/srv/x", proj) and not is_external_root(proj + "/src", proj)
    assert not is_external_root("src", proj) and not is_external_root(".", proj)


def test_unaccepted_external_roots(tmp_path):
    proj = str(tmp_path)
    roots = [".", "src", "~/scratch", "/srv/x", "../sib"]
    assert unaccepted_external_roots(roots, project_root=proj, baseline=[], accepted=[]) == [
        "~/scratch", "/srv/x", "../sib"]
    assert unaccepted_external_roots(roots, project_root=proj, baseline=["~/scratch/"],
                                     accepted=["/srv/x"], signed=["../sib"]) == []


def _manifest(tmp_path: Path, roots: list[str], **extra) -> dict:
    m = {"privilege_profile": "confined", "workspace_write_roots": roots, **extra}
    policy.begin(m, tmp_path)
    return m


def _enforce(m: dict, framework: str = "claude", accepted=None, grants=(), coord=()):
    policy.enforce(m, framework_id=framework, brief_roots=list(m.get("workspace_write_roots") or []),
                   grant_roots=list(grants), coordination_roots=list(coord), accepted=accepted,
                   confined=True)


def test_new_external_root_needs_acceptance(tmp_path, capsys):
    m = _manifest(tmp_path, [".", "~/scratch"])
    with pytest.raises(policy.WriteRootPolicyError, match="--accept-write-root '~/scratch'"):
        _enforce(m)
    assert "SANDBOX WIDENING: allowWrite gains '~/scratch'" in capsys.readouterr().err


def test_accepted_external_root_passes_with_a_notice(tmp_path, capsys):
    m = _manifest(tmp_path, [".", "~/scratch"])
    _enforce(m, accepted=["~/scratch/"])
    err = capsys.readouterr().err
    assert "source: brief workspace_write_roots; EXTERNAL" in err
    assert dict(m[PROVENANCE_KEY])["~/scratch"] == "brief workspace_write_roots"


def test_live_claude_baseline_grandfathers_but_only_for_claude(tmp_path, capsys):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"sandbox": {"enabled": True, "filesystem": {"allowWrite": [".", "~/scratch"]}}}))
    (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps(
        {"sandbox": {"filesystem": {"allowWrite": ["/srv/local-only"]}}}))
    _enforce(_manifest(tmp_path, [".", "~/scratch"]))  # in the protected baseline: no flag needed
    assert "SANDBOX WIDENING" not in capsys.readouterr().err
    with pytest.raises(policy.WriteRootPolicyError):  # goose: no baseline
        _enforce(_manifest(tmp_path, [".", "~/scratch"]), framework="goose")
    with pytest.raises(policy.WriteRootPolicyError):  # settings.local.json is not a baseline
        _enforce(_manifest(tmp_path, [".", "/srv/local-only"]))


def test_signed_grant_roots_skip_acceptance_but_not_the_bans(tmp_path):
    m = _manifest(tmp_path, [".", "/srv/granted"])
    _enforce(m, grants=["/srv/granted"])
    with pytest.raises(policy.WriteRootPolicyError, match="refused"):
        _enforce(_manifest(tmp_path, [".", "~/.ssh"]), grants=["~/.ssh"])


def test_coordination_roots_are_attributed(tmp_path, capsys):
    m = _manifest(tmp_path, [".", "../sib"])
    with pytest.raises(policy.WriteRootPolicyError, match=r"coordination_write_roots \(unsigned\)"):
        _enforce(m, coord=["../sib"])


def test_inert_when_not_confined(tmp_path):
    m = {"workspace_write_roots": ["~"]}
    policy.begin(m, tmp_path)
    policy.enforce(m, framework_id="claude", brief_roots=["~"], grant_roots=[],
                   coordination_roots=[], accepted=None, confined=False)


@pytest.mark.parametrize("key", ["accept_write_root", "accept_write_roots"])
def test_acceptance_is_never_read_from_the_brief(tmp_path, key):
    with pytest.raises(policy.WriteRootPolicyError, match="operator argv only"):
        policy.begin({key: ["~/scratch"]}, tmp_path)


def test_input_supplied_transient_keys_are_dropped(tmp_path):
    m = {PROVENANCE_KEY: (("~", "signed capability grant"),), PROJECT_ROOT_KEY: "/"}
    policy.begin(m, tmp_path)
    assert PROVENANCE_KEY not in m and m[PROJECT_ROOT_KEY] == tmp_path.resolve()


def test_flag_is_argv_only():
    from agentteams.cli.parser import _build_parser

    parser = _build_parser()
    assert parser.fromfile_prefix_chars is None
    ns = parser.parse_args(["--accept-write-root", "~/a", "--accept-write-root", "/b"])
    assert ns.accept_write_root == ["~/a", "/b"]
    env = {**os.environ, "AGENTTEAMS_ACCEPT_WRITE_ROOT": "~/x", "ACCEPT_WRITE_ROOT": "~/x"}
    out = subprocess.run([sys.executable, "-c",
                          "from agentteams.cli.parser import _build_parser as b;"
                          "print(b().parse_args([]).accept_write_root)"],
                         env={**env, "PYTHONPATH": str(REPO)}, capture_output=True, text=True)
    assert out.stdout.strip() == "None"


# --- end to end through build_team ---------------------------------------------------------------

def _generate(project: Path, roots: list[str], monkeypatch, *extra: str) -> int:
    import build_team
    from agentteams.cli import security_gate

    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *a, **k: None)
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    brief["privilege_profile"] = "confined"
    brief["workspace_write_roots"] = roots
    path = project / "brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    return build_team.main(["--description", str(path), "--framework", "claude", "--output",
                            str(project / ".claude/agents"), "--yes", "--no-scan",
                            "--security-offline", *extra])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="claude sandbox emitted on linux")
def test_generation_refuses_then_accepts(tmp_path, monkeypatch, capsys):
    p = (tmp_path / "proj").resolve()
    p.mkdir()
    assert _generate(p, ["~"], monkeypatch) == 1
    assert not (p / ".claude/settings.hooks.example.json").exists()
    assert _generate(p, [".", "~/agt-accept-test"], monkeypatch) == 1
    assert "--accept-write-root '~/agt-accept-test'" in capsys.readouterr().err
    assert _generate(p, [".", "~/agt-accept-test"], monkeypatch,
                     "--accept-write-root", "~/agt-accept-test") == 0
    example = json.loads((p / ".claude/settings.hooks.example.json").read_text())
    assert example["sandbox"]["filesystem"]["allowWrite"] == [".", "~/agt-accept-test"]
    for key in (PROJECT_ROOT_KEY, PROVENANCE_KEY):
        for f in p.rglob("*.json"):
            assert key not in f.read_text(encoding="utf-8", errors="ignore"), f


def test_operator_examples_are_edit_denied():
    rules = permission_deny_rules("claude")
    for rel in OPERATOR_EXAMPLE_PATHS:
        assert f"Edit(/{rel})" in rules
    assert "Edit(/.github/agents/_build-description.json)" not in rules  # decision (b): deferred


# --- @security implementation review (step 16) conditions -----------------------------------------

def test_symlinked_in_project_root_is_external(tmp_path):
    """Condition 1: `data -> /elsewhere` looks internal lexically; classify by its target."""
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (proj / "data").symlink_to(outside)
    (proj / "real").mkdir()
    assert is_external_root("data", str(proj))
    assert is_external_root(str(proj / "data"), str(proj))
    assert not is_external_root("real", str(proj))
    assert unaccepted_external_roots(["data"], project_root=str(proj), baseline=[], accepted=[]) == ["data"]


@pytest.mark.parametrize("root", ["~/.local", "~/.config", "~/.local/share/..", "~/.config/"])
def test_ancestors_of_every_protected_home_path_are_refused(root):
    """Condition 2: `~/.local` holds `~/.local/bin` (PATH persistence)."""
    assert root_problem(root, "/srv/proj") is not None


def test_external_baseline_root_is_still_printed(tmp_path, capsys):
    """Condition 4: a planted baseline must not silence an external root."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"sandbox": {"filesystem": {"allowWrite": [".", "/srv/x"]}}}))
    _enforce(_manifest(tmp_path, [".", "/srv/x"]))
    assert "keeps external root '/srv/x'" in capsys.readouterr().err


_LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
_CLAUDE_DIR_PROBE = r"""
import errno, os, sys
os.chdir(sys.argv[1])
def attempt(label, fn):
    try:
        fn(); print(label, "OK")
    except OSError as e:
        print(label, errno.errorcode.get(e.errno, e.errno))
attempt("settings", lambda: open(".claude/settings.json", "w").write("{}"))
attempt("local", lambda: open(".claude/settings.local.json", "w").write("{}"))
attempt("agent", lambda: open(".claude/agents/x.md", "w").write("x"))
attempt("refs", lambda: open(".claude/agents/references/log.csv", "a").write("r\n"))
attempt("example", lambda: open(".claude/settings.hooks.example.json", "w").write("{}"))
attempt("runner", lambda: open(".goose/confined-run.example.sh", "w").write("x"))
attempt("rename", lambda: os.rename(".claude", ".claude.moved"))
attempt("root", lambda: open("rootfile", "w").write("x"))
"""


@pytest.mark.skipif(not (sys.platform.startswith("linux") and __import__("shutil").which("bwrap")),
                    reason="Linux + bubblewrap only")
def test_launcher_makes_all_of_dot_claude_and_the_goose_runner_read_only(tmp_path):
    """Condition 3: the whole-`.claude` ro-bind covers files under the rw ancestor self-binds."""
    p = (tmp_path / "proj").resolve()
    (p / ".claude" / "agents" / "references").mkdir(parents=True)
    (p / ".claude" / "settings.json").write_text("{\"keep\": 1}")
    (p / ".claude" / "settings.hooks.example.json").write_text("{}")
    (p / ".goose").mkdir()
    (p / ".goose" / "confined-run.example.sh").write_text("orig")
    res = subprocess.run(["bash", str(_LAUNCHER), "--scratch", str(p), "--", sys.executable, "-c",
                          _CLAUDE_DIR_PROBE, str(p)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = dict(line.split(" ", 1) for line in res.stdout.splitlines())
    for key in ("settings", "local", "agent", "refs", "example", "runner"):
        assert out[key] == "EROFS", (key, out)
    assert out["rename"] == "EBUSY" and out["root"] == "OK"
    assert (p / ".claude" / "settings.json").read_text() == "{\"keep\": 1}"
    assert not (p / ".claude" / "settings.local.json").exists()


# --- @adversarial step-17 findings ---------------------------------------------------------------

def test_shell_expanded_tilde_acceptance_matches(tmp_path):
    """B2: bash turns `--accept-write-root ~/x` into `$HOME/x`; it must still match `~/x`."""
    m = _manifest(tmp_path, [".", "~/agt-x"])
    _enforce(m, accepted=[os.path.join(HOME, "agt-x")])


def test_printed_remedy_is_shell_quoted(tmp_path):
    m = _manifest(tmp_path, [".", "/srv/a b"])
    with pytest.raises(policy.WriteRootPolicyError, match=r"--accept-write-root '/srv/a b'"):
        _enforce(m)


def test_generate_threads_the_project_root_to_the_emitters(tmp_path, monkeypatch):
    """R3: the CLI sets the transient project root, so a project-ancestor root is refused."""
    p = (tmp_path / "proj").resolve()
    p.mkdir()
    assert _generate(p, [".", str(tmp_path)], monkeypatch, "--accept-write-root", str(tmp_path)) == 1


def test_multi_sync_threads_the_project_root(tmp_path, monkeypatch):
    from agentteams import multi_sync
    from agentteams.frameworks.registry import FRAMEWORKS

    seen = {}
    real = FRAMEWORKS["claude"].extra_output_files

    def spy(self, manifest):
        seen["root"] = manifest.get(PROJECT_ROOT_KEY)
        return real(self, manifest)

    monkeypatch.setattr(FRAMEWORKS["claude"], "extra_output_files", spy)
    multi_sync._emit_privilege_artifacts(tmp_path, "claude", {"privilege_profile": "confined"},
                                         dry_run=True)
    assert seen["root"] == tmp_path.resolve()
