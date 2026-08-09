#!/usr/bin/env python3
"""Preflight + reproduction test for Goose running on OpenRouter.

Diagnoses the common "the goose query stops early and quickly on OpenRouter"
failure, whose usual cause is an **invalid model slug** in
``~/.config/goose/config.yaml`` — specifically Ollama tag syntax (``model:tag``)
pasted into an OpenRouter slug, where ``:`` means a *variant* (``:free``…). For
example ``qwen/qwen3.6:35b-a3b`` (colon) does not exist on OpenRouter, while
``qwen/qwen3.6-35b-a3b`` (hyphen) does and supports tool calls. OpenRouter rejects
the unknown model immediately, so goose stops before doing any work.

Two tiers:

* **Offline static validation (always on, no key, no goose subprocess):** resolve
  the model goose will actually use, validate it against the OpenRouter *public*
  model catalog (exists + tool-calling), and — for an invalid colon slug — print
  the exact hyphen fix. This deterministically reproduces and pinpoints the bug.
* **Live probe (opt-in ``--live``, needs a key):** run goose on a tiny tool-using
  task and classify its OUTPUT (goose exits 0 even on a 400/401, so the exit code
  is not a signal). PASS only when a tool-produced sentinel appears.

Because plain ``goose run`` (e.g. a VS Code task, ``goose run --recipe …``) reads
``config.yaml`` while ``goose-or``/``goose-backend openrouter`` export a
``GOOSE_MODEL`` override, this tool judges the **config.yaml** model as primary
(what "this manner" uses) and *also* reports any divergent env override.

Usage::

    python scripts/goose-openrouter-preflight.py            # validate config.yaml model
    python scripts/goose-openrouter-preflight.py --json     # machine-readable report
    python scripts/goose-openrouter-preflight.py --offline  # syntax-only (no catalog fetch)
    python scripts/goose-openrouter-preflight.py --live     # also run a goose tool probe (needs key)
    python scripts/goose-openrouter-preflight.py --fix      # rewrite the bad slug (backup first)

Exit codes::

    0  the config.yaml model exists on OpenRouter and supports tools (live probe, if run, PASSed)
    1  the model is missing/unsupported, or the live probe confirmed an early-stop
    2  setup/parse/network/auth error, or the live probe was inconclusive (e.g. hit --max-turns)

The resolved ``OPENROUTER_API_KEY`` is passed only into the goose subprocess; it is
never printed, serialized to ``--json``, or written to the ``--fix`` backup.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CATALOG_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_CONFIG = Path("~/.config/goose/config.yaml").expanduser()

# OpenRouter variant suffixes (after the FIRST ``:``). A colon whose suffix is one
# of these is a legitimate variant, not an Ollama tag — do not "fix" it unless the
# hyphen form is independently catalog-confirmed.
KNOWN_VARIANTS = frozenset({"free", "nitro", "thinking", "beta", "online", "extended", "floor"})

# Sentinel for the live probe. 2026-08-08 found the previous design ($((6*7)) -> "42")
# produced FALSE PASSES: the probe ran under GOOSE_MODE=chat, where goose intercepts the
# tool call and asks the model to narrate a plan instead — and trivial arithmetic is
# narratable, so the "sentinel" appeared from text prediction with no execution at all.
# The fix is two-part: the probe now runs in auto mode, and the sentinel is a random
# nonce written to a temp file the model is only told the PATH of. Chat-mode narration,
# prompt echoes, and mental arithmetic cannot produce the file's contents; only a real
# read can. (Remediation log 2026-08-08 row; closed 2026-08-09.)
_SENTINEL_PREFIX = "GOOSE_OR_PREFLIGHT_OK_"


def _probe_fixture() -> tuple[str, str, str]:
    """Create the nonce file for one live probe.

    Returns:
        ``(probe_text, expected_sentinel, fixture_path)``. Caller deletes the file.
    """
    import tempfile
    import uuid
    nonce = uuid.uuid4().hex[:20]
    sentinel = _SENTINEL_PREFIX + nonce
    fd, path = tempfile.mkstemp(prefix="goose-preflight-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(sentinel + "\n")
    text = (
        f"Use the developer shell tool to run exactly this command: cat {path} — "
        f"then reply with the exact line it printed."
    )
    return text, sentinel, path

_GOOSE_KV_RE = re.compile(r"^(GOOSE_[A-Z0-9_]+):\s*(.*?)\s*$")
_ACTIVE_PROVIDER_RE = re.compile(r"^active_provider:\s*(.+?)\s*$")
_PROVIDER_NAME_RE = re.compile(r"^  ([a-zA-Z0-9_-]+):\s*$")
_PROVIDER_MODEL_RE = re.compile(r"^    model:\s*(.+?)\s*$")


# ---------------------------------------------------------------------------
# Pure logic (unit-tested offline; no network, no subprocess)
# ---------------------------------------------------------------------------

def parse_goose_config(text: str) -> dict[str, str]:
    """Parse top-level ``GOOSE_*: value`` scalars from a goose config.yaml.

    Stops at the first non-``GOOSE_`` top-level key (e.g. the nested
    ``extensions:`` block) so indented sub-keys are never misread as settings.
    Avoids a YAML dependency, matching the rest of the codebase.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0].isspace():  # indented → inside a nested block; ignore
            continue
        m = _GOOSE_KV_RE.match(raw)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def parse_goose_providers_block(text: str) -> dict[str, object] | None:
    """Parse the newer ``providers:``/``active_provider:`` config.yaml schema.

    Goose's config.yaml migrated (observed live, goose 1.37.0) from flat
    ``GOOSE_PROVIDER:``/``GOOSE_MODEL:`` scalars to::

        providers:
          ollama:
            enabled: true
            model: qwen3.6:35b-a3b
            configured: true
          openrouter:
            enabled: true
            model: qwen/qwen3.6-27b
            configured: true
        active_provider: openrouter

    A narrow line-scan state machine, not a YAML parser (matches
    ``parse_goose_config``'s own no-dependency convention) — it only recognizes
    this one fixed shape: a top-level ``providers:`` block, 2-space-indented
    provider names, and their 4-space-indented ``model:`` value, matched **by
    key name, not position** (real files interleave ``enabled``/``configured``
    around ``model`` in varying order). Blank lines and ``#``-comment lines
    inside the block are skipped, not treated as structure. Returns ``None``
    when neither ``providers:`` nor ``active_provider:`` is present anywhere in
    the file (an old-schema file) so callers can cleanly fall back.
    """
    active_provider: str | None = None
    models_by_provider: dict[str, str] = {}
    in_providers_block = False
    current_provider: str | None = None

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue

        if raw == "providers:":
            in_providers_block = True
            current_provider = None
            continue

        m_active = _ACTIVE_PROVIDER_RE.match(raw)
        if m_active and not raw[0].isspace():
            active_provider = m_active.group(1).strip().strip('"').strip("'")
            in_providers_block = False
            continue

        if in_providers_block:
            if not raw[0].isspace():
                # Any other unindented top-level key ends the providers: block.
                in_providers_block = False
                current_provider = None
                continue
            m_name = _PROVIDER_NAME_RE.match(raw)
            if m_name:
                current_provider = m_name.group(1)
                continue
            m_model = _PROVIDER_MODEL_RE.match(raw)
            if m_model and current_provider is not None:
                models_by_provider[current_provider] = m_model.group(1).strip('"').strip("'")
                continue
            # Other 4-space sub-keys (enabled:, configured:) — ignored by name.

    if active_provider is None and not models_by_provider:
        return None

    return {
        "active_provider": active_provider,
        "models_by_provider": models_by_provider,
        "model": models_by_provider.get(active_provider) if active_provider else None,
    }


