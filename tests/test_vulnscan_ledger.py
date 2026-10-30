"""agentteams.vulnscan.ledger: crash safety, concurrency, key permissions, old formats (Area C).

Covers D5 (an interrupted final row fails closed with a clear message; rows and the high-water mark
are fsynced in order), I17 (two processes appending under :func:`ledger.locked`), the ledger key's
owner/mode check, and R4/R5 upgrade states (an old ledger row without newer fields; an old self-test
record without ``flags_sha256``). Network- and Docker-free.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import agent_run, executor, ledger, paths


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point the vulnscan config and state dirs into ``tmp_path``.

    Args:
        tmp_path: Pytest tmp dir.
        monkeypatch: Pytest monkeypatch.

    Returns:
        ``{"config": ..., "state": ...}``.
    """
    config = tmp_path / "config"
    state = tmp_path / "state"
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(config))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(state))
    return {"config": config, "state": state}


def _append(n: int, kind: str = "note") -> None:
    with ledger.locked():
        for i in range(n):
            ledger.append(kind, detail=str(i))


# ---------------------------------------------------------------- D5 (a): interrupted final row

@pytest.mark.parametrize("cut", [1, 20, -2, None])
def test_an_incomplete_final_row_fails_closed_with_a_clear_message(env: dict[str, Path], cut: int | None) -> None:
    _append(3)
    p = ledger.ledger_path()
    text = p.read_text(encoding="utf-8")
    last = text.splitlines()[-1]
    # a crash or a full disk mid-write leaves a prefix of the last row (None: everything but its newline)
    p.write_text(text[: len(text) - len(last) - 1] + last[:cut], encoding="utf-8")
    with pytest.raises(ValueError, match=r"incomplete final row \(interrupted write\?\)") as info:
        ledger.read()
    assert "vulnscan-ledger.hwm" in str(info.value) and "revoke every grant" in str(info.value)
    with pytest.raises(ValueError, match="incomplete final row"):     # appending does not paper over it
        _append(1)


def test_an_incomplete_final_row_is_reported_as_a_ledger_integrity_failure(env: dict[str, Path]) -> None:
    _append(1)
    p = ledger.ledger_path()
    p.write_text(p.read_text(encoding="utf-8") + '{"seq": 2, "ki', encoding="utf-8")
    out = executor.act("server-audit", "web1", {}, request_id="r-1")
    assert out["status"] == "refused" and "incomplete final row" in out["detail"]


def test_a_garbled_middle_row_names_its_line(env: dict[str, Path]) -> None:
    _append(3)
    p = ledger.ledger_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1][:10]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 2 is not valid JSON"):
        ledger.read()


def test_an_unverified_read_still_refuses_an_incomplete_final_row(env: dict[str, Path]) -> None:
    _append(1)
    p = ledger.ledger_path()
    p.write_text(p.read_text(encoding="utf-8") + "{", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete final row"):
        ledger.read(verify=False)


@pytest.mark.parametrize("edit", ["delete", "swap"])
def test_a_deleted_or_swapped_middle_row_fails_at_row_2(env: dict[str, Path], edit: str) -> None:
    """V3: the chain links each row to its predecessor's MAC, so a middle edit fails where it happened."""
    _append(3)
    p = ledger.ledger_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    lines = [lines[0], lines[2]] if edit == "delete" else [lines[0], lines[2], lines[1]]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ledger row 2 fails verification"):
        ledger.read()


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


@pytest.mark.parametrize("mark", ["abc", "", "-1", "2.0", " 3x"])
def test_a_corrupt_high_water_mark_is_refused_and_nothing_changes(env: dict[str, Path], mark: str) -> None:
    """V9/V10: read() and act() refuse a corrupt mark; the ledger and mark bytes are left exactly as they were."""
    _append(2)
    ledger.hwm_path().write_text(mark, encoding="utf-8")
    before = (_bytes(ledger.ledger_path()), _bytes(ledger.hwm_path()))
    with pytest.raises(ValueError, match="corrupt"):
        ledger.read()
    out = executor.act("server-audit", "web1", {}, request_id="r-hwm")
    assert out["status"] == "refused" and "ledger integrity failure" in out["detail"] and "corrupt" in out["detail"]
    with pytest.raises(ValueError, match="corrupt"):
        _append(1)
    assert (_bytes(ledger.ledger_path()), _bytes(ledger.hwm_path())) == before


@pytest.mark.parametrize("tamper", ["truncate-tail", "alter-row", "remove-ledger"])
def test_integrity_refusals_leave_ledger_and_mark_bytes_unchanged(env: dict[str, Path], tamper: str) -> None:
    """V10: an integrity refusal appends nothing and never lowers (or rewrites) the high-water mark."""
    _append(3)
    p = ledger.ledger_path()
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    if tamper == "truncate-tail":
        p.write_text("".join(lines[:2]), encoding="utf-8")
    elif tamper == "alter-row":
        p.write_text("".join(lines).replace('"detail": "1"', '"detail": "X"'), encoding="utf-8")
    else:
        p.unlink()
    before = (_bytes(p), _bytes(ledger.hwm_path()))
    out = executor.act("server-audit", "web1", {}, request_id="r-tamper")
    assert out["status"] == "refused" and "ledger integrity failure" in out["detail"]
    assert (_bytes(p), _bytes(ledger.hwm_path())) == before
    assert before[1] == b"3"


def test_the_lock_is_exclusive_while_held(env: dict[str, Path]) -> None:
    """V12, deterministic: while locked() is held, a second non-blocking exclusive flock fails; after, it succeeds."""
    import fcntl
    lock_path = ledger.ledger_path().with_suffix(".lock")
    with ledger.locked(), open(lock_path, "a+") as other, pytest.raises(BlockingIOError):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(lock_path, "a+") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_UN)


