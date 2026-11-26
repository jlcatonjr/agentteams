"""``--reconcile-apply`` widens a capability grant only under a ``reconcile-apply-widen`` clearance (C-3).

A narrowing and a non-capability key apply as before. A widening needs, in a governed workspace, a signed decision
(never a waiver) whose ``scope`` is the action and whose ``effect_grants`` are exactly this run's
``frontmatter:<file>#<key>@sha256:<digest of the value>`` tokens; in an ungoverned one, an ordinary PASS row from an approved author.
"""

from __future__ import annotations

import csv
import re
import types
from pathlib import Path

import pytest

from agentteams import front_matter_reconcile as fmr
from agentteams import front_matter_widening as fmw
from agentteams.cli import reconcile_apply_gate as gate
from agentteams.cli import security_gate
from agentteams.front_matter_reconcile import Divergence
from tests._sandbox_gate_helpers import _log, _retraction, _row, _team

_A = gate.ACTION
_ORCH_DISK = "---\nname: o\ndescription: old\ntools: Read\nagents:\n  - a\n---\nbody\n"
_ORCH_TMPL = "---\nname: o\ndescription: old\ntools: Read\nagents:\n  - a\n  - b\n---\nbody\n"
_ORCH_TOKEN = gate.grant_token("orchestrator.md", "agents", fmw.value_digest("agents", "\n  - a\n  - b"))


def _run(team: Path, rendered: list[tuple[str, str]], *, dry_run: bool = False) -> int:
    args = types.SimpleNamespace(reconcile_apply=True, dry_run=dry_run)
    return fmr.run_reconcile(args, rendered, team)


def _deploy(team: Path, name: str, text: str) -> Path:
    path = team / name
    path.write_text(text, encoding="utf-8")
    return path


def _consumed(team: Path) -> list[str]:
    with (team / "references" / "security-decisions.log.csv").open(newline="") as fh:
        return [r["action_reviewed"] for r in csv.DictReader(fh) if (r.get("consumed") or "") == "yes"]


def _widening_team(tmp_path: Path, *, governed: bool | None = True) -> tuple[Path, Path, list[tuple[str, str]]]:
    team = _team(tmp_path, governed=governed)
    orch = _deploy(team, "orchestrator.md", _ORCH_DISK)
    return team, orch, [("orchestrator.md", _ORCH_TMPL)]


def test_narrowing_and_non_capability_apply_without_clearance(tmp_path, capsys):
    team = _team(tmp_path, governed=None)
    narrow = _deploy(team, "a.md", "---\nname: a\ndescription: old\ntools: Read, Bash\n---\nbody\n")
    wide = _deploy(team, "b.md", "---\nname: b\ntools: Read\n---\nbody\n")
    wide_before = wide.read_bytes()
    rendered = [("a.md", "---\nname: a\ndescription: new\ntools: Read\n---\nbody\n"),
                ("b.md", "---\nname: b\ntools: Read, Bash\n---\nbody\n")]
    assert _run(team, rendered) == 1
    assert narrow.read_text(encoding="utf-8") == "---\nname: a\ndescription: new\ntools: Read\n---\nbody\n"
    # Negative control: the superset in the same run is refused and its file is byte-identical.
    assert wide.read_bytes() == wide_before
    assert "REFUSED b.md: tools" in capsys.readouterr().out
    assert _run(team, rendered[:1]) == 0  # only safe divergences left: exit 0


def test_orchestrator_agents_widening_refused_without_decision(tmp_path, capsys):
    team, orch, rendered = _widening_team(tmp_path)
    assert _run(team, rendered) == 1
    assert orch.read_text(encoding="utf-8") == _ORCH_DISK
    out = capsys.readouterr().out
    assert "frontmatter:orchestrator.md#agents" in out and "effect_grants=frontmatter:orchestrator.md#agents" in out


