"""doctor.py — ``--doctor``: one report on whether a project's providers agree (baseAgent P3–P11).

``--check`` asks whether one team matches its templates. ``--doctor`` asks the cross-surface question: do all of
the project's providers see the same rules, the same memory, and the same agents? It reads only, and reports:

* **Rules projection** (FAIL): a copy of the Project-Specific Rules or a notes pointer that is out of date, or was
  refused (hand-edited, a duplicate region) — :mod:`agentteams.rules_projection`.
* **Memory index** (WARN): surfaces whose copies were built from different sources (``source_fingerprint``) or that
  are stale — :mod:`agentteams.memory_index_surfaces`.
* **Copilot body limit** (FAIL): operator notes past 30,000 characters — :mod:`agentteams.copilot_body_limit`.
* **Agents** (INFO): agents missing on some surface, notes that differ by surface, skills on some surfaces only —
  :mod:`agentteams.surface_matrix`.
* **Shared AGENTS.md** (WARN): an ``AGENTS.md`` whose constitutional core is older than the other entry files' (it
  was frozen with Codex while the others moved on, P11).
* **Hygiene rule ids** (WARN): a ``### CH-…`` heading repeated in a ``code-hygiene-rules.reference.md`` (file and
  both lines), or a project rule using a framework-range id — :mod:`agentteams.hygiene_rules` (IM item 26).
* **Backup stubs** (WARN, warn-first): a backed-up agent file under any runtime dir's ``.agentteams-backups/`` that
  a runtime would still load as a live agent (IM item 01) — :func:`agentteams.backup.runtime_loadable_backups`.
* **Signing governance** (WARN): a team dir whose ``signing-governed.marker`` is absent on disk and in HEAD and was
  deleted in a commit, naming that commit and whether the team still governs. Governance ends once the deletion has
  reached the upstream and default branch (IM item 17) —
  :func:`agentteams.signing_switch.marker_deletion_commits`.
* **Agent runtime** (INFO/WARN; an invalid brief block is a FAIL): the configured model and effort per framework
  and role, every value a framework does not write, a review-critical agent run below the default effort, and the
  Claude agent files on disk with no ``effort:`` (agentteams #508) — :mod:`agentteams.agent_runtime`.

Exit code 1 when any FAIL is reported.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_CORE_RE = re.compile(r"<!-- AGENTTEAMS:BEGIN constitutional_core v=(\d+) -->")
_ENTRY_FILES = (".github/copilot-instructions.md", "CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.override.md")


def _brief(root: Path) -> dict:
    from agentteams.memory_index_surfaces import resolve_brief

    found = resolve_brief(root)
    if found is None:
        return {}
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _rules(root: Path, brief: dict) -> tuple[list[str], int]:
    from agentteams import rules_projection

    configured = brief.get("project_rules_source") or None
    frozen = brief.get("frozen_surfaces") or None
    report = rules_projection.project(root, configured=configured, frozen=frozen, write=False)
    if report.source is None:
        return ["  ℹ  No instruction file holds Project-Specific Rules; nothing to project."], 0
    pointers = rules_projection.sync_pointers(root, configured=configured, frozen=frozen, write=False)
    lines = [f"  source: {report.source}"]
    fails = 0
    for o in report.outcomes + pointers:
        if o.status in ("updated", "added", "refused"):
            fails += 1
            state = {"updated": "out of date", "added": "missing"}.get(o.status, "refused")
            lines.append(f"  ✗  FAIL {o.path}: {state}" + (f" ({o.detail})" if o.detail else ""))
        elif o.status == "skipped":
            lines.append(f"  ℹ  {o.path}: skipped ({o.detail})")
        else:
            lines.append(f"  ✓  {o.path}: current")
    if fails:
        lines.append("     Run `agentteams --sync-project-rules` (a refused copy needs its hand edit moved to the source).")
    return lines, fails


def _indexes(root: Path, brief: dict) -> list[str]:
    from agentteams.memory_index_surfaces import index_surfaces

    surfaces = index_surfaces(root, brief.get("frozen_surfaces") or None)  # frozen copies are never rebuilt
    if not surfaces:
        return ["  ℹ  No memory index on any surface."]
    seen: dict[str, list[str]] = {}
    lines: list[str] = []
    for agents_dir in surfaces:
        rel = agents_dir.relative_to(root).as_posix()
        try:
            head = json.loads((agents_dir / "references" / "memory-index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            lines.append(f"  !  WARN {rel}: index unreadable ({exc})")
            continue
        fingerprint = str(head.get("source_fingerprint", "?"))
        seen.setdefault(fingerprint, []).append(rel)
        lines.append(f"  ·  {rel}: built {head.get('built_at', '?')} from {head.get('source_count', '?')} source(s)")
    if len(seen) > 1:
        lines.append(f"  !  WARN the {len(surfaces)} copies were built from {len(seen)} different source sets; run "
                     "`agentteams --refresh-index` to rebuild them together.")
    return lines


def _body_limit(root: Path) -> tuple[list[str], int]:
    from agentteams.copilot_body_limit import body_limit_findings

    folder = root / ".github" / "agents"
    if not folder.is_dir():
        return [], 0
    failures, advisories = body_limit_findings(folder)
    return ([f"  ✗  FAIL {f}" for f in failures] + [f"  ℹ  {a}" for a in advisories]), len(failures)


def _agents(root: Path) -> list[str]:
    from agentteams.surface_matrix import MATRIX_REL, build_matrix

    matrix = build_matrix(root)
    if not matrix.surfaces:
        return ["  ℹ  No agent files found."]
    gaps = matrix.gaps()
    lines = [f"  ·  {len(matrix.agents)} agents on {len(matrix.surfaces)} surfaces ({', '.join(matrix.surfaces)}); "
             f"{len(gaps)} missing on at least one surface (see {MATRIX_REL})."]
    lines += [f"  ℹ  {slug}: missing on {', '.join(missing)}" for slug, missing in gaps.items()]
    differ = matrix.notes_differ()
    if differ:
        lines.append(f"  ℹ  Notes differ by surface for: {', '.join(differ)}")
    return lines


def _shared_agents_md(root: Path) -> list[str]:
    agents_md = root / "AGENTS.md"
    if not agents_md.is_file():
        return []
    m = _CORE_RE.search(agents_md.read_text(encoding="utf-8", errors="replace"))
    others = [int(x.group(1)) for rel in _ENTRY_FILES if (root / rel).is_file()
              for x in [_CORE_RE.search((root / rel).read_text(encoding="utf-8", errors="replace"))] if x]
    if m and others and int(m.group(1)) < max(others):
        return [f"  !  WARN AGENTS.md carries constitutional_core v={m.group(1)}, the other entry files v={max(others)}. "
                "It was likely frozen (a Codex freeze holds the shared AGENTS.md) while they moved on; Goose and "
                "other AGENTS.md readers see the older core until it is updated."]
    return []


def _backup_stubs(root: Path) -> tuple[list[str], int]:
    """WARN per backed-up agent file a runtime would still load (IM item 01): it shadows a live agent.

    Warn-first (the item's rollback note): a WARN, not a FAIL, for this release, so teams that passed before do
    not start failing; the next --update/--bridge-merge with a backup renames the file.
    """
    from agentteams.backup import runtime_loadable_backups

    stubs = runtime_loadable_backups(root)
    lines = [f"  !  WARN {p.relative_to(root)} loads as a live agent; run --update --merge or --bridge-merge (it is "
             "renamed inert, never deleted) or rename it to *.agentteams-bak" for p in stubs]
    return lines, 0


def _hygiene_rule_ids(root: Path) -> tuple[list[str], int]:
    """WARN on a repeated ``### CH-…`` heading, or a framework-range id among the project's own rules (IM item 26)."""
    from agentteams.hygiene_rules import HYGIENE_REF_NAME, duplicate_rule_ids, framework_ids_outside_fence
    from agentteams.prompt_roots import TEAM_AGENT_GLOBS

    lines: list[str] = []
    for team in TEAM_AGENT_GLOBS:
        ref = root / team / "references" / HYGIENE_REF_NAME
        if not ref.is_file() or not ref.resolve().is_relative_to(root.resolve()):
            continue  # @security: a symlinked team dir is not followed out of the project
        text = ref.read_text(encoding="utf-8", errors="replace")
        rel = ref.relative_to(root)
        lines += [f"  !  WARN {rel}: {rid} appears twice (lines {a} and {b}); rule ids must be unique"
                  for rid, a, b in duplicate_rule_ids(text)]
        lines += [f"  !  WARN {rel}:{n}: project rule uses the framework id {rid}; renumber it CH-P01, CH-P02, … "
                  "(framework ids CH-01 onward can collide with a later release)" for rid, n in framework_ids_outside_fence(text)]
    return lines, 0


def _signing_governance(root: Path) -> tuple[list[str], int]:
    """WARN per team dir whose signing-governed.marker was deleted in a commit (IM item 17 addition).

    Governance ends once a deletion has reached the governing refs (``signing_switch.is_governed``). The operator may
    mean that, so this is a WARN that names the commit, not a FAIL, and it repeats until the marker is restored. Its
    wording follows the real state: still governed (the deletion is only on this branch), governed through a sibling
    team dir, or not governed. A team dir that no longer exists gets one INFO line instead.
    """
    from agentteams.signing_switch import GOVERNED_MARKER, TEAM_DIRS, is_governed, marker_deletion_commits

    lines: list[str] = []
    for team, commit in marker_deletion_commits(root).items():
        here = root / team
        if not here.is_dir():
            lines.append(f"  ℹ  {team} was removed; its {GOVERNED_MARKER} was deleted in {commit}")
            continue
        others = [t for t in TEAM_DIRS if t != team and (root / t).is_dir() and is_governed(root / t)]
        if is_governed(here):  # deleted on this branch only: the upstream or origin/HEAD still carries it
            effect = "it still governs until that deletion reaches the upstream and default branch"
        elif others:
            effect = f"this team dir is no longer signing-governed ({', '.join(others)} still is)"
        else:
            effect = "no team dir in this project is signing-governed any more"
        lines.append(f"  !  WARN {team}/{GOVERNED_MARKER} is absent and was deleted in {commit}; {effect}. Restore "
                     "it (git checkout <commit>^ -- <path>) unless that was intended")
    return lines, 0


def _runtime_frameworks(root: Path) -> list[tuple[str, Path]]:
    """The frameworks generated here, each with its agents dir: the build log names it where a dir is shared."""
    from agentteams.frameworks.registry import FRAMEWORK_IDS, agents_dir_for

    found: dict[Path, list[str]] = {}
    for fw in FRAMEWORK_IDS:
        d = agents_dir_for(fw, root)
        if d.is_dir() and d.resolve().is_relative_to(root):
            found.setdefault(d, []).append(fw)
    out: list[tuple[str, Path]] = []
    for d, fws in found.items():
        if len(fws) > 1:  # copilot-vscode and copilot-cli share .github/agents: the build log says which
            try:
                logged = json.loads((d / "references" / "build-log.json").read_text(encoding="utf-8")).get("framework")
            except (OSError, ValueError):
                logged = None
            fws = [logged] if logged in fws else fws
        out += [(fw, d) for fw in fws]
    return out


def _runtime_table(runtime: object, frameworks: list[str]) -> list[str]:
    """The configured model and effort per framework and role (``agent_runtime``; presentation only)."""
    from agentteams import agent_runtime as ar
    from agentteams.frameworks._claude_model import default_claude_model

    lines = []
    for fw in frameworks:
        for role, probe in (("orchestrator", "orchestrator"), ("subagents", "navigator")):
            model = ar.model_for(runtime, probe, fw) if fw in ar.MODEL_WRITTEN else None
            if model is None:
                model = default_claude_model(probe) if fw == "claude" else (
                    "(the agent file's own)" if fw in ar.MODEL_WRITTEN else "-")
            try:
                effort = ar.effort_for(runtime, probe, fw) or "-"
            except ar.AgentRuntimeError as exc:
                effort = f"refused ({exc})"
            lines.append(f"  ℹ  configured {fw:<15} {role:<12} model {model:<26} effort {effort}")
    return lines


def _agent_runtime(root: Path, brief: dict) -> tuple[list[str], int]:
    """Model and effort per role and provider (agentteams #508): configured values, what is not written, what is on disk."""
    from agentteams import agent_runtime as ar
    from agentteams.yaml_frontmatter import parse_yaml_front_matter

    try:
        runtime = ar.parse(brief)
    except ar.AgentRuntimeError as exc:
        return [f"  ✗  FAIL the brief's agent_runtime is invalid: {exc}"], 1
    surfaces = _runtime_frameworks(root)
    if not surfaces:
        return [], 0
    lines = _runtime_table(runtime, [fw for fw, _ in surfaces])
    for fw, d in surfaces:
        slugs = sorted(f.name.split(".")[0] for f in d.iterdir() if f.is_file() and not f.name.startswith("_"))
        lines += [f"  {'!' if 'WARNING' in line else 'ℹ'}  {line}" for line in ar.report_lines(runtime, fw, slugs)]
        if fw != "claude":
            continue
        missing = []
        for f in sorted(d.glob("*.md")):
            if f.is_symlink() or not f.resolve().is_relative_to(root):
                continue  # never follow a link out of the project
            yaml_body, _ = parse_yaml_front_matter(f.read_text(encoding="utf-8", errors="ignore"))
            if yaml_body is not None and not ar.EFFORT_LINE_RE.search(yaml_body):
                missing.append(f.name)
        if missing:
            lines.append(f"  !  WARN {len(missing)} Claude agent file(s) on disk set no effort: and run at the session "
                         f"level (e.g. {', '.join(missing[:3])}); --update --merge adds it where the build log has a "
                         "baseline, else reports it")
    lines += [f"  ℹ  not checkable from files: {c}" for c in ar.RUNTIME_CAVEATS]
    return lines, 0


def run_doctor(project: str | None) -> int:
    """``--doctor``: print the cross-surface report for the project at *project* (else the working directory).

    Args:
        project: The ``--project`` value.

    Returns:
        1 when any FAIL is reported, else 0.

    Raises:
        Nothing. A section that can't be read prints the error and counts as a FAIL.
    """
    root = Path(project or ".").resolve()
    brief = _brief(root)
    fails = 0
    print(f"agentteams doctor — {root}")
    sections = (("Rules projection", lambda: _rules(root, brief)), ("Memory index", lambda: (_indexes(root, brief), 0)),
                ("Copilot body limit", lambda: _body_limit(root)), ("Agents", lambda: (_agents(root), 0)),
                ("Shared AGENTS.md", lambda: (_shared_agents_md(root), 0)), ("Backup stubs", lambda: _backup_stubs(root)),
                ("Hygiene rule ids", lambda: _hygiene_rule_ids(root)),
                ("Signing governance", lambda: _signing_governance(root)),
                ("Agent runtime (model / effort)", lambda: _agent_runtime(root, brief)))
    for title, section in sections:
        try:
            lines, failed = section()
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            lines, failed = [f"  ✗  FAIL could not check: {exc}"], 1
        if lines:
            print(f"\n{title}")
            print("\n".join(lines))
        fails += failed
    print(f"\n{'✗' if fails else '✓'}  {fails} failure(s).")
    return 1 if fails else 0


__all__ = ["run_doctor"]
