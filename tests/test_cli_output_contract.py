"""What the CLI tells the operator must be true and machine-readable where promised.

Three independent defects on one surface, fixed together because they share `cli/generate.py`
and its output contract:

1. ``--dry-run --json`` promised "a single JSON document on stdout" in ``--help`` and delivered
   nine progress lines plus one ``[DRY RUN] WRITE`` line per file ahead of it, so
   ``json.load(sys.stdin)`` failed at line 1.
2. A no-op update announced *"No structural or content changes detected"* and then rewrote every
   file carrying a live-data fence — an operator seeing a clean summary followed by a dirty
   working tree could not tell whether the tool had misreported.
3. Four security-gate failures across **two different gates** printed near-identical
   ``Security gate blocked <x>: …`` lines, so a log could not say which gate fired.

These are contract tests: they assert the shape of what an operator or a script consumes, not
the internals that produce it.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GENERATE = _REPO_ROOT / "agentteams" / "cli" / "generate.py"


# --- 1. --dry-run --json emits exactly one JSON document -------------------

def test_json_mode_hands_the_real_stdout_to_the_report():
    """The mechanism, asserted behaviourally rather than by reading source.

    A subprocess round-trip would be a full generation run (minutes), and an earlier version of
    this test grepped `run_generate`'s source — which broke the moment the logic was carved into
    `cli.json_mode`, testing where the code lived rather than what it did. This drives the real
    wrapper with a stub and checks the two things that matter: the stashed handle is the stdout
    from *before* the redirect, and `sys.stdout` is pointed at stderr while the inner call runs.
    """
    import sys

    from agentteams.cli import json_mode

    seen = {}

    def _inner(args, strict):
        seen["stdout_during"] = sys.stdout
        seen["stashed"] = json_mode.json_stdout()
        return 0

    class _Args:
        json = True
        dry_run = True

    real_stdout = sys.stdout
    assert json_mode.run_with_json_stdout(_inner, _Args(), False) == 0
    assert seen["stashed"] is real_stdout, "the report must receive the pre-redirect stdout"
    assert seen["stdout_during"] is sys.stderr, "human output must go to stderr in JSON mode"
    assert json_mode.json_stdout() is None, "the handle must not leak past the run"


def test_non_json_runs_are_untouched():
    """The compatibility half: plain --dry-run must behave exactly as before."""
    import sys

    from agentteams.cli import json_mode

    seen = {}

    def _inner(args, strict):
        seen["stdout_during"] = sys.stdout
        return 7

    class _Args:
        json = False
        dry_run = True

    assert json_mode.run_with_json_stdout(_inner, _Args(), False) == 7
    assert seen["stdout_during"] is sys.stdout, "no redirection outside JSON mode"


def test_json_without_dry_run_is_a_documented_no_op():
    """`--json` alone is documented as a no-op; it must not redirect anything."""
    from agentteams.cli import json_mode

    class _Args:
        json = True
        dry_run = False

    assert json_mode.is_json_mode(_Args()) is False


def test_every_dry_run_report_call_site_passes_the_json_stream():
    """Regression guard for the bug found while building this fix.

    There are two `print_dry_run_report` call sites — the update path and the generate path.
    Patching only the first left the generate path writing its JSON to the redirected stdout,
    i.e. to stderr, so stdout came back empty. Any new call site must pass the stream too.
    """
    source = _GENERATE.read_text(encoding="utf-8")
    call_sites = re.findall(
        r"emit\.print_dry_run_report\((.*?)\)\n", source, re.DOTALL
    )
    assert len(call_sites) >= 2, "expected at least the update and generate call sites"
    missing = [c for c in call_sites if "stream=" not in c]
    assert not missing, (
        f"{len(missing)} print_dry_run_report call site(s) omit `stream=`; in JSON mode their "
        f"output lands on stderr and stdout comes back empty."
    )


def test_report_writes_to_the_supplied_stream(tmp_path):
    """`stream=` must actually be honoured, in both formats."""
    import io

    from agentteams import emit

    result = emit.EmitResult(dry_run=True, dry_run_report=emit.DryRunReport(
        entries=[emit.DryRunEntry(path="a.md", action="WRITE", delta_bytes=1)],
    ))
    manifest = {"project_name": "P", "framework": "claude"}

    buf = io.StringIO()
    emit.print_dry_run_report(result, manifest, fmt="json", stream=buf)
    payload = json.loads(buf.getvalue())
    assert payload["project_name"] == "P"
    assert payload["counts"] == {"WRITE": 1}

    buf_text = io.StringIO()
    emit.print_dry_run_report(result, manifest, fmt="text", stream=buf_text)
    assert "DRY RUN PLAN" in buf_text.getvalue()


def test_json_report_output_is_parseable_on_its_own():
    """The property the --help text promises: nothing but JSON."""
    import io

    from agentteams import emit

    result = emit.EmitResult(dry_run=True, dry_run_report=emit.DryRunReport(
        entries=[emit.DryRunEntry(path=f"f{i}.md", action="WRITE") for i in range(5)],
        notices=["a notice"],
    ))
    buf = io.StringIO()
    emit.print_dry_run_report(result, {"project_name": "P", "framework": "claude"},
                              fmt="json", stream=buf)
    payload = json.loads(buf.getvalue())   # would raise if anything else were written
    assert payload["notices"] == ["a notice"]
    assert len(payload["entries"]) == 5


# --- 2. the no-op summary states what actually happens ---------------------

def test_no_change_summary_does_not_claim_nothing_happens():
    source = _GENERATE.read_text(encoding="utf-8")
    assert "No structural or content changes detected; refreshing security" not in source, (
        "the old wording is back: it reads as 'this run did nothing' while the run goes on to "
        "rewrite every live-data fence"
    )
    assert "No structural or template-content changes detected." in source
    assert "may change" in source, "the summary must warn that files can still change"


# --- 3. security gates are distinguishable -------------------------------

# Gate-FIRST naming. The first version of these codes was action-first and filed the
# freshness gate under "[SEC-GATE/WRITE-PATH]" — which hid the two-gates distinction the codes
# existed to expose. Three sites raise from `_assert_destructive_action_allowed`; one raises
# from `_assert_security_intelligence_fresh`, and a log grep must be able to tell which.
_EXPECTED_GATE_CODES = {
    "[SEC-GATE/DESTRUCTIVE:restore-backup]",
    "[SEC-GATE/DESTRUCTIVE:overwrite-update]",
    "[SEC-GATE/DESTRUCTIVE:overwrite]",
    "[SEC-GATE/INTEL-FRESHNESS]",
}


def test_each_security_gate_has_a_distinct_greppable_code():
    source = _GENERATE.read_text(encoding="utf-8")
    for code in _EXPECTED_GATE_CODES:
        assert code in source, f"missing gate code {code}"


def test_no_two_gates_share_a_code():
    """The whole point: two *different* gates were previously indistinguishable in a log."""
    source = _GENERATE.read_text(encoding="utf-8")
    found = re.findall(r"\[SEC-GATE/[A-Za-z:-]+\]", source)
    assert len(found) == len(set(found)), f"duplicate gate codes: {found}"


def test_the_old_undifferentiated_message_is_gone():
    source = _GENERATE.read_text(encoding="utf-8")
    assert "Security gate blocked" not in source, (
        "an undifferentiated 'Security gate blocked' message remains; each site needs its own "
        "[SEC-GATE/...] code so a log can say which gate fired"
    )


def test_each_code_names_the_gate_that_actually_raised():
    """The mis-mapping that shipped first: a code must match the function it wraps.

    Walks back from each `[SEC-GATE/...]` print to the nearest `_assert_*` call above it and
    checks the code names that gate. This is what would have caught filing the freshness gate
    under "WRITE-PATH".
    """
    lines = _GENERATE.read_text(encoding="utf-8").splitlines()
    expected_by_fn = {
        "_assert_destructive_action_allowed": "DESTRUCTIVE",
        "_assert_security_intelligence_fresh": "INTEL-FRESHNESS",
    }
    checked = 0
    for i, line in enumerate(lines):
        if "[SEC-GATE/" not in line:
            continue
        raiser = next(
            (fn for back in range(1, 12) if i - back >= 0
             for fn in expected_by_fn
             if fn in lines[i - back]),
            None,
        )
        assert raiser, f"line {i+1}: no _assert_* call found above the gate message"
        assert expected_by_fn[raiser] in line, (
            f"line {i+1}: code names the wrong gate — raised by {raiser}, message is {line.strip()}"
        )
        checked += 1
    assert checked == 4, f"expected 4 gate sites, found {checked}"


@pytest.mark.parametrize("code", sorted(_EXPECTED_GATE_CODES))
def test_gate_codes_are_shaped_for_grepping(code):
    """Bracketed, uppercase, no spaces — greppable from a CI log without a regex."""
    assert code.startswith("[SEC-GATE/") and code.endswith("]")
    assert " " not in code


# --- the redirect boundary covers pre-dispatch output ----------------------

def test_the_json_redirect_wraps_the_whole_dispatch_not_just_the_pipeline():
    """Regression guard for the --self banner that re-broke the contract.

    Wrapping only `run_generate` left every print issued before dispatch on real stdout; on the
    --self path `cli/app.py`'s "Self-maintenance mode:" banner landed ahead of the JSON and
    `json.load(sys.stdin)` failed at line 1 again. The boundary belongs in `main`.
    """
    from agentteams.cli import app

    source = inspect.getsource(app.main)
    assert "run_with_json_stdout" in source, "main must own the redirect boundary"
    assert "_main_dispatch" in source


def test_the_wrapper_is_reentrant():
    """main and run_generate both wrap; a naive second entry would stash the redirected stdout."""
    import sys

    from agentteams.cli import json_mode

    class _Args:
        json = True
        dry_run = True

    seen = {}

    def _outer(args, *rest):
        def _inner(a, *r):
            seen["nested_stash"] = json_mode.json_stdout()
            return 0
        return json_mode.run_with_json_stdout(_inner, args)

    real = sys.stdout
    json_mode.run_with_json_stdout(_outer, _Args())
    assert seen["nested_stash"] is real, "nested entry must not re-stash the redirected stream"
