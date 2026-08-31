"""test_template_pins.py — the pin must not follow the thing it is checking.

Security review §4.6: nothing verifies the shipped templates. `drift.py` compares against the
target's own build log, which is change-since-last-build, not authenticity. Anyone who can write
to the installed package edits Tier 1 of the authority hierarchy and every restore propagates it.

A checksum manifest or a signature *inside the package* does not close that: the thing being
protected and the thing protecting it share a writable surface. This puts the record in the
**consumer's** version control instead.

**The load-bearing property is negative.** The pin is only ever written by an explicit
`--pin-templates`. If any code path re-pinned on mismatch, on upgrade, or on any condition at
all, the file would record whatever it last saw and prove nothing. Most of this file tests that
nothing does.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agentteams import template_pins as tp

INSTALLED = {"universal/navigator.template.md": "aaaa1111", "domain/x.template.md": "bbbb2222"}


def _pin(tmp_path: Path, hashes: dict[str, str]) -> Path:
    return tp.write_pin(tmp_path, hashes, pinned_at="2026-08-03T00:00:00Z")


def test_no_pin_means_not_opted_in_not_failure(tmp_path: Path) -> None:
    """Absent is not a failure. Pinning is opt-in; an unpinned project is unprotected, not broken."""
    assert tp.load_pin(tmp_path) is None
    assert tp.verify(tmp_path, INSTALLED) is None


def test_a_matching_pin_verifies(tmp_path: Path) -> None:
    _pin(tmp_path, INSTALLED)
    result = tp.verify(tmp_path, INSTALLED)
    assert result is not None and result.is_clean
    assert "verified" in result.format()


def test_a_changed_template_is_reported_loudly(tmp_path: Path) -> None:
    """The threat: an installed template edited after the pin was taken."""
    _pin(tmp_path, INSTALLED)
    tampered = dict(INSTALLED, **{"universal/navigator.template.md": "deadbeef"})
    result = tp.verify(tmp_path, tampered)

    assert result is not None and not result.is_clean
    assert "navigator" in result.format()
    assert "aaaa1111" in result.format() and "deadbeef" in result.format()
    assert "do NOT re-pin" in result.format()


def test_verification_never_rewrites_the_pin(tmp_path: Path) -> None:
    """THE property. A pin that follows what it checks records nothing."""
    path = _pin(tmp_path, INSTALLED)
    before = path.read_bytes()

    tp.verify(tmp_path, dict(INSTALLED, **{"domain/x.template.md": "99999999"}))
    tp.verify(tmp_path, {})
    tp.verify(tmp_path, {"brand/new.template.md": "cccc3333"})

    assert path.read_bytes() == before, "verification mutated the trust root"


def test_only_the_pin_command_may_write_the_pin() -> None:
    """Structural: `write_pin` must have exactly one caller, and it must be the CLI action.

    A guard in prose would not survive someone adding a helpful "auto-refresh on upgrade".
    """
    repo = Path(__file__).resolve().parents[1]
    callers: list[str] = []
    for path in sorted(repo.glob("agentteams/**/*.py")):
        if path.name == "template_pins.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name == "write_pin":
                    callers.append(str(path.relative_to(repo)))
    assert len(callers) <= 1, (
        f"write_pin has {len(callers)} callers: {callers}. The pin's value is that it does not "
        "follow the templates; more than one write path is how that erodes."
    )


def test_a_removed_template_is_reported(tmp_path: Path) -> None:
    _pin(tmp_path, INSTALLED)
    result = tp.verify(tmp_path, {"universal/navigator.template.md": "aaaa1111"})
    assert result is not None and not result.is_clean
    assert "no longer installed" in result.format()


def test_a_new_template_is_noted_but_not_alarming(tmp_path: Path) -> None:
    """Adding an archetype installs templates the pin predates. That is normal, not a finding."""
    _pin(tmp_path, INSTALLED)
    result = tp.verify(tmp_path, dict(INSTALLED, **{"domain/new.template.md": "cccc3333"}))
    assert result is not None
    assert result.is_clean, "a purely additive change must not read as tampering"
    assert "not in the pin" in result.format()


def test_the_pin_tells_the_reader_to_commit_it(tmp_path: Path) -> None:
    """The trust root is the consumer's version control. A pin left uncommitted is decoration."""
    path = _pin(tmp_path, INSTALLED)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "Commit this file" in data["note"]
    assert data["template_hashes"] == dict(sorted(INSTALLED.items()))


def test_a_corrupt_pin_reads_as_absent_not_as_clean(tmp_path: Path) -> None:
    """Fail toward 'unprotected', never toward 'verified'."""
    path = tp.pin_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert tp.load_pin(tmp_path) is None
    assert tp.verify(tmp_path, INSTALLED) is None


