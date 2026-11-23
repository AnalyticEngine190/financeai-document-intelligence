
import pytest

from app.chunking import clean_text, chunk_report


def make_report(text: str) -> dict:
    return {
        "sha256": "a" * 64,
        "filename": "test.pdf",
        "pages": [
            {"page_number": 1, "text": text},
            {"page_number": 2, "text": ""},
            {"page_number": 3, "text": "Operating profit increased."},
        ],
    }


def test_clean_text_preserves_financial_values():
    raw = "Revenue:  1,250  EUR\r\n  Profit: 300 EUR"
    cleaned = clean_text(raw)

    assert "1,250 EUR" in cleaned
    assert "Profit: 300 EUR" in cleaned


def test_chunks_preserve_page_numbers():
    report = make_report("Revenue increased. " * 200)
    chunks = chunk_report(report)

    assert chunks
    assert all(c["page_number"] != 2 for c in chunks)
    assert any(c["page_number"] == 1 for c in chunks)
    assert any(c["page_number"] == 3 for c in chunks)


def test_chunks_have_unique_ids():
    report = make_report("Financial data. " * 200)
    chunks = chunk_report(report)

    ids = [c["id"] for c in chunks]

    assert len(ids) == len(set(ids))


def test_chunks_match_source_offsets():
    report = make_report("Revenue grew significantly. " * 200)
    chunks = chunk_report(report, chunk_size=200, overlap=40)

    source = clean_text(report["pages"][0]["text"])
    page_chunks = [
        c for c in chunks if c["page_number"] == 1
    ]

    assert len(page_chunks) > 1

    for chunk in page_chunks:
        assert chunk["text"] == source[
            chunk["start_char"]:chunk["end_char"]
        ]


def test_invalid_configuration():
    report = make_report("Revenue increased.")

    with pytest.raises(ValueError):
        chunk_report(report, chunk_size=100, overlap=100)
