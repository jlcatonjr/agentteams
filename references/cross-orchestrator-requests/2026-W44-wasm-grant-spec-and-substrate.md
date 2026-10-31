# Coordination Request — 2026-W44 WASM Grant Spec and Substrate

**From:** agentteams orchestrator · **Date:** 2026-10-31 · **Protocol:** 3 (orchestrator-to-orchestrator)
**Target:** baseAgent (`~/githubrepositories/baseAgent`), at commit `49e0e1a6daf2b9f2656fae8cf2dc0efc33983449`
**Clearance:** `@security` review of this document **pending** (P19 step 6); nothing below is applied until it is
recorded and the baseAgent operator approves.
**Channel:** mediated — this artifact is the request. baseAgent is read-only from the agentteams session that wrote
it; no cross-repo write has occurred. The operator delivers this document to baseAgent's orchestrator.
**Source plan:** agentteams `tmp/by-week/2026-W44/wasm-sandbox/P19-baseagent-alignment.plan.md` (METAPLAN v2.1 §6 R9).

---

## 1. What agentteams now owns: the wasmtime grant spec

The pinned wasmtime grant that baseAgent's `experiments/battery/wasmtime_cmd_runner.mjs` carries as literals
(`GRANT_C`, `GRANT_S`, `W_FIXED`, `W_FIXED_COMPONENT`, `DEFAULT_LIMITS`, `LIMIT_BOUNDS`, the `wasmtimeFlags` order
and the `assertGrantFlags` `-W` regex) is now **owned by agentteams** as one constant:

- `agentteams.wasm_exec.GRANT_SPEC` — the canonical spec (schema `agentteams-wasm-grant/v1`, keyed on wasmtime
  major 36), seeded byte-for-byte from baseAgent's values above;
- `python -m agentteams.wasm_exec --print-spec` — prints it as canonical JSON
  (`json.dumps(GRANT_SPEC, sort_keys=True, separators=(",", ":"))` plus one newline);
- `agentteams.wasm_exec.GRANT_SPEC_SHA256` = sha256 of those bytes without the newline, today
  **`3e35eea32231a96fe9265437b1593b1e768d4cb42589519272051661c34669b1`**.

There is no separate data file. This is the custody model already in force for the OS launcher: agentteams owns
`confine-run.sh`, baseAgent pins the emitted bytes in `tests/test_confine_run_parity.py`.

**Day-1 parity needs no baseAgent runtime change.** agentteams' side is proven by
`tests/test_wasm_grant_spec.py` against two fixtures:

- `tests/fixtures/wasm/baseagent_wasmtime_grant.excerpt.txt` — a verbatim excerpt (lines 26-28, 37, 49-105) of
  baseAgent's runner, header naming commit `49e0e1a6`; parsed as text, never executed;
- `tests/fixtures/wasm/grant_corpus.json` — baseAgent's W6 accept/refuse cases (`test_deny_probe.mjs:149-180`, 44
  refuse + 4 accept) expanded to literal flag arrays for **both** profiles, plus 9 agentteams additions per profile
  (114 cases), each carrying **baseAgent's own `assertGrantFlags` verdict** at that commit.

All corpus verdicts agree with agentteams' parity mode (`assert_grant_shape(argv, None)`). When a baseAgent checkout
exists, two further tests check that the excerpt still appears verbatim in the live runner and that the live W6 case
names equal the corpus's.

### Intentional differences (agentteams is stricter or wider only where listed)

| Key | Difference |
|---|---|
| `agentteams-mounts` | In grant mode agentteams may append `--dir host::guest` pairs from a per-agent `Grant` (read-only `/project/<rel>`, one writable `/scratch`). baseAgent never passes `--dir`. agentteams' parity mode refuses every `--dir`, as `assertGrantFlags` does. **This is the only widening, and it is agentteams-side only.** |
| `argv-tail` | agentteams checks the whole argv and requires `--argv0 <name> -- <absolute module>`; baseAgent checks the six flags and passes `--argv0 <name> <module> args` (no `--`). |
| `grant-mode-timeout-seconds` | agentteams' limits are whole seconds (1-3600 s); `<n>ms` is accepted in parity mode only. |
| `component-parity-only` | agentteams v1 refuses component guests in grant mode; the component profile is in the spec for parity coverage. |
| `no-env-overrides` | `BA_WASMTIME_FUEL/MAXMEM/TIMEOUT` have no agentteams equivalent; limits come from the grant only. |
| `never-list-superset` | The spec's documentary `never` list adds `--preload`, `--allow-precompiled`, `--config`; both exact-shape checks refuse them anyway. |

