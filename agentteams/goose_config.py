"""
goose_config.py — locate and safely mutate Goose's config.yaml for source/model switching.

Powers the ``agentteams --goose-source/--goose-model/--goose-show`` CLI action. Three concerns:

* **Location protocol** (``resolve_goose_config_path``): explicit override → ask ``goose info``
  (authoritative — goose's own resolver) → ``$XDG_CONFIG_HOME``/platform default. Returns the
  path AND which method resolved it, so the choice is never silent.
* **Source registry** (``load_sources``): per-source default model + provider key env name,
  seeded for ollama + openrouter and extensible via ``~/.config/agentteams/goose-sources.json``.
* **Config mutation** (``set_provider_model``): set only the top-level ``GOOSE_PROVIDER`` /
  ``GOOSE_MODEL`` scalars with a column-0 anchor (never the nested ``extensions:`` keys),
  timestamped-backup-before-write, no provider keys ever read or written. Only applies to the
  older flat-key config.yaml schema — raises ``NewSchemaTargetError`` (no write, no backup)
  against the newer ``providers:``/``active_provider:`` schema (2026-07-24) rather than
  writing dead lines the newer reader never consults; see that exception's docstring.

config.yaml is the **persistent default** goose reads when no env override is set; an active
``GOOSE_PROVIDER``/``GOOSE_MODEL`` env (e.g. a ``goose-or`` shell) wins over it — callers must
surface that (see ``current_status``/``env_override``). ``read_config``/``current_status``
recognize both config.yaml schemas; ``set_provider_model`` writes only the older one.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Provider key env var resolved by reference only — never read/written by us.
_GOOSE_INFO_TIMEOUT = 8

# OpenRouter variant suffixes after the first ``:`` (a real variant, not an Ollama tag).
_OPENROUTER_VARIANTS = frozenset({"free", "nitro", "thinking", "beta", "online", "extended", "floor"})


@dataclass(frozen=True)
class SourceSpec:
    """A Goose source (provider) with its default model and provider-key env name."""
    default_model: str
    key_env: str | None = None   # env var name only — never a value
    host_env: str | None = None


# Seeded with the two providers in active use; defaults are catalog/local-verified.
# Extend or override per-source via ~/.config/agentteams/goose-sources.json.
BUILTIN_SOURCES: dict[str, SourceSpec] = {
    "ollama": SourceSpec(default_model="qwen3.6:35b-a3b", key_env=None, host_env="OLLAMA_HOST"),
    "openrouter": SourceSpec(default_model="qwen/qwen3.6-35b-a3b", key_env="OPENROUTER_API_KEY"),
}

DEFAULT_SOURCES_FILE = Path("~/.config/agentteams/goose-sources.json").expanduser()


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

def load_sources(user_file: Path | None = None) -> dict[str, SourceSpec]:
    """Built-in sources merged with an optional user JSON file (user wins per key).

    File shape: ``{"sources": {"<name>": {"default_model": "...", "key_env": "...",
    "host_env": "..."}}}``. Unreadable/invalid files are ignored with the built-ins kept.
    """
    sources = dict(BUILTIN_SOURCES)
    path = user_file if user_file is not None else DEFAULT_SOURCES_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return sources
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return sources
    user_sources = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(user_sources, dict):
        return sources
    for name, spec in user_sources.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        model = spec.get("default_model")
        if not isinstance(model, str) or not model:
            continue
        sources[name] = SourceSpec(
            default_model=model,
            key_env=spec.get("key_env") if isinstance(spec.get("key_env"), str) else None,
            host_env=spec.get("host_env") if isinstance(spec.get("host_env"), str) else None,
        )
    return sources


# ---------------------------------------------------------------------------
# config.yaml location protocol
# ---------------------------------------------------------------------------

_CONFIG_YAML_RE = re.compile(r"^\s*config\s+yaml\s*:\s*(.+?)\s*$", re.IGNORECASE)
# Status token goose appends when the file does not exist yet.
_MISSING_TOKEN_RE = re.compile(r"\s*\.\.\..*$")


def parse_goose_info_config_path(text: str) -> str | None:
    """Extract the config.yaml path from ``goose info`` stdout.

    Tolerant of fixed-column trailing padding and a trailing ``... missing (can create)``
    status token; does NOT split on internal whitespace (a Windows path may contain spaces).
    """
    for line in text.splitlines():
        m = _CONFIG_YAML_RE.match(line)
        if not m:
            continue
        candidate = _MISSING_TOKEN_RE.sub("", m.group(1)).strip()
        if candidate:
            return candidate
    return None


def _platform_default(env: dict[str, str], platform: str) -> Path:
    if platform.startswith("win"):
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Block" / "goose" / "config" / "config.yaml"
    xdg = env.get("XDG_CONFIG_HOME")
    base_dir = Path(xdg) if xdg else (Path.home() / ".config")
    return base_dir / "goose" / "config.yaml"


def resolve_goose_config_path(
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    runner=subprocess.run,
) -> tuple[Path, str]:
    """Resolve goose's config.yaml path and report which method found it.

    Order: explicit flag / ``AGENTTEAMS_GOOSE_CONFIG`` → ``goose info`` (authoritative)
    → ``$XDG_CONFIG_HOME``/platform default. ``runner`` is injectable for tests.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform

    chosen = explicit or env.get("AGENTTEAMS_GOOSE_CONFIG")
    if chosen:
        return Path(chosen).expanduser(), "explicit"

    info = _run_goose_info(runner)
    if info is not None:
        parsed = parse_goose_info_config_path(info)
        if parsed:
            return Path(parsed).expanduser(), "goose-info"

    fallback = _platform_default(env, platform)
    method = "xdg" if (not platform.startswith("win") and env.get("XDG_CONFIG_HOME")) else "platform-default"
    return fallback, method


