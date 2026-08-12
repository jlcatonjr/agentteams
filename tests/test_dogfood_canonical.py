"""Dogfood integration test (durable-canonical-agent-format plan, H.6).

This repo's own .github/agents/ team goes to the durable canonical format and
back out through EVERY registered framework. Two guarantees are pinned:

1. Every framework imports the canonical CAI with zero errors and re-lands
   every agent file.
2. Zero drift: the canonical round trip (materialize -> load -> import)
   produces BYTE-IDENTICAL output trees to importing the original CAI
   directly, for every framework. The canonical format is a lossless
   intermediate; any divergence here is a regression in canonical.py or the
   dispatch seam in interop.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.frameworks.registry import FRAMEWORK_IDS
from agentteams.interop import export_to_cai, import_from_cai

REPO = Path(__file__).resolve().parents[1]
_SOURCE = REPO / ".github" / "agents"

pytestmark = pytest.mark.skipif(
    not _SOURCE.is_dir(), reason="repo copilot-vscode source team not found"
)

_FRAMEWORK_AGENTS_REL = {
    "copilot-vscode": Path(".github") / "agents",
    "copilot-cli": Path(".github") / "copilot",
    "claude": Path(".claude") / "agents",
    "goose": Path(".goose") / "recipes",
    "agents-md": Path(".agents"),
    "codex": Path(".agents"),
}


def _tree_bytes(root: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every file under *root*."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _agent_ext(framework: str) -> str:
    if framework == "copilot-vscode":
        return ".agent.md"
    if framework == "goose":
        return ".yaml"
    return ".md"


@pytest.fixture(scope="module")
def dogfood_cai() -> dict:
    return export_to_cai(_SOURCE, "copilot-vscode")


@pytest.mark.parametrize("framework", FRAMEWORK_IDS)
def test_dogfood_canonical_imports_every_framework(tmp_path, dogfood_cai, framework):
    """Leg 1: the canonical CAI imports into every framework without errors."""
    canon = tmp_path / "canon"
    materialize_canonical(dogfood_cai, canon)
    cai = load_canonical(canon)

    root = tmp_path / "out"
    target_dir = root / _FRAMEWORK_AGENTS_REL[framework]
    result = import_from_cai(cai, framework, target_dir)
    assert result.errors == []
    ext = _agent_ext(framework)
    for slug in (a["slug"] for a in cai["agents"]):
        assert (target_dir / f"{slug}{ext}").is_file(), f"{framework}: {slug} missing"


@pytest.mark.parametrize("framework", FRAMEWORK_IDS)
def test_dogfood_canonical_round_trip_is_byte_identical_to_direct(
    tmp_path, dogfood_cai, framework
):
    """Leg 2: materialize -> load -> import renders exactly what the direct
    import renders — the canonical format introduces zero drift."""
    rel = _FRAMEWORK_AGENTS_REL[framework]

    direct_root = tmp_path / "direct"
    r_direct = import_from_cai(dogfood_cai, framework, direct_root / rel)
    assert r_direct.errors == []

    canon = tmp_path / "canon"
    materialize_canonical(dogfood_cai, canon)
    canon_root = tmp_path / "via-canonical"
    r_canon = import_from_cai(load_canonical(canon), framework, canon_root / rel)
    assert r_canon.errors == []

    direct_tree = _tree_bytes(direct_root)
    canon_tree = _tree_bytes(canon_root)
    assert set(direct_tree) == set(canon_tree), (
        f"{framework}: file sets diverge — "
        f"direct-only {sorted(set(direct_tree) - set(canon_tree))}, "
        f"canonical-only {sorted(set(canon_tree) - set(direct_tree))}"
    )
    for rel_path, digest in direct_tree.items():
        assert canon_tree[rel_path] == digest, f"{framework}: {rel_path} bytes diverge"


def test_dogfood_canonical_preserves_agent_count(tmp_path, dogfood_cai):
    """Sanity: the real team's full roster survives the canonical round trip."""
    canon = tmp_path / "canon"
    materialize_canonical(dogfood_cai, canon)
    cai = load_canonical(canon)
    assert len(cai["agents"]) == len(dogfood_cai["agents"]) > 20
    handoffs_in = sum(len(a["handoffs"]) for a in dogfood_cai["agents"])
    handoffs_out = sum(len(a["handoffs"]) for a in cai["agents"])
    assert handoffs_out == handoffs_in > 0
