"""The fence-twin lineage corpus is deletion authority, so it must be exactly what git says.

``agentteams/data/template_lineage.json`` lets a merge delete an unfenced section when its text
equals one listed here (Rule 12: a PR that adds arbitrary text to it would authorise deleting that
text from every deployed repository). Three guards:

* **Regeneration equality.** ``scripts/build_template_lineage.py`` rebuilt from git must reproduce
  the shipped file byte for byte. It needs the archived pre-public history (``LINEAGE_TIPS``);
  where those commits are absent (a fresh clone of ``origin``, CI) the test skips, unless
  ``AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1`` makes their absence a failure.
* **Per-entry provenance.** Every entry whose commit is present must be found, unfenced, under its
  heading in ``git show <commit>:<path>`` — a weaker check that still runs on public history.
* **Shape.** Checked everywhere, history or not: every heading leads exactly one fence of its
  current template, every text starts with its heading and carries no AGENTTEAMS marker.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from agentteams import fence_twins

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_template_lineage", REPO / "scripts" / "build_template_lineage.py")
lineage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lineage)

CORPUS = fence_twins.load_corpus(fence_twins.CORPUS_PATH)


def _has_git_history() -> bool:
    proc = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--is-shallow-repository"],
                          capture_output=True, text=True)
    return proc.returncode == 0 and proc.stdout.strip() == "false"


def _require_history() -> bool:
    return os.environ.get("AGENTTEAMS_REQUIRE_LINEAGE_HISTORY") == "1"


def test_corpus_regenerates_byte_for_byte():
    missing = lineage.missing_tips() if _has_git_history() else list(lineage.LINEAGE_TIPS)
    if missing:
        if _require_history():
            pytest.fail(f"lineage history required but absent: {missing}")
        pytest.skip(f"archived pre-public lineage history not in this clone ({len(missing)} tip(s))")
    assert lineage.serialise(lineage.build()) == fence_twins.CORPUS_PATH.read_text(encoding="utf-8"), (
        "agentteams/data/template_lineage.json is stale or hand-edited: run "
        "scripts/build_template_lineage.py, review the diff, and re-pin the integrity manifest")


def test_every_entry_present_in_git_is_its_cited_unfenced_section():
    if not _has_git_history():
        pytest.skip("shallow clone: no commit objects to check provenance against")
    checked = 0
    for template, headings in CORPUS.items():
        for heading, entries in headings.items():
            for entry in entries:
                show = subprocess.run(["git", "-C", str(REPO), "show", f"{entry['commit']}:{entry['path']}"],
                                      capture_output=True, text=True)
                if show.returncode != 0:
                    if _require_history():
                        pytest.fail(f"{template} {heading}: {entry['commit']}:{entry['path']} not in this clone")
                    continue
                span = fence_twins.unfenced_section(show.stdout, heading, allow_eof=True)
                assert not isinstance(span, str), f"{template} {heading}@{entry['commit']}: {span}"
                assert show.stdout[span[0]:span[1]] == entry["text"], f"{template} {heading}@{entry['commit']}"
                checked += 1
    if checked == 0 and not lineage.missing_tips():
        pytest.fail("no corpus entry could be checked against git although the history is present")


def test_corpus_shape():
    assert CORPUS, "the shipped corpus is empty or unreadable — every twin would be kept"
    templates = REPO / "agentteams" / "templates"
    for template, headings in CORPUS.items():
        leads = fence_twins.fence_leading_headings((templates / template).read_text(encoding="utf-8"))
        for heading, entries in headings.items():
            assert len(leads.get(heading, [])) == 1, f"{template}: {heading!r} must lead exactly one fence"
            for entry in entries:
                assert entry["text"].startswith(heading), f"{template} {heading}: text must start with its heading"
                assert not fence_twins._MARKER_RE.search(entry["text"]), f"{template} {heading}: a marker in authority text"
                assert entry["path"].endswith(template), f"{template}: entry cites {entry['path']}"


def test_corpus_never_authorises_researchteams_hand_written_rows():
    """The motivating fixture: CH-08..10 were written by hand in researchteam, never by a template."""
    rules = CORPUS.get("domain/technical-validator.template.md", {}).get("## Accuracy Rules", [])
    assert rules, "the pre-fencing CH-01..07 table should be in the lineage"
    assert not any("CH-08" in entry["text"] for entry in rules)
