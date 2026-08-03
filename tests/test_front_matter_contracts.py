"""test_front_matter_contracts.py — a framework's declared header must match what it emits.

`required_front_matter_keys()` was added when the post-generation audit was found demanding
copilot-vscode's five keys of every framework, producing 83 findings against a correct tree.
Contracts were declared for copilot-vscode and claude — the two whose headers had been
inspected — and the other adapters were left on the empty default, stated at the time as "not
guessed" rather than "none".

That is a distinction nothing could see from outside, and it is the kind that rots: an adapter
that starts emitting front matter would silently go unchecked, exactly as claude did.

Both are now examined and both genuinely emit nothing:

* **agents_md** strips front matter and prepends `# {Name}` — plain Markdown by construction.
* **goose** emits a recipe YAML; there is no front-matter block at all.

This ties every declaration to real emitted output, so the answer cannot drift from the code
without failing here.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))

BRIEF = REPO_ROOT / ".github/agents/_build-description.json"

#: The frameworks `tests/test_integration._run_pipeline` wires. goose and agents_md are checked
#: at the adapter level below rather than end-to-end, and are named rather than omitted.
PIPELINE_FRAMEWORKS = ["copilot-vscode", "claude", "copilot-cli"]


def _adapter(framework_id: str):
    from agentteams.frameworks.registry import FRAMEWORKS

    return FRAMEWORKS[framework_id]()


def test_every_registered_framework_declares_a_contract_explicitly() -> None:
    """Inheriting the empty default is no longer acceptable: state it or state keys.

    `required_front_matter_keys` must be defined on the adapter itself, not resolved to
    `FrameworkAdapter`'s default — that is what makes "none" a finding rather than a silence.
    """
    from agentteams.frameworks.base import FrameworkAdapter
    from agentteams.frameworks.registry import FRAMEWORKS

    inherited = [
        fid
        for fid, cls in FRAMEWORKS.items()
        if cls.required_front_matter_keys is FrameworkAdapter.required_front_matter_keys
    ]
    assert not inherited, (
        f"framework(s) {inherited} inherit the empty front-matter contract instead of "
        "declaring one. Declare `()` with a docstring saying the header was inspected and is "
        "absent, or declare the keys."
    )


def test_at_least_two_frameworks_declare_a_non_empty_contract() -> None:
    """Anti-vacuity. `declared == emitted` is trivially true when everything declares nothing.

    Without this, a regression that emptied every contract would leave the audit checking no
    front matter anywhere and this file green.
    """
    from agentteams.frameworks.registry import FRAMEWORKS

    non_empty = {
        fid: _adapter(fid).required_front_matter_keys()
        for fid in FRAMEWORKS
        if _adapter(fid).required_front_matter_keys()
    }
    assert len(non_empty) >= 2, (
        f"only {len(non_empty)} framework(s) declare front-matter keys: {non_empty}. "
        "The comparison below would pass without checking anything."
    )


@pytest.mark.parametrize("framework", PIPELINE_FRAMEWORKS)
def test_declared_keys_are_present_in_real_emitted_agents(framework: str, tmp_path) -> None:
    """The tie to reality: every declared key must appear in the files actually rendered."""
    from test_integration import _run_pipeline

    from agentteams.audit_types import _agent_slug, _is_agent_file

    if not BRIEF.exists():
        pytest.skip("self brief absent")

    adapter = _adapter(framework)
    required = adapter.required_front_matter_keys()
    ext = adapter.get_file_extension("agent")

    out = tmp_path / "agents"
    _run_pipeline(BRIEF, out, framework=framework)

    if not required:
        pytest.skip(
            f"{framework} declares no front-matter contract; an empty contract asserts "
            "NOTHING about the header, it does not forbid one — see the copilot-cli docstring"
        )

    checked = 0
    missing: list[str] = []
    for path in sorted(out.rglob(f"*{ext}")):
        rel = path.relative_to(out).as_posix()
        if not _is_agent_file(rel, ext):
            continue
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---"), f"[{framework}] {rel} has no front matter"
        header = text.split("---", 2)[1]
        for key in required:
            if f"{key}:" not in header:
                missing.append(f"{rel}: {key}")
        checked += 1
        assert _agent_slug(rel, ext), rel

    assert checked >= 20, f"[{framework}] only {checked} agent files checked; setup regressed"
    assert not missing, f"[{framework}] declared keys absent from emitted files: {missing[:8]}"


def test_an_empty_contract_carries_a_stated_reason() -> None:
    """A bare `return ()` is indistinguishable from the default it replaced.

    The ambiguity this file exists to remove is between "inspected, and there is none" and
    "not yet inspected". Only a docstring carries that difference.
    """
    from agentteams.frameworks.registry import FRAMEWORKS

    unexplained = []
    for fid, cls in FRAMEWORKS.items():
        if cls().required_front_matter_keys():
            continue
        if not (cls.required_front_matter_keys.__doc__ or "").strip():
            unexplained.append(fid)
    assert not unexplained, (
        f"framework(s) {unexplained} declare an empty front-matter contract with no stated "
        "reason. Say that the header was inspected and is absent."
    )


def test_the_frameworks_that_strip_front_matter_really_emit_none() -> None:
    """The examined finding for agents_md and goose, asserted rather than asserted-about.

    Neither is wired into `tests/test_integration._run_pipeline`, so they are checked at the
    adapter level: feed each a rendered file that HAS front matter and confirm the emitted
    agent does not. That is the fact behind their empty contract.
    """
    rendered = (
        "---\nname: N\ndescription: d\ntools: ['read']\nmodel: m\n---\n\n"
        "## Responsibilities\n\nDo the thing.\n"
    )
    manifest = {"project_name": "P", "framework": "x", "agent_slug_list": [], "components": []}

    for fid in ("agents-md", "goose"):
        adapter = _adapter(fid)
        assert adapter.required_front_matter_keys() == (), fid
        out = adapter.render_agent_file(rendered, "navigator", manifest)
        assert not out.lstrip().startswith("---\nname:"), (
            f"{fid} emits YAML front matter but declares no contract for it"
        )