def resolve_models(
    env: dict[str, str],
    config: dict[str, str],
    providers_block: dict[str, object] | None = None,
) -> dict[str, object]:
    """Resolve the provider + the env-effective and config-effective models.

    The **config** model is what plain ``goose run`` (no override) uses — the
    primary subject. The **env** model (``GOOSE_MODEL`` exported by
    ``goose-or``/``goose-backend``) overrides it only for invocations that set it.

    ``providers_block`` (``parse_goose_providers_block``'s result) is preferred over
    the flat ``GOOSE_PROVIDER``/``GOOSE_MODEL`` keys when present — ``active_provider:``
    is what current ``goose`` itself actually reads. ``schema_source`` in the return
    value ("v2" | "v1" | "none") tells callers (notably ``--fix``) which shape the
    config-effective value came from, since a "v2" model lives on an indented
    ``model:`` line ``apply_fix``'s column-0-anchored rewrite cannot safely reach.
    """
    new_schema_model = ""
    new_schema_provider = ""
    if providers_block is not None:
        # A providers:/active_provider: block was found -- this file is schema v2,
        # regardless of whether a model could be fully resolved from it (e.g. a
        # dangling active_provider naming a provider absent from the block).
        schema_source = "v2"
        new_schema_provider = str(providers_block.get("active_provider") or "")
        new_schema_model = str(providers_block.get("model") or "")
    elif config.get("GOOSE_MODEL") or config.get("GOOSE_PROVIDER"):
        schema_source = "v1"
    else:
        schema_source = "none"

    provider = env.get("GOOSE_PROVIDER") or new_schema_provider or config.get("GOOSE_PROVIDER") or ""
    config_model = new_schema_model or config.get("GOOSE_MODEL") or ""
    env_model = env.get("GOOSE_MODEL") or ""
    primary = config_model or env_model
    diverges = bool(env_model and config_model and env_model != config_model)
    return {
        "provider": provider,
        "config_model": config_model,
        "env_model": env_model,
        "primary_model": primary,
        "primary_source": "config.yaml" if config_model else ("env" if env_model else "none"),
        "diverges": diverges,
        "schema_source": schema_source,
    }


