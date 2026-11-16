"""Placeholder completeness is a test (IM item 13b, G-17-3).

``SETUP-REQUIRED.md`` was built only from the brief's ``manual_required_placeholders``. A ``{MANUAL:X}`` written in a
template never reached it: every example emitted ``PROJECT_REDTEAM_NOTES``, ``ADJACENT_REPO_PATH`` and
``ADJACENT_REPO_AGENTS_PATH`` unlisted, and the audit and enrich scans skipped the last two because they sit in inline
code. The checklist is now built from the emitted text, with one rule shared by every scanner: only the
``INSTRUCTIONAL_TOKENS`` are skipped, and this file pins where those may appear.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import analyze, placeholder_scan, tool_metadata_catalog
from agentteams.cli.render_pipeline import _build_final_rendered
from agentteams.frameworks.registry import FRAMEWORKS

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "agentteams" / "templates"
EXAMPLES = sorted((REPO / "examples").glob("*/brief.json"))
#: Authoring guides at the templates root: documentation, never rendered into a team.
GUIDES = ("AUTHORING-GUIDE.md", "FENCE-CONVENTIONS.md", "PLACEHOLDER-CONVENTIONS.md")
#: The only files where an instructional name may be written (IM-5c: pin it, so a new instructional example, or a
#: real placeholder named like one, fails here instead of silently joining or leaving the checklist).
INSTRUCTIONAL_HOMES = {
    "TOKEN": {"domain/content-enricher.template.md"},
    "UPPER_SNAKE_CASE": {"AUTHORING-GUIDE.md"},
    "PLACEHOLDER_NAME": {"PLACEHOLDER-CONVENTIONS.md"},
}
#: Python files that write an instructional name as ``{MANUAL:X}`` (all in docstrings or comments, explaining the syntax).
CODE_HOMES = {
    "TOKEN": {"enrich/_audit.py", "enrich/_enrich.py", "enrich/_models.py"},
    "PLACEHOLDER_NAME": {"render.py"},
}
#: Templates that write an instructional name as a plain ``{X}`` (explaining auto placeholders, never a slot).
PLAIN_HOMES = {
    "UPPER_SNAKE_CASE": {"AUTHORING-GUIDE.md", "domain/module-doc-author.template.md",
                         "domain/module-doc-validator.template.md"},
    "PLACEHOLDER_NAME": {"PLACEHOLDER-CONVENTIONS.md"},
}
HIDDEN_BEFORE = {"ADJACENT_REPO_PATH", "ADJACENT_REPO_AGENTS_PATH", "PROJECT_REDTEAM_NOTES"}

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")  # Copilot body-length warnings


def _names_by_file() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in TEMPLATES.rglob("*.md"):
        for name in placeholder_scan.MANUAL_TOKEN_RE.findall(path.read_text(encoding="utf-8")):
            out.setdefault(name, set()).add(path.relative_to(TEMPLATES).as_posix())
    return out


def _render(brief: Path, framework: str) -> tuple[dict, dict[str, str]]:
    manifest = analyze.build_manifest(json.loads(brief.read_text(encoding="utf-8")), framework=framework)
    return manifest, dict(_build_final_rendered(manifest, FRAMEWORKS[framework](), "x"))


def _setup(files: dict[str, str]) -> str:
    return next(body for path, body in files.items() if Path(path).name == "SETUP-REQUIRED.md")


# --- the instructional names are pinned ------------------------------------------------------------------------

def test_the_instructional_names_are_one_constant():
    assert placeholder_scan.INSTRUCTIONAL_TOKENS == frozenset(INSTRUCTIONAL_HOMES)


def test_an_instructional_name_is_written_only_where_it_is_instructional_today():
    names = _names_by_file()
    for name, homes in INSTRUCTIONAL_HOMES.items():
        assert names.get(name, set()) == homes, f"{{MANUAL:{name}}} moved: decide whether it is a real placeholder"


def test_code_writes_an_instructional_name_only_where_it_explains_the_syntax():
    pattern = re.compile(r"\{MANUAL:(" + "|".join(sorted(INSTRUCTIONAL_HOMES)) + r")\}")
    found: dict[str, set[str]] = {}
    for path in (REPO / "agentteams").rglob("*.py"):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(path.relative_to(REPO / "agentteams").as_posix())
    assert found == CODE_HOMES


def test_a_template_writes_an_instructional_name_as_a_plain_placeholder_only_in_its_homes():
    pattern = re.compile(r"(?<!MANUAL:)\{(" + "|".join(sorted(INSTRUCTIONAL_HOMES)) + r")\}")
    found: dict[str, set[str]] = {}
    for path in TEMPLATES.rglob("*.md"):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(path.relative_to(TEMPLATES).as_posix())
    assert found == PLAIN_HOMES


def test_no_code_emits_an_instructional_name_as_a_value():
    literal = re.compile(r"[\"']\{MANUAL:(" + "|".join(sorted(INSTRUCTIONAL_HOMES)) + r")\}[\"']")
    hits = [str(p.relative_to(REPO)) for p in (REPO / "agentteams").rglob("*.py")
            if literal.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_every_name_a_rendered_template_writes_is_documented_or_instructional():
    # A new {MANUAL:X} in prose fails here until an author lists it in PLACEHOLDER-CONVENTIONS or marks it instructional.
    documented = set(placeholder_scan.MANUAL_TOKEN_RE.findall(
        (TEMPLATES / "PLACEHOLDER-CONVENTIONS.md").read_text(encoding="utf-8")))
    rendered = {name for name, files in _names_by_file().items() if files - set(GUIDES)}
    assert rendered - documented - placeholder_scan.INSTRUCTIONAL_TOKENS == set()


# --- the checklist is complete ------------------------------------------------------------------------------------

@pytest.mark.parametrize("framework", sorted(FRAMEWORKS))
@pytest.mark.parametrize("brief", EXAMPLES, ids=lambda p: p.parent.name)
def test_every_emitted_manual_token_is_on_the_checklist(brief, framework):
    _, files = _render(brief, framework)
    emitted = {item["placeholder"] for item in placeholder_scan.scan_manual_tokens(files.items())}
    listed = set(placeholder_scan.MANUAL_TOKEN_RE.findall(_setup(files)))
    assert emitted <= listed
    assert HIDDEN_BEFORE <= listed                            # IM-5c: the template-literal and backticked cases
    assert "TOKEN" not in listed                              # instructional prose is not a checklist item


def test_the_checklist_is_deterministic_and_idempotent():
    manifest, first = _render(EXAMPLES[0], "copilot-vscode")
    _, second = _render(EXAMPLES[0], "copilot-vscode")
    assert _setup(first) == _setup(second)
    scanned = list(manifest[placeholder_scan.SCANNED_KEY])
    assert placeholder_scan.complete_setup_required(manifest, list(first.items())) == list(first.items())
    assert manifest[placeholder_scan.SCANNED_KEY] == scanned


@pytest.mark.parametrize("brief", EXAMPLES, ids=lambda p: p.parent.name)
def test_render_leaves_the_drift_fingerprint_unchanged(brief):
    # Adversarial 1: --check fingerprints the manifest before render, generate and --update after it.
    from agentteams import drift

    manifest = analyze.build_manifest(json.loads(brief.read_text(encoding="utf-8")), framework="copilot-vscode")
    listed = json.dumps(manifest.get("manual_required_placeholders", []), sort_keys=True)
    before = drift.compute_manifest_fingerprint(manifest)
    _build_final_rendered(manifest, FRAMEWORKS["copilot-vscode"](), "x")
    assert drift.compute_manifest_fingerprint(manifest) == before
    assert json.dumps(manifest.get("manual_required_placeholders", []), sort_keys=True) == listed
    assert manifest[placeholder_scan.SCANNED_KEY]


def test_the_emit_summary_counts_the_scanned_placeholders(capsys):
    from agentteams import emit

    manifest, files = _render(EXAMPLES[0], "copilot-vscode")
    expected = len(manifest.get("manual_required_placeholders", [])) + len(manifest[placeholder_scan.SCANNED_KEY])
    assert placeholder_scan.open_count(manifest) == expected
    emit.print_summary(emit.EmitResult(), manifest)
    assert f"{expected} placeholder(s) require manual completion" in capsys.readouterr().out


def test_the_count_does_not_repeat_a_name_enrich_relisted():
    # --enrich replaces manual_required_placeholders with its findings after the scan (generate.py), names included.
    manifest = {"manual_required_placeholders": [{"placeholder": "A"}, {"placeholder": "B"}],
                placeholder_scan.SCANNED_KEY: [{"placeholder": "B"}, {"placeholder": "C"}]}
    assert placeholder_scan.open_count(manifest) == 3


def test_only_update_carries_filled_values_into_the_checklist(tmp_path):
    # generate --merge / --overwrite writes the raw render, so a filled token on disk is open again and stays listed.
    manifest, files = _render(EXAMPLES[0], "copilot-vscode")
    path = next(p for p, body in files.items() if "{MANUAL:ADJACENT_REPO_PATH}" in body)
    _write_on_disk(tmp_path, path, files[path].replace("{MANUAL:ADJACENT_REPO_PATH}", "/repos/neighbour"))
    names = lambda m: {item["placeholder"] for item in m[placeholder_scan.SCANNED_KEY]}  # noqa: E731
    adapter = FRAMEWORKS["copilot-vscode"]()
    _build_final_rendered(manifest, adapter, "x", output_dir=tmp_path)
    assert "ADJACENT_REPO_PATH" in names(manifest)
    _build_final_rendered(manifest, adapter, "x", output_dir=tmp_path, filled_from=tmp_path)
    assert "ADJACENT_REPO_PATH" not in names(manifest)


def _write_on_disk(out: Path, path: str, body: str) -> None:
    from agentteams import emit

    target = emit._resolve_path(out, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def test_a_token_the_operator_filled_leaves_the_checklist(tmp_path):
    # Adversarial 2: --update carries the filled value forward, so the token is no longer open.
    from agentteams.cli.render_pipeline import _with_filled_values

    manifest, files = _render(EXAMPLES[0], "copilot-vscode")
    path = next(p for p, body in files.items() if "{MANUAL:ADJACENT_REPO_PATH}" in body)
    _write_on_disk(tmp_path, path, files[path].replace("{MANUAL:ADJACENT_REPO_PATH}", "/repos/neighbour"))
    out = dict(placeholder_scan.complete_setup_required(manifest, list(files.items()),
                                                        effective=_with_filled_values(tmp_path)))
    names = {item["placeholder"] for item in manifest[placeholder_scan.SCANNED_KEY]}
    assert "ADJACENT_REPO_PATH" not in names and "{MANUAL:ADJACENT_REPO_PATH}" not in _setup(out)
    assert "ADJACENT_REPO_AGENTS_PATH" in names                  # the one left open stays listed


def test_a_token_new_in_the_template_stays_listed_on_update(tmp_path):
    # A file on disk without the token is not "filled" when the render adds the token for the first time.
    from agentteams.cli.render_pipeline import _with_filled_values

    manifest, files = _render(EXAMPLES[0], "copilot-vscode")
    path = next(p for p, body in files.items() if "{MANUAL:ADJACENT_REPO_PATH}" in body)
    _write_on_disk(tmp_path, path, "# an older version of this file\n")
    placeholder_scan.complete_setup_required(manifest, list(files.items()), effective=_with_filled_values(tmp_path))
    assert "ADJACENT_REPO_PATH" in {item["placeholder"] for item in manifest[placeholder_scan.SCANNED_KEY]}


def test_a_non_string_content_is_a_type_error():
    with pytest.raises(TypeError, match="a.md"):
        placeholder_scan.scan_manual_tokens([("a.md", b"{MANUAL:X}")])


def test_scan_results_are_sorted_and_carry_line_and_context():
    found = placeholder_scan.scan_manual_tokens([
        ("b.md", "x {MANUAL:ZED}\n`{MANUAL:ALPHA}` and {MANUAL:ALPHA}"),
        ("a.md", "{MANUAL:ZED} {MANUAL:TOKEN}"),
        ("SETUP-REQUIRED.md", "{MANUAL:NOPE}"),
    ])
    assert [(f["placeholder"], f["agent_file"], f["line"]) for f in found] == [
        ("ALPHA", "b.md", 2), ("ZED", "a.md", 1), ("ZED", "b.md", 1)]
    assert found[0]["context"] == "`{MANUAL:ALPHA}` and {MANUAL:ALPHA}"


# --- every scanner uses the same rule --------------------------------------------------------------------------

_FILES = {
    "references/adjacent-repos.md": "| **Local path** | `{MANUAL:ADJACENT_REPO_PATH}` |\n",
    "content-enricher.agent.md": "locate the `{MANUAL:TOKEN}` placeholder\n",
}


def test_the_post_audit_flags_a_backticked_placeholder_and_not_an_instructional_one():
    from agentteams import audit

    findings = audit._check_unresolved_manual_placeholders(_FILES)
    assert [(f.file, f.description) for f in findings] == [
        ("references/adjacent-repos.md", "Unresolved manual placeholder: {MANUAL:ADJACENT_REPO_PATH}")]


def test_enrich_finds_a_backticked_placeholder_and_not_an_instructional_one():
    from agentteams.enrich import scan_defaults

    tokens = {(f.file, f.token) for f in scan_defaults(_FILES, {"components": []}) if f.token.isupper()}
    assert ("references/adjacent-repos.md", "ADJACENT_REPO_PATH") in tokens
    assert all(token != "TOKEN" for _, token in tokens)


# --- well-known tools carry their official docs URL -----------------------------------------------------------

@pytest.mark.parametrize("name, url", [
    ("git", "https://git-scm.com/docs"), ("gh", "https://cli.github.com/manual/"),
    ("GitHub CLI", "https://cli.github.com/manual/"), ("PostgreSQL", "https://www.postgresql.org/docs/current/"),
    ("Python", "https://docs.python.org/3/"), ("Docker", "https://docs.docker.com/reference/"),
    ("Terraform", "https://developer.hashicorp.com/terraform/docs"),
    ("make", "https://www.gnu.org/software/make/manual/")])
def test_a_well_known_tool_s_reference_doc_has_its_docs_url(name, url):
    from agentteams import analyze_tools

    assert analyze_tools._merge_known_tool_metadata({"name": name})["docs_url"] == url
    assert analyze_tools._merge_known_tool_metadata({"name": name, "docs_url": "https://mine"})["docs_url"] == \
        "https://mine"                                        # the brief's own value still wins


def test_a_well_known_cli_is_not_matched_by_python_import_name():
    # enrich looks the catalog up by import name: `import git` is GitPython, `import docker` the Docker SDK.
    assert tool_metadata_catalog.get_tool_metadata("git") == {}
    assert not tool_metadata_catalog.is_known_tool("docker")


def test_a_brief_naming_git_and_postgresql_ships_no_manual_docs_url():
    brief = json.loads(EXAMPLES[0].read_text(encoding="utf-8"))
    brief["tools"] = [{"name": "git", "category": "cli"}, {"name": "PostgreSQL", "category": "database"}]
    manifest = analyze.build_manifest(brief, framework="copilot-vscode")
    files = dict(_build_final_rendered(manifest, FRAMEWORKS["copilot-vscode"](), "x"))
    tool_docs = {p: b for p, b in files.items() if "git" in p.lower() or "postgresql" in p.lower()}
    assert tool_docs and not any("{MANUAL:TOOL_DOCS_URL}" in body for body in tool_docs.values())
