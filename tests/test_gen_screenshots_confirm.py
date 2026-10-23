"""scripts/gen_vulnscan_screenshots.py: interactive captures without a pseudo-terminal (#25a).

Where no pseudo-terminal can be allocated (some sandboxes refuse ``openpty``), the grant-sign capture
must still render by the real non-interactive confirmation the signing prompt asks for: re-run with
``--confirm-review-sha256 <digest>``. Captures with no such route are skipped, not faked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_vulnscan_screenshots.py"
_DIGEST = "ab" * 32


@pytest.fixture()
def gen(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("gen_vulnscan_screenshots", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path)
    return mod


class _NoPtyDemo:
    """Demo stand-in: no pseudo-terminal; signing refuses until the digest is passed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run_pty(self, argv, answers):
        raise OSError("out of pty devices")

    def run(self, argv, *, cwd=None, stdin=""):
        self.calls.append(list(argv))
        if "--confirm-review-sha256" in argv:
            return 0, f"  review sha256   : {_DIGEST}\ngrant installed: vg-1\n"
        return 1, (f"  review sha256   : {_DIGEST}\nError: no terminal to confirm on. Review the payload "
                   f"above, then re-run with --confirm-review-sha256 {_DIGEST} (operator argv only).\n")

    def shorten(self, text: str) -> str:
        return text


def test_grant_sign_without_a_pty_confirms_by_review_digest(gen, tmp_path):
    demo = _NoPtyDemo()
    argv = ["python", "-m", "agentteams.vulnscan", "grant", "sign", "--key-id", "op"]
    codes = gen.capture(demo, "06-grant-sign", "sign", [
        (argv, {"pty": {"[y/N]": "y"}, "digest": True, "exit": True})])
    assert codes == [1, 0]
    assert demo.calls == [argv, [*argv, "--confirm-review-sha256", _DIGEST]]
    svg = (tmp_path / "06-grant-sign.svg").read_text()
    assert "--confirm-review-sha256" in svg and "grant installed" in svg


def test_capture_without_a_pty_or_digest_route_is_skipped(gen, tmp_path, capsys):
    codes = gen.capture(_NoPtyDemo(), "09-resolve", "resolve", [(["resolve"], {"pty": {"[y/N]": "y"}})])
    assert codes == [] and not (tmp_path / "09-resolve.svg").exists()
    assert "skipped 09-resolve.svg" in capsys.readouterr().out
