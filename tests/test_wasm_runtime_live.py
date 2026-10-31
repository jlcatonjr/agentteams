"""Live provisioning test (P03): real download from GitHub releases. Operator-run only.

Runs only with ``AGENTTEAMS_WASM_LIVE=1`` on a host that can reach ``release-assets.githubusercontent.com``.
It installs the one fully pinned component (QuickJS-ng ``qjs-wasi.wasm``) into ``tmp_path`` and resolves it.
"""

from __future__ import annotations

import os

import pytest

from agentteams import wasm_runtime as wr

needs_network = pytest.mark.skipif(os.environ.get("AGENTTEAMS_WASM_LIVE") != "1",
                                   reason="set AGENTTEAMS_WASM_LIVE=1 on a host that can download")


@needs_network
def test_live_install_javascript_then_resolve(tmp_path):
    rep = wr.install(tmp_path, ["javascript"])
    assert rep.installed == ("javascript",)
    path = wr.resolve(tmp_path, "javascript")
    assert path.name == "qjs-wasi.wasm" and path.read_bytes()[:4] == b"\0asm"
    assert wr.status(tmp_path)["javascript"] == "ok " + wr.pinned("javascript")["version"]
