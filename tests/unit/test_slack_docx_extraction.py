from __future__ import annotations

import io

import pytest

import app as app_module

try:
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False


def _make_docx_bytes(paragraphs: list[str], tables: list[list[list[str]]]) -> bytes:
    """Build an in-memory DOCX with given paragraphs and tables."""
    doc = DocxDocument()
    for text in paragraphs:
        doc.add_paragraph(text)
    for table_data in tables:
        if not table_data:
            continue
        col_count = max(len(row) for row in table_data)
        tbl = doc.add_table(rows=len(table_data), cols=col_count)
        for r_idx, row_data in enumerate(table_data):
            for c_idx, cell_text in enumerate(row_data):
                tbl.rows[r_idx].cells[c_idx].text = cell_text
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx not installed")
def test_extract_docx_text_reads_paragraphs() -> None:
    docx_bytes = _make_docx_bytes(["Hello World", "Second paragraph"], [])
    extracted = app_module._extract_docx_text(docx_bytes)
    assert "Hello World" in extracted
    assert "Second paragraph" in extracted


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx not installed")
def test_extract_docx_text_reads_table_cells() -> None:
    table_data = [
        ["Site", "Loc Code", "School Name", "City", "Contractor"],
        ["MANN", "UCLA COMM SCH7574", "Horace Mann UCLA Community School", "LOS ANGELES, CA", "CSIB"],
    ]
    docx_bytes = _make_docx_bytes([], [table_data])
    extracted = app_module._extract_docx_text(docx_bytes)

    assert "Site" in extracted
    assert "UCLA COMM SCH7574" in extracted
    assert "LOS ANGELES, CA" in extracted
    assert "CSIB" in extracted
    # Cells should be tab-separated on each row
    assert "Horace Mann UCLA Community School\tLOS ANGELES, CA" in extracted


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx not installed")
def test_extract_docx_text_reads_paragraphs_and_tables_together() -> None:
    docx_bytes = _make_docx_bytes(
        ["CCTV Project - PUNCH LIST"],
        [[["Item", "Status"], ["Camera 1", "Done"], ["Camera 2", "Pending"]]],
    )
    extracted = app_module._extract_docx_text(docx_bytes)

    assert "CCTV Project - PUNCH LIST" in extracted
    assert "Camera 1" in extracted
    assert "Pending" in extracted


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx not installed")
def test_extract_docx_text_empty_bytes_returns_empty() -> None:
    assert app_module._extract_docx_text(b"") == ""
