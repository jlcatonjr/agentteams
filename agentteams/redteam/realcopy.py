"""realcopy.py — attack the *real* agent infrastructure, in an isolated copy of it.

**The instinct this module serves is right.** The handoff's §7 says it plainly: *"Rehearse
against a real target, not a synthetic fixture. Both F-1 and F-2 were invisible to synthetic
tests and obvious against a copy of a real downstream repo."* The standing battery builds tiny
synthetic trees (``write_decisions(tmp())``), so it measures each control against inputs its
author imagined. Pointing the probes at the actual ``.github/agents/`` and ``.claude/agents/``
content closes that gap.

**The mechanism that must not be used is `--update --merge` as the undo.** It was proposed as
the safety net — attack the live tree, then restore it by re-running the pipeline. The battery
has already measured what that merge does to each attack class, and the answer is that the
attacks most worth running are exactly the ones it cannot reverse:

===== ==================================================== ==========================
Probe What it does                                         What `--update --merge` does
===== ==================================================== ==========================
C3    escalates a capability grant in YAML front matter    **nothing** — front matter
                                                           cannot be fenced, so there is
                                                           no restore-on-update guarantee.
                                                           The probe's own name: *"An
                                                           escalated grant survives
                                                           `--update --merge`; nothing
                                                           reverts it"*
D3    deletes a fence's markers                            **refuses to write** — the
                                                           merge declines, so the
                                                           mutation stays
D4    renames a fence                                      keeps the weakened body AND
                                                           re-inserts the real one
D2    appends to a fenced body (shrink-preserve poisoning) pins the fence indefinitely
                                                           outside the template-
                                                           authoritative set
===== ==================================================== ==========================

A merge is not an undo. Its entire design purpose is to *preserve* on-disk divergence — that
is what protects operator enrichment — and an attack is on-disk divergence. Three further
reasons the live tree is the wrong target:

* ``.claude/agents/`` is what the agents running the audit read. Mutating it mid-session means
  the harness attacks its own control plane while standing on it.
* The daily job is unattended. A dropped, cancelled or crashed run — the exact scenario the
  reliability contract in ``docs-freshness-watch.yml`` is written around — would leave the
  repository mutated with nothing scheduled to clean up.
* ``git`` already provides a byte-exact restore that the merge cannot match, including front
  matter, deleted markers and renamed fences.

**So: real content, isolated location.** :func:`snapshot_agent_infrastructure` copies the
genuine agent infrastructure into a temp root; probes attack that; the temp root is discarded.
Fidelity is preserved and there is nothing to restore.

**And the merge becomes a measurement instead of a safety net**, which is where it is genuinely
useful: :func:`classify_restorability` runs ``--update --merge`` against the *copy* and reports,
per mutation, whether the pipeline RESTORED it, PRESERVED it, or REFUSED to write. That turns
"can the pipeline heal this?" from an assumption into a number.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: What gets copied. The agent files themselves plus everything the constitutional controls
#: read while judging them — the decision and waiver ledgers, the hooks, the instruction
#: authority reference. Copying the agents without the ledgers would measure the controls
#: against an empty clearance history, which is a different system.
SNAPSHOT_PATHS: tuple[str, ...] = (
    ".github/agents",
    ".claude/agents",
    ".claude/hooks",
    ".claude/settings.json",
    "references",
    "AGENTS.md",
)

#: Restorability outcomes, from :func:`classify_restorability`.
RESTORED = "RESTORED"      # the pipeline put the original content back
PRESERVED = "PRESERVED"    # the mutation survived the merge untouched
REFUSED = "REFUSED"        # the merge declined to write the file at all
ALTERED = "ALTERED"        # neither original nor mutation — a third state


@dataclass(frozen=True)
class RealCopy:
    """An isolated copy of the live agent infrastructure.

    Attributes:
        source_root: The repository the copy was taken from.
        root: The temp root holding the copy.
        copied: Repo-relative paths that were present and copied.
        absent: Repo-relative paths in :data:`SNAPSHOT_PATHS` that did not exist.
    """

    source_root: Path
    root: Path
    copied: tuple[str, ...]
    absent: tuple[str, ...]

    @property
    def agent_file_count(self) -> int:
        """Number of agent markdown files in the copy — the population any coverage claim
        about this copy must be stated over."""
        return len(list(self.root.rglob("*.agent.md"))) + len(
            [p for p in self.root.rglob("*.md") if p.parent.name == "agents"]
        )


def snapshot_agent_infrastructure(source_root: Path, dest_root: Path) -> RealCopy:
    """Copy the live agent infrastructure into an isolated root.

    Args:
        source_root: Repository holding the real infrastructure.
        dest_root: Destination root, created if absent. Callers own its lifetime and should
            place it under a temp directory.

    Returns:
        A :class:`RealCopy` describing what was taken.

    Raises:
        ValueError: If ``dest_root`` is inside ``source_root``. A "copy" written into the tree
            it copies is not isolated, and the next sweep would find the attacked duplicates
            as real agent files.
    """
    source_root = source_root.resolve()
    dest_root = dest_root.resolve()
    if dest_root == source_root or source_root in dest_root.parents:
        raise ValueError(
            f"refusing to snapshot {source_root} into {dest_root}, which is inside it; an "
            f"attacked copy living in the source tree is not an isolated copy"
        )
    dest_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    absent: list[str] = []
    for rel in SNAPSHOT_PATHS:
        src = source_root / rel
        if not src.exists():
            absent.append(rel)
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        copied.append(rel)
    return RealCopy(
        source_root=source_root,
        root=dest_root,
        copied=tuple(copied),
        absent=tuple(absent),
    )


def live_tree_fingerprint(root: Path) -> dict[str, str]:
    """Return ``path -> git status code`` for the agent infrastructure.

    Args:
        root: Repository root.

    Returns:
        A mapping of repo-relative path to its two-character porcelain status. Empty when git
        cannot answer; callers must consult :func:`live_tree_is_verifiable` rather than reading
        an empty mapping as "nothing changed".
    """
    if not live_tree_is_verifiable(root):
        return {}
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--", *SNAPSHOT_PATHS],
        cwd=root, text=True, capture_output=True, check=False,
    )
    fingerprint: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if len(line) > 3:
            fingerprint[line[3:].strip()] = line[:2]
    return fingerprint


def live_tree_modifications(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Return paths the red-team run itself changed.

    This is the post-condition the daily audit asserts: **the live agent infrastructure is
    byte-identical before and after a red-team run.** It is the mechanical form of "attack the
    copy, never the original", and unlike a promise in a docstring it fails loudly.

    A **delta**, not an absolute cleanliness check, and the distinction is load-bearing: an
    absolute check reports every uncommitted edit an operator already had in flight, which on
    a working branch is every run. A rule that fires on ordinary work is a rule that gets
    muted, and a muted safety property is worse than none because it reads as coverage.

    Args:
        before: Fingerprint taken before the run.
        after: Fingerprint taken after it.

    Returns:
        Repo-relative paths whose status appeared or changed during the run.
    """
    changed = [path for path, code in after.items() if before.get(path) != code]
    changed.extend(path for path in before if path not in after)
    return sorted(set(changed))


