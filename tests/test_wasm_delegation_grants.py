"""Grant attenuation for WASM delegation (WASM P09 §4.1-4.2, D11, METAPLAN R12).

A sub-agent's effective grant is ``child_declared ∩ parent_effective``, never wider: ``/project`` mounts survive only
inside a parent mount (resolved-path containment, symlinks dropped), ``/scratch`` only when the parent has one and
then always the child's own dir, limits element-wise min, the toolset re-narrowed. A property test checks
``child ⊆ parent`` over random grant pairs; a signature test checks no parameter can widen. Also
``reachable_scratch_dirs`` (Stage 0's write roots).
"""

from __future__ import annotations

import ast
import inspect
import os
import random
import sys
import types
from pathlib import Path

import pytest

from agentteams import wasm_exec as W
from agentteams.wasm_agent import delegation as D
from tests._wasm_delegation_helpers import LIMITS, Team, agent, grant, project, scratch

PKG = Path(D.__file__).parent


def _guests(g: object) -> list[tuple[str, str, str]]:
    return [(m.guest, m.host, m.mode) for m in g.mounts]


# --- intersection rules --------------------------------------------------------------------------------------------

def test_project_mount_inside_a_parent_mount_is_kept(tmp_path: Path) -> None:
    root = project(tmp_path)
    parent = grant(root, "docs")
    child = W.Grant((W.Mount(str(root / "docs" / "sub"), "/project/docs/sub", "ro"),
                     W.Mount(str(root / "docs"), "/project/docs", "ro")), LIMITS)
    att = D.attenuate(parent, child, child_scratch_host=None)
    assert [g for g, _, _ in _guests(att.grant)] == ["/project/docs/sub", "/project/docs"] and not att.dropped


def test_project_mount_outside_and_sibling_prefix_are_dropped(tmp_path: Path) -> None:
    root = project(tmp_path)
    child = W.Grant((W.Mount(str(root / "secret"), "/project/secret", "ro"),
                     W.Mount(str(root / "docsx"), "/project/docsx", "ro")), LIMITS)   # /docs vs /docsx
    att = D.attenuate(grant(root, "docs"), child, child_scratch_host=None)
    assert att.grant.mounts == () and len(att.dropped) == 2
    assert all("outside every delegator mount" in d for d in att.dropped)


def test_symlinked_child_path_is_dropped(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "docs" / "link").symlink_to(root / "docs" / "sub")
    child = W.Grant((W.Mount(str(root / "docs" / "link"), "/project/docs/link", "ro"),), LIMITS)
    att = D.attenuate(grant(root, "docs"), child, child_scratch_host=None)
    assert att.grant.mounts == () and "symlink" in att.dropped[0]


def test_scratch_only_if_the_parent_has_one_and_always_the_childs_own(tmp_path: Path) -> None:
    root = project(tmp_path)
    child = grant(root, scratch_of="primary-producer")
    own = scratch(root, "primary-producer")
    none = D.attenuate(grant(root, "docs"), child, child_scratch_host=own)
    assert none.grant.mounts == () and "no /scratch" in none.dropped[0]
    att = D.attenuate(grant(root, scratch_of="orchestrator"), child, child_scratch_host=own)
    assert _guests(att.grant) == [("/scratch", own, "rw")]
    with pytest.raises(ValueError, match="its own"):
        D.attenuate(grant(root, scratch_of="orchestrator"), child, child_scratch_host=scratch(root, "orchestrator"))
    assert D.attenuate(grant(root, scratch_of="orchestrator"), child, child_scratch_host=None).grant.mounts == ()


def test_limits_are_the_elementwise_minimum(tmp_path: Path) -> None:
    root = project(tmp_path)
    parent = grant(root, limits=W.Limits(fuel=2_000_000_000, max_memory_bytes=128 << 20, timeout_s=20))
    child = grant(root, limits=W.Limits(fuel=5_000_000_000, max_memory_bytes=64 << 20, timeout_s=60))
    att = D.attenuate(parent, child, child_scratch_host=None)
    assert att.grant.limits == W.Limits(2_000_000_000, 64 << 20, 20)
    assert sorted(d.split(":")[0] for d in att.dropped) == ["limits.fuel", "limits.timeout_s"]


