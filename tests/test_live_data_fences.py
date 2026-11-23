"""The live-data fence set (``fences._LIVE_DATA_FENCES``) and the framework-watch snapshot (R29).

``framework_data`` in framework-watch.reference.md is re-rendered from the framework-research snapshot on every
``--update --merge``. Before 1.3.0 it was not a live-data fence, so the shrink guard's rule (c) read each refresh's new
date and diff tokens as lost concrete references, and ``--shrink-policy preserve`` kept a months-old body (field
evidence: a 2026-06-23 snapshot with a frozen "108.9 days old" banner, kept in 7+ downstream repositories).

Guards (``@security`` design ruling, 2026-11-22):

* the set equals the template manifests' "Live" rows, and each live id is a fence in exactly one template;
* no live id is also template-authoritative or brief-derived (their exemptions differ on purpose);
* every line of the ``framework_data`` template body is placeholder-filled, so nothing a project writes can live there;
* a snapshot in which no framework doc was fetched is labelled unverified, however fresh its date.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams import emit, fences, framework_research

TEMPLATES = Path(fences.__file__).resolve().parent / "templates"
_BEGIN_RE = re.compile(r"<!--\s*AGENTTEAMS:BEGIN\s+([A-Za-z0-9_]+)\s+v=\d+\s*-->")
_ROW_RE = re.compile(r"^\|\s*([a-z0-9_]+)\s*\|\s*FENCED\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)

# The framework_data body found in a downstream repository (relayed 2026-11-22, data only).
FIELD_BODY = """> ⚠️ **STALE DATA** — snapshot is 108.9 days old (threshold 7 days). Run the daily research stage online.

Generated on: `2026-06-23`
Source: https://docs.anthropic.com/en/docs/claude-code/sub-agents
Fetch status: `ok`
Diff summary: `matched=1 new_upstream=2 missing_upstream=1`

| Framework | Fetch | Tokens observed | Locations observed | Drift (missing keys ↓) |
|---|---|---|---|---|
| claude (Claude Code Sub-Agents) | `ok` | model, name, tools | .claude/agents, CLAUDE.md | — |
| copilot_vscode (GitHub Copilot — VS Code Chat Modes) | `ok` | — | .github/agents | — |
| copilot_cli (GitHub Copilot — CLI) | `ok` | — | — | — |

Local Claude adapter constants (parsed from claude.py source):
- strictly-required keys (_CLAUDE_REQUIRED_KEYS): description, name
- default_allowed_tools: Bash, Read, Write, Edit
- claude diff (vs expected_doc_tokens) — matched: name; new_upstream: model, tools; missing_upstream: description
"""

FRESH_BODY = """> ⚠️ **UNVERIFIED DATA** — no framework doc was fetched for this snapshot (every fetch was skipped or failed). Run the daily research stage online.

Generated on: `2026-11-22`
Source: https://docs.anthropic.com/en/docs/claude-code/sub-agents
Fetch status: `skipped`
Diff summary: `matched=0 new_upstream=0 missing_upstream=0`

