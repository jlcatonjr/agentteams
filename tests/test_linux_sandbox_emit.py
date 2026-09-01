"""Tests for the FRAMEWORK-NEUTRAL Linux OS-confinement emitter (_linux_sandbox_emit).

Linux "works like Seatbelt": agentteams emits a provider-agnostic bwrap launcher
(``sandbox/confine-run.sh``) whenever a confined/exclusive profile is requested — for ANY
framework, never goose-gated, never under ``.goose/``. These tests pin that contract:

* emission is gated on (linux AND confinement-requested);
* the emitted path is repo-root ``sandbox/confine-run.sh`` (agents-dir-relative ``../../`` for a
  2-deep agents dir, ``../`` for a 1-deep one), deliberately NOT under ``.goose/``;
* the launcher content is the reference bwrap launcher (shebang + write-confine + read-deny +
  egress rules present);
* it is framework-neutral — codex/copilot/agents-md/claude all emit it, not only goose.
"""

from __future__ import annotations

import stat
import sys

import pytest

from agentteams.frameworks._linux_sandbox_emit import (
    LINUX_SANDBOX_LAUNCHER_PROJECT_PATH,
    linux_sandbox_output_files,
)
from agentteams.frameworks.agents_md import AgentsMdAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.codex import CodexAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.goose import GooseAdapter
from agentteams.host_features import is_sandbox_capable


# ---------------------------------------------------------------------------
# is_sandbox_capable — framework-neutral on Linux
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "framework",
    ["claude", "goose", "codex", "copilot-vscode", "copilot-cli", "anything-else"],
)
def test_is_sandbox_capable_linux_is_framework_neutral(framework):
    # The emitted bwrap launcher wraps any process, so EVERY framework is capable on Linux.
    assert is_sandbox_capable(framework, "linux") is True


def test_is_sandbox_capable_macos_neutral_windows_framework_specific():
    # macOS is now framework-NEUTRAL too (2026-W36): the SAME launcher's build_macos branch is
    # emitted for ANY framework (macos_sandbox_output_files), so codex/goose/anything are capable
    # on darwin (manual-wire, enforcement-UNVERIFIED until mac-escape-tests.sh passes on-host).
    # Only Windows/other stays framework-specific: claude's native sandbox everywhere, nothing else.
    assert is_sandbox_capable("claude", "darwin") is True
    assert is_sandbox_capable("claude", "win32") is True
    assert is_sandbox_capable("goose", "darwin") is True
    assert is_sandbox_capable("goose", "win32") is False
    assert is_sandbox_capable("codex", "darwin") is True  # was False — macOS launcher now emits
    assert is_sandbox_capable("codex", "win32") is False


# ---------------------------------------------------------------------------
# linux_sandbox_output_files — gating + content
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "manifest",
    [
        {"privilege_profile": "confined"},
        {"privilege_profile": "exclusive"},
        {"host_features": ["goose:sandbox"]},
        {"host_features": ["claude:sandbox"]},
    ],
)
def test_emits_launcher_on_linux_when_confinement_requested(manifest):
    files = linux_sandbox_output_files(manifest, platform="linux")
    assert len(files) == 1
    rel, content = files[0]
    assert rel == "../../sandbox/confine-run.sh"          # default 2-deep neutral path
    assert LINUX_SANDBOX_LAUNCHER_PROJECT_PATH == "sandbox/confine-run.sh"
    assert "/.goose/" not in rel and not rel.startswith(".goose")  # NOT goose-pathed
    # Reference launcher content: shebang + the three enforcement rule families.
    assert content.startswith("#!/usr/bin/env bash")
    assert "--ro-bind / /" in content            # read-only root (write-confine)
    assert "--unshare-net" in content            # egress deny
    assert "MASK" in content or "--tmpfs" in content  # credential/sibling read-exclusion
    assert "FAIL CLOSED" in content              # fail-closed posture


def test_no_launcher_when_cooperative():
    assert linux_sandbox_output_files({"privilege_profile": "cooperative"}, platform="linux") == []
    assert linux_sandbox_output_files({}, platform="linux") == []


