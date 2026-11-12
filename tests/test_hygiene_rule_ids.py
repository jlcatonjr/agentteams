"""Project hygiene rules live in their own CH-P namespace and survive --update --merge (IM item 26).

G-26-1  the catalogue told projects to "add CH-21+" inside the managed fence, where the framework's own
        CH-21…CH-31 already live: a project's rule collided by id, and the next merge dropped it silently.
G-26-2  nothing checked that rule ids are unique (the old test used a set) or that every catalogue rule is in
        the agent's summary table (only CH-26…CH-28 were checked).
G-26-3  --doctor did not report a repeated id or a project rule using a framework-range id.

The migration test starts from ``tests/fixtures/hygiene/dev-code-hygiene-rules.reference.md``: the software-project
golden as rendered by dev before this change (cc836468).
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams import doctor, emit, hygiene_rules

REPO = Path(__file__).resolve().parents[1]
_DOMAIN = REPO / "agentteams/templates/domain/code-hygiene-rules-reference.template.md"
_UNIVERSAL = REPO / "agentteams/templates/universal/code-hygiene.template.md"
_DEV_RENDER = REPO / "tests/fixtures/hygiene/dev-code-hygiene-rules.reference.md"
_NEW_RENDER = REPO / "examples/software-project/expected/references/code-hygiene-rules.reference.md"
_REL = "references/code-hygiene-rules.reference.md"

_LOCAL_29 = ("### CH-29 — Local: collector timeouts\n\n**Category:** Reliability\n**Severity:** High\n\n"
             "Every collector call sets an explicit timeout of at most 30 s.\n\n")
_LOCAL_NEW = ("### CH-40 — Local: retry budgets\n\n**Category:** Reliability\n**Severity:** Medium\n\n"
              "Retries are capped at three per run.\n\n")


def _plant(text: str, *blocks: str) -> str:
    """Insert ``blocks`` just before the managed fence's END marker (where 'projects add CH-21+' put them)."""
    end = text.index("<!-- AGENTTEAMS:END content -->")
    return text[:end] + "".join(blocks) + text[end:]


def _catalogue_ids(text: str) -> list[str]:
    return re.findall(r"^### (CH-\d\d) — ", hygiene_rules._COMMENT_RE.sub("", text), re.M)


# --------------------------------------------------------------------------- G-26-1


def test_merge_moves_planted_project_rules_out_of_the_fence_never_drops_them(tmp_path):
    """IM's acceptance: a dev-rendered reference with a local CH-29 and a new heading, merged to the new
    template, keeps both texts verbatim and says so."""
    out = tmp_path / "agents"
    (out / "references").mkdir(parents=True)
    target = out / _REL
    target.write_text(_plant(_DEV_RENDER.read_text(encoding="utf-8"), _LOCAL_29, _LOCAL_NEW), encoding="utf-8")
    res = emit.emit_all([(_REL, _NEW_RENDER.read_text(encoding="utf-8"))], output_dir=out, merge=True, yes=True)
    text = target.read_text(encoding="utf-8")
    for block in (_LOCAL_29, _LOCAL_NEW):
        assert block.strip() in text                                         # verbatim, not dropped
    fence_end = text.index("<!-- AGENTTEAMS:END content -->")
    assert text.index("Local: collector timeouts") > fence_end               # now outside the managed region
    assert text.index(hygiene_rules.PROJECT_SECTION_HEADING) < text.index("Local: collector timeouts")
    assert text.count(hygiene_rules.PROJECT_SECTION_HEADING) == 1
    assert "### CH-29 — Script-First Output Discipline" in text[:fence_end]   # the framework rule is intact
    notes = " ".join(res.notices)
    assert _REL in notes and "moved 2 project rule(s)" in notes and "CH-P01" in notes
    assert "CH-29 — Local: collector timeouts" in notes and "CH-40 — Local: retry budgets" in notes


def test_a_second_merge_moves_nothing_and_keeps_the_project_section(tmp_path):
    out = tmp_path / "agents"
    (out / "references").mkdir(parents=True)
    target = out / _REL
    target.write_text(_plant(_DEV_RENDER.read_text(encoding="utf-8"), _LOCAL_29), encoding="utf-8")
    new = _NEW_RENDER.read_text(encoding="utf-8")
    emit.emit_all([(_REL, new)], output_dir=out, merge=True, yes=True)
    first = target.read_text(encoding="utf-8")
    res = emit.emit_all([(_REL, new)], output_dir=out, merge=True, yes=True)
    assert target.read_text(encoding="utf-8") == first
    assert not any("moved" in n for n in res.notices)


