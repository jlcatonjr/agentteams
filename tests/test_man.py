"""
Tests for agentteams/man.py — generate_man_page().
"""

import argparse
import pytest

from agentteams.man import generate_man_page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parser(prog: str = "agentteams", description: str = "Generate agent teams") -> argparse.ArgumentParser:
    """Return a minimal parser for use in tests."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    return parser


def _make_parser_with_args() -> argparse.ArgumentParser:
    """Return a parser with a representative set of options."""
    parser = _make_parser()
    parser.add_argument("--description", "-d", metavar="FILE", help="Path to project description file")
    parser.add_argument("--output", "-o", metavar="DIR", help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    return parser


# ---------------------------------------------------------------------------
# Return type and non-emptiness
# ---------------------------------------------------------------------------

def test_generate_man_page_returns_string():
    result = generate_man_page(_make_parser())
    assert isinstance(result, str)


def test_generate_man_page_non_empty():
    result = generate_man_page(_make_parser())
    assert len(result) > 0


def test_generate_man_page_ends_with_newline():
    result = generate_man_page(_make_parser())
    assert result.endswith("\n")


# ---------------------------------------------------------------------------
# Required groff sections
# ---------------------------------------------------------------------------

def test_th_header_contains_prog_name():
    result = generate_man_page(_make_parser(prog="agentteams"))
    assert ".TH AGENTTEAMS 1" in result


def test_th_header_custom_prog():
    result = generate_man_page(_make_parser(prog="mytool"))
    assert ".TH MYTOOL 1" in result


def test_contains_sh_name():
    result = generate_man_page(_make_parser())
    assert ".SH NAME" in result


def test_contains_sh_synopsis():
    result = generate_man_page(_make_parser())
    assert ".SH SYNOPSIS" in result


def test_contains_sh_description():
    result = generate_man_page(_make_parser())
    assert ".SH DESCRIPTION" in result


def test_contains_sh_options():
    result = generate_man_page(_make_parser())
    assert ".SH OPTIONS" in result


def test_contains_sh_exit_status():
    result = generate_man_page(_make_parser())
    assert ".SH EXIT STATUS" in result


def test_contains_sh_examples():
    result = generate_man_page(_make_parser())
    assert ".SH EXAMPLES" in result


# ---------------------------------------------------------------------------
# Description in NAME section
# ---------------------------------------------------------------------------

def test_name_section_contains_description():
    result = generate_man_page(_make_parser(description="Generate agent teams"))
    assert "Generate agent teams" in result


# ---------------------------------------------------------------------------
# OPTIONS section content
# ---------------------------------------------------------------------------

def test_options_section_contains_flag(self=None):
    result = generate_man_page(_make_parser_with_args())
    assert "--description" in result


def test_options_section_contains_multiple_flags():
    result = generate_man_page(_make_parser_with_args())
    assert "--description" in result
    assert "--output" in result
    assert "--dry-run" in result


def test_options_section_contains_help_text():
    result = generate_man_page(_make_parser_with_args())
    assert "Path to project description file" in result


def test_options_section_omits_help_action():
    """The built-in --help action should not appear in OPTIONS."""
    result = generate_man_page(_make_parser())
    # The standard '-h, --help' option string itself should not be a .B header
    # (it's filtered by _HelpAction check in the man page generator)
    lines = result.splitlines()
    b_lines = [ln for ln in lines if ln.startswith(".B ")]
    for ln in b_lines:
        assert "--help" not in ln


# ---------------------------------------------------------------------------
# EXIT STATUS content
# ---------------------------------------------------------------------------

def test_exit_status_contains_zero():
    result = generate_man_page(_make_parser())
    assert ".B 0" in result


def test_exit_status_contains_one():
    result = generate_man_page(_make_parser())
    assert ".B 1" in result
