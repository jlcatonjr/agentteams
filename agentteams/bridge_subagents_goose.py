"""Opt-in subagent-stub-recipe emitter for the ``… → goose`` bridge.

Parity with :mod:`agentteams.bridge_subagents` (the claude path). When the host
feature ``bridge:<source>-to-goose:subagents`` is selected, this emits one thin
**stub recipe** per canonical source agent into ``.goose/recipes/<slug>.yaml`` —
each a valid Goose recipe (via :func:`agentteams.frameworks.goose._emit_recipe`,
``developer`` builtin) whose instructions point at the canonical source agent and
add no policy of their own. The pointer bridge stays the default; this is opt-in.

Safety:
- Reserved/bridge-owned slugs (``orchestrator``, ``team-builder``,
  ``bridge-orchestrator``) are skipped so a stub never shadows a direct-build or
  bridge-owned recipe.
- A stub is written only when ``<slug>.yaml`` does **not** already exist — an
  existing recipe (a real ``--convert`` recipe or a hand-authored one) is never
  overwritten.
- Stubs are pointers (content is just "read the source"), so they need no refresh.

This is the lightweight alternative to ``--convert-from … --framework goose``
(full per-agent recipes).
"""
from __future__ import annotations

from pathlib import Path

from agentteams.bridge_subagents import (
    StubEmissionResult,
    _parse_front_matter,
    _short_description,
    _slug_from_source,
)
from agentteams.frameworks.goose import _emit_recipe

# Slugs that name a direct-build or bridge-owned recipe — never emit a stub for these.
_RESERVED_SLUGS = frozenset({"orchestrator", "team-builder", "bridge-orchestrator"})

# Source files that are instructions/setup, not agents.
_NON_AGENT_NAMES = frozenset({"CLAUDE.md", "copilot-instructions.md", "SETUP-REQUIRED.md"})


def _collect_source_agents(source_dir: Path, source_framework: str) -> list[Path]:
    """Return canonical source agent files, by the source framework's convention.

    copilot-vscode uses ``*.agent.md``; claude / copilot-cli use ``*.md``.
    """
    if not source_dir.is_dir():
        return []
    suffix = ".agent.md" if source_framework == "copilot-vscode" else ".md"
    return sorted(
        p for p in source_dir.iterdir()
        if p.is_file() and p.name.endswith(suffix) and p.name not in _NON_AGENT_NAMES
    )


def _stub_instructions(*, source_rel_path: str, source_abs_path: Path, role_desc: str) -> str:
    """Render the recipe instructions that delegate to the canonical source agent."""
    return (
        "This is a bridged Goose stub recipe. The canonical agent definition lives in "
        "the source framework at:\n\n"
        f"    {source_rel_path}\n\n"
        "On activation, read that source file with your developer (file) tool, then "
        "perform the work it describes. Honor every constraint and protocol stated in "
        "the canonical body; this stub adds no policy of its own.\n\n"
        f"- Source absolute path: {source_abs_path}\n"
        f"- Source role: {role_desc}\n"
    )


def emit_goose_subagent_stubs(
    *,
    source_dir: Path,
    output_root: Path,
    source_framework: str = "copilot-vscode",
    dry_run: bool = False,
) -> StubEmissionResult:
    """Emit stub recipes into ``<output_root>/.goose/recipes/``; one per source agent.

    Skips reserved/bridge-owned slugs and any ``<slug>.yaml`` that already exists.
    """
    result = StubEmissionResult()
    source_dir = source_dir.resolve()
    target_dir = (output_root / ".goose" / "recipes").resolve()
    sources = _collect_source_agents(source_dir, source_framework)
    if not sources:
        return result

    for src in sources:
        slug = _slug_from_source(src.name)
        if slug in _RESERVED_SLUGS:
            result.skipped.append(str(target_dir / f"{slug}.yaml"))
            continue
        out_path = target_dir / f"{slug}.yaml"
        if out_path.exists():
            result.skipped.append(str(out_path))
            continue

        text = src.read_text(encoding="utf-8")
        meta, _ = _parse_front_matter(text)
        display_name = meta.get("name") or _slug_to_title(slug)
        description = _short_description(meta, slug)
        try:
            source_rel = src.relative_to(output_root).as_posix()
        except ValueError:
            source_rel = src.as_posix()

        recipe = _emit_recipe(
            title=f"{display_name} (bridged stub)",
            description=description,
            instructions=_stub_instructions(
                source_rel_path=source_rel, source_abs_path=src, role_desc=description,
            ),
            extensions=["developer"],
        )
        if not dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(recipe, encoding="utf-8")
        result.written.append(str(out_path))

    return result


def _slug_to_title(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-"))


__all__ = ["emit_goose_subagent_stubs"]
