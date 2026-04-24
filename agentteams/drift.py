"""
drift.py — Detect template-to-instance drift in generated agent teams.

Compares current template hashes against those recorded in build-log.json
at generation time, and reports which agent files need re-rendering.

Drift has two independent dimensions:

  Content drift  — a template's text has changed since the last build.
                   Detected via template_hashes in the build-log.
                   Resolved by: re-rendering affected output files.

  Structural drift — the set of planned output files has changed.
                     Detected by diff-ing output_files_map (v1.2+) against
                     the current manifest's output_files.
                     Resolved by: adding new files, reporting removed files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class DriftReport:
    """Results of a content-drift detection run."""

    def __init__(self) -> None:
        self.changed_templates: list[dict[str, str]] = []
        self.missing_templates: list[str] = []
        self.new_templates: list[str] = []
        self.unchanged: list[str] = []

    @property
    def has_drift(self) -> bool:
        """Return True if any templates have changed since last generation."""
        return bool(self.changed_templates or self.missing_templates)

    @property
    def affected_output_files(self) -> list[str]:
        """Return output file paths affected by drifted templates."""
        return [entry["output_file"] for entry in self.changed_templates]


class StructuralDiffReport:
    """Results of a structural diff between a build-log and a current manifest.

    Attributes:
        added_files:     Files in the new manifest that were not in the old log.
        removed_files:   Files in the old log that are absent from the new manifest.
        drifted_files:   Files present in both but whose template hash has changed.
        unchanged_files: Files present in both with the same template hash.
        team_membership_changed: True when agent_slug_list differs between old and new.
        legacy_log:      True when build-log predates schema v1.2 (no output_files_map).
    """

    def __init__(self) -> None:
        self.added_files: list[dict[str, Any]] = []
        self.removed_files: list[dict[str, Any]] = []
        self.drifted_files: list[dict[str, Any]] = []
        self.unchanged_files: list[dict[str, Any]] = []
        self.team_membership_changed: bool = False
        self.manifest_changed: bool = False
        self.legacy_log: bool = False

    @property
    def has_changes(self) -> bool:
        """Return True if any structural or content changes require action."""
        return bool(
            self.added_files
            or self.drifted_files
            or self.team_membership_changed
        )

    @property
    def update_files(self) -> list[dict[str, Any]]:
        """Return all file entries that need to be written (added + drifted)."""
        return self.added_files + self.drifted_files


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def load_build_log(agents_dir: Path) -> dict[str, Any]:
    """Load build-log.json from an agents directory.

    Args:
        agents_dir: Path to the .github/agents/ directory.

    Returns:
        Parsed build-log.json dict.

    Raises:
        FileNotFoundError: If build-log.json does not exist.
        ValueError:        If build-log.json is malformed.
    """
    log_path = agents_dir / "references" / "build-log.json"
    if not log_path.exists():
        raise FileNotFoundError(
            f"No build-log.json found at {log_path}. "
            "Run build_team.py first to generate the agent team."
        )
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed build-log.json: {exc}") from exc

    return data


def detect_drift(
    agents_dir: Path,
    templates_dir: Path,
    *,
    build_log: dict[str, Any] | None = None,
) -> DriftReport:
    """Compare current template hashes against the build log.

    Args:
        agents_dir:    Path to the .github/agents/ directory containing build-log.json.
        templates_dir: Path to the templates/ directory with current templates.
        build_log:     Pre-loaded build log dict. If None, loaded from agents_dir.

    Returns:
        DriftReport with details of changed, missing, and new templates.
    """
    if build_log is None:
        build_log = load_build_log(agents_dir)

    recorded_hashes: dict[str, str] = build_log.get("template_hashes", {})
    output_files: list[dict[str, Any]] = build_log.get("output_files_map", [])

    # Build template→output_file mapping from the build log's file list
    # We reconstruct by checking which output file each template would produce
    template_to_output = _build_template_output_map(build_log)

    report = DriftReport()

    if not recorded_hashes:
        # Build log predates hash tracking (schema_version 1.0)
        # All templates are treated as potentially drifted
        report.changed_templates = [
            {"template": tpl, "output_file": template_to_output.get(tpl, "unknown"), "reason": "no recorded hash (legacy build log)"}
            for tpl in _discover_used_templates(build_log, templates_dir)
        ]
        return report

    # Check each recorded template
    for template_rel, recorded_hash in recorded_hashes.items():
        full_path = templates_dir / template_rel
        if not full_path.exists():
            report.missing_templates.append(template_rel)
            continue

        current_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]
        if current_hash != recorded_hash:
            report.changed_templates.append({
                "template": template_rel,
                "output_file": template_to_output.get(template_rel, "unknown"),
                "recorded_hash": recorded_hash,
                "current_hash": current_hash,
                "reason": "template content changed",
            })
        else:
            report.unchanged.append(template_rel)

    # Check for new templates that weren't in the build log
    for template_file in _iter_template_files(templates_dir):
        rel = str(template_file.relative_to(templates_dir))
        if rel not in recorded_hashes:
            report.new_templates.append(rel)

    return report


def print_drift_report(report: DriftReport) -> None:
    """Print a human-readable drift report to stdout.

    Args:
        report: DriftReport from detect_drift().
    """
    if not report.has_drift and not report.new_templates:
        print("No drift detected. All templates match the last build.")
        return

    if report.changed_templates:
        print(f"\n  Changed templates ({len(report.changed_templates)}):")
        for entry in report.changed_templates:
            print(f"    {entry['template']} → {entry['output_file']}")
            print(f"      Reason: {entry['reason']}")

    if report.missing_templates:
        print(f"\n  Missing templates ({len(report.missing_templates)}):")
        for tpl in report.missing_templates:
            print(f"    {tpl} (template file deleted)")

    if report.new_templates:
        print(f"\n  New templates ({len(report.new_templates)}):")
        for tpl in report.new_templates:
            print(f"    {tpl} (not in build log)")

    print(f"\n  Unchanged: {len(report.unchanged)} template(s)")

    if report.has_drift:
        print("\n  Run `python build_team.py --update` to re-render drifted files.")


def compute_structural_diff(
    old_log: dict[str, Any],
    new_manifest: dict[str, Any],
    templates_dir: Path,
) -> StructuralDiffReport:
    """Diff a build-log against a freshly-computed manifest to find structural changes.

    Detects three categories of change:

    - **Added**: files in the new manifest that are absent from the old log.
    - **Removed**: files in the old log that are absent from the new manifest.
    - **Drifted**: files present in both whose template hash differs from the
      hash recorded in the build-log (or whose template was previously unknown).

    Also sets ``team_membership_changed`` when the agent slug lists differ, which
    forces ``copilot-instructions.md`` to be re-rendered regardless of template hash.

    Args:
        old_log:       Parsed build-log.json from the existing agents directory.
        new_manifest:  Manifest produced by analyze.build_manifest() for the same project.
        templates_dir: Path to the templates/ directory (used for current hash computation).

    Returns:
        StructuralDiffReport classifying every planned output file.
    """
    report = StructuralDiffReport()

    # -----------------------------------------------------------------------
    # Legacy log (schema < 1.2): no output_files_map recorded.
    # Fall back to files_written for a best-effort removed-file list.
    # Treat all new-manifest files as additions (safe: emit is idempotent).
    # -----------------------------------------------------------------------
    old_files_map: list[dict[str, Any]] = old_log.get("output_files_map", [])
    if not old_files_map:
        report.legacy_log = True
        old_written = set(Path(f).name for f in old_log.get("files_written", []))
        for entry in new_manifest.get("output_files", []):
            file_name = Path(entry["path"]).name
            if file_name not in old_written:
                report.added_files.append(dict(entry))
            else:
                report.drifted_files.append(dict(entry))
        # team membership is always considered changed for legacy logs
        report.team_membership_changed = True
        return report

    # -----------------------------------------------------------------------
    # Standard diff (schema v1.2+)
    # -----------------------------------------------------------------------
    old_by_path: dict[str, dict[str, Any]] = {f["path"]: f for f in old_files_map}
    new_by_path: dict[str, dict[str, Any]] = {
        f["path"]: f for f in new_manifest.get("output_files", [])
    }

    # Build current template-hash lookup (same logic as detect_drift)
    recorded_hashes: dict[str, str] = old_log.get("template_hashes", {})

    for path, new_entry in new_by_path.items():
        if path not in old_by_path:
            report.added_files.append(dict(new_entry))
        else:
            old_entry = old_by_path[path]
            template_rel = new_entry.get("template", "")
            # Re-render if the template file itself changed since last build
            if template_rel and template_rel in recorded_hashes:
                tpl_path = templates_dir / template_rel
                if tpl_path.exists():
                    current_hash = hashlib.sha256(tpl_path.read_bytes()).hexdigest()[:16]
                    if current_hash != recorded_hashes[template_rel]:
                        entry = dict(new_entry)
                        entry["_reason"] = "template content changed"
                        report.drifted_files.append(entry)
                        continue
                else:
                    # Template is gone — still re-render so fallback logic kicks in
                    entry = dict(new_entry)
                    entry["_reason"] = "template file missing"
                    report.drifted_files.append(entry)
                    continue
            elif template_rel and template_rel not in recorded_hashes:
                # Template wasn't tracked before — treat as drifted to be safe
                entry = dict(new_entry)
                entry["_reason"] = "template not previously tracked"
                report.drifted_files.append(entry)
                continue
            report.unchanged_files.append(dict(new_entry))

    for path, old_entry in old_by_path.items():
        if path not in new_by_path:
            report.removed_files.append(dict(old_entry))

    # -----------------------------------------------------------------------
    # Team membership check: force copilot-instructions re-render when the
    # agent slug list has changed even if the template hash is the same.
    # -----------------------------------------------------------------------
    old_slugs = old_log.get("agent_slug_list", [])
    new_slugs = new_manifest.get("agent_slug_list", [])
    if sorted(old_slugs) != sorted(new_slugs):
        report.team_membership_changed = True
        # Move copilot-instructions from unchanged → drifted
        framework = new_manifest.get("framework", "copilot-vscode")
        instructions_path = "../CLAUDE.md" if framework == "claude" else "../copilot-instructions.md"
        report.unchanged_files = [
            f for f in report.unchanged_files if f["path"] != instructions_path
        ]
        # Add to drifted only if it's not already there
        already_drifted = {f["path"] for f in report.drifted_files}
        if instructions_path not in already_drifted:
            instr_entry = new_by_path.get(
                instructions_path,
                {"path": instructions_path, "template": "copilot-instructions.template.md", "type": "instructions"},
            )
            entry = dict(instr_entry)
            entry["_reason"] = "team membership changed"
            report.drifted_files.append(entry)

    # -----------------------------------------------------------------------
    # Manifest drift: a brief/config change can alter rendered content without
    # changing template hashes or file structure. Promote unchanged files into
    # the drifted set when the manifest fingerprint changes.
    # -----------------------------------------------------------------------
    old_fingerprint = old_log.get("manifest_fingerprint")
    new_fingerprint = compute_manifest_fingerprint(new_manifest)
    if old_fingerprint != new_fingerprint:
        report.manifest_changed = True
        reason = "manifest values changed" if old_fingerprint else "manifest fingerprint unavailable"
        promoted: list[dict[str, Any]] = []
        already_flagged = {f["path"] for f in report.added_files + report.drifted_files}
        for entry in report.unchanged_files:
            if entry["path"] in already_flagged:
                continue
            refreshed = dict(entry)
            refreshed["_reason"] = reason
            promoted.append(refreshed)
        report.drifted_files.extend(promoted)
        report.unchanged_files = []

    return report


def print_structural_diff_report(report: StructuralDiffReport) -> None:
    """Print a human-readable structural diff report to stdout.

    Args:
        report: StructuralDiffReport from compute_structural_diff().
    """
    if report.legacy_log:
        print("  ℹ  Build-log predates schema v1.2 — using best-effort structural diff.")
    if report.manifest_changed:
        print("  ℹ  Manifest values changed since the last build — re-rendering affected files.")

    if not report.has_changes and not report.removed_files:
        print("  No structural changes detected.")
        return

    if report.added_files:
        print(f"\n  Added ({len(report.added_files)}):")
        for f in report.added_files:
            print(f"    + {f['path']}")

    if report.drifted_files:
        print(f"\n  Updated ({len(report.drifted_files)}):")
        for f in report.drifted_files:
            reason = f.get("_reason", "template changed")
            print(f"    ~ {f['path']}  ({reason})")

    if report.removed_files:
        print(f"\n  Removed ({len(report.removed_files)}) — not deleted (use --prune to remove):")
        for f in report.removed_files:
            print(f"    - {f['path']}")

    if report.unchanged_files:
        print(f"\n  Unchanged: {len(report.unchanged_files)} file(s)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_template_output_map(build_log: dict[str, Any]) -> dict[str, str]:
    """Reconstruct template→output_file mapping from build log data."""
    mapping: dict[str, str] = {}
    # Use files_written list to infer mappings via slug matching
    files_written = build_log.get("files_written", [])
    template_hashes = build_log.get("template_hashes", {})

    for template_rel in template_hashes:
        # Derive expected output filename from template path
        tpl_path = Path(template_rel)
        stem = tpl_path.stem  # e.g. 'orchestrator.template'
        if stem.endswith(".template"):
            stem = stem[:-9]  # strip '.template'

        # Find matching written file
        for written in files_written:
            written_stem = Path(written).stem
            if written_stem.endswith(".agent"):
                written_stem = written_stem[:-6]
            if written_stem == stem:
                mapping[template_rel] = written
                break
        else:
            # Fallback: derive from template path
            mapping[template_rel] = f"{stem}.agent.md"

    return mapping


def compute_manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """Return a stable hash for manifest values that affect rendered output.

    Args:
        manifest: Manifest produced by analyze.build_manifest().

    Returns:
        First 16 hex characters of a SHA-256 digest.
    """

    def _sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: _sanitize(val)
                for key, val in value.items()
                if not str(key).startswith("_")
            }
        if isinstance(value, list):
            return [_sanitize(item) for item in value]
        return value

    payload = _sanitize(manifest)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _discover_used_templates(build_log: dict[str, Any], templates_dir: Path) -> list[str]:
    """Infer which templates were used from a legacy build log (no hashes)."""
    templates: list[str] = []
    for tpl_file in _iter_template_files(templates_dir):
        rel = str(tpl_file.relative_to(templates_dir))
        templates.append(rel)
    return templates


def _iter_template_files(templates_dir: Path) -> list[Path]:
    """Return all .template.md files in the templates directory."""
    return sorted(templates_dir.rglob("*.template.md"))


def detect_user_customizations(
    agents_dir: Path,
    *,
    build_log: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Detect generated agent files that have been edited since last build.

    Compares the SHA-256 hash of each on-disk file against the hash recorded
    in ``build-log.json`` at the time of last generation.  A hash mismatch
    indicates the file has been modified since generation -- either by the user
    or by an automated agent (e.g. ``@agent-updater``).

    This result is **advisory**.  It surfaces for human review before the user
    commits to ``--merge`` or ``--overwrite``; it is not a hard block.

    Args:
        agents_dir: Path to the ``.github/agents/`` directory.
        build_log:  Pre-loaded build-log dict.  If ``None``, loaded from
                    ``agents_dir``.

    Returns:
        List of dicts, each with keys ``path`` (absolute path string) and
        ``reason`` (always ``"modified since last build"``).  Empty list if
        no customizations are detected or if ``build-log.json`` is absent.
    """
    try:
        if build_log is None:
            build_log = load_build_log(agents_dir)
    except (FileNotFoundError, ValueError):
        return []

    file_hashes: dict[str, str] = build_log.get("file_hashes", {})
    if not file_hashes:
        return []

    customized: list[dict[str, str]] = []
    for rel_path, recorded_hash in file_hashes.items():
        # rel_path may start with '../' (e.g. '../copilot-instructions.md')
        abs_path = (agents_dir / Path(rel_path)).resolve()
        if not abs_path.exists():
            continue
        current_hash = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
        if current_hash != recorded_hash:
            customized.append({
                "path": str(abs_path),
                "rel_path": rel_path,
                "reason": "modified since last build",
            })

    return customized
