"""Tests for agentteams.plan_priority — expected-net-benefit ordering of plan items."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import plan_priority as pp
from agentteams.parallel_plan import compute_waves, main as parallel_main, read_steps

HEADER = "step,agent,action,inputs,outputs,status,notes,depends_on,severity,breadth,cost,confidence,kind,pin"


def _csv(tmp_path: Path, body: str, header: str = HEADER, name: str = "p.steps.csv") -> Path:
    path = tmp_path / name
    path.write_text(header + "\n" + body.strip() + "\n", encoding="utf-8")
    return path


def _order(report: pp.PriorityReport) -> list[str]:
    return [it.step for it in report.items]


def _by(report: pp.PriorityReport) -> dict[str, pp.PriorityItem]:
    return {it.step: it for it in report.items}


# --------------------------------------------------------------------------- formula


@pytest.mark.parametrize(
    ("s", "b", "c", "p", "eb", "cp", "net"),
    [
        (3, 5, 3, 0.9, 13.5, 4, 9.5),
        (5, 5, 1, 1.0, 25.0, 1, 24.0),
        (1, 1, 5, 1.0, 1.0, 16, -15.0),
        (4, 3, 4, 0.5, 6.0, 8, -2.0),
    ],
)
def test_score_formula(s, b, c, p, eb, cp, net):
    assert pp.score(s, b, c, p) == (eb, cp, net)


def test_cost_points_double_per_tier():
    assert [pp.COST_POINTS[c] for c in range(1, 6)] == [1, 2, 4, 8, 16]


@pytest.mark.parametrize(
    ("net", "s", "band"),
    [
        (10, 1, "do-now"),
        (9.99, 1, "next"),
        (4, 1, "next"),
        (3.99, 1, "later"),
        (0, 1, "later"),
        (-0.01, 1, "reconsider"),
        (3.99, 5, "next (safety floor)"),
        (-11, 5, "next (safety floor)"),
        (12, 5, "do-now"),
    ],
)
def test_bands_and_safety_floor_label(net, s, band):
    assert pp.band_for(net, s) == band


# --------------------------------------------------------------------------- ordering


def test_safety_floor_outranks_a_cheap_next_item(tmp_path):
    # A: S5 B1 C2 -> net 5-2 = 3 -> safety floor. B: S1 B5 C1 -> net 4 -> next.
    # C: S5 B1 C3 -> net 1 -> safety floor. Spec case: S5 net -1 outranks S1 B5 C1 net 4.
    path = _csv(tmp_path, """
A,a,x,,,pending,,,5,1,2,,,
B,a,x,,,pending,,,1,5,1,,,
C,a,x,,,pending,,,5,1,4,0.8,,
D,a,x,,,pending,,,5,5,1,,,
""")
    rep = pp.analyze_priority(path)
    assert not rep.errors
    by = _by(rep)
    assert by["C"].net_benefit == -4.0
    assert _order(rep) == ["D", "A", "C", "B"]
    assert by["A"].band == by["C"].band == "next (safety floor)"
    assert any("safety floor" in w and w.startswith("C:") for w in rep.warnings)


def test_safety_floor_case_from_the_spec(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,1,5,1,,,
2,a,x,,,pending,,,5,1,3,0.6,,
""")
    rep = pp.analyze_priority(path)
    by = _by(rep)
    assert by["2"].net_benefit == -1.0 and by["1"].net_benefit == 4.0
    assert _order(rep) == ["2", "1"]


def test_tie_breaks_severity_then_cost_then_numeric_id(tmp_path):
    # All net 4 (next band).
    path = _csv(tmp_path, """
10,a,x,,,pending,,,2,4,3,,,
9,a,x,,,pending,,,2,4,3,,,
3,a,x,,,pending,,,4,2,3,,,
2,a,x,,,pending,,,1,5,1,,,
""")
    rep = pp.analyze_priority(path)
    assert [it.net_benefit for it in rep.items] == [4.0] * 4
    # S4 first; then S2 C3 (9 before 10, numerically); S1 last.
    assert _order(rep) == ["3", "9", "10", "2"]


