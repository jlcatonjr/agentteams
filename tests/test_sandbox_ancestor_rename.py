"""F-4: an agent that may write the project root must not be able to RENAME an ancestor of a
write-denied control-plane path away and plant a replacement tree (PR-B, 2026-09-30).

Three layers, each labelled honestly:

* **Emitted config** (always runs): the Claude block write-denies the whole ``.claude`` dir without
  touching ``permissions.deny``; the goose Seatbelt profile denies ``file-write-unlink`` /
  ``file-write-create`` on each ancestor literal (UNVERIFIED: no macOS host has run it).
* **Mechanism** (raw bubblewrap; skipped unless bwrap works on this host): the mount layouts the
  fixes produce refuse the rename (EBUSY), a hard link out of the protected mount (EXDEV) and a
  delete of it, while normal writes still succeed; bwrap aborts on a missing bind source. The
  ``unshare -rm`` + umount probe skips where nested user namespaces are unavailable (Ubuntu's
  ``kernel.apparmor_restrict_unprivileged_userns=1``).
* **Launcher argv** (``confine-run.sh --check``; Linux + bwrap): membership and ORDER of the
  control-plane binds (rw roots, ancestor self-binds, read-only binds, masks), refusal of a
  symlinked path, and die-not-mkdir on a missing ``--protect``.

The product arm (Claude Code's own bwrap argv) is the opt-in shim test in
``test_os_sandbox_product_enforcement.py``. Status: mechanism-verified, product-unverified.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks._goose_sandbox_emit import (
    _build_seatbelt_profile,
    _seatbelt_ancestor_rename_rules,
)
from agentteams.frameworks._sandbox_emit import (
    _build_sandbox_block,
    control_plane_ancestors,
    framework_config_dir,
    permission_deny_rules,
    protected_write_paths,
)

LAUNCHER = (Path(__file__).resolve().parents[1] / "agentteams" / "templates" / "universal"
            / "sandbox" / "confine-run.sh")
_BWRAP = shutil.which("bwrap")


def _bwrap_usable() -> bool:
    if not (sys.platform.startswith("linux") and _BWRAP):
        return False
    probe = subprocess.run(
        [_BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user",
         "true"], capture_output=True, text=True,
    )
    return probe.returncode == 0


_HAS_BWRAP = _bwrap_usable()
_bwrap = pytest.mark.skipif(not _HAS_BWRAP, reason="bubblewrap not usable on this host")


# --- emitted config ---------------------------------------------------------------------------

def test_claude_block_write_denies_the_framework_config_dir():
    for roots, deny_read in ((None, None), (["./src"], None), (["."], ["~/.ssh"])):
        deny = _build_sandbox_block(roots, deny_read, platform="linux")["filesystem"]["denyWrite"]
        assert deny == [*protected_write_paths("claude"), ".claude"]
    assert framework_config_dir("claude") == ".claude"
    assert framework_config_dir("goose") == ".goose"


def test_config_dir_deny_does_not_touch_permissions_deny():
    # Still no Edit(/.claude/**) (that would block agents' own Write-tool edits under .claude).
    # PR-D adds the rosters, the settings files and hooks/** (which subsumes the single hook rule).
    assert permission_deny_rules("claude") == [
        "Read(~/.config/agentteams/keys/**)",
        "Read(~/.config/agentteams/*.pem)",
        "Edit(/.claude/agents/references/agent-privilege.json)",
        "Edit(/.claude/agents/references/authorized-verify-keys/**)",
        "Edit(/.claude/agents/references/security-approvers.txt)",
        "Edit(/.claude/agents/references/authorized-managers.txt)",
        "Edit(/.claude/agents/references/management-authority.json)",
        "Edit(/references/security-approvers.txt)",
        "Edit(/.claude/settings.json)",
        "Edit(/.claude/settings.local.json)",
        "Edit(/.claude/hooks/**)",
    ]
    assert "Edit(/.claude/**)" not in permission_deny_rules("claude")


def test_control_plane_ancestors_are_top_down_and_deduplicated():
    assert control_plane_ancestors(protected_write_paths("claude")) == (
        ".claude", ".claude/agents", ".claude/agents/references", ".claude/hooks",
    )
    assert control_plane_ancestors(["a/b/c", "a/b/d", "e"]) == ("a", "a/b")


def test_seatbelt_denies_rename_of_every_control_plane_ancestor_after_the_workspace_allow():
    prof = _build_seatbelt_profile(["."])
    anc = control_plane_ancestors([*protected_write_paths("goose"), ".goose/sandbox.sb"])
    assert ".goose/recipes/references" in anc and ".claude" in anc
    rule = prof.index("(deny file-write-unlink file-write-create")
    assert prof.index("(allow file-write*") < rule
    for a in anc:
        lit = f'(literal (string-append (param "WORKSPACE_ROOT") "/{a}"))'
        assert lit in prof[rule:], a
    assert "UNVERIFIED" in prof[rule - 300:rule]


def test_seatbelt_ancestor_rules_fail_closed_on_an_unrepresentable_ancestor():
    with pytest.raises(ValueError):
        _seatbelt_ancestor_rename_rules(('.goose/"x',))
    assert _seatbelt_ancestor_rename_rules(()) == []


def test_launcher_control_plane_list_is_locked_to_the_python_source():
    from agentteams.frameworks._sandbox_emit import (
        GRANT_ROSTER_PROJECT_REL,
        TEAM_MARKER_REL,
        governed_roster_paths,
    )

    text = LAUNCHER.read_text(encoding="utf-8")
    body = re.search(r"CONTROL_PLANE_REL=\(([^)]*)\)", text).group(1)
    expected = {*protected_write_paths("claude"), *protected_write_paths("goose"), ".goose/sandbox.sb",
                *governed_roster_paths("claude"), *governed_roster_paths("goose"),
                GRANT_ROSTER_PROJECT_REL}
    assert set(body.split()) == expected
    assert re.search(r"^TEAM_MARKER_REL=(\S+)$", text, re.M).group(1) == TEAM_MARKER_REL
    teams = re.search(r"TEAM_DIRS_REL=\(([^)]*)\)", text).group(1).split()
    assert teams == [".claude/agents", ".goose/recipes"]


# --- mechanism: raw bubblewrap ----------------------------------------------------------------

_PROBE = r"""
import errno, os, sys
root = sys.argv[1]
def attempt(label, fn):
    try:
        fn(); print(label, "OK")
    except OSError as e:
        print(label, errno.errorcode.get(e.errno, e.errno))
