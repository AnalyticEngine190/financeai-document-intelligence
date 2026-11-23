
"""Automated tests for PDF ingestion."""

import hashlib

import pymupdf
import pytest

from app.ingestion import extract_pdf


def test_extracts_text_and_page_numbers(tmp_path):
    pdf_path = tmp_path / "sample.pdf"

    with pymupdf.open() as document:
        first_page = document.new_page()
        first_page.insert_text(
            (72, 72),
            "Revenue increased to EUR 100 million.",
        )
        document.new_page()
        document.save(pdf_path)

    result = extract_pdf(pdf_path)

    assert result["page_count"] == 2
    assert result["text_page_count"] == 1
    assert result["pages"][0]["page_number"] == 1
    assert "EUR 100 million" in result["pages"][0]["text"]
    assert result["pages"][1]["page_number"] == 2
    assert result["pages"][1]["text"] == ""


def test_sha256_is_correct(tmp_path):
    pdf_path = tmp_path / "sample.pdf"

    with pymupdf.open() as document:
        document.new_page()
        document.save(pdf_path)

    result = extract_pdf(pdf_path)

    expected = hashlib.sha256(
        pdf_path.read_bytes()
    ).hexdigest()

    assert result["sha256"] == expected


def test_missing_file_raises_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_pdf(tmp_path / "missing.pdf")


def test_non_pdf_extension_is_rejected(tmp_path):
    text_path = tmp_path / "report.txt"
    text_path.write_text("Not a PDF")

    with pytest.raises(ValueError):
        extract_pdf(text_path)