def test_unknown_mount_kind_is_dropped(tmp_path: Path) -> None:
    root = project(tmp_path)
    # W.Mount itself refuses this shape; a hand-built object still meets attenuate's own check (defence in depth)
    child = types.SimpleNamespace(mounts=(types.SimpleNamespace(host=str(root / "docs"), guest="/project/docs",
                                                                mode="rw"),), limits=LIMITS)
    att = D.attenuate(grant(root, "docs", scratch_of="orchestrator"), child, child_scratch_host=None)
    assert att.grant.mounts == () and "unsupported mount kind" in att.dropped[0]


def test_toolset_is_narrowed_to_the_effective_grant_and_depth(tmp_path: Path) -> None:
    root = project(tmp_path)
    parent = agent("orchestrator", W.COMPUTE_ONLY, delegates=("primary-producer",))
    child = agent("primary-producer", grant(root, "docs", scratch_of="primary-producer"),
                  tools=frozenset({"read_file", "write_file", "run_code", "delegate"}), delegates=("reviewer",))
    spec, dropped = D.effective_child_spec(parent, child, project_root=root, can_delegate=False)
    assert spec.tools == frozenset({"run_code"}) and spec.delegates == frozenset() and spec.grant.mounts == ()
    assert {"tool delegate: not servable under the effective grant or depth",
            "tool read_file: not servable under the effective grant or depth"} <= set(dropped)
    deeper, _ = D.effective_child_spec(parent, child, project_root=root, can_delegate=True)
    assert "delegate" in deeper.tools and deeper.delegates == frozenset({"reviewer"})


@pytest.mark.skipif(sys.platform != "darwin", reason="case-insensitive APFS only")
def test_case_variant_path_is_dropped_not_widened_on_darwin(tmp_path: Path) -> None:
    """R-3: ``resolve()`` keeps case, so a case variant of a parent dir fails containment: narrowed, never widened."""
    root = project(tmp_path)
    variant = str(root / "DOCS")
    if not os.path.isdir(variant):
        pytest.skip("case-sensitive volume")
    child = W.Grant((W.Mount(variant, "/project/DOCS", "ro"),), LIMITS)
    assert D.attenuate(grant(root, "docs"), child, child_scratch_host=None).grant.mounts == ()


# --- the subset property and the no-widening surface ---------------------------------------------------------------

def _random_grant(rng: random.Random, root: Path, rels: list[str], slug: str) -> W.Grant:
    picked = rng.sample(rels, rng.randint(0, len(rels)))
    mounts = [W.Mount(str(root / r), "/project/" + r, "ro") for r in picked]
    if rng.random() < 0.5:
        mounts.append(W.Mount(scratch(root, slug), "/scratch", "rw"))
    lo, hi = W.LIMIT_BOUNDS["fuel"], W.LIMIT_BOUNDS["max_memory_bytes"]
    return W.Grant(tuple(mounts), W.Limits(rng.randint(lo[0], lo[1]), rng.randint(hi[0], hi[1]),
                                           rng.randint(1, W.LIMIT_BOUNDS["timeout_s"][1])))


def test_property_child_effective_is_within_parent_effective(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "docs" / "sub" / "deep").mkdir()
    (root / "docs" / "ln").symlink_to(root / "secret")
    rels = ["docs", "docs/sub", "docs/sub/deep", "docsx", "secret", "docs/ln"]
    rng = random.Random(9)
    for _ in range(400):
        parent = _random_grant(rng, root, rels, "orchestrator")
        child = _random_grant(rng, root, rels, "primary-producer")
        own = scratch(root, "primary-producer")
        eff = D.child_grant(parent, child, child_scratch_host=own)
        assert D.grant_subset(eff, parent)
        parent_real = [Path(m.host).resolve() for m in parent.mounts]
        for m in eff.mounts:                               # independent re-check of the order
            if m.guest == "/scratch":
                assert m.host == own and any(p.guest == "/scratch" for p in parent.mounts)
            else:
                real = Path(m.host).resolve()
                assert str(real) == m.host and any(real == p or p in real.parents for p in parent_real)
        for name in ("fuel", "max_memory_bytes", "timeout_s"):
            assert getattr(eff.limits, name) <= getattr(parent.limits, name)