def _run_goose_info(runner) -> str | None:
    """Return ``goose info`` stdout, or None if goose is absent/slow (then we fall back)."""
    try:
        proc = runner(
            ["goose", "info"], capture_output=True, text=True, check=False, timeout=_GOOSE_INFO_TIMEOUT,
        )
    except FileNotFoundError:
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout or ""


# ---------------------------------------------------------------------------
# config.yaml read / mutate
# ---------------------------------------------------------------------------

_TOP_LEVEL_KV_RE = re.compile(r"^(GOOSE_[A-Z0-9_]+):\s*(.*?)\s*$")
_ACTIVE_PROVIDER_RE = re.compile(r"^active_provider:\s*(.+?)\s*$")
_PROVIDER_NAME_RE = re.compile(r"^  ([a-zA-Z0-9_-]+):\s*$")
_PROVIDER_MODEL_RE = re.compile(r"^    model:\s*(.+?)\s*$")

# Cheap, sufficient-for-a-pre-write-check detector: does this text contain the newer
# providers:/active_provider: schema at all? Deliberately NOT the full parser below --
# set_provider_model needs this before it does any write, and a full parse isn't required
# to answer "is this new-schema" (confirmed: no ordering dependency on the full parser).
_NEW_SCHEMA_MARKER_RE = re.compile(r"^(providers|active_provider):", re.MULTILINE)


class NewSchemaTargetError(ValueError):
    """Raised when set_provider_model's target uses the providers:/active_provider: schema.

    Distinct from set_provider_model's other ValueError (missing provider/model args) --
    callers (the CLI) must be able to tell these apart and print a different message.
    """


def _parse_providers_block(text: str) -> dict[str, object] | None:
    """Parse the newer ``providers:``/``active_provider:`` config.yaml schema.

    Independently implemented from ``scripts/goose-openrouter-preflight.py``'s identical
    function (not shared: that script is deliberately standalone/dependency-free -- it is
    excluded from the packaged distribution per ``pyproject.toml``'s
    ``[tool.setuptools.packages.find] exclude`` -- so a shared module under ``scripts/``
    would be unimportable from an installed ``agentteams``, and one under ``agentteams/``
    would make the standalone script depend on the package being installed). See that
    script's docstring for the schema shape this recognizes. Returns ``None`` when neither
    ``providers:`` nor ``active_provider:`` is present (an old-schema file).
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

    if active_provider is None and not models_by_provider:
        return None

    return {
        "active_provider": active_provider,
        "models_by_provider": models_by_provider,
        "model": models_by_provider.get(active_provider) if active_provider else None,
    }


def read_config(path: Path) -> tuple[dict[str, str], dict[str, object] | None]:
    """Return (flat GOOSE_* scalars, new-schema providers-block parse or None).

    The flat-key scan ignores the nested ``extensions:`` block and the new schema's
    ``providers:``/``active_provider:`` lines equally (neither matches ``GOOSE_*:``).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except (OSError, UnicodeDecodeError):
        return {}, None
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw[0].isspace() or raw.lstrip().startswith("#"):
            continue
        m = _TOP_LEVEL_KV_RE.match(raw)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out, _parse_providers_block(text)


def _rewrite_or_insert(text: str, key: str, value: str) -> str:
    """Set a top-level ``KEY: value`` using a COLUMN-0 anchor (never a nested key)."""
    pattern = re.compile(rf"^({re.escape(key)}:\s*).*$", re.MULTILINE)
    new_text, n = pattern.subn(rf"\g<1>{value}", text, count=1)
    if n:
        return new_text
    sep = "" if (text == "" or text.endswith("\n")) else "\n"
    return f"{key}: {value}\n{sep}{text}"


