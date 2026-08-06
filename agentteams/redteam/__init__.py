"""agentteams.redteam — the standing red-team audit.

A deterministic attack battery, plus six checks that evaluate the red team itself, run on a
daily cadence. It **measures and reports; it never remediates**: an unattended job that writes
remediation code is a larger risk than the one it closes.

Entry points::

    agentteams --redteam --redteam-probes tests.constitutional_redteam_battery
    agentteams --redteam --dry-run                     # computes everything, writes nothing
    agentteams --redteam --accept-probe-baseline ...   # operator only; costs a reviewed diff

    from agentteams.redteam import cycle
    result = cycle.run_cycle(root, probe_module_path=..., report_dir=..., generated_at=...)

Three exit codes, kept distinguishable because collapsing the third into the first is the most
dangerous outcome available here — a battery whose controls failed reports "no exploits"
exactly as loudly as one that found none:

* ``0`` clean
* ``1`` findings — a measured attack is live, or phase 6 found a defect in the red team
* ``2`` **harness broken** — a failed control probe, an unimportable probe module, a corpus
  claim that no longer matches the scanner, a run that modified the live agent tree, or a death
  by traceback. Indeterminate is not a pass, and 2 outranks 1.

**This package contains no ``except`` clauses**, asserted by
``tests/test_redteam_audit_workflow.py``. A probe that raises propagates; the shell driver
classifies a traceback death as harness-broken. Catching it here to synthesise an exit code
would recreate the ``PROBE-ERROR`` row the battery's own comment warns about — it would let a
battery of broken probes finish and report all-clear.

Procedure: ``references/redteam-audit.procedure.md``.
Methodology, shipped to every generated team:
``agentteams/templates/universal/redteam-methodology.reference.template.md``.
"""

# Deliberately NO re-exports. `cycle` imports `report`, `runner` and `selfaudit`, so
# eager imports here re-enter this partially-initialised package and
# `tests/test_living_doc_and_cycles.py::test_this_package_has_no_load_time_cycles`
# fails. Import the submodule you need:
#
#     from agentteams.redteam import cycle, registry
