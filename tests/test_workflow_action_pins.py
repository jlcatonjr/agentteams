"""Every GitHub Action a workflow uses is pinned by full commit SHA (remediation row 506).

A tag such as ``actions/checkout@v7`` can be moved to new code by the action's owner, and that code then runs in
CI with the job's token. Each ``uses:`` must therefore name a 40-hex commit with the exact release tag as a trailing
comment, and each pin must match ``references/dependency-pins.json`` ``github_actions``. Bump procedure:
``references/git-procedures.md`` section J.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO / ".github" / "workflows"
_PINS = _REPO / "references" / "dependency-pins.json"

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
_PINNED = re.compile(r"^(?P<action>[\w.-]+/[\w.-]+(?:/[\w./-]+)?)@(?P<sha>[0-9a-f]{40})$")
_TAG_COMMENT = re.compile(r"^\s+#\s+(?P<tag>v\d+\.\d+\.\d+)\s*$")
_HOW = "pin it by commit SHA with '# vX.Y.Z' (references/git-procedures.md section J)"


def _scan(name: str, text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return the (action, sha, tag) pins in one workflow text, and a problem line for each unpinned ref."""
    pins: list[tuple[str, str, str]] = []
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _USES.match(line)
        if not m:
            continue
        ref = m["ref"].strip("'\"")
        if ref.startswith("./"):
            continue
        pinned, tag = _PINNED.match(ref), _TAG_COMMENT.match(m["rest"])
        if not pinned or not tag:
            problems.append(f"{name}:{lineno}: {line.strip()} — {_HOW}")
            continue
        pins.append((pinned["action"], pinned["sha"], tag["tag"]))
    return pins, problems


def _workflow_pins() -> list[tuple[str, str, str]]:
    pins: list[tuple[str, str, str]] = []
    for path in sorted(_WORKFLOWS.glob("*.y*ml")):
        found, _ = _scan(path.name, path.read_text(encoding="utf-8"))
        pins.extend(found)
    return pins


def _pin_mismatches(pins: list[tuple[str, str, str]], recorded: list[dict]) -> list[str]:
    """Return why the workflow pins disagree with the recorded ``github_actions`` entries (empty when they agree)."""
    table = {r["action"]: (r["sha"], r["tag"]) for r in recorded}
    problems: list[str] = []
    seen: dict[str, tuple[str, str]] = {}
    for action, sha, tag in pins:
        if action not in table:
            problems.append(f"{action}@{sha} is not recorded in dependency-pins.json github_actions")
        elif table[action] != (sha, tag):
            problems.append(f"{action}@{sha} # {tag} differs from the recorded {table[action]}")
        if seen.setdefault(action, (sha, tag)) != (sha, tag):
            problems.append(f"{action} is pinned to more than one commit across workflows")
    return problems


def test_every_workflow_action_is_pinned_by_commit_sha() -> None:
    problems: list[str] = []
    paths = sorted(_WORKFLOWS.glob("*.y*ml"))
    assert paths, "no workflows found"
    for path in paths:
        problems.extend(_scan(path.name, path.read_text(encoding="utf-8"))[1])
    assert not problems, "\n".join(problems)
    assert _workflow_pins(), "no action references found"


def test_the_checker_refuses_a_tag_ref_and_a_sha_without_its_tag() -> None:
    text = (
        "steps:\n"
        "  - uses: actions/checkout@v7\n"
        "  - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0\n"
        "  - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0   # v7\n"
        "  - uses: ./.github/actions/local\n"
        "  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1   # v7.0.1\n"
    )
    pins, problems = _scan("inline.yml", text)
    assert [p.split(":")[1] for p in problems] == ["2", "3", "4"]
    assert pins == [("actions/checkout", "3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1")]


def test_workflow_pins_match_the_pins_file() -> None:
    recorded = json.loads(_PINS.read_text(encoding="utf-8"))["github_actions"]
    for row in recorded:
        assert re.fullmatch(r"[0-9a-f]{40}", row["sha"]) and re.fullmatch(r"v\d+\.\d+\.\d+", row["tag"])
        assert row["commit_date"] and row["verified_on"] and row["vuln_search"]
    pins = _workflow_pins()
    assert not _pin_mismatches(pins, recorded)
    assert {a for a, _, _ in pins} == {r["action"] for r in recorded}, "a recorded pin is no longer used"


def test_the_cross_check_refuses_a_missing_or_divergent_pin() -> None:
    recorded = json.loads(_PINS.read_text(encoding="utf-8"))["github_actions"]
    pins = _workflow_pins()
    missing = [r for r in recorded if r["action"] != "actions/checkout"]
    assert any("actions/checkout" in p for p in _pin_mismatches(pins, missing))
    other = "0" * 40
    split = pins + [("actions/checkout", other, "v7.0.0")]
    assert any("more than one commit" in p for p in _pin_mismatches(split, recorded))
