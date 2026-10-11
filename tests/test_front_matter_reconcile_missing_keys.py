"""--reconcile-front-matter reports and inserts capability keys absent on disk.

Biographies pilot rehearsal (2026-10-11): on an existing Claude team under write_policy, reconcile
set the granted agents' ``tools:`` to the runner tools but never added the ``mcpServers`` block,
because it skipped template keys the deployed file lacked and could not rewrite a block value.
"""
from __future__ import annotations

from pathlib import Path

from agentteams.front_matter_reconcile import (
    _front_matter_map,
    apply_divergences,
    find_divergences,
    format_report,
)

_RUNNER = (
    "mcpServers:\n"
    "  - agentteams_runner:\n"
    "      type: stdio\n"
    '      command: "/usr/bin/python3"\n'
    '      args: ["-I", "-S", ".agentteams/bin/agentteams-runner-mcp.py", "--agent", "primary-producer"]\n'
)
_TOOLS = "tools: Read, Grep, Glob, mcp__agentteams_runner__write_file\n"
_TEMPLATE = f"---\nname: Primary Producer\n{_TOOLS}{_RUNNER}---\n<!-- body -->\nTemplate body.\n"
_DEPLOYED = "---\nname: Primary Producer\ntools: Read, Edit, Write\n---\n<!-- body -->\nProject body kept.\n"


def _deploy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "primary-producer.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_capability_block_is_reported(tmp_path: Path) -> None:
    _deploy(tmp_path, _DEPLOYED)
    found = find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path)
    by_key = {d.key: d for d in found}
    assert set(by_key) == {"tools", "mcpServers"}
    assert by_key["mcpServers"].deployed is None and by_key["mcpServers"].is_capability
    assert "deployed: (absent)" in format_report(found)


def test_apply_inserts_block_and_keeps_body(tmp_path: Path) -> None:
    path = _deploy(tmp_path, _DEPLOYED)
    found = find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path)
    applied = apply_divergences(found, tmp_path)
    assert all("[CAPABILITY GRANT CHANGED]" in line for line in applied), applied
    text = path.read_text(encoding="utf-8")
    assert _front_matter_map(text)["mcpServers"] == _front_matter_map(_TEMPLATE)["mcpServers"]
    assert _front_matter_map(text)["tools"] == _front_matter_map(_TEMPLATE)["tools"]
    assert text.endswith("---\n<!-- body -->\nProject body kept.\n")
    assert find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path) == []  # idempotent


def test_existing_block_value_is_replaced_whole(tmp_path: Path) -> None:
    stale = _RUNNER.replace('"primary-producer"', '"old-agent"')
    path = _deploy(tmp_path, f"---\nname: Primary Producer\n{_TOOLS}{stale}---\nbody\n")
    found = find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path)
    assert [d.key for d in found] == ["mcpServers"]
    assert apply_divergences(found, tmp_path) == ["applied primary-producer.md: mcpServers  [CAPABILITY GRANT CHANGED]"]
    assert '"old-agent"' not in path.read_text(encoding="utf-8")
    assert find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path) == []


def test_absent_non_capability_key_is_not_reported(tmp_path: Path) -> None:
    _deploy(tmp_path, f"---\nname: Primary Producer\n{_TOOLS}{_RUNNER}---\nbody\n")
    template = f"---\nname: Primary Producer\ndescription: new\n{_TOOLS}{_RUNNER}---\nbody\n"
    assert find_divergences([("primary-producer.md", template)], tmp_path) == []


def test_file_without_front_matter_is_not_given_one(tmp_path: Path) -> None:
    _deploy(tmp_path, "no front matter here\n")
    assert find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path) == []


# --- @security conditions (2026-10-11) -------------------------------------------------------

def test_added_key_is_labelled_added(tmp_path: Path) -> None:
    _deploy(tmp_path, f"---\nname: Primary Producer\n{_TOOLS}---\nbody\n")
    applied = apply_divergences(find_divergences([("primary-producer.md", _TEMPLATE)], tmp_path), tmp_path)
    assert applied == ["ADDED (absent) primary-producer.md: mcpServers  [CAPABILITY GRANT CHANGED]"]


def test_inline_value_with_continuation_is_never_applied(tmp_path: Path) -> None:
    # The flat map reads `tools: Read` + `# note` as a bare block; writing that would leave
    # `tools:` empty, which on Claude inherits every tool.
    template = "---\nname: A\ntools: Read\n# note\n---\nbody\n"
    path = _deploy(tmp_path, "---\nname: A\ntools: Read, Edit, Write\n---\nbody\n")
    found = find_divergences([("primary-producer.md", template)], tmp_path)
    assert [(d.key, d.ambiguous) for d in found] == [("tools", True)]
    applied = apply_divergences(found, tmp_path)
    assert applied[0].startswith("SKIPPED primary-producer.md: tools — inline value with continuation")
    assert "tools: Read, Edit, Write" in path.read_text(encoding="utf-8")


def test_replace_is_anchored_to_a_line_start_in_front_matter(tmp_path: Path) -> None:
    deployed = ("---\nname: A\ndescription: \"see allowed-tools: Read\"\nallowed-tools: Read\ntools: Read\n---\n"
                "tools: Read\n")
    path = _deploy(tmp_path, deployed)
    template = "---\nname: A\ndescription: \"see allowed-tools: Read\"\nallowed-tools: Read\ntools: Read, Grep\n---\nx\n"
    applied = apply_divergences(find_divergences([("primary-producer.md", template)], tmp_path), tmp_path)
    assert applied == ["applied primary-producer.md: tools  [CAPABILITY GRANT CHANGED]"]
    text = path.read_text(encoding="utf-8")
    assert "allowed-tools: Read\ntools: Read, Grep\n---\ntools: Read\n" in text  # body and sibling key untouched


def test_high_risk_keys_are_never_added(tmp_path: Path) -> None:
    template = ("---\nname: A\ntools: Read\nhooks:\n  PreToolUse: []\nmemory: project\n"
                "permissionMode: bypassPermissions\n---\nbody\n")
    path = _deploy(tmp_path, "---\nname: A\ntools: Read\n---\nbody\n")
    found = find_divergences([("primary-producer.md", template)], tmp_path)
    assert {d.key for d in found} == {"hooks", "memory", "permissionMode"}
    applied = apply_divergences(found, tmp_path)
    assert all(line.startswith("SKIPPED") and "not added" in line for line in applied), applied
    assert path.read_text(encoding="utf-8") == "---\nname: A\ntools: Read\n---\nbody\n"


def test_safe_permission_mode_is_added(tmp_path: Path) -> None:
    _deploy(tmp_path, "---\nname: A\ntools: Read\n---\nbody\n")
    template = "---\nname: A\ntools: Read\npermissionMode: plan\n---\nbody\n"
    applied = apply_divergences(find_divergences([("primary-producer.md", template)], tmp_path), tmp_path)
    assert applied == ["ADDED (absent) primary-producer.md: permissionMode  [CAPABILITY GRANT CHANGED]"]