def test_natural_key_compares_numbers_as_numbers():
    assert sorted(["S10", "S2", "S1"], key=pp.natural_key) == ["S1", "S2", "S10"]


def test_band_order_beats_net_across_bands(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,1,1,1,,,
2,a,x,,,pending,,,3,4,3,,,
3,a,x,,,pending,,,1,1,5,,,
4,a,x,,,pending,,,5,5,4,,,
""")
    rep = pp.analyze_priority(path)
    assert [it.band for it in rep.items] == ["do-now", "next", "later", "reconsider"]
    assert _order(rep) == ["4", "2", "1", "3"]


# --------------------------------------------------------------------------- pins


def test_pins_go_first_in_pin_date_order_and_are_listed(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,5,5,1,,,
2,a,x,,,pending,,,1,1,5,,,operator:2026-11-12:later pin
3,a,x,,,pending,,,1,1,1,,,operator:2026-11-10:first pin
""")
    rep = pp.analyze_priority(path)
    assert not rep.errors
    assert _order(rep) == ["3", "2", "1"]
    assert [s for s, _ in rep.pins] == ["3", "2"]
    assert _by(rep)["2"].band == "pinned"


@pytest.mark.parametrize("pin", ["agent:2026-11-10:me", "operator:yesterday:x", "operator:2026-11-10:", "yes"])
def test_malformed_or_agent_pin_is_an_error(tmp_path, pin):
    path = _csv(tmp_path, f"1,a,x,,,pending,,,3,3,3,,,{pin}\n")
    rep = pp.analyze_priority(path)
    assert any("pin=" in e and "only the operator pins" in e for e in rep.errors)


