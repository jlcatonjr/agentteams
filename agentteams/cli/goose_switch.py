"""
goose_switch.py — CLI glue for ``agentteams --goose-source/--goose-model/--goose-show``.

Argument registration (``add_goose_arguments``) and the standalone action dispatch
(``run_goose_switch``) for switching Goose's persistent source/model in config.yaml.
Domain logic lives in ``agentteams.goose_config``; this module is the thin CLI layer.
"""
from __future__ import annotations

import argparse
import os

from agentteams import goose_config as gc


def add_goose_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the Goose source/model switch flags on the main parser."""
    group = parser.add_argument_group("goose source/model switch")
    group.add_argument(
        "--goose-source", metavar="NAME", default=None,
        help="Switch Goose's provider in config.yaml (e.g. ollama, openrouter). "
             "Applies that source's default model unless --goose-model is given.",
    )
    group.add_argument(
        "--goose-model", metavar="ID", default=None,
        help="Set Goose's model in config.yaml (with --goose-source: that source's model; "
             "alone: the current provider's model).",
    )
    group.add_argument(
        "--goose-show", action="store_true",
        help="Show the resolved config.yaml path, current provider/model, any masking env "
             "override, and the known sources.",
    )
    group.add_argument(
        "--goose-config", metavar="PATH", default=None,
        help="Override the goose config.yaml path (else resolved via goose info / XDG).",
    )


def run_goose_switch(args: argparse.Namespace) -> int:
    """Dispatch the goose switch action. Returns an exit code (0 ok, 2 setup/guard error)."""
    path, method = gc.resolve_goose_config_path(explicit=getattr(args, "goose_config", None))
    sources = gc.load_sources()
    source = getattr(args, "goose_source", None)
    model = getattr(args, "goose_model", None)

    # --goose-show alone (no switch requested): status only.
    if not source and not model:
        _print_status(path, method, sources)
        return 0

    # Resolve target provider + model (apply per-source default when model omitted).
    provider: str | None = None
    if source is not None:
        if source not in sources:
            known = ", ".join(sorted(sources))
            print(f"agentteams --goose-source: unknown source '{source}'. Known: {known}.")
            return 2
        provider = source
        if model is None:
            model = sources[source].default_model

    status = gc.current_status(path, os.environ)
    eff_provider = provider or str(status["config_provider"])

    # Guard 2: provider-aware model namespace validation (reject the silent-breakage slug).
    if model and eff_provider:
        mismatch = gc.model_provider_mismatch(eff_provider, model)
        if mismatch:
            print(f"agentteams --goose-model: {mismatch}\nNo change written.")
            return 2

    # Apply the persistent config edit (backup-before-write).
    try:
        backup = gc.set_provider_model(path, provider=provider, model=model)
    except OSError as exc:
        print(f"agentteams: could not write {path}: {exc}")
        return 2

    _print_applied(path, method, provider, model, backup)
    _print_guards(provider, model, sources, status)
    return 0


def _print_applied(path, method, provider, model, backup) -> None:
    print("goose source/model switch:")
    print(f"  config.yaml: {path}  (resolved via {method})")
    if provider:
        print(f"  GOOSE_PROVIDER -> {provider}")
    if model:
        print(f"  GOOSE_MODEL    -> {model}")
    print(f"  backup: {backup}" if backup else "  (created new config.yaml)")


def _print_guards(provider, model, sources, status) -> None:
    # Guard 1: an active env override masks the config edit in this shell.
    override = status["env_override"]
    if override:
        pairs = ", ".join(f"{k}={v}" for k, v in override.items())
        print(f"  ⚠ env override active ({pairs}) — Goose reads env BEFORE config.yaml, so this")
        print("    edit is masked in shells where that override is set (e.g. a `goose-or` shell).")
    # Guard 3: switching to a cloud source whose key env is unset.
    if provider and provider in sources:
        key_env = sources[provider].key_env
        if key_env and not os.environ.get(key_env):
            print(f"  ⚠ {key_env} is not set — export it before running Goose on {provider}.")
    if provider == "openrouter" or (model and "/" in str(model)):
        print("  → validate the model end-to-end: python scripts/goose-openrouter-preflight.py")


def _print_status(path, method, sources) -> None:
    status = gc.current_status(path, os.environ)
    print("goose configuration:")
    print(f"  config.yaml: {path}  (resolved via {method})")
    print(f"  GOOSE_PROVIDER: {status['config_provider'] or '<unset>'}")
    print(f"  GOOSE_MODEL:    {status['config_model'] or '<unset>'}")
    if status["config_mode"]:
        print(f"  GOOSE_MODE:     {status['config_mode']}")
    override = status["env_override"]
    if override:
        pairs = ", ".join(f"{k}={v}" for k, v in override.items())
        print(f"  ⚠ env override active ({pairs}) — this WINS over config.yaml for goose runs in this shell.")
    print("  known sources (default model):")
    for name in sorted(sources):
        spec = sources[name]
        key = f", key={spec.key_env}" if spec.key_env else ""
        print(f"    - {name}: {spec.default_model}{key}")