def index_catalog(catalog: list[dict[str, object]]) -> dict[str, bool]:
    """Map model id -> supports_tools from an OpenRouter /models payload list."""
    index: dict[str, bool] = {}
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("id")
        if not isinstance(mid, str):
            continue
        params = entry.get("supported_parameters") or []
        index[mid] = isinstance(params, list) and "tools" in params
    return index


def validate_model(model: str, index: dict[str, bool]) -> dict[str, object]:
    """Validate one slug against the catalog index.

    ``supports_tools`` is a GATE (a vision/no-tool model fails), not a PASS — only
    the live sentinel certifies an end-to-end run.
    """
    exists = model in index
    return {
        "model": model,
        "exists": exists,
        "supports_tools": bool(index.get(model, False)),
        "ok": exists and bool(index.get(model, False)),
    }


def suggest_fix(model: str, index: dict[str, bool]) -> str | None:
    """Suggest a hyphen slug for an invalid colon (Ollama-tag) slug, guarded.

    Returns a catalog-confirmed hyphen slug only when: the model is absent; it has
    a ``:``; the part before the FIRST colon is itself absent (so a real-but-offline
    ``…instruct:free`` is not rewritten); the suffix is not a known variant; and the
    hyphen-join (first colon -> ``-``) exists in the catalog. Otherwise ``None``.
    """
    if model in index or ":" not in model:
        return None
    base, _, suffix = model.partition(":")
    if base in index:
        return None
    if suffix in KNOWN_VARIANTS:
        return None
    candidate = f"{base}-{suffix}"
    return candidate if index.get(candidate, False) else None


def classify_goose_output(text: str, sentinel: str) -> dict[str, object]:
    """Classify goose stdout+stderr into a verdict (exit code not usable: goose
    exits 0 on 400/401/max-turns/success alike).

    Args:
        text: Combined stdout+stderr of the probe run.
        sentinel: The per-run nonce sentinel from :func:`_probe_fixture`. Only real tool
            execution can put it in the transcript — it never appears in the prompt.
    """
    if sentinel in text:
        return {"verdict": "pass", "exit": 0, "detail": "tool sentinel reached"}
    low = text.lower()
    if "not a valid model" in low or "bad request (400)" in low or ("400" in low and "model" in low):
        return {"verdict": "invalid-model", "exit": 1, "detail": "OpenRouter rejected the model id"}
    if "401" in low or "unauthorized" in low or "invalid api key" in low or "no api key" in low:
        return {"verdict": "auth-error", "exit": 2, "detail": "API key missing/invalid (not a model verdict)"}
    if "maximum number of" in low or "max turns" in low or "max-turns" in low:
        return {"verdict": "inconclusive", "exit": 2, "detail": "hit --max-turns before the sentinel; raise it"}
    return {"verdict": "early-stop", "exit": 1, "detail": "no sentinel and no known error — genuine early stop"}


def offline_syntax_suspect(provider: str, model: str) -> bool:
    """Without the catalog: flag a likely Ollama-tag-on-OpenRouter slug."""
    if provider != "openrouter" or ":" not in model:
        return False
    suffix = model.partition(":")[2]
    return suffix not in KNOWN_VARIANTS


# ---------------------------------------------------------------------------
# Edge I/O (network, filesystem, subprocess) — kept out of the import path
# ---------------------------------------------------------------------------

class SetupError(Exception):
    """A setup/parse/network/auth failure → exit code 2 (never a model verdict)."""