def live_tree_is_verifiable(root: Path) -> bool:
    """True when git can answer whether the live tree changed.

    Separated from :func:`live_tree_modifications` so "no git here" cannot be read as "nothing
    changed". Indeterminate is not a pass.
    """
    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _digest(path: Path) -> str | None:
    """Return a SHA-256 of ``path``, or ``None`` when it does not exist."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Mutation:
    """One attack applied to a file in the copy, and the digests that bracket it.

    Attributes:
        label: What the mutation represents, e.g. ``"front-matter capability widening"``.
        rel_path: Path within the copy.
        original: Digest before the mutation.
        mutated: Digest after the mutation.
        expectation: What the author believes the merge will do — recorded so a surprise is
            visible as a surprise rather than absorbed into the result.
    """

    label: str
    rel_path: str
    original: str | None
    mutated: str | None
    expectation: str = ""


@dataclass
class RestorabilityResult:
    """Per-mutation verdicts after running the merge against the copy."""

    verdicts: dict[str, str] = field(default_factory=dict)
    merge_exit_code: int = 0
    merge_output: str = ""

    @property
    def restored(self) -> int:
        """How many mutations the pipeline put back."""
        return sum(1 for v in self.verdicts.values() if v == RESTORED)


def apply_mutation(copy: RealCopy, label: str, rel_path: str, mutate) -> Mutation:
    """Apply one attack to a file inside the copy.

    Args:
        copy: The isolated copy.
        label: Human-readable name for the attack class.
        rel_path: Path within the copy to mutate.
        mutate: Callable taking the current text and returning the attacked text.

    Returns:
        The recorded :class:`Mutation`.

    Raises:
        FileNotFoundError: If ``rel_path`` is not in the copy. A mutation that silently
            targets nothing is an attack that always "succeeds" against a file that is not
            there — the vacuity failure, in probe form.
    """
    path = copy.root / rel_path
    if not path.exists():
        raise FileNotFoundError(
            f"cannot mutate {rel_path}: not present in the copy at {copy.root}"
        )
    original = _digest(path)
    path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
    return Mutation(
        label=label, rel_path=rel_path, original=original, mutated=_digest(path)
    )


def run_merge_against_copy(copy: RealCopy, *, timeout: int = 600) -> tuple[int, str]:
    """Run ``--update --merge`` against the copy and return ``(exit_code, output)``.

    The pipeline is invoked as a subprocess against the **copy**, never against
    ``source_root``. Isolated by construction: the descriptor, project and output paths all
    resolve inside :attr:`RealCopy.root`.

    Args:
        copy: The isolated copy.
        timeout: Seconds before the subprocess is killed.

    Returns:
        The pipeline's exit code and its combined output.

    Raises:
        FileNotFoundError: If the copy carries no resolvable descriptor.
    """
    # The canonical resolver, not a hand-rolled two-location lookup. Its docstring is
    # literally "Stub-trap fix: prefer the rich brief over the thin stub", and hand-rolling
    # this exact lookup rendered 6 repositories from a thin stub on 2026-08-06 (W22). The
    # tool very often already has the function you are about to write.
    from agentteams import fleet

    descriptor = fleet._resolve_descriptor(copy.root)
    if descriptor is None:
        raise FileNotFoundError(
            f"no descriptor resolves inside the copy at {copy.root}; there is nothing to "
            f"re-render, and running the merge anyway would measure the wrong thing"
        )
    completed = subprocess.run(
        [
            sys.executable, str(copy.source_root / "build_team.py"),
            "--description", str(descriptor),
            "--project", str(copy.root),
            "--output", str(copy.root / ".github" / "agents"),
            "--update", "--merge", "--yes",
        ],
        cwd=copy.source_root, text=True, capture_output=True,
        check=False, timeout=timeout,
    )
    return completed.returncode, completed.stdout + completed.stderr


def classify_restorability(
    copy: RealCopy, mutations: list[Mutation], merge_exit: int, merge_output: str
) -> RestorabilityResult:
    """Classify what the merge did to each mutation.

    This is the honest use of ``--update --merge`` in a red-team context: not as an undo, but
    as a **measurement** of which attack classes the pipeline can heal. A mutation classified
    :data:`PRESERVED` names an attack that would survive the proposed remediation; the C3
    probe is the worked example, and its residue is that an escalated capability grant
    survives the merge because front matter cannot be fenced.

    Args:
        copy: The isolated copy, post-merge.
        mutations: The mutations applied before the merge.
        merge_exit: Exit code from :func:`run_merge_against_copy`.
        merge_output: Its output, scanned for per-file refusals.

    Returns:
        A :class:`RestorabilityResult` keyed by mutation label.
    """
    result = RestorabilityResult(merge_exit_code=merge_exit, merge_output=merge_output)
    for mutation in mutations:
        current = _digest(copy.root / mutation.rel_path)
        if current == mutation.original:
            verdict = RESTORED
        elif current == mutation.mutated:
            verdict = REFUSED if mutation.rel_path in merge_output else PRESERVED
        else:
            verdict = ALTERED
        result.verdicts[mutation.label] = verdict
    return result


__all__ = [
    "ALTERED",
    "PRESERVED",
    "REFUSED",
    "RESTORED",
    "SNAPSHOT_PATHS",
    "Mutation",
    "RealCopy",
    "RestorabilityResult",
    "apply_mutation",
    "classify_restorability",
    "live_tree_fingerprint",
    "live_tree_is_verifiable",
    "live_tree_modifications",
    "run_merge_against_copy",
    "snapshot_agent_infrastructure",
]
