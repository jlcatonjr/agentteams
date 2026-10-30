"""test_vulnscan_images.py — container base-image support checks (plan N2, 2026-W44).

`images.refs` reads Dockerfile ``FROM`` lines and compose ``image:`` keys; `images.judge` decides each one.
Fixtures follow the operator's repositories (survey of 2026-10-29). No test touches the network:
endoflife.date is a fake `_eol_cycles`, and `_http.fetch_json` refuses every call.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import _http, coverage, engine, executor, fix, images, manifests, runtimes
from agentteams.vulnscan.report import Report
from tests._vulnscan_helpers import operator_env

TODAY = dt.date(2026, 10, 30)
ECR = "123456789012.dkr.ecr.us-west-1.amazonaws.com/vk-services/base:latest"

CYCLES: dict[str, list[dict]] = {
    "python": [{"cycle": "3.14", "eol": False, "is_eol": False, "latest": "3.14.0"},
               {"cycle": "3.12", "eol": False, "is_eol": False, "latest": "3.12.12"},
               {"cycle": "3.11", "eol": False, "is_eol": False, "latest": "3.11.14"},
               {"cycle": "3.9", "eol": "2025-10-31", "is_eol": True, "latest": "3.9.25"}],
    "nodejs": [{"cycle": "24", "eol": False, "is_eol": False, "latest": "24.11.0"},
               {"cycle": "18", "eol": "2025-04-30", "is_eol": True, "latest": "18.20.8"}],
    "ubuntu": [{"cycle": "24.04", "eol": False, "is_eol": False, "latest": "24.04.3"},
               {"cycle": "22.04", "eol": False, "is_eol": False, "latest": "22.04.5"},
               {"cycle": "20.04", "eol": "2025-05-31", "is_eol": True, "latest": "20.04.6"}],
    "debian": [{"cycle": "13", "eol": False, "is_eol": False, "latest": "13.1"},
               {"cycle": "12", "eol": False, "is_eol": False, "latest": "12.12"},
               {"cycle": "10", "eol": "2022-09-10", "is_eol": True, "latest": "10.13"}],
    "alpine": [{"cycle": "3.22", "eol": False, "is_eol": False, "latest": "3.22.2"},
               {"cycle": "3.18", "eol": "2025-05-09", "is_eol": True, "latest": "3.18.12"}],
    "eclipse-temurin": [{"cycle": "17", "eol": False, "is_eol": False, "latest": "17.0.13"}],
    "amazon-corretto": [{"cycle": "8", "eol": False, "is_eol": False, "latest": "8.472"}],
    "amazon-linux": [{"cycle": "2023", "eol": False, "is_eol": False, "latest": "2023.9"},
                     {"cycle": "2", "eol": "2026-06-30", "is_eol": True, "latest": "2.0.2025"}],
    "dotnet": [{"cycle": "8.0", "eol": False, "is_eol": False, "latest": "8.0.11"},
               {"cycle": "6.0", "eol": "2024-11-12", "is_eol": True, "latest": "6.0.36"}],
    "redis": [{"cycle": "7.4", "eol": False, "is_eol": False, "latest": "7.4.1"}],
}


@pytest.fixture
def eol(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake endoflife.date (a slug not in CYCLES fails like an unreachable feed). Returns the slugs asked."""
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


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _refs(tmp_path: Path, text: str, name: str = "Dockerfile") -> tuple[list[str], list[str]]:
    found, notes = images.refs(_write(tmp_path / name, text))
    return [f"{r.registry}|{r.repository}|{r.tag}|{r.digest[:8]}|{r.line}|{int(r.arg_default)}" for r in found], notes


def _scan(tmp_path: Path, text: str, name: str = "Dockerfile") -> Report:
    rep = Report(target="t")
    images.scan_manifest(_write(tmp_path / name, text), name, "repo", rep, TODAY)
    return rep


def _one(text: str) -> images.ImageRef:
    ref = images.parse_ref(text, 1, "FROM")
    assert ref is not None, text
    return ref


# ---------------------------------------------------------------- parsing

