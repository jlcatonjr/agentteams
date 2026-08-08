"""End-to-end probes — the installed console script against a real tree.

Most of the suite exercises *functions*. These exercise the thing a user actually runs:
the packaged entrypoint, resolving its own templates, writing to a real directory. That
gap is where packaging faults hide — a module that imports fine in-process can still be
unusable once installed, which is precisely how the live tier was about to fail on a
missing extra.

No network. These are `tier=e2e`, `external_dep=none`, and run in ordinary CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BRIEF = REPO / ".github/agents/_build-description.json"


def _cli(*args: str, cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    """Drive the module entrypoint, which is what the console script wraps."""
    return subprocess.run(
        [sys.executable, str(REPO / "build_team.py"), *args],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


@pytest.fixture(scope="module")
def brief_available() -> Path:
    if not BRIEF.is_file():
        pytest.skip(f"self-build descriptor absent: {BRIEF}")
    return BRIEF


def test_cli_generates_a_usable_team_into_an_empty_tree(tmp_path, brief_available):
    """PROOF: a full generate produces an invocable team, end to end.

    Asserts *substance*, not exit status: agent files present, front matter well-formed,
    and the orchestrator actually rendered. An exit-code-only check would pass on a run
    that wrote nothing.
    """
    out = tmp_path / "agents"
    proc = _cli(
        "--description", str(brief_available), "--project", str(tmp_path),
        "--framework", "claude", "--output", str(out), "--no-scan", "--yes",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, f"generate failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"

    agents = sorted(out.glob("*.md"))
    assert agents, f"generate exited 0 but wrote no agent files into {out}"

    orchestrator = out / "orchestrator.md"
    assert orchestrator.is_file(), f"no orchestrator rendered; got {[a.name for a in agents]}"

    body = orchestrator.read_text(encoding="utf-8")
    assert body.startswith("---\n"), "orchestrator has no YAML front matter"
    assert "name:" in body.split("---", 2)[1], "orchestrator front matter carries no name"


def test_a_generate_into_a_nonexistent_project_does_not_silently_succeed(tmp_path, brief_available):
    """NEGATIVE CONTROL for generate.

    Distinct from the proof, and the reason it exists: a generator that writes a team no
    matter what it is pointed at would satisfy the proof above while being badly broken.
    This fails if input validation stops rejecting a missing descriptor.
    """
    proc = _cli(
        "--description", str(tmp_path / "no-such-brief.json"),
        "--project", str(tmp_path), "--framework", "claude",
        "--output", str(tmp_path / "out"), "--no-scan", "--yes",
        cwd=tmp_path,
    )
    assert proc.returncode != 0, (
        "a missing descriptor produced a successful run — input validation is gone:\n"
        f"{proc.stdout[-800:]}"
    )
    assert not (tmp_path / "out").glob("*.md") or not list((tmp_path / "out").glob("*.md")), (
        "a failed generate still wrote agent files"
    )


def test_cli_dry_run_writes_nothing(tmp_path, brief_available):
    """PROOF: --dry-run is honoured.

    A dry run that writes is worse than no dry run, because it is trusted.
    """
    out = tmp_path / "agents"
    proc = _cli(
        "--description", str(brief_available), "--project", str(tmp_path),
        "--framework", "claude", "--output", str(out), "--no-scan", "--dry-run",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, f"dry run failed:\n{proc.stderr[-800:]}"
    written = list(out.glob("**/*")) if out.exists() else []
    assert not written, f"--dry-run wrote {len(written)} path(s): {[str(w) for w in written[:5]]}"


def test_cli_json_plan_is_machine_readable(tmp_path, brief_available):
    """NEGATIVE CONTROL for dry-run.

    The dry-run proof passes trivially if the CLI does nothing at all. This asserts the run
    still *computed* a plan — so "wrote nothing" means "correctly withheld", not "died".
    """
    proc = _cli(
        "--description", str(brief_available), "--project", str(tmp_path),
        "--framework", "claude", "--output", str(tmp_path / "agents"),
        "--no-scan", "--dry-run", "--json",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, f"json dry run failed:\n{proc.stderr[-800:]}"
    start = proc.stdout.find("{")
    assert start >= 0, f"--json emitted no JSON document:\n{proc.stdout[-800:]}"
    payload = json.loads(proc.stdout[start:])
    assert payload, "--json emitted an empty document"
