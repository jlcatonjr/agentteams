"""Generated .vscode/tasks.json never binds a project script as a default (fleet round 3, 2026-10-11).

Defect: Tracers-Reporting-Automation's generated tasks made ``script: install_tracers_purge_cron``
(it installs a cron job) the default build task, one keystroke from running. These pin: no
``scripts/*.sh`` is ever a group default, and a command that schedules jobs or purges/deletes data
is not emitted as a task at all.
"""

from __future__ import annotations

import json
import re

import pytest

from agentteams.vscode_tasks import (
    ProjectCommand,
    _assign_group_defaults,
    _refresh_jsonc_sentinels,
    discover_project_commands,
    hazardous_command_name,
    render_tasks_json,
    sentinel_merge,
)


def _scripts(tmp_path, files: dict[str, str]):
    d = tmp_path / "scripts"
    d.mkdir()
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")


def test_tracers_scenario_cron_installer_is_neither_default_nor_emitted(tmp_path, capsys):
    _scripts(tmp_path, {
        "install_tracers_purge_cron.sh": "#!/bin/bash\n(crontab -l; echo '0 3 * * * x') | crontab -\n",
        "purge_tracers_store.sh": "#!/bin/bash\nrm -rf store\n",
        "closeout_check.sh": "#!/bin/bash\necho ok\n",
        "build_docs.sh": "#!/bin/bash\nmkdocs build\n",
    })
    tasks = json.loads(render_tasks_json(discover_project_commands(tmp_path)))["tasks"]
    labels = {t["label"] for t in tasks}
    assert "script: install_tracers_purge_cron" not in labels
    assert "script: purge_tracers_store" not in labels
    assert {"script: closeout_check", "script: build_docs"} <= labels
    for task in tasks:
        group = task["group"]
        assert not (isinstance(group, dict) and group.get("isDefault")), task["label"]
    assert "install_tracers_purge_cron.sh not offered" in capsys.readouterr().err


@pytest.mark.parametrize("body", [
    "crontab /tmp/x\n", "launchctl load ~/Library/LaunchAgents/x.plist\n",
    "systemctl --user enable x.timer\n", "psql -c 'DELETE FROM events'\n",
    "sqlite3 db 'DROP TABLE t'\n", "rm -rf ~/data\n", "find store -mtime +30 -delete\n",
])
def test_innocuous_name_with_hazardous_body_is_skipped(tmp_path, body):
    _scripts(tmp_path, {"setup.sh": "#!/bin/bash\n" + body})
    assert discover_project_commands(tmp_path) == []


@pytest.mark.parametrize("name", [
    "install_tracers_purge_cron", "purge-store", "cron", "install-crontab", "launchd_agent",
    "delete_old_rows", "wipe-db", "systemd-unit",
])
def test_hazardous_names(name):
    assert hazardous_command_name(name)


@pytest.mark.parametrize("name", ["build", "test", "lint", "install", "acronym-check", "deploy",
                                  "test-scheduler", "timer-tests"])
def test_ordinary_names(name):
    assert not hazardous_command_name(name)


