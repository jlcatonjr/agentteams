"""test_dry_run_front_matter_parity.py — the preview must predict what the run does.

`--dry-run` reported `UNCHANGED` for a file whose `allowed-tools` grant the real run widened.
Found on 2026-08-03 by rehearsing a merge against an isolated copy of the tree rather than
trusting its dry run: the dry run predicted 7 changed files, the real merge changed 12.

The cause is structural, not a threshold. Both paths call `_merge_fenced_content`; **only the
real path then calls `_merge_front_matter`**:

    emit.py:565  real      _merge_fenced_content(...) → _merge_front_matter(..., baseline) → applied
    emit.py:455  dry run   _merge_fenced_content(...)                    ← and nothing more

So the dry run's `migrated == existing_text` comparison is made against content whose front
matter was never merged. A file whose *only* change is a front-matter key therefore compares
equal and is reported unchanged.

Why it matters more than an ordinary preview inaccuracy: front matter is where `allowed-tools`
lives. C-3 makes widening a capability grant a privileged change, and `--dry-run` is what an
operator reads before deciding to proceed.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import emit

#: The fixture is a capability **narrowing**: the template grants less than the deployed file.
#:
#: It was a widening until 2026-08-06. That direction stopped being applied when
#: `_CAPABILITY_FRONT_MATTER_KEYS` was unified to include `allowed-tools` — a widening is
#: proposal-only for every capability key now, which is what C-3 requires and what `tools:`
#: already did. Narrowing is still applied on an unmodified file (it removes a capability, so
#: it needs no authority the engine lacks), so it is the direction that still exercises this
#: test's actual subject: whether the PREVIEW predicts what the RUN does to front matter.
#: `test_widening_is_proposed_not_applied` below pins the other direction.
RENDERED = (
    "---\n"
    "name: Navigator\n"
    "description: d\n"
    "tools: Read, Grep, Glob\n"
    "---\n\n"
    "<!-- AGENTTEAMS:BEGIN body v=1 -->\n"
    "## Body\n\nSame in both.\n"
    "<!-- AGENTTEAMS:END body -->\n\n"
    # Present in both copies: without it `_ensure_project_notes_section` adds it during the
    # merge, so every file would differ and the negative control below could never pass.
    "## Project-Specific Notes\n\n- operator content.\n"
)

#: Identical to RENDERED except the capability grant — so the *only* difference is front matter.
DEPLOYED = RENDERED.replace(
    "tools: Read, Grep, Glob", "tools: Read, Grep, Glob, Bash(python -m agentteams.research:*)"
)


def _tree(tmp_path: Path) -> Path:
    out = tmp_path / "agents"
    (out / "references").mkdir(parents=True)
    (out / "navigator.md").write_text(DEPLOYED, encoding="utf-8")
    # The build log's front-matter baseline is what proves the file is unmodified since
    # generation, and therefore what makes the real run apply the template's value.
    (out / "references" / "build-log.json").write_text(
        json.dumps({
            "front_matter_baseline": {
                "navigator.md": {
                    "name": "Navigator",
                    "description": "d",
                    "tools": "Read, Grep, Glob, Bash(python -m agentteams.research:*)",
                }
            }
        }),
        encoding="utf-8",
    )
    return out


def _entry(result, name: str):
    for e in (result.dry_run_report.entries if result.dry_run_report else []):
        if str(e.path).endswith(name):
            return e
    return None


def test_dry_run_does_not_call_a_front_matter_only_change_unchanged(tmp_path: Path) -> None:
    """The defect. The only difference is a capability grant, and the preview hid it."""
    out = _tree(tmp_path)
    result = emit.emit_all(
        [("navigator.md", RENDERED)], output_dir=out, dry_run=True, merge=True, yes=True
    )
    entry = _entry(result, "navigator.md")
    assert entry is not None, "no dry-run entry for navigator.md"
    assert entry.action != "UNCHANGED", (
        "dry run reports UNCHANGED for a file whose front matter the real run rewrites — "
        "the preview an operator reads before approving a capability change"
    )


def test_the_real_run_does_change_it(tmp_path: Path) -> None:
    """Anti-vacuity: the test above is only meaningful if the real run genuinely differs."""
    out = _tree(tmp_path)
    emit.emit_all(
        [("navigator.md", RENDERED)], output_dir=out, dry_run=False, merge=True, yes=True,
        auto_fence_legacy=False,
    )
    written = (out / "navigator.md").read_text(encoding="utf-8")
    assert "Bash(python -m agentteams.research:*)" not in written, (
        "the real run did not apply the narrowing, so the parity test proves nothing"
    )


def test_widening_is_proposed_not_applied(tmp_path: Path) -> None:
    """A template that GRANTS MORE than the deployed file is never applied unattended.

    C-3 makes widening a capability grant a privileged change. Before the capability-key sets
    were unified (2026-08-06) this held for `tools:` and silently failed for `allowed-tools:`,
    which is the key every Claude team actually carried — so the one framework whose governance
    agents claim to be read-only was the one where a template widening could land unreviewed.
    """
    out = tmp_path / "agents"
    (out / "references").mkdir(parents=True)
    narrow = "tools: Read, Grep, Glob"
    wide = "tools: Read, Grep, Glob, Bash"
    (out / "navigator.md").write_text(RENDERED, encoding="utf-8")   # deployed = narrow
    (out / "references" / "build-log.json").write_text(
        json.dumps({"front_matter_baseline": {
            "navigator.md": {"name": "Navigator", "description": "d", "tools": "Read, Grep, Glob"}
        }}),
        encoding="utf-8",
    )
    emit.emit_all(
        [("navigator.md", RENDERED.replace(narrow, wide))],
        output_dir=out, dry_run=False, merge=True, yes=True, auto_fence_legacy=False,
    )
    written = (out / "navigator.md").read_text(encoding="utf-8")
    assert narrow in written and "Glob, Bash" not in written, (
        "a template widening was applied unattended to an unmodified file"
    )


def test_dry_run_still_reports_unchanged_when_nothing_changes(tmp_path: Path) -> None:
    """Negative control. The fix must not make every file look changed."""
    out = _tree(tmp_path)
    (out / "navigator.md").write_text(RENDERED, encoding="utf-8")
    result = emit.emit_all(
        [("navigator.md", RENDERED)], output_dir=out, dry_run=True, merge=True, yes=True
    )
    entry = _entry(result, "navigator.md")
    assert entry is not None and entry.action == "UNCHANGED", (
        f"an identical file is no longer reported UNCHANGED: {entry.action if entry else None}"
    )


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """`--dry-run`'s whole contract. Running more of the real path must not start writing."""
    import hashlib

    out = _tree(tmp_path)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*")) if p.is_file()}
    emit.emit_all([("navigator.md", RENDERED)], output_dir=out, dry_run=True, merge=True, yes=True)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*")) if p.is_file()}
    assert after == before, "dry run wrote to disk"
