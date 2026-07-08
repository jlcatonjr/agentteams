"""F-CODEIDX Phase C — end-to-end emit/refresh/query in a project fixture, plus
the optional (off-by-default) pre-commit warm-up. Exercises the real artifact
helpers against a temp project tree, not the agentteams repo itself (plan m3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentteams import git_hooks as gh
from agentteams.cli import artifacts


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "auth.py").write_text(
        "import jsonschema\n\n"
        "def login(user, password):\n"
        "    '''Authenticate a user login against the store.'''\n"
        "    return jsonschema.validate(user, password)\n"
    )
    (root / "scripts" / "deploy.sh").write_text(
        "#!/bin/sh\ndeploy_release() {\n  echo deploying release\n}\n"
    )
    return root


def _manifest(root: Path) -> dict:
    return {"project_name": "proj", "framework": "copilot-vscode",
            "existing_project_path": str(root)}


# ------------------------------ downstream fixture flow ------------------------------

def test_emit_refresh_query_roundtrip(tmp_path):
    pytest.importorskip("jsonschema")
    root = _make_project(tmp_path)
    manifest = _manifest(root)

    cache_dir = artifacts._write_code_index(manifest, root)
    assert cache_dir == root / "references" / "code-index"
    assert (cache_dir / "manifest.json").exists()
    assert (cache_dir / "local.json").exists()

    data = artifacts._read_code_index(root)
    assert set(data["partitions"]) >= {"local", "api-modules", "api-docs"}
    local = data["partitions"]["local"]
    assert local["N"] >= 2  # auth.py + deploy.sh
    assert all(d["source_kind"] == "local-script" for d in local["documents"])

    rc = artifacts._run_query_code_index(manifest, root, "user login", 5, "lexical", "local")
    assert rc == 0


def test_no_op_refresh_leaves_local_partition_byte_identical(tmp_path):
    pytest.importorskip("jsonschema")
    root = _make_project(tmp_path)
    manifest = _manifest(root)
    artifacts._write_code_index(manifest, root)
    local_path = root / "references" / "code-index" / "local.json"
    first = local_path.read_bytes()
    # Nothing changed → the content-fingerprint skip must preserve the file.
    artifacts._write_code_index(manifest, root)
    assert local_path.read_bytes() == first


def test_query_missing_cache_raises_controlled_error(tmp_path):
    root = _make_project(tmp_path)
    with pytest.raises(artifacts.CodeIndexError):
        artifacts._read_code_index(root)


def test_code_index_extra_dirs_picked_up(tmp_path):
    pytest.importorskip("jsonschema")
    root = _make_project(tmp_path)
    extra = root / "vendor_scripts"
    extra.mkdir()
    (extra / "special.py").write_text("def special_marker_fn():\n    return 42\n")
    manifest = _manifest(root)
    manifest["code_index_extra_dirs"] = ["vendor_scripts"]
    sources = artifacts._code_index_sources(manifest, root)
    assert any(p.name == "special.py" for p in sources)


def test_code_index_extra_dirs_rejects_absolute_and_traversal(tmp_path):
    root = _make_project(tmp_path)
    manifest = _manifest(root)
    manifest["code_index_extra_dirs"] = ["/etc", "../../elsewhere", str(tmp_path)]
    sources = artifacts._code_index_sources(manifest, root)
    # none of the escapes contribute files outside the project root
    for p in sources:
        assert str(p).startswith(str(root))


# ------------------------------ optional pre-commit warm-up ------------------------------

def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_refresh_code_index_never_stages(tmp_path):
    pytest.importorskip("jsonschema")
    root = _make_project(tmp_path)
    _git_init(root)
    result = gh.refresh_code_index(root)
    assert result.wrote
    # The cache must never be in the git index (it is a gitignored local cache).
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root, capture_output=True, text=True,
    ).stdout
    assert "code-index" not in staged


def test_hook_block_optin_adds_no_git_add_clause():
    default = gh._render_hook_block("/x")
    optin = gh._render_hook_block("/x", code_index_hook=True)
    assert "refresh-code-index" not in default          # off by default
    assert "refresh-code-index" in optin                # opt-in present
    # The warm-up clause must not stage the gitignored cache.
    clause_start = optin.index("refresh-code-index")
    clause = optin[optin.rindex("if printf", 0, clause_start):
                   optin.index("fi\n", clause_start)]
    assert "git -C" not in clause and "add " not in clause
    # ...and it must be sequential to (not a replacement of) the arch guard.
    assert "architecture-graph.svg" in optin


def test_install_hook_with_code_index_is_idempotent(tmp_path):
    root = _make_project(tmp_path)
    _git_init(root)
    hooks_dir = root / ".git" / "hooks"
    r1 = gh.install_pre_commit_hook(root, hooks_dir=hooks_dir, code_index_hook=True)
    assert r1.action in ("created", "updated")
    body = (hooks_dir / "pre-commit").read_text()
    assert "refresh-code-index" in body
    r2 = gh.install_pre_commit_hook(root, hooks_dir=hooks_dir, code_index_hook=True)
    assert r2.action == "unchanged"  # idempotent
