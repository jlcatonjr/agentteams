"""The vulnscan tools image: build it, and prove its isolation (``tools-image build``, ``tools-selftest``).

:mod:`.subproc_confine` runs git, npm and the server kit in containers of this image and refuses to run them at all
until :func:`selftest` has passed for the *current* image and container flags (``<state>/tools-selftest.json``,
keyed by image id and :func:`subproc_confine.flags_sha256`). The self-test runs the image's probe with the exact
flags of a confined operation, twice:

- ``--network none`` (a git command that needs no network): read-only root filesystem, an empty tmpfs ``$HOME``, no
  credential path (``~/.ssh``, ``~/.config/gh`` ... under the host home or the container's), no token variables,
  the operator's uid and never root, zero capabilities, ``no_new_privs``, the writable mount writable and the
  read-only mount not, the tools present, no direct egress and no DNS;
- through an egress proxy allowing only ``registry.npmjs.org:443``: the same, plus the proxy refusing another host
  and an IP literal (403) and allowing the listed endpoint (200).

Node.js is pinned here (:data:`NODE_VERSION`, :data:`NODE_SHA256`): the official linux tarball, its sha256 per
architecture taken from the release's ``SHASUMS256.txt``, whose GPG signature was verified against a Node.js
releaser key (:data:`NODE_RELEASE_SIGNERS`) when it was pinned. ``scripts/vulnscan-tools-image.sh`` re-checks both
before building. Rule S-10: a release is pinned only once it is at least 14 days old and the latest stable release of
its line (:data:`NODE_RELEASED`), unless it fixes a vulnerability the scan detected.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths
from agentteams.vulnscan import subproc_confine as sc

NODE_VERSION = "24.21.0"                 # LTS "Krypton"; npm 11.19.0
NODE_RELEASED = "2026-09-07"             # pinned 2026-10-23 (46 days old; S-10 needs >= 14)
NODE_SHA256 = {"amd64": "fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6",
               "arm64": "6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2"}
#: Signer of v24.21.0's SHASUMS256.txt (listed in the nodejs/node README's release keys).
NODE_RELEASE_SIGNERS = ("5BE8A3F6C8A5C01D106C0AD820B1A390B168D356",)
PROBE = "/usr/local/lib/vulnscan/selftest.py"
ALLOWED = sc.NPM_ENDPOINT
DENIED = "example.com:443"
COMMANDS = ("tools-image", "tools-selftest")


def build_argv(context: Path, tag: str, python_image: str, *, node_version: str = NODE_VERSION,
               node_sha256: dict[str, str] | None = None) -> list[str]:
    """Return the ``docker build`` command (pure)."""
    sha = node_sha256 or NODE_SHA256
    return ["docker", "build", "--pull", "-t", tag, "--build-arg", f"PYTHON_IMAGE={python_image}",
            "--build-arg", f"NODE_VERSION={node_version}", "--build-arg", f"NODE_SHA256_AMD64={sha['amd64']}",
            "--build-arg", f"NODE_SHA256_ARM64={sha['arm64']}", str(context)]


def build_image(cfg: dict[str, Any], *, python_image: str, node_version: str = NODE_VERSION,
                node_sha256: dict[str, str] | None = None) -> str:
    """Build the tools image from the packaged context.

    Args:
        cfg: Daily config (``confine.image`` names the tag).
        python_image: Base image, ideally pinned by digest.
        node_version: Node.js release (default the pinned one).
        node_sha256: ``{"amd64": ..., "arm64": ...}`` tarball checksums (required with another version).

    Returns:
        The image tag.

    Raises:
        RuntimeError: Docker is unreachable, another Node.js version came without checksums, or the build failed.
    """
    if node_version != NODE_VERSION and not node_sha256:
        raise RuntimeError(f"Node.js {node_version} is not the pinned {NODE_VERSION}: pass both checksums")
    if not shutil.which("docker"):
        raise RuntimeError("docker is not installed")
    if "@sha256:" not in python_image:
        print(f"warning: base image {python_image!r} is not pinned by digest", file=sys.stderr)
    tag = sc.image_tag(sc.settings(cfg))
    with tempfile.TemporaryDirectory(prefix="vulnscan-tools-image-") as tmp:
        ctx = Path(tmp) / "ctx"
        shutil.copytree(sc.data_dir(), ctx)
        proc = subprocess.run(build_argv(ctx, tag, python_image, node_version=node_version, node_sha256=node_sha256),
                              capture_output=True, text=True, timeout=3600, check=False)
    if proc.returncode:
        raise RuntimeError(f"docker build failed: {(proc.stderr or proc.stdout).strip()[-1500:]}")
    return tag


def _probe_env(rw: Path, ro: Path, proxy: str = "") -> dict[str, str]:
    env = {"VULNSCAN_EXPECT_UID": str(os.getuid()), "VULNSCAN_HOST_HOME": str(Path.home()),
           "VULNSCAN_RW": str(rw.resolve()), "VULNSCAN_RO": str(ro.resolve())}
    if proxy:
        env.update({"VULNSCAN_PROXY": proxy, "VULNSCAN_ALLOWED": ALLOWED, "VULNSCAN_DENIED": DENIED})
    return env


def _parse(proc: subprocess.CompletedProcess) -> dict[str, bool]:
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("{")]
    try:
        data = json.loads(lines[-1]) if lines else {}
    except ValueError:
        data = {}
    return {str(k): v is True for k, v in data.items()} if isinstance(data, dict) else {}


def selftest(cfg: dict[str, Any]) -> dict[str, Any]:
    """Run the isolation probe with a confined operation's flags; record a pass for this image and these flags.

    Returns:
        ``{"passed": bool, "checks": {...}, "image": tag, "image_id": ..., "error"?: str}``.
    """
    tag = sc.image_tag(sc.settings(cfg))
    if os.getuid() == 0:
        return {"passed": False, "checks": {}, "image": tag, "error": "run as the operator, not root"}
    if not shutil.which("docker"):
        return {"passed": False, "checks": {}, "image": tag, "error": "docker is not installed"}
    image_id = sc.image_id_of(tag)
    if not image_id:
        return {"passed": False, "checks": {}, "image": tag,
                "error": f"docker is not reachable or {tag} is not built — run tools-image build"}
    checks: dict[str, bool] = {}
    error = ""
    base = sc.scratch_parent()
    with tempfile.TemporaryDirectory(prefix="selftest-", dir=base) as tmp:
        rw, ro = Path(tmp) / "rw", Path(tmp) / "ro"
        rw.mkdir()
        ro.mkdir()
        try:
            none = sc.run_confined("selftest", ["python3", "-I", PROBE, "--mode", "none"], writable=[rw],
                                   readonly=[ro], env=_probe_env(rw, ro), timeout=120, image=tag)
            checks.update({f"none:{k}": v for k, v in _parse(none).items()})
            proxy = sc.new_name("egress")
            net = sc.run_confined("selftest", ["python3", "-I", PROBE, "--mode", "proxy"], writable=[rw],
                                  readonly=[ro], env=_probe_env(rw, ro, proxy), egress=(ALLOWED,), timeout=120,
                                  image=tag, proxy_name=proxy)
            checks.update({f"proxy:{k}": v for k, v in _parse(net).items()})
        except (sc.Unavailable, subprocess.TimeoutExpired) as exc:
            error = str(exc)
    passed = bool(checks) and all(checks.values()) and not error and len(checks) >= 20
    rec = {"passed": passed, "image": tag, "image_id": image_id, "flags_sha256": sc.flags_sha256(image_id),
           "checks": checks, "at": datetime.now(UTC).replace(microsecond=0).isoformat()}
    path = sc.selftest_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return {**rec, **({"error": error} if error else {})}


def register(sub: Any) -> None:
    """Add ``tools-image build`` and ``tools-selftest`` to the vulnscan parser."""
    sp = sub.add_parser("tools-image", help="build the tools image git, npm and the server kit run in")
    sp.add_argument("action", choices=("build",))
    sp.add_argument("--python-image", default="python:3.12-slim", help="pin by digest: python:3.12-slim@sha256:...")
    sp.add_argument("--node-version", default=NODE_VERSION)
    sp.add_argument("--node-sha256-amd64", default="")
    sp.add_argument("--node-sha256-arm64", default="")
    sub.add_parser("tools-selftest", help="prove the tools container's isolation (required before confined runs)")


def dispatch(args: argparse.Namespace, load_cfg: Callable[[], dict[str, Any]]) -> int:
    """Run a tools-image command: 0 done or passed, 1 failed, 2 setup error."""
    try:
        cfg = load_cfg() if paths.config_file().exists() else {}
    except (OSError, ValueError) as exc:
        print(f"config unusable: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "tools-image":
        sha = {"amd64": args.node_sha256_amd64, "arm64": args.node_sha256_arm64}
        try:
            print(build_image(cfg, python_image=args.python_image, node_version=args.node_version,
                              node_sha256=sha if all(sha.values()) else None))
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"tools image build failed: {exc}", file=sys.stderr)
            return 1
        return 0
    res = selftest(cfg)
    if res.get("error"):
        print(f"tools-selftest: {res['error']}", file=sys.stderr)
    print(json.dumps(res, indent=2))
    return 0 if res["passed"] else 1
