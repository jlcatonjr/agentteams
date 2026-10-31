"""Wave-3 @security S1/S3/S4: one credential-name deny list, a bounded scan of granted mounts, case-folded protection.

* S1: ``wasm_config`` (brief read paths) and ``runner_wasm`` (runner jails) refuse exactly the same credential names,
  from the one list in the import-free ``_sandbox_profiles`` (``.env.example`` / ``.sample`` / ``.template`` allowed).
* S3: a granted read directory holding a credential-named entry anywhere below it is refused when the grant is built
  (``sandbox_state.grant_from_tokens``: brief grants and ``runner_wasm.build_grant``) and again at
  ``wasm_exec.materialize``; a tree past the entry or depth cap is refused.
* S4: protected-path comparisons are case-folded (``.GITHUB`` is ``.github`` on darwin's default filesystem).
"""

from __future__ import annotations

import ast
import os
import types
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import runner_wasm as RW
from agentteams import sandbox_state as ss
from agentteams import wasm_config as wc
from agentteams import wasm_exec as W
from agentteams.frameworks._write_roots import control_plane_of
from tests._wasm_helpers import PROBE, FakeResolver, project

#: The union the review named, plus the earlier wasm_config-only names.
DENIED = [".kube", ".docker", "credentials", "credentials.json", ".pgpass", ".htpasswd", "prod.tfstate",
          "prod.tfstate.backup", "x.tfvars", ".env", ".envrc", ".env.local", "prod.env", "id_rsa", "id_ed25519_sk",
          "server.pem", "tls.key", "c.p12", "c.pfx", "k.jks", "k.keystore", "db.kdbx", ".ssh", ".gnupg", ".aws",
          ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".SSH", "Credentials.TXT", ".ENV"]
ALLOWED = [".env.example", ".env.sample", ".env.template", "docs", "src", "README.md", "envoy", "identity.py"]
SLUGS = ("primary-producer",)


def _brief_refuses(name: str) -> bool:
    try:
        wc.validate_read_path(f"data/{name}", "p", project_root=None)
    except ValueError as exc:
        return "credential" in str(exc)
    return False


def _runner_refuses(name: str) -> bool:
    try:
        RW._check_rel("a", "wasm.read", f"data/{name}", (".git",), lambda rel: None)
    except SP.ConfinementError as exc:
        return "secret" in str(exc)
    return False


# --- S1: one list --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", DENIED)
def test_both_consumers_refuse_every_credential_name(name: str) -> None:
    assert SP.credential_name(name)
    assert _brief_refuses(name) and _runner_refuses(name)


@pytest.mark.parametrize("name", ALLOWED)
def test_both_consumers_allow_the_same_non_credentials(name: str) -> None:
    assert not SP.credential_name(name)
    assert not _brief_refuses(name) and not _runner_refuses(name)


def test_the_runner_lists_are_the_shared_lists() -> None:
    assert RW.DENY_SUFFIXES is SP.CREDENTIAL_SUFFIXES and RW.DENY_NAMES is SP.CREDENTIAL_NAMES
    assert RW.DENY_PREFIXES is SP.CREDENTIAL_PREFIXES and RW.DENY_INFIXES is SP.CREDENTIAL_INFIXES
    assert RW.DENY_ALLOWED is SP.CREDENTIAL_ALLOWED and RW.DENY_PARTS == RW.VCS_PARTS + SP.CREDENTIAL_PARTS


def test_wasm_config_has_no_private_credential_list() -> None:
    src = Path(wc.__file__).read_text(encoding="utf-8")
    assert "_SECRET_SEGMENT_RE" not in src and "credential_name" in src


def test_sandbox_profiles_stays_import_free() -> None:
    """The bundle exec-loads _sandbox_profiles, so the shared list may not import agentteams."""
    tree = ast.parse(Path(SP.__file__).read_text(encoding="utf-8"))
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m == "agentteams" or m.startswith("agentteams.") for m in mods)


# --- S3: the bounded scan ------------------------------------------------------------------------------------------