def set_provider_model(
    path: Path, provider: str | None = None, model: str | None = None,
) -> str | None:
    """Set top-level GOOSE_PROVIDER/GOOSE_MODEL, preserving everything else.

    Writes a timestamped backup BEFORE the rewrite (no partial-write window). Creates a
    minimal config if the file is absent. Returns the backup path, or None when the file
    was newly created. Never reads or writes provider keys.

    Raises ``NewSchemaTargetError`` (2026-07-24), refusing the write entirely, when the
    target already uses the newer ``providers:``/``active_provider:`` schema: this
    function's flat-key rewrite would either no-op (no top-level ``GOOSE_PROVIDER:``/
    ``GOOSE_MODEL:`` line to match) or insert one goose's new-schema reader never
    consults -- confirmed empirically (round-1 plan-level audit) to silently produce a
    config.yaml whose real ``active_provider:``/nested ``model:`` are unchanged while
    ``current_status()`` reports the dead write back as if it had taken effect. No backup
    is written and the original file is untouched when refusing -- checked before any I/O
    besides the read already needed to detect this.
    """
    if provider is None and model is None:
        raise ValueError("set_provider_model requires provider and/or model")

    try:
        original = path.read_text(encoding="utf-8")
        existed = True
    except FileNotFoundError:
        original = ""
        existed = False

    if existed and _NEW_SCHEMA_MARKER_RE.search(original):
        block = _parse_providers_block(original)
        active = block.get("active_provider") if block else None
        raise NewSchemaTargetError(
            f"'{path}' uses the newer providers:/active_provider: schema -- refusing to "
            f"write flat GOOSE_PROVIDER:/GOOSE_MODEL: lines goose would never read. Edit "
            f"'model: {model or '<value>'}' under 'providers:\\n  {active or provider or '<provider>'}:' "
            "by hand, or 'active_provider: ' to switch which provider is active."
        )

    if not existed:
        # Fresh file: write a clean, canonical, ordered block.
        lines = []
        if provider is not None:
            lines.append(f"GOOSE_PROVIDER: {provider}")
        if model is not None:
            lines.append(f"GOOSE_MODEL: {model}")
        lines.append("GOOSE_MODE: auto")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return None

    new_text = original
    if provider is not None:
        new_text = _rewrite_or_insert(new_text, "GOOSE_PROVIDER", provider)
    if model is not None:
        new_text = _rewrite_or_insert(new_text, "GOOSE_MODEL", model)

    backup = str(path.with_suffix(path.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    Path(backup).write_text(original, encoding="utf-8")
    path.write_text(new_text, encoding="utf-8")
    return backup


# ---------------------------------------------------------------------------
# Guards / status (provider-aware model check, env override, current state)
# ---------------------------------------------------------------------------

def model_provider_mismatch(provider: str, model: str) -> str | None:
    """Return a human reason when a model slug is namespace-incompatible with the provider.

    ollama uses ``name:tag`` (no ``/``); OpenRouter uses ``vendor/slug`` (hyphens, ``:`` only
    for real variants). Catches the exact early-stop trap (an Ollama tag in an OpenRouter slug).
    """
    if provider == "ollama" and "/" in model:
        return (f"'{model}' looks like an OpenRouter slug (contains '/'); ollama uses "
                "'name:tag' (e.g. 'qwen3.6:35b-a3b').")
    if provider == "openrouter":
        if "/" not in model:
            return (f"'{model}' is not an OpenRouter 'vendor/model' slug.")
        suffix = model.partition(":")[2]
        if suffix and suffix not in _OPENROUTER_VARIANTS:
            return (f"'{model}' uses Ollama ':tag' syntax; OpenRouter slugs use hyphens "
                    f"(try '{model.replace(':', '-', 1)}'). Validate with "
                    "scripts/goose-openrouter-preflight.py.")
    return None


def env_override(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return any active GOOSE_PROVIDER/GOOSE_MODEL env override (masks config.yaml)."""
    env = os.environ if env is None else env
    out = {}
    for key in ("GOOSE_PROVIDER", "GOOSE_MODEL"):
        if env.get(key):
            out[key] = env[key]
    return out


def current_status(path: Path, env: dict[str, str] | None = None) -> dict[str, object]:
    """Snapshot the config.yaml provider/model and any masking env override.

    Prefers the newer providers:/active_provider: schema when present (2026-07-24) --
    previously this always reported the flat GOOSE_PROVIDER/GOOSE_MODEL keys, which are
    silently absent from any config.yaml written by current `goose configure`, so every
    new-schema file reported empty provider/model here regardless of its real, active
    configuration. `schema_source` ("v2"/"v1"/"none") tells a caller which shape resolved.
    """
    cfg, providers_block = read_config(path)
    if providers_block is not None:
        active = providers_block.get("active_provider") or ""
        model = providers_block.get("model") or ""
        schema_source = "v2"
    else:
        active = cfg.get("GOOSE_PROVIDER", "")
        model = cfg.get("GOOSE_MODEL", "")
        schema_source = "v1" if (active or model) else "none"
    return {
        "config_provider": active,
        "config_model": model,
        "config_mode": cfg.get("GOOSE_MODE", ""),
        "env_override": env_override(env),
        "schema_source": schema_source,
    }