def test_merge_keeps_a_project_rule_when_the_framework_adds_a_rule(tmp_path):
    """A project rule in the user-owned section survives an update that adds a framework rule to the fence."""
    out = tmp_path / "agents"
    (out / "references").mkdir(parents=True)
    target = out / _REL
    new = _NEW_RENDER.read_text(encoding="utf-8")
    mine = "### CH-P01 — Local: collector timeouts\n\nEvery collector call sets a timeout.\n"
    target.write_text(new.rstrip("\n") + "\n\n" + mine, encoding="utf-8")
    added = _plant(new, "### CH-32 — A Later Framework Rule\n\n**Category:** Test\n\nBody.\n\n")
    res = emit.emit_all([(_REL, added)], output_dir=out, merge=True, yes=True)
    text = target.read_text(encoding="utf-8")
    assert mine.strip() in text and "### CH-32 — A Later Framework Rule" in text
    assert not any("moved" in n for n in res.notices)
    assert hygiene_rules.duplicate_rule_ids(text) == []


def test_the_templates_send_project_rules_to_the_ch_p_namespace():
    domain = _DOMAIN.read_text(encoding="utf-8")
    universal = _UNIVERSAL.read_text(encoding="utf-8")
    assert "## Project Extension Rules (CH-P01+)" in domain
    assert domain.index("<!-- AGENTTEAMS:END content -->") < domain.index("## Project Extension Rules (CH-P01+)")
    for text in (domain, universal):
        assert "projects add CH-21+" not in text and "### CH-21 — Project-Specific" not in text
    assert "`CH-P01`" in universal


# --------------------------------------------------------------------------- G-26-2


def test_catalogue_rule_ids_are_unique():
    """A list, not a set: a repeated heading is a failure."""
    ids = _catalogue_ids(_DOMAIN.read_text(encoding="utf-8"))
    assert ids and len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    assert hygiene_rules.duplicate_rule_ids(_DOMAIN.read_text(encoding="utf-8")) == []


def test_every_catalogue_rule_is_in_the_agent_summary_table():
    catalogue = set(_catalogue_ids(_DOMAIN.read_text(encoding="utf-8")))
    table = set(re.findall(r"^\| (CH-\d\d) \|", _UNIVERSAL.read_text(encoding="utf-8"), re.M))
    assert not catalogue - table, f"in the catalogue, missing from the agent table: {sorted(catalogue - table)}"
    assert not table - catalogue, f"in the agent table, missing from the catalogue: {sorted(table - catalogue)}"


def test_framework_ids_are_stable():
    """Renumbering a framework rule would break every citation of it (IM condition 2)."""
    titles = dict(re.findall(r"^### (CH-\d\d) — (.+)$", hygiene_rules._COMMENT_RE.sub("", _DOMAIN.read_text()), re.M))
    assert titles["CH-21"] == "Validate New Features Before Mainline Integration"
    assert titles["CH-29"] == "Script-First Output Discipline: Build and Reuse Reference Files"
    assert titles["CH-30"] == "No Exemption Without Provenance"
    assert titles["CH-31"] == "When to Convert an `if`/`elif` Chain to a Dispatch Table"


# --------------------------------------------------------------------------- G-26-3


def _team(root: Path, text: str) -> Path:
    ref = root / ".claude" / "agents" / "references" / hygiene_rules.HYGIENE_REF_NAME
    ref.parent.mkdir(parents=True)
    ref.write_text(text, encoding="utf-8")
    return ref


def test_doctor_names_the_file_and_both_lines_of_a_duplicate(tmp_path, capsys):
    text = _NEW_RENDER.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + _LOCAL_29
    _team(tmp_path, text)
    first = text.splitlines().index("### CH-29 — Script-First Output Discipline: Build and Reuse Reference Files") + 1
    last = text.splitlines().index("### CH-29 — Local: collector timeouts") + 1
    doctor.run_doctor(str(tmp_path))
    out = capsys.readouterr().out
    assert "Hygiene rule ids" in out
    assert f".claude/agents/references/{hygiene_rules.HYGIENE_REF_NAME}: CH-29 appears twice (lines {first} and {last})" in out
    assert f"{hygiene_rules.HYGIENE_REF_NAME}:{last}: project rule uses the framework id CH-29" in out


def test_doctor_is_quiet_on_a_clean_render_and_the_comment_example(tmp_path, capsys):
    _team(tmp_path, _NEW_RENDER.read_text(encoding="utf-8"))
    doctor.run_doctor(str(tmp_path))
    out = capsys.readouterr().out
    assert hygiene_rules.HYGIENE_REF_NAME not in out and "appears twice" not in out


def test_a_ch_p_rule_is_not_a_framework_id():
    text = _NEW_RENDER.read_text(encoding="utf-8") + "\n### CH-P01 — Mine\n"
    assert hygiene_rules.framework_ids_outside_fence(text) == []
