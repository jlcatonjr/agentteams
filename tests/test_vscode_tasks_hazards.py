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
