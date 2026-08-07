"""instantiate.py — generate every framework's agent tree fresh, from the canonical source.

**What this is for.** The judgment layer measured one agent on one framework. Sweeping the whole
infrastructure needs the whole infrastructure to exist somewhere safe to attack, freshly built
from what the module actually produces today.

**Why generation is parallel and not chained.** The operator's request was to make Goose primary
and derive Copilot and Claude *from it*. The goal is right; the chaining is not, and the
adapter says why in its own docstring:

    "Goose caps delegation at ONE layer — a sub-recipe/subagent cannot delegate further."
    — agentteams/frameworks/goose.py

The pipeline produces ``orchestrator -> specialist -> cross-cutting`` handoffs. Goose renders
the first layer as ``sub_recipes`` and flattens the rest into ``summon load(...)`` **text
references**. Rebuilding Claude from that cannot recover the graph, so the sweep would measure a
degraded reconstruction and blame the agent infrastructure. ``bridge_sources.py`` confirms the
ceiling: for a goose source it extracts ``display_name``, ``invokable`` and ``role`` — metadata,
not agent bodies.

The chain is also unnecessary. **The brief and the template library are canonical**, and all
five frameworks already render from them. So all trees are generated in parallel from that one
source, and Goose is the primary *target* rather than the source. That keeps the Goose-first
intent, loses nothing, and adds what the chain could not: the same agent measured across three
adapters, where an adapter-specific defect shows up as a *difference*.

That is not hypothetical. ``audit.py`` gated eight checks on a hardcoded ``.agent.md``, so every
``.md`` framework skipped them silently — invisible until two frameworks were compared on
identical input.

**Three properties this module refuses to compromise on:**

* **Isolation.** Generation never touches the live trees. It refuses a destination inside the
  source root, the same rule :func:`~agentteams.redteam.realcopy.snapshot_agent_infrastructure`
  enforces, and for the same reason: regenerating the thing you are about to attack, in place,
  on the operator's machine, is not a test setup.
* **Zero files is an error.** A framework that emits nothing sweeps clean, and "0 failures over
  0 agents" renders as success. That is F-4 with the denominator at zero.
* **Provenance is recorded.** A finding against "the claude tree" cannot be reproduced without
  knowing which brief and which commit built it.

Generation costs nothing — no model calls — so it runs on every sweep. A cached tree stops
matching the brief the moment a template changes, and nobody would notice.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.bridge_sources import _INSTRUCTIONS_NAMES
from agentteams.frameworks.registry import FRAMEWORKS

#: Files that live in an agents directory and are NOT agents. Reused from
#: ``bridge_sources``, which already had to answer this exact question when reading a source
#: tree — re-deriving it here would be the hand-rolling F-3 catches, and the first sweep run
#: proved the cost: it measured ``SETUP-REQUIRED`` as an agent, spending real money asking a
#: setup document to defend against prompt injection.
_NON_AGENT_FILES: frozenset[str] = frozenset(
    {"SETUP-REQUIRED.md", "README.md", "AGENTS.md"} | set(_INSTRUCTIONS_NAMES)
)

#: Frameworks swept by default. Goose leads because the operator selected it as the primary
#: target; the order is also the order results are reported in.
DEFAULT_FRAMEWORKS: tuple[str, ...] = ("goose", "claude", "copilot-vscode")


@dataclass
class FrameworkTree:
    """One generated framework tree and what it contains."""

    framework: str
    root: Path
    agents_dir: Path
    agent_files: list[Path] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.agent_files)


@dataclass
class Instantiation:
    """A full generation pass, with the provenance needed to reproduce it."""

    dest_root: Path
    brief: Path
    brief_digest: str
    git_head: str
    trees: dict[str, FrameworkTree] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.trees) and all(t.ok for t in self.trees.values())

    def problems(self) -> list[str]:
        """Return one message per framework that did not produce a usable tree."""
        issues = [f"{t.framework}: {t.error}" for t in self.trees.values() if t.error]
        issues += [
            f"{t.framework}: generated ZERO agent files in {t.agents_dir} — a sweep over an "
            f"empty tree reports no failures, which is indistinguishable from success"
            for t in self.trees.values() if not t.error and not t.agent_files
        ]
        return issues

    def to_dict(self) -> dict:
        return {
            "brief": str(self.brief),
            "brief_digest": self.brief_digest,
            "git_head": self.git_head,
            "frameworks": {
                name: {
                    "agents_dir": str(tree.agents_dir),
                    "agent_count": len(tree.agent_files),
                    "error": tree.error,
                }
                for name, tree in sorted(self.trees.items())
            },
        }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    return completed.stdout.strip() or "unknown"


def agent_files_for(framework: str, root: Path) -> list[Path]:
    """Return the agent files a framework emitted under ``root``.

    Both the directory and the extension come from the framework registry rather than from a
    table in this module. A hardcoded ``.agent.md`` is precisely how ``audit.py`` came to skip
    eight checks on four frameworks, and re-deriving what the module already knows is the
    hand-rolling failure F-3 exists to catch.
    """
    adapter = FRAMEWORKS[framework]()
    agents_dir = adapter.get_agents_dir(root)
    extension = adapter.get_file_extension("agent")
    if not agents_dir.is_dir():
        return []
    # NON-RECURSIVE, and that is the whole point. Agent files sit directly in the agents
    # directory; `references/` beneath it holds shared reference documents that are not agents.
    # A recursive walk counted 55 "agents" for claude against 29 for goose and copilot-vscode
    # — 29 agents plus 26 references — which would have inflated every claude denominator and
    # made the sweep look like it covered twice what it did. Caught by the counts disagreeing
    # across frameworks, which is the comparison this design exists to make possible.
    return sorted(
        p for p in agents_dir.glob(f"*{extension}")
        if p.is_file() and p.name not in _NON_AGENT_FILES
    )


def instantiate_all(
    *,
    source_root: Path,
    brief: Path,
    dest_root: Path,
    frameworks: tuple[str, ...] = DEFAULT_FRAMEWORKS,
    timeout: int = 900,
) -> Instantiation:
    """Generate each framework's agent tree from the canonical brief, in isolation.

    Args:
        source_root: The repository holding the pipeline and templates.
        brief: The canonical project description. **This is the source** — not any framework's
            output.
        dest_root: Where the trees are written. One subdirectory per framework.
        frameworks: Framework ids to generate.
        timeout: Per-framework seconds.

    Returns:
        An :class:`Instantiation` carrying the trees and the provenance.

    Raises:
        ValueError: If ``dest_root`` is inside ``source_root``. Generating into the tree under
            test mutates the thing about to be attacked, and would trip the deterministic
            audit's own live-tree guard.
        FileNotFoundError: If the brief does not exist.
    """
    source_root = source_root.resolve()
    dest_root = dest_root.resolve()
    if dest_root == source_root or source_root in dest_root.parents:
        raise ValueError(
            f"refusing to generate into {dest_root}, which is inside {source_root}. The sweep "
            f"attacks these trees; building them in place would mutate the live infrastructure "
            f"immediately before red-teaming it."
        )
    if not brief.exists():
        raise FileNotFoundError(f"canonical brief not found: {brief}")

    dest_root.mkdir(parents=True, exist_ok=True)
    result = Instantiation(
        dest_root=dest_root,
        brief=brief,
        brief_digest=_digest(brief),
        git_head=_git_head(source_root),
    )

    for framework in frameworks:
        root = dest_root / framework
        root.mkdir(parents=True, exist_ok=True)
        adapter = FRAMEWORKS[framework]()
        agents_dir = adapter.get_agents_dir(root)
        tree = FrameworkTree(framework=framework, root=root, agents_dir=agents_dir)

        # Through the module's own entry point, never a hand-rolled render. Calling
        # emit.emit_all directly would skip whatever the CLI does around it — the exact shape
        # of the capability-key sweep that reached one of two call sites and reported success.
        completed = subprocess.run(
            [
                sys.executable, str(source_root / "build_team.py"),
                "--description", str(brief),
                "--project", str(root),
                "--framework", framework,
                "--output", str(agents_dir),
                "--yes", "--no-scan",
            ],
            cwd=source_root, text=True, capture_output=True,
            check=False, timeout=timeout,
        )
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()
            tree.error = f"generation exited {completed.returncode}: {tail[-1] if tail else ''}"
        tree.agent_files = agent_files_for(framework, root)
        result.trees[framework] = tree

    (dest_root / "instantiation.json").write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


__all__ = [
    "DEFAULT_FRAMEWORKS",
    "FrameworkTree",
    "Instantiation",
    "agent_files_for",
    "instantiate_all",
]
