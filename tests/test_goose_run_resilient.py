"""Offline unit tests for scripts/goose-run-resilient.py, plus a skip-by-default
live probe.

Pure-logic tests are deterministic and filesystem/network-free (mirrors the
importlib loader pattern of test_goose_openrouter_preflight.py / test_verify_env.py).
The live probe actually runs the script against a real goose subprocess and a real
provider — skip-by-default, same two independent skipif gates as
test_goose_live_delegation.py, so CI / this repo (no OPENROUTER_API_KEY) stay
offline-green.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

from . import conftest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "goose-run-resilient.py"


def _load():
    spec = importlib.util.spec_from_file_location("goose_run_resilient", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not perform any network I/O or subprocess calls
    return module


grr = _load()


def _jsonl(*entries) -> list[str]:
    return [json.dumps(e) for e in entries]


# --- classify_request_log ---------------------------------------------------


class TestClassifyRequestLog:
    def test_empty_lines_is_alive_not_dead(self):
        out = grr.classify_request_log([])
        assert out["ok"] is True
        assert out["dead"] is False
        assert out["reason"] == "no_content_blocks_found"

    def test_only_blank_and_whitespace_lines_is_alive(self):
        out = grr.classify_request_log(["", "   ", "\n"])
        assert out["dead"] is False
        assert out["reason"] == "no_content_blocks_found"

    def test_malformed_json_lines_are_skipped_not_dead(self):
        # Fail-closed: garbage input must never be classified as a dead turn.
        out = grr.classify_request_log(["{not valid json", "also not json}"])
        assert out["ok"] is True
        assert out["dead"] is False
        assert out["reason"] == "no_content_blocks_found"

    def test_non_dict_json_lines_are_skipped(self):
        out = grr.classify_request_log(["[1, 2, 3]", '"just a string"', "42"])
        assert out["dead"] is False
        assert out["reason"] == "no_content_blocks_found"

    def test_thinking_only_is_dead(self):
        # The confirmed real-world failure signature: a tool call trapped inside
        # <tool_call> text within a "thinking" block, no text/toolRequest emitted.
        lines = _jsonl(
            {"data": {"content": [{"type": "thinking", "thinking": "<tool_call>...</tool_call>"}]}},
            {"data": None, "usage": {"output_tokens": 86}},
        )
        out = grr.classify_request_log(lines)
        assert out["dead"] is True
        assert out["reason"] == "dead_turn"
        assert out["has_text"] is False
        assert out["has_tool_request"] is False

    def test_untagged_single_token_dead_turn(self):
        # Not every dead turn carries the <tool_call> tag — some are just a single
        # stray thinking token with nothing after. Must still classify as dead.
        lines = _jsonl({"data": {"content": [{"type": "thinking", "thinking": "Let"}]}})
        out = grr.classify_request_log(lines)
        assert out["dead"] is True

    def test_text_content_is_alive(self):
        lines = _jsonl(
            {"data": {"content": [{"type": "thinking", "thinking": "reasoning..."}]}},
            {"data": {"content": [{"type": "text", "text": "Here is the answer."}]}},
        )
        out = grr.classify_request_log(lines)
        assert out["dead"] is False
        assert out["has_text"] is True

    def test_tool_request_content_is_alive(self):
        lines = _jsonl({"data": {"content": [{"type": "toolRequest", "id": "call_1"}]}})
        out = grr.classify_request_log(lines)
        assert out["dead"] is False
        assert out["has_tool_request"] is True

    def test_missing_data_key_does_not_crash_and_is_alive(self):
        out = grr.classify_request_log(_jsonl({"unrelated": "entry"}))
        assert out["dead"] is False

    def test_data_content_not_a_list_does_not_crash(self):
        out = grr.classify_request_log(_jsonl({"data": {"content": "not-a-list"}}))
        assert out["dead"] is False

    def test_content_block_not_a_dict_does_not_crash(self):
        out = grr.classify_request_log(_jsonl({"data": {"content": ["not-a-dict", 42]}}))
        assert out["dead"] is False


# --- request_matches_prompt (ring-buffer race guard) ------------------------


class TestRequestMatchesPrompt:
    def test_matching_prompt_returns_true(self):
        # The log contains the full prompt verbatim (it's what was actually sent);
        # the check matches the first-40-chars substring against that log content.
        line = json.dumps({"input": {"messages": [{"role": "user", "content": "check disk usage now please"}]}})
        assert grr.request_matches_prompt(line, "check disk usage now please") is True

    def test_non_matching_prompt_returns_false(self):
        line = json.dumps({"input": {"messages": [{"role": "user", "content": "unrelated request"}]}})
        assert grr.request_matches_prompt(line, "check disk usage now please") is False

    def test_malformed_json_fails_closed_to_false(self):
        assert grr.request_matches_prompt("{not json", "anything") is False

    def test_missing_input_key_fails_closed_to_false(self):
        assert grr.request_matches_prompt(json.dumps({"no_input": True}), "anything") is False

    def test_empty_prompt_substring_fails_closed_to_false(self):
        line = json.dumps({"input": {"messages": [{"role": "user", "content": "x"}]}})
        assert grr.request_matches_prompt(line, "") is False


# --- pick_newest_candidate ---------------------------------------------------


class TestPickNewestCandidate:
    def test_empty_list_returns_none(self):
        assert grr.pick_newest_candidate([], after_mtime=0.0) is None

    def test_all_before_cutoff_returns_none(self):
        assert grr.pick_newest_candidate([("a.jsonl", 10.0), ("b.jsonl", 20.0)], after_mtime=25.0) is None

    def test_picks_newest_at_or_after_cutoff(self):
        candidates = [("old.jsonl", 5.0), ("new.jsonl", 30.0), ("mid.jsonl", 15.0)]
        assert grr.pick_newest_candidate(candidates, after_mtime=10.0) == "new.jsonl"

    def test_cutoff_is_inclusive(self):
        assert grr.pick_newest_candidate([("exact.jsonl", 10.0)], after_mtime=10.0) == "exact.jsonl"


# --- CLI surface: no hardcoded provider/model default -----------------------


class TestNoHardcodedProviderDefault:
    def test_source_has_no_hardcoded_default_model_constant(self):
        # Static guard against regressing the "no matter the source of LLM compute"
        # requirement: no module-level default like _DEFAULT_MODEL = "qwen/...".
        source = _SCRIPT.read_text(encoding="utf-8")
        assert not re.search(r'default\s*=\s*["\']qwen/', source)
        assert not re.search(r'default\s*=\s*["\']openrouter["\']', source)

    def test_max_continues_default_documented_as_floor_not_margin(self):
        # The source report's own caveat must ship in the artifact itself.
        source = _SCRIPT.read_text(encoding="utf-8")
        assert "floor" in source.lower()
        assert "not a comfortable margin" in source.lower()


# --- live probe (credential-gated, skip-by-default) --------------------------
# Shared skip-gate helpers (tests/conftest.py) — see that file's docstring for
# why this was extracted (code-hygiene CH-08: was duplicated 3x across test files).


@conftest.skip_no_goose
@conftest.skip_no_openrouter_key
class TestLiveProbe:
    def test_trivial_prompt_produces_output_first_try(self):
        proc = subprocess.run(
            ["python3", str(_SCRIPT), "-t", "Reply with exactly: OK",
             "--provider", "openrouter", "--json"],
            capture_output=True, text=True, timeout=90, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        report = json.loads(proc.stdout)
        assert report["final_dead"] is False
        assert report["final_stdout"].strip()
