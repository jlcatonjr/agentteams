"""Tests for agentteams.env_hygiene — env files are gitignored and kept out of Docker build contexts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import env_hygiene as eh
from agentteams.scan import CONDITIONAL_PASS, HALT, PASS, verdict_for_findings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str], *, commit: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    if commit and files:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.mark.parametrize("name", [".env", ".env.production", ".env.local", ".env.proxy", "prod.env",
                                  "deploy/systemd/app.env", "frontend/.env"])
def test_is_env_file_matches(name):
    assert eh.is_env_file(name)


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template", "dev.example.env",
                                  "app.env.template", ".envrc", "environment.yml", "env", ".env_vars.py",
                                  "requirements.txt", ".env.production.example"])
def test_is_env_file_exempts(name):
    assert not eh.is_env_file(name)


def test_clean_repo_passes(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n", "README.md": "x\n"})
    (repo / ".env").write_text("SECRET=1\n")
    assert eh.audit_repo(repo) == []


def test_tracked_env_file_is_high(tmp_path):
    repo = _repo(tmp_path, {".env.production": "VITE_X=1\n", ".env.example": "VITE_X=\n"})
    findings = eh.audit_repo(repo)
    assert [(f.file, f.category) for f in findings] == [(".env.production", "tracked-env-file")]
    assert verdict_for_findings(findings) == HALT


def test_unignored_env_file_is_high(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n"})
    (repo / ".env.proxy").write_text("USER=x\n")
    (repo / ".env").write_text("ignored\n")
    findings = eh.audit_repo(repo)
    assert [(f.file, f.category) for f in findings] == [(".env.proxy", "unignored-env-file")]


def test_dockerfile_copy_dot_without_dockerignore_is_medium(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n", "Dockerfile": "FROM python\nCOPY . .\n",
                            ".dockerignore": ".git\n"})
    findings = eh.audit_repo(repo)
    assert [(f.file, f.category, f.severity) for f in findings] == [("Dockerfile", "dockerignore-env", "medium")]
    assert verdict_for_findings(findings) == CONDITIONAL_PASS


@pytest.mark.parametrize("patterns", ["*env*", ".env\n.env.*\n*.env", "**/.env*\n**/*.env", "/.env*\n*.env"])
def test_dockerignore_patterns_that_exclude_every_env_shape(tmp_path, patterns):
    repo = _repo(tmp_path, {".gitignore": ".env\n", "Dockerfile": "FROM python\nCOPY --chown=a . /app\n",
                            ".dockerignore": f"{patterns}\n"})
    assert eh.audit_repo(repo) == []


@pytest.mark.parametrize("patterns", [".env", ".env\n*.env", ".env*"])
def test_dockerignore_covering_only_some_env_shapes_is_flagged(tmp_path, patterns):
    # `.env` alone still lets .env.production (or app.env) into the image.
    repo = _repo(tmp_path, {".gitignore": ".env\n", "Dockerfile": "COPY . /app\n", ".dockerignore": f"{patterns}\n"})
    assert [f.category for f in eh.audit_repo(repo)] == ["dockerignore-env"]


@pytest.mark.parametrize("dockerfile", ['COPY [".", "/app"]\n', "COPY * /app/\n", "COPY --chown=a:b \\\n    . /app\n",
                                        "add ./ /srv\n"])
def test_whole_context_copy_forms_are_detected(tmp_path, dockerfile):
    repo = _repo(tmp_path, {".gitignore": ".env\n", "Dockerfile": "FROM python\n" + dockerfile})
    assert [f.category for f in eh.audit_repo(repo)] == ["dockerignore-env"]


def test_buildkit_per_dockerfile_ignore_is_honoured(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n", "docker/api/Dockerfile": "COPY . /app\n",
                            "docker/api/Dockerfile.dockerignore": ".env*\n*.env\n"})
    assert eh.audit_repo(repo) == []


def test_dockerignore_next_to_nested_dockerfile_is_not_the_context(tmp_path):
    # The context is the repo root, so a plain .dockerignore beside a nested Dockerfile doesn't count,
    # and --fix writes the root one.
    repo = _repo(tmp_path, {".gitignore": ".env\n", "docker/api/Dockerfile": "COPY . /app\n",
                            "docker/api/.dockerignore": ".env*\n*.env\n"})
    findings = eh.audit_repo(repo)
    assert [f.category for f in findings] == ["dockerignore-env"]
    assert ".dockerignore" in eh.plan_fix(repo, findings).appends


def test_dockerfile_copying_named_files_only_is_fine(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n", "Dockerfile": "FROM python\nCOPY requirements.txt .\n"})
    assert eh.audit_repo(repo) == []


def test_fix_is_dry_run_and_append_only(tmp_path, capsys):
    repo = _repo(tmp_path, {".gitignore": "node_modules/\n.env\n", "Dockerfile": "COPY . .\n",
                            ".dockerignore": ".git\n"})
    (repo / ".env.proxy").write_text("USER=x\n")
    assert eh.main([str(repo), "--fix"]) == 1
    out = json.loads(capsys.readouterr().out)
    fix = out["repos"][0]["fix"]
    assert fix["dry_run"] and fix["written"] == []
    assert ".env" not in fix["appends"][".gitignore"]          # already present
    assert (repo / ".gitignore").read_text() == "node_modules/\n.env\n"  # dry run wrote nothing


def test_fix_execute_appends_and_clears_findings(tmp_path, capsys):
    repo = _repo(tmp_path, {".gitignore": "node_modules/\n.env\n", "Dockerfile": "COPY . .\n",
                            ".dockerignore": ".git\n"})
    (repo / ".env.proxy").write_text("USER=x\n")
    assert eh.main([str(repo), "--fix", "--execute"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == PASS
    gi = (repo / ".gitignore").read_text()
    assert gi.startswith("node_modules/\n.env\n") and ".env.*" in gi and "!.env.example" in gi
    assert "**/.env" in (repo / ".dockerignore").read_text()


def test_fix_never_untracks(tmp_path, capsys):
    repo = _repo(tmp_path, {".env.production": "VITE_X=1\n"})
    eh.main([str(repo), "--fix", "--execute"])
    capsys.readouterr()
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout
    assert ".env.production" in tracked
    assert [f.category for f in eh.audit_repo(repo)] == ["tracked-env-file"]


def test_fix_refuses_file_with_uncommitted_changes(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n"})
    (repo / ".gitignore").write_text(".env\nlocal-edit\n")
    (repo / ".env.proxy").write_text("USER=x\n")
    plan = eh.plan_fix(repo, eh.audit_repo(repo))
    assert ".gitignore" in plan.refused and plan.appends == {}


def test_execute_requires_fix():
    with pytest.raises(SystemExit) as exc:
        eh.main([".", "--execute"])
    assert exc.value.code == 2


def test_not_a_repo_raises(tmp_path):
    with pytest.raises(RuntimeError):
        eh.audit_repo(tmp_path)


def test_main_reports_unauditable_repo_and_continues(tmp_path, capsys):
    good = _repo(tmp_path, {".gitignore": ".env\n"})
    bad = tmp_path / "not-a-repo"
    bad.mkdir()
    assert eh.main([str(bad), str(good)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [r["verdict"] for r in out["repos"]] == [HALT, PASS]
    assert out["repos"][0]["findings"][0]["category"] == "audit-error"


def test_fix_hardens_gitignore_even_without_findings(tmp_path):
    repo = _repo(tmp_path, {".gitignore": ".env\n"})
    assert eh.audit_repo(repo) == []
    plan = eh.plan_fix(repo, [])
    assert plan.appends == {".gitignore": [".env.*", "*.env", "!.env.example", "!.env.sample", "!.env.template",
                                           "!*.example.env", "!*.sample.env", "!*.template.env"]}


def test_fixed_gitignore_agrees_with_is_env_file_on_placeholders(tmp_path, capsys):
    repo = _repo(tmp_path, {".gitignore": ""})
    eh.main([str(repo), "--fix", "--execute"])
    capsys.readouterr()
    for name in [".env.example", "dev.example.env", "fixtures/test.sample.env", ".env", ".env.prod", "app.env"]:
        ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--no-index", name]).returncode == 0
        assert ignored == eh.is_env_file(name), name
