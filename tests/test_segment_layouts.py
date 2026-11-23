"""Regression tests for PDF-extracted segment tables with varying whitespace."""

from unittest.mock import patch

import pytest

from app.table_lookup import lookup_fact


# Deliberately tiny synthetic data: no real figures or pages are hardcoded in
# production logic. Three text layouts reflect common PDF extraction formats.
HEADER = (
    "BUSINESS AREAS\n"
    "Personal Banking\n"
    "Financial highlights\n"
    "EURm Q326 Q226 Q126 Q425 Q325 Q225 Q125 Q3/Q3 Q3/Q2\n"
)
ROWS = (
    "Net interest income 780 763 763 781 797 828 846 -2% 2%\n"
    "Total income 1,166 1,145 1,127 1,149 1,164 1,173 1,184 0% 2%\n"
    "Volumes\n"
    "EURbn Q326 Q226 Q126 Q425 Q325 Q225 Q125\n"
    "Total lending 183.4 182.4 182.5 180.8 179.4 178.0 180.4\n"
)


def fake_lookup(text, question="What was Personal Banking's total income in Q3 2026?"):
    class Collection:
        def get(self, where, include):
            assert where == {"document_id": "doc"}
            return {
                "documents": [text],
                "metadatas": [{
                    "filename": "report.pdf", "page_number": 13, "chunk_index": 0,
                }],
            }

    with patch("app.table_lookup.get_collection", return_value=Collection()):
        return lookup_fact(question, "doc")


@pytest.mark.parametrize("transform", [
    lambda text: text,                          # row-aligned text
    lambda text: " ".join(text.split()),      # one long paragraph
    lambda text: "\n".join(text.split()),     # one token per line
])
def test_personal_banking_income_across_pdf_text_layouts(transform):
    fact, unavailable = fake_lookup(transform(HEADER + ROWS))
    assert unavailable is False
    assert fact is not None
    assert fact.answer == "EUR 1,166 million"
    assert fact.page == 13


def test_segment_row_does_not_use_volume_table():
    fact, unavailable = fake_lookup(
        " ".join((HEADER + ROWS).split()),
        "What was Personal Banking's total income in Q2 2035?",
    )
    assert fact is None and unavailable is True


def test_missing_segment_heading_does_not_use_another_business_area():
    text = (HEADER + ROWS).replace("Personal Banking", "Business Banking")
    fact, unavailable = fake_lookup(text)
    assert fact is None and unavailable is False


def test_incomplete_metric_row_does_not_pull_next_row_value():
    text = HEADER + (
        "Total income 1,166 1,145\n"
        "Other expenses 99 88 77 66 55 44 33\n"
    )
    fact, unavailable = fake_lookup(" ".join(text.split()))
    assert fact is None and unavailable is False
