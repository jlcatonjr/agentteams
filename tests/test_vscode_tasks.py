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
    _find_tasks_close_bracket,
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

    def test_non_phony_rule_headers_are_included(self, tmp_path):
        """Makefile with no .PHONY still yields tasks for rule-header targets."""
        (tmp_path / "Makefile").write_text(
            "build:\n\tgcc main.c -o app\n\ntest:\n\tpytest\n\nlint:\n\truff check .\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = {c.label for c in cmds}
        assert labels == {"make: build", "make: test", "make: lint"}

    def test_rule_headers_union_with_phony(self, tmp_path):
        """Targets in .PHONY and additional rule-header-only targets are all included."""
        (tmp_path / "Makefile").write_text(
            ".PHONY: test\ntest:\n\tpytest\ninstall:\n\tpip install .\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = {c.label for c in cmds}
        assert "make: test" in labels
        assert "make: install" in labels

    def test_no_duplicates_when_target_is_both_phony_and_rule_header(self, tmp_path):
        """A target in .PHONY that also has an explicit rule header appears once."""
        (tmp_path / "Makefile").write_text(
            ".PHONY: build\nbuild:\n\tgcc main.c\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = [c.label for c in cmds]
        assert labels.count("make: build") == 1

    def test_duplicate_rule_headers_appear_only_once(self, tmp_path):
        """A target defined more than once (e.g. inside ifeq/else blocks) is emitted once."""
        (tmp_path / "Makefile").write_text(
            "ifeq ($(OS),Windows)\nbuild:\n\tcl main.c\nelse\nbuild:\n\tgcc main.c\nendif\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = [c.label for c in cmds]
        assert labels.count("make: build") == 1

    def test_rule_header_with_inline_comment_is_included(self, tmp_path):
        """Rule headers followed by an inline comment are still discovered."""
        (tmp_path / "Makefile").write_text(
            "deploy: # push to prod\n\t./deploy.sh\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        assert any(c.label == "make: deploy" for c in cmds)

    def test_rule_header_with_prerequisites_is_excluded(self, tmp_path):
        """Targets with prerequisites on the same line are not discovered (safe default)."""
        (tmp_path / "Makefile").write_text(
            "all: build test\nbuild:\n\tgcc main.c\ntest:\n\tpytest\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        labels = {c.label for c in cmds}
        # 'all' carries prerequisites so it does not match _RULE_HEADER_RE
        assert "make: all" not in labels
        # standalone targets are captured
        assert "make: build" in labels
        assert "make: test" in labels

    def test_non_phony_targets_classified_by_group(self, tmp_path):
        """Group assignment works for targets discovered via rule headers."""
        (tmp_path / "Makefile").write_text(
            "build:\n\tgcc\ntest:\n\tpytest\ndeploy:\n\t./ship.sh\n",
            encoding="utf-8",
        )
        cmds = _from_makefile(tmp_path)
        by_label = {c.label: c for c in cmds}
        assert by_label["make: build"].group == "build"
        assert by_label["make: test"].group == "test"
        assert by_label["make: deploy"].group == "none"

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
        assert ".goose/recipes/orchestrator.yaml" in cmd.command
        assert "goose-or run --recipe" in cmd.command
        assert "goose-backend.sh" in cmd.command

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
# _find_tasks_close_bracket
# ---------------------------------------------------------------------------

class TestFindTasksCloseBracket:
    """Unit tests for the forward-scanning helper that locates the tasks ] ."""

    def test_simple_tasks_array(self):
        raw = '{\n  "version": "2.0.0",\n  "tasks": [\n  ]\n}'
        idx = _find_tasks_close_bracket(raw)
        assert idx != -1
        assert raw[idx] == "]"
        # The character after the ] should be a newline (not the outer })
        assert raw[idx + 1] == "\n"

    def test_task_with_nested_args_array(self):
        """A ] inside an args sub-array or a trailing comment must not be returned.

        The raw string is intentionally constructed so that raw.rfind(']') would
        return the ] inside the trailing JSONC comment (a later position than the
        structural tasks ]), confirming that _find_tasks_close_bracket does not
        regress to rfind-equivalent behaviour.
        """
        raw = (
            '{\n  "tasks": [\n'
            '    {"label": "x", "args": ["--filter=[unit]"]}\n'
            '  ]\n'
            '  // refs: https://example.com/path[0]\n'
            '}'
        )
        idx = _find_tasks_close_bracket(raw)
        assert idx != -1
        assert raw[idx] == "]"
        # The comment follows the structural tasks ] — confirming we stopped at
        # the right position, not at the ] inside the args string or the one
        # inside the trailing comment (which rfind would have returned instead).
        assert "// refs" in raw[idx + 1:]
        assert raw[idx + 1:].strip().startswith("//")

    def test_inputs_array_after_tasks(self):
        """Returns tasks ] even when an 'inputs' array follows with its own ]."""
        raw = (
            '{\n'
            '  "tasks": [\n    {"label": "a"}\n  ],\n'
            '  "inputs": [\n    {"id": "x", "options": ["y", "z"]}\n  ]\n'
            '}'
        )
        idx = _find_tasks_close_bracket(raw)
        assert idx != -1
        assert raw[idx] == "]"
        # After the tasks ] there is still ",\n  \"inputs\"..." in the file
        remaining = raw[idx + 1:]
        assert '"inputs"' in remaining

    def test_jsonc_comment_with_bracket_after_tasks(self):
        """A // comment containing ] after the tasks array is ignored."""
        raw = (
            '{\n'
            '  "tasks": [\n    {"label": "x"}\n  ]\n'
            '  // trailing comment with ] bracket\n'
            '}'
        )
        idx = _find_tasks_close_bracket(raw)
        assert idx != -1
        assert raw[idx] == "]"
        # The comment must come after our chosen position
        remaining = raw[idx + 1:]
        assert "// trailing comment" in remaining

    def test_block_comment_with_bracket_inside_tasks(self):
        """A /* */ comment inside the tasks array containing ] is skipped."""
        raw = (
            '{\n'
            '  "tasks": [\n'
            '    /* section: foo[0] */\n'
            '    {"label": "x"}\n'
            '  ]\n'
            '}'
        )
        idx = _find_tasks_close_bracket(raw)
        assert idx != -1
        assert raw[idx] == "]"
        assert raw[idx + 1] == "\n"  # structural ] at end of tasks array

    def test_tasks_key_inside_string_value_is_ignored(self):
        """A literal string containing 'tasks' must not mislead the scanner."""
        raw = (
            '{\n'
            '  "description": "run tasks: [a, b]",\n'
            '  "tasks": [\n    {"label": "real"}\n  ]\n'
            '}'
        )
        idx = _find_tasks_close_bracket(raw)
        assert idx != -1
        # The returned ] must be the structural close of the real tasks array,
        # not the one inside the "description" string.
        before = raw[:idx]
        assert '"label": "real"' in before

    def test_returns_minus_one_when_no_tasks_key(self):
        raw = '{\n  "version": "2.0.0"\n}'
        assert _find_tasks_close_bracket(raw) == -1

    def test_returns_minus_one_on_empty_string(self):
        assert _find_tasks_close_bracket("") == -1


# ---------------------------------------------------------------------------
# _merge_jsonc (integration)
# ---------------------------------------------------------------------------

class TestMergeJsonc:
    """Integration tests that exercise _merge_jsonc end-to-end."""

    def _make_new_content(self) -> str:
        """Minimal new_content with one AGENTTEAMS task."""
        return json.dumps({
            "version": "2.0.0",
            "tasks": [{
                "label": "agentteams: update agents",
                "type": "shell",
                "command": "agentteams --update --merge",
                "group": "none",
                "presentation": {"reveal": "always", "panel": "shared"},
                "problemMatcher": [],
                "detail": "AGENTTEAMS",
            }],
        }, indent=2) + "\n"

    def test_merge_jsonc_with_inputs_section_produces_valid_json(self):
        raw = (
            '{\n'
            '  "version": "2.0.0",\n'
            '  "tasks": [\n'
            '    {\n'
            '      "label": "user task",\n'
            '      "type": "shell",\n'
            '      "command": "echo hi",\n'
            '      "problemMatcher": []\n'
            '    }\n'
            '  ],\n'
            '  "inputs": [\n'
            '    {\n'
            '      "id": "target",\n'
            '      "type": "promptString",\n'
            '      "description": "Build target",\n'
            '      "options": ["debug", "release"]\n'
            '    }\n'
            '  ]\n'
            '}'
        )
        existing = json.loads(raw)
        result = _merge_jsonc(existing, raw, self._make_new_content())
        # Must be parseable as JSON
        parsed = json.loads(result)
        labels = {t["label"] for t in parsed["tasks"]}
        # User task preserved
        assert "user task" in labels
        # AGENTTEAMS task appended
        assert "agentteams: update agents" in labels
        # inputs section intact
        assert "inputs" in parsed
        assert parsed["inputs"][0]["id"] == "target"

    def test_merge_jsonc_task_with_bracket_in_command_produces_valid_json(self):
        raw = (
            '{\n'
            '  "version": "2.0.0",\n'
            '  "tasks": [\n'
            '    {\n'
            '      "label": "glob task",\n'
            '      "type": "shell",\n'
            '      "command": "grep foo[bar] .",\n'
            '      "args": ["--include=[*.py]"],\n'
            '      "problemMatcher": []\n'
            '    }\n'
            '  ]\n'
            '}'
        )
        existing = json.loads(raw)
        result = _merge_jsonc(existing, raw, self._make_new_content())
        parsed = json.loads(result)
        labels = {t["label"] for t in parsed["tasks"]}
        assert "glob task" in labels
        assert "agentteams: update agents" in labels

    def test_merge_jsonc_with_comment_containing_bracket_after_tasks(self):
        raw = (
            '{\n'
            '  "version": "2.0.0",\n'
            '  "tasks": [\n'
            '    {\n'
            '      "label": "my task",\n'
            '      "type": "shell",\n'
            '      "command": "make build",\n'
            '      "problemMatcher": []\n'
            '    }\n'
            '  ]\n'
            '  // see: https://example.com/tasks[0]\n'
            '}'
        )
        existing = json.loads(_strip_jsonc_comments(raw))
        result = _merge_jsonc(existing, raw, self._make_new_content())
        # Strip comments before parsing to validate JSON structure
        parsed = json.loads(_strip_jsonc_comments(result))
        labels = {t["label"] for t in parsed["tasks"]}
        assert "my task" in labels
        assert "agentteams: update agents" in labels


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

    def test_jsonc_with_inputs_array_produces_valid_and_complete_json(self, tmp_path):
        """sentinel_merge on a JSONC file with 'inputs' must not corrupt it."""
        existing = tmp_path / "tasks.json"
        existing.write_text(
            '{\n'
            '  // VS Code tasks — hand-authored\n'
            '  "version": "2.0.0",\n'
            '  "tasks": [\n'
            '    {\n'
            '      "label": "my task",\n'
            '      "type": "shell",\n'
            '      "command": "echo ${input:target}",\n'
            '      "problemMatcher": []\n'
            '    }\n'
            '  ],\n'
            '  "inputs": [\n'
            '    {\n'
            '      "id": "target",\n'
            '      "type": "promptString",\n'
            '      "description": "Enter target",\n'
            '      "default": "debug"\n'
            '    }\n'
            '  ]\n'
            '}',
            encoding="utf-8",
        )
        new_content = render_tasks_json([])
        result = sentinel_merge(existing, new_content)
        # After stripping comments the result must parse as valid JSON.
        parsed = json.loads(_strip_jsonc_comments(result))
        task_labels = {t["label"] for t in parsed["tasks"]}
        assert "my task" in task_labels
        assert "agentteams: update agents" in task_labels
        # inputs section must be preserved and structurally intact
        assert "inputs" in parsed
        assert parsed["inputs"][0]["id"] == "target"


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
