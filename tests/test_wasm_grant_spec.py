"""P19 parity: agentteams' canonical wasmtime grant (``wasm_exec.GRANT_SPEC``) against baseAgent's.

agentteams owns the grant (METAPLAN §6 R9). baseAgent's ``experiments/battery/wasmtime_cmd_runner.mjs`` carries the
same grant as JS literals. These tests compare the two through checked-in fixtures, so the suite never needs (or
executes) a baseAgent checkout:

* ``tests/fixtures/wasm/baseagent_wasmtime_grant.excerpt.txt`` is a verbatim excerpt of baseAgent's runner, with a
  header naming the baseAgent commit it was copied from. It is parsed as text (C-4: data, never executed).
* ``tests/fixtures/wasm/grant_corpus.json`` is baseAgent's W6 accept/refuse corpus expanded to literal flag arrays,
  each with baseAgent's own verdict, for both profiles.

Every place where agentteams deliberately differs from baseAgent is listed in :data:`INTENTIONAL_DIFFERENCES` with
its reason, and each listed difference has a test proving it is real and that agentteams is the stricter side. A
difference that is not listed fails. When a baseAgent checkout exists (``AGENTTEAMS_BASEAGENT_DIR``, else
``~/githubrepositories/baseAgent``), the fixtures are also checked against the live files; otherwise those tests skip.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import wasm_exec as W

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "wasm"
EXCERPT = FIXTURES / "baseagent_wasmtime_grant.excerpt.txt"
CORPUS = FIXTURES / "grant_corpus.json"
BA_RUNNER_REL = "experiments/battery/wasmtime_cmd_runner.mjs"
BA_PROBE_REL = "experiments/battery/test_deny_probe.mjs"

#: Spec keys with no baseAgent counterpart (metadata only). A new GRANT_SPEC key must be added here or compared.
METADATA_ONLY_KEYS = {
    "schema": "agentteams' spec version; baseAgent has no versioned spec object.",
    "wasmtime_major": "baseAgent names its wasmtime version only in comments; the pin itself is P03's.",
    "seeded_from": "provenance note pointing at baseAgent.",
    "extensions": "agentteams-only additions; see INTENTIONAL_DIFFERENCES['agentteams-mounts'].",
}

#: Every deliberate divergence from baseAgent, with its reason. Each key has a test below.
INTENTIONAL_DIFFERENCES = {
    "agentteams-mounts": "In grant mode agentteams may append '--dir host::guest' pairs derived from Grant.mounts "
                         "(D5 per-agent widening, P02 §5.2). baseAgent never passes --dir. Parity mode "
                         "(assert_grant_shape(argv, None)) refuses every --dir exactly as baseAgent does.",
    "argv-tail": "agentteams checks the whole argv and requires '--argv0 <name> -- <absolute module>'; baseAgent "
                 "checks only the six flags and passes '--argv0 <name> <module> args' with no '--' and no "
                 "absoluteness check. agentteams refuses more.",
    "grant-mode-timeout-seconds": "agentteams' Limits carry whole seconds (1..3600 s) and grant_flags emits only "
                                  "'<n>s'; an '<n>ms' timeout is accepted only in parity mode. baseAgent accepts "
                                  "'<n>ms' down to 1 ms everywhere. agentteams refuses more in grant mode.",
    "component-parity-only": "baseAgent runs composed components (--invoke); agentteams v1 refuses components in "
                             "grant mode (P02 §5.4). The component profile is in GRANT_SPEC for parity coverage only.",
    "no-env-overrides": "baseAgent's wasmtimeFlags reads BA_WASMTIME_FUEL/MAXMEM/TIMEOUT from the environment; "
                        "agentteams takes limits from the Grant only, so the environment cannot widen them.",
    "never-list-superset": "GRANT_SPEC['never'] names --preload, --allow-precompiled and --config besides baseAgent's "
                           "--dir/--env/-S inherit-network; '--dir' is qualified '(BA)' because of agentteams-mounts. "
                           "The list is documentary: both exact-shape checks refuse all of these anyway.",
}

#: Corpus cases where agentteams' parity-mode verdict deliberately differs from baseAgent's (name, profile) -> reason.
#: Empty: on the six flags, parity mode agrees with baseAgent case for case.
CORPUS_DIVERGENCES: dict[tuple[str, str], str] = {}

#: baseAgent W6 case names (test_deny_probe.mjs:149-180), so the corpus can't silently lose one.
BA_W6_NAMES = {
    "bare --dir", "bare --dir=", "bare --env", "bare -S inherit-network=y", "bare -S cli=y,inherit-network",
    "bare -S inherit-env=y", "bare -S tcplisten", "bare -S listenfd=y", "+ --dir /", "+ --dir=/tmp", "+ --env HOME",
    "+ --env=HOME", "+ --preload", "+ later -S inherit-network=y", "+ -Sinherit-network=y",
    "+ --wasi=inherit-network=y", "+ --wasi inherit-network=y", "+ -S tcplisten", "+ later -W fuel", "S http=y",
    "S tcp=y", "S udp=y", "S allow-ip-name-lookup=y", "S inherit-network=y", "S inherit-env=y",
    "S inherit-network (bare key)", "S + ,inherit-network=y", "S + ,listenfd=y", "--wasi for -S", "--wasi=<grant>",
    "-S<grant> joined", "-S before -W (reordered)", "-C cache=y", "W trap-on-grow-failure=n", "W max-instances=9",
    "W + ,max-wasm-stack", "W fuel=0", "W fuel>1e15", "W fuel=1e9 (not decimal)", "W timeout=2h", "W timeout=3601s",
    "W max-memory-size=8GiB", "W max-memory-size=1", "W fuel twice",
    "default", "W4 override", "500ms timeout", "1 MiB memory",
}

_BA_W_VAR_NAMES = {"fuel": "fuel", "timeout": "timeout", "maxMemory": "max-memory-size"}


class BaShapeChanged(AssertionError):
    """baseAgent's runner no longer has the shape the parser expects: review it, don't skip."""


# --- parsing baseAgent's runner (text only) -------------------------------------------------------------------------

def _excerpt() -> tuple[str, list[str], list[str]]:
    """Return (commit, header lines, verbatim blocks) of the excerpt fixture."""
    header, blocks, cur = [], [], None
    for line in EXCERPT.read_text(encoding="utf-8").splitlines():
        if cur is None and line.startswith("#"):
            header.append(line)
        elif re.fullmatch(r"@@ lines \d+-\d+ @@", line):
            if cur is not None:
                blocks.append("\n".join(cur))
            cur = []
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append("\n".join(cur))
    m = re.search(r"baseAgent commit: ([0-9a-f]{40})", "\n".join(header))
    assert m, "excerpt header must name the baseAgent commit it was copied from"
    return m[1], header, blocks


def _need(pattern: str, text: str, what: str, flags: int = 0) -> re.Match[str]:
    m = re.search(pattern, text, flags)
    if not m:
        raise BaShapeChanged(f"baseAgent runner changed shape ({what} not found): review it and re-copy the fixture")
    return m


def _bigint(expr: str) -> int:
    m = re.fullmatch(r"\s*(\d+)n(?:\s*\*\*\s*(\d+)n)?\s*", expr)
    if not m:
        raise BaShapeChanged(f"LIMIT_BOUNDS element {expr!r} is not '<n>n' or '<n>n ** <n>n'")
    return int(m[1]) ** int(m[2]) if m[2] else int(m[1])


def parse_ba_grant(text: str) -> dict:
    """Extract baseAgent's grant constants from runner source text into GRANT_SPEC's vocabulary.

    Args:
        text: Source of ``wasmtime_cmd_runner.mjs`` (or the excerpt fixture's blocks).

    Returns:
        A dict with the GRANT_SPEC keys that have a baseAgent counterpart, plus ``never_ba`` and ``n_flags``.

    Raises:
        BaShapeChanged: When any expected literal is missing.
    """
    s = lambda name: _need(rf'^const {name} = "([^"]*)";$', text, name, re.M)[1]  # noqa: E731
    d = _need(r'^export const DEFAULT_LIMITS = Object\.freeze\(\{ fuel: "(\d+)", maxMemory: "(\d+)", '
              r'timeout: "(\d+(?:ms|s))" \}\);$', text, "DEFAULT_LIMITS", re.M)
    b = _need(r"^export const LIMIT_BOUNDS = Object\.freeze\(\{ fuel: \[([^\]]*)\], maxMemory: \[([^\]]*)\], "
              r"timeoutMs: \[([^\]]*)\] \}\);$", text, "LIMIT_BOUNDS", re.M)
    profiles = _need(r"^const W_FIXED_BY_PROFILE = Object\.freeze\(\{ (.*) \}\);$", text, "W_FIXED_BY_PROFILE", re.M)
    w_fixed = {}
    for pair in profiles[1].split(", "):
        prof, const = _need(r"^(\w+): (W_FIXED\w*)$", pair, f"profile entry {pair!r}").groups()
        w_fixed[prof] = s(const)
    body = _need(r"export function wasmtimeFlags\([^)]*\) \{.*?return \[(.*?)\];", text, "wasmtimeFlags return",
                 re.S)[1]
    elems = [e.strip() for e in body.split("\n") if e.strip()]
    order, consts = [], {}
    for e in elems:
        m = _need(r'^"(-[A-Z])", (GRANT_C|GRANT_S|`[^`]*`),$', e, f"flag element {e!r}")
        order.append(m[1])
        consts[m[1]] = m[2]
    w_tpl = consts.get("-W", "")
    w_var = [_BA_W_VAR_NAMES.get(n, f"?{n}") for k, n in re.findall(r"([a-z-]+)=\$\{L\.(\w+)\}", w_tpl)]
    if not w_tpl.endswith(",${wFixed(profile)}`") or consts.get("-C") != "GRANT_C" or consts.get("-S") != "GRANT_S":
        raise BaShapeChanged("wasmtimeFlags no longer builds -C GRANT_C, -W <vars>,<wFixed>, -S GRANT_S")
    w_re = _need(r"const m = /(\^fuel=[^/]*\$)/\.exec\(flags\[3\]\);", text, "assertGrantFlags -W regex")[1]
    n_flags = int(_need(r"flags\.length !== (\d+)", text, "assertGrantFlags length check")[1])
    never = _need(r"never (--dir), (--env) or\s*//\s*(-S inherit-network)\.", text, "security condition comment")
    return {
        "flag_order": order,
        "C": s("GRANT_C"),
        "S": s("GRANT_S"),
        "W_variable": w_var,
        "W_fixed": w_fixed,
        "W_regex": w_re,
        "limit_bounds": {"fuel": [_bigint(x) for x in b[1].split(",")],
                         "max_memory_bytes": [_bigint(x) for x in b[2].split(",")],
                         "timeout_ms": [_bigint(x) for x in b[3].split(",")]},
        "defaults": {"fuel": int(d[1]), "max_memory_bytes": int(d[2]), "timeout": d[3]},
        "never_ba": list(never.groups()),
        "n_flags": n_flags,
    }


def _fixture_grant() -> dict:
    return parse_ba_grant("\n".join(_excerpt()[2]))


def _corpus() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _wrap(flags: list[str]) -> list[str]:
    return ["/abs/wasmtime", "run", *flags, "--argv0", "m", "--", "/abs/m.wasm"]


def _verdict(flags: list[str], profile: str) -> str:
    try:
        W.assert_grant_shape(_wrap(flags), None, profile=profile)
    except W.WasmExecError:
        return "refuse"
    return "accept"


# --- spec vs the fixture copy of baseAgent's constants --------------------------------------------------------------

def test_spec_schema_and_types() -> None:
    spec = W.GRANT_SPEC
    assert spec["schema"] == "agentteams-wasm-grant/v1"
    assert set(spec["W_fixed"]) == {"module", "component"}
    for lo, hi in spec["limit_bounds"].values():
        assert type(lo) is int and type(hi) is int and lo <= hi


def test_every_spec_key_is_compared_or_classified() -> None:
    compared = set(_fixture_grant()) - {"never_ba", "n_flags"} | {"never"}
    assert set(W.GRANT_SPEC) == compared | set(METADATA_ONLY_KEYS), (
        "a GRANT_SPEC key is neither compared with baseAgent nor listed in METADATA_ONLY_KEYS with a reason")
    assert not compared & set(METADATA_ONLY_KEYS)


@pytest.mark.parametrize("key", ["flag_order", "C", "S", "W_variable", "W_fixed", "W_regex", "limit_bounds",
                                 "defaults"])
def test_spec_equals_baseagent_constant(key: str) -> None:
    assert W.GRANT_SPEC[key] == _fixture_grant()[key], f"GRANT_SPEC[{key!r}] differs from baseAgent's"


def test_six_flags_like_baseagent() -> None:
    assert _fixture_grant()["n_flags"] == 6 == len(W.grant_flags(W.DEFAULT_LIMITS))


def test_never_list_is_a_superset_of_baseagents() -> None:
    """INTENTIONAL_DIFFERENCES['never-list-superset']."""
    ba = _fixture_grant()["never_ba"]
    spec = W.GRANT_SPEC["never"]
    assert ba == ["--dir", "--env", "-S inherit-network"]
    assert "--dir (BA)" in spec and "--env" in spec and "-S inherit-network=y" in spec
    extras = {x for x in spec if x not in ("--dir (BA)", "--env", "-S inherit-network=y")}
    assert extras == {"--preload", "--allow-precompiled", "--config"}
    for flag in sorted(extras):  # documentary only: the exact-shape check refuses each one anyway
        assert _verdict([*W.grant_flags(W.DEFAULT_LIMITS), flag, "x"], "module") == "refuse"


def test_excerpt_header_names_the_baseagent_commit() -> None:
    commit, header, blocks = _excerpt()
    assert commit == _corpus()["baseagent_commit"]
    assert any(BA_RUNNER_REL in h for h in header) and len(blocks) == 3


def test_intentional_differences_each_have_a_test() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    for key in INTENTIONAL_DIFFERENCES:
        assert src.count(f"INTENTIONAL_DIFFERENCES['{key}']") >= 1, f"no test documents difference {key!r}"


# --- build_argv / grant_flags vs baseAgent's flags -----------------------------------------------------------------

def test_build_argv_matches_baseagent_default_flags() -> None:
    by = {(c["name"], c["profile"]): c["flags"] for c in _corpus()["cases"]}
    argv = W.build_argv("/abs/wasmtime", "/abs/m.wasm", [], W.COMPUTE_ONLY)
    assert argv[2:8] == by[("default", "module")]
    assert W.grant_flags(W.DEFAULT_LIMITS, profile="component") == by[("default", "component")]


def test_print_spec_cli_bytes() -> None:
    out = subprocess.run([sys.executable, "-m", "agentteams.wasm_exec", "--print-spec"], capture_output=True,
                         check=True, cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)}).stdout
    assert out.endswith(b"\n")
    assert hashlib.sha256(out[:-1]).hexdigest() == W.GRANT_SPEC_SHA256
    assert json.loads(out) == json.loads(json.dumps(W.GRANT_SPEC))


def test_no_grant_spec_data_file() -> None:
    """R9: GRANT_SPEC is the one source; no JSON copy ships."""
    assert not (REPO / "agentteams" / "data" / "wasm_grant_spec.json").exists()


# --- the shared corpus ---------------------------------------------------------------------------------------------

def test_corpus_spec_sha_current() -> None:
    assert _corpus()["spec_sha256"] == W.GRANT_SPEC_SHA256, (
        "GRANT_SPEC changed: review tests/fixtures/wasm/grant_corpus.json against it and re-record spec_sha256")


def test_corpus_covers_ba_w6_names_in_both_profiles() -> None:
    cases = _corpus()["cases"]
    for profile in ("module", "component"):
        names = {c["name"] for c in cases if c["profile"] == profile and c["source"] == "ba-w6"}
        assert names == BA_W6_NAMES, profile
    assert all(c["ba_verdict"] in ("accept", "refuse") for c in cases)


def test_corpus_verdicts_match_baseagent() -> None:
    wrong = []
    for c in _corpus()["cases"]:
        got = _verdict(c["flags"], c["profile"])
        expect = c["ba_verdict"]
        if (c["name"], c["profile"]) in CORPUS_DIVERGENCES:
            expect = "refuse" if expect == "accept" else "accept"
        if got != expect:
            wrong.append(f"{c['profile']} {c['name']!r}: agentteams {got}, expected {expect}")
    assert not wrong, "\n".join(wrong)


def test_corpus_divergences_are_live() -> None:
    names = {(c["name"], c["profile"]) for c in _corpus()["cases"]}
    assert set(CORPUS_DIVERGENCES) <= names


# --- each intentional difference is real, and agentteams is the stricter side --------------------------------------

def test_difference_agentteams_mounts(tmp_path: Path) -> None:
    """INTENTIONAL_DIFFERENCES['agentteams-mounts']."""
    host = os.path.realpath(tmp_path)
    g = W.Grant((W.Mount(host, "/project/data", "ro"),), W.DEFAULT_LIMITS)
    argv = W.build_argv("/abs/wasmtime", "/abs/m.wasm", [], g)
    assert argv[8:10] == ["--dir", f"{host}::/project/data"]
    with pytest.raises(W.WasmExecError, match="parity"):
        W.assert_grant_shape(argv, None)
    # baseAgent's six-element check would refuse the same flags with a --dir appended (corpus "+ --dir /").
    by = {(c["name"], c["profile"]): c["ba_verdict"] for c in _corpus()["cases"]}
    assert by[("+ --dir /", "module")] == "refuse" == by[("+ --dir H::.::readonly", "module")]


def test_difference_argv_tail() -> None:
    """INTENTIONAL_DIFFERENCES['argv-tail']."""
    flags = W.grant_flags(W.DEFAULT_LIMITS)
    ba_shape = ["/abs/wasmtime", "run", *flags, "--argv0", "m", "/abs/m.wasm"]  # wasmtimeInvocation's shape
    with pytest.raises(W.WasmExecError):
        W.assert_grant_shape(ba_shape, None)
    with pytest.raises(W.WasmExecError, match="absolute"):
        W.assert_grant_shape(["/abs/wasmtime", "run", *flags, "--argv0", "m", "--", "m.wasm"], None)


def test_difference_grant_mode_timeout_seconds() -> None:
    """INTENTIONAL_DIFFERENCES['grant-mode-timeout-seconds']."""
    assert W.LIMIT_BOUNDS["timeout_s"] == (1, W.GRANT_SPEC["limit_bounds"]["timeout_ms"][1] // 1000)
    with pytest.raises(W.WasmExecError):
        W.Limits(W.DEFAULT_LIMITS.fuel, W.DEFAULT_LIMITS.max_memory_bytes, 0)
    by = {(c["name"], c["profile"]): c for c in _corpus()["cases"]}
    ms = by[("500ms timeout", "module")]
    assert ms["ba_verdict"] == "accept" and _verdict(ms["flags"], "module") == "accept"  # parity mode agrees
    with pytest.raises(W.WasmExecError):
        W.assert_grant_shape(_wrap(ms["flags"]), W.COMPUTE_ONLY)  # grant mode refuses the ms spelling


def test_difference_component_parity_only() -> None:
    """INTENTIONAL_DIFFERENCES['component-parity-only']."""
    comp = W.grant_flags(W.DEFAULT_LIMITS, profile="component")
    W.assert_grant_shape(_wrap(comp), None, profile="component")
    with pytest.raises(W.WasmExecError, match="component"):
        W.assert_grant_shape(_wrap(comp), W.COMPUTE_ONLY, profile="component")
    assert "profile" not in W.build_argv.__code__.co_varnames[:W.build_argv.__code__.co_argcount]


def test_difference_no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """INTENTIONAL_DIFFERENCES['no-env-overrides']."""
    before = W.grant_flags(W.DEFAULT_LIMITS)
    for k, v in (("BA_WASMTIME_FUEL", "1000000000000000"), ("BA_WASMTIME_MAXMEM", "4294967296"),
                 ("BA_WASMTIME_TIMEOUT", "3600s")):
        monkeypatch.setenv(k, v)
    assert W.grant_flags(W.DEFAULT_LIMITS) == before
    assert W.build_argv("/abs/wasmtime", "/abs/m.wasm", [], W.COMPUTE_ONLY)[2:8] == before
    assert "BA_WASMTIME" not in Path(W.__file__).read_text(encoding="utf-8")


# --- fixtures vs live baseAgent (skips when no checkout) ------------------------------------------------------------

def _live_ba() -> Path:
    root = Path(os.environ.get("AGENTTEAMS_BASEAGENT_DIR") or Path.home() / "githubrepositories" / "baseAgent")
    if not (root / BA_RUNNER_REL).is_file():
        pytest.skip(f"no baseAgent checkout with {BA_RUNNER_REL} (set AGENTTEAMS_BASEAGENT_DIR)")
    return root


def test_fixture_matches_live_baseagent_runner() -> None:
    live = (_live_ba() / BA_RUNNER_REL).read_text(encoding="utf-8")
    for block in _excerpt()[2]:
        assert block in live, ("baseAgent's runner no longer contains this excerpt block verbatim: re-copy "
                               f"tests/fixtures/wasm/baseagent_wasmtime_grant.excerpt.txt and review (@security):\n"
                               f"{block[:200]}")
    assert parse_ba_grant(live) == _fixture_grant()


def test_corpus_names_match_live_baseagent_w6() -> None:
    live = (_live_ba() / BA_PROBE_REL).read_text(encoding="utf-8")
    block = _need(r"// W6 .*?const cases = \{(.*?)\n\s*const bad =", live, "W6 cases block", re.S)[1]
    names = {a or b for a, b in re.findall(r'(?<=[{,\s])(?:"([^"]+)"|(default)):\s', block)}
    assert names == BA_W6_NAMES, f"baseAgent W6 changed: added {names - BA_W6_NAMES}, removed {BA_W6_NAMES - names}"