## 2. Request A — the parity test on baseAgent's side (one atomic unit)

Apply together or not at all:

1. **Vendor** `experiments/battery/wasm_grant_spec.json` = the exact output of
   `python -m agentteams.wasm_exec --print-spec` (with its trailing newline).
2. **Vendor** `experiments/battery/wasm_grant_corpus.json` = a byte copy of agentteams
   `tests/fixtures/wasm/grant_corpus.json` (sha256 `1e457bc234b3dada0dbd934569e33bd016b4f810d3a5f5be0b6805dc8192e7fd`).
3. **Add** `tests/test_wasm_grant_parity.py` (text below), mirroring `tests/test_confine_run_parity.py`:
   - `test_vendored_spec_and_corpus_pinned` — both vendored files match the pinned digests (as
     `AGENTTEAMS_EMITTED_SHA256` does for the launcher);
   - `test_runner_matches_vendored_spec` — runs node, dumps `wasmtimeFlags({}, "module" | "component")`,
     `DEFAULT_LIMITS` and `LIMIT_BOUNDS` with `BA_WASMTIME_*` unset, and compares them with the spec;
   - `test_runner_verdicts_match_vendored_corpus` — every corpus case through `assertGrantFlags(flags, profile)`;
   - `test_vendored_spec_matches_an_installed_agentteams` — if agentteams is importable and ships a different
     `GRANT_SPEC_SHA256`, **skip** with a review notice (an upstream change is noticed, not imposed).
4. **No change to `wasmtime_cmd_runner.mjs`** in this phase. (Phase 2, baseAgent's call: have the runner read its
   constants from the vendored JSON. It changes certified runtime code, so it is offered, not requested.)

The test text was dry-run (not committed anywhere) against a scratch copy of baseAgent's runner at `49e0e1a6` with
the two vendored files: 4 passed; flipping `http=n` to `http=y` in the runner copy failed both node tests.

### Change protocol for the grant

1. A grant change is proposed in agentteams; any flag change bumps `schema` (`v1` → `v2`). It is a C-3 capability
   change: agentteams `@security` decides, then the integrity manifest is re-pinned.
2. agentteams' release carries the new spec; baseAgent's installed-agentteams test skips with the review notice.
3. baseAgent reviews, re-vendors both files, re-pins the digests, and updates the runner literals (phase 1).

### Proposed `tests/test_wasm_grant_parity.py`

