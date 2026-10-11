"""The fence-twin lineage corpus is deletion authority, so it must be exactly what git says.

``agentteams/data/template_lineage.json`` lets a merge delete an unfenced section when its text
equals one listed here (Rule 12: a PR that adds arbitrary text to it would authorise deleting that
text from every deployed repository). Three guards:

* **Regeneration equality.** ``scripts/build_template_lineage.py`` rebuilt from git must reproduce
  the shipped file byte for byte. It needs the archived pre-public history (``LINEAGE_TIPS``);
  where those commits are absent (a fresh clone of ``origin``, CI) the test skips, unless
  ``AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1`` makes their absence a failure.
* **Per-entry provenance.** Every entry whose commit is in this clone's ``HEAD`` history (public
  history) must be found, unfenced, under its heading in ``git show <commit>:<path>``.
* **Pinned pre-public set.** Entries citing a commit outside ``HEAD``'s history cannot be checked
  where that history is absent, so their exact set (template, heading, commit, path, text) is pinned
  by digest here. An entry with an invented SHA changes the digest and fails CI; changing the pin is
  a reviewed edit that must follow a local regeneration with the archived history present.
* **Shape.** Checked everywhere, history or not: every heading leads exactly one fence of its
  current template, every text starts with its heading and carries no AGENTTEAMS marker.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
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


#: sha256 of the canonical JSON of every corpus row whose commit is NOT in ``HEAD``'s history (the
#: archived pre-public lineage). 111 rows, verified by regeneration with the archive present on 2026-10-10.
PRE_PUBLIC_DIGEST = "02f346c447ac64a1d0c1fac6008bf96b7e7056c67218d70562eb086ca57dc83d"


def _public_commits() -> set[str]:
    return set(subprocess.run(["git", "-C", str(REPO), "rev-list", "HEAD"],
                              capture_output=True, text=True).stdout.split())


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


def test_every_public_entry_is_its_cited_unfenced_section():
    if not _has_git_history():
        pytest.skip("shallow clone: no commit objects to check provenance against")
    public = _public_commits()
    for template, headings in CORPUS.items():
        for heading, entries in headings.items():
            for entry in entries:
                if entry["commit"] not in public:
                    continue  # pre-public: pinned by digest below
                show = subprocess.run(["git", "-C", str(REPO), "show", f"{entry['commit']}:{entry['path']}"],
                                      capture_output=True, text=True)
                assert show.returncode == 0, f"{template} {heading}: {entry['commit']}:{entry['path']} unreadable"
                span = fence_twins.unfenced_section(show.stdout, heading, allow_eof=True)
                assert not isinstance(span, str), f"{template} {heading}@{entry['commit']}: {span}"
                assert show.stdout[span[0]:span[1]] == entry["text"], f"{template} {heading}@{entry['commit']}"


def test_pre_public_entries_match_the_pinned_set():
    """CI cannot read the archived history, so an entry citing it must be one regeneration produced."""
    if not _has_git_history():
        pytest.skip("shallow clone: cannot tell public from pre-public commits")
    public = _public_commits()
    rows = sorted([t, h, e["commit"], e["path"], e["text"]]
                  for t, hs in CORPUS.items() for h, es in hs.items() for e in es if e["commit"] not in public)
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert digest == PRE_PUBLIC_DIGEST, (
        "the corpus's pre-public entries changed. They are deletion authority CI cannot verify: regenerate "
        "with the archived history present (AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1), review, then update the pin")


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
