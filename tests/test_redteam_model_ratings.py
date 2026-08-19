"""test_redteam_model_ratings.py — guards on the ratings aggregation (roadmap R1).

These tests exist to keep two easy-to-reintroduce mistakes out:

* **Availability silently re-measuring operability.** `availability` asks only "did the endpoint
  respond usefully at all" — a LOW bar (>=1 parseable verdict, no transport failure). If someone
  raises `AVAILABILITY_PARSEABLE_FLOOR` back toward the corpus size, the field stops meaning
  availability and starts re-measuring `operability` under a second name. The floor is asserted
  low here so that change fails loudly.
* **Repeat runs getting discarded again.** R1's whole point is that a model measured twice keeps
  both measurements (`runs == 2`) even though its SCORE still comes FIRST-WINS from the primary
  run. If the retry run dirs fall out of `RUN_DIRS`, or `collect()` reverts to overwriting, the
  repeated-model count drops back to 1 and this catches it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "redteam_model_ratings", REPO_ROOT / "scripts" / "redteam_model_ratings.py"
)
rm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rm)  # type: ignore[union-attr]


def test_availability_floor_is_the_honest_low_bar():
    # "responded usefully at all" == 1 parseable verdict. Anything higher conflates availability
    # with operability. This is a deliberate tripwire, not a style preference.
    assert rm.AVAILABILITY_PARSEABLE_FLOOR == 1


def test_collect_rows_carry_runs_and_availability():
    rows = rm.collect()
    if not rows:
        pytest.skip("no local redteam-matrix run artifacts (RUN_DIRS under tmp/ are gitignored; absent in CI)")
    for row in rows:
        assert row["runs"] >= 1, f"{row['model']} has runs<1"
        assert 0.0 <= row["availability"] <= 1.0, f"{row['model']} availability out of range"


def test_repeat_measured_models_retain_both_runs():
    # These four models were measured twice (a primary run + a retry/replication dir). R1 keeps both
    # measurements; if a retry dir is dropped from RUN_DIRS the count silently falls back to 1.
    rows = {r["model"]: r for r in rm.collect()}
    if not rows:
        pytest.skip("no local redteam-matrix run artifacts (RUN_DIRS under tmp/ are gitignored; absent in CI)")
    repeated = [
        "mistralai/mistral-large-2512",
        "qwen/qwen3-30b-a3b",
        "deepseek/deepseek-v4-flash",
        "nvidia/nemotron-3-super-120b-a12b",
    ]
    for model in repeated:
        assert model in rows, f"{model} missing from ratings"
        assert rows[model]["runs"] == 2, f"{model} lost its repeat run (runs={rows[model]['runs']})"


def test_repeat_runs_do_not_duplicate_or_perturb_published_rows():
    # A repeat run must add a measurement, not a row: each model appears exactly once in the
    # published CSV, and its score is a single deterministic integer in [0,100] (FIRST-WINS keeps
    # the primary run authoritative — a retry cannot average or double a model's score).
    import csv

    csv_path = REPO_ROOT / "references" / "openweights-security-model-ratings.csv"
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    models = [r["model"] for r in rows]
    assert len(models) == len(set(models)), "a model appears twice — a repeat run leaked a row"
    for r in rows:
        for field in ("security_score", "reliability_score"):
            assert 0 <= float(r[field]) <= 100, f"{r['model']} {field} out of range"
        # The four twice-measured models must show runs==2 in the published CSV, not a duplicate row.
        if r["model"] in {
            "mistralai/mistral-large-2512",
            "qwen/qwen3-30b-a3b",
            "deepseek/deepseek-v4-flash",
            "nvidia/nemotron-3-super-120b-a12b",
        }:
            assert int(r["runs"]) == 2, f"{r['model']} runs != 2 in published CSV"