```python
"""Drift guard for the pinned wasmtime grant.

agentteams owns the grant (`agentteams.wasm_exec.GRANT_SPEC`, printed by `python -m agentteams.wasm_exec
--print-spec`). This repository vendors that output as `experiments/battery/wasm_grant_spec.json` and agentteams'
accept/refuse corpus as `experiments/battery/wasm_grant_corpus.json`, and `wasmtime_cmd_runner.mjs` must build and
check exactly that grant.

To take a new grant: re-vendor both files from agentteams, review the diff (@security), update the runner literals,
and re-pin the two digests below.
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BATTERY = ROOT / "experiments" / "battery"
SPEC = BATTERY / "wasm_grant_spec.json"
CORPUS = BATTERY / "wasm_grant_corpus.json"
RUNNER = BATTERY / "wasmtime_cmd_runner.mjs"

# agentteams GRANT_SPEC_SHA256 (sha256 of the --print-spec bytes without the trailing newline).
AGENTTEAMS_GRANT_SPEC_SHA256 = "3e35eea32231a96fe9265437b1593b1e768d4cb42589519272051661c34669b1"
# sha256 of the vendored corpus file (agentteams tests/fixtures/wasm/grant_corpus.json, byte copy).
AGENTTEAMS_GRANT_CORPUS_SHA256 = "1e457bc234b3dada0dbd934569e33bd016b4f810d3a5f5be0b6805dc8192e7fd"


def _spec():
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_vendored_spec_and_corpus_pinned():
    data = SPEC.read_bytes()
    assert data.endswith(b"\n") and hashlib.sha256(data[:-1]).hexdigest() == AGENTTEAMS_GRANT_SPEC_SHA256, (
        "wasm_grant_spec.json differs from the reviewed agentteams grant: re-vendor, review, and re-pin.")
    assert hashlib.sha256(CORPUS.read_bytes()).hexdigest() == AGENTTEAMS_GRANT_CORPUS_SHA256
    assert json.loads(CORPUS.read_text(encoding="utf-8"))["spec_sha256"] == AGENTTEAMS_GRANT_SPEC_SHA256


def _node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BA_WASMTIME_")}
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, cwd=ROOT,
                         env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_runner_matches_vendored_spec():
    spec = _spec()
    got = _node(
        'import { wasmtimeFlags, DEFAULT_LIMITS, LIMIT_BOUNDS } from "./experiments/battery/wasmtime_cmd_runner.mjs";\n'
        "const b = (p) => p.map(String);\n"
        "process.stdout.write(JSON.stringify({ module: wasmtimeFlags({}, 'module'),"
        " component: wasmtimeFlags({}, 'component'), defaults: DEFAULT_LIMITS,"
        " bounds: { fuel: b(LIMIT_BOUNDS.fuel), max_memory_bytes: b(LIMIT_BOUNDS.maxMemory),"
        " timeout_ms: b(LIMIT_BOUNDS.timeoutMs) } }));")
    d = spec["defaults"]
    for profile in ("module", "component"):
        want = ["-C", spec["C"], "-W", f"fuel={d['fuel']},timeout={d['timeout']},max-memory-size="
                f"{d['max_memory_bytes']},{spec['W_fixed'][profile]}", "-S", spec["S"]]
        assert got[profile] == want, profile
    assert spec["flag_order"] == ["-C", "-W", "-S"] and spec["W_variable"] == ["fuel", "timeout", "max-memory-size"]
    assert got["defaults"] == {"fuel": str(d["fuel"]), "maxMemory": str(d["max_memory_bytes"]), "timeout": d["timeout"]}
    assert {k: [int(x) for x in v] for k, v in got["bounds"].items()} == spec["limit_bounds"]


def test_runner_verdicts_match_vendored_corpus():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    verdicts = _node(
        'import { readFileSync } from "node:fs";\n'
        'import { assertGrantFlags } from "./experiments/battery/wasmtime_cmd_runner.mjs";\n'
        'const c = JSON.parse(readFileSync("experiments/battery/wasm_grant_corpus.json", "utf8"));\n'
        "process.stdout.write(JSON.stringify(c.cases.map((k) => {"
        " try { assertGrantFlags(k.flags, k.profile); return 'accept'; } catch { return 'refuse'; } })));")
    wrong = [f"{c['profile']} {c['name']!r}: {v}, corpus {c['ba_verdict']}"
             for c, v in zip(corpus["cases"], verdicts) if v != c["ba_verdict"]]
    assert not wrong, "\n".join(wrong)
    assert any(c["name"] == "+ --dir H::.::readonly" and c["ba_verdict"] == "refuse" for c in corpus["cases"])


def test_vendored_spec_matches_an_installed_agentteams():
    """When agentteams is installed, its grant must be the one pinned here (an upstream change is noticed here,
    not imposed)."""
    try:
        from agentteams import wasm_exec
    except ImportError:
        pytest.skip("agentteams (with wasm_exec) is not installed")
    if wasm_exec.GRANT_SPEC_SHA256 != AGENTTEAMS_GRANT_SPEC_SHA256:
        pytest.skip("the installed agentteams ships a different grant spec: review the diff (@security) and "
                    "re-vendor (not a failure of this repository's copy)")
```

## 3. Verified wasmtime facts (for baseAgent's `@security`; surfaced, not edited)

