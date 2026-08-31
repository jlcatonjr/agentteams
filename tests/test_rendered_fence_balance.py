"""test_rendered_fence_balance.py — the fences that actually shipped must pair up.

`tests/test_template_memory_index_fence.py` guards fence balance in the **templates**. Nothing
guarded it in the **renders**, and that is where the defect was.

The history matters, because it is the reason this file exists rather than a note saying the
problem went away. On 2026-08-01 `conflict-auditor.md` could not be merged: *"Nested fence not
allowed: invariant_core inside content"*. The template was blamed. On 2026-08-02 the template
was measured and found clean — all 66 balanced and unnested — and the real defect was located
one step upstream: the **rendered** file was truncated after `BEGIN
handoff_payload_conflict_codes`, losing its heading, its END, and the entire following fence.
8 BEGIN, 7 END. An unbalanced render makes `_extract_fenced_regions` return an error string;
`emit.py` tests `isinstance(dict)`, sees a non-dict, and falls through to wrapping the whole
body in the `content` fence — which puts the template's own fences inside it. The "nested
fence" message was a symptom two steps downstream, and the cause was never found.

Today both rendered files are balanced (8 BEGIN, 8 END) and the merge no longer errors. So the
defect stopped reproducing without anyone fixing it, which is the least trustworthy state a
finding can be in: *a probe can stop firing because the system improved or because the probe
went blind*. Closing those two rows on a passing measurement, with no standing check over the
thing that was broken, would be exactly that mistake.

This is the missing check. It reads what the repository's own two teams have on disk — the
artifacts, not the sources — and fails if any fence set is unbalanced, crossed or nested. If
the truncation returns, it surfaces here with the file named, rather than as a confusing
merge error in a long run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams.backup import BACKUP_DIR_NAME

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two teams this repository generates for itself. Both are checked: the truncation was
#: observed in exactly 1 of 54 rendered files, so a check over one framework could miss it.
RENDER_ROOTS = (".github/agents", ".claude/agents")

_BEGIN_RE = re.compile(r"AGENTTEAMS[A-Z_-]*:BEGIN\s+(\S+)")
_END_RE = re.compile(r"AGENTTEAMS[A-Z_-]*:END\s+(\S+)")


def _rendered_files() -> list[Path]:
    """Return every markdown artifact the repo's own teams have on disk.

    Backup snapshots under ``.agentteams-backups/`` are excluded, and the exclusion is a
    scoping decision rather than a convenience: a backup is a **record of what was there**,
    not a render. Holding a historical snapshot to today's invariant would fail forever on a
    file nobody can fix, which is how a check gets muted.

    That is not hypothetical here. Adding this check immediately failed on
    ``.claude/agents/.agentteams-backups/20260801-220336/technical-validator.md`` (1 BEGIN,
    2 END) while the live file at the same name is balanced 7/7. The backup is dated
    2026-08-01 — the same day the "nested fence" symptom was first reported — so it is
    **corroborating evidence that the truncation was real**, preserved by the backup system.
    Excluded from the assertion; recorded here because it is the only surviving artifact of
    the defect.
    """
    files: list[Path] = []
    for rel in RENDER_ROOTS:
        root = REPO_ROOT / rel
        if root.is_dir():
            files.extend(
                sorted(
                    p for p in root.rglob("*.md")
                    if BACKUP_DIR_NAME not in p.parts
                )
            )
    return files


@pytest.mark.skipif(
    not _rendered_files(),
    reason="no rendered agent trees (.github/agents, .claude/agents) in this checkout (public release / CI)",
)
def test_the_check_has_something_to_read() -> None:
    """Anti-vacuity. A parametrised test over an empty list passes and proves nothing.

    This is the failure the red-team audit's F-4 check calls a scope that resolves to zero:
    silent, green, and indistinguishable from working.
    """
    files = _rendered_files()
    assert len(files) >= 20, (
        f"only {len(files)} rendered agent files found under {RENDER_ROOTS} — the walk "
        f"regressed, and every assertion below would pass vacuously"
    )
    fenced = [p for p in files if _BEGIN_RE.search(p.read_text(encoding="utf-8"))]
    assert len(fenced) >= 10, (
        f"only {len(fenced)} rendered files carry any fence at all; the balance assertions "
        f"below would be measuring almost nothing"
    )


@pytest.mark.parametrize(
    "path", _rendered_files(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_rendered_fences_are_balanced(path: Path) -> None:
    """Every BEGIN pairs with its own END, in order, with no fence opening inside another.

    A truncated render loses an END. `_extract_fenced_regions` then returns an error string
    instead of a mapping, `emit.py` falls through to wrapping the entire body in the `content`
    fence, and the next merge reports a *nested fence* — three steps from the actual damage.
    Catching it at the source is the difference between "conflict-auditor.md is truncated after
    handoff_payload_conflict_codes" and "Nested fence not allowed".
    """
    text = path.read_text(encoding="utf-8")
    events = sorted(
        [(m.start(), "begin", m.group(1)) for m in _BEGIN_RE.finditer(text)]
        + [(m.start(), "end", m.group(1)) for m in _END_RE.finditer(text)]
    )
    rel = path.relative_to(REPO_ROOT)
    stack: list[str] = []
    for _pos, kind, section_id in events:
        if kind == "begin":
            assert not stack, (
                f"{rel}: fence {section_id!r} opens inside {stack[-1]!r}. In a RENDER this is "
                f"usually the downstream symptom of a truncation, not a nesting bug — check "
                f"whether an earlier fence lost its END."
            )
            stack.append(section_id)
        else:
            assert stack, f"{rel}: END {section_id!r} with no open fence"
            opened = stack.pop()
            assert opened == section_id, (
                f"{rel}: END {section_id!r} closes {opened!r} — markers crossed"
            )
    assert not stack, (
        f"{rel}: fence(s) opened and never closed: {stack}. This is the 2026-08-02 truncation "
        f"shape: the render stops mid-file and every fence after the cut is lost."
    )


def test_begin_and_end_counts_agree_across_the_whole_tree() -> None:
    """The blunt version of the check above, stated as the count the original finding used.

    Kept alongside the per-file test deliberately: the finding was *reported* as "8 BEGIN / 7
    END", and a check phrased the same way as the observation is the one a reader will
    recognise when it fires.
    """
    mismatched: dict[str, tuple[int, int]] = {}
    for path in _rendered_files():
        text = path.read_text(encoding="utf-8")
        begins = len(_BEGIN_RE.findall(text))
        ends = len(_END_RE.findall(text))
        if begins != ends:
            mismatched[str(path.relative_to(REPO_ROOT))] = (begins, ends)
    assert not mismatched, (
        "rendered file(s) with unequal BEGIN/END counts (file: begins, ends): "
        f"{mismatched}"
    )
