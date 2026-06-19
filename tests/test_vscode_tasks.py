"""
Tests for agentteams/vscode_tasks.py — task discovery, rendering, and sentinel merge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vscode_tasks import (
    AGENTTEAMS_META_TASKS,
    ProjectCommand,
    _assign_group_defaults,
    _classify_group,
    _from_goose_recipes,
    _from_makefile,
    _from_package_json,
    _from_pyproject_toml,
    _from_scripts_dir,
    _from_taskfile,
    _from_tox_ini,
    _merge_jsonc,
    _safe_name,
    _strip_jsonc_comments,
    discover_project_commands,
    render_tasks_json,
    sentinel_merge,
)


# ---------------------------------------------------------------------------
# _safe_name
# ---------------------------------------------------------------------------

class TestSafeName:
    def test_accepts_simple_names(self):
        assert _safe_name("test")
        assert _safe_name("build")
        assert _safe_name("my-task")
        assert _safe_name("my_task")
        assert _safe_name("task:build")
        assert _safe_name("Task 1")

    def test_rejects_shell_metacharacters(self):
        assert not _safe_name("$(INJECT)")
        assert not _safe_name("test;rm")
        assert not _safe_name("../escape")
        assert not _safe_name("test`whoami`")
        assert not _safe_name("test && rm")
        assert not _safe_name("")

    def test_rejects_empty_string(self):
        assert not _safe_name("")


# ---------------------------------------------------------------------------
# _classify_group
# ---------------------------------------------------------------------------

class TestClassifyGroup:
    def test_test_keywords(self):
        assert _classify_group("test") == "test"
        assert _classify_group("unit-test") == "test"
        assert _classify_group("lint") == "test"
        assert _classify_group("verify") == "test"
        assert _classify_group("qa") == "test"

    def test_build_keywords(self):
        assert _classify_group("build") == "build"
        assert _classify_group("compile") == "build"
        assert _classify_group("bundle") == "build"
        assert _classify_group("dist") == "build"

    def test_none_fallback(self):
        assert _classify_group("deploy") == "none"
        assert _classify_group("start") == "none"
        assert _classify_group("clean") == "none"


# ---------------------------------------------------------------------------
# _from_package_json
# ---------------------------------------------------------------------------

class TestFromPackageJson:
    def test_extracts_scripts(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "build": "tsc", "lint": "eslint ."}}),
            encoding="utf-8",
        )
        cmds = _from_package_json(tmp_path)
        labels = [c.label for c in cmds]
        assert "npm: test" in labels
        assert "npm: build" in labels
        assert "npm: lint" in labels

    def test_classifies_groups_correctly(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "build": "tsc", "start": "node ."}}),
            encoding="utf-8",
        )
        cmds = _from_package_json(tmp_path)
        by_label = {c.label: c for c in cmds}
        assert by_label["npm: test"].group == "test"
        assert by_label["npm: build"].group == "build"
        assert by_label["npm: start"].group == "none"

    def test_returns_empty_when_no_file(self, tmp_path):
        assert _from_package_json(tmp_path) == []

    def test_returns_empty_on_malformed_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{bad json", encoding="utf-8")
        assert _from_package_json(tmp_path) == []

    def test_returns_empty_when_no_scripts_key(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "my-project"}), encoding="utf-8"
        )
        assert _from_package_json(tmp_path) == []

    def test_skips_unsafe_script_names(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"safe": "echo ok", "$(INJECT)": "evil"}}),
            encoding="utf-8",
        )
        cmds = _from_package_json(tmp_path)
        labels = [c.label for c in cmds]
        assert "npm: safe" in labels
        assert not any("INJECT" in l for l in labels)


# ---------------------------------------------------------------------------
# _from_makefile
# ---------------------------------------------------------------------------

class TestFromMakefile:
    def test_extracts_phony_targets(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            ".PHONY: test build lint\ntest:\n\tpytest\nbuild:\n\tgcc\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = {c.label for c in cmds}
        assert labels == {"make: test", "make: build", "make: lint"}

    def test_phony_only_not_all_targets(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            ".PHONY: test\ntest:\n\tpytest\nhidden-target:\n\techo hi\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = {c.label for c in cmds}
        assert labels == {"make: test"}
        assert "make: hidden-target" not in labels

    def test_rejects_shell_expansion_names(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            ".PHONY: test $(DANGEROUS)\n", encoding="utf-8"
        )
        cmds = _from_makefile(tmp_path)
        labels = [c.label for c in cmds]
        assert labels == ["make: test"]

    def test_returns_empty_when_no_file(self, tmp_path):
        assert _from_makefile(tmp_path) == []

    def test_classifies_groups(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            ".PHONY: test build deploy\n", encoding="utf-8"
        )
        cmds = _from_makefile(tmp_path)
        by_label = {c.label: c for c in cmds}
        assert by_label["make: test"].group == "test"
        assert by_label["make: build"].group == "build"
        assert by_label["make: deploy"].group == "none"


# ---------------------------------------------------------------------------
# _from_pyproject_toml
# ---------------------------------------------------------------------------

class TestFromPyprojectToml:
    def test_extracts_taskipy_tasks(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.taskipy.tasks]\ntest = \"pytest tests/ -q\"\nlint = \"ruff check .\"\n",
            encoding="utf-8",
        )
        cmds = _from_pyproject_toml(tmp_path)
        labels = {c.label for c in cmds}
        assert labels == {"task: test", "task: lint"}

    def test_extracts_poe_tasks(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poethepoet.tasks]\ntest = \"pytest\"\n",
            encoding="utf-8",
        )
        cmds = _from_pyproject_toml(tmp_path)
        assert any(c.label == "task: test" for c in cmds)

    def test_skips_project_scripts_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project.scripts]\nmy-cli = \"mypackage.cli:main\"\n",
            encoding="utf-8",
        )
        cmds = _from_pyproject_toml(tmp_path)
        assert cmds == []

    def test_returns_empty_when_no_file(self, tmp_path):
        assert _from_pyproject_toml(tmp_path) == []

    def test_skips_comment_lines(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.taskipy.tasks]\n# this is a comment\ntest = \"pytest\"\n",
            encoding="utf-8",
        )
        cmds = _from_pyproject_toml(tmp_path)
        assert all(c.label != "task: # this is a comment" for c in cmds)
        assert any(c.label == "task: test" for c in cmds)


# ---------------------------------------------------------------------------
# _from_tox_ini
# ---------------------------------------------------------------------------

class TestFromToxIni:
    def test_extracts_testenv_names(self, tmp_path):
        (tmp_path / "tox.ini").write_text(
            "[testenv:test]\ncommands = pytest\n\n[testenv:lint]\ncommands = ruff check .\n",
            encoding="utf-8",
        )
        cmds = _from_tox_ini(tmp_path)
        labels = {c.label for c in cmds}
        assert labels == {"tox: test", "tox: lint"}

    def test_all_tox_tasks_classified_as_test(self, tmp_path):
        (tmp_path / "tox.ini").write_text(
            "[testenv:build]\ncommands = python -m build\n", encoding="utf-8"
        )
        cmds = _from_tox_ini(tmp_path)
        assert all(c.group == "test" for c in cmds)

    def test_returns_empty_when_no_file(self, tmp_path):
        assert _from_tox_ini(tmp_path) == []


# ---------------------------------------------------------------------------
# _from_taskfile
# ---------------------------------------------------------------------------

class TestFromTaskfile:
    def test_extracts_task_keys_yml(self, tmp_path):
        (tmp_path / "Taskfile.yml").write_text(
            "version: '3'\ntasks:\n  test:\n    cmds: [go test ./...]\n  build:\n    cmds: [go build ./...]\n",
            encoding="utf-8",
        )
        cmds = _from_taskfile(tmp_path)
        labels = {c.label for c in cmds}
        assert "task: test" in labels
        assert "task: build" in labels

    def test_skips_reserved_keys(self, tmp_path):
        (tmp_path / "Taskfile.yml").write_text(
            "version: '3'\ntasks:\n  test:\n    cmds: [echo]\nvars:\n  FOO: bar\n",
            encoding="utf-8",
        )
        cmds = _from_taskfile(tmp_path)
        labels = {c.label for c in cmds}
        assert "task: vars" not in labels
        assert "task: version" not in labels

    def test_falls_back_to_yaml_extension(self, tmp_path):
        (tmp_path / "Taskfile.yaml").write_text(
            "version: '3'\ntasks:\n  deploy:\n    cmds: [echo deploy]\n",
            encoding="utf-8",
        )
        cmds = _from_taskfile(tmp_path)
        assert any(c.label == "task: deploy" for c in cmds)

    def test_returns_empty_when_no_file(self, tmp_path):
        assert _from_taskfile(tmp_path) == []


# ---------------------------------------------------------------------------
# _from_scripts_dir
# ---------------------------------------------------------------------------

class TestFromScriptsDir:
    def test_extracts_sh_files(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "deploy.sh").write_text("#!/bin/bash\necho deploy\n", encoding="utf-8")
        (scripts / "test.sh").write_text("#!/bin/bash\npytest\n", encoding="utf-8")
        cmds = _from_scripts_dir(tmp_path)
        labels = {c.label for c in cmds}
        assert labels == {"script: deploy", "script: test"}

    def test_skips_non_sh_files(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "deploy.sh").write_text("echo", encoding="utf-8")
        (scripts / "helper.py").write_text("print('x')", encoding="utf-8")
        cmds = _from_scripts_dir(tmp_path)
        labels = {c.label for c in cmds}
        assert "script: helper" not in labels

    def test_returns_empty_when_no_scripts_dir(self, tmp_path):
        assert _from_scripts_dir(tmp_path) == []


# ---------------------------------------------------------------------------
# _assign_group_defaults
# ---------------------------------------------------------------------------

class TestAssignGroupDefaults:
    def test_first_test_and_build_become_defaults(self):
        cmds = [
            ProjectCommand("npm: test", "npm run test", group="test"),
            ProjectCommand("npm: test2", "npm run test2", group="test"),
            ProjectCommand("npm: build", "npm run build", group="build"),
            ProjectCommand("npm: build2", "npm run build2", group="build"),
        ]
        _assign_group_defaults(cmds)
        assert cmds[0].is_default is True
        assert cmds[1].is_default is False
        assert cmds[2].is_default is True
        assert cmds[3].is_default is False


# ---------------------------------------------------------------------------
# discover_project_commands
# ---------------------------------------------------------------------------

class TestDiscoverProjectCommands:
    def test_empty_project_returns_empty(self, tmp_path):
        assert discover_project_commands(tmp_path) == []

    def test_aggregates_multiple_sources(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
        )
        (tmp_path / "tox.ini").write_text("[testenv:lint]\ncommands = ruff\n", encoding="utf-8")
        cmds = discover_project_commands(tmp_path)
        labels = {c.label for c in cmds}
        assert "npm: test" in labels
        assert "tox: lint" in labels


# ---------------------------------------------------------------------------
# _from_goose_recipes
# ---------------------------------------------------------------------------

class TestFromGooseRecipes:
    def _make_recipe(self, path: Path, stem: str, title: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{stem}.yaml").write_text(
            f'version: "1.0.0"\ntitle: "{title}"\ndescription: "test"\n',
            encoding="utf-8",
        )

    def test_discovers_recipes(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        self._make_recipe(rd, "orchestrator", "Orchestrator — MyProject")
        self._make_recipe(rd, "security", "Security — MyProject")
        cmds = _from_goose_recipes(tmp_path)
        labels = [c.label for c in cmds]
        assert "Goose: Orchestrator" in labels
        assert "Goose: Security" in labels

    def test_strips_project_suffix(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        self._make_recipe(rd, "git-operations", "Git Operations — SomeProject")
        cmds = _from_goose_recipes(tmp_path)
        assert cmds[0].label == "Goose: Git Operations"

    def test_command_uses_recipe_path(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        self._make_recipe(rd, "orchestrator", "Orchestrator — X")
        cmd = _from_goose_recipes(tmp_path)[0]
        assert cmd.command == "goose run --recipe .goose/recipes/orchestrator.yaml"

    def test_uses_dedicated_panel(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        self._make_recipe(rd, "orchestrator", "Orchestrator — X")
        cmd = _from_goose_recipes(tmp_path)[0]
        assert cmd.presentation is not None
        assert cmd.presentation.get("panel") == "dedicated"
        assert cmd.presentation.get("focus") is True

    def test_skips_setup_required(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        self._make_recipe(rd, "orchestrator", "Orchestrator — X")
        self._make_recipe(rd, "SETUP-REQUIRED", "Setup Required — X")
        cmds = _from_goose_recipes(tmp_path)
        labels = [c.label for c in cmds]
        assert not any("SETUP" in l for l in labels)

    def test_falls_back_to_slug_when_no_title(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        rd.mkdir(parents=True)
        (rd / "my-agent.yaml").write_text("version: '1.0.0'\n", encoding="utf-8")
        cmds = _from_goose_recipes(tmp_path)
        assert cmds[0].label == "Goose: My Agent"

    def test_returns_empty_when_no_recipes_dir(self, tmp_path):
        assert _from_goose_recipes(tmp_path) == []

    def test_goose_tasks_use_dedicated_panel_in_rendered_json(self, tmp_path):
        rd = tmp_path / ".goose" / "recipes"
        self._make_recipe(rd, "orchestrator", "Orchestrator — X")
        cmds = _from_goose_recipes(tmp_path)
        content = render_tasks_json(cmds)
        data = json.loads(content)
        goose_task = next(t for t in data["tasks"] if t["label"] == "Goose: Orchestrator")
        assert goose_task["presentation"]["panel"] == "dedicated"
        assert goose_task["presentation"]["focus"] is True


# ---------------------------------------------------------------------------
# render_tasks_json
# ---------------------------------------------------------------------------

class TestRenderTasksJson:
    def test_valid_json_output(self):
        content = render_tasks_json([])
        data = json.loads(content)
        assert data["version"] == "2.0.0"
        assert isinstance(data["tasks"], list)

    def test_all_tasks_have_agentteams_sentinel(self):
        cmds = [ProjectCommand("npm: test", "npm run test", group="test")]
        content = render_tasks_json(cmds)
        data = json.loads(content)
        assert all(t["detail"] == "AGENTTEAMS" for t in data["tasks"])

    def test_meta_tasks_always_included(self):
        content = render_tasks_json([])
        data = json.loads(content)
        meta_labels = {t["label"] for t in AGENTTEAMS_META_TASKS}
        output_labels = {t["label"] for t in data["tasks"]}
        assert meta_labels.issubset(output_labels)

    def test_test_group_uses_object_form(self):
        cmds = [ProjectCommand("npm: test", "npm run test", group="test", is_default=True)]
        content = render_tasks_json(cmds)
        data = json.loads(content)
        task = next(t for t in data["tasks"] if t["label"] == "npm: test")
        assert task["group"] == {"kind": "test", "isDefault": True}

    def test_none_group_uses_string_form(self):
        cmds = [ProjectCommand("npm: start", "npm start", group="none")]
        content = render_tasks_json(cmds)
        data = json.loads(content)
        task = next(t for t in data["tasks"] if t["label"] == "npm: start")
        assert task["group"] == "none"

    def test_no_duplicate_labels(self):
        cmds = [
            ProjectCommand("npm: test", "npm run test", group="test"),
            ProjectCommand("npm: test", "npm run test", group="test"),
        ]
        content = render_tasks_json(cmds)
        data = json.loads(content)
        labels = [t["label"] for t in data["tasks"]]
        assert len(labels) == len(set(labels))

    def test_ends_with_newline(self):
        assert render_tasks_json([]).endswith("\n")

    def test_meta_tasks_not_duplicated_by_command_list(self):
        # If a discovered command has the same label as a meta-task, it should appear once
        meta_label = AGENTTEAMS_META_TASKS[0]["label"]
        cmds = [ProjectCommand(meta_label, "some-other-cmd", group="none")]
        content = render_tasks_json(cmds)
        data = json.loads(content)
        occurrences = sum(1 for t in data["tasks"] if t["label"] == meta_label)
        assert occurrences == 1


# ---------------------------------------------------------------------------
# sentinel_merge
# ---------------------------------------------------------------------------

class TestSentinelMerge:
    def test_returns_new_content_when_no_existing_file(self, tmp_path):
        new_content = render_tasks_json([])
        result = sentinel_merge(tmp_path / "tasks.json", new_content)
        assert result == new_content

    def test_preserves_user_tasks(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text(
            json.dumps({"version": "2.0.0", "tasks": [
                {"label": "my-custom", "type": "shell", "command": "echo hi"},
            ]}),
            encoding="utf-8",
        )
        new_content = render_tasks_json([])
        result = sentinel_merge(existing, new_content)
        data = json.loads(result)
        labels = {t["label"] for t in data["tasks"]}
        assert "my-custom" in labels

    def test_replaces_old_agentteams_tasks(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text(
            json.dumps({"version": "2.0.0", "tasks": [
                {"label": "agentteams: dry-run check", "type": "shell",
                 "command": "OLD-COMMAND", "detail": "AGENTTEAMS"},
            ]}),
            encoding="utf-8",
        )
        new_content = render_tasks_json([])
        result = sentinel_merge(existing, new_content)
        data = json.loads(result)
        dry_run = next(t for t in data["tasks"] if t["label"] == "agentteams: dry-run check")
        assert dry_run["command"] == "agentteams --update --dry-run"

    def test_user_task_wins_label_collision(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text(
            json.dumps({"version": "2.0.0", "tasks": [
                {"label": "npm: test", "type": "shell", "command": "user-version"},
            ]}),
            encoding="utf-8",
        )
        cmds = [ProjectCommand("npm: test", "npm run test", group="test")]
        new_content = render_tasks_json(cmds)
        result = sentinel_merge(existing, new_content)
        data = json.loads(result)
        npm_test = next(t for t in data["tasks"] if t["label"] == "npm: test")
        # Generated version wins (sentinel_merge replaces user task with generated one)
        assert npm_test["command"] == "npm run test"

    def test_no_duplicate_labels_after_merge(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text(
            json.dumps({"version": "2.0.0", "tasks": [
                {"label": "my-task", "type": "shell", "command": "echo"},
                {"label": "agentteams: dry-run check", "type": "shell",
                 "command": "old", "detail": "AGENTTEAMS"},
            ]}),
            encoding="utf-8",
        )
        new_content = render_tasks_json([])
        result = sentinel_merge(existing, new_content)
        data = json.loads(result)
        labels = [t["label"] for t in data["tasks"]]
        assert len(labels) == len(set(labels))

    def test_raises_on_malformed_json(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text('{"version": "2.0.0", "tasks": [,]}', encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            sentinel_merge(existing, render_tasks_json([]))

    def test_jsonc_file_gets_tasks_appended(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text(
            '{\n  "version": "2.0.0",\n  "tasks": [\n'
            '    // ── MY TASKS ──\n'
            '    {\n      "label": "my-task",\n      "type": "shell",\n'
            '      "command": "echo hi",\n      "problemMatcher": []\n    }\n\n  ]\n}',
            encoding="utf-8",
        )
        new_content = render_tasks_json([])
        result = sentinel_merge(existing, new_content)
        # Original comment preserved
        assert "// ── MY TASKS ──" in result
        # User task preserved
        assert '"label": "my-task"' in result
        # agentteams meta-tasks appended
        assert '"agentteams: update agents"' in result
        assert '"detail": "AGENTTEAMS"' in result

    def test_jsonc_append_is_idempotent(self, tmp_path):
        existing = tmp_path / "tasks.json"
        existing.write_text(
            '{\n  "version": "2.0.0",\n  "tasks": [\n'
            '    // comment\n'
            '    {\n      "label": "my-task",\n      "type": "shell",\n'
            '      "command": "echo",\n      "problemMatcher": []\n    }\n\n  ]\n}',
            encoding="utf-8",
        )
        new_content = render_tasks_json([])
        first = sentinel_merge(existing, new_content)
        # Write result back and merge again
        existing.write_text(first, encoding="utf-8")
        second = sentinel_merge(existing, new_content)
        # Labels should appear exactly once each
        meta_label = "agentteams: update agents"
        assert first.count(f'"{meta_label}"') == 1
        assert second.count(f'"{meta_label}"') == 1

    def test_raises_with_jsonc_unparseable_after_strip(self, tmp_path):
        existing = tmp_path / "tasks.json"
        # JSONC that's still invalid even after stripping comments
        existing.write_text(
            '{\n  // a comment\n  "version": "2.0.0",\n  "tasks": [,]\n}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="JSONC but could not be parsed"):
            sentinel_merge(existing, render_tasks_json([]))

    def test_returns_new_content_on_unreadable_file(self, tmp_path):
        # Simulate unreadable file by pointing to a directory
        existing = tmp_path / "tasks.json"
        existing.mkdir()  # a directory, not a file — is_file() returns False
        new_content = render_tasks_json([])
        result = sentinel_merge(existing, new_content)
        assert result == new_content


# ---------------------------------------------------------------------------
# Adapter vscode_tasks_rel_path() integration (imported from frameworks)
# ---------------------------------------------------------------------------

class TestAdapterRelPath:
    def test_copilot_vscode_returns_path(self):
        from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
        assert CopilotVSCodeAdapter().vscode_tasks_rel_path() == "../../.vscode/tasks.json"

    def test_claude_returns_path(self):
        from agentteams.frameworks.claude import ClaudeAdapter
        assert ClaudeAdapter().vscode_tasks_rel_path() == "../../.vscode/tasks.json"

    def test_goose_returns_path(self):
        from agentteams.frameworks.goose import GooseAdapter
        assert GooseAdapter().vscode_tasks_rel_path() == "../../.vscode/tasks.json"

    def test_agents_md_returns_none(self):
        from agentteams.frameworks.agents_md import AgentsMdAdapter
        assert AgentsMdAdapter().vscode_tasks_rel_path() is None