- **F1 — pin wasmtime 36.0.20, not 36.0.16.** baseAgent's install (security-decisions log row 44) and
  `.github/workflows/conformance.yml` (lines 14, 63-70) use **36.0.16** (2026-09-24), which is four security patch
  releases behind. agentteams pins **36.0.20** (2026-10-29, the 36 LTS line, operator decision D9) because
  36.0.17-36.0.20 fix:
  - 36.0.17: GHSA-j366-h8gg-77pm (preview0 `poll_oneoff` circumvents **fuel**), GHSA-gqmc-89g8-p25r,
    GHSA-96f6-r43r-8c24;
  - 36.0.18: GHSA-m9f7-9w4p-gv38 (**filesystem sandbox escape when renaming symlinks** with trailing slashes),
    GHSA-q554-3cjc-rx7p, GHSA-5qpp-g3j2-8frx;
  - 36.0.19: GHSA-mghf-wjfp-p5xm (**aarch64 Cranelift `br_table` miscompile** — relevant on an Apple-silicon
    operator host), GHSA-897f-g5gm-fmxm, GHSA-r8xh-cgf7-392j;
  - 36.0.20: GHSA-6fr9-xmw9-5352, GHSA-h4wp-g4x3-7398.

  36.0.20 is younger than the 14-day S-10 cooldown, so agentteams adopts it under a `@security` remediation waiver
  naming those ids (drafted in agentteams P03 step 1; recording is pending). Release-archive sha256 values (GitHub
  API digests) are pinned in agentteams `agentteams/data/wasm_runtime_pins.json`, e.g. linux-x86_64
  `wasmtime-v36.0.20-x86_64-linux.tar.xz` = `66d843c88cd9ecd5ca31004301ee337b8750bf08a839d06c2e7598e531b06de8`,
  darwin-arm64 `wasmtime-v36.0.20-aarch64-macos.tar.xz` =
  `7228786d479b0e5bddf293d07f53b9aecb1a8f1d55a02821640aa65a522e2631`; the extracted-binary digests are still
  `TODO-PIN` (fail closed) until the operator runs `agentteams --wasm-pin-update` on a host that can download. The
  grant itself is unchanged across 36.0.x, but the W1-W6 runs must be re-run on the new patch, because trap texts are
  matched by regex (`test_deny_probe.mjs:115,123,129,138`). baseAgent's exposure to GHSA-m9f7 is low (no `--dir`).
- **F2 — never pass extra `::` segments to `--dir`; no read-only preopen exists.** On wasmtime 36.0.16,
  `--dir HOST::.::readonly` is **accepted silently and the mount is writable** (verified 2026-10-31 with baseAgent's
  own `deny_probe.wasm`). wasmtime 36's `--dir` has no read-only mode at all: a preopen is always writable by the
  guest. baseAgent's exact-shape check already refuses every `--dir`; keep it so, and add a W6 case
  `"+ --dir H::.::readonly"` (it is already in the vendored corpus, refused by `assertGrantFlags`) so a future
  widening cannot assume a read-only spelling exists. agentteams enforces read-only `/project/<rel>` mounts by its own
  means (a read-only snapshot copy / OS confinement), never by a `--dir` option, and refuses any `:` in a mount host.
- **F3 — trap labels read from stderr are guest-forgeable.** The guest's stderr is wasmtime's stderr. W2-W5 match
  trap text on runs of baseAgent's own trusted probe, which is sound; any future use on untrusted guests should pair
  the text with the exit code and host-side timing (the stdout analogue is already documented at
  `wasmtime_cmd_runner.mjs:143-152`).

## 4. Substrate switch (P20)

*Placeholder — agentteams plan P20 (`substrate-projection`) fills this section: projecting the WASM switch into
baseAgent-style model-substrate consumers.*

## Honesty ceilings

- The parity tests prove the flag strings and the shape-check verdicts are equal. They do not prove that both
  runtimes enforce them identically on every platform (agentteams P17's platform matrix).
- agentteams' `--dir` extension is outside baseAgent's grant by design.
- The agentteams-side live check runs only where a baseAgent checkout exists (the operator's host); CI sees the
  fixtures only. If baseAgent changes its runner before adopting Request A, only that local run notices.

## Approval

The baseAgent operator (as target orchestrator) selects for Request A and for each finding: **apply now (agentteams
orchestrator as delegate)** / **operator applies** / **record finding only, defer**.
