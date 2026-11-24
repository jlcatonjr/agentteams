"""#622: a template's retired source line reaches existing teams, and no template names a private checkout.

A universal reference template cited a private checkout's directory in its `infra_security_model` fence. Removing
the line from the template was not enough on its own: `_detect_fence_shrink` rule (c) read the removed backticked
path as "lost concrete refs", so `--shrink-policy=preserve` (the default) kept every existing team's old body.
The fence is now template-owned, and a template-owned fence's shrink notice gives a count instead of the lost
tokens, so the retired text is not copied into `update.report.md` either.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams import fences
from agentteams.fences import _detect_fence_shrink, _merge_fenced_content

_ROOT = Path(__file__).resolve().parents[1]
_SID = "infra_security_model"
_RETIRED = "`private-checkout/security-infrastructure/`"   # a stand-in: no real checkout is named in tests


def _doc(body: str) -> str:
    return f"# Layers\n\n<!-- AGENTTEAMS:BEGIN {_SID} v=1 -->\n{body}<!-- AGENTTEAMS:END {_SID} -->\n"


_OLD = _doc("| L0 | Governance |\n\nFull source grounding: the backing research at\n"
            f"{_RETIRED} (where present) and the standards named above.\n")
_NEW = _doc("| L0 | Governance |\n\nFull source grounding: the standards named above.\n")


def test_the_fence_is_template_owned():
    assert _SID in fences._TEMPLATE_AUTHORITATIVE_FENCES
    assert fences._is_template_authoritative(_SID, "references/security-infrastructure-layers.reference.md")


def test_a_retired_line_reaches_an_existing_team_under_the_default_preserve_policy():
    mr = _merge_fenced_content(_NEW, _OLD, preserve_on_shrink=True,
                               rel_path="references/security-infrastructure-layers.reference.md")
    assert _SID not in mr.sections_preserved
    assert _RETIRED not in mr.merged_content
    assert "Full source grounding: the standards named above." in mr.merged_content
    # The shrink is still reported, and the report does not repeat what was retired.
    assert any(n.startswith(f"fence '{_SID}': ") and "lost concrete refs: 1" in n for n in mr.shrink_notices)
    assert not any("private-checkout" in n for n in mr.shrink_notices)


def test_a_template_owned_notice_counts_lost_refs_instead_of_naming_them():
    notice = _detect_fence_shrink(_SID, _OLD, _NEW)
    assert notice is not None and "lost concrete refs: 1" in notice and "private-checkout" not in notice
    assert f".lost.{_SID}.md.agentteams-bak" in notice


def test_other_fences_still_name_lost_refs():
    old = _OLD.replace(_SID, "project_notes")
    new = _NEW.replace(_SID, "project_notes")
    notice = _detect_fence_shrink("project_notes", old, new)
    assert notice is not None and "private-checkout/security-infrastructure/" in notice


def _private_root_paths() -> list[str]:
    """Root directories `.gitignore` ignores under a comment that calls them private."""
    found: list[str] = []
    comment = ""
    for line in (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            comment += line.lower() + "\n"
            continue
        m = re.fullmatch(r"/([A-Za-z0-9._-]+)/", line.strip())
        if m and "private" in comment:
            found.append(m.group(1) + "/")
        if not line.strip():
            comment = ""
    return found


def test_the_gitignore_names_a_private_checkout_to_guard():
    # Guards the guard: without an entry, the scan below would pass vacuously.
    assert _private_root_paths()


def test_no_template_names_a_private_checkout():
    # A whole path segment only: `research/` must not match inside `docs/research/` or `x-research/`.
    patterns = [re.compile(r"(?<![A-Za-z0-9._/-])" + re.escape(name)) for name in _private_root_paths()]
    offenders = [
        f"{path.relative_to(_ROOT)}: {pat.pattern}"
        for path in sorted((_ROOT / "agentteams" / "templates").rglob("*"))
        if path.is_file()
        for pat in patterns
        if pat.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not offenders, offenders
