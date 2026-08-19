"""Tests for agentteams/provenance.py — the reusable provenance stamp."""

from __future__ import annotations

import json

from agentteams.provenance import Provenance


def test_stamp_records_generator_and_timestamp():
    p = Provenance(generator="x.py", generated_at="2026-08-18T00:00:00+00:00")
    d = p.to_dict()
    assert d["generator"] == "x.py"
    assert d["generated_at"] == "2026-08-18T00:00:00+00:00"
    assert d["provisional"] == []  # empty, explicit


def test_provisional_is_explicit_not_defaulted_to_reassurance():
    # A stamp with no provisional notes must SAY so, not imply "verified".
    p = Provenance(generator="x.py", generated_at="t")
    md = p.to_markdown()
    assert "Provisional: none declared (deliberate)." in md
    p2 = Provenance(generator="x.py", generated_at="t", provisional=["snapshot only"])
    assert "snapshot only" in p2.to_markdown()
    assert "none declared" not in p2.to_markdown()


def test_input_file_digest(tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("hello")
    p = Provenance(generator="x.py", generated_at="t").with_input_files(src=f)
    assert p.inputs["src"] != "<missing>" and len(p.inputs["src"]) == 16
    missing = Provenance(generator="x.py", generated_at="t").with_input_files(gone=tmp_path / "nope")
    assert missing.inputs["gone"] == "<missing>"


def test_sidecar_written_next_to_artifact(tmp_path):
    art = tmp_path / "ratings.csv"
    art.write_text("model,score\n")
    p = Provenance(generator="g", generated_at="t", provisional=["p"])
    sidecar = p.write_sidecar(art)
    assert sidecar.name == "ratings.csv.provenance.json"
    loaded = json.loads(sidecar.read_text())
    assert loaded["generator"] == "g" and loaded["provisional"] == ["p"]


def test_json_is_deterministic_given_inputs():
    p = Provenance(generator="g", generated_at="t", inputs={"b": "2", "a": "1"})
    # sort_keys makes it stable regardless of insertion order
    assert Provenance(generator="g", generated_at="t", inputs={"a": "1", "b": "2"}).to_json() == p.to_json()
