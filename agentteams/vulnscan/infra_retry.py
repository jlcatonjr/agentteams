"""Same-day re-runs of a daily run Docker left partial (``daily --catch-up``), and the summaries they replace.

:func:`infra_retry_due` decides whether today's partial run is worth re-running now; the retries are counted in
the run directory (:data:`INFRA_RETRY_FILE`), and :func:`_archive_summary` keeps the summary a re-run replaces.
Split from :mod:`.daily`, which re-exports these names and runs the catch-up itself (``daily.catch_up``).
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path

from agentteams.vulnscan import docker_readiness, paths
from agentteams.vulnscan.report import EXIT_UNSCANNED

#: Retries of a same-day run left partial by Docker (catch_up), counted in the run directory.
INFRA_RETRY_FILE = "infra-retry.count"


def _stale(cfg: dict) -> tuple[bool, int | None]:
    """Return (no completed run within ``stale_after_hours``, hours since the last one or None)."""
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    if not daily.heartbeat_path().exists():
        return True, None
    beat = daily._read_heartbeat()
    if isinstance(beat, str):
        return True, None
    age = (_dt.datetime.now(_dt.UTC) - beat[0]).total_seconds() / 3600
    return age > float(cfg.get("stale_after_hours", daily.DEFAULT_CONFIG["stale_after_hours"])), max(int(age), 0)


def _max_retries(cfg: dict) -> int:
    """Return ``catch_up.max_retries_per_day`` (the default when unset)."""
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    catch = cfg.get("catch_up") if isinstance(cfg.get("catch_up"), dict) else {}
    return int(catch.get("max_retries_per_day", daily.DEFAULT_CONFIG["catch_up"]["max_retries_per_day"]))


def _retries(run_dir: Path) -> int:
    """Return how many infrastructure retries ran for ``run_dir`` (0 when none, or the count is unreadable)."""
    try:
        text = (run_dir / INFRA_RETRY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    return int(text) if text.isdigit() else 0


def infra_retry_due(cfg: dict, *, today: str | None = None) -> Path | None:
    """Return today's run directory when its run is worth re-running now because Docker made it partial, else None.

    All of: the last run (heartbeat) exited 3; it is today's run; its ``summary.json`` names Docker as a cause (an
    ``infra.*`` code in ``infra_codes`` or ``remediation.docker``; feed error text is never matched); fewer than
    ``catch_up.max_retries_per_day`` retries have run for that date (:data:`INFRA_RETRY_FILE` in the run
    directory); and Docker is ready now (:func:`docker_readiness.ensure`, read-only: nothing is started here,
    the retry's own run does that when ``docker.autostart`` allows it).

    Args:
        cfg: Daily config.
        today: Override the date (tests).

    Returns:
        The run directory, or None.
    """
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    beat = daily._read_heartbeat()
    if isinstance(beat, str) or beat[1] != EXIT_UNSCANNED:
        return None
    run_dir = paths.state_dir() / (today or _dt.date.today().isoformat())
    try:
        data = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    rem = data.get("remediation") if isinstance(data.get("remediation"), dict) else {}
    layers = rem.get("docker") if isinstance(rem.get("docker"), dict) else {}
    codes = [v.get("code", "") for v in layers.values() if isinstance(v, dict)]
    codes += data.get("infra_codes") if isinstance(data.get("infra_codes"), list) else []
    infra = any(isinstance(c, str) and c.startswith("infra.") for c in codes)
    if not infra or _retries(run_dir) >= _max_retries(cfg):
        return None
    return run_dir if docker_readiness.ensure(cfg, start=False).ok else None


def _archive_summary(run_dir: Path) -> list[Path]:
    """Copy an existing ``summary.json`` / ``.md`` to ``summary.<HHMMSS>.{json,md}`` before a re-run rewrites them.

    ``HHMMSS`` is the local time the earlier summary was written (``-1``, ``-2`` ... on a clash). Readers keep
    reading ``summary.json`` only (:func:`daily._previous_summary`, :func:`paths.run_dirs`).

    Returns:
        The copies written.

    Raises:
        OSError: A copy failed (the caller then writes nothing: the earlier summary stays in place).
    """
    old = [p for p in (run_dir / "summary.json", run_dir / "summary.md") if p.is_file()]
    if not old:
        return []
    stamp = _dt.datetime.fromtimestamp(old[0].stat().st_mtime).strftime("%H%M%S")
    base, n = stamp, 0
    while any((run_dir / f"summary.{base}{p.suffix}").exists() for p in old):
        n += 1
        base = f"{stamp}-{n}"
    out = []
    for p in old:
        dest = run_dir / f"summary.{base}{p.suffix}"
        shutil.copy2(p, dest)
        out.append(dest)
    return out