def test_signed_exact_grant_clears_widening(tmp_path, stub_ed25519):
    team, orch, rendered = _widening_team(tmp_path)
    _log(team, _row(_A, grants=_ORCH_TOKEN, relaxes=""))
    assert _run(team, rendered) == 0
    assert orch.read_text(encoding="utf-8") == _ORCH_TMPL
    assert _consumed(team) == [_A]
    # Single use: the consumed row does not clear the same widening again.
    _deploy(team, "orchestrator.md", _ORCH_DISK)
    assert _run(team, rendered) == 1


@pytest.mark.parametrize("grants, named", [
    ("frontmatter:orchestrator.md#agents;frontmatter:orchestrator.md#tools",
     "extra in decision: ['frontmatter:orchestrator.md#tools']"),
    ("frontmatter:other.md#agents", "missing from decision: ['frontmatter:orchestrator.md#agents']"),
])
def test_a_grant_set_other_than_the_runs_refuses_and_names_the_difference(tmp_path, stub_ed25519, grants, named):
    team, orch, _rendered = _widening_team(tmp_path)
    _log(team, _row(_A, grants=grants, relaxes=""))
    with pytest.raises(gate.ReconcileApplyError) as exc:
        gate.authorize(team, {"frontmatter:orchestrator.md#agents"}, consume=True)
    assert named in str(exc.value)
    assert _consumed(team) == []


def test_the_row_checked_is_the_row_spent(tmp_path, stub_ed25519, monkeypatch):
    """A row appended after the gate selects its clearance is neither checked in its place nor spent."""
    tokens = {"frontmatter:orchestrator.md#agents"}
    team, _orch, _rendered = _widening_team(tmp_path)
    _log(team, _row(_A, grants=";".join(tokens), relaxes=""))
    real_read, calls = security_gate._read_decision_rows, []

    def read_then_append(team_dir):
        read = real_read(team_dir)
        if not calls:  # the gate's first read of the log; another writer appends right after it
            _log(team, _row(_A, grants="frontmatter:other.md#tools", relaxes="", stamp="2026-11-20"))
        calls.append(1)
        return read

    monkeypatch.setattr(security_gate, "_read_decision_rows", read_then_append)
    try:
        gate.authorize(team, tokens, consume=True)
    except gate.ReconcileApplyError:
        assert _consumed(team) == []
        return
    with (team / "references" / "security-decisions.log.csv").open(newline="") as fh:
        spent = [r["effect_grants"] for r in csv.DictReader(fh) if (r.get("consumed") or "") == "yes"]
    assert spent == [";".join(tokens)], "a row other than the one whose grants were checked was spent"


def test_the_clearance_is_bound_to_the_value_it_was_signed_for(tmp_path, stub_ed25519, capsys):
    """A row signed for value A does not clear a wider value B rendered for the same key later."""
    wider = _ORCH_TMPL.replace("  - b\n", "  - b\n  - c\n")
    team, orch, rendered = _widening_team(tmp_path)
    assert _run(team, rendered) == 1
    token = re.search(r"effect_grants=(\S+?),", capsys.readouterr().out).group(1)
    assert token.startswith("frontmatter:orchestrator.md#agents@sha256:"), token
    _log(team, _row(_A, grants=token, relaxes=""))
    assert _run(team, [("orchestrator.md", wider)]) == 1
    assert orch.read_text(encoding="utf-8") == _ORCH_DISK and _consumed(team) == []
    assert _run(team, rendered) == 0  # negative control: the value it was signed for clears
    assert orch.read_text(encoding="utf-8") == _ORCH_TMPL


def test_governed_symmetric_signature_is_insufficient(tmp_path, stub_ed25519):
    """A declared grant makes the row constraint-relaxing, so only the operator's Ed25519 signature clears it."""
    team, _orch, _rendered = _widening_team(tmp_path)
    _log(team, _row(_A, grants="frontmatter:orchestrator.md#agents", relaxes="", scheme="hmac"))
    with pytest.raises(gate.ReconcileApplyError, match="REQUIRES ed25519"):
        gate.authorize(team, {"frontmatter:orchestrator.md#agents"}, consume=False)
    assert _consumed(team) == []