def test_declared_tooling_keeps_its_defaults_but_hazards_are_dropped(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": "jest", "build": "tsc", "purge-cache": "rm -rf c"}}), encoding="utf-8")
    _scripts(tmp_path, {"build_all.sh": "#!/bin/bash\nmake\n"})
    cmds = {c.label: c for c in discover_project_commands(tmp_path)}
    assert "npm: purge-cache" not in cmds
    assert cmds["npm: test"].is_default and cmds["npm: build"].is_default
    assert cmds["script: build_all"].group == "build" and not cmds["script: build_all"].is_default


def test_only_scripts_means_no_default_at_all(tmp_path):
    _scripts(tmp_path, {"test.sh": "#!/bin/bash\npytest\n", "build.sh": "#!/bin/bash\nmake\n"})
    assert not any(c.is_default for c in discover_project_commands(tmp_path))


def test_npm_script_with_hazardous_body_is_dropped(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": "jest", "nightly": "crontab ops/cron.txt"}}), encoding="utf-8")
    assert [c.label for c in discover_project_commands(tmp_path)] == ["npm: test"]


def test_install_and_deploy_are_never_the_default():
    cmds = [ProjectCommand("make: install", "make install", group="build"),
            ProjectCommand("make: build", "make build", group="build")]
    _assign_group_defaults(cmds)
    assert [c.is_default for c in cmds] == [False, True]


_JSONC = """{
  // hand-written header comment
  "version": "2.0.0",
  "tasks": [
    {
      "label": "mine",
      "type": "shell",
      "command": "echo mine"
    },
    {
      "label": "script: install_tracers_purge_cron",
      "type": "shell",
      "command": "bash scripts/install_tracers_purge_cron.sh",
      "group": {"kind": "build", "isDefault": true},
      "detail": "AGENTTEAMS"
    },
    {
      "label": "script: closeout_check",
      "type": "shell",
      "command": "bash scripts/closeout_check.sh",
      "group": {"kind": "test", "isDefault": true},
      "detail": "AGENTTEAMS"
    }
  ]
}
"""


def test_jsonc_stale_generated_tasks_are_removed_and_refreshed(tmp_path):
    """A commented tasks.json keeps its comments and user task, loses the cron default."""
    _scripts(tmp_path, {"closeout_check.sh": "#!/bin/bash\necho ok\n",
                        "install_tracers_purge_cron.sh": "#!/bin/bash\ncrontab x\n"})
    path = tmp_path / "tasks.json"
    path.write_text(_JSONC, encoding="utf-8")
    merged = sentinel_merge(path, render_tasks_json(discover_project_commands(tmp_path)))
    assert "// hand-written header comment" in merged
    assert "install_tracers_purge_cron" not in merged
    parsed = json.loads(re.sub(r"//[^\n]*", "", merged))
    tasks = {t["label"]: t for t in parsed["tasks"]}
    assert "mine" in tasks
    assert tasks["script: closeout_check"]["group"] == {"kind": "test", "isDefault": False}
    # Idempotent on a second run.
    path.write_text(merged, encoding="utf-8")
    assert sentinel_merge(path, render_tasks_json(discover_project_commands(tmp_path))) == merged


def test_jsonc_removing_the_first_and_only_generated_tasks_keeps_valid_json():
    raw = ('{\n  "tasks": [\n    {"label": "a", "detail": "AGENTTEAMS"}, // gone\n'
           '    {"label": "b", "detail": "AGENTTEAMS"},\n    {"label": "u"}\n  ]\n}\n')
    out = _refresh_jsonc_sentinels(raw, [])
    assert json.loads(re.sub(r"//[^\n]*", "", out))["tasks"] == [{"label": "u"}]


# ---------------------------------------------------------------------------
# Makefile recipe bodies are screened like npm script bodies (fleet round 3 follow-up)
# ---------------------------------------------------------------------------

def _makefile(tmp_path, text: str):
    (tmp_path / "Makefile").write_text(text, encoding="utf-8")
    return {c.label: c for c in discover_project_commands(tmp_path)}


@pytest.mark.parametrize("recipe", [
    "crontab ops/cron.txt", "(crontab -l; echo '0 3 * * * x') | crontab -",
    "launchctl load ~/Library/LaunchAgents/x.plist", "systemctl --user enable nightly.timer",
    "cp x.timer ~/.config/systemd/user/", "@rm -rf build", "-rm -fr ~/data", "rm -r -f store",
    "$(RM) -r dist", "find store -mtime +30 -delete", "git clean -fdx",
    "psql -c 'DELETE FROM events'", "sqlite3 db 'DROP TABLE t'",
])
def test_makefile_innocuous_target_with_hazardous_recipe_is_dropped(tmp_path, recipe, capsys):
    cmds = _makefile(tmp_path, f".PHONY: test tidy\ntest:\n\tpytest\ntidy:\n\t{recipe}\n")
    assert list(cmds) == ["make: test"]
    assert "make target 'tidy' not offered" in capsys.readouterr().err


def test_makefile_hazard_is_found_in_rules_with_prerequisites_and_continuations(tmp_path):
    cmds = _makefile(tmp_path, (
        "build: src/main.c  # a comment\n\tcc -o app src/main.c\n\n"
        "# a comment inside the next recipe does not end it\n"
        "reset: build\n\techo resetting\n\n\t# note\n\trm -rf \\\n\t  ~/.cache/app\n"
        "inline: ; crontab ops/cron.txt\n"
        ".PHONY: build reset inline\n"))
    assert set(cmds) == {"make: build"}


def test_makefile_target_running_a_hazardous_target_is_dropped(tmp_path):
    """A target runs its prerequisites and recursive makes, so it inherits their hazard."""
    cmds = _makefile(tmp_path, (
        ".PHONY: test ci nightly schedule rebuild clean\n"
        "test:\n\tpytest\n"
        "schedule:\n\tcrontab ops/cron.txt\n"
        "nightly: schedule\n\techo ok\n"
        "clean:\n\trm -rf build\n"
        "rebuild:\n\t$(MAKE) clean\n\t$(MAKE) test\n"
        "ci: purge-cache test\n"
        "purge-cache:\n\techo\n"))
    assert set(cmds) == {"make: test"}


def test_makefile_ordinary_recipes_are_kept(tmp_path):
    cmds = _makefile(tmp_path, (
        ".PHONY: test build lint\n"
        "test:\n\tpytest -q\n"
        "build:\n\tpython -m build\n\trm -f dist/*.tmp\n"  # rm -f without -r is not screened
        "lint:\n\truff check .\n"
        "VAR := value\nOTHER ::= x\n"))
    assert set(cmds) == {"make: test", "make: build", "make: lint"}
    assert cmds["make: test"].is_default and cmds["make: build"].is_default


@pytest.mark.parametrize("name", ["install", "install-dev", "setup", "bootstrap", "provision",
                                  "deploy", "publish"])
def test_install_like_target_names_are_never_the_default(tmp_path, name):
    cmds = _makefile(tmp_path, f".PHONY: {name}-build\n{name}-build:\n\techo ok\n")
    assert len(cmds) == 1
    cmd = next(iter(cmds.values()))
    assert cmd.group == "build" and not cmd.is_default


@pytest.mark.parametrize("recipe", [
    "pip install -e .", "python -m pip install -r requirements.txt", "npm install", "brew install jq",
    "sudo cp app /usr/local/bin", "install -m 755 app /usr/local/bin/app", "@install app $(PREFIX)/bin",
    "python setup.py install", "cargo install --path .",
])
def test_makefile_install_like_recipe_is_never_the_default(tmp_path, recipe):
    """``make build`` that installs is offered, but never as the one-keystroke default."""
    cmds = _makefile(tmp_path, f".PHONY: build compile\nbuild:\n\t{recipe}\ncompile:\n\tcc x.c\n")
    assert not cmds["make: build"].is_default
    assert cmds["make: compile"].is_default


def test_makefile_target_depending_on_install_is_never_the_default(tmp_path):
    cmds = _makefile(tmp_path, (
        ".PHONY: install test build check\n"
        "install:\n\tpip install -e .\n"
        "test: install\n\tpytest\n"
        "build:\n\t$(MAKE) install\n"
        "check:\n\truff check .\n"))
    assert not cmds["make: install"].is_default
    assert not cmds["make: test"].is_default and not cmds["make: build"].is_default
    assert cmds["make: check"].is_default


# Review follow-ups (@security / @code-hygiene on the Makefile delta).

@pytest.mark.parametrize("makefile", [
    # A conditional inside a recipe does not end it.
    ".PHONY: clean\nclean:\nifeq ($(OS),Windows_NT)\n\tdel build\nelse\n\trm -rf build\nendif\n",
    # A variable or define block the recipe expands.
    ".PHONY: clean\nRMRF = rm -rf\nclean:\n\t$(RMRF) build\n",
    ".PHONY: clean\nRMRF := rm\nRMRF += -rf\nclean:\n\t${RMRF} build\n",
    ".PHONY: clean\ndefine CLEAN\nrm -rf build\nendef\nclean:\n\t$(CLEAN)\n",
    ".PHONY: clean\nCLEAN = $(RM) -r build\nclean:\n\t$(call CLEAN)\n",
    # A changed recipe prefix.
    ".RECIPEPREFIX = >\n.PHONY: clean\nclean:\n> rm -rf build\n",
    # A dot-named (non-special) target is screened, not skipped.
    ".PHONY: .reset\n.reset:\n\trm -rf data\n",
    # A prerequisite built by a pattern rule.
    ".PHONY: tidy\ntidy: cache.tidy-dir\n%.tidy-dir: ; rm -rf $*\n",
    # A recursive make with a directory option, and rm's long option.
    ".PHONY: all clean\nall:\n\t@$(MAKE) -C sub clean\nclean:\n\trm --recursive --force build\n",
])
def test_makefile_hazards_reached_through_make_constructs(tmp_path, makefile):
    assert _makefile(tmp_path, makefile) == {}


def test_makefile_parse_time_hazard_marks_every_target(tmp_path, capsys):
    """``$(shell …)`` runs while make reads the file, so it runs for ``make test`` too."""
    cmds = _makefile(tmp_path, "X := $(shell crontab ops/c.txt)\n.PHONY: test\ntest:\n\tpytest\n")
    assert cmds == {}
    assert "make target 'test' not offered" in capsys.readouterr().err


def test_make_in_prose_is_not_a_recursive_make(tmp_path):
    cmds = _makefile(tmp_path, (
        ".PHONY: all sure\nall:\n\techo make sure it works\nsure:\n\trm -rf x\n"))
    assert set(cmds) == {"make: all"}


def test_makefile_target_specific_variables_and_comments_are_not_rules(tmp_path):
    cmds = _makefile(tmp_path, (
        ".PHONY: test build # all phony\ntest: V = 1\ntest:\n\tpytest\n"
        "build: ## build the wheel\n\tpython -m build\n"))
    assert set(cmds) == {"make: test", "make: build"}
    assert cmds["make: test"].is_default and cmds["make: build"].is_default


def test_oversized_makefile_is_not_offered(tmp_path, capsys):
    from agentteams._makefile_screen import MAKEFILE_MAX_BYTES
    body = ".PHONY: test\ntest:\n\tpytest\n" + "# pad\n" * (MAKEFILE_MAX_BYTES // 6 + 1)
    assert _makefile(tmp_path, body) == {}
    assert "too large to screen" in capsys.readouterr().err


def test_long_header_lines_parse_in_linear_time():
    import time

    from agentteams._makefile_screen import parse_makefile
    start = time.monotonic()
    parse_makefile("a" + " " * 200000 + "\n" + "b" + " x" * 100000 + "\n")
    assert time.monotonic() - start < 2


@pytest.mark.parametrize("recipe", [
    "$(INSTALL) app $(PREFIX)/bin", "cd x && install app /usr/local/bin", "uv pip install -e .",
    "curl -fsSL https://example.invalid/i.sh | sh", "doas cp app /usr/local/bin",
])
def test_more_install_like_recipes_are_never_the_default(tmp_path, recipe):
    cmds = _makefile(tmp_path, f".PHONY: build\nbuild:\n\t{recipe}\n")
    assert not cmds["make: build"].is_default


@pytest.mark.parametrize("makefile", [
    # A conditional runs its $(shell) while make reads the file.
    "ifeq ($(shell crontab -r),)\nendif\n.PHONY: test\ntest:\n\tpytest\n",
    # `#` inside a function call is literal, not a comment.
    "X := $(shell echo '#'; crontab -r)\n.PHONY: test\ntest:\n\tpytest\n",
    # Indirection: the shell call and the hazard reach the immediate value through variables.
    "CMD = crontab ops/c.txt\nRUN = $(shell $(CMD))\nY := $(RUN)\n.PHONY: test\ntest:\n\tpytest\n",
    "define DEF\nx:\n\trm -rf data\nendef\n$(eval $(DEF))\n.PHONY: test\ntest:\n\tpytest\n",
    "OUT != crontab -l\n.PHONY: test\ntest:\n\tpytest\n",
])
def test_makefile_parse_time_hazards_through_indirection(tmp_path, makefile):
    assert _makefile(tmp_path, makefile) == {}


def test_makefile_target_specific_variable_and_empty_recipeprefix(tmp_path):
    cmds = _makefile(tmp_path, (
        ".RECIPEPREFIX = >\n.PHONY: test clean\ntest:\n> pytest\n.RECIPEPREFIX =\n"
        "clean: CMD = rm -rf\nclean:\n\t$(CMD) build\n"))
    assert set(cmds) == {"make: test"}


def test_ordinary_immediate_values_do_not_mark_every_target(tmp_path):
    cmds = _makefile(tmp_path, (
        "CLEAN := rm -rf build\nVERSION := $(shell git describe)\n"
        ".PHONY: test clean\ntest:\n\tpytest\nclean:\n\t$(CLEAN)\n"))
    assert set(cmds) == {"make: test"}


def test_variable_expansion_does_not_blow_up():
    import time

    from agentteams._makefile_screen import screen_makefile
    big = "X0 = " + "a" * 60000 + "\n" + "".join(f"X{i} = $(X{i-1}) $(X{i-1})\n" for i in range(1, 40))
    body = big + "".join(f"t{i}:\n\techo $(X39)\n" for i in range(500))
    start = time.monotonic()
    _, hazardous, _ = screen_makefile(body, lambda t: "crontab" in t, lambda n: False, lambda n: False)
    assert hazardous == set() and time.monotonic() - start < 5


def test_submake_and_install_patterns_are_linear_on_whitespace():
    import time

    from agentteams._makefile_screen import INSTALL_CONTENT_RE, parse_makefile
    start = time.monotonic()
    parse_makefile("t:\n\t" + " " * 200000 + "x\n")
    INSTALL_CONTENT_RE.search(";" + " " * 200000 + "x")
    assert time.monotonic() - start < 2