# ---------------------------------------------------------------- D5 (b): fsync order

def test_append_fsyncs_the_row_before_raising_the_high_water_mark(env: dict[str, Path],
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real = os.fsync

    def spy(fd: int) -> None:
        synced.append(os.fstat(fd).st_ino)
        real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    _append(1)
    led = ledger.ledger_path().stat().st_ino
    hwm = ledger.hwm_path().stat().st_ino
    keys = ledger.hwm_path().parent.stat().st_ino
    state = ledger.ledger_path().parent.stat().st_ino
    key = (paths.config_dir() / "keys" / "vulnscan-ledger.key").stat().st_ino
    for ino in (led, hwm, keys, state, key):
        assert ino in synced, synced
    assert synced.index(led) < synced.index(hwm) < len(synced) - synced[::-1].index(keys) - 1
    synced.clear()
    _append(1)                                           # later appends: the row and the mark, rows first
    led2, hwm2 = ledger.ledger_path().stat().st_ino, ledger.hwm_path().stat().st_ino
    assert led2 in synced and hwm2 in synced and synced.index(led2) < synced.index(hwm2)


# ---------------------------------------------------------------- I17: two processes

def _worker(tag: str, n: int, start: Any) -> None:
    """Append ``n`` rows under the ledger lock, one lock acquisition per row (spawned child).

    Args:
        tag: Row label.
        n: Rows to append.
        start: Event both workers wait on, so they contend.
    """
    start.wait(30)
    for i in range(n):
        with ledger.locked():
            ledger.append("note", detail=f"{tag}-{i}")


def test_two_processes_appending_under_the_lock_keep_one_chain(env: dict[str, Path]) -> None:
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    procs = [ctx.Process(target=_worker, args=(tag, 50, start)) for tag in ("a", "b")]
    for p in procs:
        p.start()
    start.set()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    rows = ledger.read()                                 # verifies every MAC and the chain
    assert len(rows) == 100
    assert [r["seq"] for r in rows] == list(range(1, 101))
    assert sorted(r["detail"] for r in rows) == sorted(f"{t}-{i}" for t in "ab" for i in range(50))
    assert int(ledger.hwm_path().read_text()) == 100


# ---------------------------------------------------------------- ledger key owner and mode

@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644])
def test_a_group_or_world_readable_ledger_key_is_refused(env: dict[str, Path], mode: int) -> None:
    _append(1)
    key = paths.config_dir() / "keys" / "vulnscan-ledger.key"
    os.chmod(key, mode)
    with pytest.raises(PermissionError, match="mode 600"):
        ledger.read()
    with pytest.raises(PermissionError):
        _append(1)
    os.chmod(key, 0o600)
    assert len(ledger.read()) == 1


def test_a_ledger_key_owned_by_someone_else_is_refused(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _append(1)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(ledger.ledger_path()).st_uid + 1)
    with pytest.raises(PermissionError, match="owned by you"):
        ledger.read()