def test_multi_stage_platform_scratch_and_stage_aliases(tmp_path: Path) -> None:
    got, notes = _refs(tmp_path, "# syntax=docker/dockerfile:1\n"
                       "FROM --platform=$BUILDPLATFORM python:3.11-slim-bookworm AS builder\n"
                       "RUN pip install x \\\n    y\n"
                       "FROM python:3.11-slim-bookworm\nCOPY --from=builder /a /a\n"
                       "FROM builder AS again\nFROM scratch\n")
    assert got == ["|python|3.11-slim-bookworm||2|0", "|python|3.11-slim-bookworm||5|0"]
    assert notes == []


def test_arg_default_before_from_resolves_and_is_labelled(tmp_path: Path) -> None:
    got, notes = _refs(tmp_path, f"ARG PYTHON_IMAGE=python:3.12-slim\nARG BASE_IMAGE={ECR}\n"
                       "FROM ${PYTHON_IMAGE} AS base\nFROM $BASE_IMAGE\nFROM ${UNSET}\n")
    assert got == ["|python|3.12-slim||3|1",
                   "123456789012.dkr.ecr.us-west-1.amazonaws.com|vk-services/base|latest||4|1"]
    assert notes == ["line 5: image reference uses an unresolved variable: not judged"]


def test_arg_after_the_first_from_does_not_resolve(tmp_path: Path) -> None:
    got, notes = _refs(tmp_path, "FROM python:3.12\nARG IMG=python:3.9\nFROM ${IMG}\n")
    assert len(got) == 1 and notes == ["line 3: image reference uses an unresolved variable: not judged"]


@pytest.mark.parametrize(("text", "want"), [
    ("python", ("", "python", "", "")),
    ("docker.io/library/node:18-alpine", ("docker.io", "library/node", "18-alpine", "")),
    ("library/redis:7.4", ("", "library/redis", "7.4", "")),
    ("host:5000/x:1", ("host:5000", "x", "1", "")),
    ("localhost/app", ("localhost", "app", "", "")),
    ("mcr.microsoft.com/dotnet/aspnet:8.0", ("mcr.microsoft.com", "dotnet/aspnet", "8.0", "")),
    ("python:3.11@sha256:" + "a" * 64, ("", "python", "3.11", "sha256:" + "a" * 64)),
])
def test_reference_split(text: str, want: tuple[str, str, str, str]) -> None:
    r = _one(text)
    assert (r.registry, r.repository, r.tag, r.digest) == want


@pytest.mark.parametrize("bad", ["Python:3", "a b", "x:`rm`", "x@sha256:zz", "x|y", "a//b", "x:" + "1" * 200])
def test_unusual_references_are_refused(bad: str) -> None:
    assert images.parse_ref(bad, 1, "FROM") is None


def test_compose_images_skip_build_services_and_flag_variables(tmp_path: Path) -> None:
    got, notes = _refs(tmp_path, "version: '3'\nservices:\n"
                       "  web:\n    build: .\n    image: myorg/web:latest\n"
                       "  worker:\n    build:\n      context: ./w\n"
                       "  fs:\n    image: ghcr.io/flaresolverr/flaresolverr:latest   # proxy\n"
                       "  db:\n    image: \"postgres:${PG_TAG}\"\n"
                       "    environment:\n      image: not-a-service-key\n"
                       "volumes:\n  image: nope\n", "docker-compose.yml")
    assert got == ["ghcr.io|flaresolverr/flaresolverr|latest||10|0"]
    assert notes == ["line 12: image reference uses an unresolved variable: not judged"]


# ---------------------------------------------------------------- tag rules

@pytest.mark.parametrize(("tag", "want"), [
    ("3.12-slim-bookworm", ("debian", "bookworm")), ("v1.57.0-jammy", ("ubuntu", "jammy")),
    ("1.22-alpine3.19", ("alpine", "3.19")), ("17-al2023", ("amazonlinux", "2023")),
    ("3.12-slim", None), ("18-alpine", None),
])
def test_tag_os(tag: str, want: tuple[str, str] | None) -> None:
    assert images.tag_os(tag) == want