def _tree(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


def test_scan_finds_nested_names_and_symlinks(tmp_path: Path) -> None:
    _tree(tmp_path, "a/b/c/.env", "a/ok.txt", "a/.env.example", "z/deploy/id_rsa")
    (tmp_path / "a" / "creds-link").symlink_to(tmp_path / "a" / "ok.txt")
    (tmp_path / "a" / ".npmrc").symlink_to(tmp_path / "a" / "ok.txt")
    assert SP.credential_scan(str(tmp_path)) == ["a/.npmrc", "a/b/c/.env", "z/deploy/id_rsa"]


def test_scan_refuses_past_the_caps(tmp_path: Path) -> None:
    _tree(tmp_path, *(f"f{i}.txt" for i in range(5)), "d1/d2/d3/x.txt")
    with pytest.raises(SP.ConfinementError, match="stopped at 3 entries"):
        SP.credential_scan(str(tmp_path), max_entries=3)
    with pytest.raises(SP.ConfinementError, match="stopped at depth 2"):
        SP.credential_scan(str(tmp_path), max_depth=2)
    assert SP.credential_scan(str(tmp_path), max_entries=9, max_depth=3) == []


def test_grant_from_tokens_refuses_a_credential_under_the_mount(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    _tree(root, "docs/guide.md", "docs/deploy/.env")
    with pytest.raises(ValueError, match=r"credential-named entries \(deploy/\.env\)"):
        ss.grant_from_tokens("a", ["wasm:a:read:docs"], W.DEFAULT_LIMITS, project_root=root)
    # A recorded state is bookkeeping, not a mount: parsing it does not scan.
    g = ss.grant_from_tokens("a", ["wasm:a:read:docs"], W.DEFAULT_LIMITS, project_root=root, check_credentials=False)
    assert [m.guest for m in g.mounts] == ["/project/docs"]


def test_grant_from_tokens_refuses_when_the_scan_cap_is_hit(tmp_path: Path, monkeypatch) -> None:
    root = Path(os.path.realpath(tmp_path))
    _tree(root, *(f"docs/f{i}" for i in range(4)))
    monkeypatch.setattr(SP.credential_scan, "__kwdefaults__", {"max_entries": 2, "max_depth": 32})
    with pytest.raises(ValueError, match="stopped at 2 entries"):
        ss.grant_from_tokens("a", ["wasm:a:read:docs"], W.DEFAULT_LIMITS, project_root=root)


def test_brief_grant_is_refused_at_build_time(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    _tree(root, "docs/a.md", "docs/k8s/.kube/config")
    manifest = {"agent_slug_list": list(SLUGS),
                "wasm_sandbox": wc.normalize_wasm_sandbox(
                    {"enabled": True, "agents": {SLUGS[0]: {"read_paths": ["docs"]}}},
                    agent_slugs=SLUGS).to_record()}
    from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY

    manifest[PROJECT_ROOT_KEY] = root
    with pytest.raises(ValueError, match=r"wasm_sandbox\.agents\.primary-producer\.read_paths: .*k8s/\.kube"):
        wc.wasm_state_fields(manifest)


def test_runner_grant_is_refused_at_build_time(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    _tree(root, "tools/s.py", "data/backup/credentials.csv")
    jail = RW.validate_entry("x", {"sandbox": "wasm", "wasm": {"languages": ["python"], "read": ["tools", "data"]}},
                             lambda rel: None, protected_rel=(".git",))
    with pytest.raises(RW.RunnerWasmError, match=r"credential-named entries \(backup/credentials\.csv\)"):
        RW.build_grant(root, "x", jail, timeout_s=30)
    (root / "data" / "backup" / "credentials.csv").unlink()
    grant = RW.build_grant(root, "x", jail, timeout_s=30)
    assert [m.guest for m in grant.mounts] == ["/project/data", "/project/tools"]


def _profiles() -> types.ModuleType:
    mod = types.ModuleType("fake_profiles")
    for name in ("seatbelt_profile", "bwrap_argv", "snapshot_tree", "remove_tree", "refuse_credentials",
                 "ConfinementError"):
        setattr(mod, name, getattr(SP, name))
    mod.available = lambda: None  # type: ignore[attr-defined]
    return mod


def _wasmtime(tmp_path: Path) -> str:
    path = tmp_path / "bin" / "wasmtime"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return os.path.realpath(path)


def test_materialize_scans_again_before_the_run(tmp_path: Path) -> None:
    """A credential file that appears after the grant was built is still refused at materialize (S3)."""
    root = project(tmp_path)
    grant = W.Grant((W.Mount(str(root / "data"), "/project/data", "ro"),), W.DEFAULT_LIMITS)
    prepared = W.prepare(PROBE, grant=grant)
    (root / "data" / "nested").mkdir()
    (root / "data" / "nested" / "site.pem").write_text("-----BEGIN")
    with pytest.raises(W.WasmExecError, match=r"/project/data holds credential-named entries \(nested/site\.pem\)"):
        W.materialize(prepared, [], project_root=root, os_confine="off", resolver=FakeResolver(_wasmtime(tmp_path)),
                      profiles=_profiles())
    (root / "data" / "nested" / "site.pem").unlink()
    launch = W.materialize(prepared, [], project_root=root, os_confine="off",
                           resolver=FakeResolver(_wasmtime(tmp_path)), profiles=_profiles())
    launch.cleanup()


# --- S4: case-folded protected paths -------------------------------------------------------------------------------

def test_runner_check_rel_refuses_an_uppercase_protected_top() -> None:
    from agentteams.confinement import PROTECTED_REL

    with pytest.raises(SP.ConfinementError, match="control plane"):
        RW._check_rel("a", "wasm.read", ".GITHUB/workflows", PROTECTED_REL, lambda rel: None)
    with pytest.raises(SP.ConfinementError, match="control plane"):
        RW._check_rel("a", "wasm.read", ".Git", PROTECTED_REL, control_plane_of)


def test_materialize_refuses_an_uppercase_protected_mount(tmp_path: Path) -> None:
    root = project(tmp_path)
    upper = root / ".GITHUB"
    if not upper.exists():
        upper.mkdir()
    m = W.Mount(str(upper), "/project/x", "ro")
    with pytest.raises(W.WasmExecError, match="overlaps the protected"):
        W._check_mount_host(str(root), m, None)
    sub = root / ".Agentteams-Queue"
    sub.mkdir()
    with pytest.raises(W.WasmExecError, match="overlaps the protected"):
        W._check_mount_host(str(root), W.Mount(str(sub), "/project/q", "ro"), None)