def test_governed_scope_must_be_the_action(tmp_path, stub_ed25519):
    team, _orch, _rendered = _widening_team(tmp_path)
    _log(team, _row(_A, scope="overwrite", grants="frontmatter:orchestrator.md#agents", relaxes=""))
    with pytest.raises(gate.ReconcileApplyError, match="scope=reconcile-apply-widen"):
        gate.authorize(team, {"frontmatter:orchestrator.md#agents"}, consume=False)


def test_a_waiver_refuses(tmp_path, stub_ed25519, monkeypatch):
    team, _orch, _rendered = _widening_team(tmp_path)
    _log(team, _row(_A, grants="frontmatter:orchestrator.md#agents", relaxes=""))
    monkeypatch.setattr(security_gate, "_latest_security_waiver", lambda *a, **k: {"action_reviewed": _A})
    with pytest.raises(gate.ReconcileApplyError, match="waiver"):
        gate.authorize(team, {"frontmatter:orchestrator.md#agents"}, consume=True)


def test_an_unretracted_halt_refuses_and_a_retraction_voids_the_older_clearance(tmp_path, stub_ed25519):
    tokens = {"frontmatter:orchestrator.md#agents"}
    team, _orch, _rendered = _widening_team(tmp_path / "halt")
    _log(team, _row(_A, grants=";".join(tokens), relaxes=""), _row(_A, verdict="HALT", stamp="2026-11-01"))
    with pytest.raises(gate.ReconcileApplyError, match="HALT"):
        gate.authorize(team, tokens, consume=False)
    team, _orch, _rendered = _widening_team(tmp_path / "retracted")
    _log(team, _row(_A, verdict="HALT", stamp="2026-11-01"), _row(_A, grants=";".join(tokens), relaxes=""),
         _retraction(_A, stamp="2026-11-02"))  # a clearance recorded under the HALT does not survive its retraction
    with pytest.raises(gate.ReconcileApplyError, match="older than the HALT retraction"):
        gate.authorize(team, tokens, consume=False)
    # Negative control: a clearance recorded after the retraction clears.
    _log(team, _row(_A, grants=";".join(tokens), relaxes="", stamp="2026-11-03"))
    gate.authorize(team, tokens, consume=False)


_LEGACY = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions", "conditions_verified"]