def test_real_world_dockerfile(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM mcr.microsoft.com/playwright/python:v1.57.0-jammy\n"
                "FROM python:3.12-slim\nFROM python:3.11-slim-bookworm AS builder\nFROM python:3.11-slim-bookworm\n"
                "FROM python:3.9-slim\n")
    assert [(f.kind, f.name, f.version, f.severity, f.fix) for f in rep.findings] == [
        ("image", "python", "3.9-slim", 3, "python:3.14-slim")]
    assert rep.findings[0].locations == ["Dockerfile:5"]
    assert rep.unscannable[0] == ("Dockerfile", images.CONTAINER_REASON)
    assert ("Dockerfile", "line 1: mcr.microsoft.com/playwright/python:v1.57.0-jammy: not an official image vulnscan "
            "knows: release line not judged; base OS Ubuntu 22.04 judged") in rep.unscannable
    assert "ubuntu" in eol and rep.checked == 5   # playwright's OS, 3.12, 3.11 + bookworm (once), 3.9
    assert not rep.feed_errors


def test_release_line_tag_ignores_patch_verdicts(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM python:3.11\nFROM node:24-alpine\n")   # 3.11 < 3.11.14: still fine (rebuilt)
    assert rep.findings == []


def test_full_patch_tag_behind_is_moderate(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM python:3.11.4-slim\nFROM python:3.11.14\n")
    assert [(f.version, f.severity, f.fix, f.detail) for f in rep.findings] == [
        ("3.11.4-slim", 2, "python:3.11.14-slim", "pinned patch tag behind 3.11.14")]


@pytest.mark.parametrize("ref", ["python:3", "redis:7", "node:lts-alpine", "node:current", "debian:stable-slim",
                                 "alpine:3", "node:hydrogen"])
def test_floating_tags_are_not_judged_and_never_fine(tmp_path: Path, eol: list[str], ref: str) -> None:
    rep = _scan(tmp_path, f"FROM {ref}\n")
    assert rep.findings == [] and len(rep.unscannable) == 2
    assert "not determinable" in rep.unscannable[1][1] or "without a version" in rep.unscannable[1][1]


@pytest.mark.parametrize("ref", ["python", "python:latest", "ghcr.io/flaresolverr/flaresolverr:latest"])
def test_latest_or_no_tag_is_low(tmp_path: Path, eol: list[str], ref: str) -> None:
    rep = _scan(tmp_path, f"FROM {ref}\n")
    assert [(f.severity, f.detail) for f in rep.findings] == [
        (1, "unpinned tag: rebuilds change silently; release line not judged")]


def test_digest_is_a_coverage_note_not_a_finding(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, f"FROM python:3.12@sha256:{'b' * 64}\nFROM python@sha256:{'c' * 64}\n")
    assert rep.findings == []
    assert rep.unscannable[0][1].endswith("; 2 pinned by digest: contents frozen at the pinned digest)")
    assert rep.unscannable[0][1].startswith(manifests.UNSUPPORTED_REASON + "container images")
    assert any("pinned by digest only" in r for _p, r in rep.unscannable)


def test_variant_suffix_gives_a_second_os_judgement(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM python:3.12-slim-buster\nFROM golang:1.22\n")
    got = sorted((f.name, f.severity, f.detail.split(":")[0], f.fix) for f in rep.findings)
    assert got == [("python", 3, "base OS", "Debian 13")]   # go is unknown to the fake feed: a feed error
    assert any(": go 1.22: release data unavailable" in e for e in rep.feed_errors)   # partial, never fine
    assert any(f.detail.startswith("base OS: Alpine Linux 3.18") for f in _scan(tmp_path, "FROM golang:1.22-alpine3.18\n",
                                                                                "c.Dockerfile").findings)
    rep2 = _scan(tmp_path, "FROM node:24-alpine3.18\n", "b.Dockerfile")
    assert [(f.detail.split(":")[0], f.fix) for f in rep2.findings] == [("base OS", "Alpine Linux 3.22")]


def test_os_images_and_codename_tags(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM ubuntu:20.04\nFROM ubuntu:jammy\nFROM debian:bookworm-slim\nFROM debian:buster\n"
                "FROM amazonlinux:2023\nFROM amazonlinux:2\n")
    assert sorted((f.name, f.version) for f in rep.findings) == [
        ("amazonlinux", "2"), ("debian", "buster"), ("ubuntu", "20.04")]
    assert all(f.severity == 3 and f.detail.startswith("base OS:") for f in rep.findings)


def test_openjdk_deprecated_and_corretto_and_dotnet(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM openjdk:17-slim\nFROM amazoncorretto:8-al2023\n"
                "FROM mcr.microsoft.com/dotnet/aspnet:6.0\n")
    assert sorted((f.name, f.severity, f.fix) for f in rep.findings) == [
        ("mcr.microsoft.com/dotnet/aspnet", 3, "mcr.microsoft.com/dotnet/aspnet:8.0"),
        ("openjdk", 1, "eclipse-temurin:17")]
    assert {"eclipse-temurin", "amazon-corretto", "amazon-linux", "dotnet"} <= set(eol)


def test_private_and_local_images_are_never_named_or_sent(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, f"ARG BASE_IMAGE={ECR}\nFROM ${{BASE_IMAGE}}\nFROM vk-collectors-base:latest\n"
                "FROM registry.internal.example:5000/team/svc:3.12-bookworm\n")
    text = repr(rep.to_dict())
    assert "123456789012" not in text and "vk-services" not in text and "vk-collectors" not in text
    assert "internal.example" not in text and "team/svc" not in text
    assert eol == []   # nothing judged: no slug asked for either
    assert [(f.name, f.severity, f.detail) for f in rep.findings] == [
        ("(private registry image)", 1, "unpinned tag: rebuilds change silently; release line not judged"
         " (ARG default; `--build-arg` may override)"),
        ("(local or unlisted image)", 1, "unpinned tag: rebuilds change silently; release line not judged")]


def test_only_slugs_reach_endoflife_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fetch(url: str, *a: object, **kw: object) -> object:
        urls.append(url)
        raise _http.FeedError("offline")

    runtimes._CACHE.clear()
    monkeypatch.setattr(_http, "fetch_json", fetch)
    rep = _scan(tmp_path, "FROM mcr.microsoft.com/playwright/python:v1.57.0-jammy\nFROM python:3.9-slim\n")
    assert urls and all(u.rsplit("/", 1)[-1].split(".")[0] in ("ubuntu", "python") for u in urls)
    assert len(rep.feed_errors) == 2 and rep.findings == []   # unreachable: partial, never fine


def test_a_failed_slug_is_not_refetched_for_the_rest_of_the_run(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review I-s1: an outage costs one v1 + v0 attempt per slug, not one per image reference."""
    urls: list[str] = []

    def fetch(url: str, *a: object, **kw: object) -> object:
        urls.append(url)
        raise _http.FeedError("offline")

    runtimes._CACHE.clear()
    monkeypatch.setattr(_http, "fetch_json", fetch)
    rep = _scan(tmp_path, "FROM python:3.9-slim\nFROM python:3.10-slim\nFROM python:3.11.4\n")
    assert urls == ["https://endoflife.date/api/v1/products/python", "https://endoflife.date/api/python.json"]
    assert len(rep.feed_errors) == 3 and rep.findings == []   # each reference still reported unchecked
    assert "cached failure" in rep.feed_errors[-1]
    rep = _scan(tmp_path, "FROM node:20.1.0\n")           # another slug is still tried
    assert urls[2:] == ["https://endoflife.date/api/v1/products/nodejs", "https://endoflife.date/api/nodejs.json"]
    runtimes._CACHE.clear()                                  # a new run tries again


def test_unknown_release_line_is_unscannable(tmp_path: Path, eol: list[str]) -> None:
    rep = _scan(tmp_path, "FROM python:3.99\n")
    assert rep.findings == [] and "unknown to endoflife.date" in rep.unscannable[1][1]


# ---------------------------------------------------------------- engine, coverage, fix plan, executor

def test_scan_path_replaces_the_reason_and_coverage_is_unchanged(tmp_path: Path, eol: list[str]) -> None:
    repo = tmp_path / "svc"
    (repo / ".git").mkdir(parents=True)
    _write(repo / "Dockerfile", "FROM python:3.9-slim\n")
    _write(repo / "compose.yaml", "services:\n  app:\n    build: .\n")
    assert manifests.parse(repo / "Dockerfile").unscannable == "unsupported ecosystem: container images"
    rep = engine.scan_path(tmp_path)
    assert [(f.kind, f.target, f.name) for f in rep.findings] == [("image", "svc", "python")]
    assert ("svc/Dockerfile", images.CONTAINER_REASON) in rep.unscannable
    assert ("svc/compose.yaml", images.CONTAINER_REASON) in rep.unscannable
    assert coverage.unsupported_only(repo)   # still a coverage gap: the image contents are not scanned
    assert "| base image | python | 3.9-slim |" in rep.markdown()


def test_fix_plan_section_is_plan_only(tmp_path: Path, eol: list[str]) -> None:
    _write(tmp_path / "Dockerfile", "FROM python:3.9-slim\nFROM python:3.12-slim\n")
    res = fix.fix_path(tmp_path, npm=False)
    md = res.markdown()
    assert "## base images: `Dockerfile` — plan-only (no staged edit)" in md
    assert "| python | 3.9-slim | `python:3.14-slim` | plan-only |" in md and fix.IMAGE_NOTE in md
    assert res.exit_code() == 1 and res.changes == []
    assert fix.fix_path(tmp_path, apply=True, npm=False).image_plan == []   # apply: never touched


def test_image_findings_never_reach_package_paths(tmp_path: Path, eol: list[str]) -> None:
    from agentteams.vulnscan import server_fixplan

    rep = _scan(tmp_path, "FROM python:3.9-slim\n")
    md = server_fixplan.markdown("repo", rep.findings, [])
    assert "L3" not in md and "L4" not in md and "python" not in md.split("## Commands", 1)[1]


def test_executor_refuses_dockerfile_and_compose_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operator_env(tmp_path, monkeypatch)
    tmp_path = tmp_path / "repo"
    tmp_path.mkdir()

    def g(*args: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True,
                              text=True).stdout

    g("init", "-q")
    g("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    base = g("rev-parse", "HEAD").strip()
    for name in ("Dockerfile", "docker-compose.yml"):
        _write(tmp_path / name, "FROM python:3.14-slim\n")
        with pytest.raises(executor.ExecError, match="outside dependency pins"):
            executor.validate_diff(tmp_path, base)
        (tmp_path / name).unlink()


# ---------------------------------------------------------------- hostile input

@pytest.mark.parametrize("content", [
    b"FROM python:3.12\n" * 10_000,
    b"\x00\x01\x02FROM python:3.12\n",
    b"\xff\xfe FROM python\n",
    b"FROM " + b"a" * 5000 + b"\n",
    b"FROM\nFROM --platform=x\nARG\nFROM ${\nFROM $\nFROM :\nFROM @\nFROM a:b:c\n",
    b"services:\n  x:\n    image: \"" + b"\\" * 300 + b"\"\n",
])
def test_hostile_input_never_raises(tmp_path: Path, eol: list[str], content: bytes) -> None:
    for name in ("Dockerfile", "compose.yml"):
        (tmp_path / name).write_bytes(content)
        rep = Report(target="t")
        images.scan_manifest(tmp_path / name, name, "repo", rep, TODAY)
        assert rep.unscannable[0] == (name, images.CONTAINER_REASON)


def test_huge_file_and_ref_cap(tmp_path: Path, eol: list[str]) -> None:
    (tmp_path / "Dockerfile").write_bytes(b"#" * (images.MAX_BYTES + 10))
    rep = Report(target="t")
    images.scan_manifest(tmp_path / "Dockerfile", "Dockerfile", "repo", rep, TODAY)
    assert "larger than" in rep.unscannable[1][1]
    many = "".join(f"FROM python:3.{i % 20}\n" for i in range(images.MAX_REFS + 5))
    found, notes = images.refs(_write(tmp_path / "Dockerfile", many))
    assert len(found) == images.MAX_REFS and notes == [f"more than {images.MAX_REFS} image references: the rest not judged"]
