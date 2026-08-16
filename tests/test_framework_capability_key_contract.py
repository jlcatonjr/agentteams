"""test_framework_capability_key_contract.py — the emitted capability key must be one the
target runtime actually reads.

**The defect this exists to prevent.** Every Claude agent file this module generated between
the adapter's introduction and 2026-08-06 declared its tool grant under `allowed-tools:`.
Claude Code's subagent front-matter schema defines `name`, `description`, `tools`,
`disallowedTools`, `model` and `permissionMode` — `allowed-tools` is the *slash-command* key
and is ignored in a subagent file. Its documented behaviour when `tools` is absent is to
"inherit every tool available to subagents".

So the grant was inert. `@security`, whose own body says "You are **read-only**… this is a
capability limit, not a stylistic preference", ran with Write, Edit and Bash — as did every
other self-declared read-only governance agent, in every team this module produced.

`tests/test_frameworks.py` asserted `"allowed-tools:" in result`. That test passed for the
whole life of the defect: it pinned the emission, and nothing anywhere asserted that the
emitted key was one the *runtime* honours. That gap is what this file closes.

Full finding: references/plans/constitutional-redteam-audit-2026-08-06.report.md (F-1 / W1).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams.frameworks.claude import (
    CLAUDE_CAPABILITY_KEY,
    CLAUDE_LEGACY_CAPABILITY_KEY,
    ClaudeAdapter,
)
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

ADAPTERS = {
    "claude": ClaudeAdapter,
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
}


def get_adapter(framework: str):
    return ADAPTERS[framework]()

REPO = Path(__file__).resolve().parents[1]

#: Capability keys each target runtime's agent-file schema actually reads.
#:
#: Sourced from each framework's published documentation, NOT from this module's own emitter —
#: an expectation derived from the code under test cannot detect the code being wrong. Claude:
#: code.claude.com/docs/en/sub-agents (verified 2026-08-06). copilot-vscode: the `tools:` inline
#: list this repo's own `.github/agents/*.agent.md` carry.
RUNTIME_CAPABILITY_KEYS: dict[str, frozenset[str]] = {
    "claude": frozenset({"tools", "disallowedTools"}),
    "copilot-vscode": frozenset({"tools"}),
    # P1 (2026-08-15): copilot-cli converged onto copilot-vscode's front-matter surface
    # (delegates render_agent_file), so it carries the same capability key.
    "copilot-cli": frozenset({"tools"}),
}

#: Frameworks whose agent files carry NO front matter by design, so there is no capability key
#: to get wrong. Named explicitly rather than omitted, so adding a framework forces a decision
#: about which list it belongs on instead of silently escaping this contract. Empty as of P1
#: (2026-08-15) — copilot-cli was the only member and now has front matter (see
#: RUNTIME_CAPABILITY_KEYS above); kept as a frozenset rather than removed so a future
#: no-front-matter framework has an obvious place to register.
FRAMEWORKS_WITHOUT_FRONT_MATTER: frozenset[str] = frozenset()

#: Keys a runtime silently ignores in an agent file. Emitting one of these as the capability
#: declaration is the defect, so they are named rather than merely absent from the set above.
RUNTIME_IGNORED_CAPABILITY_KEYS: dict[str, frozenset[str]] = {
    "claude": frozenset({"allowed-tools", "allowed_tools"}),
}


def test_claude_capability_key_constant_is_a_key_the_runtime_reads() -> None:
    assert CLAUDE_CAPABILITY_KEY in RUNTIME_CAPABILITY_KEYS["claude"]
    assert CLAUDE_LEGACY_CAPABILITY_KEY in RUNTIME_IGNORED_CAPABILITY_KEYS["claude"], (
        "the legacy constant must name a key the runtime IGNORES — it exists so detectors "
        "still recognise deployed files, not so anything emits it again"
    )


@pytest.mark.parametrize("framework", sorted(RUNTIME_CAPABILITY_KEYS))
def test_required_front_matter_keys_declare_a_readable_capability_key(framework: str) -> None:
    """Every adapter's declared required keys must include a capability key its runtime reads."""
    adapter = get_adapter(framework)
    required = set(adapter.required_front_matter_keys())
    readable = RUNTIME_CAPABILITY_KEYS[framework]
    ignored = RUNTIME_IGNORED_CAPABILITY_KEYS.get(framework, frozenset())

    assert not (required & ignored), (
        f"{framework} declares a capability key its runtime ignores: "
        f"{sorted(required & ignored)}. A grant under an ignored key is not a limit — the "
        f"runtime falls back to inheriting every tool."
    )
    # copilot-vscode carries `tools` among its required keys; claude now does too.
    assert required & readable, (
        f"{framework} declares no capability key at all: {sorted(required)}. "
        f"Expected one of {sorted(readable)}."
    )


