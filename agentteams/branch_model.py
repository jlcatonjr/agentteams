"""branch_model.py — the project's branch model (trunk-based or integration-branch), from the brief.

The brief's optional ``git_workflow`` block picks how work reaches the default branch:

* ``"trunk"`` (the default, and what an absent block means): feature branches merge to the default branch
  through PRs. Nothing in this module changes a trunk team: every placeholder it owns is ``""``, so the
  rendered files are byte-identical to a team without the block.
* ``"integration"``: feature branches merge **locally** into an integration branch (``dev`` by default) behind
  a local quality gate, and the default branch advances only through a release PR (integration → default,
  merged with a merge commit), the single event that runs cloud CI and deploys. Hotfixes go to the default
  branch and are merged back into the integration branch at once. The team gets
  ``references/integration-branch.reference.md`` and a few ``\\n``-prefixed rows at the end of existing lines in
  the git-operations, pr-manager, merge-strategy and branch-lifecycle files (the AUTHORING-GUIDE §5 pattern
  that ``wasm_policy`` uses).

The motive is GitHub Actions cost: CI cost scales with the number of trigger events, not with how much code
each carries (``references/plans/ci-cost-branching-strategy.report.md`` in AgentTeamsModule).

:func:`integration_branch_from_project` lets the branch tools (``--branch-inventory``, ``--branch-post-merge``,
``--branch-cleanup``) find the integration branch without a flag, so it is never proposed for deletion after a
release merge makes it an ancestor of the default branch. Reading it can only *add* protection (fail safe).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: The ``git_workflow.branch_model`` values.
MODELS: tuple[str, ...] = ("trunk", "integration")
#: The ``git_workflow.release_cadence`` values.
CADENCES: tuple[str, ...] = ("milestone", "weekly", "on-demand")
DEFAULT_INTEGRATION_BRANCH = "dev"
DEFAULT_CADENCE = "milestone"

#: Placeholders this module owns. Each is ``""`` for a trunk team.
ROW_PLACEHOLDERS: tuple[str, ...] = (
    "BRANCH_MODEL_MERGE_ROWS",
    "BRANCH_MODEL_GITOPS_REFS",
    "BRANCH_MODEL_GITOPS_CONTRACT",
    "BRANCH_MODEL_PR_REFS",
    "BRANCH_MODEL_DISPOSITION_ROWS",
    "BRANCH_MODEL_LIFECYCLE_ROWS",
)
VALUE_PLACEHOLDERS: tuple[str, ...] = ("INTEGRATION_BRANCH", "RELEASE_CADENCE")

REFERENCE_PATH = "references/integration-branch.reference.md"
REFERENCE_TEMPLATE = "universal/integration-branch.reference.template.md"



def _valid_branch(name: object) -> bool:
    """Whether ``name`` is a branch name the brief may give: vulnscan's ``base_branch`` rule, plus no ``..``.

    One rule for both brief keys that feed :func:`agentteams.vulnscan.executor_guard.brief_base_ref`.
    """
    from agentteams.vulnscan.project_policy import BASE_BRANCH_PATTERN

    return isinstance(name, str) and ".." not in name and re.fullmatch(BASE_BRANCH_PATTERN, name) is not None


def resolve(description: dict[str, Any]) -> dict[str, str] | None:
    """Return the normalized ``git_workflow`` record when the brief selects the integration model.

    Args:
        description: The project description (brief).

    Returns:
        ``{"branch_model": "integration", "integration_branch": ..., "release_cadence": ...}``, or None for a
        trunk team (block absent, or ``branch_model`` ``"trunk"``).

    Raises:
        ValueError: The block is malformed: not an object, an unknown model or cadence, or an invalid branch
            name (including ``main``/``master``, which would make the integration branch the default branch).
    """
    block = description.get("git_workflow")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("git_workflow must be an object")
    model = block.get("branch_model", "trunk")
    if model not in MODELS:
        raise ValueError(f"git_workflow.branch_model must be one of {MODELS}, not {model!r}")
    branch = block.get("integration_branch", DEFAULT_INTEGRATION_BRANCH)
    if not _valid_branch(branch):
        raise ValueError(f"git_workflow.integration_branch is not a valid branch name: {branch!r}")
    if branch in ("main", "master"):
        raise ValueError("git_workflow.integration_branch must not be the default branch (main/master)")
    cadence = block.get("release_cadence", DEFAULT_CADENCE)
    if cadence not in CADENCES:
        raise ValueError(f"git_workflow.release_cadence must be one of {CADENCES}, not {cadence!r}")
    if model == "trunk":
        return None
    return {"branch_model": model, "integration_branch": branch, "release_cadence": cadence}


def plan_files(description: dict[str, Any], output_files: list[dict[str, Any]]) -> dict[str, str] | None:
    """Resolve the brief's block and, for an integration team, add the reference to the output plan.

    A trunk team (block absent or ``"trunk"``) changes nothing, so its manifest and render stay byte-identical.

    Args:
        description: The project description (brief).
        output_files: The output plan (mutated for an integration team).

    Returns:
        The record :func:`resolve` returns (for ``manifest["git_workflow"]``), or None.

    Raises:
        ValueError: The block is malformed.
    """
    record = resolve(description)
    if record:
        output_files.append({"path": REFERENCE_PATH, "template": REFERENCE_TEMPLATE, "type": "reference",
                             "component_slug": None})
    return record


def enabled(manifest: dict[str, Any]) -> bool:
    """Return whether the manifest carries an integration-model record.

    Args:
        manifest: The team manifest.

    Returns:
        True for an integration-model team.
    """
    record = manifest.get("git_workflow")
    return isinstance(record, dict) and record.get("branch_model") == "integration"


def default_placeholders() -> dict[str, str]:
    """Return every placeholder this module owns, set for a trunk team (all ``""``).

    Returns:
        Placeholder name → ``""``.
    """
    return {name: "" for name in ROW_PLACEHOLDERS + VALUE_PLACEHOLDERS}


def populate_placeholders(manifest: dict[str, Any]) -> None:
    """Fill this module's placeholders from the manifest's ``git_workflow`` record (``""`` for a trunk team).

    Called by the render pipeline before the templates are rendered.

    Args:
        manifest: The team manifest (mutated: ``auto_resolved_placeholders``).
    """
    placeholders = manifest.setdefault("auto_resolved_placeholders", {})
    if not enabled(manifest):
        placeholders.update(default_placeholders())
        return
    record = manifest["git_workflow"]
    placeholders.update(rows(record["integration_branch"]))
    placeholders["INTEGRATION_BRANCH"] = record["integration_branch"]
    placeholders["RELEASE_CADENCE"] = record["release_cadence"]


def rows(branch: str) -> dict[str, str]:
    """Return the ``\\n``-prefixed rows an integration-model team gets, keyed by placeholder.

    Args:
        branch: The integration branch name.

    Returns:
        Placeholder name → text appended at the end of an existing table row or list item.
    """
    b = f"`{branch}`"
    ref = f"`{REFERENCE_PATH}`"
    return {
        "BRANCH_MODEL_MERGE_ROWS": (
            f"\n| Merge into {b} (local, `--no-ff`) | Integration model: every feature branch | Low "
            f"(no cloud CI runs) | The local gate in {ref} passed |"
            f"\n| Release PR ({b} → default branch) | Integration model: shipping a batch | Medium (large batch) "
            "| CI green on the release PR; **merge commit only**, never squash or rebase |"
            f"\n| Contributor PR into {b} | Integration model: a pull request based on {b} | Low "
            "| `agentteams --merge-pr <N>` when the signed integration-branch merge authorization verifies and its "
            "conditions hold (checks green, `@security` PASS for the head, merge commit); otherwise the gate's prompt |"
        ),
        "BRANCH_MODEL_GITOPS_REFS": (
            f"\n- {ref} *(this project's **integration-branch model**: feature branches merge locally into {b}; "
            "the default branch advances only by a release PR or a hotfix. It sets how rule 7 and the Post-Merge "
            "Branch Step apply)*"
        ),
        "BRANCH_MODEL_GITOPS_CONTRACT": (
            f"\n- Local gate — for each merge into {b}: the gate command and its result (`pass` / `fail`), or "
            "`N/A — not an integration merge`. A push to "
            f"{b} should trigger no CI: report rule 7 as `N/A — integration branch` only after the REST "
            "`actions/runs?head_sha=<sha>` query shows no run for the pushed commit; any run that did trigger is "
            "verified as usual"
        ),
        "BRANCH_MODEL_PR_REFS": (
            f"\n- {ref} *(integration-branch model: per-feature work merges locally into {b}; PRs go to the "
            "default branch only as a release PR or a hotfix)*"
        ),
        "BRANCH_MODEL_DISPOSITION_ROWS": (
            f"\n| Merge locally into {b} | `@git-operations` runs the local gate, merges `--no-ff` into {b}, and "
            f"may push {b} | No cloud CI; the work ships with the next release. **The usual choice under the "
            "integration model.** |"
            f"\n| Open the release PR ({b} → default branch) | `@git-operations` pushes {b}; you open the PR "
            "with the first-parent merge list since the last release; `@pr-notifier` notifies | One cloud-CI "
            "run. Merged with a merge commit, it deploys. |"
        ),
        "BRANCH_MODEL_LIFECYCLE_ROWS": (
            f"\n| I1 | Integration | the integration branch ({b}; checked right after rule 1) | Keep. Never "
            "delete: after a release merge it is an ancestor of the default branch, and without this rule it "
            "would classify as Merged. |"
            f"\n| I2 | Integrated | not rule 6 or 7, and the tip is an ancestor of {b} (local or on the push "
            "remote; checked before rule 8) | Keep until a release carries it to the default branch, where rule "
            "6 then applies. |"
        ),
    }


def integration_branch_from_project(root: Path) -> str | None:
    """Return the integration branch the project's brief names, or None (trunk, no brief, or unreadable).

    Finds the brief as fleet discovery does (``.agentteams/brief.json``, the root ``brief.json``, then the
    generated ``_build-description.json``). A brief that is a symlink or resolves outside ``root`` is ignored,
    and so is one that cannot be read or parsed (None). A readable brief whose ``git_workflow`` block is
    *malformed* raises: treating it as trunk would silently drop the integration branch's protection and
    retarget vulnerability-fix PRs.

    Args:
        root: The repository root.

    Returns:
        The integration branch name, or None.

    Raises:
        ValueError: The brief's ``git_workflow`` block is malformed (see :func:`resolve`).
    """
    from agentteams.fleet import _resolve_descriptor

    root = Path(root)
    try:
        path = _resolve_descriptor(root)
        if path is None or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return None
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(brief, dict):
        return None
    try:
        record = resolve(brief)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return record["integration_branch"] if record else None


__all__ = [
    "CADENCES",
    "DEFAULT_CADENCE",
    "DEFAULT_INTEGRATION_BRANCH",
    "MODELS",
    "REFERENCE_PATH",
    "REFERENCE_TEMPLATE",
    "ROW_PLACEHOLDERS",
    "VALUE_PLACEHOLDERS",
    "default_placeholders",
    "enabled",
    "integration_branch_from_project",
    "plan_files",
    "populate_placeholders",
    "resolve",
    "rows",
]
