"""WASM plan P14: the WASM text in full team renders, per framework, and WASM-off byte identity.

* On: every agent P01 calls eligible (template tools hold an execute-class tool) carries exactly one WASM section;
  read-only auditors carry none; ``@security`` carries the trigger rows and its review section; the orchestrator
  and the instructions file carry the routing rows and the laundering rule; the reference doc ships. On per-agent
  hosts the text says ``run_wasm`` exactly where P11 put ``run_wasm`` in the front matter.
* Off (absent or ``off``), for every example brief and framework: the render equals one with every ``wasm_policy``
  hook bypassed, and no ``WASM_`` placeholder or WASM text survives.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import analyze, wasm_policy
from agentteams.cli.render_pipeline import TEMPLATES_DIR, _build_final_rendered
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY, wasm_eligible

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
ON_FRAMEWORKS = ("claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md")
AUDITORS = ("security", "adversarial", "conflict-auditor", "code-hygiene", "quality-auditor", "technical-validator")
_TOOLS_RE = re.compile(r"^tools:[ \t]*\[([^\]\n]*)\]", re.MULTILINE)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")  # Copilot body-length warnings


def _manifest(framework: str, brief: dict | None = None, *, wasm: str | None = "on") -> dict:
    manifest = analyze.build_manifest(json.loads(json.dumps(brief or BRIEF)), framework=framework)
    if wasm is not None:
        manifest[WASM_SANDBOX_CLI_KEY] = wasm
    return manifest


def _render(framework: str, manifest: dict) -> dict[str, str]:
    return dict(_build_final_rendered(manifest, FRAMEWORKS[framework](), "x"))


def _by_slug(out: dict[str, str], framework: str) -> dict[str, str]:
    """Rendered agent files keyed by slug (the agents dir only, no references)."""
    exts = (".agent.md", ".md", ".yaml", ".toml")
    found: dict[str, str] = {}
    for path, text in out.items():
        if "/" in path or path.startswith(".."):
            continue
        name = Path(path).name
        for ext in exts:
            if name.endswith(ext):
                found[name[: -len(ext)]] = text
                break
    return found


def _eligible_slugs(manifest: dict) -> set[str]:
    """Agents whose template declares an execute-class tool (P01's rule, read from the templates)."""
    out = set()
    for spec in manifest["output_files"]:
        name = Path(spec.get("path", "")).name
        if not name.endswith(".agent.md") or "team-builder" in name:
            continue
        tpl = TEMPLATES_DIR / (spec.get("template") or "")
        if not tpl.is_file():
            continue
        m = _TOOLS_RE.search(tpl.read_text(encoding="utf-8"))
        tools = [t.strip().strip("'\"") for t in m.group(1).split(",")] if m else None
        if wasm_eligible({"slug": name[:-9], "tools": tools}):
            out.add(name[:-9])
    return out


@pytest.fixture(scope="module", params=ON_FRAMEWORKS)
def rendered_on(request):
    framework = request.param
    brief = {**BRIEF, "goose_tool_scoping": "grant"} if framework == "goose" else BRIEF
    manifest = _manifest(framework, brief)
    return framework, manifest, _render(framework, manifest)


def test_every_eligible_agent_has_exactly_one_section(rendered_on):
    framework, manifest, out = rendered_on
    agents = _by_slug(out, framework)
    eligible = _eligible_slugs(manifest)
    assert eligible >= {"orchestrator", "cleanup", "git-operations", "navigator"}
    for slug in eligible:
        assert agents[slug].count("## WASM Sandbox:") == 1, slug
    for slug in AUDITORS[1:]:
        assert "WASM Sandbox" not in agents[slug], slug


def test_security_has_the_trigger_rows_and_review_section_only(rendered_on):
    framework, _manifest_, out = rendered_on
    sec = _by_slug(out, framework)["security"]
    assert "Laundering / sandbox relaxation" in sec and "agentteams-wasm-mcp.py" in sec
    assert "## WASM Sandbox: Reviewing Grants and Relaxations" in sec
    assert "Run Code Through" not in sec and "Your native shell" not in sec


def test_orchestrator_and_instructions_route_code_and_grants(rendered_on):
    framework, _manifest_, out = rendered_on
    orch = _by_slug(out, framework)["orchestrator"]
    assert "never the orchestrator's shell" in orch and "`@security` first" in orch  # routing rows
    assert "Never launder code." in orch
    [instructions] = [t for p, t in out.items() if Path(p).name in {"CLAUDE.md", "AGENTS.md",
                                                                     "copilot-instructions.md"}]
    assert "## WASM Sandbox: Routing Code and Grants" in instructions and "Never launder code." in instructions
    assert any(p.endswith(wasm_policy.REFERENCE_PATH) for p in out)


def _launches_wasm(framework: str, text: str) -> bool:
    """Whether the agent's own grant channel (not its prose) launches ``agentteams_wasm``."""
    if framework == "claude":
        return "agentteams_wasm:" in text.split("\n---\n", 1)[0]
    return re.search(r'^\s*-?\s*name: "?agentteams_wasm"?\s*$', text, re.MULTILINE) is not None


def _sections(text: str) -> list[str]:
    return [part.split("\n## ", 1)[0] for part in text.split("## WASM Sandbox:")[1:]]


def test_per_agent_text_matches_the_front_matter(rendered_on):
    framework, manifest, out = rendered_on
    if framework not in ("claude", "goose"):
        pytest.skip("per-agent wiring only on claude and goose")
    wired = set(manifest["wasm_wiring"]["frameworks"][framework]["agents"])
    assert wired >= {"orchestrator", "cleanup"}
    for slug, text in _by_slug(out, framework).items():
        assert (slug in wired) == _launches_wasm(framework, text), slug
        if slug in wired and slug != "orchestrator":
            assert "## WASM Sandbox: Run Code Through `run_wasm`" in text, slug


def test_no_section_ever_claims_enforcement(rendered_on):
    framework, _manifest_, out = rendered_on
    for slug, text in _by_slug(out, framework).items():
        for section in _sections(text):
            if section.startswith(" Reviewing"):
                continue  # @security: "a claim that a WASM limit is enforced ... is a finding"
            assert "enforced" not in section.lower(), (slug, section[:200])


# --- WASM off: byte identity over every example brief and framework ---------------------------------------------------

def _briefs() -> list[Path]:
    return sorted(p for p in (REPO / "examples").glob("*/*brief.json")
                  if "wasm_sandbox" not in json.loads(p.read_text(encoding="utf-8")))


def _bypassed(monkeypatch) -> None:
    monkeypatch.setattr(wasm_policy, "apply", lambda content, *a, **k: content)
    monkeypatch.setattr(wasm_policy, "apply_instructions", lambda content, *a, **k: content)
    monkeypatch.setattr(wasm_policy, "populate_placeholders", lambda manifest: None)
    monkeypatch.setattr(wasm_policy, "enabled", lambda *a, **k: False)


@pytest.mark.parametrize("brief_path", _briefs(), ids=lambda p: p.parent.name)
@pytest.mark.parametrize("wasm", [None, "off"])
def test_wasm_off_renders_byte_identically(brief_path, wasm, monkeypatch):
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    for framework in sorted(FRAMEWORKS):
        if framework == "wasm" and wasm == "off":
            continue  # --framework wasm with an explicit off is refused upstream (P08)
        live = _render(framework, _manifest(framework, brief, wasm=wasm))
        for text in live.values():
            assert "{WASM_" not in text and "## WASM Sandbox:" not in text
        with monkeypatch.context() as mp:
            _bypassed(mp)
            plain = _render(framework, _manifest(framework, brief, wasm=wasm))
        assert live == plain, framework