def test_grant_subset_refuses_wider() -> None:
    parent = W.Grant((), LIMITS)
    assert not D.grant_subset(W.Grant((W.Mount("/tmp", "/project/tmp", "ro"),), LIMITS), parent)
    assert not D.grant_subset(W.Grant((W.Mount("/tmp", "/scratch", "rw"),), LIMITS), parent)
    assert not D.grant_subset(W.Grant((), W.Limits(LIMITS.fuel + 1, LIMITS.max_memory_bytes, 30)), parent)


def test_no_parameter_can_widen_a_child_grant() -> None:
    assert list(inspect.signature(D.child_grant).parameters) == ["parent", "child", "child_scratch_host"]
    assert list(inspect.signature(D.attenuate).parameters) == ["parent", "child", "child_scratch_host"]
    assert list(inspect.signature(D.effective_child_spec).parameters) == ["parent", "child", "project_root",
                                                                          "can_delegate"]
    for fn in (D.child_grant, D.attenuate, D.effective_child_spec):
        assert not any(p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL) for p in inspect.signature(fn).parameters.values())


def test_no_amplification_surface_exists() -> None:
    """D11: no identifier, schema property or brief key spells an amplification path."""
    for path in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            name = getattr(node, "name", None) or getattr(node, "id", None) or getattr(node, "arg", None) or \
                getattr(node, "attr", None)
            assert not (isinstance(name, str) and "amplif" in name.lower()), f"{path.name}: {name}"
    schema = (PKG.parent / "schemas" / "project-description.schema.json").read_text()
    assert '"amplify"' not in schema and '"amplification"' not in schema


# --- reachable scratch dirs (Stage 0's write roots) ----------------------------------------------------------------

def _team(root: Path) -> Team:
    return Team(agent("orchestrator", grant(root, "docs", scratch_of="orchestrator"), delegates=("a", "b")),
                agent("a", grant(root, scratch_of="a"), delegates=("c",)),
                agent("b", grant(root, "docs")),
                agent("c", grant(root, scratch_of="c"), delegates=("d",)),
                agent("d", grant(root, scratch_of="d")),
                agent("unreached", grant(root, scratch_of="unreached")))


def test_reachable_scratch_dirs_lists_only_reachable_scratch_agents_within_depth(tmp_path: Path) -> None:
    root = project(tmp_path)
    team = _team(root)
    g = team.specs["orchestrator"].grant
    assert D.reachable_scratch_dirs(team.agent_spec, "orchestrator", g, project_root=root, max_depth=1) == (
        scratch(root, "a"),)
    assert D.reachable_scratch_dirs(team.agent_spec, "orchestrator", g, project_root=root, max_depth=2) == (
        scratch(root, "a"), scratch(root, "c"))
    assert D.reachable_scratch_dirs(team.agent_spec, "orchestrator", g, project_root=root, max_depth=9) == (
        scratch(root, "a"), scratch(root, "c"), scratch(root, "d"))


def test_reachable_scratch_dirs_is_empty_without_a_root_scratch(tmp_path: Path) -> None:
    root = project(tmp_path)
    team = _team(root)
    assert D.reachable_scratch_dirs(team.agent_spec, "orchestrator", grant(root, "docs"), project_root=root,
                                    max_depth=4) == ()
    assert team.asked == []              # nothing resolved: derived from the root grant first


def test_reachable_scratch_dirs_takes_no_op_content() -> None:
    """Derived from the team and the root grant only: the signature has no task, op or model input."""
    assert list(inspect.signature(D.reachable_scratch_dirs).parameters) == [
        "resolve_agent", "root_slug", "root_grant", "project_root", "max_depth"]
