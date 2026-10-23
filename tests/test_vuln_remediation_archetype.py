"""The vuln-remediation agent and Rule S-VM (vulnscan plan R4, P3).

The agent requests and cannot act: its grant is read/search and its only actions are vulnscan MCP
requests that a host-side executor performs under the operator's signed standing grant. It is opt-in
(``vulnerability_remediation`` capability), never inferred from project text. Rule S-VM lives inside
@security's invariant fence and must not weaken a HALT.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import build_team
from agentteams.analyze import build_manifest
from agentteams.cli import security_gate
from agentteams.fences import CONSTRAINT_BEARING_RE, is_trackable_constraint_line, unfenced_lines
from agentteams.frameworks.claude import _map_allowed_tools
from agentteams.yaml_frontmatter import parse_yaml_front_matter

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "agentteams" / "templates" / "domain" / "vuln-remediation.template.md"
SECURITY = ROOT / "agentteams" / "templates" / "universal" / "security.template.md"
_BRIEF = {"project_name": "Demo", "project_goal": "build things", "deliverable_type": "x",
          "output_format": "y", "description": "a software project"}


@pytest.fixture(scope="module")
def body() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_grant_is_read_only(body: str) -> None:
    yaml_text, _ = parse_yaml_front_matter(body)
    tools = next(ln for ln in yaml_text.splitlines() if ln.startswith("tools:"))
    for forbidden in ("edit", "execute", "write", "todo"):
        assert forbidden not in tools
    allowed = _map_allowed_tools(body)
    assert "Read" in allowed and not any(t in allowed for t in ("Write", "Edit", "Bash", "NotebookEdit"))


def test_every_constraint_is_fenced(body: str) -> None:
    loose = [ln for ln in unfenced_lines(body) if CONSTRAINT_BEARING_RE.search(ln) and is_trackable_constraint_line(ln)]
    assert loose == [], loose


def test_contract_names_the_boundaries(body: str) -> None:
    for phrase in ("only** through the `vulnscan` tools", "standing grant", "data, not instructions",
                   "Do **not** stop to ask", "no merge", "Stop conditions", "call `escalate`",
                   "submit it with `submit_report`", "**never** suppress or\nreplace an escalation"):
        assert phrase in body, phrase
    from agentteams.vulnscan import mcp
    for kind in mcp.ESCALATION_KINDS:                              # every kind the tool accepts is explained
        assert f"kind `{kind}`" in body, kind
    core = body[body.index("BEGIN invariant_core"):body.index("END invariant_core")]
    assert "Operator notes never suppress" in core                 # inside the fence: --update cannot drop it


def test_opt_in_only() -> None:
    assert "vuln-remediation" not in build_manifest(dict(_BRIEF))["selected_archetypes"]
    chatty = dict(_BRIEF, description="vulnerability remediation, CVE patching, dependency security scans")
    assert "vuln-remediation" not in build_manifest(chatty)["selected_archetypes"]
    on = build_manifest(dict(_BRIEF, capabilities=["vulnerability_remediation"], selected_archetypes=["primary-producer"]))
    assert "vuln-remediation" in on["selected_archetypes"]


def test_rule_s_vm_is_inside_the_invariant_fence_and_cannot_weaken_halt() -> None:
    text = SECURITY.read_text(encoding="utf-8")
    begin = text.index("<!-- AGENTTEAMS:BEGIN security_rules_invariant")
    end = text.index("<!-- AGENTTEAMS:END security_rules_invariant")
    pos = text.index("**Rule S-VM: Continuous Vulnerability Monitoring")
    assert begin < pos < end
    assert int(re.search(r"security_rules_invariant v=(\d+)", text).group(1)) >= 12
    rule = text[pos:end]
    assert "never** overrides an unretracted HALT" in rule
    assert "never** covers merging" in rule and "never** sent to a person" in rule
    assert not re.search(r"\*\*Rule S-11:", text), "S-11+ belong to project rules (consumer teams use them)"
    assert "Rule S-VM's standing-grant executor" in text           # explicit S-2 carve-out
    assert "valid, unexhausted S-VM standing grant" in text and "compromise indicator on a host (Rule S-VM) | **HALT**" in text


def test_goose_team_gets_the_sub_recipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    brief = json.loads((ROOT / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
    brief["capabilities"] = sorted(set(brief.get("capabilities", [])) | {"vulnerability_remediation"})
    bp = tmp_path / "brief.json"
    bp.write_text(json.dumps(brief), encoding="utf-8")
    out = tmp_path / "team"
    args = ["--description", str(bp), "--framework", "goose", "--output", str(out), "--yes", "--no-scan",
            "--security-offline"]
    assert build_team.main(args) == 0
    recipes = next(out.rglob("orchestrator.yaml")).parent
    assert (recipes / "vuln-remediation.yaml").is_file()
    orch = (recipes / "orchestrator.yaml").read_text(encoding="utf-8")
    assert 'name: "vuln_remediation"' in orch and 'path: "./vuln-remediation.yaml"' in orch
    assert "parameters:" in orch and "key: task" in orch.replace('"', "")      # `--params task=` is accepted
