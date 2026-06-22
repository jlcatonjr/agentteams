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
  timestamped-backup-before-write, no provider keys ever read or written.

config.yaml is the **persistent default** goose reads when no env override is set; an active
``GOOSE_PROVIDER``/``GOOSE_MODEL`` env (e.g. a ``goose-or`` shell) wins over it — callers must
surface that (see ``current_status``/``env_override``).
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


def read_config(path: Path) -> dict[str, str]:
    """Parse top-level ``GOOSE_*: value`` scalars; ignore the nested ``extensions:`` block."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw[0].isspace() or raw.lstrip().startswith("#"):
            continue
        m = _TOP_LEVEL_KV_RE.match(raw)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


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
    """
    if provider is None and model is None:
        raise ValueError("set_provider_model requires provider and/or model")

    try:
        original = path.read_text(encoding="utf-8")
        existed = True
    except FileNotFoundError:
        original = ""
        existed = False

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
    """Snapshot the config.yaml provider/model and any masking env override."""
    cfg = read_config(path)
    return {
        "config_provider": cfg.get("GOOSE_PROVIDER", ""),
        "config_model": cfg.get("GOOSE_MODEL", ""),
        "config_mode": cfg.get("GOOSE_MODE", ""),
        "env_override": env_override(env),
    }