os.chdir(root)
for d in sys.argv[2:]:
    attempt("rename:" + d, lambda d=d: os.rename(d, d + ".moved"))
sw = ".claude/agents/references/agent-privilege.json"
attempt("link", lambda: os.link(sw, "planted"))
attempt("unlink", lambda: os.unlink(sw))
attempt("overwrite", lambda: open(sw, "w").write("{}"))
attempt("local", lambda: open(".claude/settings.local.json", "w").write("{}"))
attempt("inside", lambda: open(".claude/agents/references/log.csv", "a").write("row\n"))
attempt("root", lambda: open("rootfile", "w").write("x"))
"""


def _project(tmp_path: Path, *, team: bool = True) -> Path:
    """A sandboxed Claude team: the control plane, the roster stubs and (``team``) the build-log."""
    from agentteams.frameworks._sandbox_emit import TEAM_MARKER_REL, governed_roster_paths

    p = (tmp_path / "proj").resolve()
    for rel in (*protected_write_paths("claude"), *governed_roster_paths("claude")):
        q = p / rel
        if q.name == "authorized-verify-keys":
            q.mkdir(parents=True)
            (q / "README.md").write_text("x\n", encoding="utf-8")
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text("{}\n", encoding="utf-8")
    if team:
        (p / ".claude/agents" / TEAM_MARKER_REL).write_text("{}\n", encoding="utf-8")
    return p


def _probe(argv_prefix: list[str], project: Path, dirs: list[str]) -> dict[str, str]:
    res = subprocess.run(
        [*argv_prefix, sys.executable, "-c", _PROBE, str(project), *dirs],
        capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"},
    )
    assert res.returncode == 0, res.stderr
    return dict(line.split(" ", 1) for line in res.stdout.splitlines())


def _raw(project: Path, *binds: str) -> list[str]:
    return [_BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user", "--bind", str(project), str(project), *binds, "--"]


@_bwrap
def test_mechanism_claude_layout_ro_config_dir_refuses_rename_and_plant(tmp_path):
    """The Claude fix: rw project bind, THEN a read-only bind of `.claude` (captured order)."""
    p = _project(tmp_path)
    cfg = str(p / ".claude")
    out = _probe(_raw(p, "--ro-bind", cfg, cfg), p,
                 [".claude", ".claude/agents", ".claude/agents/references", ".claude/hooks"])
    assert out["rename:.claude"] == "EBUSY"
    for d in (".claude/agents", ".claude/agents/references", ".claude/hooks"):
        assert out[f"rename:{d}"] in {"EBUSY", "EROFS"}, (d, out)
    assert out["link"] == "EXDEV"
    assert out["unlink"] != "OK" and out["overwrite"] != "OK"
    assert out["local"] == "EROFS"  # the planted settings.local.json route is closed
    assert out["root"] == "OK"
    assert not (p / ".claude.moved").exists()


@_bwrap
def test_mechanism_without_the_fix_the_rename_succeeds(tmp_path):
    """Negative control: with only the project bind, `.claude` is renameable (the F-4 route)."""
    p = _project(tmp_path)
    out = _probe(_raw(p), p, [".claude"])
    assert out["rename:.claude"] == "OK"


@_bwrap
def test_mechanism_confine_run_layout_every_ancestor_is_ebusy_writes_still_work(tmp_path):
    """The launcher itself, end to end: ancestors EBUSY, switch read-only, writes inside allowed."""
    p = _project(tmp_path)
    ancestors = list(control_plane_ancestors(protected_write_paths("claude")))
    launcher = ["bash", str(LAUNCHER), "--scratch", str(p), "--"]
    out = _probe(launcher, p, ancestors)
    for d in ancestors:
        assert out[f"rename:{d}"] == "EBUSY", (d, out)
    assert out["link"] == "EXDEV"
    assert out["unlink"] == "EBUSY" and out["overwrite"] == "EROFS"
    assert out["inside"] == "OK" and out["root"] == "OK"


@_bwrap
def test_mechanism_protect_of_the_config_dir_makes_it_read_only_over_submounts(tmp_path):
    p = _project(tmp_path)
    launcher = ["bash", str(LAUNCHER), "--scratch", str(p), "--protect", str(p / ".claude"), "--"]
    out = _probe(launcher, p, [".claude", ".claude/agents"])
    assert out["rename:.claude"] == "EBUSY"
    assert out["inside"] == "EROFS" and out["local"] == "EROFS"
    assert out["root"] == "OK"


@_bwrap
def test_mechanism_bwrap_aborts_on_a_missing_bind_source(tmp_path):
    missing = str(tmp_path / "does-not-exist")
    res = subprocess.run(
        [_BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-user",
         "--ro-bind", missing, missing, "true"], capture_output=True, text=True,
    )
    assert res.returncode != 0
    assert not Path(missing).exists()


@_bwrap
def test_mechanism_nested_userns_cannot_umount_the_protected_mount(tmp_path):
    p = _project(tmp_path)
    cfg = str(p / ".claude")
    base = _raw(p, "--ro-bind", cfg, cfg)
    if shutil.which("unshare") is None:
        pytest.skip("unshare not installed")
    probe = subprocess.run([*base, "unshare", "-rm", "true"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"nested user namespaces unavailable here: {probe.stderr.strip()[:120]}")
    res = subprocess.run([*base, "unshare", "-rm", "sh", "-c", f"umount '{cfg}'"],
                         capture_output=True, text=True)
    assert res.returncode != 0, "a nested namespace unmounted the protected .claude mount"


# --- launcher argv (confine-run.sh --check) ----------------------------------------------------

def _check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(LAUNCHER), *args, "--check", "--", "true"],
                          capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})


def _effective(stdout: str) -> list[str]:
    line = next(ln for ln in stdout.splitlines() if ln.strip().startswith("effective"))
    return line.split(":", 1)[1].split()


def _binds(argv: list[str]) -> list[tuple[str, str]]:
    out = []
    for i, tok in enumerate(argv):
        if tok == "--":
            break
        if tok in {"--bind", "--ro-bind"}:
            out.append((tok, argv[i + 2]))  # the destination
        elif tok == "--tmpfs":
            out.append((tok, argv[i + 1]))
    return out


_linux_bwrap = pytest.mark.skipif(not (sys.platform.startswith("linux") and _BWRAP),
                                  reason="confine-run's Linux branch needs bwrap on PATH")


@_linux_bwrap
def test_launcher_argv_order_rw_roots_then_ancestors_then_ro_then_masks(tmp_path):
    p = _project(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("s\n", encoding="utf-8")
    r = _check("--scratch", str(p), "--exclude", str(secret))
    assert r.returncode == 0, r.stderr
    binds = _binds(_effective(r.stdout))
    pos = {b: i for i, b in enumerate(binds)}
    root = pos[("--bind", str(p))]
    anc = [pos[("--bind", str(p / a))] for a in control_plane_ancestors(protected_write_paths("claude"))]
    ro = [pos[("--ro-bind", str(p / c))] for c in protected_write_paths("claude")]
    assert ("--ro-bind", str(p / ".claude/agents/references/build-log.json")) in pos
    mask = pos[("--ro-bind", str(secret))]
    assert root < min(anc) and max(anc) < min(ro) and max(ro) < mask
    assert anc == sorted(anc)  # top-down


@_linux_bwrap
def test_launcher_skips_absent_frameworks_and_binds_nothing_extra(tmp_path):
    p = (tmp_path / "empty").resolve()
    p.mkdir()
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr
    assert "control-plane (ro): <none>" in r.stdout
    assert not (p / ".claude").exists() and not (p / ".goose").exists()


@_linux_bwrap
def test_launcher_refuses_a_symlinked_control_plane_ancestor(tmp_path):
    p = (tmp_path / "proj").resolve()
    elsewhere = tmp_path / "elsewhere" / "hooks"
    elsewhere.mkdir(parents=True)
    (elsewhere / "constitutional-gate.py").write_text("x\n", encoding="utf-8")
    (p / ".claude").mkdir(parents=True)
    (p / ".claude" / "hooks").symlink_to(elsewhere)
    r = _check("--scratch", str(p))
    assert r.returncode == 2
    assert "symlink" in r.stderr


@_linux_bwrap
def test_launcher_missing_protect_path_dies_and_is_never_created(tmp_path):
    p = (tmp_path / "proj").resolve()
    p.mkdir()
    missing = p / ".claude"
    r = _check("--scratch", str(p), "--protect", str(missing))
    assert r.returncode == 2 and "never created" in r.stderr
    assert not missing.exists()


@_linux_bwrap
def test_launcher_covers_writable_and_coord_roots_too(tmp_path):
    scratch = (tmp_path / "scratch").resolve()
    scratch.mkdir()
    sib = _project(tmp_path / "sib")
    r = _check("--scratch", str(scratch), "--coord-root", str(sib))
    assert r.returncode == 0, r.stderr
    binds = _binds(_effective(r.stdout))
    assert ("--bind", str(sib / ".claude")) in binds
    assert ("--ro-bind", str(sib / ".claude/agents/references/agent-privilege.json")) in binds


@_linux_bwrap
@pytest.mark.parametrize("missing", [
    ".claude/agents/references/agent-privilege.json",
    ".claude/agents/references/authorized-verify-keys",
    ".claude/hooks/constitutional-gate.py",
    ".claude/agents/references/security-approvers.txt",
    ".claude/agents/references/authorized-managers.txt",
    ".claude/agents/references/management-authority.json",
])
def test_launcher_refuses_a_missing_control_plane_entry_when_the_team_exists(tmp_path, missing):
    """@security (PR-B review): with the team present, an absent entry sits under a rename-locked but
    WRITABLE parent, so a confined process could create a `false` switch, a store with a planted
    .pub.pem, or a gate hook. The launcher must refuse, and never create the path."""
    import shutil as _sh
    p = _project(tmp_path)
    target = p / missing
    (_sh.rmtree if target.is_dir() else os.unlink)(target)
    r = _check("--scratch", str(p))
    assert r.returncode == 2, r.stdout
    assert "missing although" in r.stderr
    assert not target.exists()


@_linux_bwrap
def test_launcher_allows_a_bare_config_dir_without_a_team(tmp_path):
    """A plain `.claude/` (e.g. only settings.local.json) is not a native team: nothing is required."""
    p = (tmp_path / "bare").resolve()
    (p / ".claude").mkdir(parents=True)
    (p / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    r = _check("--scratch", str(p))
    assert r.returncode == 0, r.stderr


def test_two_framework_project_warns_that_goose_is_not_denied(tmp_path, capsys):
    """@security PR-B condition B: a documented manual step also needs a generate-time warning."""
    from agentteams.cli.generate_helpers import _warn_goose_under_claude_sandbox

    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    m = {"framework": "claude", "host_features": ["claude:sandbox"]}
    assert _warn_goose_under_claude_sandbox(m, agents) is False
    (tmp_path / ".goose" / "recipes").mkdir(parents=True)
    assert _warn_goose_under_claude_sandbox(m, agents) is True
    assert '".goose"' in capsys.readouterr().err
    assert _warn_goose_under_claude_sandbox({"framework": "claude"}, agents) is False