@pytest.mark.parametrize("framework", sorted(FRAMEWORKS_WITHOUT_FRONT_MATTER))
def test_front_matter_free_frameworks_declare_no_keys(framework: str) -> None:
    """A framework on the no-front-matter list must actually declare no keys.

    Otherwise the list becomes a way to opt out of the capability-key contract by asserting
    something untrue about the framework — which is the shape of the original defect.
    """
    assert not get_adapter(framework).required_front_matter_keys()


def test_rendered_claude_agent_declares_the_readable_key() -> None:
    """End-to-end: the bytes an adapter writes carry the key, not just its declaration."""
    adapter = get_adapter("claude")
    src = (
        "---\n"
        "name: Security\n"
        "description: sentinel\n"
        "tools: ['read', 'search']\n"
        "---\n\n"
        "# Security\n\nBody.\n"
    )
    out = adapter.render_agent_file(src, "security", {"project_name": "Demo"})
    front_matter = out.split("---")[1]
    assert re.search(rf"^{re.escape(CLAUDE_CAPABILITY_KEY)}:", front_matter, re.MULTILINE), (
        f"rendered Claude agent has no `{CLAUDE_CAPABILITY_KEY}:` line:\n{front_matter}"
    )
    assert not re.search(
        rf"^{re.escape(CLAUDE_LEGACY_CAPABILITY_KEY)}:", front_matter, re.MULTILINE
    ), "rendered Claude agent still emits the ignored legacy key"


def test_read_only_grant_survives_the_key_change() -> None:
    """The narrow grant must still be narrow — renaming the key must not widen it."""
    adapter = get_adapter("claude")
    src = (
        "---\nname: Security\ndescription: sentinel\ntools: ['read', 'search']\n---\n\n"
        "# Security\n"
    )
    out = adapter.render_agent_file(src, "security", {"project_name": "Demo"})
    line = next(
        l for l in out.splitlines() if l.startswith(f"{CLAUDE_CAPABILITY_KEY}:")
    )
    granted = {t.strip() for t in line.split(":", 1)[1].split(",")}
    assert granted == {"Read", "Grep", "Glob"}, granted
    assert not granted & {"Write", "Edit", "Bash"}, (
        "a read/search grant must not map to any write or execute tool"
    )


def test_this_repository_deployed_agents_declare_the_readable_key() -> None:
    """The deployed team is the thing that was actually vulnerable — pin it directly.

    A fix to the emitter does not fix already-generated files: front matter is preserved
    verbatim across `--update --merge`. This asserts the migration actually reached disk.
    """
    agents_dir = REPO / ".claude" / "agents"
    if not agents_dir.is_dir():
        pytest.skip("no deployed .claude/agents team in this checkout")

    offenders: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        fm = text[3:end] if end != -1 else ""
        keys = {
            line.split(":", 1)[0].strip()
            for line in fm.splitlines()
            if ":" in line and not line.startswith((" ", "\t", "-"))
        }
        if not keys & RUNTIME_CAPABILITY_KEYS["claude"]:
            offenders.append(path.name)

    assert not offenders, (
        "deployed agents declare no capability key Claude Code reads, so they inherit every "
        f"tool regardless of what their body claims: {offenders}"
    )
