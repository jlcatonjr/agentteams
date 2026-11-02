"""test_vulnscan_runtime_table.py — the runtime product table (plan N4, 2026-W44).

`runtimes.PRODUCTS` replaced the hard-coded Node/PHP/Python list; `runtimes.OS_PRODUCTS` and
`evaluate_os` judge OS releases. No test touches the network: endoflife.date is replaced by a fake
`_eol_cycles`, and `_http.fetch_json` refuses every call.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, engine, runtimes

TODAY = dt.date(2026, 10, 30)

#: Fake endoflife.date answers: slug -> cycles in `_eol_cycles`' shape.
CYCLES: dict[str, list[dict]] = {
    "eclipse-temurin": [{"cycle": "21", "eol": False, "is_eol": False, "latest": "21.0.5+11"},
                        {"cycle": "8", "eol": False, "is_eol": False, "latest": "8u432-b06"},
                        {"cycle": "7", "eol": "2020-01-01", "is_eol": True, "latest": "7u80"}],
    "openssl": [{"cycle": "3.0", "eol": False, "is_eol": False, "latest": "3.0.15"},
                {"cycle": "1.1.1", "eol": "2023-09-11", "is_eol": True, "latest": "1.1.1w"}],
    "nginx": [{"cycle": "1.26", "eol": False, "is_eol": False, "latest": "1.26.2"}],
    "dotnet": [{"cycle": "8", "eol": False, "is_eol": False, "latest": "8.0.11"},
               {"cycle": "6", "eol": "2024-11-12", "is_eol": True, "latest": "6.0.36"}],
    "ubuntu": [{"cycle": "24.04", "eol": False, "is_eol": False, "latest": "24.04.1"},
               {"cycle": "20.04", "eol": "2025-05-31", "is_eol": True, "latest": "20.04.6"}],
    "fedora": [{"cycle": "44", "eol": False, "is_eol": False, "latest": ""},
               {"cycle": "40", "eol": "2025-05-13", "is_eol": True, "latest": ""}],
    "debian": [{"cycle": "12", "eol": False, "is_eol": False, "latest": "12.7"},
               {"cycle": "10", "eol": "2022-09-10", "is_eol": True, "latest": "10.13"}],
}


@pytest.fixture
def eol(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake endoflife.date; a slug not in CYCLES fails like an unreachable feed. Returns the slugs asked."""
    asked: list[str] = []

    def cycles(slug: str) -> list[dict]:
        asked.append(slug)
        if slug not in CYCLES:
            raise _http.FeedError(f"no fake data for {slug}")
        return CYCLES[slug]

    def no_network(url: str, *a: object, **kw: object) -> object:
        raise _http.FeedError(f"network refused in tests: {url}")

    runtimes._CACHE.clear()
    monkeypatch.setattr(runtimes, "_eol_cycles", cycles)
    monkeypatch.setattr(_http, "fetch_json", no_network)
    return asked


# ---------------------------------------------------------------- the table

def test_every_row_has_a_slug_a_cycle_rule_and_a_probe() -> None:
    for kind, p in runtimes.PRODUCTS.items():
        assert p.eol and (p.cycle in (1, 2) or p.cycle_fn), kind
        assert p.security_feed in (None, "node", "php"), kind
        if kind == "java-corretto":
            assert not p.probes          # chosen by image name only (N2)
            continue
        assert p.probes, kind
        for probe in p.probes:
            assert probe.argv and probe.stream in ("stdout", "stderr", "both") and "(" in probe.regex, kind
    for os_id, p in runtimes.OS_PRODUCTS.items():
        assert p.eol and p.cycle in (1, 2) and not p.probes and p.security_feed is None, os_id
    assert set(runtimes.OS_CODENAMES) <= set(runtimes.OS_PRODUCTS)


