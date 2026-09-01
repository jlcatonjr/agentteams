"""Offline unit tests for scripts/goose-recover.py."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "goose-recover.py"


def _load():
    spec = importlib.util.spec_from_file_location("goose_recover", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gr = _load()


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _process(pid=123, service="goose-openrouter-route-proxy"):
    script = gr.ROUTE_PROXY.resolve()
    return gr._ProcessInfo(
        pid, script, service,
        ("python3", "-u", str(script), "--port", "8791", "--only", "Alibaba"),
        f"python3 {script.name}",
    )


def test_import_has_no_operational_side_effects():
    assert callable(gr.main)


def test_tracker_path_honors_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert gr._tracker_path() == tmp_path / "goose" / "projects.json"


def test_healthy_tracker_is_accepted(tmp_path):
    tracker = tmp_path / "projects.json"
    tracker.write_text(json.dumps({"projects": {"repo": {}}}))
    assert gr._inspect_tracker(tracker).kind == "healthy"


def test_recognized_trailing_garbage_is_repairable(tmp_path):
    tracker = tmp_path / "projects.json"
    tracker.write_text('{"projects": {}}\n: null\n}}\n')
    state = gr._inspect_tracker(tracker)
    assert state.kind == "repairable"
    assert state.value == {"projects": {}}


def test_bare_closing_brace_trailing_garbage_is_repairable(tmp_path):
    # 2026-08-10: observed in the wild -- same non-atomic-write root cause as the
    # ": null" tail, but a different leftover shape (no null token, just stale
    # closing braces/whitespace from an older, longer write).
    tracker = tmp_path / "projects.json"
    tracker.write_text('{"projects": {"repo": {}}}\n    }\n  }\n}')
    state = gr._inspect_tracker(tracker)
    assert state.kind == "repairable"
    assert state.value == {"projects": {"repo": {}}}


@pytest.mark.parametrize("content", [
    '{"projects": {',
    '{"projects": {}} trailing',
    '{"projects": {}}{"other": 1}',
    '[1, 2, 3]: null}',
    '{"not_projects": {}}',
])
def test_unknown_or_truncated_tracker_corruption_is_refused(tmp_path, content):
    tracker = tmp_path / "projects.json"
    tracker.write_text(content)
    assert gr._inspect_tracker(tracker).kind == "unsupported"


def test_repair_creates_private_backup_and_valid_tracker(tmp_path):
    tracker = tmp_path / "projects.json"
    source = b'{"projects": {"repo": {}}}\n: null\n}}\n'
    tracker.write_bytes(source)
    backup = gr._repair_tracker(tracker, gr._inspect_tracker(tracker))

    assert backup.read_bytes() == source
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert backup.name.startswith("projects.json.bak-")
    assert json.loads(tracker.read_text()) == {"projects": {"repo": {}}}


def test_repair_refuses_when_source_changes_before_replace(monkeypatch, tmp_path):
    tracker = tmp_path / "projects.json"
    tracker.write_text('{"projects": {}}: null}}')
    state = gr._inspect_tracker(tracker)
    original_backup = gr._exclusive_backup

    def change_after_backup(path, source):
        backup = original_backup(path, source)
        path.write_text('{"projects": {"new": {}}}')
        return backup

    monkeypatch.setattr(gr, "_exclusive_backup", change_after_backup)
    with pytest.raises(gr._RecoveryError, match="changed during repair"):
        gr._repair_tracker(tracker, state)
    assert json.loads(tracker.read_text()) == {"projects": {"new": {}}}
    assert not list(tmp_path.glob(".projects.json.tmp-*"))


def test_private_log_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = gr._log_path("goose-openrouter-route-proxy")
    descriptor = gr._open_log(path)
    os.close(descriptor)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_partial_backup_is_removed_on_write_failure(monkeypatch, tmp_path):
    tracker = tmp_path / "projects.json"

    class FailingStream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def write(self, data):
            raise OSError("disk full")

    monkeypatch.setattr(gr.os, "fdopen", lambda *args, **kwargs: FailingStream())
    with pytest.raises(OSError, match="disk full"):
        gr._exclusive_backup(tracker, b"source")
    assert not list(tmp_path.glob("projects.json.bak-*"))


def test_process_info_recognizes_known_relative_proxy():
    script = "goose-openrouter-route-proxy.py"
    service = "goose-openrouter-route-proxy"
    def runner(command, **kwargs):
        if command[0] == "ps":
            return _completed(stdout=f"python3 -u scripts/{script} --port 8791 --only Alibaba\n")
        return _completed(stdout=f"p123\nn{gr.REPO_ROOT}\n")

    info = gr._process_info(123, runner)
    assert info is not None
    assert info.service == service
    assert info.restart_command[-4:] == ("--port", "8791", "--only", "Alibaba")
    assert Path(info.restart_command[2]).is_absolute()


def test_unknown_listener_is_refused_before_health_probe(monkeypatch):
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner=subprocess.run: (77,))
    monkeypatch.setattr(gr, "_process_info", lambda pid, runner=subprocess.run: None)
    monkeypatch.setattr(
        gr, "_health_service",
        lambda *args, **kwargs: pytest.fail("unknown listener must not be probed"),
    )
    with pytest.raises(gr._RecoveryError, match="unknown listener PID 77"):
        gr._inspect_listener(gr.PORT)


def test_service_fingerprint_must_match_process_owner(monkeypatch):
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner=subprocess.run: (123,))
    monkeypatch.setattr(gr, "_process_info", lambda pid, runner=subprocess.run: _process(pid))
    monkeypatch.setattr(gr, "_health_service", lambda port: "goose-cost-proxy")
    with pytest.raises(gr._RecoveryError, match="identity mismatch"):
        gr._inspect_listener(gr.PORT)


def test_health_service_reports_http_error_code(monkeypatch):
    error = urllib.error.HTTPError("http://127.0.0.1", 502, "bad gateway", {}, None)
    monkeypatch.setattr(gr.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(gr._RecoveryError, match="HTTP 502"):
        gr._health_service(gr.PORT)


def test_check_mode_never_starts_or_stops_proxy(monkeypatch):
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_inspect_listener", lambda port: _process())
    monkeypatch.setattr(gr, "_start_proxy", lambda *args, **kwargs: pytest.fail("start called"))
    monkeypatch.setattr(gr, "_stop_proxy", lambda *args, **kwargs: pytest.fail("stop called"))
    assert gr.main(["--check"]) == 0


def test_check_mode_reports_missing_proxy(monkeypatch):
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_inspect_listener", lambda port: None)
    assert gr.main(["--check"]) == 1


def test_restart_without_listener_is_refused(monkeypatch):
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_inspect_listener", lambda port: None)
    monkeypatch.setattr(gr, "_start_proxy", lambda *args, **kwargs: pytest.fail("start called"))
    assert gr.main(["--restart"]) == 2


def test_normal_mode_starts_route_proxy_without_duplicate_allowlist(monkeypatch):
    started = []
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_inspect_listener", lambda port: None)
    monkeypatch.setattr(gr, "_start_proxy", lambda command, service: started.append((command, service)))
    assert gr.main([]) == 0
    command, service = started[0]
    assert service == "goose-openrouter-route-proxy"
    assert "--only" not in command


def test_restart_preserves_exact_known_proxy_command(monkeypatch):
    info = _process()
    calls = []
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_inspect_listener", lambda port: info)
    monkeypatch.setattr(gr, "_stop_proxy", lambda current: calls.append(("stop", current)))
    monkeypatch.setattr(
        gr, "_start_proxy",
        lambda command, service: calls.append(("start", tuple(command), service)),
    )
    assert gr.main(["--restart"]) == 0
    assert calls == [
        ("stop", info),
        ("start", info.restart_command, "goose-openrouter-route-proxy"),
    ]


def test_stop_reverifies_owner_and_waits_for_free_port(monkeypatch):
    info = _process()
    listeners = iter([(123,), ()])
    signals = []
    monkeypatch.setattr(gr, "_inspect_listener", lambda port, runner=subprocess.run: info)
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner=subprocess.run: next(listeners))
    gr._stop_proxy(info, kill=lambda pid, sig: signals.append((pid, sig)))
    assert signals == [(123, 0), (123, gr.signal.SIGTERM)]


def test_listener_pids_classifies_absent_and_failed_lsof(monkeypatch):
    monkeypatch.setattr(gr.shutil, "which", lambda command: "/usr/sbin/lsof")
    assert gr._listener_pids(gr.PORT, lambda *args, **kwargs: _completed(returncode=1)) == ()
    with pytest.raises(gr._RecoveryError, match="lsof failed"):
        gr._listener_pids(
            gr.PORT,
            lambda *args, **kwargs: _completed(returncode=2, stderr="denied"),
        )


def test_wait_for_listener_accepts_expected_started_process(monkeypatch):
    process = SimpleNamespace(pid=456, poll=lambda: None)
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner: (456,))
    monkeypatch.setattr(gr, "_health_service", lambda port: "goose-openrouter-route-proxy")
    gr._wait_for_listener(
        456, "goose-openrouter-route-proxy", process, subprocess.run, timeout=0.1,
    )


def test_wait_for_listener_reports_early_process_exit():
    process = SimpleNamespace(pid=456, poll=lambda: 2)
    with pytest.raises(gr._RecoveryError, match="exited during startup with status 2"):
        gr._wait_for_listener(
            456, "goose-openrouter-route-proxy", process, subprocess.run, timeout=0.1,
        )


def test_main_runtime_failure_exit_code(monkeypatch):
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_inspect_listener", lambda port: (_ for _ in ()).throw(OSError("boom")))
    assert gr.main([]) == 3


def test_default_route_command_uses_proxy_owned_defaults():
    command = gr._default_route_command()
    assert str(gr.ROUTE_PROXY.resolve()) in command
    assert "--only" not in command


# --------------------------------------------------------------------------- #
# --force-restart: identity-only resolve + SIGTERM->SIGKILL for a hung proxy
# --------------------------------------------------------------------------- #

def test_resolve_listener_skips_health_probe(monkeypatch):
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner=subprocess.run: (123,))
    monkeypatch.setattr(gr, "_process_info", lambda pid, runner=subprocess.run: _process(pid))
    monkeypatch.setattr(
        gr, "_health_service",
        lambda *a, **k: pytest.fail("_resolve_listener must not probe /healthz"),
    )
    info = gr._resolve_listener(gr.PORT)
    assert info is not None and info.pid == 123


def test_resolve_listener_refuses_unknown_pid(monkeypatch):
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner=subprocess.run: (77,))
    monkeypatch.setattr(gr, "_process_info", lambda pid, runner=subprocess.run: None)
    with pytest.raises(gr._RecoveryError, match="unknown listener PID 77"):
        gr._resolve_listener(gr.PORT)


def test_resolve_listener_refuses_ambiguous_set(monkeypatch):
    monkeypatch.setattr(gr, "_listener_pids", lambda port, runner=subprocess.run: (1, 2))
    with pytest.raises(gr._RecoveryError, match="ambiguous listener set"):
        gr._resolve_listener(gr.PORT)


def test_inspect_listener_still_enforces_health_after_refactor(monkeypatch):
    monkeypatch.setattr(gr, "_resolve_listener", lambda port, runner=subprocess.run: _process(123))
    monkeypatch.setattr(gr, "_health_service", lambda port: "goose-openrouter-route-proxy")
    info = gr._inspect_listener(gr.PORT)
    assert info is not None and info.service == "goose-openrouter-route-proxy"


def test_force_restart_recovers_hung_proxy(monkeypatch):
    info = _process()
    calls = []
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_resolve_listener", lambda port: info)
    monkeypatch.setattr(gr, "_force_stop_proxy", lambda current: calls.append(("force_stop", current)))
    monkeypatch.setattr(
        gr, "_start_proxy",
        lambda command, service: calls.append(("start", tuple(command), service)),
    )
    assert gr.main(["--force-restart"]) == 0
    assert calls == [
        ("force_stop", info),
        ("start", info.restart_command, "goose-openrouter-route-proxy"),
    ]


def test_force_restart_starts_default_when_absent(monkeypatch):
    started = []
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))
    monkeypatch.setattr(gr, "_resolve_listener", lambda port: None)
    monkeypatch.setattr(gr, "_force_stop_proxy", lambda *a, **k: pytest.fail("must not signal when absent"))
    monkeypatch.setattr(gr, "_start_proxy", lambda command, service: started.append((tuple(command), service)))
    assert gr.main(["--force-restart"]) == 0
    command, service = started[0]
    assert service == "goose-openrouter-route-proxy"
    assert "--only" not in command


def test_force_restart_refuses_unknown_listener(monkeypatch):
    monkeypatch.setattr(gr, "_check_tracker", lambda check_only: (True, False))

    def refuse(port):
        raise gr._RecoveryError("refusing unknown listener PID 77 on port 8791")

    monkeypatch.setattr(gr, "_resolve_listener", refuse)
    monkeypatch.setattr(gr, "_force_stop_proxy", lambda *a, **k: pytest.fail("must not signal unknown"))
    monkeypatch.setattr(gr, "_start_proxy", lambda *a, **k: pytest.fail("must not start over unknown"))
    assert gr.main(["--force-restart"]) == 2


def test_force_stop_escalates_to_sigkill(monkeypatch):
    info = _process()
    monkeypatch.setattr(gr, "PROCESS_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(gr, "_resolve_listener", lambda port, runner=subprocess.run: info)
    signals: list[tuple[int, int]] = []

    def fake_pids(port, runner=subprocess.run):
        # Port frees only once SIGKILL has been delivered (SIGTERM was ignored).
        return () if (info.pid, gr.signal.SIGKILL) in signals else (info.pid,)

    monkeypatch.setattr(gr, "_listener_pids", fake_pids)
    gr._force_stop_proxy(info, kill=lambda pid, sig: signals.append((pid, sig)))
    assert signals == [
        (info.pid, 0),
        (info.pid, gr.signal.SIGTERM),
        (info.pid, gr.signal.SIGKILL),
    ]


def test_force_stop_sigterm_suffices(monkeypatch):
    info = _process()
    monkeypatch.setattr(gr, "PROCESS_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(gr, "_resolve_listener", lambda port, runner=subprocess.run: info)
    signals: list[tuple[int, int]] = []

    def fake_pids(port, runner=subprocess.run):
        return () if (info.pid, gr.signal.SIGTERM) in signals else (info.pid,)

    monkeypatch.setattr(gr, "_listener_pids", fake_pids)
    gr._force_stop_proxy(info, kill=lambda pid, sig: signals.append((pid, sig)))
    assert signals == [(info.pid, 0), (info.pid, gr.signal.SIGTERM)]
    assert (info.pid, gr.signal.SIGKILL) not in signals


def test_force_stop_refuses_when_owner_changed(monkeypatch):
    info = _process(pid=123)
    other = _process(pid=999)
    monkeypatch.setattr(gr, "_resolve_listener", lambda port, runner=subprocess.run: other)
    with pytest.raises(gr._RecoveryError, match="ownership changed"):
        gr._force_stop_proxy(info, kill=lambda pid, sig: pytest.fail("must not signal a changed owner"))


def test_check_and_force_restart_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        gr.main(["--check", "--force-restart"])


def test_restart_and_force_restart_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        gr.main(["--restart", "--force-restart"])