"""Hermetic security intelligence for tests that drive ``build_team.py`` in a subprocess.

A subprocess build fetches live intel. Without network the fetch fails, the snapshot is stale,
and ``security_gate._assert_security_intelligence_fresh`` refuses the run, so these tests errored
on every offline run (remediation row GROK-RETRO-R7). A pytest monkeypatch cannot reach a
subprocess, so the subprocess instead reads a synthetic cache through the existing
``--security-offline`` path (``security_refs.build_security_placeholders``):

* the cache carries a current ``generated_at`` and no advisories, so nothing real is relabelled;
* the producer recomputes ``SECURITY_DATA_PAYLOAD_DIGEST`` over it, so the digest check holds;
* it lives only in the test's tmp output folder.

No product bypass and no waiver. ``--security-offline`` does not apply to convert, interop or
bridge, which force a live fetch; tests of those paths run in-process and patch the gate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agentteams.frameworks.registry import FRAMEWORKS

OFFLINE_FLAG = "--security-offline"


def seed_offline_intel(output: Path, framework: str) -> list[str]:
    """Write a synthetic, current vulnerability-watch cache where ``--security-offline`` reads it.

    The build reads the cache under the adapter-normalised output directory (goose, for one,
    nests ``--output`` under ``.goose/recipes``), so the seed uses the same normaliser.

    Args:
        output: The ``--output`` path the subprocess build will be given.
        framework: The ``--framework`` the build will be given.

    Returns:
        The extra CLI arguments that make the build read the seeded cache.
    """
    output_dir = FRAMEWORKS[framework]().normalize_output_path(output.resolve())
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [],
        "vulnerabilities": [],
        "osv_packages": [],
    }
    (refs / "security-vulnerability-watch.json").write_text(json.dumps(payload), encoding="utf-8")
    return [OFFLINE_FLAG]



def run_build(brief: Path, project: Path, framework: str, output: Path, *extra: str,
              seed: bool = True) -> "subprocess.CompletedProcess[str]":
    """Run ``build_team.py`` for *framework* in a subprocess, reading hermetic intel.

    Args:
        brief: The brief to pass as ``--description``.
        project: The project root (``--project`` and the working directory).
        framework: The ``--framework``.
        output: The ``--output`` agents directory.
        *extra: More CLI arguments (``--update``, ``--check`` …).
        seed: Seed a fresh intel cache first. Pass ``False`` when the caller has seeded already and the
            cache must not change (a write-nothing check).

    Returns:
        The completed process (text mode, output captured).
    """
    import subprocess
    import sys

    intel = seed_offline_intel(output, framework) if seed else [OFFLINE_FLAG]
    build_team = Path(__file__).resolve().parent.parent / "build_team.py"
    return subprocess.run([sys.executable, str(build_team), "--description", str(brief), "--project", str(project),
                           "--framework", framework, "--output", str(output), "--no-scan", "--yes", *extra, *intel],
                          cwd=project, capture_output=True, text=True, timeout=300)
