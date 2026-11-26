"""L1 origin of a distro package whose installed version is superseded in the archive (#585 A2).

While a distro update is pending, `apt-cache policy` lists the installed version under `/var/lib/dpkg/status` only;
the archive serves the candidate. That used to read as `repo:unknown` and drew a false upstream end-of-life finding.
It is `pkg:` (with an INFO line) only when every candidate archive is the distro's, the candidate shares the installed
version's upstream version, and the installed version carries a distro revision.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_vulnscan_server_kit import OS_RELEASE, SERVER_TOOLS, Kit, _host, _l1, kit  # noqa: F401  (kit: fixture)

SECURITY = "http://security.ubuntu.com/ubuntu"
NGINX_ORG = "http://nginx.org/packages/ubuntu"


def _apt_cache(installed: str, candidate: str, uri: str) -> str:
    return f"""#!/bin/bash
[ "$*" = "-o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= policy $6" ] || exit 1
shift 4
[ "$2" = nginx ] || exit 0
# apt translates the labels: a German server says "Installiert:" unless L1 pins the C locale
if [ "${{LC_ALL:-}}" = C ]; then i=Installed c=Candidate; else i=Installiert c=Installationskandidat; fi
echo "nginx:"; echo "  $i: {installed}"; echo "  $c: {candidate}"; echo "  Version table:"
printf '     {candidate} 500\\n        500 {uri} noble-security/main amd64 Packages\\n'
printf ' *** {installed} 100\\n        100 /var/lib/dpkg/status\\n'
"""


def _nginx(kit: Kit, installed: str, candidate: str, uri: str) -> tuple[str, str]:
    hb = _host(kit, extra=dict(SERVER_TOOLS) | {"apt-cache": _apt_cache(installed, candidate, uri)})
    root = kit.root / "hostroot"
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "os-release").write_text(OS_RELEASE)
    proc = _l1(hb, root)
    assert "\nRESULT " in proc.stdout, (proc.stdout, proc.stderr)
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RUNTIME\tnginx\t"))
    assert line.startswith(f"RUNTIME\tnginx\t1.26.1\t{os.path.realpath(hb)}/nginx\t"), line
    return line.rsplit("\t", 1)[1], proc.stdout + proc.stderr


PENDING = "INFO        nginx: installed version superseded in the archive (update pending)"


def test_l1_a_superseded_distro_version_with_a_distro_candidate_is_pkg(kit: Kit) -> None:
    origin, out = _nginx(kit, "1.24.0-2ubuntu7.19", "1.24.0-2ubuntu7.21", SECURITY)
    assert origin == "pkg:nginx" and PENDING in out


@pytest.mark.parametrize("installed, candidate, uri", [
    ("1.24.0-2ubuntu7.19", "1.24.0-2ubuntu7.21", NGINX_ORG),   # the candidate comes from a third-party archive
    ("1.25.3-1", "1.24.0-2ubuntu7.21", SECURITY),               # a different upstream version (a local build)
    ("1.24.0", "1.24.0-2ubuntu7.21", SECURITY),                 # no distro revision on the installed version
    ("1.24.0-2ubuntu7.21", "1.24.0-2ubuntu7.21", SECURITY),     # installed is the candidate, served by dpkg only
])
def test_l1_otherwise_a_status_only_version_stays_unknown(kit: Kit, installed: str, candidate: str, uri: str) -> None:
    origin, out = _nginx(kit, installed, candidate, uri)
    assert origin == "repo:unknown:nginx" and PENDING not in out
