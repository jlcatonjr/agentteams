"""Shared vulnscan test setup: an isolated operator home with a verify key, and grant signing.

:func:`operator_env` needs nothing beyond the stdlib; :func:`operator_home` and :func:`sign_grant` need
``cryptography`` (the ``[signing]`` extra), imported lazily, so importers that sign call
``pytest.importorskip("cryptography")`` first.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import grant

DECISION_LOG_HEADER = "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner\n"


def operator_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point HOME and the vulnscan config/state/kit dirs into ``tmp_path`` and create an empty governance team.

    Args:
        tmp_path: Pytest tmp dir.
        monkeypatch: Pytest monkeypatch.

    Returns:
        ``{"tmp", "home", "gov"}``.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    for var, value in (("HOME", home), ("AGENTTEAMS_VULNSCAN_CONFIG_DIR", home / ".config" / "agentteams"),
                       ("AGENTTEAMS_VULNSCAN_STATE_DIR", tmp_path / "state"),
                       ("AGENTTEAMS_SERVER_KIT_DIR", tmp_path / "kit")):
        monkeypatch.setenv(var, str(value))
    gov = tmp_path / "govteam"
    (gov / "references").mkdir(parents=True, exist_ok=True)
    (gov / "references" / "security-decisions.log.csv").write_text(DECISION_LOG_HEADER)
    return {"tmp": tmp_path, "home": home, "gov": gov}


def operator_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point HOME and the vulnscan config/state/kit dirs into ``tmp_path`` and install an operator verify key.

    The verify-key directory is 0700 and the key 0600, as the operator helper creates them. The governance
    team has an empty decision log (no HALT).

    Args:
        tmp_path: Pytest tmp dir.
        monkeypatch: Pytest monkeypatch.

    Returns:
        ``{"tmp", "home", "pem" (the private key, PKCS8 PEM text), "gov"}``.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    world = operator_env(tmp_path, monkeypatch)
    home = world["home"]
    key = Ed25519PrivateKey.generate()
    vk = home / ".config" / "agentteams" / "verify-keys"
    vk.mkdir(parents=True)
    os.chmod(vk, 0o700)
    (vk / "op.pub.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    os.chmod(vk / "op.pub.pem", 0o600)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return {**world, "pem": pem}


def sign_grant(pem: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return ``record`` signed with the operator key (as ``grant sign`` would, without the review prompt).

    Args:
        pem: Private key PEM from :func:`operator_home`.
        record: Output of ``grant.build``.

    Returns:
        The signed record.
    """
    from agentteams.cli.signed_ledger import ed25519_sign

    return {**record, "signature": ed25519_sign(pem, grant.signed_values(record))}