def test_a_bad_ledger_key_is_a_ledger_integrity_failure_for_the_executor(env: dict[str, Path]) -> None:
    _append(1)
    os.chmod(paths.config_dir() / "keys" / "vulnscan-ledger.key", 0o644)
    out = executor.act("server-audit", "web1", {}, request_id="r-2")
    assert out["status"] == "refused" and "ledger integrity failure" in out["detail"]


def test_an_unreadable_ledger_key_is_a_clear_refusal_not_a_crash(env: dict[str, Path],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    _append(1)
    key = paths.config_dir() / "keys" / "vulnscan-ledger.key"
    before = key.read_bytes()
    real_stat = Path.stat

    def denied(self: Path, *a: Any, **kw: Any) -> os.stat_result:
        if self == key:
            raise PermissionError(1, "Operation not permitted", str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(ledger.LedgerKeyError, match="cannot be read or created"):
        ledger.read()
    out = executor.act("server-audit", "web1", {}, request_id="r-3")
    assert out["status"] == "refused" and "ledger integrity failure" in out["detail"]
    monkeypatch.undo()
    assert key.read_bytes() == before       # no second key was created over it


def test_a_ledger_key_created_by_another_process_first_is_used(env: dict[str, Path],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    key = paths.config_dir() / "keys" / "vulnscan-ledger.key"
    real_open = os.open

    def racing(path: Any, flags: int, *a: Any) -> int:
        if Path(path) == key and flags & os.O_EXCL:     # the other process wins between stat and create
            key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            key.write_text("ab" * 32)
            key.chmod(0o600)
        return real_open(path, flags, *a)

    monkeypatch.setattr(os, "open", racing)
    assert ledger._key() == bytes.fromhex("ab" * 32)


@pytest.mark.parametrize("content", ["", "ab" * 31, "not hex at all"])
def test_an_empty_or_damaged_ledger_key_is_refused(env: dict[str, Path], content: str) -> None:
    key = paths.config_dir() / "keys" / "vulnscan-ledger.key"
    key.parent.mkdir(parents=True, mode=0o700)
    key.write_text(content)
    key.chmod(0o600)
    with pytest.raises(ledger.LedgerKeyError, match="64-hex-digit"):
        ledger._key()
    assert key.read_text() == content       # refused, never replaced


def test_a_racing_creators_key_still_empty_is_refused(env: dict[str, Path],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    key = paths.config_dir() / "keys" / "vulnscan-ledger.key"
    real_open = os.open

    def racing(path: Any, flags: int, *a: Any) -> int:
        if Path(path) == key and flags & os.O_EXCL:     # created by the other process, not yet written
            key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            key.touch(mode=0o600)
        return real_open(path, flags, *a)

    monkeypatch.setattr(os, "open", racing)
    with pytest.raises(ledger.LedgerKeyError, match="empty or damaged"):
        ledger._key()


def test_a_new_ledger_key_is_created_mode_600(env: dict[str, Path]) -> None:
    _append(1)
    key = paths.config_dir() / "keys" / "vulnscan-ledger.key"
    assert key.stat().st_mode & 0o777 == 0o600
    assert (key.parent.stat().st_mode & 0o777) == 0o700


# ---------------------------------------------------------------- R4/R5: old formats

def test_an_old_format_row_without_newer_fields_still_reads_and_counts(env: dict[str, Path]) -> None:
    with ledger.locked():
        ledger.append("begun", grant_id="vg-0000000000000001")                 # no op/target/request_id
        ledger.append("done", grant_id="vg-0000000000000001")
        ledger.append("begun", grant_id="vg-0000000000000001", op="stage-fix", target="/r", request_id="q1")
    rows = ledger.read()
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert ledger.uses("vg-0000000000000001") == 2
    assert ledger.consumed_request_ids() == {"q1"}


def test_an_old_selftest_record_without_flags_sha256_is_refused(env: dict[str, Path],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_run, "_image_id", lambda tag: "sha256:img")
    env["state"].mkdir(parents=True, exist_ok=True)
    f = paths.state_dir() / "agent-selftest.json"
    f.write_text(json.dumps({"passed": True, "image": "sha256:img", "checks": []}), encoding="utf-8")
    ok, reason = agent_run.selftest_ok({})
    assert not ok and "predates container-flag binding" in reason
    f.write_text(json.dumps({"passed": True, "image": "sha256:img", "checks": [],
                             "flags_sha256": agent_run.flags_sha256(image_id="sha256:img")}), encoding="utf-8")
    assert agent_run.selftest_ok({}) == (True, "ok")