def test_eol_product_stays_a_dict_derived_from_the_table() -> None:
    assert runtimes.EOL_PRODUCT == {k: p.eol for k, p in runtimes.PRODUCTS.items()}
    assert {"node": "nodejs", "php": "php", "python": "python"}.items() <= runtimes.EOL_PRODUCT.items()
    assert runtimes.EOL_PRODUCT["java-corretto"] == "amazon-corretto"


@pytest.mark.parametrize(("kind", "version", "cycle"), [
    ("node", "v24.22.0", "24"), ("php", "8.3.30", "8.3"), ("python", "3.12.11", "3.12"),
    ("ruby", "3.3.0", "3.3"), ("go", "1.22.3", "1.22"), ("go", "1.23", "1.23"),
    ("java", "1.8.0_412", "8"), ("java", "21.0.4", "21"), ("java", "21", "21"), ("java-corretto", "8.432.06.1", "8"),
    ("dotnet", "8.0.6", "8"), ("dotnet", "10.0.1", "10"), ("dotnet", "3.1.32", "3.1"), ("nginx", "1.26.2", "1.26"),
    ("openssl", "3.0.13", "3.0"), ("openssl", "3.5.1", "3.5"), ("openssl", "1.1.1w", "1.1.1"),
    ("postgresql", "16.4", "16"), ("postgresql", "9.6.24", "9.6"),
    ("mysql", "8.0.36", "8.0"), ("mariadb", "10.11.6", "10.11"), ("redis", "7.2.4", "7.2"),
    ("docker", "28.3.2", "28"), ("docker", "27.5.1", "27"), ("docker", "26.1.4", "26.1"),
    ("ubuntu", "22.04", "22.04"), ("debian", "12", "12"), ("alpine", "3.18.4", "3.18"),
    ("amzn", "2023", "2023"), ("amazonlinux", "2", "2"), ("rhel", "9.4", "9"), ("rocky", "9.4", "9"),
])
def test_cycle_of_per_product(kind: str, version: str, cycle: str) -> None:
    assert runtimes.cycle_of(kind, version) == cycle


def test_cycle_of_unknown_kind_raises() -> None:
    with pytest.raises(KeyError):
        runtimes.cycle_of("cobol", "1.0")


# ---------------------------------------------------------------- verdicts (no network)

def test_java_8_version_form_is_judged_on_line_8_with_comparable_patches(eol: list[str]) -> None:
    behind = runtimes.evaluate("java", "1.8.0_412", today=TODAY)
    assert (behind.verdict, behind.severity, behind.upgrade_to) == ("behind-patch", 2, "8u432-b06")
    assert "no upstream security-release feed" in behind.detail
    assert runtimes.evaluate("java", "1.8.0_432", today=TODAY).verdict == "ok"
    assert runtimes.evaluate("java", "21.0.5", today=TODAY).verdict == "ok"
    old = runtimes.evaluate("java", "1.7.0_80", today=TODAY)
    assert old.verdict == "eol" and "Eclipse Temurin" in old.detail and eol == ["eclipse-temurin"] * 4


def test_products_without_a_feed_never_assume_high(eol: list[str]) -> None:
    """Only a product with a security feed turns "no security data" into HIGH; the rest stay MODERATE."""
    v = runtimes.evaluate("nginx", "1.26.0", today=TODAY)
    assert (v.verdict, v.severity) == ("behind-patch", 2) and "judged on end-of-life and latest patch only" in v.detail
    assert runtimes.evaluate("openssl", "1.1.1w", today=TODAY).verdict == "eol"
    assert runtimes.evaluate("openssl", "3.0.15", today=TODAY).verdict == "ok"
    assert runtimes.evaluate("dotnet", "6.0.36", today=TODAY).severity == 3


def test_unknown_cycle_slug_or_kind_is_unchecked_never_ok(eol: list[str]) -> None:
    assert runtimes.evaluate("nginx", "1.99.0", today=TODAY).verdict == "unchecked"     # line not listed
    assert runtimes.evaluate("redis", "7.2.4", today=TODAY).verdict == "unchecked"      # feed unreachable
    unknown = runtimes.evaluate("cobol", "1.0", today=TODAY)
    assert unknown.verdict == "unchecked" and "not judged" in unknown.detail
    assert runtimes.evaluate("mysql", "8.0.1", manager="pkg:mysql-server").verdict == "distro"