def _legacy_log(team: Path, *authors: str) -> None:
    """An unsigned six-column log (no signing columns), one PASS row per author, newest last."""
    with (team / "references" / "security-decisions.log.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_LEGACY)
        writer.writeheader()
        writer.writerows({"timestamp": f"2026-11-0{i + 1}", "requesting_agent": author, "action_reviewed": _A,
                          "verdict": "PASS", "conditions": "", "conditions_verified": "verified"}
                         for i, author in enumerate(authors))


@pytest.mark.parametrize("halt_id", gate.HALT_IDS)
def test_a_halt_on_either_spelling_blocks_and_its_retraction_voids_an_older_clearance(tmp_path, stub_ed25519,
                                                                                      halt_id):
    tokens = {"frontmatter:orchestrator.md#agents"}
    team, _orch, _rendered = _widening_team(tmp_path / "open")
    _log(team, _row(_A, grants=";".join(tokens), relaxes=""), _row(halt_id, verdict="HALT", stamp="2026-11-01"))
    with pytest.raises(gate.ReconcileApplyError, match="HALT"):
        gate.authorize(team, tokens, consume=False)
    team, _orch, _rendered = _widening_team(tmp_path / "retracted")
    _log(team, _row(halt_id, verdict="HALT", stamp="2026-11-01"), _row(_A, grants=";".join(tokens), relaxes=""),
         _retraction(halt_id, stamp="2026-11-02"))
    with pytest.raises(gate.ReconcileApplyError, match="retraction"):
        gate.authorize(team, tokens, consume=False)
    _log(team, _row(_A, grants=";".join(tokens), relaxes="", stamp="2026-11-03"))  # negative control
    gate.authorize(team, tokens, consume=False)


def test_ungoverned_ordinary_pass_from_an_approved_author_clears(tmp_path):
    team, orch, rendered = _widening_team(tmp_path, governed=False)
    _legacy_log(team, "someone-else")
    assert _run(team, rendered) == 1  # negative control: an author off the roster does not clear it
    assert orch.read_text(encoding="utf-8") == _ORCH_DISK
    _legacy_log(team, "someone-else", "security")
    assert _run(team, rendered) == 0
    assert orch.read_text(encoding="utf-8") == _ORCH_TMPL
    assert _consumed(team) == [_A]


def test_ungoverned_row_that_names_grants_must_name_this_runs(tmp_path, stub_ed25519):
    team, _orch, _rendered = _widening_team(tmp_path, governed=False)
    _log(team, _row(_A, scope="", grants="frontmatter:x.md#tools", relaxes=""))
    with pytest.raises(gate.ReconcileApplyError, match="missing from decision"):
        gate.authorize(team, {"frontmatter:orchestrator.md#agents"}, consume=False)


_RO_DISK = "---\nname: c\ntools: Edit, Write, Grep, Glob, Bash\n---\nbody\n"
_RO_TMPL = "---\nname: c\ntools: Read, Grep, Glob\n---\nbody\n"


def test_a_key_that_removes_and_adds_applies_the_removal_without_a_clearance(tmp_path, capsys):
    """The read-only switch removes Write/Edit/Bash at once; only the added Read waits on a clearance."""
    team = _team(tmp_path, governed=True)
    path = _deploy(team, "cleanup.md", _RO_DISK)
    assert _run(team, [("cleanup.md", _RO_TMPL)]) == 1
    assert path.read_text(encoding="utf-8") == "---\nname: c\ntools: Grep, Glob\n---\nbody\n"
    assert "REFUSED cleanup.md: tools" in capsys.readouterr().out


def test_with_the_clearance_the_added_items_apply_too(tmp_path, stub_ed25519, capsys):
    team = _team(tmp_path, governed=True)
    path = _deploy(team, "cleanup.md", _RO_DISK)
    assert _run(team, [("cleanup.md", _RO_TMPL)]) == 1
    token = re.search(r"effect_grants=(\S+?),", capsys.readouterr().out).group(1)
    _log(team, _row(_A, grants=token, relaxes=""))
    assert _run(team, [("cleanup.md", _RO_TMPL)]) == 0
    assert path.read_text(encoding="utf-8") == _RO_TMPL


@pytest.mark.parametrize("disk, tmpl", [
    ("['write']", "['read']"),          # nothing in common: an empty tools list can mean every tool
    ("Write, Bash", "Read"),
    ("\n  - write", "\n  - read"),
])
def test_an_empty_intersection_refuses_the_whole_key(tmp_path, disk, tmpl):
    team = _team(tmp_path, governed=True)
    text = f"---\nname: c\ntools:{'' if disk.startswith(chr(10)) else ' '}{disk}\n---\nbody\n"
    path = _deploy(team, "c.md", text)
    render = f"---\nname: c\ntools:{'' if tmpl.startswith(chr(10)) else ' '}{tmpl}\n---\nbody\n"
    assert _run(team, [("c.md", render)]) == 1
    assert path.read_text(encoding="utf-8") == text


def test_a_deny_list_adds_its_new_denials_and_waits_to_drop_one(tmp_path):
    team = _team(tmp_path, governed=True)
    path = _deploy(team, "d.md", "---\nname: d\ndisallowedTools: Bash\n---\nbody\n")
    assert _run(team, [("d.md", "---\nname: d\ndisallowedTools: Write\n---\nbody\n")]) == 1
    assert path.read_text(encoding="utf-8") == "---\nname: d\ndisallowedTools: Write, Bash\n---\nbody\n"


@pytest.mark.parametrize("disk, tmpl, expected", [
    ("['read', 'edit', 'execute']", "['read', 'search']", "['read']"),
    ("\n  - a\n  - b", "\n  - a\n  - c", "\n  - a"),
    ("Read, Bash", "Read, Grep", "Read"),
])
def test_the_safe_part_keeps_the_templates_shape(disk, tmpl, expected):
    assert fmw.safe_part("tools", disk, tmpl) == expected


@pytest.mark.parametrize("key, deployed, template, expected", [
    ("mcpServers", None, "\n  runner:\n    command: x", "widening"),
    ("model", "sonnet", "opus", "widening"),
    ("tools", "Read, Bash", "Read", "safe"),
    ("tools", "Read", "Read, Bash", "widening"),
    ("tools", "['read', 'edit']", "Read", "widening"),     # unparseable against each other: different vocabularies
    ("tools", "Read [x", "Read", "widening"),            # unparseable value
    ("agents", "\n  - a\n  - b", "\n  - a", "safe"),
    ("agents", "\n  - a", "\n  - a\n  - b", "widening"),
    ("disallowedTools", "Bash", "Bash, Write", "safe"),   # a deny list grows: narrowing
    ("disallowedTools", "Bash, Write", "Bash", "widening"),
    ("description", "old", "new", "safe"),
])
def test_absent_capability_key_and_model_change_are_widening(key, deployed, template, expected):
    assert fmr.classify(Divergence("a.md", key, deployed, template)) == expected


@pytest.mark.parametrize("template", [
    "['read', execute]",        # an unquoted item the lenient parse drops
    "['read', 'execute',]",     # trailing comma
    "['read', ['execute']]",    # nested brackets
    "['read' 'execute']",       # no separator
    "[]",                       # an empty list can mean every tool
    "['read', '']",
])
def test_a_flow_list_not_made_only_of_well_formed_items_is_widening(template):
    assert fmr.classify(Divergence("a.md", "tools", "['read', 'edit']", template)) == "widening"
    # Negative control: the well-formed subset is safe.
    assert fmr.classify(Divergence("a.md", "tools", "['read', 'edit']", "['read']")) == "safe"


def test_dry_run_consumes_nothing(tmp_path, stub_ed25519, capsys):
    team, orch, rendered = _widening_team(tmp_path)
    _log(team, _row(_A, grants=_ORCH_TOKEN, relaxes=""))
    log = (team / "references" / "security-decisions.log.csv").read_bytes()
    assert _run(team, rendered, dry_run=True) == 0
    assert orch.read_text(encoding="utf-8") == _ORCH_DISK
    assert (team / "references" / "security-decisions.log.csv").read_bytes() == log
    assert "[DRY RUN]" in capsys.readouterr().out
    # Negative control: without a clearance the dry run names what it would refuse.
    team2, _orch2, rendered2 = _widening_team(tmp_path / "none")
    assert _run(team2, rendered2, dry_run=True) == 0
    assert "REFUSED orchestrator.md: agents" in capsys.readouterr().out


def test_the_value_digest_changes_with_the_items_the_key_or_the_text():
    base = fmw.value_digest("tools", "['read', 'edit']")
    assert fmw.value_digest("tools", "['read', 'edit', 'execute']") != base
    assert fmw.value_digest("agents", "['read', 'edit']") != base  # the key is part of the digest
    block = fmw.value_digest("mcpServers", "\n  r:\n    command: x")
    assert fmw.value_digest("mcpServers", "\n  r:\n    command: y") != block


def test_the_value_digest_ignores_order_quoting_and_trailing_whitespace():
    assert fmw.value_digest("tools", '["edit", "read"]') == fmw.value_digest("tools", "['read', 'edit']")
    block = fmw.value_digest("mcpServers", "\n  r:\n    command: x")
    assert fmw.value_digest("mcpServers", "\n  r:\n    command: x  ") == block