def test_pin_on_unrated_or_closing_row_is_an_error(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,,,,,,operator:2026-11-10:x
2,a,x,,,pending,,,,,,,closing,operator:2026-11-10:x
""")
    rep = pp.analyze_priority(path)
    assert any(e.startswith("1:") and "unrated" in e for e in rep.errors)
    assert any(e.startswith("2:") and "closing" in e for e in rep.errors)


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("cells", "needle"),
    [
        ("0,3,3,", "severity=0 outside 1..5"),
        ("3,6,3,", "breadth=6 outside 1..5"),
        ("3,3,x,", "cost='x' is not an integer"),
        ("3,3,3,0.2", "confidence=0.2 outside 0.25..1.0"),
        ("3,3,3,1.5", "confidence=1.5 outside 0.25..1.0"),
        ("3,3,3,high", "confidence='high' is not a number"),
        ("3,,3,", "partly rated; missing breadth"),
    ],
)
def test_validation_errors(tmp_path, cells, needle):
    path = _csv(tmp_path, f"1,a,x,,,pending,,,{cells},,\n")
    rep = pp.analyze_priority(path)
    assert any(needle in e for e in rep.errors), rep.errors


def test_every_bad_row_is_listed_and_cli_exits_2(tmp_path, capsys):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,9,3,3,,,
2,a,x,,,pending,,,3,3,3,,,
3,a,x,,,pending,,,3,0,3,,,
""")
    assert pp.main([str(path), "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert {e.split(":")[0] for e in out["errors"]} == {"1", "3"}


def test_missing_file_exits_1(tmp_path, capsys):
    assert pp.main([str(tmp_path / "nope.csv")]) == 1


def test_csv_without_step_or_id_column_is_rejected(tmp_path):
    path = _csv(tmp_path, "x,y\n", header="name,other")
    with pytest.raises(ValueError):
        pp.analyze_priority(path)


def test_backlog_csv_keyed_by_id_with_rationales(tmp_path):
    header = "id,kind,title,severity,breadth,cost,confidence,pin,rationale_s,rationale_b,rationale_c,rationale_p,depends_on"
    path = _csv(tmp_path, """
01,,Gate fails open,5,4,2,,,"bypass","claude surface","small fix",,
02,,Typo,1,1,1,,,"cosmetic","maintainers","one line",,
03,,Wire checker,3,3,3,0.5,,"not wired","research teams","one module","unproven design",
""", header=header, name="backlog.csv")
    rep = pp.analyze_priority(path, require=True)
    assert not rep.errors, rep.errors
    # 01: 18 (do-now); 03: 0.5 and 02: 0 (both later).
    assert _order(rep) == ["01", "03", "02"]
    assert _by(rep)["01"].title == "Gate fails open"


def test_require_rejects_an_empty_rationale(tmp_path):
    header = "id,severity,breadth,cost,confidence,rationale_s,rationale_b,rationale_c,rationale_p"
    path = _csv(tmp_path, """
1,3,3,3,,why,,why,
2,3,3,3,0.5,why,why,why,
""", header=header)
    assert not pp.analyze_priority(path).errors
    errs = pp.analyze_priority(path, require=True).errors
    assert any(e.startswith("1:") and "breadth has no rationale" in e for e in errs)
    assert any(e.startswith("2:") and "confidence has no rationale" in e for e in errs)
    assert not any(e.startswith("1:") and "confidence" in e for e in errs)  # default p needs none


# --------------------------------------------------------------------------- closing / unrated


def test_closing_rows_are_unranked_and_last_in_csv_order(tmp_path):
    path = _csv(tmp_path, """
8,a,close,,,pending,,,,,,,closing,
1,a,x,,,pending,,,1,1,1,,,
7,a,integrate,,,pending,,,,,,,closing,
2,a,x,,,pending,,,5,5,1,,,
""")
    rep = pp.analyze_priority(path)
    assert not rep.errors and not any("unrated" in w for w in rep.warnings)
    assert _order(rep) == ["2", "1", "8", "7"]
    assert _by(rep)["8"].rank is None and _by(rep)["8"].order == 3


def test_unrated_row_warns_and_is_never_ranked_ahead_of_a_rated_item(tmp_path):
    path = _csv(tmp_path, """
1,a,unrated,,,pending,,,,,,,,
2,a,x,,,pending,,,1,1,5,,,
""")
    rep = pp.analyze_priority(path)
    assert not rep.errors
    assert _order(rep) == ["2", "1"]
    assert _by(rep)["1"].rank is None and _by(rep)["1"].effective_rank is None
    assert any(w.startswith("1: unrated") for w in rep.warnings)
    assert any(e.startswith("1: unrated") for e in pp.analyze_priority(path, require=True).errors)


def test_unrated_prerequisite_of_a_rated_item_runs_before_it(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,1,1,1,,,
2,a,unrated prereq,,,pending,,,,,,,,
3,a,x,,,pending,,2,5,5,1,,,
""")
    rep = pp.analyze_priority(path)
    assert _order(rep) == ["2", "3", "1"]
    assert _by(rep)["2"].rank is None and _by(rep)["2"].effective_rank == 1


def test_seven_column_csv_without_ratings(tmp_path, capsys):
    path = _csv(tmp_path, """
1,a,x,in/a.md,out/a.md,pending,
2,a,y,in/b.md,out/b.md,pending,
""", header="step,agent,action,inputs,outputs,status,notes")
    rep = pp.analyze_priority(path)
    assert not rep.errors and _order(rep) == ["1", "2"]
    assert all(it.rank is None for it in rep.items)
    assert pp.main([str(path)]) == 0
    assert pp.main([str(path), "--require"]) == 2


def test_work_row_depending_on_a_closing_row_is_an_error(tmp_path):
    path = _csv(tmp_path, """
1,a,close,,,pending,,,,,,,closing,
2,a,x,,,pending,,1,3,3,3,,,
""")
    rep = pp.analyze_priority(path)
    assert any("depends on closing row 1" in e for e in rep.errors)


# --------------------------------------------------------------------------- dependencies


def test_priority_inheritance_across_a_three_level_chain(tmp_path):
    # top (rank 1) <- mid <- base; base and mid are cheap low-value prerequisites.
    path = _csv(tmp_path, """
base,a,x,,,pending,,,1,1,3,,,
mid,a,x,,,pending,,base,1,2,3,,,
top,a,x,,,pending,,mid,5,5,2,,,
other,a,x,,,pending,,,3,3,1,,,
""")
    rep = pp.analyze_priority(path)
    by = _by(rep)
    assert by["top"].rank == 1 and by["other"].rank == 2
    assert by["base"].effective_rank == by["mid"].effective_rank == 1
    assert _order(rep) == ["base", "mid", "top", "other"]


def test_ranking_never_reorders_across_an_edge(tmp_path):
    # The high-value item depends on the low-value one: the edge wins.
    path = _csv(tmp_path, """
1,a,x,,,pending,,2,5,5,1,,,
2,a,x,,,pending,,,1,1,5,,,
""")
    rep = pp.analyze_priority(path)
    assert _order(rep) == ["2", "1"]


def test_implicit_read_after_write_edge_is_honoured(tmp_path):
    # 2 reads what 1 writes; no depends_on. 2 is worth more, 1 inherits its rank.
    path = _csv(tmp_path, """
1,a,write,src/a.md,build/out.md,pending,,,1,1,3,,,
2,a,read,build/out.md,docs/b.md,pending,,,5,5,1,,,
3,a,other,src/c.md,docs/c.md,pending,,,3,3,1,,,
""")
    rep = pp.analyze_priority(path)
    assert any("inferred order 1 -> 2" in w for w in rep.warnings)
    assert _by(rep)["1"].effective_rank == 1
    assert _order(rep) == ["1", "2", "3"]


def test_write_write_overlap_is_not_an_ordering_edge(tmp_path):
    path = _csv(tmp_path, """
1,a,x,src/a.md,shared/out.md,pending,,,1,1,1,,,
2,a,y,src/b.md,shared/out.md,pending,,,5,5,1,,,
""")
    rep = pp.analyze_priority(path)
    assert _order(rep) == ["2", "1"]
    assert _by(rep)["1"].effective_rank == 2


def test_cycle_is_an_error(tmp_path, capsys):
    path = _csv(tmp_path, """
1,a,x,,,pending,,3,3,3,3,,,
2,a,x,,,pending,,1,3,3,3,,,
3,a,x,,,pending,,2,3,3,3,,,
""")
    rep = pp.analyze_priority(path)
    assert any("dependency cycle" in e for e in rep.errors)
    assert pp.main([str(path)]) == 2


def test_unknown_dependency_warns(tmp_path):
    path = _csv(tmp_path, "1,a,x,,,pending,,99,3,3,3,,,\n")
    rep = pp.analyze_priority(path)
    assert not rep.errors
    assert any("unknown row '99'" in w for w in rep.warnings)


def test_duplicate_id_is_an_error(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,3,3,3,,,
1,a,y,,,pending,,,3,3,3,,,
""")
    assert any("duplicate row id" in e for e in pp.analyze_priority(path).errors)


# --------------------------------------------------------------------------- computed columns / output


def test_computed_columns_are_checked_never_written(tmp_path):
    header = "step,severity,breadth,cost,net_benefit,rank,band"
    path = _csv(tmp_path, """
1,3,5,3,12.0,1,next
2,1,1,1,0,2,later
""", header=header)
    before = path.read_text(encoding="utf-8")
    rep = pp.analyze_priority(path)
    assert path.read_text(encoding="utf-8") == before
    assert any(w.startswith("1: CSV net_benefit='12.0'") for w in rep.warnings)
    assert not any(w.startswith("1: CSV rank") or w.startswith("2: CSV") for w in rep.warnings)
    assert any(e.startswith("1: CSV net_benefit") for e in pp.analyze_priority(path, require=True).errors)


def test_json_shape(tmp_path, capsys):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,3,5,3,0.9,,operator:2026-11-10:requested first
2,a,close,,,pending,,,,,,,closing,
""")
    assert pp.main([str(path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"plan", "items", "pins", "warnings", "errors"}
    first = out["items"][0]
    for key in ("step", "rank", "net_benefit", "band", "effective_rank", "order"):
        assert key in first
    assert first["net_benefit"] == 9.5 and first["band"] == "pinned" and first["rank"] == 1
    assert out["items"][1]["rank"] is None and out["items"][1]["kind"] == "closing"
    assert out["pins"] == [{"step": "1", "pin": "operator:2026-11-10:requested first"}]


def test_json_lists_several_plans(tmp_path, capsys):
    a = _csv(tmp_path, "1,a,x,,,pending,,,3,3,3,,,\n", name="a.csv")
    b = _csv(tmp_path, "1,a,x,,,pending,,,3,3,3,,,\n", name="b.csv")
    assert pp.main([str(a), str(b), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list) and [o["plan"] for o in out] == [str(a), str(b)]


def test_markdown_table(tmp_path):
    path = _csv(tmp_path, """
1,a,first item,,,pending,,,3,5,3,0.9,,operator:2026-11-10:requested first
2,a,unrated item,,,pending,,,,,,,,
""")
    md = pp.render_markdown(pp.analyze_priority(path))
    assert "| order | rank | eff. | step |" in md
    assert "| 1 | 1 | 1 | 1 | 9.5 | 3 | 5 | 3 | 0.9 | pinned | pending | first item |" in md
    assert "| unrated |" in md
    assert "**Operator pins:**" in md


# --------------------------------------------------------------------------- parallel_plan --priority


_WW = """step,agent,action,inputs,outputs,status,notes,depends_on,severity,breadth,cost
1,a,low,src/a.md,shared/out.md,pending,,,1,1,1
2,a,high,src/b.md,shared/out.md,pending,,,5,5,1
3,a,mid,src/c.md,docs/c.md,pending,,,3,3,1
"""


def test_default_schedule_is_unchanged_without_priority(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text(_WW, encoding="utf-8")
    sched = compute_waves(read_steps(path))
    assert sched.waves == [["1", "3"], ["2"]]
    assert "serialized 1 before 2 (write-write conflict on shared path)" in sched.warnings


def test_priority_breaks_write_write_conflicts_by_rank(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text(_WW, encoding="utf-8")
    prio = pp.priority_map(pp.analyze_priority(path))
    sched = compute_waves(read_steps(path), priority=prio)
    assert not sched.errors
    assert sched.waves == [["2", "3"], ["1"]]
    assert any("serialized 2 before 1" in w and "by priority rank" in w for w in sched.warnings)


def test_priority_orders_wave_members_by_rank(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("""step,agent,action,inputs,outputs,status,notes,depends_on,severity,breadth,cost
a,x,one,src/a.md,out/a.md,pending,,,1,1,1
b,x,two,src/b.md,out/b.md,pending,,,5,5,1
c,x,three,src/c.md,out/c.md,pending,,,3,3,1
""", encoding="utf-8")
    assert compute_waves(read_steps(path)).waves == [["a", "b", "c"]]
    prio = pp.priority_map(pp.analyze_priority(path))
    assert compute_waves(read_steps(path), priority=prio).waves == [["b", "c", "a"]]


def test_parallel_plan_cli_priority_flag(tmp_path, capsys):
    path = tmp_path / "p.csv"
    path.write_text(_WW, encoding="utf-8")
    assert parallel_main([str(path), "--priority", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["schedule"]["waves"] == [["2", "3"], ["1"]]


def test_parallel_plan_cli_priority_refuses_invalid_ratings(tmp_path, capsys):
    path = tmp_path / "p.csv"
    path.write_text(_WW.replace("5,5,1", "9,5,1"), encoding="utf-8")
    assert parallel_main([str(path), "--priority"]) == 2
    assert "severity=9 outside 1..5" in capsys.readouterr().err


# --------------------------------------------------------------------------- every rendered surface


_REPO = Path(__file__).resolve().parents[1]
_CLAUSE_CORE = "descending **expected net benefit**"


def _render_surface(framework: str) -> dict[str, str]:
    from agentteams import analyze, ingest, render
    from agentteams.frameworks.registry import FRAMEWORKS

    adapter = FRAMEWORKS[framework]()
    description = ingest.load(_REPO / "examples/software-project/brief.json", scan_project=False)
    manifest = analyze.build_manifest(description, framework=framework)
    out: dict[str, str] = {}
    for rel, content in render.render_all(manifest, templates_dir=_REPO / "agentteams/templates"):
        lower = rel.lower()
        if "copilot-instructions" in lower or rel.endswith("CLAUDE.md"):
            content = adapter.render_instructions_file(content, manifest)
        elif "/references/" in rel or rel.startswith("references/"):
            pass
        elif rel.endswith(".agent.md") or "orchestrator" in lower:
            content = adapter.render_agent_file(content, Path(rel).stem.replace(".agent", ""), manifest)
        out[rel] = content
    return out


@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex", "grok", "wasm"])
def test_every_surface_renders_the_clause_and_the_reference(framework):
    files = _render_surface(framework)
    instr = [c for r, c in files.items() if "copilot-instructions" in r.lower() or r.endswith("CLAUDE.md")]
    orch = [c for r, c in files.items() if "orchestrator" in r.lower() and "reference" not in r.lower()]
    assert instr and any(_CLAUSE_CORE in " ".join(c.split()) for c in instr), framework
    assert orch and any(_CLAUSE_CORE in " ".join(c.split()) for c in orch), framework
    refs = [c for r, c in files.items() if r.endswith("net-benefit-prioritization.reference.md")]
    assert refs and "net_benefit      = expected_benefit − cost_points(C)" in refs[0]
    assert "{PROJECT_NAME}" not in refs[0]


# --------------------------------------------------------------------------- audit follow-ups


def test_an_edge_between_two_closing_rows_wins_over_csv_order(tmp_path):
    path = _csv(tmp_path, """
1,a,x,,,pending,,,3,3,1,,,
close,a,closeout,,,pending,,integ,,,,,closing,
integ,a,integrate,,,pending,,,,,,,closing,
""")
    rep = pp.analyze_priority(path)
    assert not rep.errors
    assert _order(rep) == ["1", "integ", "close"]


def test_priority_on_closing_rows_creates_no_false_cycle(tmp_path):
    # c1 depends on c3; c1/c2 and c2/c3 are write-write ties. CSV-order closing rows used to
    # orient the ties into a cycle.
    path = tmp_path / "p.csv"
    path.write_text("""step,agent,action,inputs,outputs,status,notes,depends_on,severity,breadth,cost,kind
w,a,work,src/w.md,out/w.md,pending,,,3,3,1,
c1,a,close one,src/c1.md,out/a.md,pending,,c3,,,,closing
c2,a,close two,src/c2.md,out/a.md out/b.md,pending,,,,,,closing
c3,a,close three,src/c3.md,out/b.md,pending,,,,,,closing
""", encoding="utf-8")
    rep = pp.analyze_priority(path)
    assert not rep.errors
    assert _order(rep).index("c3") < _order(rep).index("c1")
    sched = compute_waves(read_steps(path), priority=pp.priority_map(rep))
    assert not sched.errors, sched.errors


def test_require_needs_rationale_columns_when_rows_are_rated(tmp_path):
    path = _csv(tmp_path, "1,a,x,,,pending,,,3,3,3,,,\n")
    errs = pp.analyze_priority(path, require=True).errors
    assert any("add column(s) rationale_s, rationale_b, rationale_c" in e for e in errs)


@pytest.mark.parametrize(("args", "needle"), [((0, 3, 3), "severity=0"), ((3, 6, 3), "breadth=6"),
                                             ((3, 3, 0), "cost=0"), ((3, 3, 3, 0.1), "confidence=0.1")])
def test_score_rejects_out_of_range_ratings(args, needle):
    with pytest.raises(ValueError, match=needle):
        pp.score(*args)


def test_band_for_rejects_out_of_range_severity():
    with pytest.raises(ValueError):
        pp.band_for(3.0, 6)


def test_parallel_plan_priority_keeps_going_after_a_bad_csv(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text(_WW.replace("5,5,1", "9,5,1"), encoding="utf-8")
    good = tmp_path / "good.csv"
    good.write_text(_WW, encoding="utf-8")
    assert parallel_main([str(bad), str(good), "--priority", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert [p["plan"] for p in out if "plan" in p] == [str(good)]
