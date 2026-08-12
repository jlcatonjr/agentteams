"""Bidirectional canonical round-trip lossless-transformation tests.

Closes a gap the existing suite left open (found while answering an operator
question about round-trip test coverage, 2026-08-11): `test_interop.py` and
`test_dogfood_canonical.py` solidly prove the canonical *on-disk serialization*
is lossless (`materialize_canonical` -> `load_canonical` round trips the CAI
dict exactly, for all 6 frameworks), but nothing closes the loop all the way
back to native content with a real equality assertion, for any framework:

- `test_map16_framework_to_canonical_and_back`'s native-in leg only checks the
  re-imported file exists and one sentinel substring survives.
- `test_interop_round_trip_body_fidelity` compares one field (`body_markdown`)
  of one agent, only for copilot-vscode/claude/copilot-cli, and goes via an
  *intermediate* framework (A->B->A) rather than self-referentially.

This module adds, for all 6 registered frameworks, in both directions:

1. native -> canonical -> native (self-referential: export, materialize,
   load, import back into the SAME framework), compared field-by-field
   against the CAI derived from the *original* native input.
2. canonical -> native -> canonical (self-referential: materialize, load,
   import to native, export back), compared field-by-field against the
   *original* CAI.

Scope note: `agents-md` and `codex` carry no native front matter, so
`capabilities`/`handoffs` are documented elsewhere (docs_src/interoperability.md)
as "inferred-or-empty on export... best-effort by nature" for those two. This
module asserts that lossiness is *stable* under round-tripping (still
empty/inferred, not corrupted into something else), not that it magically
becomes lossless — see `_LOSSY_FIELDS`. `codex` has no renderer of its own; it
delegates to the `agents-md` adapter (`agentteams/frameworks/codex.py`), so its
cases are expected to behave identically to `agents-md`'s, confirming that
delegation holds under round-tripping too.

Not covered here (already covered elsewhere, see module docstrings of the
files named above): skills, MCP servers, invariant-core re-lift, the
canonical-serialization-only round trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.frameworks.agents_md import _strip_leading_synthesized_header
from agentteams.interop import export_to_cai, import_from_cai

REPO = Path(__file__).resolve().parents[1]
_REAL_NATIVE_SOURCE: dict[str, Path] = {
    "copilot-vscode": REPO / ".github" / "agents",
    "claude": REPO / ".claude" / "agents",
}

_ALL_FRAMEWORKS = (
    "copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex",
)

_AGENTS_REL: dict[str, Path] = {
    "copilot-vscode": Path(".github/agents"),
    "copilot-cli": Path(".github/copilot"),
    "claude": Path(".claude/agents"),
    "goose": Path(".goose/recipes"),
    "agents-md": Path(".agents"),
    "codex": Path(".agents"),
}

_AGENT_EXT: dict[str, str] = {"copilot-vscode": ".agent.md", "goose": ".yaml"}


def _agent_ext(framework: str) -> str:
    return _AGENT_EXT.get(framework, ".md")


# Fields excluded from the per-agent equality comparison for a given
# framework, because that framework has no native mechanism to carry them —
# documented elsewhere, not a defect. `source_path` and `raw_front_matter`
# are always excluded: they are parse/path artifacts of *where* a file was
# read from, not content, and legitimately differ across separate tmp dirs.
_ALWAYS_IGNORE = {"source_path", "raw_front_matter"}
# A.3: capabilities.raw is a snapshot of the original on-disk tool-string
# ordering, which changes after canonical re-rendering normalizes to
# canonical order. tool_scopes (the authoritative representation) still
# matches exactly. model_hint is stable and compared normally.
_CAPABILITIES_RAW_KEY = "raw"
_LOSSY_FIELDS: dict[str, set[str]] = {
    # copilot-cli: export_to_cai sets capabilities={} unconditionally
    # (interop.py:182) and has no front matter to carry description either.
    # handoffs: "manifest" delivery mode writes to references/runtime-
    # handoffs.json on import, but export_to_cai never reads that sidecar back
    # — a real, pre-existing, architectural one-way gap (not introduced by
    # this session's D1/D2 fixes, not fixed here either — logged as a
    # follow-on finding, same treatment as goose's capabilities coarseness).
    "copilot-cli": {"description", "capabilities", "handoffs"},
    "claude": {"handoffs"},
    "agents-md": {"description", "capabilities", "handoffs"},
    "codex": {"description", "capabilities", "handoffs"},
    # goose: documented coarse/best-effort tool-scope channel (capability_map.py's
    # own module docstring), not a wiring gap this session's D1 fix addresses.
    # Tool surface travels via the recipe extensions: list, coarser than the
    # 7-token vocabulary — 'developer' bundles read/edit/search/execute
    # indistinguishably and 'agent' has no extension at all, so
    # [read,edit,agent] forward-maps to ['developer'] and reverse-maps back to
    # [read,edit,search,execute]: a real, one-directional, already-documented
    # loss, not something D1's wiring fix could or should make exact. handoffs:
    # goose's native channel is sub_recipes, which has no slot for `label`/
    # `send` (only a delegation edge + prompt) — confirmed by direct testing
    # `to`/`prompt` survive, `label`/`send` come back synthesized/defaulted.
    # A separate, real, pre-existing coarseness — logged as a follow-on finding.
    "goose": {"capabilities", "handoffs"},
}


def _seed_cai() -> dict[str, Any]:
    """A hand-built, framework-neutral CAI document: 3 agents in a depth-2
    delegation chain (orchestrator→worker→worker2), non-trivial capabilities,
    and a goose recipe-extension bucket. Rich enough to exercise handoff +
    capability survival AND the depth-2 delegation shape that hid the goose
    duplication bug (A.1/A.7), not just a single sentinel token."""
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-11T00:00:00+00:00",
        "source_framework": "claude",
        "source_dir": "seed",
        "instructions_binding": {
            "source_name": "CLAUDE.md",
            "content": "# Project Instructions\n\nSEED_INSTRUCTIONS_TOKEN\n",
        },
        "agents": [
            {
                "slug": "orchestrator",
                "name": "Orchestrator",
                "description": "Routes work to the worker agent.",
                "body_markdown": (
                    "# Orchestrator\n\n"
                    "SEED_ORCH_BODY_TOKEN first paragraph.\n\n"
                    "## Responsibilities\n\n"
                    "- Route requests\n"
                    "- SEED_ORCH_BULLET_TOKEN\n"
                ),
                # tool_scopes only: capability_map.capabilities_from_tokens (the
                # only place any export path builds this dict) never emits
                # model_hint/raw on export for any framework today, so a seed
                # asserting those would fail the round trip for reasons unrelated
                # to tool_scopes forwarding (2026-08-11 adversarial finding).
                "capabilities": {"tool_scopes": ["read", "edit", "agent"]},
                "handoffs": [{
                    "to": "worker", "label": "Delegate to worker",
                    "prompt": "", "send": False,
                }],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "orchestrator.md",
            },
            {
                "slug": "worker",
                "name": "Worker",
                "description": "Executes delegated tasks and delegates to worker2.",
                "body_markdown": "# Worker\n\nSEED_WORKER_BODY_TOKEN doing the work.\n",
                "capabilities": {"tool_scopes": ["read", "execute"]},
                "handoffs": [{
                    "to": "worker2", "label": "Delegate to worker2",
                    "prompt": "", "send": False,
                }],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "worker.md",
            },
            # A.7: depth-2 delegation agent — a non-orchestrator agent with its
            # own outgoing handoffs, so the goose duplication bug class is
            # exercised for all 6 frameworks, not just the one-off goose test.
            {
                "slug": "worker2",
                "name": "Worker Two",
                "description": "Does specialized sub-tasks.",
                "body_markdown": "# Worker Two\n\nSEED_WORKER2_BODY_TOKEN.\n",
                "capabilities": {"tool_scopes": ["read"]},
                "handoffs": [],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "worker2.md",
            },
        ],
        "skills": [],
        "mcp_servers": [],
        "references": [],
        "framework_extensions": {"goose": {"recipe_extensions": ["developer"]}},
    }


def _native_source_for(framework: str, tmp_path: Path) -> Path:
    """Return a native source directory for *framework*.

    copilot-vscode/claude use this repo's own real, dogfooded source trees
    (read-only; never written to). The other four have no real source
    checked into this repo, so they are bootstrapped by rendering the seed
    CAI through the framework's own adapter — the fixture is "real" native
    content in the sense that it was produced by the same renderer under
    test, just not hand-authored.
    """
    if framework in _REAL_NATIVE_SOURCE:
        real = _REAL_NATIVE_SOURCE[framework]
        if not real.is_dir() or not any(real.glob("*.md")):
            pytest.skip(f"repo source team for {framework} not found at {real}")
        return real

    bootstrap_root = tmp_path / "bootstrap-native"
    target_dir = bootstrap_root / _AGENTS_REL[framework]
    result = import_from_cai(_seed_cai(), framework, target_dir, overwrite=True)
    assert result.errors == [], f"bootstrap import for {framework} failed: {result.errors}"
    return target_dir


def _assert_agents_equal(
    original: dict[str, Any], roundtripped: dict[str, Any], framework: str,
) -> None:
    ignore = _ALWAYS_IGNORE | _LOSSY_FIELDS.get(framework, set())
    orig_by_slug = {a["slug"]: a for a in original["agents"]}
    rt_by_slug = {a["slug"]: a for a in roundtripped["agents"]}
    roster = set(orig_by_slug)
    assert roster == set(rt_by_slug), (
        f"{framework}: agent roster diverged after round trip: "
        f"original={sorted(orig_by_slug)} round-tripped={sorted(rt_by_slug)}"
    )
    for slug, orig_agent in orig_by_slug.items():
        rt_agent = rt_by_slug[slug]
        for field, orig_value in orig_agent.items():
            if field == "handoffs":
                # copilot-vscode's render-time team-ref filter deliberately
                # drops handoffs targeting an agent outside the roster being
                # rendered (documented, existing behavior — not a round-trip
                # defect). Model that here rather than being surprised by it:
                # a handoff to a genuinely out-of-roster target is expected
                # to vanish; only in-roster handoffs are required to survive.
                orig_value = [h for h in orig_value if h.get("to") in roster]
            if field in ignore:
                continue
            rt_value = rt_agent.get(field)
            if field == "capabilities" and isinstance(orig_value, dict) and isinstance(rt_value, dict):
                # A.3: capabilities.raw is a snapshot of the original on-disk
                # tool-string ordering, which changes after canonical
                # re-rendering normalizes to canonical order. model_hint
                # may be enriched by the native format's front-matter
                # defaults (e.g. copilot-vscode's _ensure_yaml_front_matter
                # adds a default model: even when the seed had none). Both
                # are best-effort escape hatches — compare only tool_scopes
                # (the authoritative representation) for strict equality.
                orig_ts = orig_value.get("tool_scopes")
                rt_ts = rt_value.get("tool_scopes")
                assert rt_ts == orig_ts, (
                    f"{framework}/{slug}: capabilities.tool_scopes diverged\n"
                    f"  original:      {orig_ts!r}\n"
                    f"  round-tripped: {rt_ts!r}"
                )
                continue
            if field == "body_markdown" and framework == "goose":
                # A.7: goose's synthesized "## Delegation & references (Goose)"
                # block uses the handoff label, which gets normalized to the
                # bare slug on round trip (documented coarseness, same as
                # goose's handoffs label/send in _LOSSY_FIELDS). Strip the
                # block before comparing to avoid a false failure on the
                # depth-2 delegation fixture.
                import re as _re
                _deleg_re = _re.compile(
                    r"\n*## Delegation & references \(Goose\).*?(?=\n#{1,3}\s|\Z)",
                    _re.DOTALL,
                )
                orig_stripped = _deleg_re.sub("", orig_value).strip()
                rt_stripped = _deleg_re.sub("", rt_value or "").strip()
                assert rt_stripped == orig_stripped, (
                    f"{framework}/{slug}: body_markdown (excl. delegation block) diverged\n"
                    f"  original:      {orig_stripped!r}\n"
                    f"  round-tripped: {rt_stripped!r}"
                )
                continue
            if field == "body_markdown" and framework in ("agents-md", "codex"):
                # No front matter to carry `description` separately, so
                # agents_md.py::render_agent_file grafts it onto the body as a
                # leading paragraph — a real, predictable, one-way transform,
                # not noise to skip (unlike capabilities/handoffs/description
                # themselves, which really do just vanish for these two).
                # Degrades to plain equality when description is empty (the
                # native-first direction, where these frameworks' own export
                # never populates description in the first place).
                orig_desc = str(orig_agent.get("description") or "").strip()
                own_heading_stripped = _strip_leading_synthesized_header(orig_value, "")
                name = str(orig_agent.get("name", "")).strip()
                header = f"# {name}\n\n{orig_desc}\n\n" if orig_desc else f"# {name}\n\n"
                expected = (header + own_heading_stripped + "\n") if orig_desc else orig_value
                assert rt_value == expected, (
                    f"{framework}/{slug}: body_markdown did not match the expected "
                    f"description-graft transform\n"
                    f"  expected:      {expected!r}\n"
                    f"  round-tripped: {rt_value!r}"
                )
                continue
            assert rt_value == orig_value, (
                f"{framework}/{slug}: field {field!r} diverged after round trip\n"
                f"  original:      {orig_value!r}\n"
                f"  round-tripped: {rt_value!r}"
            )


# ---------------------------------------------------------------------------
# Direction 1: native -> canonical -> native, self-referential
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("framework", _ALL_FRAMEWORKS)
def test_native_to_canonical_to_native_is_lossless(tmp_path: Path, framework: str):
    """export_to_cai -> materialize_canonical -> load_canonical ->
    import_from_cai back into the SAME framework, then export_to_cai again —
    compared field-by-field to the CAI derived from the true original native
    input (not to a hypothetical seed)."""
    source_dir = _native_source_for(framework, tmp_path)
    original_cai = export_to_cai(source_dir, framework)
    assert original_cai["agents"], f"{framework}: source yielded zero agents"

    canon_dir = tmp_path / "canon"
    materialize_canonical(original_cai, canon_dir)
    loaded_cai = load_canonical(canon_dir)

    reimported_dir = tmp_path / "reimported" / _AGENTS_REL[framework]
    result = import_from_cai(loaded_cai, framework, reimported_dir, overwrite=True)
    assert result.errors == [], f"{framework}: re-import errors: {result.errors}"

    final_cai = export_to_cai(reimported_dir, framework)
    _assert_agents_equal(original_cai, final_cai, framework)


# ---------------------------------------------------------------------------
# Direction 2: canonical -> native -> canonical, self-referential
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("framework", _ALL_FRAMEWORKS)
def test_canonical_to_native_to_canonical_is_lossless(tmp_path: Path, framework: str):
    """materialize_canonical -> load_canonical -> import_from_cai to native ->
    export_to_cai back — compared field-by-field to the original seed CAI."""
    seed = _seed_cai()

    canon_dir = tmp_path / "canon"
    materialize_canonical(seed, canon_dir)
    loaded_cai = load_canonical(canon_dir)

    native_dir = tmp_path / "native" / _AGENTS_REL[framework]
    result = import_from_cai(loaded_cai, framework, native_dir, overwrite=True)
    assert result.errors == [], f"{framework}: import errors: {result.errors}"

    final_cai = export_to_cai(native_dir, framework)
    _assert_agents_equal(seed, final_cai, framework)


# ---------------------------------------------------------------------------
# A.1 regression: goose delegation-block size stability across repeated cycles
# ---------------------------------------------------------------------------

def test_goose_delegation_block_does_not_compound_across_cycles(tmp_path: Path):
    """A.1 regression guard: the `## Delegation & references (Goose)` block
    appended for non-orchestrator agents with handoffs must not compound on
    every native→canonical→native cycle. Before the fix, the block was never
    stripped before re-appending, growing the rendered body by ~340 bytes
    per cycle (616→955→1294 over 3 cycles). This test asserts the rendered
    body for the depth-2 delegation agent (`worker`, which has an outgoing
    handoff to `worker2`) is byte-identical across 3 consecutive cycles."""

    seed = _seed_cai()
    # We need the `worker` agent — it has an outgoing handoff to `worker2`,
    # which is the trigger for the delegation block synthesis.
    agent_dir = tmp_path / "native" / _AGENTS_REL["goose"]

    # Cycle 0: render seed → native
    result = import_from_cai(seed, "goose", agent_dir, overwrite=True)
    assert result.errors == [], f"cycle 0 import failed: {result.errors}"

    # Capture the rendered body of `worker` after each cycle
    worker_file = agent_dir / "worker.yaml"
    assert worker_file.exists(), f"worker.yaml not rendered at {worker_file}"

    bodies: list[str] = []
    for cycle in range(3):
        # Export native → CAI
        cai = export_to_cai(agent_dir, "goose")
        # Materialize to canonical
        canon_dir = tmp_path / f"canon-cycle-{cycle}"
        materialize_canonical(cai, canon_dir)
        loaded = load_canonical(canon_dir)
        # Re-import to native
        result = import_from_cai(loaded, "goose", agent_dir, overwrite=True)
        assert result.errors == [], f"cycle {cycle + 1} import failed: {result.errors}"

        # Capture the worker agent's body_markdown from the exported CAI
        final_cai = export_to_cai(agent_dir, "goose")
        worker = next(a for a in final_cai["agents"] if a["slug"] == "worker")
        bodies.append(worker["body_markdown"])

    # All three bodies must be identical — no compounding
    assert bodies[0] == bodies[1] == bodies[2], (
        "A.1 regression: goose delegation block compounded across cycles.\n"
        f"  cycle 1 len={len(bodies[0])}, cycle 2 len={len(bodies[1])}, "
        f"cycle 3 len={len(bodies[2])}\n"
        f"  cycle 1 == cycle 2: {bodies[0] == bodies[1]}\n"
        f"  cycle 2 == cycle 3: {bodies[1] == bodies[2]}"
    )


# ---------------------------------------------------------------------------
# Goose-specific: framework_extensions bucket survival
# ---------------------------------------------------------------------------

def test_goose_framework_extensions_survive_canonical_to_native_to_canonical(tmp_path: Path):
    """The goose-specific recipe_extensions bucket lives outside the `agents`
    list (top-level `framework_extensions.goose`), so it needs its own check —
    _assert_agents_equal only compares per-agent fields."""
    seed = _seed_cai()

    canon_dir = tmp_path / "canon"
    materialize_canonical(seed, canon_dir)
    loaded_cai = load_canonical(canon_dir)

    native_dir = tmp_path / "native" / _AGENTS_REL["goose"]
    result = import_from_cai(loaded_cai, "goose", native_dir, overwrite=True)
    assert result.errors == []

    final_cai = export_to_cai(native_dir, "goose")
    final_bucket = (final_cai.get("framework_extensions") or {}).get("goose") or {}
    assert "developer" in (final_bucket.get("recipe_extensions") or []), (
        f"goose recipe_extensions did not survive the round trip: {final_bucket}"
    )
