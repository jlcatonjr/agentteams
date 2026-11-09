"""Branch model (CI-cost plan, 2026-11-04): brief resolution, rendered rows, branch-tool states, workflow triggers.

The integration-branch model merges feature branches locally into an integration branch and advances the default
branch only by a release PR. These tests pin: a trunk team renders byte-identically (every owned placeholder is
``""``); an integration team gets the rows and the reference; the branch tools never propose the integration
branch for deletion (after a release merge it is an ancestor of the default branch, which would otherwise make
it "merged") and keep branches merged only into it; vulnscan fixes fall back to it; and this repository's own
workflows never trigger on it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import branch_cleanup as bc
from agentteams import branch_inventory as bi
from agentteams import branch_model as bm
from agentteams.render import resolve_placeholders
from tests.test_branch_lifecycle import NOW, FakeAPI, Repo, inventory, rec, repo  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "agentteams" / "templates"
OWNED_TEMPLATES = {
    "universal/github-workflows-merge.reference.template.md": ["BRANCH_MODEL_MERGE_ROWS"],
    "universal/git-operations.template.md": ["BRANCH_MODEL_GITOPS_REFS", "BRANCH_MODEL_GITOPS_CONTRACT"],
    "domain/pr-manager.template.md": ["BRANCH_MODEL_PR_REFS", "BRANCH_MODEL_DISPOSITION_ROWS"],
    "universal/branch-lifecycle.reference.template.md": ["BRANCH_MODEL_LIFECYCLE_ROWS"],
}


# ---------------------------------------------------------------------------
# Brief resolution
# ---------------------------------------------------------------------------


def test_absent_and_trunk_blocks_resolve_to_none():
    assert bm.resolve({}) is None
    assert bm.resolve({"git_workflow": {"branch_model": "trunk"}}) is None


def test_integration_block_gets_defaults():
    assert bm.resolve({"git_workflow": {"branch_model": "integration"}}) == {
        "branch_model": "integration", "integration_branch": "dev", "release_cadence": "milestone"}


@pytest.mark.parametrize("block", [
    "integration",
    {"branch_model": "gitflow"},
    {"branch_model": "integration", "integration_branch": "main"},
    {"branch_model": "integration", "integration_branch": "-x"},
    {"branch_model": "integration", "integration_branch": "a..b"},
    {"branch_model": "integration", "release_cadence": "hourly"},
])
def test_malformed_blocks_are_refused(block):
    with pytest.raises(ValueError):
        bm.resolve({"git_workflow": block})


def test_schema_declares_the_block_with_the_module_enums():
    schema = json.loads((ROOT / "agentteams/schemas/project-description.schema.json").read_text())
    block = schema["properties"]["git_workflow"]
    assert tuple(block["properties"]["branch_model"]["enum"]) == bm.MODELS
    assert tuple(block["properties"]["release_cadence"]["enum"]) == bm.CADENCES
    assert block["properties"]["integration_branch"]["default"] == bm.DEFAULT_INTEGRATION_BRANCH


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template, names", OWNED_TEMPLATES.items())
def test_trunk_render_is_byte_identical_to_the_template_without_the_placeholders(template, names):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    manifest: dict = {}
    bm.populate_placeholders(manifest)
    rendered = resolve_placeholders(text, manifest["auto_resolved_placeholders"])
    stripped = text
    for name in names:
        assert text.count("{" + name + "}") == 1, f"{name} must sit once at the end of an existing line"
        assert re.search(r"[^\n]\{" + name + r"\}\n", text), f"{name} must end an existing line, not stand alone"
        stripped = stripped.replace("{" + name + "}", "")
    assert rendered == stripped


@pytest.mark.parametrize("template, names", OWNED_TEMPLATES.items())
def test_integration_render_adds_rows_naming_the_branch(template, names):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    manifest = {"git_workflow": bm.resolve({"git_workflow": {"branch_model": "integration",
                                                              "integration_branch": "develop"}})}
    bm.populate_placeholders(manifest)
    rendered = resolve_placeholders(text, manifest["auto_resolved_placeholders"])
    assert "`develop`" in rendered
    assert not any("{" + n + "}" in rendered for n in names)


def test_integration_reference_template_uses_only_known_placeholders():
    text = (TEMPLATES / bm.REFERENCE_TEMPLATE).read_text(encoding="utf-8")
    tokens = set(re.findall(r"\{([A-Z][A-Z0-9_]+)\}", text))
    assert tokens <= {"PROJECT_NAME", *bm.VALUE_PLACEHOLDERS}
    assert "Never squash or rebase the release PR" in text


def test_integration_reference_requires_protecting_the_integration_branch():
    """Row 675: a release merge deletes an unprotected integration branch when delete_branch_on_merge is on."""
    text = (TEMPLATES / bm.REFERENCE_TEMPLATE).read_text(encoding="utf-8")
    fenced = text.split("<!-- AGENTTEAMS:BEGIN integration_branch", 1)[1].split("<!-- AGENTTEAMS:END", 1)[0]
    assert "**`{INTEGRATION_BRANCH}` must itself be a protected branch**" in fenced
    for needle in ("delete_branch_on_merge", "allow_deletions", "allow_force_pushes", "enforce_admins",
                   "refs/heads/{INTEGRATION_BRANCH}"):
        assert needle in fenced, needle


def test_analyze_adds_the_reference_and_record_only_for_integration():
    from agentteams import analyze, ingest

    base = ingest.load(ROOT / "examples" / "software-project" / "brief.json", scan_project=False)
    trunk = analyze.build_manifest(dict(base))
    assert "git_workflow" not in trunk
    assert bm.REFERENCE_PATH not in {f["path"] for f in trunk["output_files"]}
    assert all(trunk["auto_resolved_placeholders"][n] == "" for n in bm.ROW_PLACEHOLDERS)
    manifest = analyze.build_manifest({**base, "git_workflow": {"branch_model": "integration"}})
    assert manifest["git_workflow"]["integration_branch"] == "dev"
    assert bm.REFERENCE_PATH in {f["path"] for f in manifest["output_files"]}


# ---------------------------------------------------------------------------
# Branch tools
# ---------------------------------------------------------------------------


def _integration_repo(repo: Repo) -> None:
    """Create ``dev`` from ``main`` and push it (each test then merges into it as it needs)."""
    repo.git("checkout", "-q", "-b", "dev", "main")
    repo.git("push", "-q", "-u", "origin", "dev")
    repo.git("checkout", "-q", "main")


def test_integration_branch_is_never_merged_even_after_a_release(repo):
    _integration_repo(repo)
    repo.branch("feat/a", {"a.txt": "a\n"})
    repo.git("checkout", "-q", "dev")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge branch 'feat/a'", "feat/a")
    repo.git("push", "-q", "origin", "dev")
    repo.git("checkout", "-q", "main")
    repo.merge_no_ff("dev")  # the release
    without = inventory(repo, integration_branch="")
    assert rec(without, "dev")["state"] == "merged"  # the hazard this state closes
    inv = inventory(repo, integration_branch="dev")
    for ref_type in ("local", "remote"):
        r = rec(inv, "dev", ref_type)
        assert (r["state"], r["action"]) == ("integration", "keep")
    assert "dev" not in {i["branch"] for i in inv["plan"]["items"]}
    assert rec(inv, "feat/a")["state"] == "merged"  # released: deletable as usual
    assert inv["meta"]["integration_branch"] == "dev"


def test_branch_merged_only_into_integration_is_integrated_and_kept(repo):
    _integration_repo(repo)
    repo.branch("feat/b", {"b.txt": "b\n"}, date="2026-08-01T12:00:00+00:00")  # stale by date
    repo.git("checkout", "-q", "dev")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge branch 'feat/b'", "feat/b")  # local only: dev not pushed
    repo.git("checkout", "-q", "main")
    inv = inventory(repo, integration_branch="dev")
    for ref_type in ("local", "remote"):
        r = rec(inv, "feat/b", ref_type)
        assert (r["state"], r["action"]) == ("integrated", "keep")
    assert bi.summary_line(inv) == "clean"


def test_integration_branch_is_read_from_the_brief(repo):
    _integration_repo(repo)
    (repo.work / "brief.json").write_text(json.dumps({"git_workflow": {"branch_model": "integration"}}))
    assert bm.integration_branch_from_project(repo.work) == "dev"
    inv = inventory(repo)  # integration_branch None → read from the brief
    assert rec(inv, "dev")["state"] == "integration"


def test_malformed_block_raises_and_the_inventory_refuses(repo):
    (repo.work / "brief.json").write_text(json.dumps({"git_workflow": {"branch_model": "integration",
                                                                       "integration_branch": "main"}}))
    with pytest.raises(ValueError, match="brief.json"):
        bm.integration_branch_from_project(repo.work)
    with pytest.raises(bi.InventoryError, match="integration branch"):
        inventory(repo)


def test_symlinked_or_outside_brief_is_ignored(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"git_workflow": {"branch_model": "integration"}}))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "brief.json").symlink_to(outside)
    assert bm.integration_branch_from_project(root) is None


def test_unreadable_or_trunk_brief_names_no_integration_branch(tmp_path):
    assert bm.integration_branch_from_project(tmp_path) is None
    (tmp_path / "brief.json").write_text("{not json")
    assert bm.integration_branch_from_project(tmp_path) is None
    (tmp_path / "brief.json").write_text(json.dumps({"git_workflow": {"branch_model": "trunk"}}))
    assert bm.integration_branch_from_project(tmp_path) is None


def test_post_merge_refuses_the_integration_branch(repo, tmp_path):
    _integration_repo(repo)
    repo.branch("feat/c", {"c.txt": "c\n"})
    repo.git("checkout", "-q", "dev")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge branch 'feat/c'", "feat/c")
    repo.git("push", "-q", "origin", "dev")
    repo.git("checkout", "-q", "main")
    repo.merge_no_ff("dev")
    team = tmp_path / "team"
    (team / "references").mkdir(parents=True)
    cfg = bi.InventoryConfig(repo=repo.work, now=NOW, integration_branch="dev")
    code, lines = bc.run_post_merge("dev", cfg, team_dir=team, team_id="t", apply=True, api=FakeAPI())
    assert code == bc.EXIT_FAIL
    assert "integration" in lines[0]


# ---------------------------------------------------------------------------
# Vulnscan base and this repository's workflows
# ---------------------------------------------------------------------------


def test_vulnscan_base_falls_back_to_the_integration_branch(repo, tmp_path, monkeypatch):
    from agentteams.vulnscan import executor_guard

    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    _integration_repo(repo)
    (repo.work / "brief.json").write_text(json.dumps({"git_workflow": {"branch_model": "integration"}}))
    assert executor_guard.brief_base_ref(repo.work) == "origin/dev"


def test_this_repos_workflows_never_trigger_on_the_integration_branch():
    """The model saves money only while no push to ``dev`` runs a workflow (integration-branch reference §5).

    ``ci.yml`` may run on *pull requests* into ``dev``: only invited contributors' fork PRs and automation PRs
    (vulnscan fixes) target it (operator decision 2026-11-04); the maintainer merges into ``dev`` locally.
    """
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        on = re.sub(r"(?m)^\s*#.*$", "", path.read_text(encoding="utf-8").split("\njobs:", 1)[0])
        assert "'**'" not in on and '"**"' not in on, f"{path.name}: a trigger matches every branch"
        push = re.search(r"(?ms)^  push:\s*\n(.*?)(?=^  \S|\Z)", on)
        if push:
            assert not re.search(r"(^\s*-\s*|[\[,]\s*)['\"]?dev['\"]?\s*($|[\],])", push.group(1), re.M), \
                f"{path.name}: push triggers on dev"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").split("\njobs:", 1)[0]
    assert re.search(r"pull_request:\s*\n\s*branches:\s*\[\s*main\b", ci), "ci.yml PR runs must be filtered"


# --- row 675 (optional): warn when a release merge would delete the integration branch ----------------------

class _RepoAPI(FakeAPI):
    """FakeAPI that also answers the repository endpoint (``delete_branch_on_merge``)."""

    def __init__(self, *, auto_delete=True, repo_verdict="ok", **kw) -> None:
        super().__init__(**kw)
        self.auto_delete = auto_delete
        self.repo_verdict = repo_verdict

    def get(self, path: str):
        if path == f"/repos/{self.repo}":
            self.calls.append(path)
            if self.repo_verdict != "ok":
                return self.repo_verdict, None
            return "ok", ({} if self.auto_delete is None else {"delete_branch_on_merge": self.auto_delete})
        return super().get(path)


def _warnings(notes):
    return [n for n in notes if n.startswith("WARNING: integration branch")]


def _unknown(notes):
    return [n for n in notes if n.startswith("could not check whether integration branch")]


def test_unprotected_integration_branch_with_auto_delete_warns():
    notes = bi.integration_protection_notes(_RepoAPI(auto_delete=True), "dev")
    assert len(_warnings(notes)) == 1 and "merging the release PR will delete dev" in notes[0]


def test_a_non_dict_repo_body_is_unknown():
    class _ListBody(_RepoAPI):
        def get(self, path):
            if path == f"/repos/{self.repo}":
                return "ok", ["not", "a", "dict"]
            return super().get(path)
    notes = bi.integration_protection_notes(_ListBody(), "dev")
    assert _unknown(notes) and not _warnings(notes)


def test_protected_integration_branch_or_auto_delete_off_is_silent():
    assert bi.integration_protection_notes(_RepoAPI(auto_delete=True, protected={"dev"}), "dev") == []
    api = _RepoAPI(auto_delete=False)
    assert bi.integration_protection_notes(api, "dev") == []
    assert not any(c.startswith("branches/") for c in api.calls)  # protection not even looked up


@pytest.mark.parametrize("kw", [
    {"auto_delete": None},                       # field missing (e.g. no permission): unknown, not false
    {"repo_verdict": bi.API_UNKNOWN},            # repo endpoint failed
    {"repo_verdict": bi.API_NOT_FOUND},
    {"auto_delete": True, "rules_verdict": bi.API_UNKNOWN},  # protection not readable
    {"auto_delete": "true"},                     # non-bool field: unknown, never read as on/off
])
def test_unknown_facts_give_a_could_not_check_note_never_a_warning(kw):
    notes = bi.integration_protection_notes(_RepoAPI(**kw), "dev")
    assert _warnings(notes) == [] and len(_unknown(notes)) == 1


def test_inventory_carries_the_warning_and_it_does_not_touch_the_plan(repo):
    _integration_repo(repo)
    base = inventory(repo, integration_branch="dev", api=_RepoAPI(auto_delete=False))
    inv = inventory(repo, integration_branch="dev", api=_RepoAPI(auto_delete=True))
    assert len(_warnings(inv["meta"]["notes"])) == 1
    assert _warnings(base["meta"]["notes"]) == []
    assert inv["plan"]["sha256"] == base["plan"]["sha256"]  # a note, never a plan change
    assert rec(inv, "dev")["action"] == "keep"


def test_trunk_model_and_no_api_make_no_protection_call(repo):
    _integration_repo(repo)
    api = _RepoAPI(auto_delete=True)
    inventory(repo, integration_branch="", api=api)
    assert f"/repos/{api.repo}" not in api.calls
    api2 = _RepoAPI(auto_delete=True)
    cfg = bi.InventoryConfig(repo=repo.work, now=NOW, integration_branch="dev", use_api=False)
    inv = bi.build_inventory(cfg, api=api2)
    assert api2.calls == [] and _warnings(inv["meta"]["notes"]) == []
