"""Tests for F-CODEIDX — code & API vector index (Phase A: local-script)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import code_index as ci
from agentteams import memory_index as mi

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "code-index.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# ------------------------------ tokenizer ------------------------------

def test_tokenizer_keeps_short_ids_and_splits_identifiers():
    toks = ci._tokenize_code(
        "import os\ndef batch_update(inputPath):\n    return os.path.join(inputPath)"
    )
    # 2-char identifiers the prose tokenizer drops are kept here
    assert "os" in toks
    # snake_case is split AND the whole compound retained
    assert "batch" in toks and "update" in toks and "batch_update" in toks
    # camelCase is split
    assert "input" in toks and "path" in toks
    # dotted import path retained whole (up to the call paren)
    assert "os.path.join" in toks


def test_tokenizer_no_double_emission_for_plain_identifier():
    # A plain single-part identifier must not be emitted twice (tf inflation).
    toks = ci._tokenize_code("requests requests")
    assert toks.count("requests") == 2  # once per occurrence, not four times


def test_tokenizer_prose_words_match_memory_tokenizer_when_simple():
    # For plain lowercase words >=3 chars not in either stopword set, the
    # code tokenizer and the prose tokenizer agree — the basis for parity.
    text = "alpha bravo charlie delta charlie alpha"
    assert ci._tokenize_code(text) == mi._tokenize(text)


# ------------------------------ build ------------------------------

def test_build_partition_is_schema_valid(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    units = [
        {"path": "scripts/a.py", "text": "import requests\n\ndef fetch_user(uid):\n    '''Fetch a user.'''\n    return requests.get(uid)\n", "language": "python", "source_kind": "local-script"},
        {"path": "scripts/b.sh", "text": "#!/bin/sh\ndeploy() {\n  echo deploying\n}\n", "language": "shell", "source_kind": "local-script"},
    ]
    part = ci.build_code_partition(units, source_kind="local-script", project_name="demo", framework="copilot-vscode")
    jsonschema.validate(part, _schema())
    assert part["N"] == 2
    assert part["artifact_type"] == "code-index-partition"
    assert all(d["source_kind"] == "local-script" for d in part["documents"])
    # symbol-aware passage extraction (AST) for python
    a_doc = next(d for d in part["documents"] if d["path"] == "scripts/a.py")
    assert any("fetch_user" in p for p in a_doc["passages"])


def test_manifest_is_schema_valid():
    jsonschema = pytest.importorskip("jsonschema")
    part = ci.build_code_partition(
        [{"path": "x.py", "text": "def go():\n    return 1\n", "language": "python", "source_kind": "local-script"}],
        source_kind="local-script",
    )
    man = ci.build_manifest({"local": part}, project_name="demo", framework="copilot-vscode")
    jsonschema.validate(man, _schema())
    assert man["artifact_type"] == "code-index-manifest"
    # trigger metadata lives ONLY in the cache manifest (C2-1 / C-4)
    assert "trigger_sources" in man and "query_entrypoints" in man
    assert man["partitions"]["local"]["source_kind"] == "local-script"


def test_empty_units_yield_empty_valid_partition():
    jsonschema = pytest.importorskip("jsonschema")
    part = ci.build_code_partition([], source_kind="local-script")
    jsonschema.validate(part, _schema())
    assert part["N"] == 0 and part["documents"] == [] and part["postings"] == {}


def test_build_is_deterministic_by_content_fingerprint():
    units = [
        {"path": "z.py", "text": "def zulu_handler(payload):\n    return process(payload)\n", "language": "python", "source_kind": "local-script"},
        {"path": "a.py", "text": "def alpha_handler(request):\n    return dispatch(request)\n", "language": "python", "source_kind": "local-script"},
    ]
    p1 = ci.build_code_partition(units, source_kind="local-script")
    p2 = ci.build_code_partition(list(reversed(units)), source_kind="local-script")
    # order-independent: documents sorted by (source_kind, path, symbol)
    assert [d["path"] for d in p1["documents"]] == ["a.py", "z.py"]
    assert ci.partition_content_fingerprint(p1) == ci.partition_content_fingerprint(p2)


# ------------------------------ query ------------------------------

def _corpus():
    return ci.build_code_partition([
        {"path": "auth.py", "text": "def login(user, password):\n    '''Authenticate a user login.'''\n    return check_password(user, password)\n", "language": "python", "source_kind": "local-script"},
        {"path": "net.py", "text": "import requests\n\ndef download(url):\n    '''Download a file over http.'''\n    return requests.get(url)\n", "language": "python", "source_kind": "local-script"},
    ], source_kind="local-script")


@pytest.mark.parametrize("strategy", ["lexical", "vector"])
def test_query_ranks_relevant_doc_first(strategy):
    part = _corpus()
    hits = ci.query_partition(part, "user login password", k=5, strategy=strategy)
    assert hits and hits[0]["path"] == "auth.py"
    assert hits[0]["snippets"]


def test_query_unknown_strategy_raises():
    with pytest.raises(ValueError):
        ci.query_partition(_corpus(), "x", strategy="bogus")


def test_query_empty_partition_returns_empty():
    empty = ci.build_code_partition([], source_kind="local-script")
    assert ci.query_partition(empty, "anything") == []


def test_kind_filter_across_partitions():
    local = _corpus()
    apidoc = ci.build_code_partition(
        [{"path": "requests", "text": "requests http session get post download url", "source_kind": "api-doc"}],
        source_kind="api-doc",
    )
    parts = {"local": local, "api-docs": apidoc}
    doc_only = ci.query_partitions(parts, "download url", k=5, strategy="lexical", kind="doc")
    assert doc_only and all(h["source_kind"] == "api-doc" for h in doc_only)
    local_only = ci.query_partitions(parts, "download url", k=5, strategy="lexical", kind="local")
    assert local_only and all(h["source_kind"] == "local-script" for h in local_only)


# ------------------------------ staleness ------------------------------

def test_local_staleness_hash_gate(tmp_path):
    f = tmp_path / "s.py"
    f.write_text("def f():\n    return 1\n")
    part = ci.build_code_partition(ci.local_units([f]), source_kind="local-script")
    assert ci.is_partition_stale(part, [f]) is False
    f.write_text("def f():\n    return 2\n")
    assert ci.is_partition_stale(part, [f]) is True


def test_api_staleness_dependency_fingerprint():
    dep1 = ci.dependency_fingerprint(["deps"], ["requests"], [("requests", "2.31.0")])
    dep2 = ci.dependency_fingerprint(["deps"], ["requests"], [("requests", "2.32.0")])
    part = ci.build_code_partition(
        [{"path": "requests", "text": "session get post", "source_kind": "api-doc"}],
        source_kind="api-doc",
        dependency_fingerprint=dep1,
    )
    assert ci.is_partition_stale(part, dependency_fingerprint=dep1) is False
    # a version bump changes the fingerprint even though no local file changed
    assert ci.is_partition_stale(part, dependency_fingerprint=dep2) is True


def test_api_staleness_new_import_detected():
    dep1 = ci.dependency_fingerprint(["deps"], ["requests"], [("requests", "2.31.0")])
    dep2 = ci.dependency_fingerprint(["deps"], ["requests", "httpx"], [("requests", "2.31.0")])
    part = ci.build_code_partition(
        [{"path": "requests", "text": "x", "source_kind": "api-module"}],
        source_kind="api-module",
        dependency_fingerprint=dep1,
    )
    assert ci.is_partition_stale(part, dependency_fingerprint=dep2) is True


# ------------------------------ atomic write ------------------------------

def test_atomic_write_leaves_no_tmp(tmp_path):
    part = _corpus()
    out = tmp_path / "sub" / "local.json"
    ci.atomic_write_json(out, part)
    assert json.loads(out.read_text())["N"] == 2
    assert not out.with_name("local.json.tmp").exists()


# ------------------------------ scoring parity (R2-M3) ------------------------------

def _synthetic_docs(tmp_path):
    # Plain lowercase words >=4 chars, no separators, not in either stopword
    # set (avoiding e.g. "echo", a shell keyword) -> code and prose tokenizers
    # produce identical token streams, so the two scorers compare apples-to-apples.
    texts = {
        "d1.py": "apple mango cherry apple grape apple mango",
        "d2.py": "cherry grape lemon olive cherry grape",
        "d3.py": "peach berry melon peach berry apple mango cherry",
    }
    paths = []
    for name, body in texts.items():
        p = tmp_path / name
        p.write_text(body)
        paths.append(p)
    return paths


@pytest.mark.parametrize("query", ["apple mango", "cherry grape", "peach berry melon"])
def test_scoring_parity_lexical(tmp_path, query):
    """code_index BM25 == memory_index BM25 on identical token streams (R2-M3).

    Guards the deliberate copy of the scorer against silent drift without
    importing from or refactoring the shipped memory_index module.
    """
    paths = _synthetic_docs(tmp_path)
    mem = mi.build_memory_index(paths)
    code = ci.build_code_partition(ci.local_units(paths), source_kind="local-script")
    mh = mi.query_index(mem, query, k=10, strategy="lexical")
    ch = ci.query_partition(code, query, k=10, strategy="lexical")
    assert [(h["path"], h["score"]) for h in mh] == [(h["path"], h["score"]) for h in ch]


@pytest.mark.parametrize("query", ["apple mango", "cherry grape", "peach berry melon"])
def test_scoring_parity_vector(tmp_path, query):
    paths = _synthetic_docs(tmp_path)
    mem = mi.build_memory_index(paths)
    code = ci.build_code_partition(ci.local_units(paths), source_kind="local-script")
    mh = mi.query_index(mem, query, k=10, strategy="vector")
    ch = ci.query_partition(code, query, k=10, strategy="vector")
    assert [(h["path"], h["score"]) for h in mh] == [(h["path"], h["score"]) for h in ch]