def test_evaluate_os_release_line_and_codenames(eol: list[str]) -> None:
    focal = runtimes.evaluate_os("ubuntu", "20.04", today=TODAY)
    assert (focal.verdict, focal.severity) == ("eol", 3) and "distro security updates have stopped" in focal.detail
    assert focal.upgrade_to == "24.04.1"
    assert runtimes.evaluate_os("ubuntu", "noble", today=TODAY).verdict == "ok"
    assert runtimes.evaluate_os("debian", "buster", today=TODAY).verdict == "eol"
    assert runtimes.evaluate_os("debian", '"12"', today=TODAY).verdict == "ok"
    assert runtimes.evaluate_os("Debian", "11", today=TODAY).verdict == "unchecked"     # line not listed
    assert runtimes.evaluate_os("alpine", "3.18.4", today=TODAY).verdict == "unchecked"  # feed unreachable


def test_alpine_uses_the_renamed_slug_and_fedora_upgrades_to_a_line_name(eol: list[str]) -> None:
    """endoflife.date renamed alpine to alpine-linux; Fedora lists no latest release, so the line name is the target."""
    assert runtimes.OS_PRODUCTS["alpine"].eol == "alpine-linux"
    old = runtimes.evaluate_os("fedora", "40", today=TODAY)
    assert (old.verdict, old.upgrade_to) == ("eol", "44")
    assert runtimes.evaluate_os("gentoo", "2.15", today=TODAY).verdict == "unchecked"
    assert runtimes.cycle_of("ubuntu", runtimes.OS_CODENAMES["ubuntu"]["jammy"]) == "22.04"


def test_inventory_reports_an_unknown_kind_instead_of_dropping_it(eol: list[str]) -> None:
    log = "RUNTIME\tcobol\t1.0\t/opt/cobol/bin/cobc\ttarball\nRUNTIME\tnginx\t1.26.0\t/usr/sbin/nginx\ttarball\n"
    rep = engine.scan_inventory(log, "h1")
    assert rep.unscannable == [("/opt/cobol/bin/cobc", "runtime kind cobol not judged by this vulnscan version")]
    assert [(f.name, f.severity) for f in rep.findings] == [("nginx", 2)] and rep.checked == 1


# ---------------------------------------------------------------- probes on this machine

