"""Live end-to-end Goose orchestrator *delegation* validation (credential-gated).

This is the last Phase-1 sign-off for the Goose integration: prove that an
agentteams-generated Goose **orchestrator** recipe actually delegates to its
``sub_recipes`` (isolated child sessions) against a real provider, not just that
the recipe validates. See ``references/plans/goose-live-delegation-validation-2026-06-22.plan.md``.

It is **skip-by-default**: it runs *only* where a real provider key is present, so
CI / this repo (no ``OPENROUTER_API_KEY``) stay offline-green. Two independent
``skipif`` gates apply:

* ``goose`` must be on PATH (``shutil.which``) — the subprocess can't run otherwise.
* ``OPENROUTER_API_KEY`` must be **resolvable** — env, or a ``key=value`` env-file
  referenced by ``GOOSE_OPENROUTER_ENV_FILE`` (the way ``goose-backend.sh`` and
  ``scripts/goose-openrouter-preflight.py`` resolve it, by reference). This
  missing-key skip is MANDATORY — never ``xfail`` and never unconditional: a
  missing key makes goose exit 1 at config-resolution (before any LLM call), which
  is a *setup* condition, not a delegation failure.

Exit-code classes (from the openrouter-preflight work, folded into the plan):

* **missing key  → goose exits 1** at config-resolution (handled by the skip above).
* **provider error past config-resolution (model-not-found / 401 / max-turns) →
  goose exits 0.** So success is judged by OUTPUT classification, never the exit
  code (mirrors ``scripts/goose-openrouter-preflight.classify_goose_output``).

Safety: the resolved key is passed *only* into the goose subprocess env. It is
never logged, asserted on, or serialized into a captured-output message.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import conftest

REPO_ROOT = Path(__file__).parent.parent
_BUILD_TEAM = REPO_ROOT / "build_team.py"
_ORCH_REL = Path(".goose") / "recipes" / "orchestrator.yaml"

# Bound the billed, non-deterministic LLM call (plan risk mitigation).
_MAX_TURNS = 4
_PROBE_TIMEOUT_S = 180.0

# The W6 probe prompt (carried in the generated orchestrator recipe) asks the
# orchestrator to name the correct workflow + first agent for "produce a
# deliverable for this team". For a generated team that is Workflow 1 and the
# `@primary-producer` agent, whose sub_recipe tool-name is `primary_producer`.
_EXPECTED_AGENT_HINTS = ("primary-producer", "primary_producer")
_EXPECTED_WORKFLOW_HINTS = ("workflow 1", "produce a deliverable")


# --- credential resolution (by reference; never logged/serialized) -----------
# Shared with test_goose_run_resilient.py via tests/conftest.py (code-hygiene
# CH-08, 2026-07-24: this was duplicated near-identically across both files).


# --- output classification (exit code is NOT a signal; goose exits 0 on error) -

def _classify(text: str) -> str:
    """Classify goose stdout+stderr into a verdict.

    Verdicts: ``model`` (model-not-found / 400) and ``setup`` (401 / unauthorized)
    are environment faults, not wiring faults; ``inconclusive`` is a transient
    (max-turns / reasoning miss) to re-run, not a hard fail; ``delegated`` means
    the orchestrator named the correct workflow + first agent (routing observed);
    ``early-stop`` is no sentinel and no known error.
    """
    low = text.lower()
    if "not a valid model" in low or "bad request (400)" in low or ("400" in low and "model" in low):
        return "model"
    if "401" in low or "unauthorized" in low or "invalid api key" in low or "no api key" in low:
        return "setup"
    if "maximum number of" in low or "max turns" in low or "max-turns" in low:
        return "inconclusive"
    # Executed delegation first (2026-08-09): goose's transcript marks a real spawned child
    # with a subagent id and a structured delegate tool line. Only execution produces these;
    # narration cannot. The name-based check remains as the weaker fallback verdict so the
    # assertion message can distinguish "routed but did not execute" from a genuine early stop.
    if "subagent" in low or ("delegate" in low and "▸" in text):
        return "delegated-live"
    names_agent = any(h in low for h in _EXPECTED_AGENT_HINTS)
    names_workflow = any(h in low for h in _EXPECTED_WORKFLOW_HINTS)
    if names_agent and names_workflow:
        return "delegated"
    return "early-stop"


# --- minimal direct-build team (orchestrator.yaml carries sub_recipes) --------

_MINIMAL_BRIEF = {
    "project_name": "DelegationProbe",
    "project_goal": (
        "A tiny team to validate that the generated Goose orchestrator delegates "
        "to its sub_recipes."
    ),
    "deliverables": ["one Python module"],
    "output_format": "Python 3.11 module",
    "primary_output_dir": "src/",
    "build_output_dir": "dist/",
    "components": [
        {
            "slug": "writer-module",
            "name": "Writer Module",
            "number": 1,
            "output_file": "src/writer.py",
            "description": "Writes a single small Python module.",
            "sections": ["Implementation", "Tests"],
            "quality_criteria": ["Has a docstring"],
        }
    ],
}


def _generate_goose_team(project: Path) -> Path:
    """Direct-build a minimal goose team into ``project``; return orchestrator.yaml.

    A **direct build** (not the bridge) is required: ``render_agent_file`` emits a
    ``sub_recipes:`` block ONLY for the orchestrator agent, so the orchestrator
    recipe is the one that exercises native delegation.
    """
    import json

    brief = project / "brief.json"
    brief.write_text(json.dumps(_MINIMAL_BRIEF), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable, str(_BUILD_TEAM),
            "--description", str(brief),
            "--framework", "goose",
            "--project", str(project),
            "--yes",
        ],
        capture_output=True, text=True, timeout=300, check=False, cwd=str(REPO_ROOT),
    )
    orch = project / _ORCH_REL
    assert orch.is_file(), (
        f"build_team did not emit orchestrator.yaml (rc={proc.returncode})\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    # Premise check: the generated orchestrator must carry a sub_recipes block.
    recipe = orch.read_text(encoding="utf-8")
    assert "sub_recipes:" in recipe, "generated orchestrator.yaml has no sub_recipes block"
    assert any(h in recipe for h in _EXPECTED_AGENT_HINTS), (
        "generated orchestrator.yaml does not name primary-producer (probe target)"
    )
    # 2026-08-09 (remediation of the 08-08 finding): the shipped prompt only asks the model
    # to NAME the workflow and first agent, so the test could pass without any tool call ever
    # being attempted — it was structurally incapable of catching a tool-call leak. Rewrite
    # the scratch copy's prompt (never the template) to demand an actual delegation, the same
    # forced-delegation methodology the 08-08 Tier-5 harness test validated. Round-trip
    # through the YAML writer so arbitrary text stays structurally safe.
    import yaml
    doc = yaml.safe_load(recipe)
    doc["prompt"] = (
        "Do not describe or plan. Actually delegate now: invoke your delegate/sub-recipe "
        "tool to hand the first workflow's first task to the primary producer agent, then "
        "report what the sub-agent returned."
    )
    orch.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return orch


# --- the live test -----------------------------------------------------------

@conftest.skip_no_goose
@conftest.skip_no_openrouter_key
@conftest.skip_no_live_model_tests
def test_generated_orchestrator_delegates_live(tmp_path):
    """End-to-end: the generated orchestrator names the correct workflow + first
    agent (the W6 probe), demonstrating it routes/delegates to a named sub_recipe.

    Skip-by-default; runs only with a resolvable key. Classifies OUTPUT (not the
    exit code). Model/auth faults are environment problems (not wiring) and a
    max-turns/reasoning miss is inconclusive — neither is a hard delegation fail.
    """
    orchestrator = _generate_goose_team(tmp_path)

    key = conftest.resolve_openrouter_key()  # local only; never logged/asserted/serialized
    assert key, "skip gate should have prevented running without a key"
    env = dict(os.environ)
    env["OPENROUTER_API_KEY"] = key
    env["GOOSE_PROVIDER"] = "openrouter"
    env.setdefault("GOOSE_MODEL", os.environ.get("GOOSE_OPENROUTER_MODEL", "qwen/qwen3.6-35b-a3b"))
    # auto, NOT chat (2026-08-09): chat mode disables all tool use, so goose intercepts the
    # delegation and asks the model to narrate a plan — the measured false-pass mechanism.
    # A delegation test must run in the mode where delegation can actually happen.
    env["GOOSE_MODE"] = "auto"

    cmd = [
        "goose", "run",
        "--recipe", str(orchestrator),
        "--no-session",
        "--max-turns", str(_MAX_TURNS),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_PROBE_TIMEOUT_S, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"goose delegation probe timed out after {_PROBE_TIMEOUT_S:.0f}s (transient)")

    captured = (proc.stdout or "") + "\n" + (proc.stderr or "")
    verdict = _classify(captured)

    # Environment faults — not a wiring/delegation failure. Skip so a misconfigured
    # provider doesn't read as a code regression. (No raw output → never leak key.)
    if verdict == "model":
        pytest.skip("OpenRouter rejected the model id (set a tool-capable slug); not a wiring fault")
    if verdict == "setup":
        pytest.skip("OpenRouter auth error (401); provider not authorized — not a wiring fault")
    if verdict == "inconclusive":
        pytest.skip(f"hit --max-turns ({_MAX_TURNS}) before delegating; transient — raise N and re-run")

    assert verdict == "delegated-live", (
        "orchestrator did not EXECUTE a delegation (a spawned subagent in the transcript). "
        f"verdict={verdict!r} — 'delegated' alone means it only narrated the routing, which "
        "is the false-pass this test was rebuilt to catch.\n"
        "--- goose output (key never injected here) ---\n"
        f"{captured}"
    )
