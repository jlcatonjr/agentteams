"""
team_package.py — portable team package: a durable canonical directory plus
its generic bridge, zipped into one portable archive (open-items remediation
OPEN-5, depends only on OPEN-3's generic bridge target — see
tmp/by-week/2026-W33/canonical-format-open-items-remediation.plan.md section
2.5). A repo with zero agentteams integration can unpack the zip and read the
canonical tree + generic bridge prose directly; a repo with a native adapter
can later get full-fidelity native rendering via ``--interop-from
<unpacked>/.agentteams/canonical --interop-source-framework canonical
--framework <target>``.

The canonical snapshot and the generic bridge are both derived from the same
source_dir within one package_team() invocation (no intermediate write, no
window for source_dir to change between the two), but via two independent
parsers: materialize_canonical() consumes export_to_cai()'s output, while
run_bridge() re-derives its own inventory via bridge_sources' separate,
more lenient front-matter reader — bridge.py deliberately stays independent
of CAI/canonical internals (see the predecessor plan's boundary statement).
For a well-formed source team the two agree; test_team_package.py pins this
agreement empirically (roster parity) rather than claiming an architectural
guarantee that doesn't exist.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.atomicio import _atomic_copy
from agentteams.bridge import run_bridge
from agentteams.canonical import DEFAULT_CANONICAL_SUBDIR, materialize_canonical
from agentteams.interop import detect_framework, export_to_cai

__all__ = ["PackageResult", "package_team"]


@dataclass
class PackageResult:
    success: bool
    zip_path: Path
    dry_run: bool
    source_framework: str = ""
    agent_count: int = 0
    errors: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def package_team(
    *,
    source_dir: Path,
    output_zip: Path,
    source_framework: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> PackageResult:
    """Package a native-framework team as a portable canonical + generic-bridge zip.

    Args:
        source_dir: Native framework source team directory. Not a canonical
            directory — a canonical source has no native framework left to
            render the bundled generic bridge from; use ``--bridge-from
            <canonical dir> --bridge-source-framework canonical --framework
            generic`` directly for that case instead.
        output_zip: Path to write the ``.zip`` archive to.
        source_framework: Optional explicit source framework (auto-detected
            via ``detect_framework`` otherwise).
        dry_run: When True, report what would be written without writing
            anything (zero filesystem side effects, matching every other
            write path's dry-run contract).
        overwrite: When False (default) and output_zip already exists, raises
            FileExistsError rather than silently replacing it — matching
            convert/interop/bridge's no-silent-overwrite contract.

    Returns:
        PackageResult.

    Raises:
        FileNotFoundError: source_dir does not exist.
        IsADirectoryError: output_zip is an existing directory.
        FileExistsError: output_zip already exists and overwrite is False.
        ValueError: source_dir detects (or is declared) as a canonical
            directory.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")
    if output_zip.is_dir():
        raise IsADirectoryError(f"{output_zip} is a directory, not a zip file path")
    if output_zip.exists() and not overwrite:
        # Not exempted for dry_run, unlike the is_dir() check above it isn't
        # either: a dry-run must report what a real run would actually do,
        # including refusing (D.7 finding — this exemption previously made
        # --dry-run report success for a combination that would then fail).
        raise FileExistsError(
            f"{output_zip} already exists — pass --overwrite to replace it."
        )

    src_fw = source_framework or detect_framework(source_dir)
    if src_fw == "canonical":
        raise ValueError(
            "package_team expects a native framework source, not a canonical "
            "directory — a canonical source has no native framework of its own "
            "to render the bundled generic bridge from. Use --bridge-from "
            "<canonical dir> --bridge-source-framework canonical --framework "
            "generic directly for that case."
        )

    if dry_run:
        return PackageResult(
            success=True, zip_path=output_zip, dry_run=True, source_framework=src_fw,
            notices=[f"Would package {src_fw} source at {source_dir} into {output_zip}"],
        )

    cai = export_to_cai(source_dir, src_fw)
    agent_count = len(cai.get("agents") or [])

    with tempfile.TemporaryDirectory(prefix="agentteams-package-") as tmp:
        stage = Path(tmp)

        canon_dir = stage / "canonical"
        materialize_canonical(cai, canon_dir)

        bridge_root = stage / "bridge"
        # Copy source_dir under bridge_root (rather than pointing run_bridge
        # at the original, external location) so it's a genuine descendant of
        # output_root — every other run_bridge caller has this property, and
        # bridge.py's rel_to_root() relies on it to relativize bridge-manifest
        # .json's source_dir field and agent-inventory.md's Source-file column.
        # Without it, both fields silently fall back to the absolute path,
        # leaking the packaging machine's home directory/username into a zip
        # meant to be handed to a third party (2026-08-10 adversarial finding,
        # step D.7 — the exact leak class rel_to_root's own docstring, commit
        # 1937cbc, was written to close).
        bridge_source = bridge_root / "source"
        shutil.copytree(source_dir, bridge_source)
        bridge_result = run_bridge(
            source_dir=bridge_source,
            source_framework=src_fw,
            target_framework="generic",
            output_root=bridge_root,
            overwrite=True,
        )
        if not bridge_result.success:
            return PackageResult(
                success=False, zip_path=output_zip, dry_run=False,
                source_framework=src_fw, errors=list(bridge_result.errors),
            )

        pair_dir_rel = Path("references") / "bridges" / f"{src_fw}-to-generic"
        bridge_pair_dir = bridge_root / pair_dir_rel

        staged_zip = stage / output_zip.name
        with zipfile.ZipFile(staged_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(canon_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, str(Path(DEFAULT_CANONICAL_SUBDIR) / path.relative_to(canon_dir)))
            for path in sorted(bridge_pair_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, str(pair_dir_rel / path.relative_to(bridge_pair_dir)))

        _atomic_copy(staged_zip, output_zip)

    return PackageResult(
        success=True, zip_path=output_zip, dry_run=False,
        source_framework=src_fw, agent_count=agent_count,
        notices=list(bridge_result.notices),
    )