| Framework | Fetch | Tokens observed | Locations observed | Drift (missing keys ↓) |
|---|---|---|---|---|
| claude (Claude Code Sub-Agents) | `skipped` | — | — | — |
"""


def _fenced(sid: str, body: str) -> str:
    return f"# Framework Watch\n\nProse.\n\n<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}<!-- AGENTTEAMS:END {sid} -->\n\nTail.\n"


def _templates() -> list[Path]:
    return sorted(p for p in TEMPLATES.rglob("*.md") if p.is_file())


def _live_manifest_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for path in _templates():
        for sid, notes in _ROW_RE.findall(path.read_text(encoding="utf-8")):
            if re.match(r"live\b", notes.strip(), re.IGNORECASE):
                rows.setdefault(sid, []).append(path.name)
    return rows


def test_the_live_set_equals_the_manifests_live_rows():
    rows = _live_manifest_rows()
    assert set(rows) == set(fences._LIVE_DATA_FENCES), (
        f"manifest rows marked Live {sorted(rows)} differ from _LIVE_DATA_FENCES {sorted(fences._LIVE_DATA_FENCES)}")
    assert "framework_data" in fences._LIVE_DATA_FENCES


def test_each_live_id_is_a_fence_in_exactly_one_template():
    for sid in fences._LIVE_DATA_FENCES:
        owners = [p.name for p in _templates() if sid in _BEGIN_RE.findall(p.read_text(encoding="utf-8"))]
        assert len(owners) == 1, f"{sid} is a fence in {owners}; a live id must belong to one template"


def test_live_ids_are_neither_template_authoritative_nor_brief_derived():
    assert not fences._LIVE_DATA_FENCES & fences._TEMPLATE_AUTHORITATIVE_FENCES
    assert not fences._LIVE_DATA_FENCES & fences._BRIEF_DERIVED_FENCES


def test_every_framework_data_template_line_is_placeholder_filled():
    text = (TEMPLATES / "universal" / "framework-watch.reference.template.md").read_text(encoding="utf-8")
    body = re.search(r"AGENTTEAMS:BEGIN framework_data v=\d+ -->\n(.*?)<!-- AGENTTEAMS:END framework_data",
                     text, re.DOTALL)
    assert body, "the framework_data fence is missing from its template"
    lines = [line for line in body.group(1).splitlines() if line.strip()]
    assert lines and all(re.search(r"\{FRAMEWORK_RESEARCH_[A-Z_]+\}", line) for line in lines), (
        "every non-blank framework_data line must carry a FRAMEWORK_RESEARCH_* placeholder; fixed prose belongs "
        "outside the live fence, where the shrink guard still protects it")


def test_the_field_body_is_no_longer_read_as_a_shrink():
    existing, new = _fenced("framework_data", FIELD_BODY), _fenced("framework_data", FRESH_BODY)
    assert fences._detect_fence_shrink("framework_data", existing, new) is None
    # The same loss in an ordinary fence is still a shrink: rule (c) is unchanged elsewhere.
    assert fences._detect_fence_shrink("demo", _fenced("demo", FIELD_BODY), _fenced("demo", FRESH_BODY))


def test_a_preserve_merge_replaces_a_stale_framework_data_body(tmp_path):
    target = tmp_path / "framework-watch.reference.md"
    target.write_text(_fenced("framework_data", FIELD_BODY), encoding="utf-8")
    result = emit.emit_all([("framework-watch.reference.md", _fenced("framework_data", FRESH_BODY))],
                           output_dir=tmp_path, merge=True, yes=True, shrink_policy="preserve")
    text = target.read_text(encoding="utf-8")
    assert "Generated on: `2026-11-22`" in text and "2026-06-23" not in text
    assert "108.9 days" not in text
    assert not any("framework_data" in n for n in result.notices)
    assert not result.shrink_blocked
    assert not list(tmp_path.rglob("*.lost.*"))


def test_an_unfetched_snapshot_is_labelled_unverified_however_fresh():
    now = framework_research._utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    skipped = {"generated_at": now, "fetch_status": "skipped",
               "frameworks": {"claude": {"fetch_status": "skipped"}, "codex": {"fetch_status": "failed"}}}
    assert "UNVERIFIED DATA" in framework_research._staleness_banner(skipped)
    one_ok = {**skipped, "frameworks": {"claude": {"fetch_status": "ok"}, "codex": {"fetch_status": "failed"}}}
    assert framework_research._staleness_banner(one_ok) == ""
    legacy = {"generated_at": now, "fetch_status": "failed"}   # schema before 1.2: no per-framework map
    assert "UNVERIFIED DATA" in framework_research._staleness_banner(legacy)


def test_a_stale_snapshot_still_says_stale_first():
    old = {"generated_at": "2026-01-01T00:00:00Z", "fetch_status": "skipped",
           "frameworks": {"claude": {"fetch_status": "skipped"}}}
    assert "STALE DATA" in framework_research._staleness_banner(old)
