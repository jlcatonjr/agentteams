"""R5 (rc.4): secrets files the DLP check cannot use fail closed.

The secrets directory is mounted whole (no per-file name or path limits); an unreadable directory or an
unusable file ends the run ``not-run`` unless ``agent.dlp_skip_ok`` acknowledges that exact file version by
sha256; the gateway's own unreadable count does the same (exit 2). Paths reach the terminal only. Docker is
faked throughout; secret values are built at runtime and no real secrets directory is read.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_run, agent_settings, gateway, gateway_context, gateway_dlp, paths, review
from tests._vulnscan_helpers import operator_env
from tests.test_vulnscan_agent import _a, _launchable, dirs  # noqa: F401  (dirs: a fixture)
from tests.test_vulnscan_gateway_dlp import fake

needs_modes = pytest.mark.skipif(os.geteuid() == 0, reason="root reads files whatever their mode")


def _secrets(root: Path, name: str = "secrets", **files: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare(tmp_path: Path, secrets: Path | list[Path], **kw: object) -> gateway_context.Context:
    return gateway_context.prepare(tmp_path / "gw", kit_dir=tmp_path / "kit", secrets_dir=secrets, entries=[], **kw)


@pytest.fixture(autouse=True)
def _no_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No real home (so no real secrets directory), no scutil, no ssh."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(gateway_context, "_scutil", lambda key: "")


# ---------------------------------------------------------------- mounts

def test_a_comma_colon_or_quote_in_the_home_path_still_mounts(tmp_path: Path) -> None:
    home = tmp_path / 'Smith, J: "ops"'
    secrets = _secrets(home, **{"vendor.env": "X_TOKEN=" + fake(20) + "\n", ".env": "Y_TOKEN=" + fake(21) + "\n"})
    ctx = _prepare(tmp_path, secrets)
    assert ctx.secrets_dirs == (secrets,)
    mounts, flags = gateway_context.argv_parts(ctx)
    spec = mounts[mounts.index("--mount") + 1]
    assert next(csv.reader([spec])) == ["type=bind", f"source={secrets}", "target=/run/secrets/dlp/0", "readonly"]
    assert flags[-4:] == ["--dlp-secrets-mount", "/run/secrets/dlp/0", "--dlp-skip-ok-file", "/run/dlp/skip-ok"]
    with pytest.raises(ValueError, match="line break"):
        gateway_context.bind_spec(Path("/a\nb"), "/x")


def test_the_fingerprint_placeholder_has_one_secrets_directory_and_no_machine_value() -> None:
    mounts, flags = gateway_context.argv_parts(gateway_context.placeholder())
    assert mounts[-2:] == ["--mount", "type=bind,source=/secrets,target=/run/secrets/dlp/0,readonly"]
    assert "--dlp-secrets-file" not in flags


# ---------------------------------------------------------------- the host check

@needs_modes
def test_an_unreadable_directory_refuses_and_a_missing_one_does_not(tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _prepare(tmp_path, tmp_path / "absent")
    assert ctx.secrets_dirs == () and "secrets_dirs_missing=1" in (ctx.directory / "notes").read_text()
    locked = _secrets(tmp_path, **{"a.env": "X=1\n"})
    locked.chmod(0)
    try:
        with pytest.raises(gateway_context.DlpRefused) as exc:
            _prepare(tmp_path, locked)
    finally:
        locked.chmod(0o700)
    assert str(locked) not in str(exc.value) and str(locked) in capsys.readouterr().err   # the terminal only
    notes = (tmp_path / "gw" / "dlp" / "notes").read_text()
    assert "secrets_problems=1" in notes and str(tmp_path) not in notes


@needs_modes
def test_unusable_files_refuse_with_their_paths_on_the_terminal_only(tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("Z_TOKEN=" + fake(22) + "\n")
    secrets = _secrets(tmp_path, **{"locked.env": "X_TOKEN=" + fake(20) + "\n"})
    (secrets / "locked.env").chmod(0)
    (secrets / "link.env").symlink_to(outside)
    os.mkfifo(secrets / "pipe.env")                       # never opened: a read would block
    with pytest.raises(gateway_context.DlpRefused, match="3 secrets"):
        _prepare(tmp_path, secrets)
    err = capsys.readouterr().err
    assert "locked.env: cannot be read" in err and "link.env: a symlink out of" in err and "pipe.env: not a regular" in err
    (secrets / "locked.env").chmod(0o600)


def test_an_acknowledgement_binds_one_file_version(tmp_path: Path) -> None:
    secrets = _secrets(tmp_path, **{"vendor.env": "X_TOKEN=" + fake(20) + "\n"})
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("Z_TOKEN=" + fake(22) + "\n")
    link = secrets / "link.env"
    link.symlink_to(outside)
    ok = [{"path": str(link), "sha256": _sha(link)}]
    ctx = _prepare(tmp_path, secrets, skip_ok=ok)
    assert (ctx.directory / "skip-ok").read_text().splitlines()[1:] == ["/run/secrets/dlp/0/link.env"]
    assert "secrets_skip_ok=1" in (ctx.directory / "notes").read_text()
    outside.write_text("Z_TOKEN=" + fake(23) + "\n")      # an edit: the entry no longer matches
    with pytest.raises(gateway_context.DlpRefused):
        _prepare(tmp_path, secrets, skip_ok=ok)
    assert "secrets_skip_ok_unmatched=1" in (tmp_path / "gw" / "dlp" / "notes").read_text()


@pytest.mark.parametrize("bad", ["x", [{"path": "/a"}], [{"path": "/a", "sha256": "A" * 64}],
                                 [{"path": "", "sha256": "a" * 64}], [{"path": "/a", "sha256": "a" * 64, "x": 1}]])
def test_validate_agent_refuses_a_malformed_dlp_skip_ok(bad: object) -> None:
    assert agent_settings.DEFAULT_AGENT["dlp_skip_ok"] == []
    with pytest.raises(ValueError, match=r"agent\.dlp_skip_ok"):
        agent_settings.validate_agent({"dlp_skip_ok": bad})
    agent_settings.validate_agent({"dlp_skip_ok": [{"path": "~/s/a.env", "sha256": "a" * 64}]})


# ---------------------------------------------------------------- the gateway

def _gateway_args(tmp_path: Path, secrets: Path, skip: list[str]) -> list[str]:
    key = tmp_path / "k.env"
    key.write_text("OPENROUTER_API_KEY=" + fake(30) + "\n")
    (tmp_path / "skip-ok").write_text("".join(f"{s}\n" for s in skip))
    return ["--model", "m", "--key-file", str(key), "--dlp-secrets-dir", "", "--spend-file", str(tmp_path / "s.json"),
            "--dlp-secrets-mount", str(secrets), "--dlp-skip-ok-file", str(tmp_path / "skip-ok")]


@needs_modes
def test_the_gateway_counts_unreadable_files_exits_2_and_records_counts_only(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    value = fake(24, 3)
    secrets = _secrets(tmp_path, **{"a.env": f"A_TOKEN={value}\n", "b.env": "B_TOKEN=" + fake(20) + "\n"})
    (secrets / "b.env").chmod(0)
    try:
        assert gateway.main(_gateway_args(tmp_path, secrets, [])) == 2
        marker = (tmp_path / gateway_dlp.REFUSED_NAME).read_text()
        assert "1 DLP secrets file(s) could not be read" in marker and str(secrets) not in marker
        values, bad_dirs, bad_files, skipped = gateway_dlp.load_secret_mounts([secrets], [str(secrets / "b.env")])
        assert (value in values, bad_dirs, bad_files, skipped) == (True, 0, 0, 1)   # acknowledged: skipped
    finally:
        (secrets / "b.env").chmod(0o600)
    assert gateway_dlp.load_secret_mounts([tmp_path / "absent"])[1] == 1           # a mount that is not there
    (secrets / "c.env").write_bytes(b"\xff\xfe")                                     # not UTF-8 text: unusable too
    assert gateway_dlp.load_secret_mounts([secrets])[2] == 1
    capsys.readouterr()


def test_raise_if_refused_reads_the_marker_and_prepare_clears_it(tmp_path: Path) -> None:
    gw = tmp_path / "gw"
    gw.mkdir()
    gateway_context.raise_if_refused(gw)                  # no marker: nothing
    (gw / gateway_dlp.REFUSED_NAME).write_text("2 DLP secrets file(s) could not be read\n")
    with pytest.raises(gateway_context.DlpRefused, match="exit 2.*2 DLP secrets file"):
        gateway_context.raise_if_refused(gw)
    _prepare(tmp_path, tmp_path / "absent")
    gateway_context.raise_if_refused(gw)                  # an earlier start's refusal never ends a new one


# ---------------------------------------------------------------- the run

def test_a_gateway_exit_2_ends_the_run_not_run_with_no_path(dirs: dict[str, Path],  # noqa: F811
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    secrets = _secrets(Path(os.environ["HOME"]) / ".config", "agentteams-secrets", **{"a.env": "X=1\n"})

    def docker(argv: list[str], **kw: object) -> object:
        calls.append(argv)
        if argv[:3] == ["docker", "run", "-d"] and agent_run.GATEWAY in argv:   # the gateway refuses at once
            (dirs["run"] / "gateway" / gateway_dlp.REFUSED_NAME).write_text("1 DLP secrets file(s) could not be read")
            return agent_run.subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ["docker", "network", "connect"]:
            raise RuntimeError("docker network connect failed: no such container")
        return agent_run.subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(agent_run, "_docker", docker)
    status = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert status["run_state"] == "not-run" and "refused to start (exit 2)" in status["reason"]
    assert not any(c[0] == "popen" for c in calls)        # no agent container was started
    record = (dirs["run"] / "agent" / "status.json").read_text()
    assert str(secrets) not in record and str(dirs["tmp"]) not in status["reason"]


@needs_modes
def test_an_unusable_host_file_ends_the_run_not_run_before_any_gateway(dirs: dict[str, Path],  # noqa: F811
                                                                       monkeypatch: pytest.MonkeyPatch,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    calls = _launchable(dirs, monkeypatch)
    secrets = _secrets(Path(os.environ["HOME"]) / ".config", "agentteams-secrets", **{"a.env": "X=1\n"})
    (secrets / "a.env").chmod(0)
    try:
        status = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    finally:
        (secrets / "a.env").chmod(0o600)
    assert status["run_state"] == "not-run" and "agent.dlp_skip_ok" in status["reason"]
    assert not any(c[:3] == ["docker", "run", "-d"] for c in calls)
    assert str(secrets / "a.env") in capsys.readouterr().err and str(secrets) not in json.dumps(status)


# ---------------------------------------------------------------- review

def test_review_lists_the_entries_on_the_terminal_and_counts_them_when_published(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operator_env(tmp_path, monkeypatch)
    monkeypatch.setattr(review.publish, "load_active", lambda **kw: ([], []))
    entry = {"path": "/srv/example/secrets/legacy.env", "sha256": "ab" * 32}
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(json.dumps({"agent": {"dlp_skip_ok": [entry]}}))
    run = tmp_path / "state" / "2026-10-22"
    (run / "gateway" / "dlp").mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"findings": []}))
    (run / "gateway" / "dlp" / "notes").write_text("hosts_conf_unredacted=0\nsecrets_skip_ok=1\nsecrets_problems=2\n")
    md = review.build()
    assert f"`{entry['path']}` sha256 `{entry['sha256']}`" in md
    assert "1 secrets file(s) not checked" in md and "2 secrets director(ies) or file(s)" in md
    published = review.build(for_publish=True)
    assert entry["path"] not in published and "1 secrets file version(s) the DLP check may skip" in published