def test_it_pins_the_real_template_set(tmp_path: Path) -> None:
    """Anti-vacuity: the digests come from the same function the build log uses."""
    import pytest

    from agentteams import analyze, ingest, render

    repo = Path(__file__).resolve().parents[1]
    brief = repo / ".github/agents/_build-description.json"
    if not brief.exists():
        pytest.skip("self brief absent")

    desc = ingest.load(brief, scan_project=False)
    manifest = analyze.build_manifest(desc, framework="claude")
    hashes = render.compute_template_hashes(manifest, templates_dir=repo / "agentteams/templates")
    assert len(hashes) >= 20, f"only {len(hashes)} templates hashed; the manifest walk regressed"

    _pin(tmp_path, hashes)
    assert tp.verify(tmp_path, hashes).is_clean
    tampered = dict(hashes)
    first = sorted(tampered)[0]
    tampered[first] = "0" * 16
    assert not tp.verify(tmp_path, tampered).is_clean


def test_the_pin_never_lands_inside_the_generated_output(tmp_path: Path) -> None:
    """`resolve_output_dir` returns project_root == output_dir whenever --output is given —
    the normal invocation — so deriving the pin location from it put the trust root inside
    `.claude/agents/`, a directory every run rewrites.

    Caught only by running the command end-to-end; the unit tests passed throughout, because
    they were handed a root rather than deriving one.
    """
    out = tmp_path / "proj" / ".claude" / "agents"
    out.mkdir(parents=True)
    root = tp.consumer_root({"existing_project_path": str(tmp_path / "proj")}, out)
    assert root == (tmp_path / "proj").resolve()
    assert out not in tp.pin_path(root).parents, "pin is inside the generated output directory"


def test_consumer_root_falls_back_without_a_declared_project(tmp_path: Path) -> None:
    """No declared project path: still must not choose somewhere inside the output."""
    out = (tmp_path / "x" / ".claude" / "agents")
    out.mkdir(parents=True)
    root = tp.consumer_root({}, out)
    assert not str(root).startswith(str(out.resolve()) + "/")


# --------------------------------------------------------------------------------------
# D2/D3: the fallback must refuse, not guess (report: blocking-items-and-defects)
# --------------------------------------------------------------------------------------


def test_the_fallback_refuses_rather_than_choosing_the_generated_area(tmp_path, monkeypatch) -> None:
    """D2. With no declared project path and cwd inside the output, the old code returned
    `output_dir.parent` — `<proj>/.claude`, which the tool generates.

    A pin in a directory that gets overwritten is worse than no pin: it reads as protection.
    Refusing is the correct outcome, and the message must say what to do about it.
    """
    out = tmp_path / "proj" / ".claude" / "agents"
    out.mkdir(parents=True)
    monkeypatch.chdir(out)

    with pytest.raises(tp.PinLocationError) as excinfo:
        tp.consumer_root({}, out)
    message = str(excinfo.value)
    assert "existing_project_path" in message, "the refusal must name the fix"
    assert "project root" in message


def test_the_fallback_still_accepts_a_legitimate_cwd(tmp_path, monkeypatch) -> None:
    """Negative control: refusing must not break the ordinary case of running from the project."""
    proj = tmp_path / "proj"
    out = proj / ".claude" / "agents"
    out.mkdir(parents=True)
    monkeypatch.chdir(proj)
    assert tp.consumer_root({}, out) == proj.resolve()


def test_pin_templates_exits_non_zero_when_it_cannot_place_the_pin(tmp_path, monkeypatch) -> None:
    """A script must notice. Refusing silently would be the same failure in a new costume."""
    out = tmp_path / "proj" / ".claude" / "agents"
    out.mkdir(parents=True)
    monkeypatch.chdir(out)

    class _Args:
        pin_templates = True

    rc = tp.run_pinning(_Args(), {"output_files": []}, {}, out, tmp_path / "templates")
    assert rc == 1


def test_verification_says_so_when_it_cannot_locate_a_pin(tmp_path, monkeypatch, capsys) -> None:
    """Not silent. If the root cannot be found, verification did not happen and must say so."""
    out = tmp_path / "proj" / ".claude" / "agents"
    out.mkdir(parents=True)
    monkeypatch.chdir(out)

    class _Args:
        pin_templates = False

    rc = tp.run_pinning(_Args(), {"output_files": []}, {}, out, tmp_path / "templates")
    assert rc is None, "verification must not abort the pipeline"
    err = capsys.readouterr().err
    assert "template pin" in err and "cannot place" in err, err