def test_no_launcher_off_linux():
    conf = {"privilege_profile": "confined"}
    assert linux_sandbox_output_files(conf, platform="darwin") == []
    assert linux_sandbox_output_files(conf, platform="win32") == []


def test_custom_rel_path_for_shallow_agents_dir():
    # 1-deep adapters (codex/agents-md, agents dir ``.agents``) pass ``../sandbox/…``.
    files = linux_sandbox_output_files(
        {"privilege_profile": "confined"}, "../sandbox/confine-run.sh", platform="linux"
    )
    assert files and files[0][0] == "../sandbox/confine-run.sh"


# ---------------------------------------------------------------------------
# Framework neutrality through the adapters' extra_output_files
# ---------------------------------------------------------------------------
def _launcher_paths(adapter, manifest):
    return [p for p, _ in adapter.extra_output_files(manifest) if p.endswith("sandbox/confine-run.sh")]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux emission is platform-gated")
@pytest.mark.parametrize(
    "adapter, expected_rel",
    [
        (ClaudeAdapter(), "../../sandbox/confine-run.sh"),
        (GooseAdapter(), "../../sandbox/confine-run.sh"),
        (CopilotVSCodeAdapter(), "../../sandbox/confine-run.sh"),
        (CodexAdapter(), "../sandbox/confine-run.sh"),
        (AgentsMdAdapter(), "../sandbox/confine-run.sh"),
    ],
)
def test_every_framework_emits_neutral_launcher_on_linux(adapter, expected_rel):
    # No harness preference: the neutral launcher is emitted for EVERY framework, at the
    # correct repo-root-relative depth for that framework's agents dir.
    paths = _launcher_paths(adapter, {"privilege_profile": "confined"})
    assert paths == [expected_rel], f"{adapter.framework_id}: {paths}"
    # And nothing when confinement is not requested.
    assert _launcher_paths(adapter, {"privilege_profile": "cooperative"}) == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux emission is platform-gated")
@pytest.mark.parametrize(
    "adapter, agents_rel",
    [
        (GooseAdapter(), ".goose/recipes"),
        (ClaudeAdapter(), ".claude/agents"),
        (CodexAdapter(), ".agents"),
        (AgentsMdAdapter(), ".agents"),
    ],
)
def test_emitted_launcher_is_executable_via_real_pipeline(tmp_path, adapter, agents_rel):
    """The REAL write primitive (atomicio._atomic_write_text, used by emit_all) makes the emitted
    launcher executable — asserted by driving that primitive, not by re-implementing its chmod.

    Also lands the launcher at the generated repo root (`sandbox/confine-run.sh`), never under the
    agents dir, at the correct `../` depth for this adapter.
    """
    from agentteams.atomicio import _atomic_write_text

    manifest = {"project_name": "SbxE2E", "privilege_profile": "confined"}
    agents_dir = tmp_path / agents_rel
    agents_dir.mkdir(parents=True)
    for rel, content in adapter.extra_output_files(manifest):
        # Same call the emit pipeline makes (render_pipeline -> emit_all -> _atomic_write_text).
        _atomic_write_text((agents_dir / rel).resolve(), content)

    launcher = tmp_path / "sandbox" / "confine-run.sh"
    assert launcher.is_file(), f"{adapter.framework_id}: launcher must land at repo-root sandbox/"
    assert launcher.stat().st_mode & stat.S_IXUSR, (
        f"{adapter.framework_id}: the real write primitive must make the launcher executable"
    )
    assert not (agents_dir / "sandbox").exists(), "launcher must NOT be under the agents dir"


def test_atomic_write_does_not_mark_non_script_executable(tmp_path):
    """Sev-6 guard: a data/doc file starting with '#!' is NOT made executable (extension-bounded)."""
    from agentteams.atomicio import _atomic_write_text

    md = tmp_path / "note.md"
    _atomic_write_text(md, "#!look like a shebang but a markdown file\n")
    assert not (md.stat().st_mode & stat.S_IXUSR), ".md must never be marked executable"
    sh = tmp_path / "tool.sh"
    _atomic_write_text(sh, "#!/usr/bin/env bash\necho hi\n")
    assert sh.stat().st_mode & stat.S_IXUSR, ".sh with a shebang must be executable"