def read_config(path: Path) -> tuple[dict[str, str], dict[str, object] | None]:
    """Return (flat GOOSE_* scalars, new-schema providers-block parse or None)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except (OSError, UnicodeDecodeError) as exc:
        raise SetupError(f"could not read goose config {path}: {exc}") from exc
    return parse_goose_config(text), parse_goose_providers_block(text)


def fetch_catalog(timeout: float) -> list[dict[str, object]]:
    req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "agentteams-goose-preflight"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https literal)
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SetupError(f"could not fetch OpenRouter catalog: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SetupError(f"OpenRouter catalog was not valid JSON: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise SetupError("OpenRouter catalog JSON had no 'data' list")
    return data


def fetch_endpoints(model: str, timeout: float) -> list[dict[str, object]]:
    """Fetch the upstream backends OpenRouter can route ``model`` to.

    Which backend serves a request materially affects tool-calling reliability: the
    same model id can return a structured tool call from one backend and leak it into
    the reasoning stream on another (see docs_src/goose-cloud-providers.md). This
    surfaces the routing options so a caller can choose deliberately.

    Args:
        model: OpenRouter model slug, e.g. ``qwen/qwen3.6-27b``.
        timeout: Socket timeout in seconds.

    Returns:
        The endpoint dicts, each with at least ``provider_name``.

    Raises:
        SetupError: On transport failure, non-JSON payload, or a missing endpoint list.
    """
    url = f"{CATALOG_URL}/{model}/endpoints"
    req = urllib.request.Request(url, headers={"User-Agent": "agentteams-goose-preflight"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https literal)
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SetupError(f"could not fetch endpoints for '{model}': {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SetupError(f"endpoint listing for '{model}' was not valid JSON: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise SetupError(f"endpoint listing for '{model}' had no 'endpoints' list")
    return endpoints


def format_endpoints(model: str, endpoints: list[dict[str, object]]) -> str:
    """Render an endpoint listing for humans.

    Args:
        model: The model slug the endpoints belong to.
        endpoints: Endpoint dicts from :func:`fetch_endpoints`.

    Returns:
        A printable multi-line report.
    """
    lines = [f"upstream backends for {model}:"]
    for ep in endpoints:
        supported = ep.get("supported_parameters") or []
        tools = "tools=yes" if "tools" in supported else "tools=NO"
        ctx = ep.get("context_length") or "?"
        quant = ep.get("quantization") or "unknown"
        lines.append(f"  - {ep.get('provider_name', '?'):20s} {tools:9s} "
                     f"ctx={ctx} quant={quant}")
    lines.append("")
    lines.append("  Backends are NOT equally reliable for tool calling, even at tools=yes —")
    lines.append("  that flag means the parameter is accepted, not that the model's native")
    lines.append("  tool-call template is extracted correctly. Measure before trusting one;")
    lines.append("  pin with scripts/goose-openrouter-route-proxy.py --only <names>.")
    return "\n".join(lines)


def resolve_api_key(env: dict[str, str], env_file: str | None) -> str:
    """Resolve OPENROUTER_API_KEY by reference (env first, then a key=value file).

    Mirrors goose-backend.sh: extract ONLY the key line; never source a whole file.
    Returns the key string for subprocess use; callers must report presence only.
    """
    key = env.get("OPENROUTER_API_KEY", "")
    if key or not env_file:
        return key
    path = Path(env_file).expanduser()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(?:export\s+)?OPENROUTER_API_KEY=(.*)$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def live_probe(model: str, key: str, max_turns: int, timeout: float) -> dict[str, object]:
    """Run a tiny tool-using goose task on OpenRouter and classify the output."""
    env = dict(os.environ)
    env["OPENROUTER_API_KEY"] = key
    # auto, NOT chat: chat mode intercepts the tool call and invites the model to narrate
    # what it would have printed — the measured false-pass mechanism (2026-08-08). The probe
    # exists to certify tool execution, so it must run in the mode where tools execute.
    env["GOOSE_MODE"] = "auto"
    probe_text, sentinel, fixture = _probe_fixture()
    cmd = [
        "goose", "run", "--no-session", "--quiet",
        "--provider", "openrouter", "--model", model,
        "--max-turns", str(max_turns), "-t", probe_text,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, env=env,
        )
    except FileNotFoundError as exc:
        raise SetupError("goose binary not found on PATH") from exc
    except subprocess.TimeoutExpired:
        return {"verdict": "inconclusive", "exit": 2, "detail": f"goose probe timed out after {timeout:.0f}s"}
    finally:
        try:
            os.unlink(fixture)
        except OSError:
            pass
    return classify_goose_output((proc.stdout or "") + "\n" + (proc.stderr or ""), sentinel)


def apply_fix(path: Path, old_model: str, new_model: str) -> str:
    """Rewrite only the GOOSE_MODEL line old->new after a timestamped backup.

    The key never appears on the GOOSE_MODEL line, so the backup carries no secret.
    """
    text = path.read_text(encoding="utf-8")
    backup = path.with_suffix(path.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(text, encoding="utf-8")
    pattern = re.compile(r"^(GOOSE_MODEL:\s*).*$", re.MULTILINE)
    new_text, n = pattern.subn(rf"\g<1>{new_model}", text, count=1)
    if n == 0:
        raise SetupError(f"no GOOSE_MODEL line found in {path}")
    path.write_text(new_text, encoding="utf-8")
    return str(backup)


# ---------------------------------------------------------------------------
# Orchestration + reporting
# ---------------------------------------------------------------------------

def _build_report(args: argparse.Namespace) -> dict[str, object]:
    """Run the checks and return a JSON-safe report (no secrets). Exit code in
    report['exit']. Raises SetupError for exit-2 conditions.
    """
    config, providers_block = read_config(Path(args.config).expanduser())
    resolved = resolve_models(dict(os.environ), config, providers_block)
    report: dict[str, object] = {"resolved": resolved, "checks": [], "fix": None, "live": None}

    primary = str(resolved["primary_model"])
    if not primary:
        raise SetupError(
            f"no GOOSE_MODEL found in env or {args.config}. "
            "Set a provider/model (see docs_src/goose-system-prep.md §2)."
        )

    provider = str(resolved["provider"])
    if provider != "openrouter":
        report["exit"] = 0
        report["note"] = f"provider is '{provider or '<unset>'}', not openrouter — nothing to validate."
        return report

    if args.offline:
        suspect = offline_syntax_suspect(provider, primary)
        report["exit"] = 1 if suspect else 0
        report["note"] = (
            f"offline: '{primary}' looks like Ollama tag syntax (colon variant '{primary.partition(':')[2]}'). "
            "OpenRouter slugs use hyphens. Re-run online to confirm + get the exact fix."
            if suspect else "offline: catalog not checked; slug syntax looks plausible."
        )
        return report

    index = index_catalog(fetch_catalog(args.timeout))

    # Judge the config-effective model (what plain `goose run` uses) as primary.
    primary_result = validate_model(primary, index)
    report["checks"].append({**primary_result, "role": resolved["primary_source"]})
    fix = suggest_fix(primary, index) if not primary_result["ok"] else None
    report["fix"] = fix

    # Report a divergent env override for context (does not change the verdict).
    if resolved["diverges"]:
        report["checks"].append({**validate_model(str(resolved["env_model"]), index), "role": "env-override"})

    exit_code = 0 if primary_result["ok"] else 1

    if args.fix and fix:
        if resolved["schema_source"] == "v2":
            # apply_fix's column-0-anchored regex would either no-op (no top-level
            # GOOSE_MODEL: line exists to match) or, worse, insert one goose's
            # new-schema reader never consults — refuse rather than guess, naming
            # the exact indented line to edit by hand (Non-goal: safely rewriting
            # inside the nested providers: block is separable, higher-risk work).
            active = providers_block.get("active_provider") if providers_block else None
            report["fix"] = None
            report["fix_refused"] = (
                f"'{args.config}' uses the newer providers:/active_provider: schema — "
                f"--fix cannot safely rewrite an indented line automatically. Edit "
                f"'model: {fix}' under 'providers:\\n  {active or '<provider>'}:' by hand."
            )
        else:
            report["fix_applied"] = apply_fix(Path(args.config).expanduser(), primary, fix)
            # After a successful fix the plain-run path is healthy.
            exit_code = 0

    if args.live:
        target = fix if (args.fix and fix) else primary
        key = resolve_api_key(dict(os.environ), args.env_file)
        report["key_present"] = bool(key)
        if not key:
            report["live"] = {"verdict": "skipped", "exit": 0, "detail": "no OPENROUTER_API_KEY resolved"}
        elif not validate_model(target, index)["ok"]:
            report["live"] = {"verdict": "skipped", "exit": 1, "detail": "model invalid; skipping live probe"}
        else:
            live = live_probe(target, key, args.max_turns, args.probe_timeout)
            report["live"] = live
            if live["verdict"] in ("invalid-model", "early-stop"):
                exit_code = 1
            elif live["verdict"] in ("auth-error", "inconclusive"):
                exit_code = max(exit_code, 2)

    report["exit"] = exit_code
    return report


def _format_human(report: dict[str, object]) -> str:
    r = report["resolved"]
    lines: list[str] = []
    lines.append(f"  provider: {r['provider'] or '<unset>'}")
    lines.append(f"  config.yaml model (plain `goose run`): {r['config_model'] or '<unset>'}")
    if r["env_model"]:
        lines.append(f"  env GOOSE_MODEL override (goose-or path): {r['env_model']}")
    if r["diverges"]:
        lines.append("  ⚠ env override differs from config.yaml — plain `goose run` (e.g. the VS Code")
        lines.append("    task) uses the config.yaml model below, NOT the override.")
    for c in report.get("checks", []):
        mark = "✓" if c["ok"] else "✗"
        tools = "tools=yes" if c["supports_tools"] else ("tools=NO" if c["exists"] else "absent")
        lines.append(f"  {mark} [{c['role']}] {c['model']}: {'exists' if c['exists'] else 'NOT FOUND'}, {tools}")
    if report.get("fix"):
        lines.append(f"  → fix: change GOOSE_MODEL to '{report['fix']}' (Ollama uses ':', OpenRouter uses '-').")
        if r["schema_source"] == "v2":
            # 2026-07-24 (code-hygiene close-out finding): the default report used to
            # unconditionally point at `--fix`, but on a providers:/active_provider: config
            # that flag refuses (see _build_report) -- don't advise a command known to fail.
            lines.append("    config.yaml uses the newer providers:/active_provider: schema —")
            lines.append("    --fix would refuse; edit the indented 'model:' line by hand instead.")
        else:
            lines.append("    apply with: python scripts/goose-openrouter-preflight.py --fix")
    if report.get("fix_applied"):
        lines.append(f"  ✓ config.yaml updated (backup: {report['fix_applied']}).")
    if report.get("fix_refused"):
        lines.append(f"  ⚠ {report['fix_refused']}")
    if report.get("live"):
        lv = report["live"]
        lines.append(f"  live probe: {lv['verdict']} — {lv['detail']}")
    if report.get("note"):
        lines.append(f"  {report['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to goose config.yaml")
    parser.add_argument("--offline", action="store_true", help="syntax-only; skip the catalog fetch")
    parser.add_argument("--live", action="store_true", help="also run a goose tool probe (needs a key)")
    parser.add_argument("--fix", action="store_true", help="rewrite an invalid colon slug (backup first)")
    parser.add_argument("--env-file", default=os.environ.get("GOOSE_OPENROUTER_ENV_FILE"),
                        help="file holding OPENROUTER_API_KEY=… (for --live)")
    parser.add_argument("--timeout", type=float, default=20.0, help="catalog fetch timeout (s)")
    parser.add_argument("--probe-timeout", type=float, default=120.0, help="live probe timeout (s)")
    parser.add_argument("--max-turns", type=int, default=4, help="live probe --max-turns (>=3)")
    parser.add_argument("--quiet", action="store_true", help="suppress output on success")
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    parser.add_argument("--providers", metavar="MODEL", default=None,
                        help="list the upstream backends OpenRouter can route MODEL to, then "
                             "exit (backend choice affects tool-calling reliability)")
    args = parser.parse_args(argv)

    if args.providers:
        try:
            endpoints = fetch_endpoints(args.providers, args.timeout)
        except SetupError as exc:
            if args.json:
                print(json.dumps({"exit": 2, "error": str(exc)}, indent=2))
            else:
                print(f"goose-openrouter-preflight: SETUP ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"exit": 0, "model": args.providers, "endpoints": endpoints}, indent=2))
        else:
            print(format_endpoints(args.providers, endpoints))
        return 0

    try:
        report = _build_report(args)
    except SetupError as exc:
        if args.json:
            print(json.dumps({"exit": 2, "error": str(exc)}, indent=2))
        else:
            print(f"goose-openrouter-preflight: SETUP ERROR: {exc}", file=sys.stderr)
        return 2

    code = int(report["exit"])
    if args.json:
        print(json.dumps(report, indent=2))
    elif code == 0:
        if not args.quiet:
            print("goose-openrouter-preflight: FIXED" if report.get("fix_applied") else "goose-openrouter-preflight: OK")
            print(_format_human(report))
    else:
        print(f"goose-openrouter-preflight: {'FAIL' if code == 1 else 'INCONCLUSIVE'}", file=sys.stderr)
        print(_format_human(report), file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