def _bin(path: Path, *, stdout: str = "", stderr: str = "", sleep: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "#!/bin/sh\n" + (f"exec sleep {sleep}\n" if sleep else "")
    body += f"printf '%s\\n' '{stdout}'\n" if stdout else ""
    body += f"printf '%s\\n' '{stderr}' >&2\n" if stderr else ""
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[tuple[str, str, str]]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    seen: list[tuple[str, str, str]] = []

    def evaluate(kind: str, version: str, path: str = "", manager: str = "",
                 today: object = None) -> runtimes.RuntimeFinding:
        seen.append((kind, version, Path(path).name))
        return runtimes.RuntimeFinding(kind, version, path, manager, 0, "ok", "", "")

    monkeypatch.setattr(runtimes, "evaluate", evaluate)
    return bindir, seen


def test_local_runtimes_probes_every_product(fake_path: tuple[Path, list[tuple[str, str, str]]]) -> None:
    bindir, seen = fake_path
    _bin(bindir / "java", stderr='openjdk version "1.8.0_412"')
    _bin(bindir / "dotnet", stdout="Microsoft.AspNetCore.App 8.0.6 [/x]\nMicrosoft.NETCore.App 8.0.6 [/x]\n"
                                   "Microsoft.NETCore.App 6.0.36 [/x]")
    _bin(bindir / "nginx", stderr="nginx version: nginx/1.26.0")
    _bin(bindir / "psql", stdout="psql (PostgreSQL) 16.4 (Homebrew)")
    _bin(bindir / "go", stdout="go version go1.22.3 darwin/arm64")
    _bin(bindir / "redis-server", stdout="Redis server v=7.2.4 sha=00000000:0 malloc=libc bits=64")
    _bin(bindir / "mysqld", stdout="mysqld  Ver 10.11.6-MariaDB for osx10.19 on arm64 (Homebrew)")
    rep = engine.local_runtimes("box")
    assert sorted(seen) == [("dotnet", "6.0.36", "dotnet"), ("dotnet", "8.0.6", "dotnet"), ("go", "1.22.3", "go"),
                            ("java", "1.8.0_412", "java"), ("mariadb", "10.11.6", "mysqld"),
                            ("nginx", "1.26.0", "nginx"), ("postgresql", "16.4", "psql"),
                            ("redis", "7.2.4", "redis-server")]
    assert rep.checked == 8 and not rep.unscannable


def test_local_runtimes_skips_other_products_and_reports_unreadable(
        fake_path: tuple[Path, list[tuple[str, str, str]]]) -> None:
    bindir, seen = fake_path
    _bin(bindir / "mysqld", stdout="/usr/sbin/mysqld  Ver 8.0.36 for Linux on x86_64 (MySQL Community Server - GPL)")
    _bin(bindir / "openssl", stdout="LibreSSL 3.3.6")
    _bin(bindir / "docker", stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.")
    rep = engine.local_runtimes()
    assert seen == [("mysql", "8.0.36", "mysqld")]
    reasons = {Path(p).name: r for p, r in rep.unscannable}
    assert set(reasons) == {"openssl", "docker"}
    assert reasons["openssl"].startswith("not OpenSSL (LibreSSL 3.3.6)")
    assert reasons["docker"] == "could not read the docker version"


def test_probe_timeout_is_unreadable_not_a_hang(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert engine.PROBE_TIMEOUT == 20
    monkeypatch.setattr(engine, "PROBE_TIMEOUT", 0.2)
    slow = _bin(tmp_path / "node", stdout="v24.22.1", sleep=5)
    assert engine._run_probe(str(slow), runtimes.PRODUCTS["node"].probes[0]) == ([], "")
    assert engine._run_probe(str(tmp_path / "missing"), runtimes.PRODUCTS["node"].probes[0]) == ([], "")


def test_probe_runs_from_root_with_a_pinned_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review R-s1: a probe never reads the scan's cwd, GOFLAGS, GOTOOLCHAIN or a docker context."""
    probe = tmp_path / "node"
    probe.write_text('#!/bin/sh\nprintf "v24.1.0 cwd=%s tc=%s gf=[%s] dh=%s ctx=[%s] path=%s\\n" "$(pwd)" '
                     '"$GOTOOLCHAIN" "$GOFLAGS" "$DOCKER_HOST" "$DOCKER_CONTEXT" "$PATH"\n')
    probe.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("GOTOOLCHAIN", "auto")
    monkeypatch.setenv("GOFLAGS", "-modfile=evil.mod")
    monkeypatch.setenv("DOCKER_HOST", "tcp://203.0.113.9:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    seen: list[str] = []
    real = engine.subprocess.run

    def spy(*a: object, **kw: object) -> object:
        res = real(*a, **kw)
        seen.append(res.stdout)
        return res

    monkeypatch.setattr(engine.subprocess, "run", spy)
    assert engine._run_probe(str(probe), runtimes.PRODUCTS["node"].probes[0])[0] == ["24.1.0"]
    assert seen == [f"v24.1.0 cwd=/ tc=local gf=[] dh=unix:///var/run/docker.sock ctx=[] path={tmp_path}:/usr/bin:/bin\n"]
    seen.clear()
    engine._version_of(str(probe), [])
    assert seen and seen[0].startswith("v24.1.0 cwd=/ tc=local gf=[] dh=unix:///var/run/docker.sock ctx=[]")
