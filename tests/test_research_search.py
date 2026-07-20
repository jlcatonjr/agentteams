"""Tests for agentteams.research.search — ported from LingoFriend's own test suite for the
module this was ported from."""

from __future__ import annotations

import pytest

from agentteams.research.search import _extract_pdf_text


def _build_minimal_pdf(text: str) -> bytes:
    """Hand-build a minimal, genuinely valid single-page PDF with an embedded text stream —
    computes real byte offsets for the xref table rather than guessing them, so this is a real
    round-trip test of pypdf's parser, not a hand-waved fixture."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 300 300] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode("latin-1")
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return bytes(out)


def test_extract_pdf_text_round_trips_real_content() -> None:
    pdf_bytes = _build_minimal_pdf("Hello agentteams research test")
    assert "Hello agentteams research test" in _extract_pdf_text(pdf_bytes)


def test_extract_pdf_text_degrades_to_empty_on_malformed_bytes() -> None:
    assert _extract_pdf_text(b"not a real pdf at all, just garbage bytes") == ""


def test_extract_pdf_text_degrades_to_empty_on_truncated_pdf() -> None:
    pdf_bytes = _build_minimal_pdf("Hello agentteams research test")
    truncated = pdf_bytes[: len(pdf_bytes) // 2]
    assert _extract_pdf_text(truncated) == ""


def test_no_module_level_pypdf_import() -> None:
    """The exact regression guard this pattern's LingoFriend origin shipped: `search.py` must be
    importable without pypdf ever being touched at module scope, since agentteams' base install
    doesn't have it. Checks for the actual `import pypdf` statement specifically (not the bare
    word "pypdf", which legitimately appears in this module's own top-of-file docstring prose
    describing what's implemented below)."""
    import inspect

    import agentteams.research.search as search_module

    module_source = inspect.getsource(search_module)
    func_source = inspect.getsource(search_module._extract_pdf_text)
    module_only = module_source.replace(func_source, "")
    assert "import pypdf" not in module_only


def test_import_agentteams_research_search_without_pypdf() -> None:
    """Live-confirm the base-install decoupling claim, not just by static inspection: block pypdf
    at import time with a genuine find_spec-based meta-path finder (NOT the deprecated
    find_module/load_module protocol, which silently fails to intercept under Python 3.12+) and
    confirm this module still imports cleanly."""
    import importlib
    import sys

    class _BlockPypdf:
        def find_spec(self, name, path, target=None):
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("pypdf is not installed (simulating agentteams base install)")
            return None

    for mod in ("agentteams.research.search", "pypdf"):
        sys.modules.pop(mod, None)

    blocker = _BlockPypdf()
    sys.meta_path.insert(0, blocker)
    try:
        with pytest.raises(ImportError):
            importlib.import_module("pypdf")
        importlib.import_module("agentteams.research.search")
    finally:
        sys.meta_path.remove(blocker)
        for mod in ("agentteams.research.search", "pypdf"):
            sys.modules.pop(mod, None)
        importlib.import_module("agentteams.research.search")
