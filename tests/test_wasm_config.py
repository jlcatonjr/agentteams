"""P01 D2: the brief's ``wasm_sandbox`` block is validated fail-closed and normalised (``wasm_config``).

* Absent block -> WASM off, all languages, ``DEFAULT_LIMITS``, no grants, ``native_exec="replace"``, empty keep.
* Every malformed, unknown or out-of-bounds value refuses with a ``ValueError`` naming the key path; nothing is
  clamped or ignored.
* Read paths: absolute, ``~``, ``..``, the whole project, the control plane (and its ancestors), credential
  material, governance roots, characters outside the guest-name grammar, and symlinks are refused.
* Normalisation: sorted, de-duplicated, trailing ``/`` stripped, languages in ``LANGUAGES`` order.
"""

from __future__ import annotations

import os

import pytest

from agentteams import sandbox_state as ss
from agentteams import wasm_config as wc
from agentteams.wasm_exec import DEFAULT_LIMITS, LIMIT_BOUNDS, Limits
from agentteams.wasm_guests import LANGUAGES

SLUGS = ("orchestrator", "primary-producer", "analyst", "navigator", "security")


def _norm(raw, root=None):
    return wc.normalize_wasm_sandbox(raw, agent_slugs=SLUGS, project_root=root)


def _refused(raw, match, root=None):
    with pytest.raises(ValueError, match=match):
        _norm(raw, root)


def test_absent_block_is_the_off_default():
    cfg = _norm(None)
    assert cfg == wc.default_wasm_config(SLUGS)
    assert (cfg.enabled, cfg.languages, cfg.limits, dict(cfg.agents)) == (False, LANGUAGES, DEFAULT_LIMITS, {})
    # D12: the default keep (navigator, git-operations) restricted to the team (no git-operations in SLUGS).
    assert (cfg.native_exec, cfg.native_exec_keep, cfg.model_substrate) == ("replace", ("navigator",), None)
    assert wc.default_wasm_config().native_exec_keep == ()


def test_empty_block_is_the_same_defaults():
    assert _norm({}) == wc.default_wasm_config(SLUGS)


def test_languages_mirror_is_the_p04_tuple():
    assert ss.WASM_LANGUAGES == LANGUAGES


@pytest.mark.parametrize("raw, match", [
    ([], r"wasm_sandbox must be an object"),
    ({"enabled": "true"}, r"wasm_sandbox\.enabled must be true or false"),
    ({"enabled": 1}, r"wasm_sandbox\.enabled"),
    ({"languages": []}, r"wasm_sandbox\.languages must be a non-empty list"),
    ({"languages": True}, r"wasm_sandbox\.languages must be a non-empty list"),
    ({"languages": ["python", "python"]}, r"wasm_sandbox\.languages lists a language twice"),
    ({"languages": ["rust"]}, r"wasm_sandbox\.languages\[0\] = 'rust'"),
    ({"limits": {"fuel": 0}}, r"wasm_sandbox\.limits\.fuel = 0 is outside"),
    ({"limits": {"fuel": True}}, r"wasm_sandbox\.limits\.fuel must be an integer"),
    ({"limits": {"fuel": 1.5}}, r"wasm_sandbox\.limits\.fuel must be an integer"),
    ({"limits": {"fuel": 10**15 + 1}}, r"wasm_sandbox\.limits\.fuel"),
    ({"limits": {"memory_mib": 4097}}, r"wasm_sandbox\.limits\.memory_mib = 4097 is outside \[1, 4096\]"),
    ({"limits": {"memory_mib": 0}}, r"wasm_sandbox\.limits\.memory_mib"),
    ({"limits": {"timeout_s": 3601}}, r"wasm_sandbox\.limits\.timeout_s = 3601 is outside \[1, 3600\]"),
    ({"limits": {"max_memory_bytes": 1 << 20}}, r"wasm_sandbox\.limits has unknown key"),
    ({"limits": []}, r"wasm_sandbox\.limits must be an object"),
    ({"network": True}, r"wasm_sandbox has unknown key\(s\) \['network'\]"),
    ({"native_exec": "off"}, r"wasm_sandbox\.native_exec must be one of replace, deny, keep"),
    ({"model_substrate": True}, r"wasm_sandbox\.model_substrate"),
    ({"model_substrate": "maybe"}, r"wasm_sandbox\.model_substrate"),
])
def test_invalid_shapes_refuse_naming_the_key(raw, match):
    _refused(raw, match)


