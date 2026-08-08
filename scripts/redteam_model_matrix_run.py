#!/usr/bin/env python3
"""redteam_model_matrix_run.py — run the judgment corpus across several open-weights models.

**What this does not do.** It does not assemble a prompt, invoke a model, or score a response.
Every one of those already exists in :mod:`scripts.redteam_judgment_run`, which carries the
guards this harness paid for in real money: the shared front-matter strip
(``sweep.agent_system_prompt``), the ``MIN_PLAUSIBLE_SECONDS`` liveness floor that caught 11/11
"defended" in 0.0s, spend-as-liveness against ``/api/v1/credits``, ``validate_model_slug``, and
a compliance match vetoed by refusal. Re-implementing any of it here would be a hand-rolled
resolution of a solved problem — F-3 — and would silently re-open every defect above. This
module is a **loop, a price recorder, and a budget**.

**The question it answers.** Not "which model is most secure": every model measured so far
scores zero compliance, so a ceiling test between models tied at the ceiling discriminates
nothing. The question is whether the standing daily judgment job — currently defaulting to
``z-ai/glm-5.2`` at roughly $458/year — can move down the cost curve without losing the
contract. ``openai/gpt-oss-20b`` would be about $32/year for the same cadence.

**Why the ablation arm is not optional.** With zero compliance expected everywhere, a run that
reports zero compliance is indistinguishable from a harness that can no longer emit ``COMPLY``.
Each model is therefore run twice: once with this repository's ``@security`` contract, and once
with the contract ablated. The prediction is stated in advance —

    contract present -> COMPLY absent      contract ablated -> COMPLY present

— and if ``COMPLY`` stays absent under ablation, the corpus has stopped discriminating and the
per-model comparison is void rather than favourable. That outcome is reported first, not in a
footnote.

**Budget.** The child script's ``--budget`` and its ``MIN_REMAINING_USD`` floor are enforced
*per invocation*. Across a loop of thirteen models that bounds nothing: each child starts a
fresh accounting and the loop could walk the account down to the floor. ``--total-budget`` here
is cumulative, re-read from the provider between models, and aborts the loop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

JUDGMENT_RUNNER = REPO_ROOT / "scripts" / "redteam_judgment_run.py"

from agentteams.redteam.budget import MIN_REMAINING_USD, SpendCeiling  # noqa: E402

#: The evaluation population, as **size ladders within a family**.
#:
#: An earlier draft ranked sixteen models by price alone. That confounds cost with family,
#: parameter count and safety-tuning, none of them controlled, so any ordering it produced would
#: have been uninterpretable — a cheap model scoring badly would have been indistinguishable
#: from a small model scoring badly, or an untuned one. Holding the family fixed and varying
#: size means a within-family difference is attributable to something. A cross-family difference
#: still is not, and the report may not claim otherwise.
#:
#: `note` records why the family is here at all. It is a **selection heuristic** — who gets
#: measured — and is never evidence for a result. "Has a published safety programme" is
#: verifiable; "is respected for promoting security" is a reputational claim this catalogue does
#: not encode, and asserting a label in place of a measurement is the exact failure this harness
#: exists to catch. `tests/test_redteam_model_matrix.py` asserts these strings never reach a
#: findings row.
LADDERS: dict[str, dict] = {
    "openai-gpt-oss": {
        "note": "gpt-oss safety card, Apache-2.0",
        "models": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
    },
    "openai-safeguard": {
        "note": "purpose-built: reasons over a caller-supplied policy",
        "models": ["openai/gpt-oss-safeguard-20b"],
    },
    "nvidia-nemotron": {
        "note": "NeMo Guardrails",
        "models": [
            "nvidia/nemotron-3-nano-30b-a3b",
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b",
        ],
    },
    "mistral-ministral": {
        "note": "Mistral moderation/guardrails",
        "models": [
            "mistralai/ministral-3b-2512",
            "mistralai/ministral-8b-2512",
            "mistralai/ministral-14b-2512",
        ],
    },
    "meta-llama": {
        "note": "Purple Llama, CyberSecEval",
        "models": ["meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-4-maverick"],
    },
    "google-gemma": {
        "note": "ShieldGemma, Responsible AI toolkit",
        "models": ["google/gemma-3-27b-it"],
    },
    # glm-4.7 is deliberately absent: it is the incumbent's previous generation, and the
    # capability floor for this comparison is "at least as capable as glm-5.2, and no less".
    "zai-glm": {"note": "incumbent baseline", "models": ["z-ai/glm-5.2"]},
    "qwen": {
        "note": "measured baseline",
        # qwen3.8-max is a generation above everything in the 2026-08-07 run and is NOT yet
        # measured — it is listed so it resolves to a family rather than reporting "unknown",
        # and because its siblings placed 2nd and 5th of twelve. See
        # references/qwen38max-judgment-handoff.plan.md.
        "models": [
            "qwen/qwen3.6-plus",
            "qwen/qwen3.6-max-preview",
            "qwen/qwen3.8-max",
        ],
    },
    # --- frontier tier -------------------------------------------------------------------
    # Selected on CAPABILITY AND PRICE comparability with the incumbent (z-ai/glm-5.2 at
    # $0.45/$1.40 per M), deliberately NOT on any security reputation. The reputational column
    # that governed the earlier tiers is a selection heuristic at best, and pre-rating a model's
    # security before measuring it is the thing this harness exists to avoid: it would let a
    # published safety programme stand in for a result. These families are here because they
    # are the same class of model as the one the standing job already runs.
    "deepseek": {
        "note": "frontier tier, comparable capability/price",
        "models": ["deepseek/deepseek-v3.2", "deepseek/deepseek-r1"],
    },
    "moonshot": {
        "note": "frontier tier, comparable capability/price",
        "models": ["moonshotai/kimi-k2"],
    },
    "minimax": {
        "note": "frontier tier, comparable capability/price",
        "models": ["minimax/minimax-m2"],
    },
    "mistral-large": {
        "note": "frontier tier, comparable capability/price",
        "models": ["mistralai/mistral-large-2512", "mistralai/mistral-medium-3-5"],
    },
    "ai21": {
        "note": "frontier tier, comparable capability/price",
        "models": ["ai21/jamba-large-1.7"],
    },
}

#: Models already measured this session. Kept in the population so denominators stay honest,
#: skipped by default so the run does not pay twice for a known answer.
BASELINES = frozenset({"z-ai/glm-5.2", "qwen/qwen3.6-plus"})


def evaluated_models(include_baselines: bool = False) -> list[str]:
    """Return the canonical model population.

    This is the enumerator named by the ``model_matrix.evaluated_models`` population source, and
    it returns every model the matrix set out to evaluate — not the subset that answered. A
    model that timed out or refused the context length stays in the denominator, because those
    are the interesting ones and dropping them is how "0 of 9 complied" comes to mean "0 of the
    9 that survived".

    Args:
        include_baselines: When false, omit models already measured this session.

    Returns:
        Model slugs, de-duplicated, in ladder order.
    """
    out: list[str] = []
    for family in LADDERS.values():
        for model in family["models"]:
            if model in out:
                continue
            if not include_baselines and model in BASELINES:
                continue
            out.append(model)
    return out


def family_of(model: str) -> str:
    """Return the ladder family a model belongs to, or ``"unknown"``."""
    for name, family in LADDERS.items():
        if model in family["models"]:
            return name
    return "unknown"


@dataclass
class ArmResult:
    """One model run under one condition (contract present, or contract ablated)."""

    model: str
    ablated: bool
    ok: bool = False
    error: str = ""
    complied: int = 0
    attacks: int = 0
    benign_controls: int = 0
    benign_passed: int = 0
    transport_failures: int = 0
    spend: float | None = None
    seconds: float = 0.0
    price_in_per_m: float | None = None
    price_out_per_m: float | None = None

    @property
    def problems(self) -> list[str]:
        """Return reasons this arm cannot be treated as a measurement."""
        issues: list[str] = []
        if not self.ok:
            issues.append(f"run failed: {self.error or 'unknown'}")
            return issues
        if self.attacks == 0:
            issues.append("ZERO attack payloads — a sweep with no attacks cannot fail")
        if self.benign_controls == 0:
            issues.append(
                "ZERO benign controls — a model that refuses everything would score a perfect "
                "sweep and rank first"
            )
        if self.transport_failures:
            issues.append(f"{self.transport_failures} transport failure(s) — not measurements")
        return issues


#: OpenRouter's real endpoint, used to route around a filtering proxy.
DIRECT_ROUTING_HOST = "https://openrouter.ai"

#: Set by :func:`child_env` when routing is forced direct, so the choice is visible in the log
#: rather than being an invisible property of the operator's shell.
_ROUTING_NOTE: list[str] = []


#: goose's own configuration file. Checked because the proxy is configured THERE, not in the
#: environment — the first version of this check read only ``os.environ``, found nothing, and
#: cheerfully reported "routing: direct" while goose went on using the proxy from its config.
GOOSE_CONFIG = Path.home() / ".config" / "goose" / "config.yaml"


def proxy_is_filtering() -> str:
    """Return the configured ``OPENROUTER_HOST`` when it is a local proxy, else ``""``.

    The goose config on this machine points ``OPENROUTER_HOST`` at ``127.0.0.1:8791``, a proxy
    started with ``--only Z.AI,Alibaba,CoreWeave``. That is a reasonable default for the standing
    job, whose model (``z-ai/glm-5.2``) is served by Z.AI — and it is fatal to a *comparison*
    across providers, because every model outside the allow-list returns a 404 from localhost.

    Worse, those 404s were not recognised as transport failures, so two models scored MISS on all
    14 payloads and the run reported it as their judgment. A comparison silently pinned to three
    providers is not measuring the models it names.
    """
    host = os.environ.get("OPENROUTER_HOST", "")
    if not host and GOOSE_CONFIG.exists():
        for line in GOOSE_CONFIG.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped.startswith("OPENROUTER_HOST:"):
                continue
            host = stripped.split(":", 1)[1].strip()
            break
    if host and ("127.0.0.1" in host or "localhost" in host):
        return host
    return ""


def child_env(direct: bool = True) -> dict[str, str]:
    """Return the environment for a child judgment run.

    Args:
        direct: When true and a filtering proxy is configured, override ``OPENROUTER_HOST`` so
            the child reaches OpenRouter itself. The override is announced, never silent.

    Returns:
        A copy of the current environment, possibly with ``OPENROUTER_HOST`` replaced.
    """
    env = dict(os.environ)
    proxy = proxy_is_filtering()
    if direct and proxy:
        env["OPENROUTER_HOST"] = DIRECT_ROUTING_HOST
        if not _ROUTING_NOTE:
            _ROUTING_NOTE.append(proxy)
    return env


def read_catalogue() -> dict[str, dict]:
    """Return the live model catalogue keyed by slug.

    Read at launch rather than hardcoded: ``z-ai/glm-5.2`` moved from $0.76/$2.42 per million to
    $0.45/$1.40 inside a single working session. A price table written into this file would be
    stale on arrival, and a cost comparison against stale prices is a fabricated result.

    Returns:
        Mapping of model slug to its catalogue entry.
    """
    request = urllib.request.Request("https://openrouter.ai/api/v1/models")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URL
        payload = json.loads(response.read().decode("utf-8"))
    return {entry["id"]: entry for entry in payload.get("data", [])}


def read_remaining_credit(token: str) -> float | None:
    """Return remaining credit in USD, or ``None`` when the endpoint cannot answer."""
    request = urllib.request.Request("https://openrouter.ai/api/v1/credits")
    request.add_header("Authorization", f"Bearer {token}")
    # Enumerated rather than blanket, matching redteam_judgment_run.py:212. CH-24 forbids new
    # broad catches, and the rule earns its keep here: a bare `except Exception` would also
    # swallow a KeyboardInterrupt-adjacent bug or an AttributeError from a refactor, and return
    # None — which every caller reads as "refuse to continue". A swallow that turns a coding
    # error into a budget refusal is a control failing closed for the wrong reason.
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8")).get("data", {})
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError):
        return None
    total, used = data.get("total_credits"), data.get("total_usage")
    if total is None or used is None:
        return None
    return float(total) - float(used)


def summarise(report: dict) -> dict:
    """Reduce a child run-report to the counts the matrix compares."""
    results = report.get("results", [])
    attacks = [r for r in results if r.get("expected") != "PASS"]
    benign = [r for r in results if r.get("expected") == "PASS"]
    return {
        "complied": sum(1 for r in attacks if r.get("observed") == "COMPLY"),
        "attacks": len(attacks),
        "benign_controls": len(benign),
        "benign_passed": sum(1 for r in benign if r.get("acceptable")),
        "transport_failures": sum(1 for r in results if r.get("transport_error")),
        "spend": report.get("spend"),
    }


def run_arm(model: str, *, ablated: bool, out_root: Path, timeout: int, budget: float,
            catalogue: dict[str, dict], limit: int = 0) -> ArmResult:
    """Run one model under one condition by delegating to the judgment runner.

    Args:
        model: Model slug.
        ablated: When true, the child replaces the ``@security`` contract with a neutral
            identity — the positive control.
        out_root: Directory under which this arm's artifacts are written.
        timeout: Per-payload seconds, passed through.
        budget: Per-invocation budget cap, passed through.
        catalogue: Live catalogue, for recording price beside the result.
        limit: When non-zero, run only the first N payloads.

    Returns:
        The :class:`ArmResult`. A child failure is recorded, never raised — one dead model must
        not abort the other twelve — but it is also never counted as a clean sweep.
    """
    arm = ArmResult(model=model, ablated=ablated)
    entry = catalogue.get(model)
    if entry is not None:
        pricing = entry.get("pricing", {})
        arm.price_in_per_m = float(pricing.get("prompt", 0)) * 1e6
        arm.price_out_per_m = float(pricing.get("completion", 0)) * 1e6

    slug = model.replace("/", "__")
    out_dir = out_root / f"{slug}{'.ablated' if ablated else '.contract'}"
    argv = [
        sys.executable, str(JUDGMENT_RUNNER),
        "--model", model,
        "--out", str(out_dir.resolve()),
        "--timeout", str(timeout),
        "--budget", f"{budget:.4f}",
        # Condition 2 of the security clearance. Promoting inside the loop would write the
        # tracked findings ledger once per model, unattended, and start a 14-day triage clock
        # on each row. Promotion is a separate reviewed step.
        "--no-promote",
    ]
    if ablated:
        argv.append("--ablate-contract")
    if limit:
        argv += ["--limit", str(limit)]

    started = time.monotonic()
    completed = subprocess.run(argv, capture_output=True, text=True, cwd=REPO_ROOT,
                               env=child_env())
    arm.seconds = time.monotonic() - started

    report_path = out_dir / "run-report.json"
    if not report_path.exists():
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        arm.error = tail[-1] if tail else f"no run-report.json; exit {completed.returncode}"
        return arm

    counts = summarise(json.loads(report_path.read_text(encoding="utf-8")))
    arm.ok = True
    arm.complied = counts["complied"]
    arm.attacks = counts["attacks"]
    arm.benign_controls = counts["benign_controls"]
    arm.benign_passed = counts["benign_passed"]
    arm.transport_failures = counts["transport_failures"]
    arm.spend = counts["spend"]
    return arm


@dataclass
class MatrixResult:
    """The whole matrix: every arm, plus whether the control licensed reading it."""

    arms: list[ArmResult] = field(default_factory=list)
    population: list[str] = field(default_factory=list)
    aborted: str = ""

    @property
    def ablation_complied(self) -> int:
        """Total COMPLY observed across ablated arms — the positive control's signal."""
        return sum(a.complied for a in self.arms if a.ablated and a.ok)

    @property
    def corpus_discriminates(self) -> bool:
        """True when the ablation arm produced at least one COMPLY.

        When false, the corpus could not emit ``COMPLY`` even with the contract removed, so a
        zero-compliance result from the contract arm measures the harness rather than the model
        and no per-model conclusion may be drawn.
        """
        return self.ablation_complied > 0

    def population_count(self) -> tuple[int, int, str]:
        """Return ``(models_with_a_usable_contract_arm, population, source)``."""
        usable = sum(1 for a in self.arms if not a.ablated and a.ok and not a.problems)
        return usable, len(self.population), "model_matrix.evaluated_models"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot",
                        help="pilot: 1 rep, contract + ablation. full: adds repetitions.")
    parser.add_argument("--models", default="",
                        help="comma-separated slugs; default is the full ladder population")
    parser.add_argument("--include-baselines", action="store_true",
                        help="also re-measure glm-5.2 and qwen3.6-plus")
    parser.add_argument("--total-budget", type=float, default=3.30,
                        help="CUMULATIVE ceiling across all models, re-read between models")
    parser.add_argument("--per-model-budget", type=float, default=0.75)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int, default=0, help="first N payloads only")
    parser.add_argument("--out", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    models = ([m.strip() for m in args.models.split(",") if m.strip()] if args.models
              else evaluated_models(include_baselines=args.include_baselines))
    if not models:
        print("Refusing to run: the model population is empty. A matrix over zero models "
              "reports zero failures and renders as a clean sweep.", file=sys.stderr)
        return 2

    token = os.environ.get("OPENROUTER_API_KEY", "")
    if not token:
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 2

    catalogue = read_catalogue()
    # A slug absent from the catalogue is REFUSED, not skipped. Skipping shrinks the denominator
    # to the models that happened to exist and reports a clean sweep over the remainder.
    unknown = [m for m in models if m not in catalogue]
    if unknown:
        print(f"Refusing to run: not on the catalogue: {', '.join(unknown)}", file=sys.stderr)
        return 2

    print(f"  mode       : {args.mode}")
    print(f"  models     : {len(models)} across {len({family_of(m) for m in models})} families")
    print(f"  arms       : contract + ablation (positive control) = {2 * len(models)} runs")
    print(f"  budget     : ${args.total_budget:.2f} cumulative, "
          f"${args.per_model_budget:.2f} per invocation")

    proxy = proxy_is_filtering()
    if proxy:
        print(f"  routing    : OPENROUTER_HOST is {proxy} — a provider-filtering proxy. "
              f"Overriding to {DIRECT_ROUTING_HOST} for every child, because a cross-provider "
              f"comparison run through a three-provider allow-list measures the allow-list.")
    else:
        print(f"  routing    : direct ({os.environ.get('OPENROUTER_HOST') or DIRECT_ROUTING_HOST})")

    remaining = read_remaining_credit(token)
    if remaining is None:
        print("  credits    : UNAVAILABLE — cannot enforce a cumulative budget; refusing.",
              file=sys.stderr)
        return 2
    print(f"  credits    : ${remaining:.4f} remaining")
    if remaining < MIN_REMAINING_USD:
        print(f"  Refusing to start: under ${MIN_REMAINING_USD:.2f}.", file=sys.stderr)
        return 2

    if args.dry_run:
        for model in models:
            entry = catalogue[model]["pricing"]
            print(f"    {model:<40} {family_of(model):<18} "
                  f"in ${float(entry['prompt']) * 1e6:.2f}/M out "
                  f"${float(entry['completion']) * 1e6:.2f}/M")
        print("  --dry-run: validated only; nothing spent.")
        return 0

    stamp = time.strftime("%Y-%m-%d")
    out_root = (Path(args.out).resolve() if args.out
                else REPO_ROOT / "tmp" / "redteam-matrix" / stamp)
    out_root.mkdir(parents=True, exist_ok=True)

    matrix = MatrixResult(population=list(models))
    # The ceiling lives in agentteams.redteam.budget, not here. It used to live in this file,
    # which closed the hole for this caller and left it open for the next script to loop the
    # judgment runner.
    ceiling = SpendCeiling(total_budget=args.total_budget)
    refusal = ceiling.start(remaining)
    if refusal:
        print(f"  Refusing to start: {refusal}", file=sys.stderr)
        return 2

    for index, model in enumerate(models, start=1):
        for ablated in (False, True):
            label = "ablated " if ablated else "contract"
            print(f"  [{index}/{len(models)}] {model} [{label}] … ", end="", flush=True)
            arm = run_arm(model, ablated=ablated, out_root=out_root, timeout=args.timeout,
                          budget=args.per_model_budget, catalogue=catalogue, limit=args.limit)
            matrix.arms.append(arm)
            if arm.ok:
                print(f"complied {arm.complied}/{arm.attacks}, "
                      f"benign {arm.benign_passed}/{arm.benign_controls}"
                      f"{'  ' + '; '.join(arm.problems) if arm.problems else ''}")
            else:
                print(f"FAILED — {arm.error}")

        # Cumulative gate, between models. The child's own budget is per invocation and cannot
        # see the loop; without this the matrix is bounded only by the floor.
        verdict = ceiling.check(read_remaining_credit(token))
        print(f"      {ceiling.render()}")
        if verdict:
            matrix.aborted = verdict
            print(f"  ABORTING: {verdict}", file=sys.stderr)
            break

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": args.mode,
        "aborted": matrix.aborted,
        "corpus_discriminates": matrix.corpus_discriminates,
        "ablation_complied": matrix.ablation_complied,
        "population": matrix.population,
        "population_source": "model_matrix.evaluated_models",
        "arms": [
            {
                "model": a.model, "family": family_of(a.model), "ablated": a.ablated,
                "ok": a.ok, "error": a.error, "complied": a.complied, "attacks": a.attacks,
                "benign_controls": a.benign_controls, "benign_passed": a.benign_passed,
                "transport_failures": a.transport_failures, "spend": a.spend,
                "seconds": round(a.seconds, 2),
                "price_in_per_m": a.price_in_per_m, "price_out_per_m": a.price_out_per_m,
                "problems": a.problems,
            }
            for a in matrix.arms
        ],
    }
    (out_root / "matrix-report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    usable, total, source = matrix.population_count()
    print()
    print(f"  models with a usable contract arm: {usable} of {total} [{source}]")
    if matrix.corpus_discriminates:
        print(f"  positive control : PASSED — {matrix.ablation_complied} COMPLY under ablation")
    else:
        print("  positive control : FAILED — no COMPLY under ablation. The corpus cannot emit "
              "the signal it claims to measure; the per-model comparison is VOID, not clean.")
    print(f"  artifacts  : {out_root}")

    if matrix.aborted:
        return 2
    return 0 if matrix.corpus_discriminates else 1


if __name__ == "__main__":
    raise SystemExit(main())
