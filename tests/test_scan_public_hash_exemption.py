"""#460: the entropy detector exempts public lockfile SRI hashes and GitHub commit-URL SHAs,
bound to the exact token, and nothing else."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from agentteams._scan_public_hashes import check_public_hash_exemption, sri_body_spans
from agentteams.scan import _is_opaque_segment, scan_content

#: A 40-hex SHA that clears the entropy threshold on its own (measured: about 1 in 8 do).
FIRING_SHA1 = "902ba3cda1883801594b6e1b452790cc53948fda"
SHA256_HEX = hashlib.sha256(b"commit").hexdigest()
#: An opaque secret-shaped token that the context-free detector reports as high.
SECRET = "Zx9Qm2Lp7Rt4Vw8Ky3Nb6Hd1Jf5Gs0Ac"
#: Assembled at runtime so this file does not itself carry a credential-shaped literal.
SYNTHETIC_AWS_KEY = "AKIA" + ("Q7" * 8)


def _sri(alg: str, data: bytes = b"pkg") -> str:
    return f"{alg}-" + base64.b64encode(hashlib.new(alg, data).digest()).decode()


def _credentials(text: str, filename: str) -> list:
    return [f for f in scan_content(text, filename) if f.category == "credential"]


def test_fixtures_fire_without_the_exemption() -> None:
    """The negative controls below mean something only if these tokens fire on their own."""
    assert _credentials(f"value {SECRET}\n", "notes.md")
    assert _credentials(f"see {FIRING_SHA1}\n", "notes.md")
    assert _credentials(f'"x": "{_sri("sha512")[7:]}"\n', "data.json")


# --- exempt: SRI ------------------------------------------------------------------------------

@pytest.mark.parametrize("alg", ["sha1", "sha256", "sha384", "sha512"])
@pytest.mark.parametrize("name", ["package-lock.json", "npm-shrinkwrap.json", "sub/dir/package-lock.json"])
def test_an_sri_integrity_value_in_an_npm_lockfile_is_not_a_credential(alg: str, name: str) -> None:
    line = f'      "integrity": "{_sri(alg)}",\n'
    assert _credentials(line, name) == []


def test_the_issue_repro_lockfile_scans_clean() -> None:
    lock = json.dumps({"packages": {f"node_modules/p{i}": {"version": "1.0.0", "integrity": _sri("sha512", bytes([i]))}
                                    for i in range(50)}}, indent=2)
    assert _credentials(lock, "package-lock.json") == []


@pytest.mark.parametrize("line", [
    "  integrity {sri}\n",
    '  integrity "{sri}"\n',
    '  integrity "{old} {sri}"\n',
])
def test_a_yarn_lock_integrity_field_is_not_a_credential(line: str) -> None:
    text = line.format(sri=_sri("sha512"), old=_sri("sha1"))
    assert _credentials(text, "yarn.lock") == []


def test_a_multi_hash_npm_integrity_value_is_exempt() -> None:
    line = f'"integrity": "{_sri("sha1")} {_sri("sha512")}"\n'
    assert _credentials(line, "package-lock.json") == []


# --- exempt: commit URL -----------------------------------------------------------------------

@pytest.mark.parametrize("tail", ["", ")", "#diff-1", "?w=1", ").", '"'])
def test_a_github_commit_url_sha_is_not_a_credential(tail: str) -> None:
    line = f"* fix ([902ba3c](https://github.com/acme/widget/commit/{FIRING_SHA1}{tail})\n"
    assert _credentials(line, "node_modules/widget/CHANGELOG.md") == []


def test_a_64_hex_commit_url_sha_is_not_a_credential() -> None:
    assert _credentials(f"https://github.com/acme/widget/commit/{SHA256_HEX}\n", "CHANGELOG.md") == []


def test_a_repo_name_with_dots_still_exempts_only_the_sha() -> None:
    assert _credentials(f"https://github.com/acme/widget.js/commit/{FIRING_SHA1}\n", "CHANGELOG.md") == []


# --- still flagged: bound to the token, not the line -----------------------------------------

def test_a_secret_beside_an_sri_value_on_a_minified_lockfile_line_still_fires() -> None:
    line = json.dumps({"a": {"integrity": _sri("sha512"), "note": SECRET}}) + "\n"
    assert len(_credentials(line, "package-lock.json")) == 1


def test_a_pattern_credential_on_an_sri_line_still_fires() -> None:
    line = f'"integrity": "{_sri("sha512")}", "k": "{SYNTHETIC_AWS_KEY}"\n'
    assert any("AWS" in f.message for f in _credentials(line, "package-lock.json"))


def test_an_integrity_key_outside_a_lockfile_still_fires() -> None:
    line = f'"integrity": "{_sri("sha512")}"\n'
    assert _credentials(line, "config.json")
    assert _credentials(line, "package-lock.json.bak")


def test_an_sri_shaped_value_outside_an_integrity_key_still_fires() -> None:
    line = f'"resolved": "{_sri("sha512")}"\n'
    assert _credentials(line, "package-lock.json")


@pytest.mark.parametrize("value", [
    "sha512-" + SECRET,                             # wrong body length for sha512
    "sha256-" + _sri("sha512")[7:],                 # sha512-length body under sha256
    _sri("sha512") + " " + SECRET,                  # one stray item poisons the whole value
    "md5-" + _sri("sha512")[7:],                    # unknown algorithm
])
def test_a_malformed_integrity_value_is_not_exempt(value: str) -> None:
    line = f'"integrity": "{value}"\n'
    assert _credentials(line, "package-lock.json")


def test_an_escaped_integrity_value_exempts_nothing() -> None:
    line = f'"integrity": "{_sri("sha512")}\\u0041"\n'
    assert sri_body_spans(line, "package-lock.json") == []


def test_a_secret_in_the_repo_segment_of_a_commit_url_still_fires() -> None:
    line = f"https://github.com/acme/{SECRET}/commit/{FIRING_SHA1}\n"
    assert _credentials(line, "CHANGELOG.md")


def test_a_secret_in_the_owner_segment_of_a_commit_url_still_fires() -> None:
    line = f"https://github.com/{SECRET}/widget/commit/{FIRING_SHA1}\n"
    assert _credentials(line, "CHANGELOG.md")


@pytest.mark.parametrize("line", [
    f"https://github.com/acme/widget/tree/{FIRING_SHA1}\n",       # not a commit URL
    f"https://example.com/acme/widget/commit/{FIRING_SHA1}\n",    # not github.com
    f"http://github.com/acme/widget/commit/{FIRING_SHA1}\n",      # not https
    f"https://github.com/acme/widget/commit/{FIRING_SHA1}{SECRET}\n",  # runs on past the SHA
    f"token={FIRING_SHA1}\n",                                      # secret context
])
def test_a_40_hex_token_outside_a_commit_url_still_fires(line: str) -> None:
    assert _credentials(line, "CHANGELOG.md")


def test_a_second_token_on_a_commit_url_line_is_still_scanned() -> None:
    line = f"https://github.com/acme/widget/commit/{FIRING_SHA1} {SECRET}\n"
    assert len(_credentials(line, "CHANGELOG.md")) == 1


# --- the verifier itself (ledger: references/redteam-verifiers.csv) --------------------------

def test_check_public_hash_exemption_refuses_a_planted_secret_segment() -> None:
    """Sensitivity: a secret smuggled into the commit URL's repo segment is not exempted."""
    line = f"https://github.com/acme/{SECRET}/commit/{FIRING_SHA1}"
    start = line.index("com/")
    assert check_public_hash_exemption(line, "CHANGELOG.md", start, len(line), _is_opaque_segment) is False


def test_check_public_hash_exemption_accepts_a_real_sri_body() -> None:
    """Negative control: a genuine lockfile SRI body is exempted, at exactly its span."""
    sri = _sri("sha512")
    line = f'"integrity": "{sri}"'
    start = line.index(sri) + len("sha512-")
    end = start + 88
    assert check_public_hash_exemption(line, "package-lock.json", start, end, _is_opaque_segment) is True
    assert check_public_hash_exemption(line, "package-lock.json", start + 1, end, _is_opaque_segment) is False
