"""GV1 durable fail-closed: governed-absent -> raise; ungoverned-absent -> False."""
import pytest
from agentteams.cli.decision_log import _enforce_decision_signing


def test_governed_absent_config_raises(tmp_path):
    (tmp_path / "signing-governed.marker").write_text("governed\n")
    # references/agent-privilege.json is absent, but the marker asserts governance -> fail closed.
    with pytest.raises(RuntimeError):
        _enforce_decision_signing(tmp_path)


def test_ungoverned_absent_config_is_off(tmp_path):
    # No marker, no config: the one legitimate legacy-OFF case.
    assert _enforce_decision_signing(tmp_path) is False


def test_present_switch_true(tmp_path):
    ref = tmp_path / "references"; ref.mkdir()
    (ref / "agent-privilege.json").write_text('{"enforce_decision_signing": true}\n')
    assert _enforce_decision_signing(tmp_path) is True