def test_brief_bounds_are_limit_bounds_rounded_inward():
    lo, hi = LIMIT_BOUNDS["max_memory_bytes"]
    assert wc._brief_limit_bounds() == {"fuel": LIMIT_BOUNDS["fuel"], "memory_mib": (-(-lo // (1 << 20)), hi >> 20),
                                        "timeout_s": LIMIT_BOUNDS["timeout_s"]}
    cfg = _norm({"limits": {"fuel": 10**15, "memory_mib": 4096, "timeout_s": 3600}})
    assert cfg.limits == Limits(10**15, 4096 << 20, 3600)
    assert _norm({"limits": {"timeout_s": 5}}).limits == Limits(DEFAULT_LIMITS.fuel, DEFAULT_LIMITS.max_memory_bytes, 5)


def test_unknown_agent_slugs_refuse():
    _refused({"agents": {"ghost": {"scratch": True}}}, r"wasm_sandbox\.agents key names 'ghost'")
    _refused({"agents": {"Bad_Slug": {"scratch": True}}}, r"is not an agent slug")
    _refused({"native_exec_keep": ["ghost"]}, r"wasm_sandbox\.native_exec_keep\[0\] names 'ghost'")


@pytest.mark.parametrize("slug", ["orchestrator", "team-builder"])
def test_exempt_agents_may_not_be_kept(slug):
    _refused({"native_exec_keep": [slug]}, rf"native_exec_keep\[0\] = '{slug}' is exempt by design")


def test_keep_is_sorted_and_refuses_duplicates():
    assert _norm({"native_exec_keep": ["security", "analyst"]}).native_exec_keep == ("analyst", "security")
    _refused({"native_exec_keep": ["analyst", "analyst"]}, r"lists an agent twice")
    _refused({"native_exec_keep": "analyst"}, r"must be a list")


def test_grant_entry_shapes():
    _refused({"agents": {"analyst": {}}}, r"wasm_sandbox\.agents\.analyst is an empty grant")
    _refused({"agents": {"analyst": {"read_paths": [], "scratch": False}}}, r"empty grant")
    _refused({"agents": {"analyst": {"scratch": "yes"}}}, r"agents\.analyst\.scratch must be true or false")
    _refused({"agents": {"analyst": {"read_paths": "docs"}}}, r"agents\.analyst\.read_paths must be a list")
    _refused({"agents": {"analyst": {"network": True}}}, r"agents\.analyst has unknown key")
    _refused({"agents": {"analyst": {"scratch_dir": "/tmp"}}}, r"agents\.analyst has unknown key")


@pytest.mark.parametrize("bad, match", [
    ("/etc", "must be project-relative"),
    ("~/x", "must be project-relative"),
    ("../x", r"'\.\.' segment"),
    ("docs/../../x", r"'\.\.' segment"),
    (".", "names the whole project"),
    ("", "empty"),
    ("./docs", "guest-name grammar"),
    ("docs//x", "guest-name grammar"),
    (".git/x", r"control plane \(\.git\)"),
    (".GIT/config", r"control plane"),
    (".agentteams/wasm", r"control plane \(\.agentteams\)"),
    (".claude", "control plane"),
    (".github", r"control plane \(\.github/agents\)"),
    (".github/agents/x.md", "control plane"),
    ("sandbox", "control plane"),
    (".env", "credential material"),
    ("config/.env.local", "credential material"),
    ("keys/id_rsa", "credential material"),
    ("certs/server.pem", "credential material"),
    ("secrets/api.key", "credential material"),
    ("home/.ssh", "credential material"),
    ("references/authorized-verify-keys", "governance / trust root"),
    ("references/security-decisions.log.csv", "governance / trust root"),
    ("docs/CLAUDE.md", "governance / trust root"),
    ("a;b", "forbidden character"),
    ("data/my file", "guest-name grammar"),
    ("data/*", "forbidden character"),
    (" docs", "whitespace"),
    (7, "must be a string"),
])
def test_read_path_refusals_name_the_entry(bad, match):
    raw = {"agents": {"analyst": {"read_paths": ["docs", bad]}}}
    _refused(raw, r"wasm_sandbox\.agents\.analyst\.read_paths\[1\]" + ".*" + match if bad != "" else match)


def test_symlink_leading_out_of_the_project_is_refused(tmp_path):
    project, outside = tmp_path / "p", tmp_path / "outside"
    (project / "data").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside, project / "data" / "link")
    os.symlink(project / "data", project / "alias")  # inside, but still not the real path
    _refused({"agents": {"analyst": {"read_paths": ["data/link"]}}}, r"read_paths\[0\].*resolves through a symlink",
             root=project)
    _refused({"agents": {"analyst": {"read_paths": ["alias"]}}}, r"resolves through a symlink", root=project)
    assert _norm({"agents": {"analyst": {"read_paths": ["data"]}}}, root=project).agents["analyst"].read_paths == (
        "data",)


def test_normalisation_sorts_dedupes_and_strips_trailing_slashes():
    cfg = _norm({"enabled": True, "languages": ["javascript", "module"],
                 "agents": {"primary-producer": {"read_paths": ["src/", "docs", "src"], "scratch": True},
                            "analyst": {"read_paths": ["data"]}}})
    assert cfg.languages == ("module", "javascript")
    assert list(cfg.agents) == ["analyst", "primary-producer"]
    assert cfg.agents["primary-producer"] == wc.AgentWasmGrant(read_paths=("docs", "src"), scratch=True)
    assert cfg.agents["analyst"] == wc.AgentWasmGrant(read_paths=("data",), scratch=False)


def test_model_substrate_is_accepted_and_inert():
    for value in ("on", "off"):
        cfg = _norm({"model_substrate": value})
        assert cfg.model_substrate == value and cfg.enabled is False


def test_grants_validate_while_disabled():
    cfg = _norm({"enabled": False, "agents": {"analyst": {"scratch": True}}})
    assert cfg.agents["analyst"].scratch and not cfg.enabled


def test_record_round_trips_and_refuses_tampering():
    cfg = _norm({"enabled": True, "limits": {"memory_mib": 128}, "agents": {"analyst": {"read_paths": ["data"]}},
                 "native_exec": "deny", "native_exec_keep": ["security"], "model_substrate": "on"})
    rec = cfg.to_record()
    assert wc.config_from_record(rec, agent_slugs=SLUGS) == cfg
    with pytest.raises(ValueError):
        wc.config_from_record({**rec, "limits": {**rec["limits"], "max_memory_bytes": (1 << 20) + 1}},
                              agent_slugs=SLUGS)
    with pytest.raises(ValueError, match="unknown key"):
        wc.config_from_record({**rec, "network": True}, agent_slugs=SLUGS)
    with pytest.raises(ValueError, match="normalised form"):
        wc.config_from_record({**rec, "languages": ["python", "module"]}, agent_slugs=SLUGS)
    with pytest.raises(ValueError, match="control plane"):
        wc.config_from_record({**rec, "agents": {"analyst": {"read_paths": [".git"], "scratch": False}}},
                              agent_slugs=SLUGS)


@pytest.mark.parametrize("bad", ["ON", True, 1, "", "yes"])
def test_cli_value_fails_closed(bad):
    with pytest.raises(ValueError, match="--wasm-sandbox"):
        wc.validate_wasm_sandbox_cli(bad)
    assert wc.validate_wasm_sandbox_cli(None) is None and wc.validate_wasm_sandbox_cli("on") == "on"
